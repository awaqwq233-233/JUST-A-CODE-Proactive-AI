"""M7a 回灌通道：用本地 Voicebox（JAC 克隆声纹）播报升级结果文本。

架构变更（2026-08-15）：原实现开 omni 第二个 turn_based 会话播报，但 llama.cpp-omni
server 单会话——主 full_duplex 占槽后第二个会话被拒（server 日志
`session.init rejected — active session exists` → ConnectionClosedOK 无声音）。
改为直接用本地 Voicebox（独立进程，不受 omni 会话限制）合成 JAC 克隆声纹文本并播放，
同时音质远好于 omni 自带 TTS。
"""
import platform
import subprocess

IS_MACOS = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"


def _system_say(text: str):
    """无 Voicebox 时的系统 TTS 兜底（确保答案一定出声，绝不静默丢弃）。

    升级结果即使在没有克隆引擎的极端情况下也要让 boss 听到，故降级为系统 TTS。
    """
    text = (text or "").strip()
    if not text:
        return
    if IS_MACOS:
        try:
            subprocess.run(["say", "-v", "Tingting", text], capture_output=True, text=True)
            return
        except Exception:  # noqa: BLE001
            pass
    elif IS_LINUX:
        try:
            subprocess.run(["espeak", text], capture_output=True, text=True)
            return
        except Exception:  # noqa: BLE001
            pass
    print(f"[J.A.C.(回退)] {text}")


def speak_text_via_voicebox(speaker, text: str):
    """用 Voicebox 合成并播报文本（薄封装；speaker.speak 内部同步合成 + 播放并自带降级）。

    Args:
        speaker: 具备 speak(text) 方法的合成器（VoiceboxSpeaker），为 None 时降级系统 TTS。
        text: 要播报的中文文本。
    """
    if speaker is None:
        # 无克隆引擎（如 --no-voicebox）：降级系统 TTS，确保答案出声
        _system_say(text)
        return
    try:
        speaker.speak(text)
    except Exception:  # noqa: BLE001
        # VoiceboxSpeaker.speak 内部已自带系统 TTS 兜底，此处再兜一层防极端异常
        _system_say(text)
