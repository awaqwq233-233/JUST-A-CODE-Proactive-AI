#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M0 真机长测：分会话累计 30 分钟上行，持续监测同一组本机后端。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx

import verify_live_duplex as live
import verify_duplex as protocol


def session_lengths(seconds: int, maximum: int = 225) -> list[int]:
    """均分上行时长，让每段远离 Gateway 的 300 秒视频会话上限。"""
    count = (seconds + maximum - 1) // maximum
    base, remainder = divmod(seconds, count)
    return [base + (index < remainder) for index in range(count)]


def sample_rss(pids: list[int]) -> dict[str, int]:
    """只读取指定进程的 RSS KiB，不读取命令参数、媒体或对话内容。"""
    result = subprocess.run(["ps", "-o", "pid=,rss=", "-p", ",".join(map(str, pids))],
                            capture_output=True, text=True, timeout=5)
    return {parts[0]: int(parts[1]) for line in result.stdout.splitlines()
            if len(parts := line.split()) == 2}


def acceptance(sessions: list[dict], target: int, health_failures: int) -> bool:
    """核对完整时长、逐段实时性、设备清理与原生音频收播一致性。"""
    return (sum(item["chunks_sent"] for item in sessions) == target
            and bool(sessions) and health_failures == 0
            and sum(item["native_audio_seconds"] for item in sessions) > 0
            and all(item["protocol_passed"] and item["cleanup_verified"]
                    and item["realtime_threshold_passed"] is True
                    and item["camera_frames"] > 0
                    and item["native_audio_seconds"] == item["played_audio_seconds"]
                    and item["input_status_events"] == item["output_status_events"] == 0
                    for item in sessions))


async def run_soak(args) -> dict:
    """保持后端常驻，保存每段计数与每分钟资源快照，异常也保存失败报告。"""
    started = time.monotonic()
    report = {"required_checks_passed": False, "soak_30min_verified": False,
              "status": "running", "target_input_seconds": args.seconds,
              "session_input_seconds": session_lengths(args.seconds), "sessions": [],
              "resource_samples": [], "health_failures": 0,
              "continuous_single_session_verified": False,
              "context_restoration_verified": False,
              "media_recorded": False, "session_device_gaps_expected": True}
    pids = list(dict.fromkeys([os.getpid(), *args.monitor_pid]))
    protocol.save_report(args.report, report)

    async def monitor() -> None:
        """每 30 秒采样 RSS 和本机健康接口，只输出运行状态。"""
        async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
            while True:
                rss = await asyncio.to_thread(sample_rss, pids)
                healthy = len(rss) == len(pids)
                for port in (8006, 8007, 22400, 22500):
                    try:
                        healthy = (await client.get(f"http://127.0.0.1:{port}/health")).status_code == 200 and healthy
                    except httpx.HTTPError:
                        healthy = False
                report["health_failures"] += not healthy
                report["resource_samples"].append({"elapsed_seconds": round(time.monotonic() - started, 2),
                                                   "rss_kib": rss, "backend_healthy": healthy})
                report["elapsed_seconds"] = round(time.monotonic() - started, 2)
                protocol.save_report(args.report, report)
                print(f"长测 {report['elapsed_seconds']:.0f}s；完成 {len(report['sessions'])} 段；"
                      f"后端健康={healthy}；总 RSS={sum(rss.values()) / 1024:.0f} MiB", flush=True)
                await asyncio.sleep(30)

    watcher = asyncio.create_task(monitor())
    try:
        for index, seconds in enumerate(report["session_input_seconds"], 1):
            print(f"开始第 {index}/{len(report['session_input_seconds'])} 段，真实设备上行 {seconds} 秒。", flush=True)
            session_args = SimpleNamespace(**vars(args))
            session_args.seconds = seconds
            item = await live.run_live(session_args)
            item["session_index"] = index
            report["sessions"].append(item)
            protocol.save_report(args.report, report)
            print(f"第 {index} 段完成：P95={item['processing_p95_ms']}ms；"
                  f"播放={item['played_audio_seconds']}s；清理={item['cleanup_verified']}", flush=True)
            if watcher.done():
                await watcher
        report["required_checks_passed"] = acceptance(report["sessions"], args.seconds, report["health_failures"])
        report["soak_30min_verified"] = args.seconds >= 1800 and report["required_checks_passed"]
        report["status"] = "passed" if report["required_checks_passed"] else "failed"
    except (Exception, asyncio.CancelledError) as error:
        report["status"] = "interrupted" if isinstance(error, asyncio.CancelledError) else "failed"
        # 错误只记录类型，避免上游异常文本含原始用户内容。
        report["error_type"] = type(error).__name__
        print(f"长测异常：{type(error).__name__}", flush=True)
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        report["chunks_sent"] = sum(item["chunks_sent"] for item in report["sessions"])
        protocol.save_report(args.report, report)
    return report


def stop_on_signal(signum, frame) -> None:
    """把终止请求交给 asyncio 的中断清理，释放设备并保存当前报告。"""
    raise KeyboardInterrupt


def main() -> int:
    """显式设备授权后运行长测，沿用短探针的主线程摄像头权限申请。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--consent-devices", action="store_true")
    parser.add_argument("--seconds", type=int, default=1800)
    parser.add_argument("--input-device", type=int)
    parser.add_argument("--output-device", type=int)
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--url", default="ws://127.0.0.1:8006")
    parser.add_argument("--voice", type=Path, default=protocol.ROOT / "voices/silverwalf_voice.wav")
    parser.add_argument("--report", type=Path, default=protocol.ROOT / "output/m0/soak-probe.json")
    parser.add_argument("--monitor-pid", type=int, action="append", default=[])
    args = parser.parse_args()
    if not args.consent_devices:
        parser.error("须明确同意设备长测并戴好耳机，再使用 --consent-devices")
    if not 10 <= args.seconds <= 1800:
        parser.error("测试时长须为 10–1800 秒")
    protocol.gateway_url(args.url, "video")
    signal.signal(signal.SIGTERM, stop_on_signal)
    if sys.platform == "darwin":
        permission_probe = live.cv2.VideoCapture(args.camera)
        try:
            if not permission_probe.isOpened():
                raise RuntimeError("macOS 摄像头权限未授予")
        finally:
            permission_probe.release()
    report = asyncio.run(run_soak(args))
    print(json.dumps({key: value for key, value in report.items() if key != "resource_samples"},
                     ensure_ascii=False, indent=2))
    return 0 if report["required_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
