"""
J.A.C. 持久记忆子系统 —— 编排门面（Phase 3 集成层）

对 main.py 暴露两个入口：
  - ``retrieve_for_prompt(user_text, vision_info)``：对话前检索相关记忆，拼成
    注入块（总字符受限），拼进 system_prompt。
  - ``record_turn(user_text, response, window, is_thinking=False)``：对话结束后
    入队后台 worker，立即返回，**绝不阻塞响应**。

门控（worker 内）：限流（距上次分类 >= MIN_CLASSIFY_INTERVAL）；is_thinking 时不
分类（调用方在助手说完、is_speaking=False 后才调用 record_turn，通常不触发）。
pii 双层门控在 recorder 侧完成。
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Optional

from .models import MemoryFact, MemoryKind, MemorySource
from .recorder import MemoryRecorder, RecordDecision
from .store import MemoryStore
from .prompts import format_injection


class MemoryManager:
    """记忆子系统门面：检索注入 + 后台记录。"""

    def __init__(
        self,
        base_dir: Optional[str] = None,
        store: Optional[MemoryStore] = None,
        brain=None,
        recorder: Optional[MemoryRecorder] = None,
        enabled: bool = True,
        capture_person_id: bool = False,
        recurrence_threshold: int = 3,
        min_classify_interval: float = 3.0,
        inject_max_chars: int = 300,
        top_k: int = 5,
    ) -> None:
        self.enabled = enabled
        self.brain = brain
        self.inject_max_chars = inject_max_chars
        self.top_k = top_k
        self.min_classify_interval = min_classify_interval

        if not enabled:
            self.store = None
            self.recorder = None
            self._worker = None
            return

        self.store = store or MemoryStore(base_dir=base_dir)
        self.recorder = recorder or MemoryRecorder(
            capture_person_id=capture_person_id,
            recurrence_threshold=recurrence_threshold,
        )

        self._queue: "queue.Queue[Optional[tuple]]" = queue.Queue()
        self._last_classify_ts = 0.0
        self._worker = threading.Thread(target=self._worker_loop, name="memory-worker", daemon=True)
        self._worker.start()

    # ------------------------- 检索注入 -------------------------

    def retrieve_for_prompt(self, user_text: str, vision_info: str = "") -> str:
        """对话前检索相关记忆，返回注入块（无相关记忆返回空串）。"""
        if not self.enabled or self.store is None:
            return ""
        query = f"{user_text} {vision_info}".strip()
        results = self.store.query_by_keywords(query, k=self.top_k)
        if not results:
            return ""
        lines = [r.to_prompt_line() for r in results]
        return format_injection(lines, max_chars=self.inject_max_chars)

    # ------------------------- 记录入口 -------------------------

    def record_turn(
        self,
        user_text: str,
        response: str = "",
        window: str = "",
        is_thinking: bool = False,
    ) -> None:
        """对话结束后调用一次：入队后台 worker，立即返回（绝不阻塞）。"""
        if not self.enabled or self._worker is None:
            return
        if is_thinking:
            return  # 大脑繁忙则不分类，宁可漏记
        self._queue.put((user_text, response, window))

    # ------------------------- 后台 worker -------------------------

    def _worker_loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                break
            user_text, response, window = item
            self._classify_and_store(user_text, response, window)

    def _classify_and_store(self, user_text: str, response: str, window: str) -> None:
        now = time.monotonic()
        # 🟠#6 限流只作用于「LLM 辅助分支」：规则阶段（A 显式保存等）
        # 零成本且属 DoD#1 必须 100% 落库，绝不因限流被丢弃。仅在距上次
        # LLM 调用不足间隔时才把 brain 传 None（弱意图走保守不记）。
        llm_allowed = not (
            self._last_classify_ts
            and (now - self._last_classify_ts) < self.min_classify_interval
        )
        if llm_allowed:
            self._last_classify_ts = now

        try:
            decision = self.recorder.classify(
                user_text, response, window,
                brain=self.brain if llm_allowed else None,
            )
        except Exception as e:  # 判定异常绝不影响对话
            print(f"[MemoryManager] 判定异常，已跳过: {e}")
            return

        if decision.should_store and decision.kind is not None:
            fact = self._decision_to_fact(decision, user_text)
            if fact is not None and self.store is not None:
                self.store.upsert(fact)

    @staticmethod
    def _decision_to_fact(decision: RecordDecision, fallback_text: str) -> Optional[MemoryFact]:
        content = (decision.content or fallback_text or "").strip()
        if not content:
            return None
        # 🟡#11 纵深防御：即便 recorder 门控被绕过（如直接 upsert），
        # 写库前再次确认「具体人物身份未授权不落库」。
        if decision.pii and not (MemoryManager._capture_person_id(decision)):
            return None
        if decision.kind == MemoryKind.topic:
            weight = 0.8
        elif decision.source == MemorySource.inferred:
            weight = 0.4
        else:
            weight = 0.7
        return MemoryFact(
            content=content,
            kind=decision.kind,
            source=decision.source,
            weight=weight,
            tags=decision.tags or [decision.kind.value],
            pii=decision.pii,
        )

    @staticmethod
    def _capture_person_id(decision: RecordDecision) -> bool:
        """判断当前是否允许捕获具体人物身份：

        需同时满足 ``pii`` 标记且来源为显式（explicit）。recorder 侧已按
        ``capture_person_id`` 配置拦截，此处为写库前的最后一道闸门。
        """
        return decision.pii and decision.source == MemorySource.explicit

    # ------------------------- 生命周期 -------------------------

    def flush(self) -> None:
        if self.store is not None:
            self.store.flush()

    def close(self) -> None:
        if self._worker is not None:
            self._queue.put(None)
            if self._worker.is_alive():
                self._worker.join(timeout=self.min_classify_interval + 2.0)
        if self.store is not None:
            self.store.close()

    @property
    def stats(self) -> Optional[dict]:
        return self.store.stats() if self.store is not None else None
