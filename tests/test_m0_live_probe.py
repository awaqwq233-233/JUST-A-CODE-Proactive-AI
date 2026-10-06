"""短时设备探针的纯离线回调、缓冲与授权测试，不打开真实设备。"""

from __future__ import annotations

import queue
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from test_m0_backend_launcher import load_module

live = load_module("jac_m0_live_probe", "verify_live_duplex.py")


def test_microphone_callback_only_copies_and_reports_overflow():
    """输入回调保留样本，不执行重采样或网络；溢出不偷偷丢旧音频。"""
    devices = live.LiveDevices(None, None, 0)
    devices.raw_audio = queue.Queue(maxsize=1)
    source = np.full((10, 1), 0.2, dtype="float32")
    devices.microphone_callback(source, 10, None, False)
    source.fill(0)
    devices.microphone_callback(source, 10, None, False)
    assert devices.error == "麦克风原始音频队列溢出"
    assert np.all(devices.raw_audio.get_nowait() == np.float32(0.2))


def test_native_output_preserves_order_and_fills_silence():
    """不同大小的原生音频增量按原始顺序播放，空闲部分填静音。"""
    devices = live.LiveDevices(None, None, 0)
    devices.enqueue_output(np.array([0.1, 0.2], dtype="float32"))
    devices.enqueue_output(np.array([0.3, 0.4, 0.5], dtype="float32"))
    first = np.zeros((4, 1), dtype="float32")
    second = np.zeros((4, 1), dtype="float32")
    devices.speaker_callback(first, 4, None, False)
    devices.speaker_callback(second, 4, None, False)
    np.testing.assert_allclose(first[:, 0], [0.1, 0.2, 0.3, 0.4])
    np.testing.assert_allclose(second[:, 0], [0.5, 0, 0, 0])
    assert devices.played_samples == 5


def test_playback_overflow_is_explicit_failure():
    """播放积压达到上限时必须失败，不能无界增长或丢音频。"""
    devices = live.LiveDevices(None, None, 0)
    devices.playback = queue.Queue(maxsize=1)
    devices.enqueue_output(np.zeros(2, dtype="float32"))
    with pytest.raises(RuntimeError, match="播放队列溢出"):
        devices.enqueue_output(np.zeros(2, dtype="float32"))


def test_latest_video_does_not_consume_frame():
    """预览/上行读取只观察最新帧，不破坏采集线程的有界 Queue。"""
    devices = live.LiveDevices(None, None, 0)
    assert devices.latest_video() is None
    devices.video.put_nowait(b"jpeg")
    assert devices.latest_video() == b"jpeg"
    assert devices.video.qsize() == 1


def test_end_input_stops_capture_but_keeps_output_for_tail_audio():
    """等待尾部原生语音时停止麦克风采集，防止未消费输入溢出。"""
    devices = live.LiveDevices(None, None, 0)
    calls = []
    devices.mic = SimpleNamespace(abort=lambda: calls.append("mic abort"), close=lambda: calls.append("mic close"))
    devices.speaker = SimpleNamespace(abort=lambda: calls.append("speaker abort"), close=lambda: calls.append("speaker close"))
    devices.finish_input()
    assert devices.stop_event.is_set() and devices.mic is None
    assert calls == ["mic abort", "mic close"]
    assert devices.stop() is True
    assert calls[-2:] == ["speaker abort", "speaker close"]


def test_cli_requires_device_consent_before_any_camera_open(monkeypatch):
    """缺少明确设备授权时，在访问摄像头前拒绝运行。"""
    monkeypatch.setattr(sys, "argv", ["verify_live_duplex.py"])

    def forbidden(*args, **kwargs):
        """任何未授权设备访问都使测试失败。"""
        pytest.fail("未授权时访问了摄像头")

    monkeypatch.setattr(live.cv2, "VideoCapture", forbidden)
    with pytest.raises(SystemExit) as failure:
        live.main()
    assert failure.value.code == 2
