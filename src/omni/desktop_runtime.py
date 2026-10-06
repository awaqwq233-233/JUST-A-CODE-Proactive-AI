"""轻量桌面运行时；Gateway 路径不导入旧 Whisper/YOLO/PyAudio 栈。"""

from pathlib import Path
import sys

from .gateway_client import GatewayClient, GatewayCallbacks, ROOT
from src.utils.context import SharedContext


class DesktopCallbacks(GatewayCallbacks):
    """桥接已有 GUI 的状态、音量与实时文本，不触发旧升级执行器。"""

    def __init__(self, runtime):
        """持有当前运行时和共享上下文。"""
        self.runtime = runtime

    def on_state(self, state, info=None):
        """显示重连状态，在异常时恢复 GUI 可启动状态。"""
        print(f"[Gateway] {state}" + ("（采集暂停）" if state == "reconnecting" else ""))
        ctx = self.runtime.context
        if state in {"connecting", "reconnecting", "closed", "error", "ready"}:
            ctx.is_listening = ctx.is_speaking = ctx.is_thinking = False
        if state == "error":
            self.runtime.running = False
            self.runtime.omni_mode = False
            self.runtime.notify(False)

    def on_listen(self):
        """同步聆听状态灯。"""
        self.runtime.context.is_listening = True
        self.runtime.context.is_speaking = False

    def on_audio_chunk(self, pcm_bytes):
        """同步原生音频播放状态灯。"""
        self.runtime.context.is_speaking = True
        self.runtime.context.is_listening = False

    def on_text_delta(self, text):
        """把回复增量写入已有 GUI 的控制台重定向。"""
        print(text, end="", flush=True)

    def on_error(self, error):
        """打印不含原始媒体的异常类型。"""
        print(f"[Gateway] 错误：{error}")


class GatewayRuntime:
    """独立 Gateway 桌面适配器，生命周期由 GUI 控制。"""

    def __init__(self, context, on_state_change=None):
        """绑定上下文，不访问网络或设备。"""
        self.context, self.on_state_change = context, on_state_change
        self.running = self.omni_mode = False
        self.omni_client = None

    def notify(self, running):
        """向 GUI 发布启动/停止结果。"""
        if self.on_state_change:
            self.on_state_change(running)

    def start(self, config):
        """同意后连接固定 Gateway，失败时保持 GUI 可再次启动。"""
        if sys.version_info[:2] != (3, 11):
            self.notify(False)
            raise RuntimeError("方案 B Gateway GUI 必须使用 Python 3.11 环境")
        if not config.gateway_consent_devices:
            print("[Gateway] 请先勾选设备同意并戴好耳机。")
            self.notify(False)
            return
        ref = Path(config.omni_ref_audio)
        if not ref.is_absolute():
            ref = ROOT / ref
        try:
            self.omni_client = GatewayClient(
                url=config.gateway_url, ref_audio_path=ref, callbacks=DesktopCallbacks(self),
                consent_devices=True, input_device=config.gateway_input_device,
                output_device=config.gateway_output_device, camera=config.gateway_camera, video_fps=config.omni_fps,
                video_enabled=config.omni_video_enabled, mic_gain=config.omni_mic_gain,
                session_seconds=config.gateway_session_seconds,
                context_provider=lambda: self.context.get_recent_transcriptions(window=300),
            )
            self.running = self.omni_mode = True
            if not self.omni_client.start(timeout=180):
                self.stop()
                return
            self.notify(True)
        except Exception:
            self.stop()
            raise

    def stop(self):
        """关闭客户端和设备，保留外部 Gateway/Worker/Metal 后端。"""
        self.running = self.omni_mode = False
        if self.omni_client:
            self.omni_client.stop()
            self.omni_client = None
        self.notify(False)

    def manual_input(self, text):
        """解释 M1 的语音输入边界，不伪装文字任务已被执行。"""
        if (text or "").strip():
            print("[Gateway] M1 请使用语音交流；文字任务和 Qwen 工具升级待后续接入。")


class DesktopRuntime:
    """按启动配置懒选新旧运行时，复用同一 GUI 上下文。"""

    def __init__(self, context=None, on_state_change=None):
        """创建轻量外观，导入 GUI 不会启动旧模型或加载旧依赖。"""
        self.context = context or SharedContext()
        self.on_state_change = on_state_change
        self._delegate = None

    @property
    def running(self):
        """返回当前实现的运行状态。"""
        return bool(self._delegate and self._delegate.running)

    @property
    def omni_mode(self):
        """返回 GUI 是否应读取全双工客户端帧。"""
        return bool(self._delegate and self._delegate.omni_mode)

    @property
    def omni_client(self):
        """返回 GUI 当前显示的客户端。"""
        return self._delegate.omni_client if self._delegate else None

    def start(self, config):
        """Gateway 使用轻量路径，仅选择旧模式时加载完整旧运行时。"""
        if self.running:
            return
        if self._delegate:
            self._delegate.stop()
        if config.omni_enabled and config.omni_backend == "gateway":
            self._delegate = GatewayRuntime(self.context, self.on_state_change)
        else:
            import main
            from src.runtime import JACRuntime
            main.context = self.context
            self._delegate = JACRuntime(self.context, self.on_state_change)
        self._delegate.start(config)

    def stop(self):
        """停止当前实现，GUI 保持打开。"""
        if self._delegate:
            self._delegate.stop()

    def manual_input(self, text):
        """把手动输入交给当前运行时。"""
        if self._delegate:
            self._delegate.manual_input(text)
