"""真人反馈回归：应用意图、受限转写提示、短追问与原生播放抖动。"""

import json
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from src.omni.media import LiveDevices
from src.omni.response_gate import ResponseGate
from src.omni.task_pipeline import conversation_retry_key, route_instruction, transcription_quality
from src.omni.transcription_worker import decode_segments
from src.tools.local_actions import LocalActionExecutor


@pytest.mark.parametrize("text", [
    "打开lightroom", "打开LightroomClassic", "启动 Lightroom Classic",
    "打开电脑上的lightroom软件", "打开电脑上的 Lightroom 软件", "打开本机的Lightroom应用",
    "打開電腦上的Lightroom軟件", "打开应用Adobe Lightroom Classic",
])
def test_lightroom_routes_only_to_named_local_application(text):
    """用户两条原话及空格/繁体形式锁定到已获授权的 Classic 应用。"""
    fields = route_instruction(text)
    assert fields[:2] == ("local", "open_app")
    assert json.loads(fields[2]) == {"app_name": "Adobe Lightroom Classic"}


@pytest.mark.parametrize("text", [
    "打开电脑上的陌生软件", "打开本机的Github", "打开软件UnknownApp",
    "不要打开电脑上的lightroom软件", "他说打开Lightroom", "打开Lightroom然后删除照片",
    "打开电脑上的Lightroom官网", "打开电脑上的https://example.com", "打开Lightroom和计算器",
])
def test_explicit_app_rejection_cannot_fall_through_to_web(text):
    """未知本机应用、否定和复合动作不能转成联网查询或取得执行权限。"""
    assert route_instruction(text) is None


def test_explicit_web_and_software_site_still_use_website_resolver():
    """明确官网保持网站意图，含软件二字的既有站名继续支持。"""
    assert route_instruction("打开Lightroom官网") == ("website", "Lightroom")
    assert route_instruction("打开小众软件") == ("website", "小众软件")


def test_lightroom_executor_uses_verified_bundle_and_process(monkeypatch):
    """系统入口按固定 bundle ID 启动，Classic 的实际可执行文件用于回执核验。"""
    calls = []
    def command(arguments, *args, **kwargs):
        """返回系统启动回执和精确进程路径，不真正打开 Lightroom。"""
        calls.append(arguments)
        return 0, "/Applications/Adobe Lightroom Classic/Adobe Lightroom Classic.app/Contents/MacOS/Adobe Lightroom Classic\n", ""
    monkeypatch.setattr("src.tools.local_actions.platform.system", lambda: "Darwin")
    monkeypatch.setattr("src.tools.local_actions.command_result", command)
    result = LocalActionExecutor().execute("open_app", {"app_name": "Adobe Lightroom Classic"})
    assert result["status"] == "completed"
    assert calls[0] == ["/usr/bin/open", "-b", "com.adobe.LightroomClassicCC7"]


def test_vocabulary_only_guides_acoustic_decode_and_does_not_rewrite_words():
    """有限词汇不包含执行指令，错误原词仍被保留且不能绕过路由和质量门槛。"""
    def transcribe(samples, **options):
        """检查真实传给解码器的配置，模拟仍然听错的结果。"""
        assert "Python" in options["hotwords"] and "Lightroom" in options["hotwords"]
        assert not any(word in options["hotwords"] for word in ("打开", "查一下", "上网查询"))
        assert options["condition_on_previous_text"] is False and options["temperature"] == 0
        return [SimpleNamespace(text="拆一下电池电量", avg_logprob=-.3,
                                no_speech_prob=.01, compression_ratio=1)], None
    result = decode_segments(SimpleNamespace(transcribe=transcribe), np.zeros(16000))
    text, error = transcription_quality(result)
    assert text == "拆一下电池电量" and error is None and route_instruction(text) is None
    result[0]["avg_logprob"] = -1
    assert transcription_quality(result) == (None, "low_confidence")


@pytest.mark.parametrize("text", ["再详细一点", "再詳細一點", "具体一点", "继续说"])
def test_neutral_followup_is_eligible_for_acoustic_recheck_only(text):
    """展开追问只允许重新识别音频，不产生大脑任务或补写缺失指令。"""
    assert conversation_retry_key(text) is not None and route_instruction(text) is None


@pytest.mark.parametrize("text", ["继续打开计算器", "再详细一点电池电量", "上网查询Python", "把它删除"])
def test_followup_recheck_does_not_authorize_tools(text):
    """带动作或任务主题的句子不能进入普通对话复核例外。"""
    assert conversation_retry_key(text) is None


def playback_trace(delay):
    """以两块间隔 900ms 的 PCM 模拟突发到达，回调时钟完全可控。"""
    now, guard = [0.0], threading.Event()
    device = LiveDevices(None, None, 0, video_enabled=False,
                         playback_prebuffer_seconds=delay, clock=lambda: now[0])
    output = []
    for tick in range(45):
        now[0] = tick * .05
        if tick in (0, 18):
            device.enqueue_task_output(np.full(9600, .1 if tick == 0 else .2, dtype="float32"), guard)
        block = np.empty((1200, 1), dtype="float32")
        device.speaker_callback(block, 1200, None, None)
        output.append(block[:, 0].copy())
    return device, np.concatenate(output)


def test_prebuffer_removes_arrival_jitter_without_changing_pcm():
    """同样波形旧零缓冲发生句中空档，默认缓冲连续播放并保留每个样本。"""
    old, baseline = playback_trace(0)
    device, result = playback_trace(.8)
    positions = np.flatnonzero(result)
    assert np.all(np.diff(positions) == 1)
    np.testing.assert_array_equal(result[positions], np.r_[np.full(9600, .1, dtype="float32"), np.full(9600, .2, dtype="float32")])
    assert positions[0] == 19200 and device.played_samples == old.played_samples == 19200
    assert old.playback_resume_gap_events == 1 and device.playback_resume_gap_events == 0
    assert np.any(np.diff(np.flatnonzero(baseline)) > 1)


def test_short_tail_has_bounded_wait_and_cancelled_prebuffer_never_plays():
    """短于缓冲的尾音按时交付，预缓冲期间撤销的 PCM 永远不进入设备。"""
    now, guard = [0.0], threading.Event()
    device = LiveDevices(None, None, 0, clock=lambda: now[0])
    device.enqueue_task_output(np.ones(1200, dtype="float32"), guard)
    block = np.empty((1200, 1), dtype="float32")
    device.speaker_callback(block, 1200, None, None)
    assert not block.any()
    guard.set()
    now[0] = .9
    device.speaker_callback(block, 1200, None, None)
    assert not block.any() and device.played_samples == 0
    device.enqueue_output(np.full(1200, .25, dtype="float32"))
    now[0] = 1.71
    device.speaker_callback(block, 1200, None, None)
    assert np.all(block == .25) and device.played_samples == 1200


@pytest.mark.parametrize("policy", ["allow", "block"])
def test_missing_native_reply_notice_does_not_release_system_guesses(policy):
    """普通识别后无回复会提示一次；工具禁猜不被提示逻辑放行，迟到对话仍可交付。"""
    now, events = [0.0], []
    state = [1, "pending", threading.Event()]
    gate = ResponseGate(SimpleNamespace(response_state=lambda: state),
                        SimpleNamespace(on_task_event=lambda *args: events.append(args)), clock=lambda: now[0])
    gate.poll()
    state[1] = policy
    gate.poll()
    now[0] = 13
    gate.poll()
    gate.poll()
    assert len(events) == (1 if policy == "allow" else 0)
    assert bool(gate.offer("text", "补充画面细节")) == (policy == "allow")
