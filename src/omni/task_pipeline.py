"""Gateway 音频旁路：有界 VAD、离线 Whisper、用户指令校验与只读 Qwen。"""

from collections import Counter, deque
from dataclasses import dataclass
import queue
import re
import threading
import time
import unicodedata
from uuid import uuid4

import numpy as np

from .transcription_worker import WhisperProcess, DEFAULT_MODEL_DIR


@dataclass
class Utterance:
    """单会话内完整语音；原始 PCM 只在有界内存中存活。"""

    identifier: str
    generation: int
    ended_at: float
    samples: np.ndarray
    overlap: bool


class VadSegmenter:
    """WebRTC 30ms 判定，300ms 预留、600ms 句末、12s 上限。"""

    def __init__(self, detector=None):
        """创建 VAD；detector 仅供离线测试替换语音判定。"""
        if detector is None:
            import webrtcvad
            detector = webrtcvad.Vad(2)
        self.detector = detector
        self.reset()

    def reset(self):
        """重连/溢出时丢弃未结束语音，绝不把跨会话音频拼成指令。"""
        self.pending = np.zeros(0, dtype="float32")
        self.pending_overlap = False
        self.pre = deque(maxlen=10)
        self.frames, self.voiced, self.quiet = [], 0, 0
        self.overlap, self.discarding = False, False

    def feed(self, samples, overlap=False, ended_at=None):
        """在 VAD 线程切片；返回完整句子，长句拒绝而不拆成多个任务。"""
        ended_at = time.monotonic() if ended_at is None else ended_at
        samples = np.asarray(samples, dtype="float32")
        if samples.ndim != 1 or not np.isfinite(samples).all():
            raise ValueError("VAD 音频格式错误")
        self.pending = np.concatenate((self.pending, samples))
        blocked = overlap or self.pending_overlap
        results = []
        while len(self.pending) >= 480:
            frame, self.pending = self.pending[:480], self.pending[480:]
            pcm16 = (np.clip(frame, -1, 1) * 32767).astype("<i2").tobytes()
            speech = float(np.sqrt(np.mean(frame * frame))) >= .005 and self.detector.is_speech(pcm16, 16000)
            if self.discarding:
                self.quiet = 0 if speech else self.quiet + 1
                if self.quiet >= 20:
                    self.discarding, self.quiet, self.voiced, self.overlap = False, 0, 0, False
                continue
            if not self.frames:
                if not speech:
                    self.pre.append((frame.copy(), blocked))
                    continue
                self.frames = [f for f, _ in self.pre]
                self.overlap = False
                self.pre.clear()
            self.frames.append(frame.copy())
            self.overlap |= blocked and speech
            self.voiced += bool(speech)
            self.quiet = 0 if speech else self.quiet + 1
            if len(self.frames) >= 400:
                self.frames = []
                self.pre.clear()
                self.discarding, self.quiet = True, 0
                continue
            if self.quiet >= 20:
                if self.voiced >= 10:
                    results.append((np.concatenate(self.frames), self.overlap,
                                    ended_at - len(self.pending) / 16000))
                self.frames, self.voiced, self.quiet, self.overlap = [], 0, 0, False
        self.pending_overlap = overlap if len(self.pending) else False
        return results


def normalize_transcript(text):
    """固定指令词做繁简兼容和标点归一，不使用会诱导 Whisper 的示例 prompt。"""
    text = unicodedata.normalize("NFKC", text).lower()
    table = str.maketrans("電腦查詢狀態報時當餘負載記憶體製這幫請給並寫現點鐘內謝檢個還", "电脑查询状态报时当余负载记忆体制这帮请给并写现点钟内谢检个还")
    return re.sub(r"[\s，。！？、,.!?：:；;]", "", text.translate(table)).replace("记忆体", "内存")


def route_instruction(text):
    """仅完整匹配显式系统查询/报告指令；转述、否定和普通聊天不执行。"""
    text = normalize_transcript(text)
    prefix = r"(?:(?:请你|请|帮我|麻烦你|麻烦|给我|现在|jac|贾维斯)[,，]?){0,3}"
    report = r"(?:并|然后)?(?:生成|写|保存)(?:一份|一个)?(?:中文)?(?:系统状态|电脑状态|系统)?报告"
    if re.fullmatch(prefix + report, text):
        return ("all",)
    owner = r"(?:(?:这台|我的|本机|当前|现在|电脑|计算机|系统|的)){0,4}"
    action = r"(?:查询|查一下|查看|检查|查下|查|看一下|告诉我)"
    fields = {
        "battery": r"(?:电池(?:电量|状态)?|电量(?:百分比)?|剩余电量)",
        "time": r"(?:时间|几点(?:钟)?)",
        "cpu": r"cpu(?:负载|状态)?",
        "memory": r"内存(?:状态|占用)?",
        "all": r"(?:状态|运行状态|系统状态|电脑状态)",
    }
    for field, pattern in fields.items():
        if re.fullmatch(prefix + action + owner + pattern + r"(?:是多少|谢谢|好吗)?(?:(?:并|然后)" + report + r")?", text):
            return (field,)
    if re.fullmatch(prefix + r"(?:本机|当前|现在)几点(?:钟)?(?:了)?", text):
        return ("time",)
    return None


def confirmed_text(segments):
    """拒绝低置信度、无语音概率高或重复压缩异常的 Whisper 结果。"""
    if not isinstance(segments, list) or not segments:
        return None
    texts = []
    for segment in segments:
        if not isinstance(segment, dict) or not isinstance(segment.get("text"), str):
            return None
        values = [segment.get(k) for k in ("avg_logprob", "no_speech_prob", "compression_ratio")]
        if any(not isinstance(v, (float, int)) or not np.isfinite(v) for v in values):
            return None
        if values[0] < -.75 or values[1] > .45 or values[2] > 2.4:
            return None
        texts.append(segment["text"].strip())
    text = "".join(texts).strip()
    return text if text and len(text) <= 400 else None


class TaskPipeline:
    """三个后台线程分别负责 VAD、Whisper 和 Qwen；不占用 Qt/WS 回调。"""

    def __init__(self, callbacks, model_dir=DEFAULT_MODEL_DIR, brain_url="http://127.0.0.1:12345",
                 decoder_factory=WhisperProcess, runner_factory=None):
        """仅创建有界缓冲，网络和模型初始化发生在 start 或任务线程。"""
        self.callbacks, self.brain_url = callbacks, brain_url
        self.decoder = decoder_factory(model_dir)
        self.runner_factory = runner_factory
        self.audio, self.utterances, self.tasks = queue.Queue(32), queue.Queue(2), queue.Queue(1)
        self.lock = threading.RLock()
        self.stopped, self.fault = threading.Event(), threading.Event()
        self.threads, self.generation, self.active, self.sequence = [], 0, False, 0
        self.session = ""
        self.busy = False
        self.seen = deque(maxlen=256)
        self.counters = Counter()
        self.segmenter = None

    def start(self):
        """后台完成资源/模型就绪，启动三个有界处理线程。"""
        from src.brain.lm_studio import LMStudioClient
        LMStudioClient(self.brain_url)  # 仅校验本机地址，不探测或加载大脑。
        self.segmenter = VadSegmenter()
        try:
            self.decoder.start()
        except (FileNotFoundError, ValueError) as error:
            raise RuntimeError("本地 Whisper 资源未就绪，请运行安装器或选择锁定模型目录") from error
        try:
            for name, target in (("vad", self._vad_loop), ("whisper", self._decode_loop), ("qwen", self._task_loop)):
                if self.stopped.is_set():
                    return
                thread = threading.Thread(target=target, name="jac-" + name, daemon=True)
                self.threads.append(thread)
                thread.start()
        except BaseException:
            self.stop()
            raise

    @staticmethod
    def _drain(buffer):
        """清除旁路积压，主音频上行队列不会交给此函数。"""
        while True:
            try:
                buffer.get_nowait()
            except queue.Empty:
                return

    def begin_session(self):
        """分配新代次；重新连接不能继承待执行指令或半句话。"""
        with self.lock:
            self.generation += 1
            self.session, self.sequence, self.active = uuid4().hex, 0, True
            self._drain(self.audio)
            self._drain(self.utterances)

    def invalidate(self):
        """立即让在途转写/工具/文件失效，停止时不等待主协议尾窗。"""
        with self.lock:
            cancelled = self.active and self.busy
            self.generation += 1
            self.active = False
            for buffer in (self.audio, self.utterances):
                self._drain(buffer)
            if cancelled and not self.stopped.is_set():
                self._notify("cancelled", {"code": "session_changed"})

    def current(self, generation):
        """检查事件是否仍属于正在采集的会话，供每个执行边界使用。"""
        with self.lock:
            return self.active and not self.stopped.is_set() and not self.fault.is_set() and generation == self.generation

    def _fail(self, code):
        """旁路故障关闭自动任务，通知 GUI，原音视频上行继续运行。"""
        self.fault.set()
        with self.lock:
            self.counters[code] += 1
        if not self.stopped.is_set():
            self._notify("error", {"code": code})

    def _notify(self, state, detail):
        """轻量回调异常也关闭自动路由，不能令重采样/上行线程退出。"""
        try:
            self.callbacks.on_task_event(state, detail)
        except Exception:
            self.fault.set()
            with self.lock:
                self.counters["callback_error"] += 1

    def offer_audio(self, samples, overlap=False):
        """重采样线程只复制入队；满队列关闭旁路，不阻塞/丢弃主上行。"""
        with self.lock:
            generation = self.generation
            if not self.current(generation):
                return
        try:
            self.audio.put_nowait((generation, np.asarray(samples, dtype="float32").copy(), bool(overlap), time.monotonic()))
        except queue.Full:
            self._fail("audio_overflow")

    def _vad_loop(self):
        """独立切句，仅完整句进入转写队列，代次切换时重置切句状态。"""
        previous = -1
        while not self.stopped.is_set():
            try:
                generation, samples, overlap, ended_at = self.audio.get(timeout=.1)
            except queue.Empty:
                continue
            if not self.current(generation):
                continue
            if generation != previous:
                self.segmenter.reset()
                previous = generation
            try:
                for pcm, blocked, ended in self.segmenter.feed(samples, overlap, ended_at):
                    with self.lock:
                        if not self.current(generation):
                            break
                        self.sequence += 1
                        identifier = f"{self.session}:{self.sequence}"
                        self.counters["utterances"] += 1
                    self.utterances.put_nowait(Utterance(identifier, generation, ended, pcm, blocked))
            except queue.Full:
                self._fail("utterance_overflow")
            except Exception:
                self._fail("vad_error")

    def _decode_loop(self):
        """一次转写一句；过期/重连结果丢弃，播放重叠不作为用户任务。"""
        while not self.stopped.is_set():
            try:
                utterance = self.utterances.get(timeout=.1)
            except queue.Empty:
                continue
            if not self.current(utterance.generation):
                continue
            if utterance.overlap:
                with self.lock:
                    self.counters["playback_rejected"] += 1
                continue
            try:
                text = confirmed_text(self.decoder.transcribe(utterance.samples))
                if not self.current(utterance.generation):
                    continue
                if time.monotonic() - utterance.ended_at > 15:
                    with self.lock:
                        self.counters["stale_rejected"] += 1
                    continue
                if text is None:
                    with self.lock:
                        self.counters["quality_rejected"] += 1
                    self._notify("rejected", {"code": "unclear_speech"})
                    continue
                with self.lock:
                    if not self.current(utterance.generation):
                        continue
                    self.counters["transcriptions"] += 1
                    self.callbacks.on_user_transcript(text)
                    fields = route_instruction(text)
                    if fields:
                        self.submit(utterance, text, fields)
            except Exception:
                if not self.stopped.is_set():
                    self._fail("whisper_error")

    def submit(self, utterance, text, fields):
        """同一句只派发一次；忙碌时拒绝积压，语音状态不阻塞。"""
        with self.lock:
            if (not self.current(utterance.generation) or utterance.identifier in self.seen
                    or utterance.overlap or time.monotonic() - utterance.ended_at > 15
                    or route_instruction(text) != tuple(fields)):
                return False
            self.seen.append(utterance.identifier)
            if self.busy:
                self.counters["busy_rejected"] += 1
                self._notify("rejected", {"code": "brain_busy"})
                return False
            self.busy = True
            self.tasks.put_nowait((utterance, text, fields))
            self.counters["tasks_submitted"] += 1
            return True

    def _task_loop(self):
        """唯一大脑线程；执行前/发布前再次核对代次并提供实际文件路径。"""
        runner = None
        while not self.stopped.is_set():
            try:
                utterance, text, fields = self.tasks.get(timeout=.1)
            except queue.Empty:
                continue
            try:
                if not self.current(utterance.generation):
                    continue
                self._notify("running", {"id": utterance.identifier})
                if runner is None:
                    if self.runner_factory is None:
                        from src.brain.llm import LocalBrain
                        from src.brain.task_runner import BrainTaskRunner
                        runner = BrainTaskRunner(LocalBrain(backend="lm_studio", lm_studio_url=self.brain_url))
                    else:
                        runner = self.runner_factory()
                result = runner.run(text, should_stop=lambda: not self.current(utterance.generation),
                                    info_types=fields, publication_lock=self.lock)
                with self.lock:
                    if self.current(utterance.generation):
                        self.counters["tasks_completed"] += 1
                        self._notify("completed", {"id": utterance.identifier,
                            "path": str(result.path), "answer": result.answer})
            except Exception:
                if self.current(utterance.generation):
                    with self.lock:
                        self.counters["tasks_failed"] += 1
                    self._notify("error", {"code": "brain_failed"})
                    runner = None
            finally:
                with self.lock:
                    self.busy = False

    def stop(self):
        """取消代次、回收 CPU 子进程并等待三个线程；失败禁止重新启动。"""
        self.stopped.set()
        self.invalidate()
        self.decoder.stop()
        for thread in self.threads:
            if thread.ident is not None:
                thread.join(7)
        if any(t.is_alive() for t in self.threads):
            raise RuntimeError("转写或大脑线程未完整退出")
        self.decoder.close()

    def request_stop(self):
        """跨线程立即取消代次和启动等待，清理工作由后台 stop 完成。"""
        self.stopped.set()
        self.invalidate()
        self.decoder.request_stop()

    def stats(self):
        """仅返回计数；转写正文、PCM 和工具报告不进入运行统计。"""
        with self.lock:
            return dict(self.counters)
