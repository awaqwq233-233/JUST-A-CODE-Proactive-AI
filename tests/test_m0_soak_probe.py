"""长测时长、验收门槛和失败报告的离线测试。"""

import asyncio
from types import SimpleNamespace

from test_m0_backend_launcher import load_module

soak = load_module("jac_m0_soak_probe", "verify_soak_duplex.py")


def good_session(seconds=225):
    """构造已清理且原生收播一致的会话统计。"""
    return dict(chunks_sent=seconds, native_audio_seconds=2, played_audio_seconds=2,
                protocol_passed=True, cleanup_verified=True, realtime_threshold_passed=True,
                camera_frames=100, input_status_events=0, output_status_events=0)


def test_soak_respects_gateway_limit_and_exact_duration():
    """30 分钟累计发送正好 1800 块，每段预留会话关闭时间。"""
    assert soak.session_lengths(1800) == [225] * 8
    assert sum(soak.session_lengths(1799)) == 1799
    assert max(soak.session_lengths(1799)) <= 225


def test_soak_rejects_partial_capture_bad_playback_and_health():
    """少测、播放异常或后端失联均不能包装为长测通过。"""
    sessions = [good_session()] * 8
    assert soak.acceptance(sessions, 1800, 0)
    assert not soak.acceptance(sessions[:-1], 1800, 0)
    assert not soak.acceptance(sessions, 1800, 1)
    bad = good_session()
    bad["output_status_events"] = 1
    assert not soak.acceptance([bad], 225, 0)
    bad = good_session()
    bad["played_audio_seconds"] = 1
    assert not soak.acceptance([bad], 225, 0)


def test_soak_saves_failure_without_media(monkeypatch, tmp_path):
    """设备运行异常时仍保存失败证据，不泄漏异常中的用户文本。"""
    async def failed(args):
        """模拟设备异常，跳过真实音视频访问。"""
        raise RuntimeError("private user text")

    monkeypatch.setattr(soak.live, "run_live", failed)
    args = SimpleNamespace(seconds=10, report=tmp_path / "report.json", monitor_pid=[])
    report = asyncio.run(soak.run_soak(args))
    assert report["status"] == "failed" and report["chunks_sent"] == 0
    assert not report["soak_30min_verified"]
    assert "private user text" not in args.report.read_text()
