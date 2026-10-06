"""方案 B M0 启动器的安全预检与隔离安装测试。"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest


def load_module(name: str, relative: str):
    """独立加载脚本，避免测试触发安装器或模型启动入口。"""
    source = Path(__file__).resolve().parents[1] / relative
    spec = importlib.util.spec_from_file_location(name, source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


launcher = load_module("jac_m0_launcher", "new_computer_download/start_m0_backend.py")
installer = load_module("jac_m0_installer", "new_computer_download/setup_new_computer.py")


def test_checkout_accepts_only_expected_commit_and_exact_patch(tmp_path, monkeypatch):
    """确保已批准补丁可以通过，但其他源码漂移被拒绝。"""
    patch = tmp_path / "loopback.patch"
    patch.write_text("expected diff\n", encoding="utf-8")
    replies = iter(["pinned\n", "expected diff\n", "pinned\n", "other diff\n", "wrong\n"])
    monkeypatch.setattr(launcher.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=next(replies)))
    launcher.check_checkout(tmp_path, "pinned", patch)
    with pytest.raises(ValueError, match="补丁不符"):
        launcher.check_checkout(tmp_path, "pinned", patch)
    with pytest.raises(ValueError, match="commit 不符"):
        launcher.check_checkout(tmp_path, "pinned", patch)


def test_models_are_external_complete_gguf_and_hash_verified(tmp_path, monkeypatch):
    """模型在仓库外且文件头、SHA256 全部正确才接受。"""
    monkeypatch.setattr(launcher, "ROOT", tmp_path / "project")
    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUFfixture")
    digest = hashlib.sha256(model.read_bytes()).hexdigest()
    launcher.check_model_files(tmp_path, {model.name: digest}, True)
    with pytest.raises(ValueError, match="SHA256"):
        launcher.check_model_files(tmp_path, {model.name: "0" * 64}, True)
    with pytest.raises(ValueError, match="缺少模型"):
        launcher.check_model_files(tmp_path, {"missing.gguf": digest}, False)
    model.write_bytes(b"html download error")
    with pytest.raises(ValueError, match="不是 GGUF"):
        launcher.check_model_files(tmp_path, {model.name: digest}, False)
    with pytest.raises(ValueError, match="仓库外"):
        launcher.check_model_files(launcher.ROOT / "models", {}, False)


def test_config_disables_recording_and_preserves_existing_settings(tmp_path):
    """新配置禁用录制，已有配置不被覆盖，隐私设置不安全时拒绝启动。"""
    launcher.prepare_demo_config(tmp_path)
    config_path = tmp_path / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["recording"]["enabled"] is False
    config["service"]["custom"] = "keep"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    previous = config_path.read_bytes()
    launcher.prepare_demo_config(tmp_path)
    assert config_path.read_bytes() == previous
    config["recording"]["enabled"] = True
    config_path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError, match="recording.enabled=false"):
        launcher.prepare_demo_config(tmp_path)


def test_occupied_port_is_not_taken_over():
    """本机端口已被其他服务占用时，启动器明确失败且不干预该服务。"""
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        port = occupied.getsockname()[1]
        with pytest.raises(ValueError, match="端口已占用"):
            launcher.ensure_free_ports({"gateway": port})
    launcher.ensure_free_ports({"gateway": port})


def test_wait_health_handles_transient_http_failures(monkeypatch):
    """健康检查短暂失败会重试，返回 200 后结束等待。"""
    results = iter([SimpleNamespace(status_code=503), httpx.ConnectError("not ready"), SimpleNamespace(status_code=200)])

    def get_health(*args, **kwargs):
        """按顺序模拟启动中的 HTTP 状态。"""
        result = next(results)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(launcher.time, "sleep", lambda seconds: None)
    launcher.wait_health(SimpleNamespace(get=get_health), "http://127.0.0.1/health", [])


def test_wait_health_rejects_exited_child_and_timeout():
    """子进程已退出或健康检查超时，不能继续启动后续组件。"""
    with pytest.raises(RuntimeError, match="提前退出"):
        launcher.wait_health(None, "http://127.0.0.1", [("backend", SimpleNamespace(poll=lambda: 1))])
    with pytest.raises(TimeoutError):
        launcher.wait_health(None, "http://127.0.0.1", [], seconds=0)


def test_cleanup_terminates_only_owned_children_in_reverse_order():
    """逆序停止拥有的子进程；超时后仅强制回收该子进程。"""
    calls = []

    class Child:
        """模拟启动器拥有的进程对象，不创建真实服务。"""

        def __init__(self, name, *, exited=False, stuck=False):
            """保存退出状态和超时场景。"""
            self.name, self.exited, self.stuck = name, exited, stuck

        def poll(self):
            """返回进程是否已经退出。"""
            return 0 if self.exited else None

        def terminate(self):
            """记录温和终止调用。"""
            calls.append((self.name, "terminate"))

        def wait(self, timeout):
            """模拟卡住的子进程直到强制结束。"""
            if self.stuck:
                raise subprocess.TimeoutExpired(self.name, timeout)
            return 0

        def kill(self):
            """记录强制终止并解除模拟卡住状态。"""
            calls.append((self.name, "kill"))
            self.stuck = False

    launcher.terminate_children([
        ("backend", Child("backend", stuck=True)),
        ("worker", Child("worker", exited=True)),
        ("gateway", Child("gateway")),
    ])
    assert calls == [("gateway", "terminate"), ("backend", "terminate"), ("backend", "kill")]


def test_installer_dry_run_never_executes_or_writes(tmp_path, monkeypatch):
    """M0 预览不创建环境、不下载依赖，也不改旧 .venv。"""
    monkeypatch.setattr(installer, "PROJECT_ROOT", str(tmp_path))

    def forbidden(*args, **kwargs):
        """若预览错误执行外部命令，则立即失败。"""
        pytest.fail("dry-run 执行了外部命令")

    monkeypatch.setattr(installer, "run_cmd", forbidden)
    args = SimpleNamespace(mirror=None, no_mirror=False, dry_run=True)
    assert installer.step_m0(args) is True
    assert list(tmp_path.iterdir()) == []


def test_installer_rejects_existing_wrong_environment(tmp_path, monkeypatch):
    """已有 M0 环境版本错误时停止，不重新创建或安装依赖。"""
    monkeypatch.setattr(installer, "PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(installer.os.path, "exists", lambda path: True)
    calls = []

    def command(arguments, **kwargs):
        """只模拟 Python 版本查询，并记录执行范围。"""
        calls.append(arguments)
        return SimpleNamespace(stdout="3.13\n", returncode=0)

    monkeypatch.setattr(installer, "run_cmd", command)
    assert installer.step_m0(SimpleNamespace(mirror=None, no_mirror=False, dry_run=False)) is False
    assert len(calls) == 1 and "-c" in calls[0]


def test_installer_mirror_failure_falls_back_without_disabling_tls(tmp_path, monkeypatch):
    """镜像失败时只切换官方 HTTPS 源，不关闭证书校验或覆盖生产环境。"""
    monkeypatch.setattr(installer, "PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(installer.os.path, "exists", lambda path: True)
    calls = []

    def command(arguments, **kwargs):
        """依次模拟正确版本、镜像失败和官方安装成功。"""
        calls.append(arguments)
        return SimpleNamespace(stdout="3.11\n", returncode=1 if len(calls) == 2 else 0)

    monkeypatch.setattr(installer, "run_cmd", command)
    assert installer.step_m0(SimpleNamespace(mirror=None, no_mirror=False, dry_run=False)) is True
    assert calls[-2][-1] == installer.OFFICIAL_PIP_INDEX
    assert "PySide6" in calls[-1][-1] and "GatewayClient" in calls[-1][-1]
    assert all("--trusted-host" not in call for call in calls)
    assert all(str(tmp_path / ".venv") not in argument for call in calls for argument in call)


def test_installer_rejects_dependency_import_failure(tmp_path, monkeypatch):
    """pip 成功但 GUI/客户端无法导入时，安装结果必须失败。"""
    monkeypatch.setattr(installer, "PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(installer.os.path, "exists", lambda path: True)
    calls = []

    def command(arguments, **kwargs):
        """模拟版本正确、安装成功，但导入自检失败。"""
        calls.append(arguments)
        return SimpleNamespace(stdout="3.11\n", stderr="missing dependency", returncode=1 if len(calls) == 3 else 0)

    monkeypatch.setattr(installer, "run_cmd", command)
    assert installer.step_m0(SimpleNamespace(mirror=None, no_mirror=False, dry_run=False)) is False
