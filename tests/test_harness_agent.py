"""独立 Agent 的权限、取消、发布及真实 SDK 协议测试（模型使用本机假服务）。"""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
import time

import pytest

from src.brain.agent.boundary import AgentRefused, TaskBoundary
from src.brain.agent import runner
from src.brain.agent.profile import MODEL, ROOT, TOOLS, build_patch


@pytest.fixture
def boundary(tmp_path):
    """建立两份小文本，并保证目录句柄在每例结束后释放。"""
    (tmp_path / "a.txt").write_text("旧内容\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("新内容\n", encoding="utf-8")
    value = TaskBoundary(tmp_path, "a.txt", "b.txt", "test-session")
    yield value
    value.close()


def call(boundary, name, args, identity=None):
    """构造带真实任务身份的工具请求。"""
    return boundary.execute(dict(name=name, args=args, call_id=identity or str(len(boundary.trace)),
                                 session_id=boundary.task_id))


def stage(boundary):
    """完成测试所需读取、比较和草稿，所有数据经实际文件边界。"""
    call(boundary, "jac_read_text", {"source": "before"})
    call(boundary, "jac_read_text", {"source": "after"})
    call(boundary, "jac_compare_texts", {})
    call(boundary, "jac_stage_report", {"summary": "内容由旧变新。"})


def test_report_has_evidence_and_does_not_overwrite(boundary, tmp_path):
    """发布准确差异和证据，再次发布不能覆盖已有报告。"""
    stage(boundary)
    result = {"finish_reason": "completed", "session_id": boundary.task_id}
    path = boundary.publish(tmp_path / "reports", result)
    assert "-旧内容" in path.read_text() and "+新内容" in path.read_text()
    assert json.loads(path.with_name("evidence.json").read_text())["status"] == "completed"
    with pytest.raises(AgentRefused, match="已存在"):
        boundary.publish(tmp_path / "reports", result)
    assert path.exists()


def test_missing_final_newline_has_separate_diff_lines(boundary, tmp_path):
    """无末尾换行的增删仍独立展示，只有换行变化也会留下差异。"""
    (tmp_path / "a.txt").write_text("旧内容", encoding="utf-8")
    stage(boundary)
    assert "-旧内容\n\\ No newline at end of file\n+新内容\n" in boundary.diff
    from src.brain.agent.boundary import source_diff
    assert source_diff({"filename": "a", "text": "同"}, {"filename": "b", "text": "同\n"})


def test_report_treats_names_and_summary_as_literal(boundary, tmp_path):
    """来源名和摘要中的 Markdown 图片及 HTML 均不成为活跃内容。"""
    (tmp_path / "![source](evil).txt").write_text("旧内容\n", encoding="utf-8")
    boundary.names["before"] = "![source](evil).txt"
    stage(boundary)
    boundary.summary = "![remote](evil) <img> &lt;"
    path = boundary.publish(tmp_path / "reports", {"finish_reason": "completed", "session_id": boundary.task_id})
    report = path.read_text(encoding="utf-8")
    assert "\\!\\[remote\\](evil) &lt;img&gt; &amp;lt;" in report
    assert "- \\!\\[source\\](evil).txt：" in report


@pytest.mark.parametrize("failure", ["exit", "timeout"])
def test_installer_falls_back_once_and_surfaces_official_failure(monkeypatch, failure):
    """镜像失败和超时都会回退，官方失败不被吞掉或无限重试。"""
    import subprocess
    from new_computer_download import prepare_agent
    calls = []
    def fake_download(command, **kwargs):
        """模拟镜像故障，再模拟官方源同类失败。"""
        calls.append(command[-1])
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 600)
        raise subprocess.CalledProcessError(1, command)
    monkeypatch.setattr(prepare_agent.subprocess, "run", fake_download)
    with pytest.raises((subprocess.CalledProcessError, subprocess.TimeoutExpired)):
        prepare_agent.download_wheels(["pip", "download"], prepare_agent.MIRROR)
    assert calls == [prepare_agent.MIRROR, prepare_agent.OFFICIAL]


@pytest.mark.parametrize("name,args", [
    ("bash", {"command": "echo bad"}), ("jac_read_text", {"source": "../../secret"}),
    ("jac_read_text", {"source": "before", "path": "/etc/passwd"}),
    ("jac_stage_report", {"summary": "无实际证据"}), ("jac_compare_texts", {}),
])
def test_unapproved_calls_refused(boundary, name, args):
    """禁止任意工具、路径扩权和未读取输入就写报告。"""
    with pytest.raises(AgentRefused):
        call(boundary, name, args)
    assert boundary.trace[-1]["status"] == "failed"


def test_symlink_leaf_and_directory_refused(boundary, tmp_path):
    """文件和工作目录符号链接均不能转移授权到另一个路径。"""
    (tmp_path / "a.txt").unlink()
    (tmp_path / "a.txt").symlink_to(tmp_path / "b.txt")
    with pytest.raises(OSError):
        call(boundary, "jac_read_text", {"source": "before"})
    (tmp_path / "linked").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(OSError):
        TaskBoundary(tmp_path / "linked", "a.txt", "b.txt", "x")


@pytest.mark.parametrize("data", [b"x" * 769, b"\xff", b"abc\x00def"])
def test_input_budget_encoding(boundary, tmp_path, data):
    """拒绝超限、非 UTF-8 和二进制 NUL 文本。"""
    (tmp_path / "a.txt").write_bytes(data)
    with pytest.raises((AgentRefused, UnicodeDecodeError)):
        call(boundary, "jac_read_text", {"source": "before"})


def test_cancel_and_changed_input_prevent_publication(boundary, tmp_path):
    """已生成草稿也不能绕过发布前的输入哈希和取消检查。"""
    stage(boundary)
    result = {"finish_reason": "completed", "session_id": boundary.task_id}
    (tmp_path / "a.txt").write_text("发生变化")
    with pytest.raises(AgentRefused, match="改变"):
        boundary.publish(tmp_path / "reports", result)
    boundary.cancel()
    with pytest.raises(AgentRefused, match="cancelled"):
        boundary.publish(tmp_path / "reports", result)
    assert not (tmp_path / "reports").exists()


def test_ids_dedup_and_failed_call_not_replayed(boundary):
    """重复成功调用复用结果；失败 ID 不重试；参数篡改被拒绝。"""
    first = call(boundary, "jac_read_text", {"source": "before"}, "once")
    assert call(boundary, "jac_read_text", {"source": "before"}, "once") == first
    assert len(boundary.trace) == 1
    with pytest.raises(AgentRefused):
        call(boundary, "jac_read_text", {"source": "after"}, "once")
    with pytest.raises(AgentRefused):
        call(boundary, "jac_compare_texts", {}, "bad")
    with pytest.raises(AgentRefused):
        call(boundary, "jac_compare_texts", {}, "bad")
    assert len(boundary.trace) == 2


def test_wrong_session_timeout_and_budget(boundary):
    """错误任务归属、超出工具次数和总时限均拒绝。"""
    with pytest.raises(AgentRefused, match="不属于"):
        boundary.execute(dict(name="jac_read_text", args={"source": "before"}, call_id="a", session_id="old"))
    for i in range(8):
        call(boundary, "jac_read_text", {"source": "before"}, str(i))
    with pytest.raises(AgentRefused, match="8 次"):
        call(boundary, "jac_read_text", {"source": "before"}, "9")
    boundary.deadline = 0
    with pytest.raises(AgentRefused, match="timeout"):
        call(boundary, "jac_read_text", {"source": "before"}, "10")


def test_single_task_and_environment(tmp_path, boundary, monkeypatch):
    """忙碌不排队；云密钥、代理和 Node 注入不传入运行时。"""
    with runner.serial_task(tmp_path / "cache"):
        with pytest.raises(AgentRefused, match="正忙"):
            with runner.serial_task(tmp_path / "cache"):
                pass
    monkeypatch.setenv("DEEPSEEK_API_KEY", "must-not-inherit")
    monkeypatch.setenv("NODE_OPTIONS", "must-not-inherit")
    with runner.ToolServer(boundary) as server:
        env = runner.child_environment(boundary, server, 8192)
    assert "DEEPSEEK_API_KEY" not in env and "NODE_OPTIONS" not in env and "HTTP_PROXY" not in env


@contextmanager
def fake_model(mode="normal"):
    """提供可检查请求的 SSE 模型，驱动实际官方 SDK/runtime 执行真实本地工具。"""
    requests = []
    class Handler(BaseHTTPRequestHandler):
        """只实现测试用 Chat Completions。"""
        def log_message(self, format, *args):
            """屏蔽测试访问噪声。"""
        def do_POST(self):
            """按顺序返回工具调用，或注入超时/HTTP 故障/思考输出。"""
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(request)
            if mode == "slow":
                time.sleep(2)
            if mode == "error":
                self.send_error(500)
                return
            steps = [("jac_read_text", {"source": "before"}), ("jac_read_text", {"source": "after"}),
                     ("jac_compare_texts", {}), ("jac_stage_report", {"summary": "由旧内容改为新内容。"})]
            index = len(requests) - 1
            if index < len(steps):
                name, args = steps[index]
                delta = {"tool_calls": [{"index": 0, "id": f"call_{index}", "type": "function",
                                         "function": {"name": name, "arguments": json.dumps(args)}}]}
                finish = "tool_calls"
            else:
                delta, finish = {"content": "草稿已提交。"}, "stop"
            if mode == "reasoning":
                delta = {"reasoning_content": "不允许输出的思考"}
                finish = "stop"
            chunks = [dict(id="mock", object="chat.completion.chunk", model=MODEL,
                           choices=[dict(index=0, delta=delta, finish_reason=None)]),
                      dict(id="mock", object="chat.completion.chunk", model=MODEL,
                           choices=[dict(index=0, delta={}, finish_reason=finish)],
                           usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120})]
            raw = ("".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
                   + "data: [DONE]\n\n").encode()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.skipif(not (ROOT / ".cache/agent/venv/bin/python").exists(), reason="独立 SDK 环境未安装")
@pytest.mark.parametrize("mode", ["normal", "error", "reasoning", "slow", "cancel"])
def test_real_sdk_wire_and_failures(tmp_path, monkeypatch, mode):
    """真实运行时验证线缆参数、权限工具、恢复、错误、取消和超时，无外部模型请求。"""
    (tmp_path / "a.txt").write_text("旧内容\n")
    (tmp_path / "b.txt").write_text("新内容\n")
    started = time.monotonic()
    with fake_model("slow" if mode == "cancel" else mode) as (endpoint, requests):
        monkeypatch.setattr(runner, "build_patch", lambda context: build_patch(context, endpoint))
        result = runner.run_comparison(tmp_path, "a.txt", "b.txt", output=tmp_path / "reports",
                                      context_reader=lambda: 8192, timeout=0.8 if mode == "slow" else 20,
                                      should_stop=lambda: mode == "cancel" and time.monotonic() - started > 0.8)
    if mode == "normal":
        assert result["status"] == "completed", result
        assert len(requests) == 5
        for request in requests:
            assert request["model"] == MODEL
            assert request["reasoning_effort"] == "none"
            assert request["max_tokens"] == 768
            assert {tool["function"]["name"] for tool in request["tools"]} == set(TOOLS)
            assert request["messages"][0]["role"] == "system"
        restored = runner.inspect_session(result["task_id"])
        assert restored["status"] == "restored" and not restored["actions_replayed"]
    else:
        assert result["status"] == {"slow": "timeout", "cancel": "cancelled"}.get(mode, "failed"), result
        assert not (tmp_path / "reports").exists()


def test_read_after_comparison_cannot_replace_evidence(boundary):
    """比较后读取和提交后重新比较被拒绝，差异与哈希保持同一批来源。"""
    stage(boundary)
    with pytest.raises(AgentRefused):
        call(boundary, "jac_read_text", {"source": "before"}, "late-read")
    with pytest.raises(AgentRefused):
        call(boundary, "jac_compare_texts", {}, "late-compare")


def test_output_symlink_does_not_create_external_directory(boundary, tmp_path):
    """逐级创建报告目录时不沿父级符号链接写入旁支目录。"""
    stage(boundary)
    external = tmp_path / "external"
    external.mkdir()
    (tmp_path / "linked-output").symlink_to(external, target_is_directory=True)
    with pytest.raises(OSError):
        boundary.publish(tmp_path / "linked-output" / "nested", {"finish_reason": "completed", "session_id": boundary.task_id})
    assert not (external / "nested").exists()


def test_cancel_predicate_rechecked_during_publication(boundary, tmp_path):
    """发布前直接检查外部取消判据，不依赖已经结束的等待循环。"""
    stage(boundary)
    boundary.should_stop = lambda: True
    with pytest.raises(AgentRefused, match="cancelled"):
        boundary.publish(tmp_path / "out", {"finish_reason": "completed", "session_id": boundary.task_id})
    assert not (tmp_path / "out").exists()


def test_false_completion_and_tool_auth(boundary, tmp_path):
    """模型回合结束不等于完成；HTTP 无凭据不得执行工具。"""
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen
    with pytest.raises(AgentRefused, match="证据"):
        boundary.publish(tmp_path / "out", {"finish_reason": "completed", "session_id": boundary.task_id})
    with runner.ToolServer(boundary) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with pytest.raises(HTTPError) as error:
                urlopen(Request(f"http://127.0.0.1:{server.server_port}/tool", data=b"{}"), timeout=2)
            assert error.value.code == 403 and not boundary.trace
        finally:
            server.shutdown()
            thread.join()
