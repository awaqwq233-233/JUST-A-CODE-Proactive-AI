"""方案 B M0 探针的音频校验、协议生命周期和失败处理测试。"""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from websockets.asyncio.server import serve

MODULE = Path(__file__).resolve().parents[1] / "verify_duplex.py"
SPEC = importlib.util.spec_from_file_location("jac_m0_probe", MODULE)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def test_resample_stereo_and_preserve_pcm(tmp_path):
    """验证立体声合并和 48k→16k 重采样后的精确样本数。"""
    wav = tmp_path / "stereo.wav"
    sf.write(wav, np.full((48000, 2), 0.25, dtype=np.float32), 48000, subtype="FLOAT")
    mono = probe.load_audio(wav)
    assert mono.shape == (16000,)
    assert mono.dtype == np.dtype("<f4")
    assert np.mean(mono[200:-200]) == pytest.approx(0.25, abs=1e-5)
    assert np.array_equal(probe.decode_pcm(probe.encode_pcm(mono)), mono)


def test_chunk_tail_padding_without_repeating_or_dropping():
    """验证句尾补零后原始语音顺序完整且不会被重复发送。"""
    samples = np.arange(17003, dtype=np.float32) / 17003
    chunks = probe.input_chunks(samples, 3)
    combined = np.concatenate(chunks)
    assert np.array_equal(combined[:17003], samples)
    assert not combined[17003:].any()
    assert all(len(chunk) == 16000 for chunk in chunks)


@pytest.mark.parametrize("value", ["bad!", base64.b64encode(b"123").decode(), probe.encode_pcm(np.array([1.0]))[:-1]])
def test_invalid_audio_is_rejected(value):
    """拒绝损坏的 Base64 或不完整的 float32 波形。"""
    with pytest.raises(ValueError):
        probe.decode_pcm(value)


def test_nonfinite_and_wrong_chunk_are_rejected():
    """避免把 NaN 音频或不满足固定节拍的块送给后端。"""
    with pytest.raises(ValueError):
        probe.encode_pcm(np.array([np.nan], dtype=np.float32))
    with pytest.raises(ValueError):
        probe.build_input(np.zeros(15999))


@pytest.mark.parametrize("url", ["wss://minicpmo45.modelbest.cn", "ws://example.com", "ws://user:pass@127.0.0.1"])
def test_probe_does_not_send_voice_to_remote_or_credential_url(url):
    """确保默认 M0 探针只能向本机发送参考音频。"""
    with pytest.raises(ValueError):
        probe.gateway_url(url, "video")


def test_gateway_url_uses_current_endpoint():
    """验证旧路径和 session_id 不会进入固定版本的公开 URL。"""
    assert probe.gateway_url("ws://127.0.0.1:8006/duplex", "video") == "ws://127.0.0.1:8006/v1/realtime?mode=video"


@pytest.mark.parametrize("with_voice", [False, True])
def test_pinned_cpp_init_preserves_system_and_reference_boundaries(with_voice):
    """回归固定 C++ 把 prompt 当后缀的行为，避免参考音后的指令变成用户消息。"""
    samples = np.linspace(-0.2, 0.2, 16000, dtype="<f4") if with_voice else None
    payload = probe.build_init("称呼用户为 bo s s。", samples)["payload"]
    # 复现 stream_prefill 的前缀选择规则，并检查拼接后完整的 ChatML 边界。
    suffix = payload["system_prompt"]
    effective_suffix = suffix if suffix.startswith("<|") else "<|im_start|>user\n" + suffix
    template = "<|im_start|>system\nStreaming Duplex Conversation!\n<|audio_start|>"
    template += "[reference embedding]" + effective_suffix + "<|im_start|>user\n"
    assert template.index("<|audio_end|>") < template.index("称呼用户") < template.index("<|im_end|>")
    assert template.count("<|im_start|>user") == 1
    assert suffix.endswith("<|im_end|>\n")
    if with_voice:
        voice = payload["voice"]
        assert voice["tts_ref_audio_base64"] == voice["ref_audio_base64"]
        assert np.array_equal(probe.decode_pcm(voice["ref_audio_base64"]), samples)
    else:
        assert "voice" not in payload


def test_protocol_with_delayed_queue_and_independent_audio():
    """验证先排队后 init、独立音频增量、输入节拍和优雅关闭。"""
    async def run():
        """启动本机假 Gateway，对真实 WebSocket 消息顺序作断言。"""
        input_times = []

        async def handler(ws):
            """模拟排队、初始化、输出和关闭事件。"""
            await ws.send(json.dumps({"type": "session.queued"}))
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(ws.recv(), 0.05)
            await ws.send(json.dumps({"type": "session.queue_done"}))
            init = json.loads(await ws.recv())
            assert init["type"] == "session.init" and "payload" in init
            await ws.send(json.dumps({"type": "session.created", "session_id": "fake", "mode": "full_duplex"}))
            for index in range(2):
                item = json.loads(await ws.recv())
                input_times.append(asyncio.get_running_loop().time())
                assert item["type"] == "input.append"
                assert len(probe.decode_pcm(item["input"]["audio"])) == 16000
                metrics = {"wall_clock_ms": 40 + index}
                for kind in ("text", "audio", "listen"):
                    event = {"type": "response.output.delta", "kind": kind, "input_id": f"in{index}", "metrics": metrics}
                    if kind == "text":
                        event["text"] = "测试"
                    if kind == "audio":
                        event["audio"] = probe.encode_pcm(np.zeros(24000, dtype=np.float32))
                    await ws.send(json.dumps(event))
            assert json.loads(await ws.recv())["type"] == "session.close"
            await ws.send(json.dumps({"type": "session.closed", "reason": "m0_probe_done"}))

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            report = await probe.probe(f"ws://127.0.0.1:{port}/v1/realtime?mode=video", probe.input_chunks(None, 2),
                                       probe.build_init("test", None), drain_seconds=0, require_audio=True)
        assert input_times[1] - input_times[0] == pytest.approx(1.0, abs=0.1)
        assert report["protocol_passed"]
        assert report["native_audio_seconds"] == 2.0
        assert report["processing_cost_samples"] == 2
        assert report["realtime_threshold_passed"] is True
        assert report["live_camera_microphone_verified"] is False
        assert "audio" not in report and "voice" not in report and "text" not in report

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["error", "no_outputs", "invalid_audio"])
def test_probe_cannot_report_success_after_failure(failure):
    """服务端错误、无增量和坏音频必须显式失败，不能静默通过。"""
    async def run():
        """模拟三种真实网络会话失败路径。"""
        async def handler(ws):
            """按配置返回错误或遗漏所需的推理事件。"""
            await ws.send(json.dumps({"type": "session.queue_done"}))
            await ws.recv()
            if failure == "error":
                await ws.send(json.dumps({"type": "error", "error": {"code": "backend_error"}}))
                return
            await ws.send(json.dumps({"type": "session.created", "session_id": "fake", "mode": "full_duplex"}))
            await ws.recv()
            if failure == "invalid_audio":
                await ws.send(json.dumps({"type": "response.output.delta", "kind": "audio", "audio": "bad!"}))
                await ws.wait_closed()
                return
            await ws.recv()
            await ws.send(json.dumps({"type": "session.closed", "reason": "m0_probe_done"}))

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            with pytest.raises((ValueError, RuntimeError)):
                await probe.probe(f"ws://127.0.0.1:{port}/v1/realtime?mode=video", probe.input_chunks(None, 1),
                                  probe.build_init("test", None), drain_seconds=0)

    asyncio.run(run())


@pytest.mark.parametrize("metrics", [None, {"cost_all_ms": 1200}])
def test_required_realtime_gate_rejects_missing_or_slow_metrics(metrics):
    """不能把缺失耗时或 P95 超时包装成实时性能通过。"""
    async def run():
        """完成协议会话后检查带报告的性能失败。"""
        async def handler(ws):
            """仅返回一次 listen 增量，随后正常关闭。"""
            await ws.send(json.dumps({"type": "session.queue_done"}))
            await ws.recv()
            await ws.send(json.dumps({"type": "session.created", "session_id": "fake", "mode": "full_duplex"}))
            await ws.recv()
            await ws.send(json.dumps({"type": "response.output.delta", "kind": "listen", "response_id": "r0", "metrics": metrics}))
            await ws.recv()
            await ws.send(json.dumps({"type": "session.closed", "reason": "client_closed"}))

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            with pytest.raises(probe.ProbeValidationError) as failure:
                await probe.probe(f"ws://127.0.0.1:{port}/v1/realtime?mode=audio", probe.input_chunks(None, 1),
                                  probe.build_init("test", None), drain_seconds=0, require_realtime=True)
        assert failure.value.report["protocol_passed"] is True
        assert failure.value.report["required_checks_passed"] is False
        expected = None if metrics is None else 1200
        assert failure.value.report["processing_p95_ms"] == expected

    asyncio.run(run())


def test_missing_native_audio_preserves_failure_report(tmp_path):
    """正常 listen 协议不能充当语音验收通过，失败报告不包含原始媒体。"""
    async def run():
        """在假 Gateway 返回纯 listen 后验证语音验收失败。"""
        async def handler(ws):
            """按完整生命周期返回无语音的有效事件。"""
            await ws.send(json.dumps({"type": "session.queue_done"}))
            await ws.recv()
            await ws.send(json.dumps({"type": "session.created", "session_id": "fake", "mode": "full_duplex"}))
            await ws.recv()
            await ws.send(json.dumps({"type": "response.output.delta", "kind": "listen"}))
            await ws.recv()
            await ws.send(json.dumps({"type": "session.closed", "reason": "client_closed"}))

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            with pytest.raises(probe.ProbeValidationError) as failure:
                await probe.probe(f"ws://127.0.0.1:{port}/v1/realtime?mode=audio", probe.input_chunks(None, 1),
                                  probe.build_init("test", None), drain_seconds=0, require_audio=True)
        report = failure.value.report
        assert report["protocol_passed"] is True and report["required_checks_passed"] is False
        probe.save_report(tmp_path / "failure.json", report)
        assert json.loads((tmp_path / "failure.json").read_text(encoding="utf-8")) == report
        assert "audio" not in report and "text" not in report

    asyncio.run(run())
