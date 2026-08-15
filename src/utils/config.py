"""J.A.C. 运行时配置（集中管理，替代散落在 main.py 顶部的常量）。

GUI 的选项面板直接绑定一个 Config 实例；按「启动」时把它传给 JACRuntime。
所有字段都支持从环境变量读取（保持与旧 main.py 常量兼容）。
"""
import os
from dataclasses import dataclass, field
from typing import List


@dataclass
class Config:
    # --- 前导判断引擎（主动感知）---
    judgment_engine_enabled: bool = True
    judgment_interval: float = 4.0          # 每隔几秒判断一次（秒）
    judgment_timeout: float = 15.0          # 单次判断请求最长等待（秒）
    judgment_cooldown: float = 20.0         # 介入后冷却时长（秒）：避免同一场景反复触发
    judgment_model_name: str = "minicpm-v-4_5"

    # --- TTS 选择（开关，覆盖原"默认优先 Qwen、不可用时回退"的隐式逻辑）---
    use_qwen_tts: bool = True

    # --- Voicebox TTS（开源克隆引擎，REST API，macOS 友好，替代 Qwen3-TTS）---
    # 默认开启：服务未启动会自动回退系统 TTS，不会阻断启动。
    use_voicebox_tts: bool = True
    voicebox_url: str = "http://127.0.0.1:17493"   # Voicebox 桌面 App 的 REST API 地址
    voicebox_engine: str = ""                      # 留空=不指定，由 JAC 声纹绑定的模型决定
    voicebox_profile_name: str = "JAC"             # 克隆声纹名（自动建/复用）
    voicebox_ref_wav: str = "voices/silverwalf_voice.wav"  # 声音克隆参考音
    voicebox_ref_text: str = ("哎，场地限制，我还有更棒的点子没展示呢..."
                               "看谁能让我火力全开，指不定哪天就能有比999更劲爆的大数字呢。")
    voicebox_language: str = "zh"
    voicebox_fallback_voice: str = "Tingting"       # 兜底用的系统中文嗓色

    # --- MiniCPM-o-4_5 全双工（本地 llama.cpp-omni，接管 TTS + 判断引擎）---
    # OMNI 模式下跳过传统 Voicebox TTS / Whisper STT / MiniCPM-v 判断引擎，
    # 由 omni 直接做「看 + 听 + 说」。默认关闭，验证前传统模式完全可用。
    omni_enabled: bool = False
    omni_server_url: str = "ws://127.0.0.1:9060/backend"   # WS 地址（master 分支 /backend）
    omni_server_bin: str = ""                              # 二进制路径（留空自动探测）
    omni_model_dir: str = ""                              # 含 MiniCPM-o-4_5-<quant>.gguf 及其子模型目录
    omni_host: str = "127.0.0.1"
    omni_port: int = 9060
    omni_quant: str = "Q8_0"                              # Q4_K_M 在 Metal 上劣化，锁定 Q8_0
    omni_ref_audio: str = "voices/silverwalf_voice.wav"   # 声纹克隆参考音（JAC 原音色）
    omni_fps: int = 5                                     # 视频上行帧率
    omni_mic_gain: float = 1.0                           # 麦克风采集增益（OMNI 全双工，内建麦离嘴远时调高）
    omni_duplex: bool = True                             # 全双工（边听边说）；False=半双工
    omni_auto_launch: bool = True                        # 未运行则自动起服务

    # --- 大脑推理后端 ---
    brain_backend: str = "lm_studio"         # lm_studio | llama_cpp | ollama | auto

    # --- 唤醒 ---
    wake_words: List[str] = field(default_factory=lambda: [
        "jac", "j.a.c", "杰克", "接客", "你好",
        "hello jac", "hi jac", "你好 jac", "hey jac",
    ])
    awake_timeout: int = 20                  # 唤醒后维持活跃秒数

    # --- 记忆子系统 ---
    memory_enabled: bool = True
    memory_capture_person_id: bool = False

    # --- Function Calling（装手 / agent 工具层）---
    tools_enabled: bool = True

    # --- STT 语音识别语言（强制锁定，根治自动检测漂移导致的繁体/乱码）---
    stt_language: str = "zh"              # 默认简体中文；可用 STT_LANGUAGE 环境变量覆盖

    # --- 摄像头（采集分辨率固定，绝不随 GUI 缩放变化）---
    camera_width: int = 1280
    camera_height: int = 720

    @classmethod
    def load(cls) -> "Config":
        """加载"""
        def truthy(key: str, default: bool = True) -> bool:
            """真值判断"""
            v = os.environ.get(key)
            if v is None:
                return default
            return v.strip().lower() not in ("0", "false", "no", "off")

        return cls(
            judgment_engine_enabled=truthy("JUDGMENT_ENGINE_ENABLED", True),
            judgment_interval=float(os.environ.get("JUDGMENT_INTERVAL", "4.0")),
            judgment_timeout=float(os.environ.get("JUDGMENT_TIMEOUT", "15.0")),
            judgment_cooldown=float(os.environ.get("JUDGMENT_COOLDOWN", "20.0")),
            judgment_model_name=os.environ.get("JUDGMENT_MODEL_NAME", "minicpm-v-4_5"),
            use_qwen_tts=truthy("USE_QWEN_TTS", True),
            use_voicebox_tts=truthy("USE_VOICEBOX_TTS", True),
            voicebox_url=os.environ.get("VOICEBOX_URL", "http://127.0.0.1:17493"),
            voicebox_engine=os.environ.get("VOICEBOX_ENGINE", ""),
            voicebox_profile_name=os.environ.get("VOICEBOX_PROFILE_NAME", "JAC"),
            voicebox_ref_wav=os.environ.get("VOICEBOX_REF_WAV", "voices/silverwalf_voice.wav"),
            voicebox_ref_text=os.environ.get("VOICEBOX_REF_TEXT",
                                             "哎，场地限制，我还有更棒的点子没展示呢..."
                                             "看谁能让我火力全开，指不定哪天就能有比999更劲爆的大数字呢。"),
            voicebox_language=os.environ.get("VOICEBOX_LANGUAGE", "zh"),
            voicebox_fallback_voice=os.environ.get("VOICEBOX_FALLBACK_VOICE", "Tingting"),
            brain_backend=os.environ.get("JAC_BRAIN_BACKEND", "lm_studio"),
            awake_timeout=int(os.environ.get("AWAKE_TIMEOUT", "20")),
            memory_enabled=truthy("MEMORY_ENABLED", True),
            memory_capture_person_id=truthy("MEMORY_CAPTURE_PERSON_ID", False),
            tools_enabled=truthy("TOOLS_ENABLED", True),
            stt_language=os.environ.get("STT_LANGUAGE", "zh"),
            omni_enabled=truthy("OMNI_ENABLED", False),
            omni_server_url=os.environ.get("OMNI_SERVER_URL", "ws://127.0.0.1:9060/backend"),
            omni_server_bin=os.environ.get("LLAMA_OMNI_SERVER_BIN", ""),
            omni_model_dir=os.environ.get("OMNI_MODEL_DIR", ""),
            omni_host=os.environ.get("OMNI_HOST", "127.0.0.1"),
            omni_port=int(os.environ.get("OMNI_PORT", "9060")),
            omni_quant=os.environ.get("OMNI_QUANT", "Q8_0"),
            omni_ref_audio=os.environ.get("OMNI_REF_AUDIO", "voices/silverwalf_voice.wav"),
            omni_fps=int(os.environ.get("OMNI_FPS", "5")),
            omni_mic_gain=float(os.environ.get("OMNI_MIC_GAIN", "1.0")),
            omni_duplex=truthy("OMNI_DUPLEX", True),
            omni_auto_launch=truthy("OMNI_AUTO_LAUNCH", True),
        )
