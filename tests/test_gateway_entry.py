"""验证所有生产入口只进入 Gateway，帮助与安装自检不启动设备。"""
import subprocess
import sys
from pathlib import Path

import pytest
import main

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("args", [[], ["--gui"], ["--gateway", "--gui"]])
def test_main_routes_only_to_gateway(monkeypatch, args):
    """旧兼容参数移除后，其余参数原样传给唯一 Gateway 入口。"""
    from src.omni import gateway_cli
    received = []
    def capture(argv):
        """只记录参数，不创建 GUI 或访问设备。"""
        received.append(argv)
        return 7
    monkeypatch.setattr(gateway_cli, "main", capture)
    assert main.main(args) == 7
    assert received == [[arg for arg in args if arg != "--gateway"]]


@pytest.mark.parametrize("entry", [["main.py"], ["-m", "src.omni"]])
def test_help_supports_gateway_test_parameters_without_legacy_imports(entry):
    """两个独立进程入口均提供真实采集与会话参数，不依赖旧 :9060 栈。"""
    result = subprocess.run([sys.executable, *entry, "--help"], cwd=ROOT,
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert all(option in result.stdout for option in ("--mic-gain", "--fps", "--retry-limit", "--session-seconds"))
    assert "--model-dir" not in result.stdout and "--listen-prob-scale" not in result.stdout


def test_import_and_installer_default_are_device_free():
    """当前安装阶段默认 Gateway，导入 GUI 时不加载旧模型依赖。"""
    code = ("import sys; import gui, main; "
            "assert not any(name in sys.modules for name in "
            "['torch', 'pyaudio', 'whisper', 'ultralytics', 'src.omni.client']); print('ok')")
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    install = subprocess.run([sys.executable, "new_computer_download/setup_new_computer.py", "--dry-run"],
                             cwd=ROOT, text=True, capture_output=True, timeout=10)
    assert install.returncode == 0
    assert ".cache/m0/venv" in install.stdout and "requirements-m0.txt" in install.stdout
