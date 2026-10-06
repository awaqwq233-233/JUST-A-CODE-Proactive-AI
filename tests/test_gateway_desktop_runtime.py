"""轻量桌面适配器的设备同意和启动失败状态测试，不打开设备。"""

import sys

import pytest

from src.omni import desktop_runtime as desktop
from src.utils.config import Config
from src.utils.context import SharedContext


@pytest.mark.skipif(sys.version_info[:2] != (3, 11), reason="生产 Gateway GUI 使用 Python 3.11")
def test_desktop_requires_consent_without_constructing_client(monkeypatch):
    """未同意前不能构造客户端或访问设备。"""
    calls = []

    def forbidden(**kwargs):
        """未授权路径若构造客户端则立即失败。"""
        pytest.fail("未同意时构造了设备客户端")

    monkeypatch.setattr(desktop, "GatewayClient", forbidden)
    runtime = desktop.GatewayRuntime(SharedContext(), calls.append)
    runtime.start(Config(omni_enabled=True))
    assert calls == [False] and not runtime.running


@pytest.mark.skipif(sys.version_info[:2] != (3, 11), reason="生产 Gateway GUI 使用 Python 3.11")
def test_desktop_failed_start_stops_client_and_resets_state(monkeypatch):
    """启动失败后清理客户端，允许用户在 GUI 再次启动。"""
    created, states = [], []

    class FailedClient:
        """模拟不能进入 ready 的连接。"""

        def __init__(self, **kwargs):
            """保存停止计数，不打开任何硬件。"""
            self.stopped = False
            created.append(self)

        def start(self, timeout):
            """模拟初始化失败。"""
            return False

        def stop(self):
            """记录失败后的资源清理。"""
            self.stopped = True

    monkeypatch.setattr(desktop, "GatewayClient", FailedClient)
    runtime = desktop.DesktopRuntime(on_state_change=states.append)
    runtime.start(Config(omni_enabled=True, gateway_consent_devices=True))
    assert not runtime.running and not runtime.omni_mode
    assert created[0].stopped and runtime.omni_client is None
    assert states[-1] is False


def test_config_gateway_defaults_and_environment(monkeypatch):
    """新路径默认连接本机且不同意设备，设备编号和轮换时间可配置。"""
    config = Config()
    assert config.omni_backend == "gateway" and not config.gateway_consent_devices
    monkeypatch.setenv("JAC_INPUT_DEVICE", "3")
    monkeypatch.setenv("JAC_OUTPUT_DEVICE", "1")
    monkeypatch.setenv("JAC_GATEWAY_SESSION_SECONDS", "60")
    loaded = Config.load()
    assert loaded.gateway_input_device == 3 and loaded.gateway_output_device == 1
    assert loaded.gateway_session_seconds == 60
