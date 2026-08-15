"""M7a 回灌通道：用本地 Voicebox（JAC 克隆声纹）播报升级结果文本。

架构变更（2026-08-15）：原实现开 omni 第二个 turn_based 会话播报，但 llama.cpp-omni
server 单会话——主 full_duplex 占槽后第二个会话被拒（server 日志
`session.init rejected — active session exists` → ConnectionClosedOK 无声音）。
改为直接用本地 Voicebox（独立进程，不受 omni 会话限制）合成 JAC 克隆声纹文本并播放，
同时音质远好于 omni 自带 TTS。
"""


def speak_text_via_voicebox(speaker, text: str):
    """用 Voicebox 合成并播报文本（薄封装；speaker.speak 内部同步合成 + 播放并自带降级）。

    Args:
        speaker: 具备 speak(text) 方法的合成器（VoiceboxSpeaker），为 None 时静默跳过。
        text: 要播报的中文文本。
    """
    if speaker is None:
        return
    try:
        speaker.speak(text)
    except Exception:  # noqa: BLE001
        # VoiceboxSpeaker.speak 内部已自带系统 TTS 兜底，此处仅防极端异常破坏升级闭环
        pass
