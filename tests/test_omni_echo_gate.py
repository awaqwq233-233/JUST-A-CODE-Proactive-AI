#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回声门控 + 幻觉升级拦截离线单测（2026-09-06 OMNI 自言自语根因修复）。

真机现象：J.A.C. 用 Voicebox 外放说话时，声音被本机麦克风重新采集并推给 omni，
omni 把自己的语音当成用户发言 → 自问自答 → 幻觉出 `<<CALL_QWEN>>任务`
（日志铁证：「[TTS] 正在播放」出现的同一时刻「🎙 检测到人声 RMS=0.022」，
随后 LM Studio 收到 `[升级任务] 给您推荐一部电`）。

覆盖：
  1. playback 全局播放状态：mark_external_playback / is_playback_active / 拖尾计时。
  2. 回声期 _has_recent_speech() 返回 False（即使刚记录过真实人声时间戳）。
  3. 回声期令牌被拦截：不触发升级、不静音主会话。
  4. 幻觉拦截后即使任务描述出现句号也不得 fire（防「偷偷升级」）。
  5. 令牌后的任务描述不再广播到控制台 / GUI（不再显示「给您推荐一部电」）。
  6. 关闭门控（OMNI_ECHO_GATE=0）时行为回退：有人声即可正常触发升级。
"""
import os
import sys
import time
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 离线单测环境可能未安装重量级依赖，缺失时注入 MagicMock 占位（逻辑测试不调用它们）
for _name in ("cv2", "numpy", "pyaudio", "soxr", "soundfile", "websockets", "requests"):
    try:
        __import__(_name)
    except Exception:
        sys.modules[_name] = MagicMock()

from src.audio import playback
from src.omni.client import OmniClient, OmniCallbacks, resolve_echo_gate


class _CaptureCb(OmniCallbacks):
    """捕获升级任务与广播文本。"""

    def __init__(self):
        super().__init__()
        self.tasks = []
        self.text_seen = []

    def on_text_delta(self, text):
        self.text_seen.append(text)

    def on_call_qwen(self, task):
        self.tasks.append(task)


def _make_client(echo_gate=True):
    """构造不连服务的客户端（关闭采集/播放），仅测令牌与门控逻辑。"""
    os.environ["OMNI_ECHO_GATE"] = "1" if echo_gate else "0"
    cb = _CaptureCb()
    client = OmniClient(
        url="ws://127.0.0.1:9060/backend",
        ref_audio_path="voices/silverwalf_voice.wav",
        system_prompt="test",
        callbacks=cb,
        enable_mic=False, enable_camera=False, enable_playback=False,
    )
    return client, cb


def _reset_all():
    """清空 playback 全局状态，避免用例之间互相污染。"""
    playback.reset_playback_state()


def test_playback_state_marks_external_window():
    """外部播放窗口：登记期间 is_playback_active 为 True，结束后为 False。"""
    _reset_all()
    assert playback.is_playback_active() is False
    playback.mark_external_playback(0.3)      # 登记 0.3 秒的播放窗口
    assert playback.is_playback_active() is True
    time.sleep(0.45)
    assert playback.is_playback_active() is False
    assert playback.seconds_since_playback_end() < 5.0
    _reset_all()


def test_echo_window_blocks_recent_speech():
    """回声期：即使刚记录过人声时间戳，护栏也必须判为「无人声」。"""
    _reset_all()
    client, _ = _make_client(echo_gate=True)
    client._last_speech_ts = time.monotonic()      # 模拟"刚刚检测到人声"
    assert client._has_recent_speech() is True     # 正常情况：可信

    playback.mark_external_playback(1.0)           # 进入播放窗口（回声期）
    assert client._is_echoing() is True
    assert client._has_recent_speech() is False    # 回声期一律判为不可信
    _reset_all()


def test_echo_tail_blocks_after_playback():
    """拖尾保护：播放刚结束的 _echo_tail 秒内仍视为回声。"""
    _reset_all()
    client, _ = _make_client(echo_gate=True)
    client._last_speech_ts = time.monotonic()
    client._echo_tail = 0.5
    playback.mark_external_playback(0.05)
    time.sleep(0.1)                                # 播放窗口已过，但仍在拖尾期内
    assert playback.is_playback_active() is False
    assert client._is_echoing() is True
    assert client._has_recent_speech() is False
    _reset_all()


def test_apply_echo_gate_replaces_audio_with_silence():
    """门控帧：音频被替换为等长静音（保持实时节奏），且不计为真实人声。"""
    _reset_all()
    client, _ = _make_client(echo_gate=True)
    raw = b"\x01" * 64                              # 假装是有声音的一帧
    out, is_speech = client._apply_echo_gate(raw, 0.05)
    assert out == b"\x00" * 64, "门控帧应被替换为静音"
    assert len(out) == len(raw), "必须与原始等长，否则实时节奏被破坏（只听不说）"
    assert is_speech is False, "回声帧不得计为真实人声"
    _reset_all()


def test_play_wav_toggles_global_state():
    """真实 play_wav 调用也会置位/复位全局播放状态（即使播放失败也不残留）。"""
    _reset_all()
    playback.play_wav("/__not_exist__.wav")         # 播放会失败，但状态必须正确复位
    assert playback.is_playback_active() is False, "播放结束后不得残留「正在出声」"
    assert playback.seconds_since_playback_end() < 5.0
    _reset_all()


def test_token_during_echo_is_intercepted():
    """回声期令牌：不触发升级回调，也不静音主会话。"""
    _reset_all()
    client, cb = _make_client(echo_gate=True)
    client._last_speech_ts = time.monotonic()      # 回声骗过旧护栏的前提
    playback.mark_external_playback(2.0)

    client._on_text("好的，")
    client._on_text("<<CALL_QWEN>>给您推荐一部电影。")

    assert cb.tasks == [], f"回声期幻觉不应触发升级，实际收到: {cb.tasks}"
    assert client._hallucinated is True
    assert client._call_qwen_fired is False
    with client._audio_lock:
        assert client._suppress_audio is False     # 不静音，用户随后发言仍可正常回复
    _reset_all()


def test_hallucinated_task_never_fires_later():
    """幻觉拦截后，后续任务描述即使出现句号/超长也不得 fire（防偷偷升级）。"""
    _reset_all()
    client, cb = _make_client(echo_gate=True)
    client._last_speech_ts = 0.0                   # 全程无人声 → 静音期幻觉
    client._on_text("<<CALL_QWEN>>查一下天气")
    assert client._hallucinated is True
    # 后续 delta 持续到达，且出现句末标点（旧逻辑会在此触发 fire）
    client._on_text("怎么样。")
    client._on_text("再查一下时间。")
    client._finalize_pending()                     # 兜底定时器也应被拦
    assert cb.tasks == [], f"幻觉任务不得升级，实际: {cb.tasks}"
    _reset_all()


def test_task_text_not_broadcast():
    """令牌后的任务描述不再广播：用户不再看到「给您推荐一部电」这类自言自语。"""
    _reset_all()
    client, cb = _make_client(echo_gate=True)
    client._last_speech_ts = time.monotonic()
    client._on_text("好的，我帮你查。")
    client._on_text("<<CALL_QWEN>>给您推荐一部电")
    client._on_text("影，要科幻的。")
    shown = "".join(cb.text_seen)
    assert "CALL_QWEN" not in shown, f"令牌不应显示给用户: {shown!r}"
    assert "给您推荐" not in shown, f"任务描述不应显示给用户: {shown!r}"
    assert "好的，我帮你查。" in shown              # 令牌前的正常回复仍应显示
    assert cb.tasks and "给您推荐一部电影" in cb.tasks[0]   # 真实人声后仍正常升级
    _reset_all()


def test_gate_disabled_keeps_legacy_behavior():
    """关闭门控（OMNI_ECHO_GATE=0，戴耳机场景）：有人声即正常触发升级。"""
    _reset_all()
    client, cb = _make_client(echo_gate=False)
    assert client._echo_gate is False
    client._last_speech_ts = time.monotonic()
    playback.mark_external_playback(2.0)           # 即便"正在播放"也不再门控
    client._on_text("<<CALL_QWEN>>打开浏览器。")
    assert cb.tasks == ["打开浏览器"], f"关闭门控后应正常升级，实际: {cb.tasks}"
    _reset_all()


def test_resolve_echo_gate_pref_parsing():
    """resolve_echo_gate：手动指定与 auto 的解析行为。"""
    # 手动指定优先
    assert resolve_echo_gate(True)[0] is True
    assert resolve_echo_gate(False)[0] is False
    assert resolve_echo_gate("1")[0] is True
    assert resolve_echo_gate("on")[0] is True
    assert resolve_echo_gate("0")[0] is False
    assert resolve_echo_gate("off")[0] is False
    # auto / None / 未知字符串 → 走自动检测（离线 stub 下检测失败，保守开启门控）
    gate_auto, reason = resolve_echo_gate("auto")
    assert reason.startswith("自动检测")
    gate_none, _ = resolve_echo_gate(None)
    assert gate_auto == gate_none          # auto 与 None 行为一致
    # 未知字符串回退到 auto 而非抛错
    assert resolve_echo_gate("garbage")[0] == gate_auto
