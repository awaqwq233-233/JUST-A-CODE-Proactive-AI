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


def test_retired_backend_cannot_construct_any_client(monkeypatch):
    """旧配置须明确失败，不得回落到其他模型或隐式开启设备。"""
    def forbidden(**kwargs):
        """阻止旧配置构造媒体客户端。"""
        pytest.fail("旧配置触发了客户端构造")
    monkeypatch.setattr(desktop, "GatewayClient", forbidden)
    with pytest.raises(ValueError, match="已移除"):
        desktop.DesktopRuntime().start(Config(omni_backend="legacy", gateway_consent_devices=True))


def test_gui_parameters_reach_gateway_client(monkeypatch):
    """设备与采集参数真正传入生产客户端，而非只改变 GUI 显示。"""
    captured = {}
    class ReadyClient:
        """无需设备的已就绪客户端。"""
        def __init__(self, **kwargs):
            """保存传入的真实客户端参数。"""
            captured.update(kwargs)
        def start(self, timeout):
            """模拟完成启动。"""
            return True
        def stop(self):
            """无设备可释放。"""
    monkeypatch.setattr(desktop, "GatewayClient", ReadyClient)
    runtime = desktop.DesktopRuntime()
    runtime.start(Config(gateway_consent_devices=True, gateway_input_device=4,
        gateway_output_device=6, gateway_camera=2, omni_fps=8, omni_mic_gain=2.5,
        gateway_session_seconds=60, gateway_retry_limit=2, omni_video_enabled=False))
    assert (captured['input_device'], captured['output_device'], captured['camera']) == (4, 6, 2)
    assert (captured['video_fps'], captured['mic_gain'], captured['session_seconds'], captured['retry_limit']) == (8, 2.5, 60, 2)
    assert not captured['video_enabled']
    runtime.stop()


def test_callbacks_separate_reply_and_connection_state():
    """重连结束当前显示段，清除听说状态；文本不混入连接日志。"""
    runtime = desktop.DesktopRuntime()
    chunks, finishes = [], []
    runtime.text_callback = chunks.append
    runtime.reply_finished_callback = lambda: finishes.append(True)
    callbacks = desktop.DesktopCallbacks(runtime)
    callbacks.on_text_delta("你好")
    assert chunks == ["你好"]
    callbacks.on_audio_chunk(b"")
    assert runtime.context.is_speaking
    callbacks.on_state("reconnecting")
    assert runtime.state == "reconnecting" and finishes
    assert not runtime.context.is_listening and not runtime.context.is_speaking
