"""轻量桌面运行时；Gateway 路径不导入旧 Whisper/YOLO/PyAudio 栈。"""

from pathlib import Path
import sys

from .gateway_client import GatewayClient, GatewayCallbacks, ROOT
from src.utils.context import SharedContext


class DesktopCallbacks(GatewayCallbacks):
    """桥接 Gateway 状态、原生音频与独立的对话文本。"""

    def __init__(self, runtime):
        """持有当前运行时和共享上下文。"""
        self.runtime = runtime

    def on_state(self, state, info=None):
        """显示重连状态，在异常时恢复 GUI 可启动状态。"""
        print(f"[Gateway] {state}" + ("（采集暂停）" if state == "reconnecting" else ""))
        self.runtime.state = state
        if state in {"connecting", "reconnecting", "closed", "error"}:
            self.runtime.finish_reply()
        ctx = self.runtime.context
        if state in {"connecting", "reconnecting", "closed", "error", "ready"}:
            ctx.is_listening = ctx.is_speaking = ctx.is_thinking = False
        if state == "error":
            self.runtime.running = False
            self.runtime.omni_mode = False
            self.runtime.notify(False)

    def on_listen(self):
        """同步聆听状态灯。"""
        self.runtime.finish_reply()
        self.runtime.context.is_listening = True
        self.runtime.context.is_speaking = False

    def on_audio_chunk(self, pcm_bytes):
        """同步原生音频播放状态灯。"""
        self.runtime.context.is_speaking = True
        self.runtime.context.is_listening = False

    def on_text_delta(self, text):
        """将回复增量交给独立文本回调，终端调用保留标准输出。"""
        if self.runtime.text_callback:
            self.runtime.text_callback(text)
        else:
            print(text, end="", flush=True)

    def on_error(self, error):
        """打印不含原始媒体的异常类型。"""
        print(f"[Gateway] 错误：{error}")

    def on_user_transcript(self, text):
        """仅缓存确认的用户原话，独立投递至 GUI，不混入助手增量。"""
        self.runtime.context.push_transcription(text)
        if self.runtime.transcript_callback:
            self.runtime.transcript_callback(text)
        else:
            print(f"\n[bo s s] {text}", flush=True)

    def on_task_event(self, state, detail):
        """同步大脑进度和真实报告；错误不宣称任务已完成。"""
        if state != "rejected":
            self.runtime.context.is_thinking = state == "running"
        if self.runtime.task_callback:
            self.runtime.task_callback(state, detail)
        else:
            print(f"\n[大脑] {state}" + (f"：{detail['path']}" if state == "completed" else ""), flush=True)


class GatewayRuntime:
    """独立 Gateway 桌面适配器，生命周期由 GUI 控制。"""

    def __init__(self, context=None, on_state_change=None):
        """绑定上下文，不访问网络或设备。"""
        self.context, self.on_state_change = context or SharedContext(), on_state_change
        self.state = "stopped"
        self.text_callback = self.reply_finished_callback = None
        self.transcript_callback = self.task_callback = None
        self.running = self.omni_mode = False
        self.omni_client = None

    def notify(self, running):
        """向 GUI 发布启动/停止结果。"""
        if self.on_state_change:
            self.on_state_change(running)

    def start(self, config):
        """同意后连接固定 Gateway，失败时保持 GUI 可再次启动。"""
        if config.omni_backend != "gateway":
            raise ValueError("旧 MiniCPM 后端已移除，请使用固定 Gateway")
        if self.omni_client is not None:
            raise RuntimeError("上一客户端尚未释放，请先停止")
        if sys.version_info[:2] != (3, 11):
            self.notify(False)
            raise RuntimeError("方案 B Gateway GUI 必须使用 Python 3.11 环境")
        if not config.gateway_consent_devices:
            print("[Gateway] 请先授权使用设备并戴好耳机。")
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
                session_seconds=config.gateway_session_seconds, retry_limit=config.gateway_retry_limit,
                context_provider=lambda: self.context.get_recent_transcriptions(window=300),
                transcription_enabled=config.gateway_transcription_enabled,
                whisper_model_dir=config.whisper_model_dir, brain_url=config.gateway_brain_url,
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
        self.finish_reply()
        client = self.omni_client
        if client:
            client.stop()
            if self.omni_client is client:
                self.omni_client = None
        self.state = "stopped"
        self.context.is_listening = self.context.is_speaking = self.context.is_thinking = False
        self.notify(False)

    def finish_reply(self):
        """结束一段模型文本，GUI 回调负责排队到主线程。"""
        if self.reply_finished_callback:
            self.reply_finished_callback()


class DesktopRuntime(GatewayRuntime):
    """桌面只有 Gateway 路径，不再懒加载旧 MiniCPM 或传统运行时。"""
