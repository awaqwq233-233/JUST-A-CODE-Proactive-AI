"""Gateway 运行配置；旧 MiniCPM、判断轮询与 Voicebox 设置已退役。"""
import os
from dataclasses import dataclass


@dataclass
class Config:
    """GUI 所有采集与会话参数只在下一次启动生效。"""

    # 保留字段便于兼容既有 Gateway 调用；旧后端值不再可启动。
    omni_enabled: bool = True
    omni_backend: str = "gateway"
    gateway_url: str = "ws://127.0.0.1:8006"
    gateway_session_seconds: int = 240
    gateway_retry_limit: int = 3
    gateway_consent_devices: bool = False
    gateway_input_device: int | None = None
    gateway_output_device: int | None = None
    gateway_camera: int = 0
    omni_ref_audio: str = "voices/silverwalf_voice.wav"
    omni_fps: int = 5
    omni_video_enabled: bool = True
    omni_mic_gain: float = 1.0
    gateway_transcription_enabled: bool = True
    whisper_model_dir: str = os.path.expanduser("~/.cache/jac/models/whisper-small")
    gateway_brain_url: str = "http://127.0.0.1:12345"

    @classmethod
    def load(cls):
        """读取 Gateway 环境变量；保留有效的 OMNI 采集设置命名。"""
        return cls(
            omni_backend=os.environ.get("OMNI_BACKEND", "gateway"),
            gateway_url=os.environ.get("JAC_GATEWAY_URL", "ws://127.0.0.1:8006"),
            gateway_session_seconds=int(os.environ.get("JAC_GATEWAY_SESSION_SECONDS", "240")),
            gateway_retry_limit=int(os.environ.get("JAC_GATEWAY_RETRY_LIMIT", "3")),
            gateway_consent_devices=os.environ.get("JAC_CONSENT_DEVICES", "0").lower() in {"1", "true", "yes", "on"},
            gateway_input_device=int(os.environ["JAC_INPUT_DEVICE"]) if os.environ.get("JAC_INPUT_DEVICE") else None,
            gateway_output_device=int(os.environ["JAC_OUTPUT_DEVICE"]) if os.environ.get("JAC_OUTPUT_DEVICE") else None,
            gateway_camera=int(os.environ.get("JAC_CAMERA", "0")),
            omni_ref_audio=os.environ.get("OMNI_REF_AUDIO", "voices/silverwalf_voice.wav"),
            omni_fps=int(os.environ.get("OMNI_FPS", "5")),
            omni_video_enabled=os.environ.get("OMNI_VIDEO_ENABLED", "1").lower() not in {"0", "false", "no", "off"},
            omni_mic_gain=float(os.environ.get("OMNI_MIC_GAIN", "1.0")),
            gateway_transcription_enabled=os.environ.get("JAC_TRANSCRIPTION_ENABLED", "1").lower() not in {"0", "false", "no", "off"},
            whisper_model_dir=os.environ.get("JAC_WHISPER_MODEL_DIR", os.path.expanduser("~/.cache/jac/models/whisper-small")),
            gateway_brain_url=os.environ.get("JAC_BRAIN_URL", "http://127.0.0.1:12345"),
        )
