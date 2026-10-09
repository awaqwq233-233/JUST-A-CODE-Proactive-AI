"""原生任务播报：有界等待、证据摘要、会话取消与音频归属校验。"""

from collections import Counter, deque
from dataclasses import dataclass, field
import re
import threading
import time


def speech_summary(result, fields):
    """从已交付的工具证据生成短句，不让感知模型重新推理或编造数字。"""
    message = "系统状态查询已完成，报告已生成，请查看界面中的完整结果。"
    if fields[0] == "weather_city":
        from src.brain.task_runner import WEATHER_CITY_PROMPT
        return WEATHER_CITY_PROMPT
    if fields[0] == "web" and getattr(result, "status", "completed") == "no_answer":
        return "bo s s，本次联网查询未取得可核实的答案，请换个关键词或稍后重试。查询记录和来源链接已保存。"
    if fields[0] == "web":
        # 仅使用已经交付的 Qwen 回答，完整来源和摘录仍留在报告。
        answer = re.sub(r"\[[^\]]*\]\(https?://[^)]*\)|https?://\S+|\[\d+\]", "", result.answer)
        answer = re.sub(r"bo\s*s\s*s[，,：:\s]*", "", answer, flags=re.I)
        answer = re.sub(r"[#*`\n\r\t]+", " ", answer).strip()
        if answer and not any(ord(c) < 32 or ord(c) == 127 for c in answer) and "<" not in answer and ">" not in answer:
            short = answer[:180]
            if len(answer) > 180 and re.search(r"[。！？]", short):
                short = short[:max(short.rfind(c) for c in "。！？") + 1]
            message = "联网查询已完成。" + short.rstrip("。") + "。详细结果和来源已保存。"
        else:
            message = "联网查询已完成，详细结果和来源已保存，请查看报告。"
        return "bo s s，" + message
    if len(fields) == 1 and fields[0] != "all":
        outputs = [item.get("output") for item in result.trace
                   if item.get("name") == "get_system_info"
                   and item.get("arguments", {}).get("info_type") == fields[0]]
        if len(outputs) == 1 and isinstance(outputs[0], str):
            evidence = outputs[0].strip()
            if (0 < len(evidence) <= 90 and not any(ord(c) < 32 or ord(c) == 127 for c in evidence)
                    and "<" not in evidence and ">" not in evidence):
                message = evidence.rstrip("。") + "。完整结果已保存。"
    return "bo s s，" + message


@dataclass
class SpeechJob:
    """一个已完成任务的短播报，取消标志随播放块传递。"""

    identifier: str
    generation: int
    text: str
    deadline: float
    purpose: str = "result"
    cancellation: threading.Event = field(default_factory=threading.Event)
    received: int = 0
    nonzero: bool = False
    transcript: str = ""


class TaskSpeechQueue:
    """共享任务代次锁；只保留一个播报，不积压或跨会话补播。"""

    def __init__(self, pipeline, callbacks, clock=time.monotonic):
        """绑定任务来源和轻量状态回调，不打开设备或创建网络连接。"""
        self.pipeline, self.callbacks, self.clock = pipeline, callbacks, clock
        self.pending = self.active = None
        self.seen, self.guards = deque(maxlen=64), deque(maxlen=64)
        self.counters = Counter()

    def _notify(self, state, job, code=None):
        """状态仅携带标识和原因，统计不保存摘要或工具正文。"""
        detail = {"id": job.identifier}
        if job.purpose == "clarification":
            detail["purpose"] = job.purpose
        if code:
            detail["code"] = code
        self.callbacks.on_task_event("speech_" + state, detail)
        self.counters[state] += 1

    def offer(self, identifier, generation, result, fields):
        """接受当前代次报告或程序固定追问；重复或忙碌时明确跳过。"""
        with self.pipeline.lock:
            if not self.pipeline.current(generation) or identifier in self.seen:
                return
            self.seen.append(identifier)
            job = SpeechJob(identifier, generation, speech_summary(result, fields), self.clock() + 20)
            if fields[0] == "weather_city":
                job.purpose = "clarification"
            if self.pending or self.active:
                self._notify("skipped", job, "speech_busy")
                return
            self.pending = job
            self.guards.append(job.cancellation)
            self._notify("queued", job)

    def cancel(self):
        """会话失效立即取消待发和已入播放队列的任务音频。"""
        with self.pipeline.lock:
            for guard in self.guards:
                guard.set()
            for job in (self.pending, self.active):
                if job:
                    self._notify("cancelled", job, "session_changed")
            self.pending = self.active = None
            self.guards.clear()

    def next_input(self, ready, remaining):
        """在聆听、用户静音和播放空闲时附下一秒真实音频，绝不替换 PCM。"""
        with self.pipeline.lock:
            now = self.clock()
            for name in ("pending", "active"):
                job = getattr(self, name)
                if job and (not self.pipeline.current(job.generation) or now >= job.deadline):
                    job.cancellation.set()
                    setattr(self, name, None)
                    self._notify("skipped", job, "speech_timeout")
            if self.active or not self.pending:
                return None
            job = self.pending
            if remaining < 15:
                self.pending = None
                job.cancellation.set()
                self._notify("skipped", job, "session_ending")
                return None
            if not ready or not self.pipeline.user_quiet():
                return None
            if not re.fullmatch(r"[a-z0-9:-]{1,80}", job.identifier):
                raise ValueError("任务播报标识不合法")
            self.pending, self.active = None, job
            job.deadline = now + 30
            self._notify("sent", job)
            return {"id": job.identifier, "text": job.text}

    def _current(self, identifier):
        """调用方持有代次锁时核对已派发的播报归属。"""
        job = self.active
        return job if (job and job.identifier == identifier and not job.cancellation.is_set()
                       and self.clock() < job.deadline and self.pipeline.current(job.generation)) else None

    def text_delta(self, identifier, text):
        """保留有界字面文本供完成核对，过期/错代次增量直接丢弃。"""
        with self.pipeline.lock:
            job = self._current(identifier)
            if job:
                job.transcript = (job.transcript + text)[:768]
                if not job.text.startswith(job.transcript):
                    job.cancellation.set()
                    self.active = None
                    self._notify("failed", job, "speech_text_mismatch")
                    return False
                return True
            return False

    def audio(self, identifier, samples):
        """有效归属才允许入播放流，取消标志使已排队块也能被静音。"""
        with self.pipeline.lock:
            job = self._current(identifier)
            if job:
                job.received += len(samples)
                job.nonzero |= bool((abs(samples) > 1e-7).any())
                self.counters["audio_samples"] += len(samples)
                return job.cancellation
            return None

    def event(self, event):
        """要求准确文字与非静音原生 PCM；完成表示收到全部音频，不代替听感验收。"""
        with self.pipeline.lock:
            job = self._current(event.get("id"))
            if not job:
                return
            if event.get("state") == "accepted":
                self._notify("started", job)
                return
            count = event.get("audio_samples")
            if (event.get("state") == "completed" and type(count) is int and count == job.received
                    and count > 0 and job.nonzero and job.transcript == job.text):
                self._notify("completed", job)
            else:
                job.cancellation.set()
                code = "speech_backend_failed" if event.get("state") != "completed" else "speech_audio_mismatch"
                self._notify("failed", job, code)
            self.active = None

    def stats(self):
        """返回没有任务文字、参考音或路径的计数快照。"""
        with self.pipeline.lock:
            return dict(self.counters)
