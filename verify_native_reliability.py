#!/usr/bin/env python3
"""固定后端原生回归：连续聆听、字面播报与重建；不打开采集或播放设备。"""

import argparse
import asyncio
from collections import Counter
import json
from pathlib import Path
import time
from uuid import uuid4

import cv2
import numpy as np
import soundfile as sf
from websockets.asyncio.client import connect

from src.omni import realtime_protocol as protocol

ROOT = Path(__file__).resolve().parent
TEXTS = (
    "这是合成语音测试。数字是百分之八十，完整结果已保存。",
    "现在进行语音测试。桌面上有蓝色窗口、白色文字和三个按钮。",
)


class NativeObserver:
    """按任务标识核对原生音频；只缓存有界的固定测试句。"""

    def __init__(self, save_audio=False):
        """建立计数和任务状态，默认不保留 PCM。"""
        self.counts, self.jobs = Counter(), {}
        self.save_audio = save_audio
        self.closed = False

    def accept(self, event):
        """处理已解析事件，拒绝超限 PCM、重复终态和无归属的任务数据。"""
        kind = event["type"]
        if kind == "session.closed":
            self.closed = True
        if kind == "response.output.delta" and event.get("kind") == "listen":
            self.counts["listen"] += 1
        identifier = event.get("task_speech_id") if kind == "response.output.delta" else event.get("id")
        if identifier is None:
            return
        if identifier not in self.jobs:
            raise ValueError("收到不属于本探针的任务事件")
        job = self.jobs[identifier]
        if kind == "task.speech":
            state = event.get("state")
            if state == "accepted" and not job["accepted"] and not job["done"]:
                job["accepted"] = True
            elif state == "completed" and job["accepted"] and not job["done"]:
                count = event.get("audio_samples")
                if (type(count) is not int or count != job["samples"] or not job["nonzero"]
                        or job["received"] != job["expected"]):
                    raise ValueError("任务文本或音频样本数不匹配")
                job["done"] = True
            else:
                raise ValueError("任务被拒绝、失败或收到重复终态")
        elif kind == "response.output.delta":
            if not job["accepted"] or job["done"]:
                raise ValueError("任务数据发生在有效生命周期之外")
            if event.get("kind") == "text":
                job["received"] += event["text"]
                if not job["expected"].startswith(job["received"]):
                    raise ValueError("原生文本偏离固定测试句")
            elif event.get("kind") == "audio":
                pcm = protocol.decode_pcm(event["audio"])
                job["samples"] += len(pcm)
                if job["samples"] > 30 * protocol.OUTPUT_RATE:
                    raise ValueError("测试句原生音频超过三十秒上限")
                job["nonzero"] |= bool(np.any(np.abs(pcm) > 1e-7))
                if self.save_audio:
                    job["pcm"].append(pcm.copy())

    def add(self, identifier, text):
        """只登记固定测试句，每项最多接收三十秒原生音频。"""
        self.jobs[identifier] = dict(expected=text, received="", accepted=False, done=False,
                                    samples=0, nonzero=False, pcm=[])

    def report(self):
        """移除 PCM，只返回计数、精确文本和生命周期检查。"""
        return dict(counts=dict(self.counts), closed=self.closed,
                    jobs=[dict(id=key, **{k: v for k, v in value.items() if k != "pcm"})
                          for key, value in self.jobs.items()])


def test_frame():
    """生成固定 640×480 测试图，不读取摄像头或用户截图。"""
    frame = np.full((480, 640, 3), (70, 35, 15), dtype="uint8")
    cv2.rectangle(frame, (80, 80), (560, 400), (180, 110, 30), -1)
    ok, encoded = cv2.imencode(".jpg", frame)
    if not ok:
        raise RuntimeError("合成测试图编码失败")
    return encoded.tobytes()


async def checked_wait(receiver, predicate, timeout):
    """有界等待状态，接收器异常或提前关闭立即失败。"""
    deadline = time.monotonic() + timeout
    while not predicate():
        if receiver.done():
            await receiver
            raise RuntimeError("后端提前关闭")
        if time.monotonic() >= deadline:
            raise TimeoutError("原生步骤未在时限内完成")
        await asyncio.sleep(.02)


async def run_session(url, voice, observer):
    """逐块配对验证 64 次强制聆听，再验证两项原生任务和关闭握手。"""
    async with connect(protocol.gateway_url(url, "video"), max_size=8 * 1024 * 1024) as ws:
        await protocol.receive_until(ws, "session.queue_done", 30, Counter())
        init = protocol.build_init("请用简体中文回应。", voice)
        await ws.send(json.dumps(init))
        created = await protocol.receive_until(ws, "session.created", 120, Counter())
        protocol.require_voice_condition(created, init)
        protocol.require_task_speech(created)

        async def receive():
            """唯一接收协程持续处理协议事件，错误传回主探针。"""
            async for raw in ws:
                observer.accept(protocol.check_event(raw))
                if observer.closed:
                    return

        receiver = asyncio.create_task(receive())
        frame, silence = test_frame(), np.zeros(16000, dtype="float32")
        last_send = 0.0

        async def send(*, forced=False, speech=None):
            """维持至少一秒间隔，不通过加速推送掩盖后端积压。"""
            nonlocal last_send
            await asyncio.sleep(max(0, last_send + 1 - time.monotonic()))
            body = protocol.build_input(silence, frame)
            if forced:
                body["input"]["force_listen"] = True
            if speech is not None:
                body["input"]["task_speech"] = speech
            last_send = time.monotonic()
            await ws.send(json.dumps(body))

        try:
            # 64 exceeds the pinned engine's combined prefill/encoder queue capacity.
            for step in range(64):
                await send(forced=True)
                await checked_wait(receiver, lambda: observer.counts["listen"] == step + 1, 5)
            for text in TEXTS:
                identifier = "probe-" + uuid4().hex
                observer.add(identifier, text)
                await send(speech=dict(id=identifier, text=text))
                deadline = time.monotonic() + 30
                while not observer.jobs[identifier]["done"]:
                    if receiver.done():
                        await receiver
                        raise RuntimeError("播报中连接提前关闭")
                    if time.monotonic() >= deadline:
                        raise TimeoutError("字面播报超时")
                    await send()
                await asyncio.sleep(1)
            await asyncio.sleep(8)
            await ws.send(json.dumps({"type": "session.close"}))
            await asyncio.wait_for(receiver, 15)
            if not observer.closed:
                raise RuntimeError("缺少关闭确认")
        finally:
            if not receiver.done():
                try:
                    await ws.send(json.dumps({"type": "session.close"}))
                    await asyncio.wait_for(receiver, 15)
                finally:
                    receiver.cancel()
                    await asyncio.gather(receiver, return_exceptions=True)
        await asyncio.wait_for(ws.wait_closed(), 15)
    await asyncio.sleep(1)


def main(argv=None):
    """运行有界文件验证并始终保存报告，技术通过不等于听感验收。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="ws://127.0.0.1:8006")
    parser.add_argument("--sessions", type=int, choices=range(1, 5), default=2)
    parser.add_argument("--report", type=Path, default=ROOT / "output/reliability/native.json")
    parser.add_argument("--save-generated-audio", action="store_true", help="保存固定测试句的原生 WAV，便于人工试听")
    args = parser.parse_args(argv)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    report = dict(passed=False, devices_opened=False, human_listening_verified=False,
                  forced_listen_steps_per_session=64, sessions=[])
    try:
        voice = protocol.load_audio(ROOT / "voices/silverwalf_voice.wav")
        for index in range(args.sessions):
            observer = NativeObserver(args.save_generated_audio)
            try:
                asyncio.run(run_session(args.url, voice, observer))
            finally:
                report["sessions"].append(observer.report())
                if args.save_generated_audio:
                    for number, job in enumerate(observer.jobs.values()):
                        if job["pcm"]:
                            sf.write(args.report.parent / f"native-{index + 1}-{number + 1}.wav",
                                     np.concatenate(job["pcm"]), protocol.OUTPUT_RATE)
        report["passed"] = True
        return 0
    except Exception as error:
        report.update(error_type=type(error).__name__, error=str(error))
        return 2
    finally:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
