"""扬声器工厂：统一选择 TTS 引擎，消除 main.py / runtime.py 重复逻辑。

选择优先级（第一个 available 的胜出）：
  1. Voicebox        —— 开源克隆引擎（REST API，macOS 友好），替代 Qwen3-TTS
  2. Qwen3-TTS       —— 仅 NVIDIA GPU 平台（或 QWEN_TTS_FORCE=1 强制）；macOS 默认禁用
  3. 系统 TTS        —— Speaker（pyttsx3 / macOS say / espeak），永远可用作兜底

下游统一调用 speaker.speak(text, emotion_hint=None)，无需关心具体引擎。
"""
import threading

from src.utils.config import Config


def build_speaker(config: "Config"):
    """根据配置构建并返回一个 speaker 实例。

    返回 VoiceboxSpeaker / QwenTTSSpeaker / Speaker 之一。
    任何引擎不可用都不会阻断：最终一定回退到系统 TTS。
    """
    # 1) Voicebox（开源克隆 TTS，仿 LM Studio 的独立本地服务）
    if getattr(config, "use_voicebox_tts", False):
        try:
            from src.audio.voicebox_tts import VoiceboxSpeaker
            sp = VoiceboxSpeaker(
                url=config.voicebox_url,
                engine=config.voicebox_engine,
                profile_name=config.voicebox_profile_name,
                ref_wav=config.voicebox_ref_wav,
                ref_text=config.voicebox_ref_text,
                language=config.voicebox_language,
                fallback_voice=config.voicebox_fallback_voice,
            )
            if sp.available:
                return sp
            print("[TTS] Voicebox 不可用（服务未启动？），尝试其他方案。")
        except Exception as e:
            print(f"[TTS] Voicebox 加载失败: {e}")

    # 2) Qwen3-TTS（仅 NVIDIA 平台；macOS 默认禁用，QWEN_TTS_FORCE=1 可强开）
    if getattr(config, "use_qwen_tts", True):
        try:
            from src.audio import qwen_tts as _qt
            if _qt.ensure_qwen_tts():
                import importlib
                importlib.reload(_qt)
                from src.audio.qwen_tts import QwenTTSSpeaker, QWEN_TTS_AVAILABLE
                if QwenTTSSpeaker is not None and QWEN_TTS_AVAILABLE:
                    sp = QwenTTSSpeaker()
                    if getattr(sp, "available", False):
                        return sp
        except Exception as e:
            print(f"[TTS] Qwen3-TTS 不可用（{e}），将回退系统 TTS。")

    # 3) 系统 TTS 兜底（永远可用）
    from src.audio.tts import Speaker
    return Speaker()


def preload_if_needed(speaker):
    """若 speaker 支持模型预加载（仅 QwenTTSSpeaker 有 _ensure_model），后台预热。

    VoiceboxSpeaker / Speaker 没有 _ensure_model，getattr 返回 None 自动跳过。
    """
    if getattr(speaker, "_ensure_model", None) is not None and getattr(speaker, "available", False):
        threading.Thread(target=speaker._ensure_model, daemon=True, name="tts-preload").start()
