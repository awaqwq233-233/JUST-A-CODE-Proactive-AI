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
    response_sequence: int = 0


class VadSegmenter:
    """WebRTC 30ms 判定，300ms 预留、600ms 句末、12s 上限。"""

    def __init__(self, detector=None, on_start=None, on_discard=None):
        """创建 VAD；detector 仅供离线测试替换语音判定。"""
        if detector is None:
            import webrtcvad
            detector = webrtcvad.Vad(2)
        self.detector = detector
        self.on_start, self.on_discard = on_start, on_discard
        self.reset()

    def reset(self):
        """重连/溢出时丢弃未结束语音，绝不把跨会话音频拼成指令。"""
        self.pending = np.zeros(0, dtype="float32")
        self.pending_overlap = False
        self.pre = deque(maxlen=10)
        self.frames, self.voiced, self.quiet = [], 0, 0
        self.overlap, self.discarding = False, False
        self.last_speech_at = 0.0
        self.response_sequence = 0
        self.response_started = False
        self.completed_sequences = []

    def feed(self, samples, overlap=False, ended_at=None):
        """在 VAD 线程切片；返回完整句子，长句拒绝而不拆成多个任务。"""
        ended_at = time.monotonic() if ended_at is None else ended_at
        samples = np.asarray(samples, dtype="float32")
        if samples.ndim != 1 or not np.isfinite(samples).all():
            raise ValueError("VAD 音频格式错误")
        self.pending = np.concatenate((self.pending, samples))
        blocked = overlap or self.pending_overlap
        results = []
        self.completed_sequences = []
        while len(self.pending) >= 480:
            frame, self.pending = self.pending[:480], self.pending[480:]
            pcm16 = (np.clip(frame, -1, 1) * 32767).astype("<i2").tobytes()
            speech = float(np.sqrt(np.mean(frame * frame))) >= .005 and self.detector.is_speech(pcm16, 16000)
            if speech:
                self.last_speech_at = ended_at - len(self.pending) / 16000
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
                self.response_sequence, self.response_started = 0, False
            self.frames.append(frame.copy())
            self.overlap |= blocked and speech
            self.voiced += bool(speech)
            if self.voiced >= 10 and not self.response_started:
                self.response_sequence = self.on_start() if self.on_start else 0
                self.response_started = True
            self.quiet = 0 if speech else self.quiet + 1
            if len(self.frames) >= 400:
                self.frames = []
                self.pre.clear()
                self.discarding, self.quiet = True, 0
                if self.on_discard:
                    self.on_discard(self.response_sequence, "long_speech")
                continue
            if self.quiet >= 20:
                if self.voiced >= 10:
                    results.append((np.concatenate(self.frames), self.overlap,
                                    ended_at - len(self.pending) / 16000))
                    self.completed_sequences.append(self.response_sequence)
                elif self.on_discard:
                    self.on_discard(self.response_sequence, "short_noise")
                self.frames, self.voiced, self.quiet, self.overlap = [], 0, 0, False
        self.pending_overlap = overlap if len(self.pending) else False
        return results


def normalize_transcript(text):
    """固定指令词做繁简兼容和标点归一，不使用会诱导 Whisper 的示例 prompt。"""
    text = unicodedata.normalize("NFKC", text).lower()
    table = str.maketrans("電腦查詢狀態報時當餘負載記憶體製這幫請給並寫現點鐘內謝檢個還", "电脑查询状态报时当余负载记忆体制这帮请给并写现点钟内谢检个还")
    return re.sub(r"[\s，。！？、,.!?：:；;]", "", text.translate(table)).replace("记忆体", "内存")


def route_web_instruction(text):
    """完整匹配用户联网指令，保留关键词空格；天气问句可直接进入只读搜索。"""
    text = unicodedata.normalize("NFKC", text).strip().strip("，。！？,.!? ")
    text = text.translate(str.maketrans("網絡搜尋幫請氣預報麼樣嗎後開刪執", "网络搜寻帮请气预报么样吗后开删执"))
    prefix = r"(?:(?:请你|请|帮我|麻烦你|麻烦|给我|jac|贾维斯)[，,\s]*){0,3}"
    action = r"(?>搜索一下|搜索|搜一下|搜寻|查询一下|查询|查一下|查下|查|搜)"
    match = re.fullmatch(prefix + r"(?:(?:在)?(?:网上|网络|网页|互联网)|上网|联网)\s*" + action + r"\s*(.{2,200})", text, re.I)
    if match is None:
        match = re.fullmatch(prefix + action + r"\s*(?:一下)?(?:在)?(?:网上|网络|互联网|网页)(?:的)?\s*(.{2,200})", text, re.I)
    query = match.group(1).strip() if match else None
    if query is None:
        weather = re.fullmatch(prefix + r"(?:" + action + r")?\s*(.{1,25}?(?:今天|明天|后天|本周|周末|未来\d+天)?(?:的)?天气(?:预报)?)(?:怎么样|如何|怎样|是什么|会下雨吗)?", text, re.I)
        if weather is None:
            weather = re.fullmatch(prefix + r"(?:" + action + r")?\s*([\u4e00-\u9fffA-Za-z ]{2,16}?(?:今天|明天|后天|本周|周末)(?:会不会下雨|会下雨吗|下雨吗|有没有雨|气温多少(?:度)?|温度多少(?:度)?))", text, re.I)
        if weather and not re.search(r"不要|别|他说|我说|我喜欢|我觉得|刚才|原理|科普|定义|什么是|为什么", text):
            query = weather.group(1).strip()
            location = re.split(r"今天|明天|后天|本周|周末|未来\d+天|天气", query)[0].rstrip("的")
            if location in {"", "查", "查询", "查一下", "看看"}:
                query = None
    if (not query or not 2 <= len(query) <= 200 or any(ord(c) < 32 for c in query)
            or re.search(r"[<>]|(?:然后|并且|同时|再)(?:帮我|请)?(?:打开|删除|运行|执行|发送|上传|下载)", query)):
        return None
    return ("web", query)


def is_online_request(text):
    """联网意图只用于禁止普通模型猜测；此分类本身不授予工具执行权限。"""
    text = normalize_transcript(text)
    explicit = re.search(r"上网|联网|网上|网络搜索|网页搜索|互联网|最新新闻|实时新闻", text)
    weather = re.search(r"天气|下雨|气温", text) and re.search(r"查询|查|预报|怎么样|如何|怎样|多少|几度|温度|是什么|吗", text)
    return bool(explicit or weather)


def route_instruction(text):
    """完整匹配显式系统或联网查询；不把助手输出、转述或否定当成指令。"""
    web = route_web_instruction(text)
    if web:
        return web
    text = normalize_transcript(text)
    prefix = r"(?:(?:请你|请|帮我|麻烦你|麻烦|给我|现在|jac|贾维斯)[,，]?){0,3}"
    report = r"(?:并|然后)?(?:生成|写|保存)(?:一份|一个)?(?:中文)?(?:系统状态|电脑状态|系统)?报告"
    if re.fullmatch(prefix + report, text):
        return ("all",)
    owner = r"(?:(?:这台|我的|本机|当前|现在|电脑|计算机|系统|的)){0,4}"
    action = r"(?:查询一下|查询|查一下|查看|检查|检测|查下|查|看一下|告诉我)"
    fields = {
        "battery": r"(?:电池(?:的)?(?:电量|状态)?|电量(?:百分比)?|剩余电量)",
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


def is_system_request(text):
    """识别需真实电脑证据的未支持说法，仅用于禁猜，不据此执行工具。"""
    text = normalize_transcript(text)
    topic = re.search(r"电池|电量|cpu|内存|系统状态|电脑状态|几点|当前时间", text)
    educational = re.search(r"原理|科普|知识|定义|是什么|如何保养|怎么保养", text)
    state_intent = re.search(r"查询|查一下|查看|检查|检测|我的|电脑|本机|多少|剩余", text)
    return bool(topic and (state_intent or not educational))


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
        self.on_result_speech = self.on_invalidate = None
        self.response_sequence, self.response_policy = 0, "allow"
        self.response_guard = threading.Event()
        self._last_recognition_notice = float("-inf")

    def start(self):
        """后台完成资源/模型就绪，启动三个有界处理线程。"""
        from src.brain.lm_studio import LMStudioClient
        LMStudioClient(self.brain_url)  # 仅校验本机地址，不探测或加载大脑。
        self.segmenter = VadSegmenter(on_start=self._response_started, on_discard=self._response_discarded)
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
            self.response_sequence += 1
            self.response_guard.set()
            self.response_guard = threading.Event()
            self.response_policy = "allow"
            self._last_recognition_notice = float("-inf")
            self._drain(self.audio)
            self._drain(self.utterances)

    def invalidate(self):
        """立即让在途转写/工具/文件失效，停止时不等待主协议尾窗。"""
        with self.lock:
            cancelled = self.active and self.busy
            self.generation += 1
            self.active = False
            self.response_guard.set()
            self.response_policy = "block"
            if self.on_invalidate is not None:
                self.on_invalidate()
            for buffer in (self.audio, self.utterances):
                self._drain(buffer)
            if cancelled and not self.stopped.is_set():
                self._notify("cancelled", {"code": "session_changed"})

    def current(self, generation):
        """检查事件是否仍属于正在采集的会话，供每个执行边界使用。"""
        with self.lock:
            return self.active and not self.stopped.is_set() and not self.fault.is_set() and generation == self.generation

    def user_quiet(self):
        """只读 VAD 线程的最近活动；句末静音满足后才允许插入任务播报。"""
        with self.lock:
            return (self.segmenter is not None and not self.segmenter.frames
                    and not self.segmenter.discarding
                    and time.monotonic() - self.segmenter.last_speech_at >= .8)

    def _response_started(self):
        """累计 300ms 有声后撤销旧普通播放；短噪声不取消正在排队的回复。"""
        with self.lock:
            self.response_sequence += 1
            self.response_guard.set()
            self.response_guard = threading.Event()
            self.response_policy = "pending"
            return self.response_sequence

    def _response_discarded(self, sequence, code):
        """短候选只统计；长句保持禁猜并明确拒绝，不取消已有大脑任务。"""
        with self.lock:
            if code == "short_noise":
                self.counters["short_noise_ignored"] += 1
                return
            if sequence == self.response_sequence:
                self.response_policy = "block"
            self._notify("rejected", {"code": code})

    def _notify_unclear_speech(self):
        """完整句识别失败单独提示；连续失败十秒内只显示一次，不改质量门槛。"""
        with self.lock:
            now = time.monotonic()
            if now - self._last_recognition_notice < 10:
                self.counters["recognition_notice_suppressed"] += 1
                return
            self._last_recognition_notice = now
            self.counters["recognition_notices"] += 1
            self._notify("transcription_rejected", {"code": "unclear_speech"})

    def response_state(self):
        """给 WS 返回普通回复权限；任务标记音频仍由独立证据队列核验。"""
        with self.lock:
            policy = self.response_policy
            if not self.active or self.fault.is_set() or self.stopped.is_set():
                policy = "block"
            elif not self.audio.empty() or (self.segmenter is not None and self.segmenter.frames):
                policy = "pending"
            return self.response_sequence, policy, self.response_guard

    def _response_decided(self, utterance, policy):
        """旧转写不能解锁新一句话，也不能跨会话解锁播放。"""
        with self.lock:
            if self.current(utterance.generation) and utterance.response_sequence == self.response_sequence:
                self.response_policy = policy
                if policy == "block":
                    self.response_guard.set()

    def _fail(self, code):
        """旁路故障关闭自动任务，通知 GUI，原音视频上行继续运行。"""
        self.fault.set()
        with self.lock:
            self.response_policy = "block"
            self.response_guard.set()
            self.counters[code] += 1
            if self.on_invalidate is not None:
                self.on_invalidate()
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
            try:
                with self.lock:
                    if not self.current(generation):
                        continue
                    if generation != previous:
                        self.segmenter.reset()
                        previous = generation
                    results = self.segmenter.feed(samples, overlap, ended_at)
                    for (pcm, blocked, ended), sequence in zip(results, self.segmenter.completed_sequences):
                        if not self.current(generation):
                            break
                        self.sequence += 1
                        identifier = f"{self.session}:{self.sequence}"
                        self.counters["utterances"] += 1
                        self.utterances.put_nowait(Utterance(identifier, generation, ended, pcm, blocked, sequence))
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
                self._response_decided(utterance, "block")
                self._notify("rejected", {"code": "playback_overlap"})
                continue
            try:
                text = confirmed_text(self.decoder.transcribe(utterance.samples))
                if not self.current(utterance.generation):
                    continue
                if time.monotonic() - utterance.ended_at > 15:
                    with self.lock:
                        self.counters["stale_rejected"] += 1
                    self._response_decided(utterance, "block")
                    self._notify("rejected", {"code": "stale_speech"})
                    continue
                if text is None:
                    with self.lock:
                        self.counters["quality_rejected"] += 1
                    self._notify_unclear_speech()
                    self._response_decided(utterance, "block")
                    continue
                with self.lock:
                    if not self.current(utterance.generation):
                        continue
                    self.counters["transcriptions"] += 1
                    self._last_recognition_notice = float("-inf")
                    self.callbacks.on_user_transcript(text)
                    fields = route_instruction(text)
                    self._response_decided(utterance, "block" if fields or is_system_request(text) or is_online_request(text) else "allow")
                    if fields:
                        self.submit(utterance, text, fields)
                    elif is_system_request(text):
                        self._notify("rejected", {"code": "unsupported_system_request"})
                    elif is_online_request(text):
                        self._notify("rejected", {"code": "unsupported_web_request"})
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
                def tool_result(entry):
                    """只有工具真实返回后才显示查询证据，旧代次不向界面发布。"""
                    with self.lock:
                        if self.current(utterance.generation):
                            self.counters["tool_calls"] += 1
                            self._notify("tool_completed", {"id": utterance.identifier, **entry})
                scope = {"web_query": fields[1]} if fields[0] == "web" else {"info_types": fields}
                result = runner.run(text, should_stop=lambda: not self.current(utterance.generation),
                                    publication_lock=self.lock, on_tool_result=tool_result, **scope)
                with self.lock:
                    if self.current(utterance.generation):
                        self.counters["tasks_completed"] += 1
                        self._notify("completed", {"id": utterance.identifier,
                            "path": str(result.path), "answer": result.answer})
                        if self.on_result_speech is not None:
                            try:
                                self.on_result_speech(utterance.identifier, utterance.generation, result, fields)
                            except Exception:
                                self.counters["speech_callback_error"] += 1
                                self._notify("speech_failed", {"id": utterance.identifier, "code": "speech_failed"})
            except Exception:
                if self.current(utterance.generation):
                    with self.lock:
                        self.counters["tasks_failed"] += 1
                    self._notify("error", {"code": "web_failed" if fields[0] == "web" else "brain_failed"})
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
