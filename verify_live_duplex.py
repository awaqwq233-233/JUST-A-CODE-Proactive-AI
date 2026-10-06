#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M0 短时设备探针：经明确同意才开启摄像头、麦克风和原生音频播放。"""

from __future__ import annotations

import argparse
import asyncio
import json
import queue
import sys
import threading
import time
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
import sounddevice as sd
import soxr
from websockets.asyncio.client import connect

import verify_duplex as protocol


class LiveDevices:
    """隔离音视频采集与播放，所有缓冲有界且不写原始媒体文件。"""

    def __init__(self, input_device: int | None, output_device: int | None, camera: int):
        """创建缓冲与统计项，不在构造时打开设备。"""
        self.input_device, self.output_device, self.camera = input_device, output_device, camera
        self.raw_audio = queue.Queue(maxsize=30)
        self.audio = queue.Queue(maxsize=5)
        self.video = queue.Queue(maxsize=1)
        self.playback = queue.Queue(maxsize=32)
        self.stop_event = threading.Event()
        self.camera_ready = threading.Event()
        self.error = None
        self.mic = self.speaker = None
        self.threads = []
        self.pending_output = np.zeros(0, dtype="<f4")
        self.played_samples = self.captured_frames = 0
        self.audio_queue_max = self.playback_queue_max = 0
        self.input_status_events = self.output_status_events = 0
        self.mic_rms_max = 0.0

    def microphone_callback(self, data, frames, timing, status) -> None:
        """实时回调只复制入队；溢出显式失败，绝不悄悄丢弃用户语音。"""
        if status:
            self.input_status_events += 1
            self.error = f"麦克风流状态异常: {status}"
        try:
            self.raw_audio.put_nowait(data[:, 0].copy())
        except queue.Full:
            self.error = "麦克风原始音频队列溢出"

    def speaker_callback(self, output, frames, timing, status) -> None:
        """顺序播放原生 24k 音频；没有待播数据时填零，不阻塞设备回调。"""
        if status:
            self.output_status_events += 1
        output.fill(0)
        offset = 0
        while offset < frames:
            if not len(self.pending_output):
                try:
                    self.pending_output = self.playback.get_nowait()
                except queue.Empty:
                    return
            count = min(frames - offset, len(self.pending_output))
            output[offset:offset + count, 0] = self.pending_output[:count]
            self.pending_output = self.pending_output[count:]
            offset += count
            self.played_samples += count

    def audio_worker(self) -> None:
        """后台流式重采样 48k→16k，按完整一秒入队并保留波形顺序。"""
        resampler = soxr.ResampleStream(48000, 16000, 1, dtype="float32")
        pending = np.zeros(0, dtype="<f4")
        while not self.stop_event.is_set():
            try:
                raw = self.raw_audio.get(timeout=0.2)
            except queue.Empty:
                continue
            self.mic_rms_max = max(self.mic_rms_max, float(np.sqrt(np.mean(raw * raw))))
            converted = resampler.resample_chunk(raw)
            pending = np.concatenate((pending, converted))
            while len(pending) >= 16000:
                try:
                    self.audio.put_nowait(pending[:16000].copy())
                except queue.Full:
                    self.error = "一秒音频队列溢出，终止探针而不丢弃语音"
                    return
                self.audio_queue_max = max(self.audio_queue_max, self.audio.qsize())
                pending = pending[16000:]

    def camera_worker(self) -> None:
        """独立线程读取与编码 640×480 JPEG，5fps 采集且只保留最新帧。"""
        camera = cv2.VideoCapture(self.camera)
        try:
            camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            camera.set(cv2.CAP_PROP_FPS, 5)
            if not camera.isOpened():
                self.error = "摄像头无法打开或权限未授予"
                return
            while not self.stop_event.is_set():
                started = time.monotonic()
                success, frame = camera.read()
                if not success:
                    self.error = "摄像头读取失败"
                    return
                if frame.shape[:2] != (480, 640):
                    frame = cv2.resize(frame, (640, 480))
                success, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                if not success:
                    self.error = "JPEG 编码失败"
                    return
                if self.video.full():
                    self.video.get_nowait()
                self.video.put_nowait(encoded.tobytes())
                self.captured_frames += 1
                self.camera_ready.set()
                self.stop_event.wait(max(0, 0.2 - (time.monotonic() - started)))
        finally:
            camera.release()
            self.camera_ready.set()

    def start(self) -> None:
        """启动设备流与两个后台线程；失败也可由 stop 完整回收。"""
        self.mic = sd.InputStream(device=self.input_device, samplerate=48000, channels=1,
                                  dtype="float32", blocksize=4800, callback=self.microphone_callback)
        self.speaker = sd.OutputStream(device=self.output_device, samplerate=24000, channels=1,
                                      dtype="float32", blocksize=1200, callback=self.speaker_callback)
        self.threads = [threading.Thread(target=self.audio_worker, name="m0-audio"),
                        threading.Thread(target=self.camera_worker, name="m0-camera")]
        self.speaker.start()
        self.mic.start()
        for worker in self.threads:
            worker.start()

    def enqueue_output(self, samples: np.ndarray) -> None:
        """把原生输出交给播放回调，播放积压超界时显式失败。"""
        try:
            self.playback.put_nowait(samples)
        except queue.Full as error:
            raise RuntimeError("原生音频播放队列溢出") from error
        self.playback_queue_max = max(self.playback_queue_max, self.playback.qsize())

    def latest_video(self) -> bytes | None:
        """在 Queue 自身互斥锁内读取最新帧，不暴露非线程安全的队列访问。"""
        with self.video.mutex:
            return self.video.queue[-1] if self.video.queue else None

    def finish_input(self) -> None:
        """上行结束即停止采集，避免等待尾部 TTS 时音频队列被继续填满。"""
        self.stop_event.set()
        if self.mic is not None:
            self.mic.abort()
            self.mic.close()
            self.mic = None

    def stop(self) -> bool:
        """停止流、释放摄像头并等待线程退出，不影响其他应用的设备。"""
        self.stop_event.set()
        for stream in (self.mic, self.speaker):
            if stream is not None:
                stream.abort()
                stream.close()
        for worker in self.threads:
            if worker.ident is not None:
                worker.join(timeout=5)
        return all(not worker.is_alive() for worker in self.threads)


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
            await ws.send(json.dumps(protocol.build_init("你是 J.A.C. 私人助手。请自然地用中文回应听到的话。", protocol.load_audio(args.voice))))
            created = await protocol.receive_until(ws, "session.created", 120, events)
            if created.get("mode") != "full_duplex":
                raise RuntimeError("未进入全双工会话")
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
