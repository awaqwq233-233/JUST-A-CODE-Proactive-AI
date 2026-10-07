#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""启动方案 B 的独立 M0 后端，Ctrl+C 只停止本脚本启动的子进程。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
from contextlib import ExitStack
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def check_checkout(path: Path, expected: str, patch: Path | None = None) -> None:
    """验证 commit，且仅接受 lock 指定的已审阅本机监听补丁。"""
    result = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], check=True, capture_output=True, text=True)
    if result.stdout.strip() != expected:
        raise ValueError(f"仓库 commit 不符: {path}")
    dirty = subprocess.run(["git", "-C", str(path), "diff", "HEAD", "--no-ext-diff"], check=True, capture_output=True, text=True)
    expected_diff = patch.read_text(encoding="utf-8") if patch else ""
    if dirty.stdout.strip() != expected_diff.strip():
        raise ValueError(f"仓库源码与锁定补丁不符: {path}")


def check_model_files(directory: Path, files: dict[str, str], verify_sha: bool) -> None:
    """检查全部模型模块，按需逐块校验 SHA256，不加载模型进内存。"""
    if ROOT == directory or ROOT in directory.parents:
        raise ValueError("模型目录必须在项目仓库外")
    for relative, expected in files.items():
        path = directory / relative
        if not path.is_file():
            raise ValueError(f"缺少模型模块: {relative}")
        with path.open("rb") as stream:
            if stream.read(4) != b"GGUF":
                raise ValueError(f"模型不是 GGUF 或尚未下载完整: {relative}")
            if verify_sha:
                stream.seek(0)
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
                if actual != expected:
                    raise ValueError(f"模型 SHA256 不符: {relative}")
        print(f"模型校验通过: {relative}", flush=True)


def ensure_free_ports(ports: dict[str, int]) -> None:
    """确认所有本地端口可绑定，避免接管现有用户服务。"""
    for name, port in ports.items():
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError as error:
                raise ValueError(f"端口已占用: {name} {port}") from error


def prepare_demo_config(demo: Path) -> None:
    """为新克隆生成不录制会话的配置，已有配置只验证而不覆盖。"""
    config_path = demo / "config.json"
    if config_path.exists():
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if config.get("recording", {}).get("enabled") is not False:
            raise ValueError("M0 要求 Demo config.json 的 recording.enabled=false")
        return
    config = {
        "recording": {"enabled": False},
        "service": {"data_dir": str(ROOT / "output/m0/upstream")},
    }
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def wait_health(client: httpx.Client, url: str, children: list, seconds: float = 180) -> None:
    """等待本地健康接口就绪，并检测启动失败的子进程。"""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        for name, child in children:
            if child.poll() is not None:
                raise RuntimeError(f"{name} 提前退出，详见 output/m0/{name}.log")
        try:
            response = client.get(url, timeout=2)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise TimeoutError(f"等待服务就绪超时: {url}")


def terminate_children(children: list) -> None:
    """逆序停止本脚本拥有的子进程，并回收退出状态。"""
    for _, child in reversed(children):
        if child.poll() is None:
            child.terminate()
    for _, child in reversed(children):
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)


def report_event(args, state: str, detail: str = '') -> None:
    """GUI 模式输出有标识的机器事件，原终端输出保持可读。"""
    if getattr(args, 'events_json', False):
        print(json.dumps({'event': 'jac.backend', 'state': state, 'detail': detail},
                         ensure_ascii=False), flush=True)


def launch(args, lock: dict) -> None:
    """按 Backend、Worker、Gateway 顺序启动并注册固定版本服务。"""
    ports = lock["ports"]
    prepare_demo_config(args.demo_dir)
    logs = ROOT / "output/m0"
    logs.mkdir(parents=True, exist_ok=True)
    children = []
    env = dict(os.environ, PYTHONUNBUFFERED="1", NUMBA_CACHE_DIR=str(ROOT / ".cache/m0/numba"))
    commands = [
        ("backend", [str(args.engine_dir / "build/bin/llama-omni-server"),
                     "-m", str(args.model_dir / "MiniCPM-o-4_5-Q4_K_M.gguf"),
                     "--host", "127.0.0.1", "--port", str(ports["backend"]),
                     "-ngl", "99", "-c", "4096", "-t", "8"], args.engine_dir),
        ("worker", [sys.executable, "worker.py", "--host", "127.0.0.1", "--port", str(ports["worker"]),
                    "--backend-server-url", f"http://127.0.0.1:{ports['backend']}"], args.demo_dir),
        ("gateway", [sys.executable, "gateway.py", "--host", "127.0.0.1", "--port", str(ports["gateway"]),
                     "--internal-port", str(ports["registry"]), "--http"], args.demo_dir),
    ]
    with ExitStack() as stack, httpx.Client(trust_env=False) as client:
        try:
            for name, command, cwd in commands:
                report_event(args, 'phase', {'backend': '正在加载 Metal 模型…',
                             'worker': '正在启动 Worker…', 'gateway': '正在启动 Gateway…'}[name])
                log = stack.enter_context((logs / f"{name}.log").open("ab"))
                child = subprocess.Popen(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT)
                children.append((name, child))
                wait_health(client, f"http://127.0.0.1:{ports[name]}/health", children)
                print(f"{name} 已就绪 PID={child.pid}", flush=True)
            wait_health(client, f"http://127.0.0.1:{ports['registry']}/health", children, seconds=10)
            registration = client.put(
                f"http://127.0.0.1:{ports['registry']}/internal/workers/jac-m0",
                json={"endpoint": f"127.0.0.1:{ports['worker']}", "gpu_group": "local-metal"},
            )
            registration.raise_for_status()
            print(f"M0 后端就绪: {lock['protocol']['url']}；Ctrl+C 停止。", flush=True)
            report_event(args, 'ready', lock['protocol']['url'])
            while True:
                for name, child in children:
                    if child.poll() is not None:
                        raise RuntimeError(f"{name} 异常退出，详见日志")
                time.sleep(0.5)
        finally:
            terminate_children(children)


def stop_on_signal(signum, frame) -> None:
    """把终止信号转换为可触发子进程回收的中断。"""
    raise KeyboardInterrupt


def main() -> int:
    """校验 Python、源码、模型和端口，然后启动或只做预检。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo-dir", type=Path, required=True)
    parser.add_argument("--engine-dir", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--verify-sha", action="store_true", help="逐文件验证 lock 中的 SHA256")
    parser.add_argument("--preflight", action="store_true", help="只做检查，不启动服务或写配置")
    parser.add_argument('--events-json', action='store_true', help='GUI 使用的结构化生命周期事件')
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 11):
        parser.error("M0 必须使用 Python 3.11")
    args.demo_dir = args.demo_dir.expanduser().resolve()
    args.engine_dir = args.engine_dir.expanduser().resolve()
    args.model_dir = args.model_dir.expanduser().resolve()
    lock = json.loads((ROOT / "backend.lock.json").read_text(encoding="utf-8"))
    signal.signal(signal.SIGTERM, stop_on_signal)
    try:
        report_event(args, 'phase', '正在校验后端版本与模型…')
        check_checkout(args.demo_dir, lock["repositories"]["demo"]["commit"])
        engine = lock["repositories"]["engine"]
        check_checkout(args.engine_dir, engine["commit"], ROOT / engine["patch"])
        if not (args.engine_dir / "build/bin/llama-omni-server").is_file():
            raise ValueError("尚未编译 llama-omni-server")
        cache = (args.engine_dir / "build/CMakeCache.txt").read_text(encoding="utf-8")
        if "LLAMA_OPENSSL:BOOL=OFF" not in cache:
            raise ValueError("本机 HTTP 内部链路必须以 -DLLAMA_OPENSSL=OFF 构建")
        check_model_files(args.model_dir, lock["model"]["files"], args.verify_sha)
        ensure_free_ports(lock["ports"])
        if args.preflight:
            print("M0 源码、模型与端口预检通过")
        else:
            launch(args, lock)
        return 0
    except KeyboardInterrupt:
        print("M0 后端已停止")
        return 0
    except (OSError, ValueError, RuntimeError, TimeoutError, subprocess.CalledProcessError, httpx.HTTPError) as error:
        print(f"M0 未通过: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
