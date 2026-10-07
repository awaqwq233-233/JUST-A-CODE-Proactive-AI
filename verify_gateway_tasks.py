#!/usr/bin/env python3
"""M2b 固定 Gateway 文件探针：真实旁路 VAD/Whisper/Qwen，不打开设备。"""

import argparse
import asyncio
from collections import Counter
import json
from pathlib import Path

import numpy as np

from src.omni.gateway_client import GatewayClient, GatewayCallbacks, ROOT
from src.omni.gateway_cli import ReplayDevices
from src.omni.realtime_protocol import load_audio
from src.omni.task_pipeline import route_instruction
from src.omni.transcription_worker import DEFAULT_MODEL_DIR


class ProbeCallbacks(GatewayCallbacks):
    """只在内存保留断言所需转写，报告中仅保存计数和文件名。"""

    def __init__(self):
        """初始化验证计数。"""
        self.counts, self.fields, self.files = Counter(), [], []

    def on_user_transcript(self, text):
        """核对转写来自完整指令，不把原话写入日志。"""
        self.counts["user_transcripts"] += 1
        fields = route_instruction(text)
        if fields:
            self.fields.append(list(fields))

    def on_task_event(self, state, detail):
        """只记录真实任务状态，完成时核对报告存在。"""
        self.counts["task_" + state] += 1
        if state == "completed":
            path = Path(detail["path"])
            if not path.is_file():
                raise RuntimeError("任务完成但报告不存在")
            self.files.append(path.name)
        print(f"[大脑] {state}", flush=True)


def main(argv=None):
    """回放合成或明确指定文件，单会话结束后验证唯一文件交付。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-file", type=Path, required=True)
    parser.add_argument("--image-file", type=Path, required=True)
    parser.add_argument("--url", default="ws://127.0.0.1:8006")
    parser.add_argument("--brain-url", default="http://127.0.0.1:12345")
    parser.add_argument("--whisper-model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--seconds", type=int, default=24)
    parser.add_argument("--report", type=Path, default=ROOT / "output/m2/gateway-tasks.json")
    args = parser.parse_args(argv)
    samples = np.concatenate((np.zeros(4 * 16000, dtype="float32"), load_audio(args.input_file)))
    if len(samples) + 16000 > args.seconds * 16000:
        parser.error("时长须包含启动保护、完整语音和句末静音")
    jpeg, callbacks = args.image_file.read_bytes(), ProbeCallbacks()

    def devices(**kwargs):
        """纯内存回放接口；不保存或播放收到的原生音频。"""
        return ReplayDevices(samples, jpeg, **kwargs)

    client = GatewayClient(url=args.url, callbacks=callbacks, consent_devices=True,
                           device_factory=devices, session_seconds=args.seconds,
                           transcription_enabled=True, whisper_model_dir=args.whisper_model_dir,
                           brain_url=args.brain_url)
    report = {"passed": False, "devices_opened": False, "raw_media_recorded": False}
    try:
        asyncio.run(client.run(max_sessions=1))
        report.update(stats=client.stats(), counts=dict(callbacks.counts),
                      routed_fields=callbacks.fields, files=callbacks.files)
        if (callbacks.counts["task_completed"] != 1 or len(callbacks.files) != 1
                or callbacks.counts["task_error"] or report["stats"].get("cleanup_failures")):
            raise RuntimeError("未完成唯一真实任务或清理失败")
        report["passed"] = True
        return 0
    except Exception as error:
        report.update(error_type=type(error).__name__, stats=client.stats(), counts=dict(callbacks.counts))
        print(f"[未通过] {type(error).__name__}", flush=True)
        return 2
    finally:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[报告] {args.report}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
