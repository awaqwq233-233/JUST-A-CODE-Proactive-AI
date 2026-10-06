#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""方案 B M0 探针：验证本地 Gateway 的固定节拍与原生音频协议。"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import math
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, urlencode

import numpy as np
import soundfile as sf
import soxr
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake

ROOT = Path(__file__).resolve().parent
INPUT_RATE = 16000
OUTPUT_RATE = 24000
SAMPLES_PER_CHUNK = 16000


class ProbeValidationError(RuntimeError):
    """携带已完成协议会话的失败报告，便于保存原生语音验收证据。"""

    def __init__(self, message: str, report: dict) -> None:
        """保存无原始媒体的报告，同时保留普通 RuntimeError 行为。"""
        super().__init__(message)
        self.report = report


def load_audio(path: Path) -> np.ndarray:
    """读取音频并转换成连续、有限值的 16k mono float32 波形。"""
    samples, rate = sf.read(path, dtype="float32", always_2d=True)
    mono = samples.mean(axis=1)
    if rate != INPUT_RATE:
        mono = soxr.resample(mono, rate, INPUT_RATE)
    if not len(mono) or not np.isfinite(mono).all():
        raise ValueError("输入音频为空或含非有限值")
    return np.asarray(mono, dtype="<f4")


def encode_pcm(samples: np.ndarray) -> str:
    """把 mono float32 波形编码成协议要求的 little-endian Base64。"""
    samples = np.asarray(samples, dtype="<f4")
    if samples.ndim != 1 or not np.isfinite(samples).all():
        raise ValueError("PCM 必须是有限值的一维波形")
    return base64.b64encode(samples.tobytes()).decode("ascii")


def decode_pcm(value: str) -> np.ndarray:
    """校验下行 Base64，并返回 24k 播放流使用的 float32 样本。"""
    raw = base64.b64decode(value, validate=True)
    if not raw or len(raw) % 4:
        raise ValueError("下行音频字节数不是非空 float32 数组")
    samples = np.frombuffer(raw, dtype="<f4")
    if not np.isfinite(samples).all():
        raise ValueError("下行音频含非有限值")
    return samples


def build_init(prompt: str, voice: np.ndarray | None) -> dict:
    """生成当前公开 Gateway 协议的 session.init 消息。"""
    payload = {"system_prompt": prompt, "config": {"length_penalty": 1.1}}
    if voice is not None:
        reference = encode_pcm(voice)
        payload["voice"] = {
            "ref_audio_base64": reference,
            "tts_ref_audio_base64": reference,
        }
    return {"type": "session.init", "payload": payload}


def build_input(samples: np.ndarray, jpeg: bytes | None = None) -> dict:
    """组装恰好一秒音频和可选的一帧 JPEG，不包含旧协议字段。"""
    if len(samples) != SAMPLES_PER_CHUNK:
        raise ValueError("每块必须恰好有 16000 个样本")
    body = {"audio": encode_pcm(samples)}
    if jpeg is not None:
        if not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9"):
            raise ValueError("视频帧必须是完整 JPEG")
        body.update(video_frames=[base64.b64encode(jpeg).decode("ascii")], max_slice_nums=1)
    return {"type": "input.append", "input": body}


def input_chunks(samples: np.ndarray | None, count: int) -> list[np.ndarray]:
    """将样本顺序分块并补尾部静音，绝不重复或丢弃输入语音。"""
    samples = np.zeros(0, dtype="<f4") if samples is None else samples
    result = []
    for index in range(count):
        chunk = np.zeros(SAMPLES_PER_CHUNK, dtype="<f4")
        source = samples[index * SAMPLES_PER_CHUNK:(index + 1) * SAMPLES_PER_CHUNK]
        chunk[:len(source)] = source
        result.append(chunk)
    return result


def gateway_url(url: str, mode: str) -> str:
    """限制探针连接本机 Gateway，防止将参考音频意外发送给云服务。"""
    parts = urlsplit(url)
    if parts.scheme not in {"ws", "wss"} or parts.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("M0 探针只允许连接本机 ws/wss Gateway")
    if parts.username or parts.password:
        raise ValueError("URL 中不得包含凭据")
    return urlunsplit((parts.scheme, parts.netloc, "/v1/realtime", urlencode({"mode": mode}), ""))


def check_event(message: str | bytes) -> dict:
    """解析服务端事件并立即报告结构化协议错误。"""
    event = json.loads(message)
    if not isinstance(event, dict) or not isinstance(event.get("type"), str):
        raise ValueError("服务端事件必须是包含 type 的 JSON 对象")
    if event["type"] == "error":
        error = event.get("error") or {}
        raise RuntimeError(f"服务端错误: {error.get('code', 'unknown')}")
    return event


async def receive_until(ws, expected: str, timeout: float, events: Counter) -> dict:
    """在总时限内等待指定生命周期事件，拒绝提前关闭的会话。"""
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"等待 {expected} 超时")
        event = check_event(await asyncio.wait_for(ws.recv(), remaining))
        events[event["type"]] += 1
        if event["type"] == expected:
            return event
        if event["type"] == "session.closed":
            raise RuntimeError(f"会话提前关闭: {event.get('reason', 'unknown')}")


async def probe(
    url: str,
    chunks: list[np.ndarray],
    init: dict,
    *,
    jpeg: bytes | None = None,
    timeout: float = 120,
    drain_seconds: float = 10,
    require_audio: bool = False,
    require_realtime: bool = False,
) -> dict:
    """并行发送和接收，验证完整生命周期并返回不含原始音视频的报告。"""
    events = Counter()
    kinds = Counter()
    audio_samples = 0
    costs = {}
    text_chars = 0
    offsets = []
    closed_reason = None
    started = time.monotonic()
    async with connect(url, proxy=None, max_size=32 * 1024 * 1024, open_timeout=timeout) as ws:
        await receive_until(ws, "session.queue_done", timeout, events)
        await ws.send(json.dumps(init, ensure_ascii=False))
        created = await receive_until(ws, "session.created", timeout, events)
        if not created.get("session_id"):
            raise ValueError("session.created 缺少 session_id")
        if created.get("mode") != "full_duplex":
            raise ValueError("服务端未进入 full_duplex")

        async def receive_outputs() -> None:
            """持续校验增量输出，播放流与文本流分别统计。"""
            nonlocal audio_samples, text_chars, closed_reason
            while True:
                event = check_event(await ws.recv())
                events[event["type"]] += 1
                if event["type"] == "session.closed":
                    closed_reason = event.get("reason")
                    return
                if event["type"] != "response.output.delta":
                    continue
                kind = event.get("kind")
                if kind not in {"listen", "text", "audio"}:
                    raise ValueError(f"未知 output kind: {kind}")
                kinds[kind] += 1
                if kind == "audio":
                    audio_samples += len(decode_pcm(event["audio"]))
                elif kind == "text":
                    if not isinstance(event.get("text"), str):
                        raise ValueError("文本增量缺少 text 字段")
                    text_chars += len(event["text"])
                metrics = event.get("metrics") or {}
                cost = metrics.get("cost_all_ms", metrics.get("wall_clock_ms"))
                if isinstance(cost, (float, int)) and math.isfinite(cost) and cost > 0:
                    # 同一输入的 text/audio 可能重复带耗时，优先按 input_id 去重。
                    input_id = event.get("input_id") or event.get("response_id") or metrics.get("chunk_index")
                    if input_id is not None:
                        costs[str(input_id)] = max(float(cost), costs.get(str(input_id), 0))

        receiver = asyncio.create_task(receive_outputs())
        try:
            stream_start = time.monotonic()
            for index, chunk in enumerate(chunks):
                await asyncio.sleep(max(0, stream_start + index - time.monotonic()))
                if receiver.done():
                    await receiver
                    raise RuntimeError("发送完成前服务端结束会话")
                offsets.append((time.monotonic() - stream_start - index) * 1000)
                await ws.send(json.dumps(build_input(chunk, jpeg)))
            # 留出最后一块的音频时长和尾部 TTS 生成窗口。
            done, _ = await asyncio.wait({receiver}, timeout=1 + drain_seconds)
            if done:
                await receiver
                raise RuntimeError(f"主动关闭前会话已结束: {closed_reason}")
            await ws.send(json.dumps({"type": "session.close", "reason": "m0_probe_done"}))
            await asyncio.wait_for(receiver, timeout=timeout)
        finally:
            if not receiver.done():
                receiver.cancel()
            await asyncio.gather(receiver, return_exceptions=True)

    if not sum(kinds.values()):
        raise RuntimeError("没有收到任何模型增量，不能认定协议探针通过")
    p95 = float(np.percentile(list(costs.values()), 95)) if costs else None
    realtime_passed = p95 < 1000 if p95 is not None else None
    report = {
        "protocol_passed": True,
        "required_checks_passed": (not require_audio or audio_samples > 0)
        and (not require_realtime or realtime_passed is True),
        "native_audio_received": audio_samples > 0,
        "events": dict(events),
        "output_kinds": dict(kinds),
        "chunks_sent": len(chunks),
        "input_sample_rate": INPUT_RATE,
        "output_sample_rate": OUTPUT_RATE,
        "native_audio_seconds": round(audio_samples / OUTPUT_RATE, 3),
        "text_chars": text_chars,
        "send_lateness_max_ms": round(max(offsets, default=0), 3),
        "processing_cost_samples": len(costs),
        "processing_p95_ms": p95,
        "realtime_threshold_passed": realtime_passed,
        "closed_reason": closed_reason,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "live_camera_microphone_verified": False,
        "soak_30min_verified": False,
    }
    if require_audio and not audio_samples:
        raise ProbeValidationError("没有收到原生 TTS 音频，语音验收未通过", report)
    if require_realtime and realtime_passed is not True:
        raise ProbeValidationError("处理耗时缺失或 P95 不低于 1 秒，实时性能未通过", report)
    return report


def save_report(path: Path, report: dict) -> None:
    """保存计数和耗时报告，原始 PCM、参考音和文本均不进入 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    """运行 CLI 探针并把无原始媒体的结果保存到忽略目录。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="ws://127.0.0.1:8006")
    parser.add_argument("--mode", choices=["video", "audio"], default="video")
    parser.add_argument("--audio", type=Path, help="输入 WAV；不提供则发送静音")
    parser.add_argument("--voice", type=Path, help="音色参考 WAV")
    parser.add_argument("--image", type=Path, help="640×480 JPEG 测试帧")
    parser.add_argument("--chunks", type=int, default=12)
    parser.add_argument("--lead-silence-seconds", type=int, default=4, help="输入语音前的启动保护静音")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--drain-seconds", type=float, default=10)
    parser.add_argument("--require-audio", action="store_true")
    parser.add_argument("--require-realtime", action="store_true", help="缺少指标或 P95>=1000ms 时失败")
    parser.add_argument("--report", type=Path, default=ROOT / "output/m0/probe.json")
    args = parser.parse_args()
    if not 1 <= args.chunks <= (280 if args.mode == "video" else 580):
        parser.error("块数超出当前会话时限范围")
    if args.timeout <= 0 or args.drain_seconds < 0:
        parser.error("timeout 必须大于零，drain-seconds 不得为负")
    if not 0 <= args.lead_silence_seconds < args.chunks:
        parser.error("启动静音须非负且短于总块数")
    if args.mode == "audio" and args.image:
        parser.error("audio 模式不能发送视频帧")
    try:
        audio = load_audio(args.audio) if args.audio else None
        if audio is not None:
            audio = np.concatenate((np.zeros(args.lead_silence_seconds * INPUT_RATE, dtype="<f4"), audio))
        if audio is not None and len(audio) > args.chunks * SAMPLES_PER_CHUNK:
            raise ValueError("输入文件长于探针时长，请增加 --chunks，避免截断语音")
        voice = load_audio(args.voice) if args.voice else None
        jpeg = args.image.read_bytes() if args.image else None
        report = asyncio.run(probe(
            gateway_url(args.url, args.mode), input_chunks(audio, args.chunks),
            build_init("你是 J.A.C. 私人助手。请自然地用中文回应听到的话。", voice),
            jpeg=jpeg, timeout=args.timeout, drain_seconds=args.drain_seconds,
            require_audio=args.require_audio,
            require_realtime=args.require_realtime,
        ))
        save_report(args.report, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except ProbeValidationError as error:
        save_report(args.report, error.report)
        print(json.dumps(error.report, ensure_ascii=False, indent=2))
        print(f"M0 未通过: {error}")
        return 1
    except (OSError, ValueError, RuntimeError, TimeoutError, ConnectionClosed, InvalidHandshake) as error:
        print(f"M0 未通过: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
