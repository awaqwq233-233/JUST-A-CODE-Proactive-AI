"""独立 Harness 任务控制器：后台 SDK、有限等待与真实证据交付。"""
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import threading
import time
from urllib.request import build_opener, ProxyHandler
import uuid

from .boundary import AgentRefused, TaskBoundary, atomic_json, open_directory
from .profile import MODEL, ROOT, build_patch

CACHE = ROOT / ".cache/agent"


def loaded_context():
    """只读取本机原生清单，核对精确已加载实例及实际上下文/工具/关闭思考能力。"""
    opener = build_opener(ProxyHandler({}))
    with opener.open("http://127.0.0.1:12345/api/v1/models", timeout=5) as response:
        models = json.loads(response.read(262144))["models"]
    for model in models:
        for instance in model.get("loaded_instances", []):
            if instance.get("id") != MODEL or model.get("key") != MODEL:
                continue
            caps = model.get("capabilities", {})
            if not caps.get("trained_for_tool_use") or "off" not in caps.get("reasoning", {}).get("allowed_options", []):
                raise AgentRefused("精确模型未声明工具和关闭思考能力")
            length = instance.get("config", {}).get("context_length")
            if type(length) is not int or length < 4096:
                raise AgentRefused("无法确认已加载模型上下文，或容量不足 4096")
            return length
    raise AgentRefused("目标 Qwen 未加载，不会自动加载或切换其他模型：" + MODEL)


@contextmanager
def serial_task(cache=CACHE):
    """首版跨进程单任务串行，第二个调用明确忙碌而不排队。"""
    import fcntl
    cache.mkdir(parents=True, exist_ok=True)
    directory = open_directory(cache)
    fd = os.open("active.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    os.close(directory)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise AgentRefused("Agent 正忙，本次任务未排队") from error
        yield
    finally:
        os.close(fd)


class ToolServer(ThreadingHTTPServer):
    """仅监听 IPv4 loopback；所有调用仍由随机凭据和任务身份校验。"""
    daemon_threads = True

    def __init__(self, boundary):
        """为单任务分配临时端口和不可猜测凭据，不使用固定全局权限。"""
        self.boundary, self.token = boundary, secrets.token_urlsafe(32)
        super().__init__(("127.0.0.1", 0), ToolHandler)


class ToolHandler(BaseHTTPRequestHandler):
    """严格解析少量 JSON 工具请求，不提供文件或通用 HTTP 代理。"""

    def setup(self):
        """限制单个客户端发送请求的等待时间。"""
        super().setup()
        self.connection.settimeout(2)

    def log_message(self, format, *args):
        """不将来源文本、凭据和模型参数写入 HTTP 访问日志。"""

    def do_POST(self):
        """检查凭据和大小，再在任务锁中执行受控工具。"""
        try:
            if self.path != "/tool" or not secrets.compare_digest(
                    self.headers.get("Authorization", ""), "Bearer " + self.server.token):
                self.send_error(403)
                return
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 8192 or self.headers.get("Transfer-Encoding"):
                raise AgentRefused("工具请求超限或不支持分块请求")
            request = json.loads(self.rfile.read(size))
            result, status = self.server.boundary.execute(request), 200
        except Exception as error:
            result, status = {"error": str(error)}, 400
        body = json.dumps(result, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


def child_environment(boundary, server, context):
    """仅传必需系统变量和本机工具凭据，不继承云端密钥、代理或 Node 注入。"""
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "TMPDIR", "LANG") if key in os.environ}
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONPATH=str(ROOT),
               JAC_LOCAL_KEY="local-no-secret", JAC_CONTEXT_LIMIT=str(min(context, 8192)),
               JAC_TOOL_URL=f"http://127.0.0.1:{server.server_port}", JAC_TOOL_TOKEN=server.token,
               NO_PROXY="127.0.0.1,localhost", DO_NOT_TRACK="1", XDG_CACHE_HOME=str(CACHE / "native-cache"), PKG_NATIVE_CACHE_PATH=str(CACHE / "native-cache"))
    return env


def stop_child(process):
    """终止本控制器拥有的进程组并有界回收，关闭 SDK 同时中止模型 HTTP 等待。"""
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            process.wait(timeout=2)
            return
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=2)


def run_comparison(workspace, before, after, *, output=ROOT / "output/agent", timeout=120,
                   should_stop=None, context_reader=loaded_context, interpreter=None):
    """运行一项有界比较任务；取消/异常只留本地诊断，不发布成功报告。"""
    with serial_task():
        task_id = "jac-" + uuid.uuid4().hex
        run_dir = CACHE / "runs" / task_id
        run_dir.mkdir(parents=True, mode=0o700)
        boundary = TaskBoundary(workspace, before, after, task_id, timeout, should_stop)
        server, process, thread = None, None, None
        result = {"task_id": task_id, "status": "failed", "report": None}
        try:
            # 预检不计工具成功；实际工具仍须再次读取、记录哈希和经过模型循环。
            for source in boundary.names:
                boundary.read_source(source)
            context = context_reader()
            boundary.check()
            patch = run_dir / "jac.patch.json"
            atomic_json(patch, build_patch(context))
            config = {"dsh_home": str(run_dir / "home"), "profile": "sdk-minimal",
                      "patches": [str(patch)], "cwd": boundary.workspace, "runtime_cwd": str(ROOT),
                      "provider": "jac-local", "model": MODEL, "reasoning_effort": "off",
                      "max_tokens": 768, "request_timeout_seconds": min(timeout, 90),
                      "initialize_timeout_seconds": 20, "shutdown_timeout_seconds": 1,
                      "session_id": task_id}
            atomic_json(run_dir / "worker.json", config)
            atomic_json(run_dir / "state.json", boundary.evidence("running", model=MODEL, context_length=context))
            server = ToolServer(boundary)
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
            thread.start()
            python = interpreter or CACHE / "venv/bin/python"
            with (run_dir / "worker.out").open("wb") as stdout, (run_dir / "worker.err").open("wb") as stderr:
                process = subprocess.Popen([str(python), "-m", "src.brain.agent.worker", str(run_dir / "worker.json")],
                                           cwd=ROOT, env=child_environment(boundary, server, context),
                                           stdout=stdout, stderr=stderr, start_new_session=True)
                while process.poll() is None:
                    if should_stop is not None and should_stop():
                        boundary.cancel()
                    boundary.check()
                    if (run_dir / "worker.err").stat().st_size > 131072:
                        raise AgentRefused("运行时诊断超过预算")
                    time.sleep(0.05)
            boundary.check()
            if process.returncode:
                raise AgentRefused("Harness 运行失败，详情见本机 " + str(run_dir / "worker.err"))
            worker_result = json.loads((run_dir / "worker.out").read_text(encoding="utf-8"))
            if worker_result.get("finish_reason") != "completed":
                raise AgentRefused("Harness 回合未完成：" + json.dumps(worker_result.get("terminal"), ensure_ascii=False)[:1500])
            if should_stop is not None and should_stop():
                boundary.cancel()
            report = boundary.publish(output, worker_result)
            result.update(status="completed", report=str(report), runtime=worker_result, context_length=context)
        except Exception as error:
            reason = boundary.cancel_reason
            result.update(status="cancelled" if reason == "cancelled" else "timeout" if reason == "timeout" else "failed",
                          error=str(error))
        finally:
            boundary.cancel("finished")
            if process is not None:
                stop_child(process)
            if server is not None:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
            atomic_json(run_dir / "state.json", boundary.evidence(result["status"], result=result))
            boundary.close()
        result.update(evidence=str(run_dir / "state.json"))
        return result


def inspect_session(task_id):
    """只恢复本程序已有历史并核对目录；绝不发送继续提示或重新执行工具。"""
    if not task_id.startswith("jac-") or len(task_id) != 36 or any(c not in "0123456789abcdef" for c in task_id[4:]):
        raise AgentRefused("无效任务标识")
    with serial_task():
        run_dir = CACHE / "runs" / task_id
        config = json.loads((run_dir / "worker.json").read_text(encoding="utf-8"))
        config["restore_only"] = True
        atomic_json(run_dir / "inspect.json", config)
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONUTF8": "1", "PYTHONPATH": str(ROOT),
               "JAC_LOCAL_KEY": "local-no-secret", "DO_NOT_TRACK": "1",
               "XDG_CACHE_HOME": str(CACHE / "native-cache"), "PKG_NATIVE_CACHE_PATH": str(CACHE / "native-cache")}
        response = subprocess.run([str(CACHE / "venv/bin/python"), "-m", "src.brain.agent.worker",
                                   str(run_dir / "inspect.json")], cwd=ROOT, env=env,
                                  capture_output=True, text=True, encoding="utf-8", timeout=25, check=True)
        return json.loads(response.stdout)
