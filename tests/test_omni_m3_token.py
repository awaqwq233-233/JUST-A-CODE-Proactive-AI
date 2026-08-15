"""omni 全双工令牌拦截与多轮升级的单测（对应 2026-08-15 修复）。

验证两个核心 bug 已修复：
1. 承载 <<CALL_QWEN>> 的文本增量不会被送进 Voicebox 朗读队列（"把问题本身读出来"）；
2. 升级标志位在每轮聆听后复位，第二次升级仍能触发（"第二次升级被吞"）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.omni.client import OmniClient


class _FakeSpeaker:
    """假 speaker，仅用于让 VoiceboxBridge 启用（speak 不做事）。"""

    def speak(self, text):
        pass


class _RecordingBridge:
    """替换 VoiceboxBridge，记录所有被喂入 feed 的文本（不真正合成播放）。"""

    def __init__(self, speaker, *args, **kwargs):
        self._spk = speaker
        self.fed = []

    def feed(self, delta):
        self.fed.append(delta)

    def flush_remaining(self):
        pass

    def flush_and_stop(self):
        pass


def _make_client():
    """构造一个不连网络的 OmniClient，并把内部桥接器换成记录器。"""
    fake = _FakeSpeaker()
    client = OmniClient(
        url="ws://127.0.0.1:9999/backend",
        ref_audio_path="voices/silverwalf_voice.wav",
        system_prompt="x",
        voicebox_speaker=fake,
        enable_mic=False,
        enable_camera=False,
        enable_playback=True,
    )
    rec = _RecordingBridge(fake)
    client._voicebox_bridge = rec
    client._token_seen = False
    client._call_qwen_fired = False
    client._text_buf = ""
    client._pending_task = None
    client._pending_timer = None
    client._escalation_done = False
    return client, rec


def test_token_text_not_spoken():
    """含令牌的 delta 不应把令牌文本送进朗读队列，且升级应触发。"""
    client, rec = _make_client()
    # 带换行 → 立即触发升级
    client._on_text("好的，我来帮你查一下<<CALL_QWEN>>查一下电脑的电池电量百分比\n")
    joined = "".join(rec.fed)
    assert "<<CALL_QWEN>>" not in joined, f"令牌文本被送进朗读队列: {joined!r}"
    assert client._call_qwen_fired is True, "升级未被触发"
    print("PASS: 令牌文本未朗读，升级已触发")


def test_multi_turn_escalation():
    """升级标志位在每轮聆听后复位，第二次升级仍可触发。"""
    client, rec = _make_client()
    client._on_text("帮我<<CALL_QWEN>>查时间\n")
    assert client._call_qwen_fired, "第一轮升级未触发"

    # 模拟新一轮 listen：复位标志（与 _receiver_loop 的 listen 分支一致）
    client._reset_escalation_state()
    assert client._call_qwen_fired is False, "标志位未复位（多轮失效 bug）"

    rec.fed.clear()
    client._on_text("再帮我<<CALL_QWEN>>查天气\n")
    assert client._call_qwen_fired, "第二次升级未被触发（多轮失效 bug）"
    joined = "".join(rec.fed)
    assert "<<CALL_QWEN>>" not in joined, f"第二轮令牌文本被朗读: {joined!r}"
    print("PASS: 多轮升级可重复触发，令牌文本未朗读")


if __name__ == "__main__":
    test_token_text_not_spoken()
    test_multi_turn_escalation()
    print("\n全部通过 ✅")
