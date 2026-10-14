"""验证所有生产入口只进入 Gateway，帮助与安装自检不启动设备。"""
import subprocess
import sys
from pathlib import Path

import pytest
import main

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("args, expected", [
    ([], ["--gui"]), (["--gateway"], ["--gui"]), (["--gui"], ["--gui"]),
    (["--gateway", "--gui"], ["--gui"]),
    (["--gateway", "--consent-devices"], ["--consent-devices"]),
    (["--input-file", "fixture.wav"], ["--input-file", "fixture.wav"]),
    (["--help"], ["--help"]),
])
def test_main_routes_only_to_gateway(monkeypatch, args, expected):
    """直接运行打开 GUI，显式终端、文件和帮助参数保持原样。"""
    from src.omni import gateway_cli
    received = []
    def capture(argv):
        """只记录参数，不创建 GUI 或访问设备。"""
        received.append(argv)
        return 7
    monkeypatch.setattr(gateway_cli, "main", capture)
    assert main.main(args) == 7
    assert received == [expected]


def test_bootstrap_reuses_project_environment_before_imports(tmp_path, monkeypatch):
    """错误解释器切换时保留所有实参，并验证环境身份防止循环重启。"""
    from types import SimpleNamespace
    monkeypatch.setattr(main, "ROOT", tmp_path)
    environment = tmp_path / ".cache/m0/venv"
    python = environment / ("Scripts/python.exe" if main.os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(main.sys, "argv", ["main.py", "--gui", "--no-transcription"])
    monkeypatch.setattr(main.subprocess, "run", lambda *a, **k: SimpleNamespace(
        stdout=f"(3, 11)\n{environment}\n"))
    calls = []
    monkeypatch.setattr(main.os, "execv", lambda *args: calls.append(args))
    main.ensure_runtime()
    assert calls == [(str(python), [str(python), str(tmp_path / "main.py"), "--gui", "--no-transcription"])]
    monkeypatch.setattr(main.subprocess, "run", lambda *a, **k: SimpleNamespace(
        stdout=f"(3, 13)\n{environment}\n"))
    with pytest.raises(RuntimeError, match="不是有效的 Python 3.11"):
        main.ensure_runtime()
    assert len(calls) == 1


def test_bootstrap_does_not_restart_selected_environment(tmp_path, monkeypatch):
    """正确项目环境不重启，保留 F5 的调试器连接。"""
    monkeypatch.setattr(main, "ROOT", tmp_path)
    monkeypatch.setattr(main.sys, "prefix", str(tmp_path / ".cache/m0/venv"))
    monkeypatch.setattr(main.sys, "version_info", (3, 11, 0))

    def forbidden(*args, **kwargs):
        """当前环境正确时不应执行外部进程。"""
        pytest.fail("重复启动 Python")

    monkeypatch.setattr(main.subprocess, "run", forbidden)
    monkeypatch.setattr(main.os, "execv", forbidden)
    main.ensure_runtime()


def test_bootstrap_missing_environment_fails_before_dependency_import(tmp_path, monkeypatch):
    """旧 Python 且项目未安装时提供安装提示，而非 numpy 导入错误。"""
    monkeypatch.setattr(main, "ROOT", tmp_path)
    monkeypatch.setattr(main.sys, "version_info", (3, 13, 0))
    with pytest.raises(RuntimeError, match="未找到项目 Python 3.11"):
        main.ensure_runtime()


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
