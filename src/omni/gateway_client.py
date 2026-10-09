"""方案 B 生产客户端：固定 Gateway 协议、原生音频与受控会话重建。"""

from __future__ import annotations

import asyncio
from collections import Counter, deque
import json
from pathlib import Path
import threading
import time

import numpy as np
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from . import realtime_protocol as protocol
from .media import LiveDevices

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROMPT = (
    "你是 J.A.C. 本地私人助手，称呼用户为 bo s s。请自然地用简体中文回应听到的话，"
    "根据实时画面如实描述环境。没听清就请用户重复，不编造任务或已执行的操作。"
    "用户问你看到了什么、你看见什么时，请直接描述当前画面中的主要人物或物品，"
    "不要等待用户再说描述一下；画面不清楚时说明看不清，不推测身份或细节。"
    "当前阶段只提供听、看和语音交流，电脑工具及云端任务尚未接入。"
)
TASK_PROMPT = (
    "本地大脑已支持查询本机时间、电池、CPU、内存，以及明确的联网搜索并保存来源报告。"
    "这些明确指令由本地转写旁路交给大脑执行。遇到此类请求请简短说明交给大脑处理，"
    "不要猜测状态数字，不声称已经完成。实际结果由程序通过原生语音播报，完整内容保留在界面和文件。"
    "不要输出内部控制令牌。其他问题自然回应。"
)


class GatewayCallbacks:
    """轻量回调契约；重工作由调用者转交后台线程。"""

    def on_state(self, state, info=None):
        """接收 connecting/ready/reconnecting/closed/error 状态。"""

    def on_text_delta(self, text):
        """接收独立的文本增量。"""

    def on_listen(self):
        """接收模型聆听状态，不把它当成上行节拍控制。"""

    def on_audio_chunk(self, pcm_bytes):
        """接收原生 24k mono float32 音频已入播放队列的通知。"""

    def on_error(self, error):
        """接收异常类型或经校验的协议错误。"""

    def on_user_transcript(self, text):
        """接收完整、通过置信度校验且没有播放重叠的用户转写。"""

    def on_task_event(self, state, detail):
        """接收大脑任务状态；完成事件含真实本机报告路径。"""


class GatewayClient:
    """支持 asyncio 或线程启动；重连期间明确暂停设备并恢复有限上下文。"""

    def __init__(self, *, url="ws://127.0.0.1:8006", ref_audio_path=None,
                 system_prompt=DEFAULT_PROMPT, callbacks=None, consent_devices=False,
                 input_device=None, output_device=None, camera=0, video_fps=5,
                 video_enabled=True, mic_gain=1.0, session_seconds=240,
                 context_provider=None, device_factory=LiveDevices, retry_limit=3, drain_seconds=8,
                 transcription_enabled=False, whisper_model_dir=None,
                 brain_url="http://127.0.0.1:12345", pipeline_factory=None):
        """仅配置客户端，不打开设备；默认在视频会话上限前 60 秒主动重建。"""
        self.url = protocol.gateway_url(url, "video")
        if not 5 <= session_seconds <= 240 or retry_limit < 0:
            raise ValueError("会话轮换须为 5–240 秒，重试次数不能为负数")
        if not 0 <= drain_seconds <= 10:
            raise ValueError("尾部接收窗口须为 0–10 秒")
        self.ref_audio_path = Path(ref_audio_path or ROOT / "voices/silverwalf_voice.wav")
        self.system_prompt, self.context_provider = system_prompt, context_provider
        if transcription_enabled and system_prompt == DEFAULT_PROMPT:
            self.system_prompt = DEFAULT_PROMPT.replace(
                "当前阶段只提供听、看和语音交流，电脑工具及云端任务尚未接入。", TASK_PROMPT)
        self.transcription_enabled, self.whisper_model_dir = transcription_enabled, whisper_model_dir
        self.brain_url, self.pipeline_factory = brain_url, pipeline_factory
        self._pipeline = None
        self._speech = None
        self._listening = False
        self._last_assistant_output = 0.0
        self.callbacks = callbacks or GatewayCallbacks()
        self.consent_devices = consent_devices
        self.device_kwargs = dict(input_device=input_device, output_device=output_device,
                                  camera=camera, fps=video_fps, mic_gain=mic_gain,
                                  video_enabled=video_enabled)
        self.device_factory, self.session_seconds, self.retry_limit = device_factory, session_seconds, retry_limit
        self.drain_seconds = drain_seconds
        self._devices = None
        self._thread = self._loop = self._task = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._reply = self._pending_text = ""
        self._recent = deque(maxlen=8)
        self._costs = deque(maxlen=3000)
        self._stats = Counter()
        self.last_error = None

    async def _close_session(self, ws, receiver, reason) -> None:
        """保留唯一接收器直到关闭确认，避免提前断链令 Worker 收尾与重启冲突。"""
        try:
            if not receiver.done():
                await ws.send(json.dumps({"type": "session.close", "reason": reason}))
            await asyncio.wait_for(asyncio.shield(receiver), 35)
            # 固定版本会先发关闭事件再收尾；WS 关闭并不等于 C++ 已完全释放。
            await asyncio.wait_for(ws.wait_closed(), 5)
            await asyncio.sleep(1)
        except (ConnectionClosed, OSError, TimeoutError):
            with self._lock:
                self._stats["close_handshake_failures"] += 1
            raise
        with self._lock:
            self._stats["close_handshakes"] += 1

    async def _init_message(self, voice) -> dict:
        """每次建会话重新注入用户提供的上下文和有界助手历史，不伪造用户转写。"""
        extra = await asyncio.to_thread(self.context_provider) if self.context_provider else ""
        recent = "\n".join(self._recent)[-2000:]
        prompt = self.system_prompt
        if extra:
            prompt += "\n用户档案与已确认上下文：\n" + str(extra)[-2000:]
        if recent:
            prompt += "\n近期助手输出（仅助手历史，不是用户原话）：\n" + recent
        return protocol.build_init(prompt, voice)

    def _remember_text(self) -> None:
        """将已接收的助手文本放入内存环形缓冲，不保存音视频或推测用户原话。"""
        if self._pending_text.strip():
            self._recent.append(self._pending_text[-2000:])
        self._pending_text = ""

    def _deliver_output(self, kind, value, devices, guard=None):
        """只交付已放行普通回复或已核验任务结果，取消块不进入播放队列。"""
        if self._stop.is_set() or (guard is not None and guard.is_set()):
            return
        if kind == "text":
            self._pending_text = (self._pending_text + value)[-2000:]
            with self._lock:
                self._reply = (self._reply + value)[-8192:]
            self.callbacks.on_text_delta(value)
        else:
            if guard is not None:
                devices.enqueue_task_output(value, guard)
            else:
                devices.enqueue_output(value)
            with self._lock:
                self._stats["native_audio_samples"] += len(value)
            self.callbacks.on_audio_chunk(value.tobytes())

    async def _receive(self, ws, devices, costs) -> str:
        """独立收取文本和原生音频；忽略视频双工中非可靠的 response.done。"""
        from .response_gate import ResponseGate
        gate = ResponseGate(self._pipeline, self.callbacks) if self._pipeline is not None else None
        gate_counts = Counter()
        while True:
            if gate is not None:
                for kind, value, guard in gate.poll():
                    self._deliver_output(kind, value, devices, guard)
                with self._lock:
                    self._stats.update({"ordinary_" + key: value - gate_counts[key]
                                        for key, value in gate.counters.items()})
                gate_counts = gate.counters.copy()
            try:
                event = protocol.check_event(await asyncio.wait_for(ws.recv(), .1))
            except TimeoutError:
                continue
            if event["type"] == "session.closed":
                self._remember_text()
                return event.get("reason", "unknown")
            if event["type"] == "task.speech":
                if self._speech is not None and not self._stop.is_set():
                    self._speech.event(event)
                continue
            if event["type"] != "response.output.delta":
                continue
            kind = event.get("kind")
            if kind == "listen":
                self._listening = True
                self._remember_text()
                self.callbacks.on_listen()
            elif kind == "text":
                text = event.get("text")
                if not isinstance(text, str):
                    raise ValueError("文本增量缺少 text")
                speech_id = event.get("task_speech_id")
                if speech_id is not None:
                    if self._speech is None or not self._speech.text_delta(speech_id, text):
                        continue
                    self._deliver_output("text", text, devices)
                else:
                    self._listening = False
                    self._last_assistant_output = time.monotonic()
                    for kind, value, guard in gate.offer("text", text) if gate else [("text", text, None)]:
                        self._deliver_output(kind, value, devices, guard)
            elif kind == "audio":
                samples = protocol.decode_pcm(event["audio"])
                speech_id = event.get("task_speech_id")
                guard = None
                if speech_id is not None:
                    guard = self._speech.audio(speech_id, samples) if self._speech is not None else None
                    if guard is None:
                        continue
                else:
                    self._listening = False
                    self._last_assistant_output = time.monotonic()
                outputs = [("audio", samples, guard)] if speech_id is not None or gate is None else gate.offer("audio", samples)
                for kind, value, guard in outputs:
                    self._deliver_output(kind, value, devices, guard)
            else:
                raise ValueError("未知输出类型")
            metrics = event.get("metrics") or {}
            cost = metrics.get("cost_all_ms", metrics.get("wall_clock_ms"))
            key = event.get("input_id") or event.get("response_id") or metrics.get("chunk_index")
            if isinstance(cost, (int, float)) and np.isfinite(cost) and cost > 0 and key is not None:
                costs[str(key)] = max(float(cost), costs.get(str(key), 0))

    async def _session(self, voice) -> None:
        """等待排队与初始化后启动设备，固定一秒推流，在轮换前收尾并清理。"""
        devices = self.device_factory(**self.device_kwargs)
        self._devices = devices
        costs = {}
        try:
            async with connect(self.url, proxy=None, max_size=32 * 1024 * 1024,
                               open_timeout=20, close_timeout=5) as ws:
                receiver = None
                initialized = closed = False
                try:
                    events = Counter()
                    await protocol.receive_until(ws, "session.queue_done", 120, events)
                    init = await self._init_message(voice)
                    await ws.send(json.dumps(init, ensure_ascii=False))
                    initialized = True
                    created = await protocol.receive_until(ws, "session.created", 120, events)
                    if created.get("mode") != "full_duplex" or not created.get("session_id"):
                        raise ValueError("Gateway 未创建有效全双工会话")
                    protocol.require_voice_condition(created, init)
                    if self._speech is not None:
                        protocol.require_task_speech(created)
                    self._listening = False
                    self._last_assistant_output = time.monotonic()
                    if self._pipeline is not None:
                        self._pipeline.begin_session()
                        devices.audio_tap = self._pipeline.offer_audio
                    startup = asyncio.create_task(asyncio.to_thread(devices.start))
                    try:
                        await asyncio.shield(startup)
                    except asyncio.CancelledError:
                        # to_thread 不能抢占设备启动，等它返回后再完整释放。
                        await startup
                        raise
                    await asyncio.to_thread(devices.camera_ready.wait, 8)
                    if devices.error or (self.device_kwargs["video_enabled"] and not devices.captured_frames):
                        raise RuntimeError(devices.error or "摄像头无画面")
                    with self._lock:
                        self._stats["sessions_started"] += 1
                    receiver = asyncio.create_task(self._receive(ws, devices, costs))
                    self.callbacks.on_state("ready")
                    self._ready.set()
                    stream_start = None
                    for index in range(self.session_seconds):
                        chunk = await asyncio.to_thread(devices.audio.get, True, 2)
                        if stream_start is None:
                            stream_start = time.monotonic()
                        await asyncio.sleep(max(0, stream_start + index - time.monotonic()))
                        if getattr(devices, "tap_on_send", False) and self._pipeline is not None:
                            self._pipeline.offer_audio(chunk, False)
                        if devices.error:
                            raise RuntimeError(devices.error)
                        if receiver.done():
                            reason = await receiver
                            if reason == "timeout":
                                break
                            raise RuntimeError("Gateway 意外关闭会话")
                        message = protocol.build_input(chunk, devices.latest_video())
                        # 保留真实音频；启动保护仅要求模型暂时聆听，不把用户音频换成静音。
                        if index < 4:
                            message["input"]["force_listen"] = True
                        if self._speech is not None and index >= 4:
                            idle = (self._listening and time.monotonic() - self._last_assistant_output >= .3
                                    and devices.playback.empty() and not len(devices.pending_output)
                                    and time.monotonic() >= getattr(devices, "playback_active_until", 0))
                            speech = self._speech.next_input(idle, self.session_seconds - index)
                            if speech is not None and not self._stop.is_set():
                                message["input"]["task_speech"] = speech
                        await ws.send(json.dumps(message))
                        with self._lock:
                            self._stats["chunks_sent"] += 1
                            self._stats["send_lateness_max_ms"] = max(
                                self._stats["send_lateness_max_ms"],
                                (time.monotonic() - stream_start - index) * 1000)
                    if self._pipeline is not None:
                        self._pipeline.invalidate()
                    self.callbacks.on_state("reconnecting", {"capture_paused": True})
                    await asyncio.to_thread(devices.finish_input)
                    if not receiver.done():
                        await asyncio.wait({receiver}, timeout=self.drain_seconds)
                    if receiver.done():
                        if await receiver != "timeout":
                            raise RuntimeError("Gateway 在主动关闭前意外结束")
                    await self._close_session(ws, receiver, "session_rotation")
                    closed = True
                    deadline = time.monotonic() + 5
                    while (not devices.playback.empty() or len(devices.pending_output)) and time.monotonic() < deadline:
                        await asyncio.sleep(0.1)
                    with self._lock:
                        self._stats["sessions_completed"] += 1
                finally:
                    if self._pipeline is not None:
                        self._pipeline.invalidate()
                    try:
                        if initialized and not closed:
                            # 即使停在 init/设备启动阶段，也必须走关闭握手。
                            if receiver is None:
                                receiver = asyncio.create_task(self._receive(ws, devices, costs))
                            if not receiver.done():
                                await asyncio.to_thread(devices.finish_input)
                                # 避免在 C++ 同步 decode 正在用栈请求时强制打断；
                                # 先停采集并保留接收尾窗，与正常会话轮换采用同样的收尾。
                                await asyncio.wait({receiver}, timeout=self.drain_seconds)
                                try:
                                    await self._close_session(ws, receiver, "client_stop")
                                except (ConnectionClosed, OSError):
                                    # 已断网时无法取得确认；保留触发收尾的原始错误。
                                    pass
                    finally:
                        if receiver is not None:
                            if not receiver.done():
                                receiver.cancel()
                            await asyncio.gather(receiver, return_exceptions=True)
        finally:
            if self._pipeline is not None:
                self._pipeline.invalidate()
            cleaned = await asyncio.to_thread(devices.stop)
            self._remember_text()
            with self._lock:
                self._stats["played_audio_samples"] += devices.played_samples
                self._stats["input_status_events"] += devices.input_status_events
                self._stats["output_status_events"] += devices.output_status_events
                self._stats["camera_frames"] += devices.captured_frames
                self._stats["audio_queue_max"] = max(self._stats["audio_queue_max"], devices.audio_queue_max)
                self._stats["playback_queue_max"] = max(self._stats["playback_queue_max"], devices.playback_queue_max)
                self._stats["cleanup_failures"] += not cleaned
                self._costs.extend(costs.values())
            self._devices = None
            if not cleaned:
                raise RuntimeError("采集线程未完整退出")

    async def run(self, *, max_sessions=None) -> None:
        """拥有旁路完整生命周期；CPU 就绪后才允许开启生产采集。"""
        if not self.consent_devices:
            raise ValueError("须明确同意使用摄像头、麦克风并戴好耳机")
        pending = None
        try:
            if self.transcription_enabled:
                from .task_pipeline import TaskPipeline, DEFAULT_MODEL_DIR
                factory = self.pipeline_factory or TaskPipeline
                self._pipeline = factory(self.callbacks, self.whisper_model_dir or DEFAULT_MODEL_DIR, self.brain_url)
                from .task_speech import TaskSpeechQueue
                self._speech = TaskSpeechQueue(self._pipeline, self.callbacks)
                self._pipeline.on_result_speech = self._speech.offer
                self._pipeline.on_invalidate = self._speech.cancel
                pending = asyncio.create_task(asyncio.to_thread(self._pipeline.start))
                try:
                    await asyncio.shield(pending)
                except asyncio.CancelledError:
                    self._pipeline.request_stop()
                    await asyncio.gather(pending, return_exceptions=True)
                    raise
            await self._run_sessions(max_sessions=max_sessions)
        finally:
            if self._pipeline is not None:
                self._pipeline.request_stop()
                await asyncio.to_thread(self._pipeline.stop)
                counters = self._pipeline.stats()
                if self._speech is not None:
                    speech_counters = self._speech.stats()
                    with self._lock:
                        self._stats.update({"task_speech_" + key: value for key, value in speech_counters.items()})
                    self._speech = None
                with self._lock:
                    for key, value in counters.items():
                        self._stats["task_" + key] += value
                self._pipeline = None

    async def _run_sessions(self, *, max_sessions=None) -> None:
        """运行固定协议会话，仅对网络错误限次退避重试，停止请求不会重连。"""
        if not self.consent_devices:
            raise ValueError("须明确同意使用摄像头、麦克风并戴好耳机")
        voice = await asyncio.to_thread(protocol.load_audio, self.ref_audio_path)
        failures = completed = 0
        while not self._stop.is_set() and (max_sessions is None or completed < max_sessions):
            self.callbacks.on_state("connecting" if completed == 0 else "reconnecting")
            try:
                await self._session(voice)
                completed += 1
                failures = 0
            except (ConnectionClosed, OSError, TimeoutError):
                failures += 1
                if failures > self.retry_limit or self._stop.is_set():
                    raise
                self.callbacks.on_state("reconnecting", {"attempt": failures, "capture_paused": True})
                await asyncio.sleep(min(2 ** (failures - 1), 4))
        self.callbacks.on_state("closed")

    def start(self, timeout=180) -> bool:
        """在专用 asyncio 线程启动并等待 ready；超时完整停止，供 GUI 运行时调用。"""
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                if self._stop.is_set():
                    raise RuntimeError("Gateway 上一次停止尚未完成，请等待关闭确认")
            else:
                self._stop.clear()
                self._ready.clear()
                self.last_error = None
                self._thread = threading.Thread(target=self._thread_main, name="jac-gateway", daemon=True)
                self._thread.start()
            worker = self._thread
        deadline = time.monotonic() + timeout
        while not self._ready.wait(0.05):
            if not worker.is_alive() or time.monotonic() >= deadline:
                self.stop()
                return False
        return self.last_error is None and self.is_running()

    def _thread_main(self) -> None:
        """拥有独立事件循环，异常通过回调报告且始终关闭线程状态。"""
        async def owned_run():
            """暴露线程拥有的 task，供跨线程停止安全地取消。"""
            self._loop = asyncio.get_running_loop()
            self._task = asyncio.current_task()
            try:
                await self.run()
            except asyncio.CancelledError:
                self.callbacks.on_state("closed")
            except Exception as error:
                self.last_error = type(error).__name__
                if isinstance(error, (RuntimeError, ValueError)):
                    self.last_error += ": " + str(error)[:300]
                self.callbacks.on_error(self.last_error)
                self.callbacks.on_state("error")
            finally:
                self._task = self._loop = None

        asyncio.run(owned_run())

    def stop(self) -> None:
        """协作取消 WS/设备任务并等待线程清理，不停止用户已有后端服务。"""
        with self._lifecycle_lock:
            stopping = self._stop.is_set()
            self._stop.set()
            if self._pipeline is not None:
                self._pipeline.request_stop()
            loop, task, worker = self._loop, self._task, self._thread
            if not stopping and loop is not None and task is not None:
                try:
                    loop.call_soon_threadsafe(task.cancel)
                except RuntimeError:
                    pass
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=50)
            if worker.is_alive():
                raise RuntimeError("Gateway 客户端停止超时")

    def is_running(self) -> bool:
        """返回客户端工作线程是否仍在运行。"""
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()

    def get_latest_frame(self):
        """给 GUI 提供最新后台采集帧，重连暂停时返回空值。"""
        devices = self._devices
        return devices.latest_frame() if devices else None

    def get_latest_mic_level(self) -> float:
        """返回采集音量指标，供 GUI 音量条显示。"""
        devices = self._devices
        return devices.mic_rms_current if devices else 0.0

    def get_reply_text(self) -> str:
        """返回有界助手文本副本，供 GUI 实时记录显示。"""
        with self._lock:
            return self._reply

    def stats(self) -> dict:
        """只返回计数和延迟，不把参考音、对话文本或原始媒体写入报告。"""
        with self._lock:
            result = dict(self._stats)
            result["processing_p95_ms"] = float(np.percentile(self._costs, 95)) if self._costs else None
        pipeline = self._pipeline
        if pipeline is not None:
            result.update({"task_" + key: value for key, value in pipeline.stats().items()})
        speech = self._speech
        if speech is not None:
            result.update({"task_speech_" + key: value for key, value in speech.stats().items()})
        return result
