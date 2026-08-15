"""MiniCPM-o-4_5 全双工 WebSocket 客户端（J.A.C. 的「耳朵 + 眼睛 + 嘴巴」）。

协议契约（来自 llama.cpp-omni master 分支源码坐实 + M0 探针实测）：
  上行 session.init：
    {"type":"session.init","payload":{"mode":"full_duplex","use_tts":true,
     "voice":{"ref_audio":<16k float32 PCM base64>},
     "system_prompt":"..."}}
  上行 input.append（实时推流）：
    {"type":"input.append","input":{"audio_base64":<16k float32 PCM base64>,
                                     "video_frames":[<jpeg base64>]}}
  下行统一：
    {"type":"response.output.delta","kind":"text"|"audio"|"listen","text"/"audio":...}
    {"type":"response.done","text":...,"audio":...}
    {"type":"session.created"/"session.closed",...}

关键约束（M0 实测）：
  - 音频必须是 16kHz 单声道 float32 小端原始字节的 base64。
  - 全双工下必须按「真实实时节奏」喂音频（约 1 秒音频 / 1.0 秒墙钟），
    喂太快模型只 LISTEN 不 SPEAK；喂太慢则延迟增大。
  - 声纹克隆参考音用项目内 voices/silverwalf_voice.wav（44.1k → 重采样 16k）。
"""
import asyncio
import base64
import json
import os
import queue
import threading
import time

import cv2
import numpy as np
import pyaudio
import soundfile as sf
import soxr
import websockets

from src.capture.camera import Camera

# 全双工音频目标采样率（omni 要求 16kHz float32 单声道）
TARGET_SR = 16000


class OmniCallbacks:
    """omni 事件回调（全部可空实现，按需覆盖）。"""

    def on_state(self, state: str, info=None):
        """状态变化：connecting / ready / closed / error。"""

    def on_text_delta(self, text: str):
        """文本增量（流式逐块）。"""

    def on_text_final(self, text: str):
        """一轮完整文本（response.done 时）。"""

    def on_audio_chunk(self, pcm_bytes: bytes):
        """收到 omni 合成语音（16k float32 原始字节），默认交给内置播放器。"""

    def on_listen(self):
        """omni 进入「聆听」决策（用户说话/等待）。"""

    def on_call_qwen(self, task: str):
        """检测到升级令牌 <<CALL_QWEN>>{task}（M2 升级路由触发点）。

        默认空实现：由 runtime 覆盖，转交 qwen+tools 处理并回灌播报。
        """

    def on_mic_level(self, rms: float):
        """麦克风实时音量（RMS，0~1 归一），用于诊断采集是否正常。默认空实现。"""

    def on_error(self, err):
        """异常/错误。"""


class _PyAudioPlayer:
    """低延迟音频播放器：用 PyAudio 输出流 + 回调从队列取数据播放。

    收到的 omni TTS 是 16k float32 原始字节，直接喂输出流即可，无需重采样。
    """

    def __init__(self, rate: int = TARGET_SR, frames_per_buffer: int = 1024):
        """初始化 PyAudio 输出流。"""
        self.p = pyaudio.PyAudio()
        self.rate = rate
        self.q: "queue.Queue[bytes]" = queue.Queue()
        self._stream = self.p.open(
            format=pyaudio.paFloat32,
            channels=1,
            rate=rate,
            output=True,
            frames_per_buffer=frames_per_buffer,
            stream_callback=self._callback,
        )
        self._stream.start_stream()

    def _callback(self, in_data, frame_count, time_info, status):
        """PyAudio 回调：凑齐 frame_count*4 字节返回，不足补静音。"""
        needed = frame_count * 4
        buf = bytearray()
        try:
            while len(buf) < needed:
                chunk = self.q.get_nowait()
                buf.extend(chunk)
        except queue.Empty:
            pass
        if len(buf) < needed:
            buf.extend(b"\x00" * (needed - len(buf)))
        return (bytes(buf), pyaudio.paContinue)

    def play(self, pcm_bytes: bytes):
        """把一段 16k float32 语音字节压入播放队列。"""
        if pcm_bytes:
            self.q.put(pcm_bytes)

    def stop(self):
        """停止并释放音频资源。"""
        try:
            self._stream.stop_stream()
            self._stream.close()
        except Exception:
            pass
        try:
            self.p.terminate()
        except Exception:
            pass


class OmniClient:
    """MiniCPM-o-4_5 全双工客户端。"""

    def __init__(self, url: str, ref_audio_path: str, system_prompt: str,
                 callbacks: OmniCallbacks = None,
                 camera: Camera = None, enable_mic: bool = True,
                 enable_camera: bool = True, enable_playback: bool = True,
                 push_interval: float = 0.4, video_fps: int = 5,
                 camera_width: int = 1280, camera_height: int = 720,
                 video_quality: int = 80):
        """初始化客户端。

        Args:
            url: WS 地址，如 ws://127.0.0.1:9060/backend。
            ref_audio_path: 声纹克隆参考音（wav，任意采样率，会自动重采样 16k）。
            system_prompt: omni 系统提示（含角色与 CALL_QWEN 令牌约定）。
            callbacks: 事件回调（默认空实现，打印到控制台）。
            camera: 复用外部摄像头对象；为 None 且 enable_camera 时自建。
            enable_mic/camera/playback: 采集/播放开关（便于测试时关闭某项）。
            push_interval: 每多少秒把累积音频 + 最新视频帧推一次（≈实时节奏）。
            video_fps: 视频上行帧率（仅影响最新帧刷新频率，与 push 解耦）。
            camera_width/height: 自建摄像头分辨率。
            video_quality: jpeg 编码质量（0~100）。
        """
        _ensure_no_proxy()
        self.url = url
        self.ref_audio_path = ref_audio_path
        self.system_prompt = system_prompt
        self.cb = callbacks or OmniCallbacks()
        self.camera = camera
        self.enable_mic = enable_mic
        self.enable_camera = enable_camera
        self.enable_playback = enable_playback
        self.push_interval = push_interval
        self.video_fps = video_fps
        self.camera_width = camera_width
        self.camera_height = camera_height
        self.video_quality = video_quality

        # 异步运行环境：单个事件循环跑在后台线程，所有 ws 收发都在该循环内串行化
        self._loop = None
        self._thread = None
        self._ws = None
        self._stop_ev = threading.Event()
        self._ready_ev = threading.Event()

        # 采集缓冲（生产者：采集线程；消费者：推送协程）
        self._mic_buf = bytearray()          # 累积的 16k float32 音频字节
        self._mic_lock = threading.Lock()
        self._latest_jpg = None              # 最新一帧 jpeg 字节
        self._latest_jpg_lock = threading.Lock()
        self._latest_frame = None            # 最新一帧 BGR numpy（供 GUI 预览）
        self._latest_frame_lock = threading.Lock()

        # ---- M2 升级路由相关状态 ----
        # 文本累积缓冲（用于检测 <<CALL_QWEN>> 令牌，令牌可能跨多个 delta 分片到达）
        self._text_buf = ""
        self._call_qwen_fired = False        # 本次会话是否已触发过升级（避免重复触发）
        self._pending_task = None            # 令牌已命中但任务描述尚未完整时的临时累积
        self._pending_timer = None           # 令牌后无换行时的兜底触发定时器
        self._ref_audio_b64 = ""             # 克隆声纹 base64（session.init 后存入，供回灌复用）
        self._suppress_audio = False         # 升级期间抑制主会话语音输出（避免与回灌重叠/回声）
        self._escalation_done = False        # 升级任务是否已完成（配合 listen 事件解除静音）
        self._audio_lock = threading.Lock()  # 保护上面的静音/完成标志（跨线程读写）
        # 首个 listen 事件信号：omni 真正进入聆听（模型加载完成）的可靠标志，
        # 用于 start() 等待「真正就绪」，避免自动启动时模型还在后台加载就误报就绪。
        self._listen_ev = threading.Event()
        self._dbg_rx = 0  # 调试探针：下行事件计数（确认真机 server 是否下发 listen/text，定位后可移除）

        # 采集资源
        self._pyaudio = None
        self._mic_stream = None
        self._mic_thread = None
        self._cam_thread = None
        self.player = None
        self._owns_camera = False            # 是否由本客户端自建摄像头（stop 时需释放）

    # ============================================================ 对外控制
    def start(self, timeout: float = 180.0) -> bool:
        """启动全双工会话（阻塞直到 session.created 或超时）。

        Args:
            timeout: 等待模型加载 / 会话建立的最长秒数。

        Returns:
            bool: 成功就绪返回 True。
        """
        if self._thread and self._thread.is_alive():
            return True
        self._stop_ev.clear()
        self._ready_ev.clear()
        self.cb.on_state("connecting")
        self._thread = threading.Thread(target=self._run, name="omni-client", daemon=True)
        self._thread.start()

        # 等待进度提示：自动启动时模型加载可能耗时 10~60s，期间 GPU 满载属正常，
        # 用周期性提示避免用户误以为程序卡死（之前多次被误判为「没反应」）。
        import time as _t
        _deadline = _t.time() + max(timeout, 120) + 10

        def _wait_progress():
            waited = 0
            while _t.time() < _deadline:
                if not self._thread.is_alive():
                    return
                if self._listen_ev.is_set():
                    return
                _t.sleep(10)
                waited += 10
                if self._listen_ev.is_set():
                    return
                phase = ("等待模型加载完成（GPU 满载属正常）" if not self._ready_ev.is_set()
                         else "等待 omni 进入聆听（模型已加载，即将出现🎧）")
                print(f"[omni] ⏳ 已等待 {waited}s：{phase}…", flush=True)

        threading.Thread(target=_wait_progress, daemon=True, name="omni-wait-progress").start()

        if not self._ready_ev.wait(timeout=timeout):
            self.cb.on_error("等待 session.created 超时（模型加载可能过慢或服务未启动）")
            return False
        # session.created 仅代表会话建立，不代表模型已加载完（llama-omni-server 先开端口
        # 再后台加载权重）。继续等待首个 listen 事件作为「真正可对话」的信号，避免
        # 自动启动时在模型仍在加载就误报「全双工已就绪」误导用户。
        # 超时则降级返回 True（可能是无 listen 的特殊场景），但给出警告便于排障。
        if not self._listen_ev.wait(timeout=120):
            self.cb.on_error("等待 omni 进入聆听超时（模型加载可能过慢，或服务端未推 listen 事件）")
        return True

    def stop(self):
        """停止会话并释放所有资源。"""
        self._stop_ev.set()
        # 取消可能还在等待的令牌兜底定时器
        if self._pending_timer is not None and self._pending_timer.is_alive():
            try:
                self._pending_timer.cancel()
            except Exception:  # noqa: BLE001
                pass
            self._pending_timer = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        if self.player is not None:
            try:
                self.player.stop()
            except Exception:
                pass
            self.player = None
        # 释放自建摄像头
        if self._owns_camera and self.camera is not None:
            try:
                self.camera.stop()
            except Exception:
                pass
            self.camera = None
            self._owns_camera = False
        print("[omni-client] 已停止。")

    def is_running(self) -> bool:
        """是否仍在运行。"""
        return self._thread is not None and self._thread.is_alive()

    def get_latest_frame(self):
        """返回最新摄像头帧（BGR numpy），无则返回 None（供 GUI 预览）。"""
        with self._latest_frame_lock:
            return self._latest_frame

    # ============================================================ 后台主循环
    def _run(self):
        """后台线程入口：建事件循环并跑异步主流程。"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._async_main())
        except Exception as e:  # noqa: BLE001
            self.cb.on_error(e)
        finally:
            try:
                loop.close()
            except Exception:
                pass

    async def _async_main(self):
        """异步主流程：连接 → session.init → 等待 created → 启动采集 → 收发。"""
        # 1) 声纹参考音（一次性加载 + 重采样）
        try:
            ref_b64 = self._load_ref_audio()
        except Exception as e:  # noqa: BLE001
            self.cb.on_error(f"声纹参考音加载失败: {e}")
            return
        # 存入实例，供 M2 回灌通道（临时 turn_based 会话）复用同一克隆声纹
        self._ref_audio_b64 = ref_b64

        try:
            async with websockets.connect(
                self.url, max_size=None, open_timeout=30,
                ping_interval=20, ping_timeout=20,
            ) as ws:
                self._ws = ws
                # 2) session.init（full_duplex + 声纹克隆 + 系统提示）
                init_msg = {
                    "type": "session.init",
                    "payload": {
                        "mode": "full_duplex",
                        "use_tts": True,
                        "voice": {"ref_audio": ref_b64},
                        "system_prompt": self.system_prompt,
                    },
                }
                await ws.send(json.dumps(init_msg))

                # 3) 等待 session.created
                raw = await asyncio.wait_for(ws.recv(), timeout=180)
                ev = json.loads(raw)
                if ev.get("type") != "session.created":
                    self.cb.on_error(f"未收到 session.created，收到: {ev.get('type')}")
                    return
                self.cb.on_state("ready", ev.get("session_id"))
                self._ready_ev.set()

                # 4) 启动采集（麦克风 + 摄像头 + 播放器）
                self._start_capture()

                # 5) 推送协程 + 接收协程并发运行，直到停止或断连
                try:
                    await asyncio.gather(self._push_loop(ws), self._receiver_loop(ws))
                finally:
                    self._stop_capture()
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001
            self.cb.on_error(e)
        finally:
            self._ws = None
            self.cb.on_state("closed")

    # ============================================================ 声纹
    def _load_ref_audio(self) -> str:
        """读取 wav → 单声道 float32 → 重采样到 16k → base64(原始 float32 字节)。"""
        if not os.path.isfile(self.ref_audio_path):
            raise FileNotFoundError(f"声纹参考音不存在: {self.ref_audio_path}")
        data, sr = sf.read(self.ref_audio_path, dtype="float32", always_2d=True)
        mono = data.mean(axis=1)                       # 转单声道
        if sr != TARGET_SR:
            mono = soxr.resample(mono, sr, TARGET_SR)  # 重采样到目标采样率
        mono = np.clip(mono, -1.0, 1.0).astype(np.float32)
        return base64.b64encode(mono.tobytes()).decode("ascii")

    # ============================================================ 采集
    def _start_capture(self):
        """启动麦克风 / 摄像头采集线程与播放器。"""
        if self.enable_mic:
            try:
                self._pyaudio = pyaudio.PyAudio()
                self._mic_stream = self._pyaudio.open(
                    format=pyaudio.paFloat32,
                    channels=1,
                    rate=TARGET_SR,
                    input=True,
                    frames_per_buffer=1024,
                )
                self._mic_stream.start_stream()
                self._mic_thread = threading.Thread(target=self._mic_loop, daemon=True)
                self._mic_thread.start()
            except Exception as e:  # noqa: BLE001
                self.cb.on_error(f"麦克风采集启动失败（OMNI 模式将无语音输入）: {e}")
                self.enable_mic = False

        if self.enable_camera:
            if self.camera is None:
                self.camera = Camera(width=self.camera_width, height=self.camera_height)
                if self.camera.start():
                    self._owns_camera = True
                else:
                    self.camera = None
                    self.enable_camera = False
            if self.camera is not None:
                self._cam_thread = threading.Thread(target=self._cam_loop, daemon=True)
                self._cam_thread.start()

        if self.enable_playback:
            try:
                self.player = _PyAudioPlayer(rate=TARGET_SR)
            except Exception as e:  # noqa: BLE001
                self.cb.on_error(f"播放器启动失败（OMNI 语音将不播放）: {e}")
                self.player = None

    def _stop_capture(self):
        """停止采集线程与流。"""
        if self._mic_stream is not None:
            try:
                self._mic_stream.stop_stream()
                self._mic_stream.close()
            except Exception:
                pass
            self._mic_stream = None
        if self._pyaudio is not None:
            try:
                self._pyaudio.terminate()
            except Exception:
                pass
            self._pyaudio = None
        if self._mic_thread is not None:
            self._mic_thread = None
        if self._cam_thread is not None:
            self._cam_thread = None

    def _mic_loop(self):
        """麦克风采集循环：持续读取 float32 音频累加到缓冲。"""
        while not self._stop_ev.is_set() and self._mic_stream is not None \
                and self._mic_stream.is_active():
            try:
                data = self._mic_stream.read(1024, exception_on_overflow=False)
            except Exception:  # noqa: BLE001
                break
            if data:
                with self._mic_lock:
                    self._mic_buf.extend(data)

    def _cam_loop(self):
        """摄像头采集循环：按 video_fps 刷新最新帧（jpeg），由推送协程取用。"""
        interval = 1.0 / max(1, self.video_fps)
        while not self._stop_ev.is_set() and self.camera is not None:
            ret, frame = self.camera.get_frame()
            if ret and frame is not None:
                ok, buf = cv2.imencode(
                    ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.video_quality]
                )
                if ok:
                    with self._latest_jpg_lock:
                        self._latest_jpg = buf.tobytes()
                # 缓存 BGR 帧供 GUI 预览（解码一次，避免高频重复解码）
                with self._latest_frame_lock:
                    self._latest_frame = frame.copy()
            time.sleep(interval)

    # ============================================================ 收发
    async def _push_loop(self, ws):
        """实时推流循环：每隔 push_interval 把累积音频 + 最新帧打包发送。

        关键点：累积的音频量 ≈ push_interval 秒的真实录音，保证按实时节奏喂给模型，
        避免全双工下「只听不说」（M0 实测踩坑）。同时回报麦克风 RMS 用于诊断，
        持续静音（RMS≈0）时周期警告，帮助排查「用户说话但 omni 无反应」这类问题。
        """
        silent_secs = 0.0
        while not self._stop_ev.is_set():
            await asyncio.sleep(self.push_interval)

            with self._mic_lock:
                audio = bytes(self._mic_buf)
                self._mic_buf = bytearray()

            # 计算麦克风音量（RMS），用于诊断采集是否正常
            if audio:
                arr = np.frombuffer(audio, dtype=np.float32)
                rms = float(np.sqrt(np.mean(arr * arr))) if arr.size else 0.0
            else:
                rms = 0.0
            self.cb.on_mic_level(rms)
            if rms <= 1e-4:
                silent_secs += self.push_interval
                if silent_secs >= 5.0:
                    print("[omni-client] ⚠️ 持续未检测到麦克风音频（RMS≈0）："
                          "请检查 macOS 麦克风权限（系统设置→隐私与安全→麦克风）"
                          "以及运行 python 的终端/IDE 是否被授权；也可能是默认输入设备选错。",
                          flush=True)
                    silent_secs = 0.0
            else:
                silent_secs = 0.0

            payload = {}
            if audio:
                payload["audio_base64"] = base64.b64encode(audio).decode("ascii")
            with self._latest_jpg_lock:
                jpg = self._latest_jpg
            if jpg is not None:
                payload["video_frames"] = [base64.b64encode(jpg).decode("ascii")]

            if payload:
                try:
                    await ws.send(json.dumps({"type": "input.append", "input": payload}))
                except Exception as e:  # noqa: BLE001
                    self.cb.on_error(f"推送失败: {e}")
                    break

    async def _receiver_loop(self, ws):
        """接收循环：解析下行事件，广播给回调 / 播放器。"""
        try:
            async for raw in ws:
                try:
                    e = json.loads(raw)
                except Exception:
                    continue
                et = e.get("type", "")
                # 调试探针：打印下行事件（跳过 audio 刷屏，前 40 条），确认真机 server 是否下发 listen/text
                _k = e.get("kind", "")
                if _k != "audio" and self._dbg_rx < 40:
                    self._dbg_rx += 1
                    _snip = (e.get("text", "") or "")[:30] if _k == "text" else ""
                    print(f"[omni-dbg] 下行#{self._dbg_rx}: type={et} kind={_k} {_snip}", flush=True)
                if et == "response.output.delta":
                    kind = e.get("kind", "")
                    if kind == "text":
                        txt = e.get("text", "")
                        if txt:
                            self._on_text(txt)
                    elif kind == "audio":
                        ab = e.get("audio") or ""
                        if ab:
                            self._emit_audio(base64.b64decode(ab))
                    elif kind == "listen":
                        self._listen_ev.set()  # 标记 omni 已真正进入聆听（模型就绪）
                        self.cb.on_listen()
                        # 升级已完成且主会话回到聆听态 → 解除静音，恢复正常播报
                        with self._audio_lock:
                            if self._suppress_audio and self._escalation_done:
                                self._suppress_audio = False
                elif et == "response.done":
                    txt = e.get("text", "")
                    if txt:
                        self.cb.on_text_final(txt)
                    da = e.get("audio")
                    if da:
                        try:
                            self._emit_audio(base64.b64decode(da))
                        except Exception:
                            pass
                elif et == "session.closed":
                    self.cb.on_state("closed", e.get("reason"))
                    break
        except Exception:  # noqa: BLE001
            # 连接断开等异常由外层统一处理
            pass

    def _emit_audio(self, pcm_bytes: bytes):
        """收到语音：升级静音期间直接丢弃；否则交给回调（默认）与内置播放器。"""
        with self._audio_lock:
            suppressed = self._suppress_audio
        if suppressed:
            return
        self.cb.on_audio_chunk(pcm_bytes)
        if self.player is not None:
            self.player.play(pcm_bytes)

    # ============================================================ M2 升级路由
    def _on_text(self, txt: str):
        """文本增量处理：先广播给回调，再做 <<CALL_QWEN>> 令牌检测。

        令牌可能跨多个 delta 分片到达，故先做全文累积再查找；命中后抑制本轮
        omni 语音（避免把令牌行 / omni 的延续回答播给 boss），并回调 on_call_qwen。
        """
        self.cb.on_text_delta(txt)
        if self._call_qwen_fired:
            return
        self._text_buf += txt
        token = "<<CALL_QWEN>>"
        idx = self._text_buf.find(token)
        if idx < 0:
            # 尚未出现令牌；若之前已命中但任务未齐，继续累积
            if self._pending_task is not None:
                self._pending_task += txt
                if "\n" in self._pending_task or len(self._pending_task) >= 128:
                    task = self._pending_task.split("\n", 1)[0].strip()[:128]
                    self._fire_call_qwen(task)
            return
        # 命中令牌：取首个换行前的内容作为任务描述
        after = self._text_buf[idx + len(token):]
        if "\n" in after:
            task = after.split("\n", 1)[0].strip()
            self._fire_call_qwen(task)
        else:
            # 任务可能在后续 delta 到达：进入 pending 累积，直到换行或长度上限
            self._pending_task = after
            # 兜底：若 omni 在令牌行后立刻停住（不出换行），1.5s 后用已累积内容触发，
            # 避免升级永不触发。
            if self._pending_timer is None or not self._pending_timer.is_alive():
                self._pending_timer = threading.Timer(1.5, self._finalize_pending)
                self._pending_timer.daemon = True
                self._pending_timer.start()

    def _fire_call_qwen(self, task: str):
        """触发升级：置位标志 + 静音主会话 + 回调（幂等，只触发一次）。"""
        if self._call_qwen_fired:
            return
        self._call_qwen_fired = True
        self._pending_task = None
        # 取消可能仍在等待的兜底定时器
        if self._pending_timer is not None and self._pending_timer.is_alive():
            self._pending_timer.cancel()
            self._pending_timer = None
        with self._audio_lock:
            self._suppress_audio = True   # 升级期间静音主会话，避免与回灌重叠/回声
        self.cb.on_call_qwen(task)

    def _finalize_pending(self):
        """令牌已命中但任务描述未以换行结束时的兜底触发（定时器回调）。"""
        if self._call_qwen_fired:
            return
        task = (self._pending_task or "").strip()[:128]
        if task:
            self._fire_call_qwen(task)

    def mark_escalation_done(self):
        """标记升级任务已完成（由 runtime 在回灌播报结束后调用）。

        真正的静音解除发生在下一次 listen 事件（见 _receiver_loop），避免在 omni
        正在说话的中途突然恢复播放造成爆音。
        """
        with self._audio_lock:
            self._escalation_done = True

    def get_ref_audio_b64(self) -> str:
        """返回克隆声纹 base64（供回灌通道复用）。"""
        return self._ref_audio_b64

    def speak_result_via_turnbased(self, text: str):
        """回灌：用 omni 的克隆声纹（临时 turn_based 会话）播报 qwen+tools 的结果。

        不阻塞调用方太久：内部开独立线程 join 等待播报结束；调用方应在调用本方法
        之后（或本方法返回后）调用 mark_escalation_done() 解除主会话静音。
        """
        ref = self._ref_audio_b64
        if not ref:
            return
        # 延迟导入，避免回灌模块在仅需基础客户端时被加载
        from .backfeed import speak_text_via_omni
        speak_text_via_omni(self.url, ref, text)


def _ensure_no_proxy():
    """确保 localhost 不走系统代理（本机代理会劫持 127.0.0.1）。"""
    for key in ("NO_PROXY", "no_proxy"):
        val = os.environ.get(key, "")
        if "127.0.0.1" not in val:
            os.environ[key] = (val + ",127.0.0.1,localhost").strip(",")
