"""MiniCPM-o-4_5 全双工 WebSocket 客户端（J.A.C. 的「耳朵 + 眼睛 + 嘴巴」）。

协议契约（来自 llama.cpp-omni master 分支源码坐实 + M0 探针实测）：
  上行 session.init：
    {"type":"session.init","payload":{"mode":"full_duplex","use_tts":true,
     "voice":{"ref_audio":<16k float32 PCM base64>},
     "system_prompt":"...","config":{"listen_prob_scale":0.5}}}
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

P0 修复（2026-09-13，真机 + 服务端日志实测后落地；共三轮迭代）：
  - **P0-a 令牌前缀 holdback + 畸形令牌容忍**：服务端按 token 逐片下发文本，`<<CALL_QWEN>>`
    会被切成 `<<CALL_Q` 这类碎片；碎片到达瞬间缓冲里没有完整令牌，于是被当普通对话朗读
    （Voicebox 里那段 `<<CALL_Q` 怪音）。三轮追加两条硬事实：①**模型不保证原样吐出令牌**
    （真机确认会吐 `<<CALL_ QWEN>>`，中间夹空格/换行，空格来自模型本身），精确匹配会漏、
    且 `<<CALL_` 后接空格就不再是前缀、holdback 也拦不住；②`listen` 在 full_duplex 下
    **每段都会来**，不能用它清文本缓冲（会把令牌拦腰截断）。现令牌识别统一在 `tokens.py`
    （容忍空白/换行的匹配 + holdback + 含尖括号一律不外发的硬安全网），命中后消费掉令牌；
    释放点只有 listen / session.closed / 文本静默 `OMNI_HOLD_IDLE`。
  - **P0-b 推流背压 + 水位 + 段事件去重**：服务端 full_duplex 是串行「读一段 → prefill+decode
    一步」，单段实测 0.69s（VPM 图像编码 190ms 是大头）。三轮发现**一段会发两个终局事件且
    共用同一 `response_id`**（listen delta + response.done），若都算「本段完成」则推流翻倍、
    出现 0.02s 级极小块 → 每轮仍要重做图像编码（成本与块大小无关）→ 消费速率腰斩 →
    积压丢帧（真机累计丢 5.1s，丢在句子中间 → 模型只听得到残句 → 答非所问）。
    现按 response_id 去重 + 最小推流间隔 + 最小块时长（`OMNI_MIN_CHUNK_SECS`）+ 音频水位
    （`max_buf_secs`），并保证**每帧必带音频**（服务端收到空音频会 fail_fast 打死会话）。
  - **人声判据与门控**：判据改「峰值 或 RMS」双条件（RMS 阈值 0.02 正压在人声段下沿）、
    窗口放宽到 6s（原 3s 小于端到端延迟，会把真实提问误判成幻觉）；门控 auto 判定同时看
    输入与输出设备，自身播报窗口的排除从门控开关解耦。
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
from src.audio import playback as _playback
from .voicebox_bridge import VoiceboxBridge
# 令牌识别统一在 tokens.py：容忍空白/换行/大小写的变体（真机确认模型会吐 `<<CALL_ QWEN>>`）
from .tokens import (
    CALL_TOKEN,
    find_call_token,
    token_prefix_suffix_len as _token_prefix_suffix_len,
    sanitize_for_speech,
)

# 全双工音频目标采样率（omni 要求 16kHz float32 单声道）
TARGET_SR = 16000


async def _safe_close(ws):
    """尽力关闭 WS（已关闭/正在关闭时忽略异常）。"""
    try:
        await ws.close()
    except Exception:  # noqa: BLE001
        pass


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
                 video_quality: int = 80, voicebox_speaker=None,
                 echo_gate: bool = None, flow_control: bool = True,
                 max_buf_secs: float = None, chunk_wait_timeout: float = None,
                 video_interval: float = None,
                 video_enabled: bool = None, debug: bool = None):
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
            video_fps: 摄像头刷新率（只影响「最新帧」的刷新频率与 GUI 预览观感，
                与上行帧率解耦——上行由 video_interval 单独控制）。
            video_interval: **视频上行间隔（秒）**。P1：默认 1.0，即音频照常按块上行、
                但图像每秒只带 1 帧。为什么必须降：服务端每带一帧图就要做一次 VPM 编码
                （实测约 190ms）并往 KV 里塞约 64 个视觉 token，而每一段音频只有个位数
                token——按 2.5 段/秒推就是 160 视觉 token/秒，n_ctx=8192 约 45 秒填满，
                导致每约 30 秒一次上下文滑动（滑掉之后模型只剩 system prompt，于是照
                示例复读令牌）。降到 1 帧/秒可把 KV 增长降约三成、上下文寿命 +约四成，并每轮省下一次 VPM 编码，
                同时每轮省下约 190ms 让实时性有余量。<=0 表示退回旧行为（每段都带图）。
                None=读环境变量 OMNI_VIDEO_INTERVAL（默认 1.0）。
            video_enabled: **图像上行总开关**（P0 变量分离实验，默认 True）。False = 一个
                视频帧都不发，纯音频全双工（等价「模型闭上眼睛」）。用途：隔离验证
                「视觉 token 吃爆 KV」这条机制——真机日志显示每带一帧图就写约 64 个视觉
                token 进 KV，n_ctx=8192 时上下文每约 30 秒被滑动清空一次，清完模型只剩
                system prompt，于是照 prompts.py 里的示例复读「查一下这台电脑的电池电量
                百分比」。关掉图像若幻觉消失，即可坐实该机制。None=读环境变量
                OMNI_VIDEO_ENABLED（默认开）。注意与 video_interval 的区别：本开关是
                「发不发」，video_interval 是「多久发一帧」；间隔 <=0 表示每段都发。
            debug: 逐块上行诊断日志（默认 False）。True 时打印每段的推流序号/间隔/块长/
                RMS/峰值/距上次人声，以及 omni 文本 delta 的 repr，用于量化「块长抖动」与
                定位畸形令牌。会明显刷屏。None=读环境变量 OMNI_DEBUG（"1"/"true"/"on"/"yes" 为真）。
            camera_width/height: 自建摄像头分辨率。
            video_quality: jpeg 编码质量（0~100）。
            echo_gate: 回声门控开关；None 表示读环境变量 OMNI_ECHO_GATE（默认开）。
            flow_control: P0-b 推流背压开关（默认开）。开则「一段在飞」——等服务端把上
                一段 prefill+decode 完再推下一段，避免音频积压线性增长（真机实测 64s 会话
                曾落后 27s，模型回答的是半分钟前的用户）。关闭则退回固定 0.4s 猛推。
            max_buf_secs: 上行音频水位上限（秒），超过就丢最旧的音频以保住实时性。
                None=读环境变量 OMNI_MAX_BUF_SECS（默认 1.2s）。
            chunk_wait_timeout: 背压等待单段处理完成的最长秒数（超时兜底，防服务端不响应
                时死等）。None=读环境变量 OMNI_CHUNK_WAIT（默认 1.0s）。
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
        # ---- P1 图像降频（视频上行间隔）----
        # 服务端每带一帧图就要做一次 VPM 编码（实测约 190ms）并往 KV 塞约 64 个视觉 token，
        # 而每段音频只有个位数 token。按 2.5 段/秒带图 = 约 160 视觉 token/秒 →
        # n_ctx 8192 约 45 秒填满 → 每约 30 秒一次上下文滑动（滑掉后模型只剩 system prompt，
        # 于是照示例复读令牌，真机三轮已复现「查询一下最近的新闻」）。
        # 降到 1 帧/秒后的实测收益（真机日志：VPM p50=196ms/均值 232ms，图像 64 视觉 token/帧，
        # 块节奏约 1.5 段/秒）：KV 增长 109→79 token/秒（降约三成）、VPM 负载 0.34→0.23 秒/秒
        # （降约三分之一）、上下文寿命 69→95 秒（+约四成），且每轮省下一次 VPM 编码。
        self.video_interval = float(
            video_interval if video_interval is not None
            else os.environ.get("OMNI_VIDEO_INTERVAL", "1.0")
        )
        self._last_frame_ts = 0.0            # 上次带图时刻（0=本会话还没带过图）
        self._frames_sent = 0                # 已带图段数（诊断用）

        # ---- P0 图像上行总开关（变量分离实验：关掉即可隔离「视觉 token 爆 KV」）----
        # 与 video_interval 的区别：本开关决定「发不发」，video_interval 决定「多久发一帧」。
        # 关闭后纯音频全双工，服务端不再做 VPM 编码、也不再往 KV 写视觉 token。
        if video_enabled is None:
            _ve = os.environ.get("OMNI_VIDEO_ENABLED")
            video_enabled = True if _ve is None else _ve.strip().lower() not in (
                "0", "false", "no", "off")
        self.video_enabled = bool(video_enabled)

        # ---- 逐块诊断日志开关 ----
        # 原先 5 处直接读 os.environ（GUI 启动的进程改不了环境变量，开关只能靠命令行），
        # 现统一收敛到 self._debug，支持 GUI 复选框 / CLI --debug / 环境变量三种入口。
        if debug is None:
            _db = (os.environ.get("OMNI_DEBUG") or "").strip().lower()
            debug = _db not in ("", "0", "false", "no", "off")
        self._debug = bool(debug)

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
        self._shown_len = 0                  # _text_buf 中已广播显示到的位置（令牌后不再前进）
        # 升级令牌护栏：记录「最近一次检测到真实人声」的墙钟时间（monotonic）。
        # omni 会在静音期幻觉出 <<CALL_QWEN>> 任务并自触发（真机已复现：静音段 RMS 0.003
        # 却凭空生成"查电池电量"任务），令牌出现前若无真实人声则判定为幻觉、拒绝升级。
        self._last_speech_ts = 0.0           # 最近一次「有人声」的时刻（0=尚无）
        # 窗口：令牌从「用户说话」到「客户端收到」的端到端延迟 = 音频积压 + 服务端一轮
        # prefill/decode + TTS 排队，实测远超 3s。窗口小于链路延迟会把**真实提问**判成
        # 幻觉（真机三轮：真实提问被拦截、升级任务丢失）。默认放宽到 6s。
        self._speech_window = float(os.environ.get("OMNI_SPEECH_WINDOW", "6.0"))
        # 人声判据：RMS + 峰值双条件（真机实测：底噪 RMS 0.002~0.013，人声 RMS 0.020~0.056
        # ——阈值 0.02 正压在人声段下沿，单看 RMS 会让人声帧在阈值附近来回抖；
        # 而人声峰值 0.088~0.277 与底噪分离干净，两个条件取「或」更稳）。
        self._speech_rms_th = float(os.environ.get("OMNI_SPEECH_RMS_TH", "0.02"))
        self._speech_peak_th = float(os.environ.get("OMNI_SPEECH_PEAK_TH", "0.06"))
        # 自身播报窗口的排除策略：always（默认，无论门控开关都排除）| gate（只在门控开时排除）
        self._echo_guard = os.environ.get("OMNI_ECHO_GUARD", "always").strip().lower()

        # ---- 回声门控（Echo Gate）：J.A.C. 说话时把麦克风当「听不见」----
        # 根因（真机 2026-09-06 复现）：TTS 外放被本机麦克风重新采集，日志里
        # 「[TTS] 正在播放」出现的同时立即「🎙 检测到人声（RMS=0.022）」——omni 听到
        # 的是它自己上一轮的语音，于是自问自答、凭空生成「给您推荐一部电」这类
        # 幻觉文本并吐出 <<CALL_QWEN>>。因为回声 RMS 也能过 0.02 阈值，原有
        # _has_recent_speech 护栏会被回声骗过。根治靠 WebRTC AEC；工程等价做法是
        # 播放期间用等长静音替代真实采集推送（保持实时节奏，避免「只听不说」）。
        # 代价：J.A.C. 说话期间听不到用户插话；戴耳机（硬件隔离回声）时可用
        # OMNI_ECHO_GATE=0 关闭门控以保留打断能力。
        # 显式传参优先；否则读环境变量 OMNI_ECHO_GATE（未设置=auto，按输出设备自动判定）
        _pref = echo_gate if echo_gate is not None else os.environ.get("OMNI_ECHO_GATE", "auto")
        self._echo_gate, self._echo_gate_reason = resolve_echo_gate(_pref)
        self._echo_tail = float(os.environ.get("OMNI_ECHO_TAIL", "0.8"))  # 播放结束后的拖尾保护秒数
        self._echo_gated = False             # 当前是否正处于门控中（供日志/诊断）

        # ---- P0-b 推流背压（Flow Control）----
        # 根因（2026-09-13 读 temp/omni_server.log 实测）：服务端 full_duplex 是串行循环
        # 「读一条 input.append → prefill + decode 一步」，其单段耗时 ≈0.69s（图像 VPM 编码
        # 190ms 是大头，decode p50=330ms/p90=759ms），而客户端固定每 0.4s 猛推一段，
        # 于是积压线性增长——64.4s 的会话服务端累计落后 **27.2 秒**，模型回答的是半分钟前
        # 的用户（这正是 boss 反馈「我问的和它理解的不一样」的直接原因）。
        # 修法：改成「一段在飞」——等服务端把上一段处理完（收到 listen 或 response.done
        # 即算完）再推下一段；服务端慢就自动放慢节拍（chunk 变大），但延迟不再累积。
        # 另设音频水位上限：极端情况宁可丢最旧的音频，也要保证「模型听到的是现在」。
        _fc_env = os.environ.get("OMNI_FLOW_CONTROL", "1").strip().lower()
        self._flow_control = bool(flow_control and _fc_env not in ("0", "off", "false", "no"))
        self._max_buf_secs = float(
            max_buf_secs if max_buf_secs is not None
            else os.environ.get("OMNI_MAX_BUF_SECS", "1.2")
        )
        self._chunk_wait_timeout = float(
            chunk_wait_timeout if chunk_wait_timeout is not None
            else os.environ.get("OMNI_CHUNK_WAIT", "1.0")
        )
        self._chunk_done_ev = None           # asyncio.Event：服务端处理完本段的信号
        self._chunk_seq = 0                  # 已上行段号（诊断用）
        self._dropped_secs = 0.0             # 累计因超水位被丢弃的音频秒数（诊断用）
        # 终局事件去重：服务端「一段」会发 listen + response.done 两个终局事件且共用同一
        # response_id（真机三轮踩坑），不去重则一段被算两次完成 → 推流翻倍、块变小 →
        # 服务端每轮重做图像编码 → 消费速率腰斩 → 积压丢帧。
        self._last_chunk_resp_id = ""
        self._last_push_ts = 0.0             # 上次上行时刻（配合最小间隔，防小块洪泛）
        # 最小块时长：小于这个长度的音频不值得单独占一轮服务端算力（图像编码成本固定），
        # 极小块（真机见过 0.02s）会把真实吞吐量腰斩。默认与 push_interval 一致。
        self._min_chunk_secs = float(os.environ.get("OMNI_MIN_CHUNK_SECS", "0.4"))
        # 最小推流间隔：即使终局信号提前到达，也不许比 push_interval 更密
        self._min_push_interval = float(
            os.environ.get("OMNI_MIN_PUSH_INTERVAL", str(push_interval))
        )

        self._call_qwen_fired = False        # 本次会话是否已触发过升级（幂等 + 停止朗读）
        self._token_seen = False             # 是否已发现令牌但尚未 fire（pending 累积中，停止朗读）
        self._hallucinated = False           # 本轮令牌已被判定为幻觉（禁止后续任何 fire）
        self._pending_task = None            # 令牌已命中但任务描述尚未完整时的临时累积
        self._pending_timer = None           # 令牌后无换行时的兜底触发定时器
        # P0-a 文本静默兜底释放：holdback 扣留的尾巴只能在「本轮真的说完了」时释放。
        # 真机教训（2026-09-13）：`response.done` 在 full_duplex 下是**每段一次**，
        # 不是每轮一次；用它当轮末会在段边界把令牌碎片 `<<CALL_QW` 放出去（乱念）。
        # 故真正的轮末只用 listen 事件；`response.done` 之后若文本静默超过该秒数
        # （模型这一轮确实不再说话了），再由 _hold_flush_loop 兜底释放。
        self._hold_idle_secs = float(os.environ.get("OMNI_HOLD_IDLE", "1.5"))
        self._text_idle_deadline = 0.0       # >0 表示有待释放尾巴，到点由静默循环释放

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
        # 主动关掉 WS：接收协程 `async for raw in ws` 会一直阻塞等消息，不关连接
        # gather 永不返回 → 本线程活到最后（stop 会白等满 join 超时 10s，
        # 且 daemon 升级线程等残留物继续跑）。关连接后接收循环立即结束。
        self._close_ws_soon()
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

    def _close_ws_soon(self):
        """把 WS 关闭动作投递到客户端自己的事件循环（跨线程安全）。

        用于 stop()：接收协程阻塞在 `async for raw in ws` 上，只有关闭连接才能让它退出，
        否则 gather 不返回、线程活到 join 超时（实测 stop 会卡满 10s）。
        """
        ws, loop = self._ws, self._loop
        if ws is None or loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(_safe_close(ws))
            )
        except Exception:  # noqa: BLE001
            pass

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
                init_msg = self._build_session_init(ref_b64)
                print(f"[omni] 会话参数：Listen 概率系数={self.listen_prob_scale:.2f}"
                      "（已发送给服务端）", flush=True)
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
                # 4.2) 丢掉采集在启动期间攒下的音频：麦克风线程先于推流循环启动，
                # 而相机初始化要 ~1.5s，期间会攒下一大段——不清掉会「首帧就超水位被裁」，
                # 造成开局音频不连续 + 一条吓人的水位警告。
                self._take_audio()

                # 4.5) P0-b 背压信号：本段已被服务端处理完（首次立即放行，避免开局空等）
                self._chunk_done_ev = asyncio.Event()
                self._chunk_done_ev.set()

                # 5) 推送协程 + 接收协程 + 文本静默兜底协程并发运行，直到停止或断连
                try:
                    await asyncio.gather(
                        self._push_loop(ws),
                        self._receiver_loop(ws),
                        self._hold_flush_loop(),
                    )
                finally:
                    self._stop_capture()
                    self._chunk_done_ev = None
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001
            # WS 断开要给出可诊断的信息：服务端 fail_fast（协议层判定非法输入）会
            # 「发 session.closed 后立刻 ws.close(1000)」，客户端不加解释地报
            # 「received 1000 (OK)」时根本无从下手（真机已踩）。
            try:
                import websockets.exceptions as _wse
                if isinstance(e, _wse.ConnectionClosed):
                    rcvd = getattr(e, "rcvd", None)
                    print(f"[omni-client] ⚠️ WebSocket 关闭：code={getattr(rcvd, 'code', None)} "
                          f"reason={getattr(rcvd, 'reason', None)!r} "
                          f"（1000=对端正常关闭；若上一条日志有 session.closed reason，"
                          f"以那个为准）", flush=True)
            except Exception:  # noqa: BLE001
                pass
            self.cb.on_error(e)
        finally:
            self._ws = None
            self.cb.on_state("closed")

    def _build_session_init(self, ref_b64: str) -> dict:
        """构造符合 llama.cpp-omni 协议的 full-duplex 会话初始化消息。

        服务端 ``parse_session_init`` 只会读取 ``payload.config``，因此采样参数
        不能放在消息顶层；否则后端悄然使用默认 ``listen_prob_scale=1.0``，表现为
        持续输出 ``listen=1``、永远不结束回合。
        """
        return {
            "type": "session.init",
            "payload": {
                "mode": "full_duplex",
                "use_tts": True,
                "voice": {"ref_audio": ref_b64},
                "system_prompt": self.system_prompt,
                # 压低 <|listen|> 采样偏好，避免模型「只听不说」。
                "config": {
                    "listen_prob_scale": float(self.listen_prob_scale),
                },
            },
        }

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

        # 回声门控状态提示：真机验收时一眼确认是否生效（默认按输入/输出两端口自动判定）
        if self.enable_mic:
            in_name, out_name = detect_audio_devices()
            print(f"[omni] 回声门控：{'开启' if self._echo_gate else '关闭'}"
                  f"（{self._echo_gate_reason}）\n"
                  f"[omni] 设备：输入={in_name or '未知'} / 输出={out_name or '未知'}\n"
                  f"[omni] 外放时开启可防 omni 听到自己而自言自语；耳机+独立麦克风时可关闭以保留打断。"
                  f"可用 --no-echo-gate / OMNI_ECHO_GATE 覆盖；自身播报窗口排除策略 "
                  f"OMNI_ECHO_GUARD={self._echo_guard}。",
                  flush=True)

        # 图像上行间隔提示（P1）：真机验收时一眼确认降频是否生效
        if self.enable_camera and not self.video_enabled:
            print("[omni] 图像上行：已关闭（纯音频全双工）。服务端不再做 VPM 编码、"
                  "也不再往 KV 写视觉 token —— 用于隔离验证「视觉 token 吃爆 KV → "
                  "上下文每约 30s 被滑动清空 → 模型照 prompt 示例复读」这条机制。"
                  "开关入口：GUI「图像上行」复选框 / CLI --no-video / OMNI_VIDEO_ENABLED=0。",
                  flush=True)
        if self.enable_camera and self.video_enabled:
            _vi = ("每段都带图（已关闭降频）" if self.video_interval <= 0
                   else f"{self.video_interval:.2f}s/帧")
            print(f"[omni] 图像上行间隔：{_vi}（摄像头刷新 {self.video_fps}fps，上行已解耦）。"
                  f"实测收益：KV 增长降约三成、上下文寿命 +约四成、每轮省下一次 VPM 编码。"
                  f"可用 OMNI_VIDEO_INTERVAL 调整，<=0 退回每段带图。", flush=True)

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
        """麦克风采集循环：持续读取 float32 音频累加到缓冲。

        ⚠️ 死亡必须**大声报出来**：此前 `read()` 抛异常时静默 `break`，采集线程无声退出，
        表现就是「怎么说话 omni 都不回应」，而日志里只有 RMS≈0（很容易被误判成权限问题）。
        真机踩坑：默认输入设备从蓝牙耳机切到内建麦（采样率 48000）时最容易触发。
        """
        while not self._stop_ev.is_set() and self._mic_stream is not None \
                and self._mic_stream.is_active():
            try:
                data = self._mic_stream.read(1024, exception_on_overflow=False)
            except Exception as e:  # noqa: BLE001
                print(f"[omni-client] ⚠️ 麦克风读取失败，采集线程已退出——"
                      f"之后只会上行静音，OMNI 将完全听不到你说话（请重开 OMNI 或换设备）: {e}",
                      flush=True)
                self.cb.on_error(f"麦克风采集中断（之后将听不到声音）: {e}")
                return
            if data:
                with self._mic_lock:
                    self._mic_buf.extend(data)
        # 循环自然退出：停止时属正常；非停止状态退出则说明流被外部关掉了，要报警
        if not self._stop_ev.is_set():
            print("[omni-client] ⚠️ 麦克风采集流已停止（非本程序主动停止），"
                  "OMNI 将听不到声音。", flush=True)

    def _cam_loop(self):
        """摄像头采集循环：按 video_fps 刷新最新帧（jpeg），由推送协程取用。

        图像上行总开关（`video_enabled`）关闭时不产 jpeg——反正没人取用，省掉每帧的
        JPEG 编码开销；BGR 帧照常刷新，GUI 预览与「看着你」的本地画面不受影响。
        """
        interval = 1.0 / max(1, self.video_fps)
        while not self._stop_ev.is_set() and self.camera is not None:
            ret, frame = self.camera.get_frame()
            if ret and frame is not None:
                if self.video_enabled:
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
    async def _wait_chunk_slot(self):
        """P0-b 背压：等上一段被服务端处理完再放行下一段（超时兜底防死等）。

        服务端是串行循环：读一条 input.append → prefill + decode 一步 → 回 listen 或
        response.done。所以「收到任一终局事件」就等于「上一段已消费完」，可以推下一段。
        非背压模式（flow_control=False / 环境变量 OMNI_FLOW_CONTROL=0）退化为固定节拍。

        三轮追加：**最小推流间隔**。即使终局信号提前到达（例如同一段的第二个终局事件，
        或有积压的历史信号），也不允许比 `push_interval` 更密——否则会退化成
        「小块洪泛」：服务端每一轮都要重做图像编码（VPM 190ms，与块大小无关），
        真实消费速率腰斩 → 积压 → 丢帧（真机累计丢 5.1s）。
        """
        ev = self._chunk_done_ev
        if not self._flow_control or ev is None:
            await asyncio.sleep(self.push_interval)
            return
        try:
            await asyncio.wait_for(ev.wait(), timeout=self._chunk_wait_timeout)
        except asyncio.TimeoutError:
            # 服务端未回终局事件（响应慢/无响应/纯 EOF 段）：不阻塞，按超时继续推
            pass
        ev.clear()
        if self._min_push_interval > 0:
            gap = time.monotonic() - self._last_push_ts
            if gap < self._min_push_interval:
                await asyncio.sleep(self._min_push_interval - gap)

    async def _wait_min_chunk(self):
        """等采集缓冲攒够「最小块时长」，避免发出极小块白烧服务端一轮算力。

        为什么值得等：服务端每一轮都要把随帧图像重新编码（VPM 约 190ms）+ 跑一次
        decode，**这个成本与音频块大小无关**。音频以每秒 1 秒的速度累积，所以
        「每块至少 0.4s」不会降低实时性（本来也推不更快），却能让每轮算力都被有效利用。
        上限 `min_chunk_secs + 0.1s`：麦克风异常（不再产出数据）时不能死等，
        交给 `_push_loop` 的补静音兜底保证报文合法。
        """
        need = int(self._min_chunk_secs * TARGET_SR) * 4
        if need <= 0:
            return
        deadline = time.monotonic() + self._min_chunk_secs + 0.1
        while not self._stop_ev.is_set() and time.monotonic() < deadline:
            with self._mic_lock:
                have = len(self._mic_buf)
            if have >= need:
                return
            await asyncio.sleep(0.02)

    def _should_attach_frame(self, now: float) -> bool:
        """本段是否附带视频帧（P1 图像降频：默认 video_interval 秒一帧）。

        最高优先级是总开关 `video_enabled`：为 False 时本方法永远返回 False（一个视频帧
        都不发），用于 P0 变量分离实验——坐实「视觉 token 吃爆 KV → 上下文每约 30s 被
        滑动清空 → 模型照 prompt 示例复读『查电池』」这条机制。

        为什么把「图像上行」与「音频上行」解耦：服务端每一段都要为随帧图像做一次 VPM
        编码（约 190ms）并把约 64 个视觉 token 写进 KV，而一段音频只有个位数 token。
        带图频率越高，KV 越快被视觉 token 填满——真机实测每约 30 秒就触发一次上下文
        滑动，滑掉之后模型只剩 system prompt，于是照 `prompts.py` 里的示例复读令牌
        （「查询一下最近的新闻」就是这么来的）。降到 1 帧/秒后模型仍能「看着你」，
        但 KV 增长降约三成、上下文寿命 +约四成，并让每轮少一次 VPM 编码。

        Args:
            now: 当前 monotonic 时刻。

        Returns:
            bool: True=本段带图（并刷新计时），False=本段只上音频。
        """
        if not self.video_enabled:
            return False                     # 图像上行总开关关闭：永不带图（P0 变量分离实验）
        if self.video_interval <= 0:
            return True                      # <=0：退回旧行为（每段都带图，便于对照排查）
        if self._last_frame_ts <= 0.0:
            self._last_frame_ts = now
            self._frames_sent += 1
            return True
        if now - self._last_frame_ts >= self.video_interval:
            # 计时「累加」而非「赋值」：块节奏（0.4~0.9s）不是间隔的整数倍，
            # 赋值会让长期平均变成 0.6~0.8 帧/秒；累加可让长期均值准确落在 1/间隔。
            # 落后超过 2 个间隔（例如刚开始推流/长时间暂停）则重新对齐，避免连发补帧。
            self._last_frame_ts += self.video_interval
            if now - self._last_frame_ts > 2 * self.video_interval:
                self._last_frame_ts = now
            self._frames_sent += 1
            return True
        return False

    def _take_audio(self):
        """取出本轮要上行的音频；超过水位就丢最旧的（保证「模型听到的是现在」）。

        非背压模式下音频会累积到超过水位（真机曾落后 27s），此时**丢最旧的保最新的**：
        宁可让模型听到一段有断点的音频，也不能让它对着半分钟前的声音回答。
        """
        with self._mic_lock:
            audio = bytes(self._mic_buf)
            self._mic_buf = bytearray()
        max_bytes = int(self._max_buf_secs * TARGET_SR) * 4
        if max_bytes > 0 and len(audio) > max_bytes:
            dropped = len(audio) - max_bytes
            self._dropped_secs += dropped / 4 / TARGET_SR
            print(f"[omni-client] ⚠️ 上行积压超水位 "
                  f"({len(audio) / 4 / TARGET_SR:.2f}s > {self._max_buf_secs:.2f}s)，"
                  f"丢弃最旧的 {dropped / 4 / TARGET_SR:.2f}s 以保住实时"
                  f"（累计已丢 {self._dropped_secs:.1f}s）", flush=True)
            audio = audio[-max_bytes:]
        return audio

    async def _push_loop(self, ws):
        """实时推流循环：按「服务端消费得过来」的节奏上行音频 + 最新帧。

        关键点 1（P0-b）：不再是雷打不动每 0.4s 推一次，而是**一段在飞**——等服务端把
        上一段 prefill+decode 完（listen / response.done 任一到达即算完）再推下一段。
        服务端慢时自动放慢节拍（单段音频变长），但延迟不再累积；配合水位丢帧兜底。
        关键点 2：累积的音频量 ≈ 真实录音，保证按实时节奏喂给模型，避免全双工下
        「只听不说」。同时回报麦克风 RMS 用于诊断，持续静音（RMS≈0）时周期警告，
        帮助排查「用户说话但 omni 无反应」这类问题。
        """
        silent_secs = 0.0
        t_last = time.monotonic()
        while not self._stop_ev.is_set():
            await self._wait_chunk_slot()
            if self._stop_ev.is_set():
                break

            await self._wait_min_chunk()          # 攒够最小块时长，别发极小块白烧服务端算力
            audio = self._take_audio()
            # 每一帧都必须带音频：服务端 full_duplex 分支拿到空音频会
            # `fail_fast("missing_audio")` → 直接发 session.closed 并关掉整个 WS
            # （真机已复现：采集侧一断，首帧无音频就把会话打死，表现为「怎么说话都不回」）。
            # 缓冲空时先短暂等采集线程产出数据，仍为空则补 0.1s 静音（等价「这帧没听到人」）。
            if not audio:
                for _ in range(8):
                    await asyncio.sleep(0.02)
                    audio = self._take_audio()
                    if audio:
                        break
            if not audio:
                audio = b"\x00" * (int(0.1 * TARGET_SR) * 4)
                if self._debug:
                    print("[omni-client][debug] 采集缓冲为空，补 0.1s 静音以保证 "
                          "input.append 合法（否则服务端会 fail_fast 关会话）", flush=True)
            now = time.monotonic()
            elapsed = max(1e-3, now - t_last)     # 本轮实际间隔（背压下会随服务端变长）
            t_last = now
            self._last_push_ts = now
            self._chunk_seq += 1

            # 计算麦克风音量（RMS + 峰值）：两者都用于「是否有人声」判定与诊断。
            # 单看 RMS 不可靠——真机实测底噪 RMS 0.002~0.013 与人声 RMS 0.020~0.056 只差
            # 3~5 倍，阈值 0.02 正压在人声段下沿，人声帧会在阈值附近来回抖；而人声峰值
            # 0.088~0.277 与底噪分离干净，故用「峰值≥阈值 或 RMS≥阈值」的双条件。
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
            peak = float(np.max(np.abs(arr))) if audio and arr.size else 0.0
            self.cb.on_mic_level(rms)
            self._last_mic_level = rms        # 缓存供 GUI 音量条轮询

            # ---- 回声门控：J.A.C. 正在说话时，麦克风采集到的其实是自己的声音 ----
            # 不门控的话 omni 会把自己的语音当成用户发言，自问自答并幻觉出升级任务。
            # 门控方式：用等长零字节替代真实采集推送——既让 omni 听到「环境安静」，
            # 又保持「1 秒音频 / 1 秒墙钟」的实时节奏（喂太快会导致只听不说）。
            echoing = bool(self._echo_gate and self._is_echoing())
            self._echo_gated = echoing
            if echoing:
                audio, is_speech = self._apply_echo_gate(audio, rms)
            else:
                # 检测到真实人声就刷新时间戳，供升级令牌护栏判定。
                # 双条件（峰值 或 RMS）：单看 RMS 时阈值 0.02 正压在人声段下沿，
                # 真机实测人声帧会在阈值附近来回抖（日志同一秒内「检测到人声 0.023」
                # →「进入静音 0.004」），导致"最近有人声"这条判据不可靠、真实提问被误杀。
                is_speech = self._is_speech_frame(rms, peak)
            if is_speech:
                self._last_speech_ts = time.monotonic()
            # 推流诊断日志治理（#4）：默认只在「人声↔静音」状态翻转时打印一行，
            # 避免每 ~0.4s 刷屏；仅当逐块诊断开关 self._debug 打开时才打印 RMS 诊断详情
            # （GUI「上行调试日志」复选框 / CLI --debug / OMNI_DEBUG=1 三个入口）。
            prev_state = getattr(self, "_last_speech_state", None)
            if is_speech != prev_state:
                if is_speech:
                    print(f"[omni-client] 🎙 检测到人声（RMS={rms:.3f} 峰值={peak:.3f} "
                          f"判据={self._speech_peak_th:.2f}/{self._speech_rms_th:.2f}）",
                          flush=True)
                else:
                    print(f"[omni-client] 进入静音（RMS={rms:.3f} 峰值={peak:.3f}）", flush=True)
                self._last_speech_state = is_speech
            # 逐块诊断仅在显式开启时打印，便于排查「说话但 omni 无反应」类问题
            if self._debug:
                print(f"[omni-client][debug] 推流#{self._chunk_seq} "
                      f"间隔={elapsed:.2f}s {'人声' if is_speech else '静音'} "
                      f"RMS={rms:.3f} 峰值={peak:.3f} 块={len(audio)}B "
                      f"距上次人声={self._seconds_since_speech():.1f}s", flush=True)
            if rms <= 1e-4:
                silent_secs += elapsed
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
            # P1 图像降频：音频每段都上，图像默认 1 秒才带一帧。
            # 带图的那一轮服务端要多做一次 VPM 编码（约 190ms）并往 KV 塞约 64 个视觉
            # token——这是 KV 被快速填满、上下文每约 30 秒被滑动清空的主因。
            attach_frame = bool(jpg is not None) and self._should_attach_frame(now)
            if attach_frame:
                payload["video_frames"] = [base64.b64encode(jpg).decode("ascii")]
            elif self._debug:
                print(f"[omni-client][debug] 推流#{self._chunk_seq} 本段不带图"
                      f"（图像间隔 {self.video_interval:.2f}s，已带 {self._frames_sent} 帧）",
                      flush=True)

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
                        self._flush_text_holdback()   # 轮末释放 P0-a 扣留的尾巴（未升级时）
                        self.cb.on_listen()
                        # 升级已完成且主会话回到聆听态 → 解除静音，恢复正常播报
                        with self._audio_lock:
                            if self._suppress_audio and self._escalation_done:
                                self._suppress_audio = False
                        # 新一轮聆听开始 = 上一轮对话已结束 → 复位升级标志，
                        # 允许本轮再次触发 <<CALL_QWEN>>（修复"第二次升级被吞"）
                        self._reset_escalation_state()
                        # P0-b：本段已消费完，放行下一段（按 response_id 去重，
                        # 因为紧随其后的 response.done 属于同一段）
                        self._signal_chunk_done(e.get("response_id"))
                elif et == "response.done":
                    txt = e.get("text", "")
                    if txt:
                        self.cb.on_text_final(txt)
                    # ⚠️ 这里**不能**释放 P0-a 的 holdback：full_duplex 下 `response.done`
                    # 是「本段（chunk）处理完」而非「本轮流说完」——模型接着说会跨很多段，
                    # 段边界完全可能落在一个正在下发的令牌中间。真机已复现：在段边界释放
                    # 会把扣留的 `<<CALL_QW` 当普通文本播出去（乱念碎片），并且把升级任务
                    # 截断成「查一下」。真正的轮末信号是 listen 事件（模型切回聆听）。
                    # 兜底释放交给 `_hold_flush_loop` 的「文本静默超时」。
                    # M7b：本段残留文本先让桥接播完（提升实时感；桥接自身也会扣留令牌前缀，
                    # 所以这里既不会念出碎片，也不会丢字）
                    if (self._voicebox_bridge is not None
                            and not self._call_qwen_fired and not self._token_seen):
                        self._voicebox_bridge.flush_remaining()
                    da = e.get("audio")
                    if da and self._voicebox_bridge is None:
                        try:
                            self._emit_audio(base64.b64decode(da))
                        except Exception:
                            pass
                    # P0-b：本段已消费完，放行下一段（与上面 listen 分支同一段时会被去重忽略）
                    self._signal_chunk_done(e.get("response_id"))
                elif et == "session.closed":
                    # 把服务端给的关闭原因打出来：服务端 fail_fast 会「发 session.closed
                    # 后立刻 ws.close()」，客户端只看到 closed 会完全摸不着头脑
                    # （如 missing_audio = 我们发了一帧没有音频的 input.append）。
                    print(f"[omni-client] ⚠️ 服务端关闭会话：reason={e.get('reason')!r}"
                          f"（若为 missing_audio / invalid_input / mode_mismatch，"
                          f"属协议层 fail_fast，不是模型问题）", flush=True)
                    # P0-a：连接关闭也释放一次尾巴（保证最后一句能显示完整）
                    self._flush_text_holdback()
                    # M7b：连接关闭时 flush 尾句（未升级、未发现令牌时）
                    if (self._voicebox_bridge is not None
                            and not self._call_qwen_fired and not self._token_seen):
                        self._voicebox_bridge.flush_remaining()
                    self.cb.on_state("closed", e.get("reason"))
                    self._signal_chunk_done()         # 放行（避免推流协程空等后继续发）
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
            # omni 自带 TTS 走 PyAudio 流、不经 playback.play_wav，需登记播放窗口，
            # 否则回声门控感知不到「J.A.C. 正在说话」（按字节数估算持续时长）。
            try:
                _playback.mark_external_playback(len(pcm_bytes) / 4 / TARGET_SR)
            except Exception:  # noqa: BLE001
                pass
            self.player.play(pcm_bytes)

    # ============================================================ M2 升级路由
    def _on_text(self, txt: str):
        """文本增量处理：先做 <<CALL_QWEN>> 令牌检测，再把可信文本广播 / 送朗读。

        **关键修复 1**：令牌检测必须在把文本喂给 Voicebox 桥接之前完成——否则承载
        `<<CALL_QWEN>>查电池` 的 delta 会先被送进朗读队列（即"把问题本身读出来"）。

        **关键修复 2（P0-a，2026-09-13）**：omni 是**按 token 逐片**下发文本的，
        `<<CALL_QWEN>>` 必然被切成 `<<CALL_Q` 这类碎片；碎片到达时缓冲里还没有完整令牌，
        于是走了「正常对话」分支被广播并朗读出去（Voicebox 里那段 `<<CALL_Q` 怪音即由此
        而来）。现对缓冲末尾「可能是令牌前缀」的字符做 holdback：不外发、不朗读，
        等下一片到齐确认构不成令牌后再放行。

        **关键修复 3（2026-09-13 三轮）**：①令牌匹配改为**容忍空白/换行/大小写**的变体
        （bo s s 已确认模型会吐 `<<CALL_ QWEN>>`，空格来自模型本身）；②命中令牌后把
        令牌本身**从缓冲里消费掉**（而不是等 listen 事件把整个缓冲清空）——listen 在
        full_duplex 下是**每段**都会来的信号，用它清缓冲会把正在下发的令牌拦腰截断，
        剩下的裸片段（如 `QWEN>>`）就会被当普通对话朗读，这正是真机「同一句被反复念」
        的机制。

        **显示治理（2026-09-06）**：令牌之后的文本是「发给大脑的内部任务描述」，
        不是要说给用户听的话，一律既不朗读也不显示。
        """
        # 诊断：原始 delta 的 repr（OMNI_DEBUG=1 时开启），用于坐实畸形令牌的确切形态
        if self._debug:
            print(f"[omni-client][debug] text delta repr={txt!r}", flush=True)
        # 1) 令牌已发现：后续 delta 均为任务描述（内部指令），不朗读、不显示
        if self._token_seen:
            self._pending_task = (self._pending_task or "") + txt
            self._try_finalize_pending()
            return
        # 2) 升级已触发：主会话后续文本（含 omni 尾随回复）不再朗读
        if self._call_qwen_fired:
            return

        # 3) 累积全文用于跨分片令牌检测（先累积、后广播，才能把令牌之后的内容截掉）
        self._text_buf += txt
        self._trim_text_buf()
        m = find_call_token(self._text_buf)
        if m is None:
            # 尚未出现完整令牌：扣掉「可能是令牌前缀」的尾巴，只把安全部分外发
            hold = _token_prefix_suffix_len(self._text_buf)
            safe_len = len(self._text_buf) - hold
            emit = self._text_buf[self._shown_len:safe_len]
            if emit:
                self._shown_len = safe_len
                self._emit_text(emit)
            # 有扣留就登记静默兜底截止时间：段末不再释放（段末≠轮末），
            # 只有「模型这轮确实不再说话」时才由 _hold_flush_loop 放行
            self._text_idle_deadline = (
                time.monotonic() + self._hold_idle_secs if hold else 0.0
            )
            return

        # 命中令牌：只把「令牌之前、且尚未外发过」的部分显示 + 朗读（绝不重复、
        # 也不显示令牌本身与其后的任务描述——否则用户会看到 omni 自言自语的幻觉任务）
        emit = self._text_buf[self._shown_len:m.start()]
        if emit:
            self._emit_text(emit)

        # 命中令牌：**消费掉令牌及其之前的内容**。缓冲使命结束（后续 delta 由
        # `_token_seen` 分支累积进 `_pending_task`），这样既避免同一令牌被反复命中
        # 导致重复触发，也不依赖 listen 事件清缓冲（listen 每段都会来，会拦腰切断令牌）。
        tail = self._text_buf[m.end():]
        self._text_buf = ""
        self._shown_len = 0
        self._text_idle_deadline = 0.0

        # 命中令牌：先判幻觉——若令牌出现前「最近 window 秒内无真实人声」，
        # 判定为 omni 在静音期幻觉生成的自触发任务（真机已复现：纯静音段 RMS≈0.003
        # 却凭空生成"查电池电量"并自动执行）。幻觉时不触发升级、不静音，仅丢弃该任务。
        if not self._has_recent_speech():
            self._token_seen = True      # 停止朗读/显示，但不静音、不触发升级
            self._hallucinated = True    # 后续任务描述即使出现句号也不得 fire（防偷偷升级）
            reason = "播放回声期" if self._is_echoing() else "静音期"
            print(f"[omni-client] ⚠️ 升级令牌疑似{reason}幻觉，已拦截丢弃"
                  f"（距上次人声 {self._seconds_since_speech():.1f}s > 窗口 "
                  f"{self._speech_window:.1f}s）", flush=True)
            return
        # 命中令牌：标记已发现，停止后续朗读
        self._token_seen = True
        # 令牌及后续任务描述：跨换行累积，交由 _try_finalize_pending 在命中句末标点时
        # 结算触发；若模型迟迟不给标点，由 1.5s 兜底定时器兜底，避免升级永不触发。
        self._pending_task = tail
        self._try_finalize_pending()
        if not self._call_qwen_fired:
            if self._pending_timer is None or not self._pending_timer.is_alive():
                self._pending_timer = threading.Timer(1.5, self._finalize_pending)
                self._pending_timer.daemon = True
                self._pending_timer.start()

    def _trim_text_buf(self):
        """惰性裁掉 `_text_buf` 里「已外发过的前缀」，防止它随会话无限增长。

        只在已外发长度足够大时才裁，且**不碰未外发的尾巴**（holdback 扣留区必须完整保留），
        所以既能控内存，又不会漏字或让令牌被切断。
        """
        if self._shown_len <= 512:
            return
        drop = self._shown_len - 128
        self._text_buf = self._text_buf[drop:]
        self._shown_len -= drop

    def _emit_text(self, text: str):
        """把「可信对话文本」外发：显示 + 送 Voicebox 朗读（统一过硬安全网）。

        硬安全网（`sanitize_for_speech`）：任何含 `<` / `>>` 或裸标记词（`QWEN`）的片段
        一律截掉——语音助手的正常输出不可能含这些，留着只可能是畸形令牌残留，
        宁可少说几个字也绝不把内部标记念出来（真机曾反复念出 `<<CALL_QW`）。
        """
        safe = sanitize_for_speech(text)
        if not safe:
            if text:
                print(f"[omni-client] 🛡 已拦截疑似标记残留（不显示/不朗读）: {text!r}",
                      flush=True)
            return
        self._broadcast(safe)
        if self._voicebox_bridge is not None:
            self._voicebox_bridge.feed(safe)

    def _flush_text_holdback(self):
        """轮末释放 P0-a 扣留在缓冲末尾的文本（确认它终究不是令牌前缀）。

        只在「没发现令牌、没触发升级」时释放：一旦命中令牌，尾巴属于内部任务描述，
        必须继续藏着（既不显示也不朗读）。
        """
        if self._token_seen or self._call_qwen_fired:
            return
        tail = self._text_buf[self._shown_len:]
        if not tail:
            return
        self._shown_len = len(self._text_buf)
        self._emit_text(tail)
        self._text_idle_deadline = 0.0

    async def _hold_flush_loop(self):
        """文本静默兜底：模型这一轮不再吐字后，才释放被 holdback 扣住的尾巴。

        为什么需要：full_duplex 下 `response.done` 是每段一次、不是每轮一次，
        段末不能释放 holdback（否则段边界正好落在令牌中间就会把 `<<CALL_QW` 念出去，
        真机已复现）。真正的轮末信号是 listen 事件；万一某轮模型说完却不回 listen
        （以 `__END_OF_TURN__` 收尾），就靠这里「静默超过 _hold_idle_secs」兜底放行。
        跑在事件循环里（与接收协程同线程），避免与 `_text_buf` 的跨线程读写竞态。
        """
        while not self._stop_ev.is_set():
            await asyncio.sleep(0.2)
            deadline = self._text_idle_deadline
            if deadline and time.monotonic() >= deadline:
                self._text_idle_deadline = 0.0
                self._flush_text_holdback()
                if (self._voicebox_bridge is not None
                        and not self._call_qwen_fired and not self._token_seen):
                    self._voicebox_bridge.flush_remaining()

    def _broadcast(self, text: str):
        """把文本广播给回调（控制台 / GUI 实时文字区）并累积到回复缓存。

        只显示「可信的主对话文本」：令牌及其之后的任务描述不经过这里，
        避免用户看到 omni 自言自语生成的内部指令（如「给您推荐一部电」）。

        注：本方法**不**负责推进 `_shown_len`——调用侧按需自行推进（P0-a 引入 holdback
        后，「已外发位置」与「缓冲末尾」不再相等，由调用侧精确控制才不会漏字或重字）。

        Args:
            text: 待显示的文本片段（已在调用侧裁掉令牌及之后的部分）。
        """
        if not text:
            return
        self.cb.on_text_delta(text)
        # 累积回复文本供 GUI 实时文字区轮询（限长，避免无限增长）
        with self._reply_lock:
            self._reply_buf += text
            if len(self._reply_buf) > 4000:
                self._reply_buf = self._reply_buf[-2000:]

    def _signal_chunk_done(self, response_id=None):
        """P0-b：标记「本段已被服务端消费完」，放行推流协程发下一段。

        ⚠️ 必须按 `response_id` 去重（2026-09-13 三轮真机踩坑）：服务端
        「模型说了话、然后切回聆听」的这一段会**先发 listen delta 再发 response.done**，
        而两者共用同一个 `response_id`（`ws_handler.cpp:1173` / `:1232` 传的是同一个 id）。
        若不按 id 去重，一段会被算作两次完成 → 推流次数翻倍、每块音频变小 → 而服务端
        每轮都要重做图像编码（VPM 190ms，**与块大小无关**）→ 真实消费速率腰斩 →
        积压超水位丢帧（真机累计丢 5.1s，音频被切碎后模型只能听到残句）。

        Args:
            response_id: 服务端下行事件里的 response_id；缺失时退回「只认第一次」的无脑放行。
        """
        if response_id:
            if response_id == self._last_chunk_resp_id:
                return                      # 同一段的第二个终局事件，忽略
            self._last_chunk_resp_id = response_id
        ev = self._chunk_done_ev
        if ev is not None:
            ev.set()

    def _apply_echo_gate(self, audio: bytes, rms: float):
        """回声门控：把本帧真实采集替换成等长静音，并判定为「非人声」。

        用等长零字节而非跳过推送，是为了保持「1 秒音频 / 1 秒墙钟」的实时节奏——
        全双工下喂太快会导致模型只 LISTEN 不 SPEAK。

        Args:
            audio: 本帧采集到的 16k float32 原始字节。
            rms: 本帧实测 RMS（仅用于 debug 日志，不参与替换）。

        Returns:
            tuple[bytes, bool]: (替换后的音频字节, 是否计为真实人声)
        """
        if self._debug:
            print(f"[omni-client][debug] 回声门控生效（正在播报）"
                  f" 麦克风已按静音推送 实测RMS={rms:.3f}", flush=True)
        out = (b"\x00" * len(audio)) if audio else audio
        return out, False

    def _is_echoing(self) -> bool:
        """当前采集到的音频是否大概率是「J.A.C. 自己的声音」（TTS 回声）。

        判定：全局播放状态显示正在出声，或距上次播放结束不足 _echo_tail 秒（房间混响 /
        系统音频缓冲未排空的拖尾期）。

        Returns:
            bool: True 表示当前处于回声窗口，采集内容不可信。
        """
        if _playback.is_playback_active():
            return True
        try:
            return _playback.seconds_since_playback_end() < self._echo_tail
        except Exception:  # noqa: BLE001
            return False

    def _is_speech_frame(self, rms: float, peak: float) -> bool:
        """单帧是否计为「检测到人声」：峰值 或 RMS 任一达标即算。

        为什么用双条件（2026-09-13 三轮）：真机实测同一台机器上——
          底噪 RMS 0.002~0.013 / 人声 RMS 0.020~0.056（只差 3~5 倍，阈值 0.02 正压在
          人声段下沿，人声帧会在阈值附近来回抖，日志里同一秒内「检测到人声 0.023」
          →「进入静音 0.004」）；
          而人声峰值 0.088~0.277 与底噪分离干净。
        单看 RMS 会让「最近有人声」这条护栏判据不可靠，把**真实提问**误判成静音期幻觉
        （真机三轮：真实提问被拦截、升级任务丢失）。

        Args:
            rms: 本帧 RMS。
            peak: 本帧峰值（abs 最大值）。

        Returns:
            bool: True=本帧计为人声，会刷新护栏的「最近人声」时间戳。
        """
        return (peak >= self._speech_peak_th) or (rms >= self._speech_rms_th)

    def _seconds_since_speech(self) -> float:
        """距最近一次检测到真实人声的秒数（从未检测到则为一个大数，便于日志展示）。"""
        if self._last_speech_ts <= 0.0:
            return float("inf")
        return time.monotonic() - self._last_speech_ts

    def _has_recent_speech(self) -> bool:
        """升级令牌护栏：令牌出现前「最近 window 秒内是否检测到真实人声」。

        返回 True 表示令牌大概率源于用户真实发言（可信触发），False 表示静音期
        幻觉（应拦截）。判定依据：_push_loop 在每帧「峰值≥阈值 或 RMS≥阈值」时
        刷新 _last_speech_ts。

        ⚠️ 三轮修正（2026-09-13）：
          1. **窗口 3.0s → 6.0s**：令牌到达时间 = 音频积压 + 服务端一轮 prefill/decode
             + TTS 排队，端到端远超 3s；窗口小于链路延迟时，**真实提问会被判成幻觉**
             （真机已复现：同一份日志里一条令牌放行、随后几条又被拦截，判据自相矛盾）。
          2. **自身播报窗口的排除与门控开关解耦**：此前写作 `if self._echo_gate and
             self._is_echoing()`，耳机场景门控为关时这条判据被整体短路，等于丢掉了
             唯一能识别「这是我自己在说话」的手段。现默认无论门控开关都排除播报窗口
             （OMNI_ECHO_GUARD=gate 可退回旧行为）。
        """
        # 回声窗口（正在播报 / 刚播报完）：此时 omni 听到的其实是自己的声音，
        # 由此产生的「任务」一律视为幻觉（真机已复现：TTS 播放期间 RMS 0.022 被误判为
        # 人声，omni 随即吐出 <<CALL_QWEN>>给您推荐一部电）。
        if self._is_echoing() and self._echo_guard != "gate":
            return False
        if self._echo_gate and self._is_echoing():
            return False
        # 全程从未检测到人声（如开局模型自言自语）：必为幻觉
        if self._last_speech_ts <= 0.0:
            return False
        return self._seconds_since_speech() <= self._speech_window

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
        if self._call_qwen_fired or self._hallucinated:
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
        if self._call_qwen_fired or self._hallucinated:
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

        ⚠️ **只丢掉已外发的前缀，必须保留扣留中的尾巴**（2026-09-13 三轮修正）：
        此前无条件 `_text_buf = ""`，而 listen 在 full_duplex 下是**每段**都会来的信号，
        于是「正在下发的令牌」会被拦腰截断——剩下的裸片段（`QWEN>>`）既无法匹配令牌
        （用户请求静默丢失），又可能被当普通对话朗读（真机「同一句被反复念」）。
        现在保留未外发的尾巴，让跨 listen 的令牌仍能拼齐并正常触发升级。
        """
        self._call_qwen_fired = False
        self._token_seen = False
        self._hallucinated = False
        # 保留未外发的尾巴（holdback 扣留区），丢掉已显示过的前缀
        self._text_buf = self._text_buf[self._shown_len:]
        self._shown_len = 0
        self._pending_task = None
        # 尾巴若还在扣留中，重新登记静默兜底截止时间，避免它永远不被释放
        self._text_idle_deadline = (
            time.monotonic() + self._hold_idle_secs if self._text_buf else 0.0
        )
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


# 输出设备类型识别关键词（macOS 中英文设备名均覆盖）
# 耳机/蓝牙类：声音不会外泄到麦克风 → 无需回声门控，可保留「随时打断」能力
_HEADPHONE_HINTS = ("耳机", "headphone", "headset", "airpods", "earpods", "earbuds",
                    "蓝牙", "bluetooth")
# 扬声器类：外放会被麦克风回采 → 必须开启回声门控，否则 omni 会听到自己而自言自语
_SPEAKER_HINTS = ("扬声器", "speaker", "内建输出", "built-in", "internal", "monitor")


def _device_name(p, kind: str) -> str:
    """读取默认输入/输出设备名（失败返回空串，绝不抛错）。"""
    try:
        info = (p.get_default_input_device_info() if kind == "input"
                else p.get_default_output_device_info())
        return str(info.get("name", ""))
    except Exception:  # noqa: BLE001
        return ""


def detect_audio_devices():
    """返回 (默认输入设备名, 默认输出设备名)，供门控判定与启动日志使用。

    三轮修正（2026-09-13）的动机：门控自动判定此前**只看输出设备名**——只要输出是耳机
    就关掉门控。但如果**输入也是同一个耳机/蓝牙设备**，耳机麦克风会采到耳机自己的输出
    （强回声），这时关掉门控等于放任自激。所以判定必须同时看两边。

    Returns:
        tuple[str, str]: (输入设备名, 输出设备名)；取不到时为 ""。
    """
    try:
        p = pyaudio.PyAudio()
        try:
            return _device_name(p, "input"), _device_name(p, "output")
        finally:
            p.terminate()
    except Exception:  # noqa: BLE001
        return "", ""


def _is_headphone_like(name: str) -> bool:
    """设备名是否像耳机/蓝牙（大小写不敏感）。"""
    n = (name or "").lower()
    return any(h in n for h in _HEADPHONE_HINTS)


def detect_headphones() -> bool:
    """检测系统默认输出设备是否为耳机 / 蓝牙（即硬件层面是否隔离回声）。

    保留此函数是为了兼容既有调用/测试；门控判定请用 `resolve_echo_gate`。

    Returns:
        bool: True=耳机类输出；False=扬声器外放或无法判定（保守按外放处理）。
    """
    _, out_name = detect_audio_devices()
    if _is_headphone_like(out_name):
        return True
    if any(h in out_name.lower() for h in _SPEAKER_HINTS):
        return False
    return False      # 无法判定：保守按外放处理（宁可牺牲打断，也不要自激）


def resolve_echo_gate(pref=None):
    """解析回声门控最终开关；auto / None 时按「输入 + 输出」两个设备自动判定。

    bo s s 的使用约定：戴耳机（硬件隔离回声）→ 关闭门控以保留打断能力；
    用内建扬声器外放 → 开启门控，否则 omni 会听到自己的声音而自言自语。

    三轮修正（2026-09-13）：auto 判定规则改为同时看两端口——
      - 输出是耳机 **且** 输入不是耳机类 → 关（真隔离，可放心保留打断）；
      - **输入本身是耳机/蓝牙 → 开**（耳机麦会采到耳机自己的输出，关掉即自激，
        历史日志里默认输入被切到 AirPods 时正是这个情形）；
      - 输出是扬声器 / 任一端无法判定 → 开（保守）。

    Args:
        pref: True/False 手动指定；None 或 "auto" 表示按设备自动检测；
              字符串 "1/on/true" 强制开，"0/off/false" 强制关。

    Returns:
        tuple[bool, str]: (是否启用门控, 人类可读的原因，用于启动日志)
    """
    if pref is None or (isinstance(pref, str) and pref.strip().lower() in ("auto", "")):
        in_name, out_name = detect_audio_devices()
        out_phone = _is_headphone_like(out_name)
        in_phone = _is_headphone_like(in_name)
        if out_phone and not in_phone:
            return False, (f"自动检测：输出是耳机/蓝牙（{out_name or '未知'}）且输入是独立麦克风"
                           f"（{in_name or '未知'}）→ 硬件已隔离回声，关闭门控（可随时打断）")
        if in_phone:
            return True, (f"自动检测：输入设备本身是耳机/蓝牙（{in_name}），其麦克风会采到"
                          f"耳机自己的输出 → 开启门控防自激；想让 J.A.C. 被打断可换独立麦克风")
        return True, (f"自动检测：输出为扬声器或无法判定（输入={in_name or '未知'}，"
                      f"输出={out_name or '未知'}）→ 开启门控防自激")
    if isinstance(pref, str):
        v = pref.strip().lower()
        if v in ("1", "on", "true", "yes"):
            return True, "手动指定：开启"
        if v in ("0", "off", "false", "no"):
            return False, "手动指定：关闭（可打断）"
        return resolve_echo_gate(None)      # 无法识别的字符串 → 回退自动
    return bool(pref), "手动指定"


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
