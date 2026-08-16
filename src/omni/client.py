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
import re
import threading
import time

import cv2
import numpy as np
import pyaudio
import soundfile as sf
import soxr
import websockets

from src.capture.camera import Camera
from .voicebox_bridge import VoiceboxBridge

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

    def __init__(self, rate: int = TARGET_SR, frames_per_buffer: int = 1024, retries: int = 4):
        """初始化 PyAudio 输出流（打开失败时重试，应对 macOS 音频硬件插拔/蓝牙切换的瞬时错误）。

        macOS 上 PaMacCore/AUHAL 在音频设备列表变化（如 AirPods 断开/重连）瞬间打开默认
        输出流会抛 `OSError: [Errno -9986] Internal PortAudio error`，短暂重试通常可恢复；
        重试用尽仍失败则把异常抛给调用方（由调用方降级为仅文本输出，不让线程崩溃）。
        """
        self.p = pyaudio.PyAudio()
        self.rate = rate
        self.q: "queue.Queue[bytes]" = queue.Queue()
        self._stream = None
        last_err = None
        for _ in range(max(1, retries)):
            try:
                self._stream = self.p.open(
                    format=pyaudio.paFloat32,
                    channels=1,
                    rate=rate,
                    output=True,
                    frames_per_buffer=frames_per_buffer,
                    stream_callback=self._callback,
                )
                self._stream.start_stream()
                return
            except OSError as e:
                last_err = e
                time.sleep(0.3)
        raise last_err

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
                 mic_index: int = None,
                 mic_gain: float = 1.0,
                 listen_prob_scale: float = 0.5,
                 push_interval: float = 0.4, video_fps: int = 5,
                 camera_width: int = 1280, camera_height: int = 720,
                 video_quality: int = 80, voicebox_speaker=None):
        """初始化客户端。

        Args:
            url: WS 地址，如 ws://127.0.0.1:9060/backend。
            ref_audio_path: 声纹克隆参考音（wav，任意采样率，会自动重采样 16k）。
            system_prompt: omni 系统提示（含角色与 CALL_QWEN 令牌约定）。
            callbacks: 事件回调（默认空实现，打印到控制台）。
            camera: 复用外部摄像头对象；为 None 且 enable_camera 时自建。
            enable_mic/camera/playback: 采集/播放开关（便于测试时关闭某项）。
            mic_index: 强制绑定的麦克风输入设备 index；为 None 时跟随系统默认输入
                （macOS 戴蓝牙耳机时默认输入常被自动切换成耳机麦，导致采到弱信号/静音，
                可显式指定「内建麦克风」的 index 规避切麦问题）。
            mic_gain: 麦克风采集增益倍数（默认 1.0 = 不变）。内建麦在屏幕顶部、离嘴远，
                送上去的音频能量低于服务端 VAD 触发阈值时会「只听不说」，适当放大（如 6~10）
                可抬到可触发水平；增益会限幅到 [-1,1] 防削波失真。
            listen_prob_scale: 全双工采样参数，<1 压低 <|listen|> 采样概率逼模型回话，
                >1 增 listen。服务端默认 1.0（偏置 0）会让模型恒 listen 导致「只听不说」，
                客户端显式传 0.5（偏置 -1.0）可修复。
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
        self.mic_index = mic_index            # 强制绑定的麦克风 index（None=跟随系统默认输入）
        self.mic_gain = mic_gain             # 麦克风采集增益（1.0=不变，>1 放大能量触发服务端 VAD）
        self.listen_prob_scale = listen_prob_scale  # 全双工采样：压低 listen 偏好，避免只听不说
        self.push_interval = push_interval
        self.video_fps = video_fps
        self.camera_width = camera_width
        self.camera_height = camera_height
        self.video_quality = video_quality

        # ---- M7b/M7a：本地 Voicebox 克隆 TTS 复用（None=走 omni 自带 audio）----
        self._voicebox_speaker = voicebox_speaker
        # 句子级流式桥接仅当启用播放且传入 speaker 时启用；--no-play 下主对话静音
        # （回灌仍用 voicebox_speaker，不受 --no-play 影响）
        self._voicebox_bridge = (
            VoiceboxBridge(voicebox_speaker) if (voicebox_speaker and self.enable_playback)
            else None
        )

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
        # 升级令牌护栏：记录「最近一次检测到真实人声」的墙钟时间（monotonic）。
        # omni 会在静音期幻觉出 <<CALL_QWEN>> 任务并自触发（真机已复现：静音段 RMS 0.003
        # 却凭空生成"查电池电量"任务），令牌出现前若无真实人声则判定为幻觉、拒绝升级。
        self._last_speech_ts = 0.0           # 最近一次 RMS≥阈值的人声时刻（0=尚无）
        self._speech_window = 3.0            # 令牌前多少秒内有人声才算"真实触发"（秒）
        self._speech_rms_th = 0.02           # 判定人声的 RMS 阈值（mic_check 实测说话段 0.08+）
        self._call_qwen_fired = False        # 本次会话是否已触发过升级（幂等 + 停止朗读）
        self._token_seen = False             # 是否已发现令牌但尚未 fire（pending 累积中，停止朗读）
        self._pending_task = None            # 令牌已命中但任务描述尚未完整时的临时累积
        self._pending_timer = None           # 令牌后无换行时的兜底触发定时器

        # ---- GUI 实时展示用缓存（由 omni 接收/采集线程写入，GUI 定时器轮询读取）----
        self._last_mic_level = 0.0           # 最近一次麦克风 RMS（供 GUI 音量条）
        self._reply_buf = ""                 # 累积的 omni 回复文本（含升级结果，供 GUI 文字区）
        self._reply_lock = threading.Lock()  # 保护 _reply_buf 的读写
        self._ref_audio_b64 = ""             # 克隆声纹 base64（session.init 后存入，供回灌复用）
        self._suppress_audio = False         # 升级期间抑制主会话语音输出（避免与回灌重叠/回声）
        self._escalation_done = False        # 升级任务是否已完成（配合 listen 事件解除静音）
        self._audio_lock = threading.Lock()  # 保护上面的静音/完成标志（跨线程读写）
        # 首个 listen 事件信号：omni 真正进入聆听（模型加载完成）的可靠标志，
        # 用于 start() 等待「真正就绪」，避免自动启动时模型还在后台加载就误报就绪。
        self._listen_ev = threading.Event()

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
        # 释放 M7b 句子级桥接播放线程
        if self._voicebox_bridge is not None:
            try:
                self._voicebox_bridge.stop()
            except Exception:  # noqa: BLE001
                pass
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
                    # 压低 <|listen|> 采样偏好，避免模型「只听不说」
                    # （服务端默认 listen_prob_scale=1.0 偏置 0，会恒采样 listen 导致永不回复）
                    "config": {
                        "listen_prob_scale": self.listen_prob_scale,
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
                # 打印当前默认输入设备，方便排查戴耳机后麦克风被切换/失效导致 omni 听不到声音的问题
                try:
                    _dev = self._pyaudio.get_default_input_device_info()
                    print(f"[omni] 默认输入设备: {_dev.get('name')} "
                          f"(index={_dev.get('index')}, "
                          f"采样率≈{int(_dev.get('defaultSampleRate', 0))})", flush=True)
                except Exception:
                    pass
                # 若显式指定 mic_index，则强制绑定该硬件输入设备，避免 macOS 默认输入
                # 跟随蓝牙耳机（AirPods 等）自动切换、导致采到静音/远场弱信号的问题
                _open_kwargs = dict(
                    format=pyaudio.paFloat32,
                    channels=1,
                    rate=TARGET_SR,
                    input=True,
                    frames_per_buffer=1024,
                )
                if self.mic_index is not None:
                    _open_kwargs["input_device_index"] = self.mic_index
                    print(f"[omni] 已强制绑定麦克风设备 index={self.mic_index}（忽略系统默认输入）",
                          flush=True)
                if self.mic_gain and self.mic_gain != 1.0:
                    print(f"[omni] 已应用麦克风增益 ×{self.mic_gain}（提升内建麦能量以触发服务端 VAD）",
                          flush=True)
                self._mic_stream = self._pyaudio.open(**_open_kwargs)
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
                # 麦克风增益：内建麦离嘴远、能量不足时，服务端 VAD 阈值触发不了回复；
                # 乘增益并限幅到 [-1,1] 防削波，把低能量抬到可触发水平（默认 1.0 = 不变）
                if self.mic_gain and self.mic_gain != 1.0:
                    arr = np.clip(arr * self.mic_gain, -1.0, 1.0).astype(np.float32)
                    audio = arr.tobytes()
            else:
                rms = 0.0
            self.cb.on_mic_level(rms)
            self._last_mic_level = rms        # 缓存供 GUI 音量条轮询
            # 检测到真实人声（RMS≥阈值）时刷新时间戳，供升级令牌护栏判定「令牌前是否有人声」
            is_speech = rms >= self._speech_rms_th
            if is_speech:
                self._last_speech_ts = time.monotonic()
            # 推流诊断日志治理（#4）：默认只在「人声↔静音」状态翻转时打印一行，
            # 避免每 ~0.4s 刷屏；仅当环境变量 OMNI_DEBUG=1 时才逐块打印 RMS 诊断详情。
            self._push_seq = getattr(self, "_push_seq", 0) + 1
            prev_state = getattr(self, "_last_speech_state", None)
            if is_speech != prev_state:
                if is_speech:
                    peak = float(np.max(np.abs(arr))) if audio and arr.size else 0.0
                    print(f"[omni-client] 🎙 检测到人声（RMS={rms:.3f} 峰值={peak:.3f}）",
                          flush=True)
                else:
                    print(f"[omni-client] 进入静音（RMS={rms:.3f}）", flush=True)
                self._last_speech_state = is_speech
            # 逐块诊断仅在显式开启时打印，便于排查「说话但 omni 无反应」类问题
            if os.environ.get("OMNI_DEBUG") == "1":
                peak = float(np.max(np.abs(arr))) if (is_speech and audio and arr.size) else 0.0
                print(f"[omni-client][debug] 推流#{self._push_seq} "
                      f"{'人声' if is_speech else '静音'} RMS={rms:.3f} 峰值={peak:.3f} 块={len(audio)}B",
                      flush=True)
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
                if et == "response.output.delta":
                    kind = e.get("kind", "")
                    if kind == "text":
                        txt = e.get("text", "")
                        if txt:
                            self._on_text(txt)
                    elif kind == "audio":
                        # M7b：主对话走 Voicebox 句子级桥接时，丢弃 omni 自带 audio
                        # （omni 自带 TTS 无克隆、音质差）；仅当未启用桥接时才播 omni audio
                        if self._voicebox_bridge is None:
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
                        # 新一轮聆听开始 = 上一轮对话已结束 → 复位升级标志，
                        # 允许本轮再次触发 <<CALL_QWEN>>（修复"第二次升级被吞"）
                        self._reset_escalation_state()
                elif et == "response.done":
                    txt = e.get("text", "")
                    if txt:
                        self.cb.on_text_final(txt)
                    # M7b：会话正常结束（未触发升级、也未发现令牌）时 flush 尾句，让主对话
                    # 残留文本播完；已触发升级或发现令牌则主对话已转回灌，不再播主对话尾巴
                    if (self._voicebox_bridge is not None
                            and not self._call_qwen_fired and not self._token_seen):
                        self._voicebox_bridge.flush_remaining()
                    da = e.get("audio")
                    if da and self._voicebox_bridge is None:
                        try:
                            self._emit_audio(base64.b64decode(da))
                        except Exception:
                            pass
                elif et == "session.closed":
                    # M7b：连接关闭时 flush 尾句（未升级、未发现令牌时）
                    if (self._voicebox_bridge is not None
                            and not self._call_qwen_fired and not self._token_seen):
                        self._voicebox_bridge.flush_remaining()
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
        """文本增量处理：先广播回调，再做 <<CALL_QWEN>> 令牌检测与拦截。

        令牌可能跨多个 delta 分片到达，故先做全文累积再查找。**关键修复**：令牌检测
        必须在把文本喂给 Voicebox 桥接之前完成——否则承载 `<<CALL_QWEN>>查电池` 的
        delta 会先被送进朗读队列（即"把问题本身读出来"的 bug）。命中令牌时只把令牌
        之前的文本送桥接朗读，令牌本身及任务描述一律丢弃（绝不朗读问题），并触发升级。
        升级触发后主会话后续文本一律不再朗读。
        """
        # 广播给回调（GUI 实时回复文字区 / 控制台显示），不发声
        self.cb.on_text_delta(txt)
        # 累积回复文本供 GUI 实时文字区轮询（限长，避免无限增长）
        with self._reply_lock:
            self._reply_buf += txt
            if len(self._reply_buf) > 4000:
                self._reply_buf = self._reply_buf[-2000:]

        # 升级已触发：主会话后续文本（含 omni 尾随回复）一律不再朗读
        if self._call_qwen_fired:
            return
        # 令牌已发现但尚未 fire（pending 累积中）：跨换行持续累积任务描述，不再朗读；
        # 每来一段就尝试结算（命中句末标点才触发），避免等 1.5s 兜底定时器、降低延迟。
        # 关键修复：不再按首个换行硬截断任务——ASR 会把「查一下这台电脑的本地时间」
        # 拆成「查 一 下这台电」+「脑的本地时间」，按首个换行截断会丢后半句导致答非所问。
        if self._token_seen:
            self._pending_task = (self._pending_task or "") + txt
            self._try_finalize_pending()
            return

        # 未触发：累积全文用于跨分片令牌检测
        self._text_buf += txt
        token = "<<CALL_QWEN>>"
        idx = self._text_buf.find(token)
        if idx < 0:
            # 尚未出现令牌：正常主对话文本，送 Voicebox 句子级桥接朗读
            if self._voicebox_bridge is not None:
                self._voicebox_bridge.feed(txt)
            return

        # 命中令牌：先判幻觉——若令牌出现前「最近 window 秒内无真实人声」，
        # 判定为 omni 在静音期幻觉生成的自触发任务（真机已复现：纯静音段 RMS≈0.003
        # 却凭空生成"查电池电量"并自动执行，且因 _call_qwen_fired 静音导致用户随后
        # 真实发言也无回复）。幻觉时不触发升级、不静音，仅丢弃该任务并停止朗读幻觉内容。
        if not self._has_recent_speech():
            self._token_seen = True  # 停止朗读幻觉内容，但不静音、不触发升级
            snippet = self._text_buf[idx + len(token):idx + len(token) + 40].replace("\n", " ")
            print(f"[omni-client] ⚠️ 升级令牌疑似静音期幻觉（令牌前无真实人声），已拦截丢弃：{snippet!r}",
                  flush=True)
            return
        # 命中令牌：标记已发现，停止后续朗读；仅把令牌之前的内容送桥接朗读
        self._token_seen = True
        pre = self._text_buf[:idx]
        if pre.strip() and self._voicebox_bridge is not None:
            self._voicebox_bridge.feed(pre)
        # 令牌及后续任务描述：跨换行累积（不再按首个换行截断），交由 _try_finalize_pending
        # 在命中句末标点时结算触发；若模型迟迟不给标点，由 1.5s 兜底定时器触发，避免升级永不触发。
        after = self._text_buf[idx + len(token):]
        self._pending_task = after
        self._try_finalize_pending()
        if not self._call_qwen_fired:
            if self._pending_timer is None or not self._pending_timer.is_alive():
                self._pending_timer = threading.Timer(1.5, self._finalize_pending)
                self._pending_timer.daemon = True
                self._pending_timer.start()

    def _has_recent_speech(self) -> bool:
        """升级令牌护栏：令牌出现前「最近 window 秒内是否检测到真实人声」。

        返回 True 表示令牌大概率源于用户真实发言（可信触发），False 表示静音期
        幻觉（应拦截）。判定依据：_push_loop 在每帧 RMS≥阈值时刷新 _last_speech_ts。
        """
        # 全程从未检测到人声（如开局模型自言自语）：必为幻觉
        if self._last_speech_ts <= 0.0:
            return False
        return (time.monotonic() - self._last_speech_ts) <= self._speech_window

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
        # M7b：升级触发时 flush 主对话攒句缓冲并清空未播队列，避免与回灌（Voicebox）重叠
        if self._voicebox_bridge is not None:
            self._voicebox_bridge.flush_and_stop()
        self.cb.on_call_qwen(task)

    def _finalize_pending(self):
        """令牌已命中但任务描述未以句末标点结束时的兜底触发（定时器回调）。

        用 _clean_task 清洗（折叠 ASR 汉字间空格 + 跨换行连接）后再触发，
        避免把「查 一 下这台电」这类残缺任务直接交给大脑。
        """
        if self._call_qwen_fired:
            return
        task = self._clean_task(self._pending_task or "")
        if task:
            self._fire_call_qwen(task)

    def _try_finalize_pending(self):
        """令牌后的任务描述累积到「一句话结束」就即时结算触发。

        判定规则：清洗后的任务文本命中句末标点（。！？!?）即视为一句话说完，切掉标点
        之前部分作为任务立即触发；或累积长度超过上限（防模型迟迟不给句号）也触发。
        否则保持 pending，等待后续 delta 或兜底定时器。
        """
        if self._call_qwen_fired:
            return
        raw = self._pending_task or ""
        flat = raw.replace("\n", "")
        # 句末标点集合：任务描述通常以句号 / 问号 / 感叹号结束
        cut = -1
        for i, ch in enumerate(flat):
            if ch in "。！？!?":
                cut = i
                break
        if cut >= 0:
            task = self._clean_task(flat[:cut])   # 不含句末标点，交给大脑更干净
            if task:
                self._fire_call_qwen(task)
            return
        # 长度上限兜底：累积过长也触发，避免模型迟迟不给句号导致升级迟迟不触发
        if len(flat) >= 64:
            task = self._clean_task(flat)
            if task:
                self._fire_call_qwen(task)

    def _clean_task(self, text: str) -> str:
        """清洗升级任务描述：删除汉字之间的空白（根治 ASR「查 一 下」），折叠剩余空白。

        ASR 常把中文逐字以空格隔开（如「查 一 下这台 电脑」），且任务可能跨多个 delta
        分片到达并夹带换行。本方法在发给大脑前把汉字之间的空格/换行抹掉，再把其余空白
        折叠为单空格，保证大脑拿到连贯的指令（如「查一下这台电脑的本地时间」）。
        """
        if not text:
            return ""
        # 1) 删除「汉字与汉字（或汉字与标点）之间」的空白：根治逐字空格 + 跨换行连接
        #    例：「查 一 下这台电」+「脑的本地时间」→ 删汉字间空白 →「查一下这台电脑的本地时间」
        s = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
        # 2) 折叠其余空白（换行 / 多空格）为单空格并去首尾
        s = re.sub(r"\s+", " ", s).strip()
        return s

    def mark_escalation_done(self):
        """标记升级任务已完成（由 runtime 在回灌播报结束后调用）。

        真正的静音解除发生在下一次 listen 事件（见 _receiver_loop），避免在 omni
        正在说话的中途突然恢复播放造成爆音。
        """
        with self._audio_lock:
            self._escalation_done = True

    def _reset_escalation_state(self):
        """升级标志复位：在新一轮 listen（新用户轮）时调用，允许再次触发升级。

        不清空 _escalation_done（由 listen 分支按需复位静音标志）；只复位令牌检测/
        升级触发相关状态，避免"第二次升级被吞"（_call_qwen_fired 永不复位）的 bug。
        """
        self._call_qwen_fired = False
        self._token_seen = False
        self._text_buf = ""
        self._pending_task = None
        if self._pending_timer is not None and self._pending_timer.is_alive():
            self._pending_timer.cancel()
        self._pending_timer = None
        with self._audio_lock:
            self._escalation_done = False

    def get_ref_audio_b64(self) -> str:
        """返回克隆声纹 base64（供回灌通道复用）。"""
        return self._ref_audio_b64

    # ============================================================ GUI 实时展示接口
    def get_latest_mic_level(self) -> float:
        """返回最近一次麦克风 RMS（0~1 归一），供 GUI 音量条轮询。"""
        return self._last_mic_level

    def get_reply_text(self) -> str:
        """返回累积的 omni 回复文本（含升级结果），供 GUI 实时文字区轮询。"""
        with self._reply_lock:
            return self._reply_buf

    def append_reply(self, text: str):
        """向回复文本缓存追加一段（如升级结果），供 GUI 实时文字区显示。"""
        if not text:
            return
        with self._reply_lock:
            self._reply_buf += text
            if len(self._reply_buf) > 4000:
                self._reply_buf = self._reply_buf[-2000:]

    def speak_result(self, text: str):
        """M7a 回灌：用本地 Voicebox（JAC 克隆声纹）播报 qwen+tools 的结果文本。

        替代原 omni 临时 turn_based 会话（llama.cpp-omni server 单会话——主 full_duplex
        占槽后第二个会话被拒，server 日志 `session.init rejected — active session exists`
        → ConnectionClosedOK 无声音）。Voicebox 是独立进程，不受 omni 会话限制，且
        自带 JAC 克隆声纹（音质远好于 omni 自带 TTS）。

        不阻塞调用方：speak 内部同步合成 + 播放，调用方已在独立 daemon 线程里。
        调用方应在本方法返回后调用 mark_escalation_done() 解除主会话静音。
        """
        spk = self._voicebox_speaker
        # 延迟导入回灌模块（M7a 改为本地 Voicebox 克隆合成，不再开 omni 第二会话）
        from .backfeed import speak_text_via_voicebox
        if spk is None:
            # 未接入 Voicebox（如 --no-voicebox）：降级系统 TTS，确保答案一定出声
            speak_text_via_voicebox(None, text)
            return
        speak_text_via_voicebox(spk, text)


def _ensure_no_proxy():
    """确保 localhost 不走系统代理（本机代理会劫持 127.0.0.1）。"""
    for key in ("NO_PROXY", "no_proxy"):
        val = os.environ.get(key, "")
        if "127.0.0.1" not in val:
            os.environ[key] = (val + ",127.0.0.1,localhost").strip(",")


def list_input_devices():
    """打印所有可用的音频输入设备（index + 名称 + 采样率），用于定位内建麦克风 index。

    用法：先 `python -m src.omni --list-mics` 找到「内建麦克风」对应的 index，
    再 `python -m src.omni --mic <该index>` 强制绑定，规避 macOS 跟随蓝牙耳机切麦。
    """
    _p = pyaudio.PyAudio()
    try:
        n = _p.get_device_count()
        print("可用麦克风输入设备：")
        for i in range(n):
            d = _p.get_device_info_by_index(i)
            if int(d.get("maxInputChannels", 0)) > 0:
                print(f"  index={i}  {d.get('name')}  "
                      f"(采样率≈{int(d.get('defaultSampleRate', 0))})")
    finally:
        _p.terminate()
