"""在用户转写判定前暂存普通回复，系统查询只允许经核验的任务播报。"""

from collections import Counter
import time


class ResponseGate:
    """仅在 WS 线程管理有界内存；不等待转写，不改变音视频上行。"""

    def __init__(self, pipeline, callbacks, clock=time.monotonic):
        """绑定线程安全的用户句子判定，普通播放使用同句取消标志。"""
        self.pipeline, self.callbacks, self.clock = pipeline, callbacks, clock
        self.sequence = None
        self.buffer, self.size, self.started = [], Counter(), 0
        self.blocked = False
        self.counters = Counter()

    def _state(self):
        """新句子丢弃旧暂存；超时或超限后该句不再恢复普通回复。"""
        sequence, policy, guard = self.pipeline.response_state()
        if sequence != self.sequence:
            self.counters["discarded"] += len(self.buffer)
            self.buffer, self.size, self.blocked = [], Counter(), False
            self.sequence = sequence
        if self.buffer and self.clock() - self.started >= 15 and not self.blocked:
            self._reject("response_wait_timeout")
        return ("block" if self.blocked else policy), guard

    def _reject(self, code):
        """有界暂存失败明确拒绝，不把残缺普通音频冒充任务结果。"""
        self.blocked = True
        self.callbacks.on_task_event("rejected", {"code": code})

    def poll(self):
        """即使后端没有新输出，也在转写完成后及时放行普通对话。"""
        policy, guard = self._state()
        if policy == "pending":
            return []
        buffered, self.buffer = self.buffer, []
        self.size.clear()
        if policy == "block":
            self.counters["discarded"] += len(buffered)
            return []
        self.counters["released"] += len(buffered)
        return [(kind, value, guard) for kind, value in buffered]

    def offer(self, kind, value):
        """暂存最多 8 秒 PCM/8192 文本字节，系统请求永不放行普通回答。"""
        released = self.poll()
        policy, guard = self._state()
        if policy == "block":
            self.counters["discarded"] += 1
            return released
        if policy == "allow":
            return released + [(kind, value, guard)]
        size = value.nbytes if kind == "audio" else len(value.encode("utf-8"))
        limit = 8 * 24000 * 4 if kind == "audio" else 8192
        if len(self.buffer) >= 256 or self.size[kind] + size > limit:
            self._reject("response_buffer_full")
            self.poll()
            self.counters["discarded"] += 1
            return released
        if not self.buffer:
            self.started = self.clock()
        self.buffer.append((kind, value))
        self.size[kind] += size
        self.counters["held"] += 1
        return released
