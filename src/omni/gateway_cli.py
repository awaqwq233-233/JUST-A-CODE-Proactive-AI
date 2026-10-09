"""M1 Gateway 入口：终端设备使用须显式许可；GUI 默认许可，点击启动语音才开启设备。"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import queue
import sys
import threading

import numpy as np

from .gateway_client import GatewayClient, GatewayCallbacks, ROOT
from .media import request_camera_permission
from . import realtime_protocol as protocol


class ConsoleCallbacks(GatewayCallbacks):
    """显示连接状态和实时回复；统计报告不包含对话原文。"""

    def on_state(self, state, info=None):
        """显示设备暂停的重连阶段，防止用户误以为仍在采集。"""
        print(f"\n[Gateway] {state}" + ("（采集暂停）" if state == "reconnecting" else ""), flush=True)

    def on_text_delta(self, text):
        """把模型回复显示在终端，不写入统计报告。"""
        print(text, end="", flush=True)

    def on_user_transcript(self, text):
        """只在当前终端显示用户原话，不写入运行统计。"""
        print(f"\n[bo s s] {text}", flush=True)

    def on_task_event(self, state, detail):
        """显示任务状态与实际文件，不模拟原生播报。"""
        print(f"\n[大脑] {state}" + (f"：{detail['path']}" if state == "completed" else f"：{detail.get('code', '')}"), flush=True)


class ReplayDevices:
    """只发送指定的本地文件样本，接收音频但不播放，也不打开摄像头。"""

    def __init__(self, samples, jpeg=None, **kwargs):
        """建立纯内存文件源，接口与设备适配层一致。"""
        self.samples, self.jpeg = samples, jpeg
        self.index = 0
        self.audio = self
        self.camera_ready = threading.Event()
        self.camera_ready.set()
        self.playback = queue.Queue()
        self.pending_output = np.zeros(0, dtype="<f4")
        self.error = None
        self.played_samples = self.input_status_events = self.output_status_events = 0
        self.audio_queue_max = self.playback_queue_max = 0
        self.captured_frames = int(jpeg is not None)
        self.mic_rms_current = 0.0
        self.audio_tap = None
        self.tap_on_send = True

    def get(self, *args):
        """顺序回放文件并补尾部静音，固定一秒节拍由客户端管理。"""
        chunk = np.zeros(16000, dtype="<f4")
        start = self.index * 16000
        source = self.samples[start:start + 16000]
        chunk[:len(source)] = source
        self.index += 1
        return chunk

    def start(self):
        """文件源无需打开任何硬件。"""

    def finish_input(self):
        """文件源没有后台采集流。"""

    def stop(self):
        """文件源没有待回收的设备或线程。"""
        return True

    def latest_video(self):
        """返回已指定的静态 JPEG，不采集用户环境。"""
        return self.jpeg

    def latest_frame(self):
        """文件验证不提供真实摄像头预览。"""
        return None

    def enqueue_output(self, samples):
        """文件验证只校验原生音频，不冒充实际播放。"""

    def enqueue_task_output(self, samples, cancellation):
        """文件探针校验任务音频归属，不打开扬声器或冒充实际播放。"""


def main(argv=None) -> int:
    """解析独立 Gateway 参数，保存无原始媒体的 M1 运行报告。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--consent-devices", action="store_true", help="同意使用设备且已戴耳机")
    parser.add_argument("--gui", action="store_true", help="打开 Gateway GUI，设备默认许可，点击启动语音才开启")
    parser.add_argument("--url", default="ws://127.0.0.1:8006")
    parser.add_argument("--input-device", type=int)
    parser.add_argument("--output-device", type=int)
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--no-video", action="store_true")
    parser.add_argument("--fps", type=int, default=5, choices=range(5, 11), help="相机采集帧率，上行仍每秒一帧")
    parser.add_argument("--mic-gain", type=float, default=1.0)
    parser.add_argument("--retry-limit", type=int, default=3)
    parser.add_argument("--session-seconds", type=int, default=240)
    parser.add_argument("--sessions", type=int, help="限制会话数；默认持续运行，Ctrl+C 停止")
    parser.add_argument("--input-file", type=Path, help="文件验证，不打开真实设备，不播放")
    parser.add_argument("--image-file", type=Path)
    parser.add_argument("--voice", type=Path, default=ROOT / "voices/silverwalf_voice.wav")
    parser.add_argument("--report", type=Path, default=ROOT / "output/m1/gateway.json")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--transcription", dest="transcription", action="store_true", help="开启本地转写/系统与联网查询，文件验证需显式开启")
    group.add_argument("--no-transcription", dest="transcription", action="store_false", help="仅听看说，关闭转写/任务")
    parser.set_defaults(transcription=None)
    from .transcription_worker import DEFAULT_MODEL_DIR
    import os
    parser.add_argument("--whisper-model-dir", type=Path, default=Path(os.environ.get("JAC_WHISPER_MODEL_DIR", str(DEFAULT_MODEL_DIR))))
    parser.add_argument("--brain-url", default=os.environ.get("JAC_BRAIN_URL", "http://127.0.0.1:12345"))
    args = parser.parse_args(argv)
    if sys.version_info[:2] != (3, 11):
        parser.error("方案 B Gateway 入口必须使用 Python 3.11")
    if not 5 <= args.session_seconds <= 240 or not 0 <= args.retry_limit <= 10:
        parser.error("会话须为 5–240 秒，重连次数须为 0–10")
    if not np.isfinite(args.mic_gain) or not .1 <= args.mic_gain <= 8:
        parser.error("麦克风增益须为 0.1–8")
    if args.gui:
        if args.input_file or args.image_file:
            parser.error("GUI 模式不接受文件回放参数")
        from gui import run_gui
        from src.utils.config import Config
        config = Config.load()
        config.omni_backend = "gateway"
        config.omni_enabled = True
        config.omni_fps = args.fps
        config.omni_mic_gain = args.mic_gain
        config.gateway_retry_limit = args.retry_limit
        config.gateway_url = args.url
        config.gateway_session_seconds = args.session_seconds
        config.gateway_consent_devices = args.consent_devices
        config.gateway_input_device = args.input_device
        config.gateway_output_device = args.output_device
        config.gateway_camera = args.camera
        config.omni_video_enabled = not args.no_video
        config.omni_ref_audio = str(args.voice)
        if args.transcription is not None:
            config.gateway_transcription_enabled = args.transcription
        config.whisper_model_dir = str(args.whisper_model_dir)
        config.gateway_brain_url = args.brain_url
        run_gui(config)
        return 0
    if args.sessions is not None and args.sessions <= 0:
        parser.error("会话数必须大于零")
    if args.input_file is None and not args.consent_devices:
        parser.error("真实设备运行须明确 --consent-devices 并戴好耳机")
    if args.image_file and args.input_file is None:
        parser.error("--image-file 仅用于文件验证")
    kwargs = {}
    if args.input_file:
        samples = np.concatenate((np.zeros(4 * 16000, dtype="<f4"), protocol.load_audio(args.input_file)))
        if len(samples) > args.session_seconds * 16000:
            parser.error("会话时长不足以包含 4 秒启动保护与完整输入文件")
        jpeg = args.image_file.read_bytes() if args.image_file else None

        def replay_factory(**options):
            """每个文件会话使用指定的测试文件，避免意外访问设备。"""
            return ReplayDevices(samples, jpeg, **options)

        kwargs["device_factory"] = replay_factory
        video_enabled = jpeg is not None
    else:
        video_enabled = not args.no_video
        if video_enabled:
            request_camera_permission(args.camera)
    client = GatewayClient(url=args.url, ref_audio_path=args.voice, callbacks=ConsoleCallbacks(),
                           consent_devices=True, input_device=args.input_device,
                           output_device=args.output_device, camera=args.camera,
                           video_enabled=video_enabled, session_seconds=args.session_seconds,
                           video_fps=args.fps, mic_gain=args.mic_gain, retry_limit=args.retry_limit,
                           transcription_enabled=args.transcription if args.transcription is not None else (
                               args.input_file is None and os.environ.get("JAC_TRANSCRIPTION_ENABLED", "1").lower() not in {"0", "false", "no", "off"}),
                           whisper_model_dir=args.whisper_model_dir, brain_url=args.brain_url, **kwargs)
    outcome, error_type = "completed", None
    try:
        asyncio.run(client.run(max_sessions=args.sessions))
    except KeyboardInterrupt:
        outcome = "user_stopped"
    except Exception as error:
        outcome, error_type = "failed", type(error).__name__
        print(f"\n[Gateway] 运行失败：{error_type}", flush=True)
    report = {"outcome": outcome, "error_type": error_type, "stats": client.stats(),
              "live_devices": args.input_file is None, "media_recorded": False,
              "m0_acceptance": "user_approved_30min_waived"}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n[Gateway] 统计报告：{args.report}")
    return 1 if outcome == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
