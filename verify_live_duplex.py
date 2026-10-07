#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M0 短时设备探针：经明确同意才开启摄像头、麦克风和原生音频播放。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
from websockets.asyncio.client import connect

import verify_duplex as protocol
from src.omni.media import LiveDevices


async def run_live(args) -> dict:
    """连接本机 Gateway 后短时采集和播放，并输出只含计数的验收报告。"""
    devices = LiveDevices(args.input_device, args.output_device, args.camera)
    events, kinds = Counter(), Counter()
    audio_samples, costs, lateness = 0, {}, []
    closed = None
    cleanup = False
    started = time.monotonic()
    try:
        async with connect(protocol.gateway_url(args.url, "video"), proxy=None, max_size=32 * 1024 * 1024) as ws:
            await protocol.receive_until(ws, "session.queue_done", 120, events)
            init = protocol.build_init("你是 J.A.C. 私人助手。请自然地用中文回应听到的话。", protocol.load_audio(args.voice))
            await ws.send(json.dumps(init))
            created = await protocol.receive_until(ws, "session.created", 120, events)
            if created.get("mode") != "full_duplex":
                raise RuntimeError("未进入全双工会话")
            protocol.require_voice_condition(created, init)
            devices.start()
            await asyncio.to_thread(devices.camera_ready.wait, 8)
            if devices.error or not devices.captured_frames:
                raise RuntimeError(devices.error or "摄像头没有产生视频帧")
            print("设备已开启；4 秒启动保护后可自然提问，探针将在短时测试后自动停止。", flush=True)

            async def receive_outputs() -> None:
                """独立接收文本/原生音频事件，不保存媒体或对话文本。"""
                nonlocal audio_samples, closed
                while True:
                    event = protocol.check_event(await ws.recv())
                    events[event["type"]] += 1
                    if event["type"] == "session.closed":
                        closed = event.get("reason")
                        return
                    if event["type"] != "response.output.delta":
                        continue
                    kind = event.get("kind")
                    if kind not in {"listen", "text", "audio"}:
                        raise ValueError(f"未知输出类型: {kind}")
                    kinds[kind] += 1
                    if kind == "audio":
                        samples = protocol.decode_pcm(event["audio"])
                        audio_samples += len(samples)
                        devices.enqueue_output(samples)
                    metrics = event.get("metrics") or {}
                    cost = metrics.get("cost_all_ms", metrics.get("wall_clock_ms"))
                    key = event.get("input_id") or event.get("response_id") or metrics.get("chunk_index")
                    if isinstance(cost, (float, int)) and np.isfinite(cost) and cost > 0 and key is not None:
                        costs[str(key)] = max(float(cost), costs.get(str(key), 0))

            receiver = asyncio.create_task(receive_outputs())
            try:
                stream_start = time.monotonic()
                for index in range(args.seconds):
                    chunk = await asyncio.to_thread(devices.audio.get, True, 2)
                    if index == 0:
                        stream_start = time.monotonic()
                    await asyncio.sleep(max(0, stream_start + index - time.monotonic()))
                    if devices.error:
                        raise RuntimeError(devices.error)
                    if receiver.done():
                        await receiver
                        raise RuntimeError("上行完成前会话关闭")
                    lateness.append(max(0, (time.monotonic() - stream_start - index) * 1000))
                    latest_frame = devices.latest_video()
                    # 启动保护不影响原始采集；其后全程发送真实麦克风且允许打断。
                    if index < 4:
                        chunk = np.zeros(16000, dtype="<f4")
                    await ws.send(json.dumps(protocol.build_input(chunk, latest_frame)))
                await asyncio.to_thread(devices.finish_input)
                done, _ = await asyncio.wait({receiver}, timeout=8)
                if done:
                    await receiver
                    raise RuntimeError("主动关闭前服务端提前结束")
                await ws.send(json.dumps({"type": "session.close", "reason": "m0_live_done"}))
                await asyncio.wait_for(receiver, 30)
                deadline = time.monotonic() + 5
                while (not devices.playback.empty() or len(devices.pending_output)) and time.monotonic() < deadline:
                    await asyncio.sleep(0.1)
            finally:
                if not receiver.done():
                    receiver.cancel()
                await asyncio.gather(receiver, return_exceptions=True)
    finally:
        cleanup = await asyncio.to_thread(devices.stop)
    p95 = float(np.percentile(list(costs.values()), 95)) if costs else None
    passed = (audio_samples > 0 and devices.played_samples == audio_samples and cleanup
              and devices.error is None and p95 is not None and p95 < 1000)
    return {
        "protocol_passed": True, "live_camera_microphone_verified": True,
        "native_audio_received": audio_samples > 0, "native_playback_verified": devices.played_samples == audio_samples and audio_samples > 0,
        "required_checks_passed": passed, "cleanup_verified": cleanup, "soak_30min_verified": False,
        "events": dict(events), "output_kinds": dict(kinds), "chunks_sent": args.seconds,
        "camera_frames": devices.captured_frames, "camera_width": 640, "camera_height": 480,
        "input_rate": 16000, "output_rate": 24000, "native_audio_seconds": round(audio_samples / 24000, 3),
        "played_audio_seconds": round(devices.played_samples / 24000, 3), "mic_rms_max": round(devices.mic_rms_max, 5),
        "audio_queue_max": devices.audio_queue_max, "playback_queue_max": devices.playback_queue_max,
        "input_status_events": devices.input_status_events, "output_status_events": devices.output_status_events,
        "send_lateness_max_ms": round(max(lateness, default=0), 3), "processing_cost_samples": len(costs),
        "processing_p95_ms": p95, "realtime_threshold_passed": p95 < 1000 if p95 is not None else None,
        "closed_reason": closed, "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def main() -> int:
    """只有明确授权设备后才运行短时探针，失败时也自动回收设备。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--consent-devices", action="store_true", help="已同意短时开启设备且已戴耳机")
    parser.add_argument("--seconds", type=int, default=40)
    parser.add_argument("--input-device", type=int)
    parser.add_argument("--output-device", type=int)
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--url", default="ws://127.0.0.1:8006")
    parser.add_argument("--voice", type=Path, default=protocol.ROOT / "voices/silverwalf_voice.wav")
    parser.add_argument("--report", type=Path, default=protocol.ROOT / "output/m0/live-probe.json")
    args = parser.parse_args()
    if not args.consent_devices:
        parser.error("须明确同意短时设备测试并戴好耳机，再使用 --consent-devices")
    if not 10 <= args.seconds <= 120:
        parser.error("短时设备测试限 10–120 秒")
    try:
        if sys.platform == "darwin":
            # AVFoundation 首次授权必须在启动主线程申请；不在此读取或编码帧。
            permission_probe = cv2.VideoCapture(args.camera)
            try:
                if not permission_probe.isOpened():
                    raise RuntimeError("macOS 摄像头权限未授予，请检查系统隐私设置")
            finally:
                permission_probe.release()
        report = asyncio.run(run_live(args))
        protocol.save_report(args.report, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["required_checks_passed"] else 1
    except (Exception, KeyboardInterrupt) as error:
        print(f"真机探针未通过: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
