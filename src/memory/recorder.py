"""
J.A.C. 持久记忆子系统 —— 记录判定逻辑（Phase 2）

核心：「记住关键信息，忽略日常提问」。策略 = 规则优先 + LLM 辅助。
  - 规则阶段（零成本、确定）：跑锁定正则，覆盖 A 显式保存 / B 偏好画像 /
    D 决策约定 / 排除项（闲聊、一次性问答、日常）。
  - LLM 辅助阶段：仅「弱意图（以后/下次…）」等模糊信号路由到 LocalBrain 分类；
    无 LLM 时保守不记（宁漏不误）。
  - 频次升级：同 topic_key 会话级计数累计 >= RECURRENCE_THRESHOLD(3) 自动晋升
    recurring_topic；计数不持久化，晋升后保留以防重复晋升。
  - pii 双层门控：检测到的具体人物身份默认不记；仅 capture_person_id=True 且
    source=explicit 才落库并标 pii=true。

严格对齐 docs/memory/schema.md 与 docs/memory_test_plan.md §2/§3.4 锁定的
正则、判定顺序、kind 枚举、reason 受控词表。
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from typing import Optional

from .models import MemoryFact, MemoryKind, MemorySource


# ---------------------------------------------------------------------------
# 锁定的常量与正则（oracle：docs/memory_test_plan.md §2）
# ---------------------------------------------------------------------------

RECURRENCE_THRESHOLD = 3          # 同 topic_key 累计出现次数达到即晋升
MIN_CLASSIFY_INTERVAL = 3.0       # 距上次分类的最小间隔（秒），限流用
RECURRING_WEIGHT = 0.8            # 晋升 recurring_topic 的高权重[0,1]

EXPLICIT_SAVE_RE = re.compile(
    r"(记住|记一下|记着|别忘了|请记住|帮我记|记到|存一下|提醒我|remember\s+(this|that)|don'?t\s+forget|save\s+(this|that)|note\s+(this|that))",
    re.IGNORECASE,
)
WEAK_INTENT_RE = re.compile(
    r"(以后|之后|将来|下次|下回|next\s+time|from\s+now\s+on)", re.IGNORECASE,
)
PREFERENCE_RE = re.compile(
    r"(我喜欢|我不喜欢|我讨厌|我恨|我爱|我习惯|我一般|叫我|我叫|我是|我的名字|我家|我不吃|我不喝|我想要|我打算|i\s+(like|love|hate|prefer|usually|always))",
    re.IGNORECASE,
)
DECISION_RE = re.compile(
    r"(我们决定|决定|约定|答应|计划|承诺|说好|说定了|约好|we\s+(decided|agreed)|i\s+promise|let'?s\s+(meet|do))",
    re.IGNORECASE,
)
QUESTION_RE = re.compile(r"[?？]\s*$")
SMALLTALK_RE = re.compile(
    r"(你好|您好|\bhi\b|\bhello\b|\bhey\b|讲个笑话|讲个故事|再见|拜拜)",
    re.IGNORECASE,
)

# 具体人物身份检测（PII 双层门控第一层：分类侧标 pii=True）
# v1 无 NER，采用关系词启发式；capture_person_id=False 时默认拦截。
_PII_RELATIONSHIP_RE = re.compile(
    r"(是|叫)(我|我的)(儿子|女儿|爸爸|父亲|妈妈|母亲|朋友|同事|老板|妻子|丈夫|老公|老婆|室友|邻居)",
    re.IGNORECASE,
)


@dataclass
class RecordDecision:
    """记录判定结果（运行时契约，不进 memory.json；schema.md §10）。

    与 MemoryFact.kind 同源：should_store=False 时 kind 必须为 None。
    """

    should_store: bool
    reason: str
    kind: Optional[MemoryKind]
    confidence: float
    content: str = ""
    tags: list[str] = field(default_factory=list)
    source: MemorySource = MemorySource.manual
    pii: bool = False


class MemoryRecorder:
    """记录判定核心（规则优先 + LLM 辅助 + 频次升级 + pii 门控）。"""

    def __init__(
        self,
        recurrence_threshold: int = RECURRENCE_THRESHOLD,
        capture_person_id: bool = False,
    ) -> None:
        self.recurrence_threshold = recurrence_threshold
        self.capture_person_id = capture_person_id
        self._lock = threading.Lock()
        self._topic_counts: dict[str, int] = {}
        self._promoted: set[str] = set()

    # ------------------------- 对外主入口 -------------------------

    def classify(
        self,
        user_text: str,
        response: str = "",
        window: str = "",
        brain=None,
    ) -> RecordDecision:
        """对一轮对话产出 RecordDecision。

        - 规则阶段先判定（确定、零成本）；
        - 模糊信号（弱意图）且提供了 brain 时走 LLM 辅助；无 brain 则保守不记；
        - 应用 pii 双层门控；
        - 频次升级：日常陈述反复出现 >= 阈值则晋升 recurring_topic。
        """
        base = self._rule_stage(user_text)
        if base is None:
            # 弱意图等模糊信号：路由 LLM（若有），否则保守不记
            if brain is not None:
                base = self._llm_stage(user_text, response, window, brain)
            else:
                base = RecordDecision(False, "low_confidence", None, 0.3)

        # pii 双层门控：无论规则结论如何，检测到具体人物身份都必须先过此关
        base = self._apply_pii_gate(base, user_text)

        # 频次升级（仅当规则未判定为应记、且非 pii 拦截时）
        if not base.should_store and not base.pii:
            key = self.normalize_topic(user_text)
            if key:
                # 整段检查+晋升在单把锁内完成，避免并发 classify 都看到
                # "未晋升" 而各发一条重复 topic 事实（防重复晋升）。
                with self._lock:
                    self._topic_counts[key] = self._topic_counts.get(key, 0) + 1
                    count = self._topic_counts[key]
                    if count >= self.recurrence_threshold and key not in self._promoted:
                        self._promoted.add(key)
                        promote = True
                    else:
                        promote = False
                if promote:
                    return RecordDecision(
                        should_store=True,
                        reason="topic_of_interest",
                        kind=MemoryKind.topic,
                        confidence=0.7,
                        content=user_text.strip(),
                        tags=["topic"],
                        source=MemorySource.recurring,
                    )
        return base

    # ------------------------- 规则阶段 -------------------------

    def _rule_stage(self, user_text: str) -> Optional[RecordDecision]:
        text = (user_text or "").strip()
        if not text:
            return RecordDecision(False, "not_factual", None, 0.9)

        # 1. 闲聊 → 排除
        if SMALLTALK_RE.search(text):
            return RecordDecision(False, "not_factual", None, 0.9)

        # 2. 显式保存意图 → 直接记（高置信，source=explicit）
        if EXPLICIT_SAVE_RE.search(text):
            content = self._extract_proposition(text)
            kind = self._detect_kind(content)
            return RecordDecision(
                should_store=True,
                reason="user_stated",
                kind=kind,
                confidence=0.95,
                content=content,
                tags=[kind.value],
                source=MemorySource.explicit,
            )

        # 3. 一次性问答（且非上述）→ 排除
        if QUESTION_RE.search(text):
            return RecordDecision(False, "not_factual", None, 0.9)

        # 4. 偏好 / 画像 → 记（source=inferred，规则抽取）
        if PREFERENCE_RE.search(text):
            content = self._extract_proposition(text)
            kind = MemoryKind.profile if self._looks_like_profile(text) else MemoryKind.preference
            return RecordDecision(
                should_store=True,
                reason="derived_preference",
                kind=kind,
                confidence=0.8,
                content=content,
                tags=[kind.value],
                source=MemorySource.inferred,
            )

        # 5. 决策 / 约定 / 承诺 → 记（source=inferred，kind=event）
        if DECISION_RE.search(text):
            content = self._extract_proposition(text)
            return RecordDecision(
                should_store=True,
                reason="explicit_convention",
                kind=MemoryKind.event,
                confidence=0.8,
                content=content,
                tags=[MemoryKind.event.value],
                source=MemorySource.inferred,
            )

        # 6. 弱意图（以后/下次…）→ 路由 LLM（返回 None，由 classify 处理）
        if WEAK_INTENT_RE.search(text):
            return None

        # 7. 其它 → 日常，排除
        return RecordDecision(False, "not_factual", None, 0.7)

    # ------------------------- LLM 辅助阶段 -------------------------

    def _llm_stage(self, user_text: str, response: str, window: str, brain) -> RecordDecision:
        from .prompts import CLASSIFY_PROMPT

        prompt = CLASSIFY_PROMPT.format(user_text=user_text, response=response)
        try:
            raw = brain.think(prompt)
        except Exception:
            return RecordDecision(False, "low_confidence", None, 0.3)
        parsed = self._parse_llm_json(raw)
        if parsed is None:
            return RecordDecision(False, "low_confidence", None, 0.3)
        should = bool(parsed.get("should_store", False))
        kind_str = parsed.get("kind")
        kind = None
        if should and kind_str:
            try:
                kind = MemoryKind(kind_str)
            except ValueError:
                kind = MemoryKind.profile
        content = (parsed.get("content") or user_text).strip()
        tags = parsed.get("tags") or ([kind.value] if kind else [])
        try:
            conf = float(parsed.get("confidence", 0.5))
        except (ValueError, TypeError):
            conf = 0.5
        reason = parsed.get("reason") or ("user_stated" if should else "not_factual")
        source = MemorySource.inferred if should else MemorySource.manual
        return RecordDecision(
            should_store=should,
            reason=reason,
            kind=kind,
            confidence=conf,
            content=content,
            tags=tags,
            source=source,
        )

    @staticmethod
    def _parse_llm_json(raw: str) -> Optional[dict]:
        if not raw:
            return None
        # 防止异常大的输入拖垮解析
        if len(raw) > 20000:
            raw = raw[:20000]
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            pass
        # 容错：定位首个 `{` 并匹配成对 `}`（正确处理输出里夹杂的文字/反引号），
        # 而非贪婪 ``\{.*\}``（会在含多组大括号的散文上误捕获）。
        start = raw.find("{")
        if start < 0:
            return None
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(raw)):
            ch = raw[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    snippet = raw[start:i + 1]
                    try:
                        return json.loads(snippet)
                    except (ValueError, TypeError):
                        return None
        return None

    # ------------------------- pii 门控 -------------------------

    def _apply_pii_gate(self, decision: RecordDecision, user_text: str) -> RecordDecision:
        # 无论规则结论如何，只要检测到具体人物身份就先过此关
        if not self._detect_pii(user_text):
            return decision
        # 显式要求且开启捕获 → 允许落库并标 pii=true
        if self.capture_person_id and decision.source == MemorySource.explicit and decision.should_store:
            decision.pii = True
            return decision
        # 默认拦截：硬丢弃不落库
        return RecordDecision(
            should_store=False,
            reason="pii_blocked",
            kind=None,
            confidence=0.9,
            content=decision.content,
            tags=decision.tags,
            source=decision.source,
            pii=True,
        )

    @staticmethod
    def _detect_pii(text: str) -> bool:
        return bool(_PII_RELATIONSHIP_RE.search(text or ""))

    # ------------------------- 文本工具 -------------------------

    @staticmethod
    def _extract_proposition(text: str) -> str:
        """抽取应记忆的命题：去掉显式保存前缀、唤醒词与首尾标点。"""
        t = text.strip()
        # 去掉显式保存意图前缀（取第一个匹配之后的内容）
        m = EXPLICIT_SAVE_RE.search(t)
        if m:
            rest = t[m.end():].strip(" ，,。.：:；;")
            if rest:
                t = rest
        # 去掉唤醒词
        for wake in ("jac", "j.a.c", "杰克", "接客", "你好jac", "hey jac", "hi jac", "hello jac"):
            t = t.replace(wake, "")
        t = t.strip(" ，,。.：:；;！!？?")
        return t or text.strip()

    @staticmethod
    def _looks_like_profile(text: str) -> bool:
        return bool(re.search(r"(我叫|我的名字|我是.*(工程师|学生|医生|老师|程序员|设计师)|我的职业|我的工作)", text, re.IGNORECASE))

    @staticmethod
    def _detect_kind(text: str) -> MemoryKind:
        if DECISION_RE.search(text):
            return MemoryKind.event
        if MemoryRecorder._looks_like_profile(text):
            return MemoryKind.profile
        if PREFERENCE_RE.search(text):
            return MemoryKind.preference
        return MemoryKind.profile

    @staticmethod
    def normalize_topic(text: str) -> str:
        """确定性归一化（同输入 → 同 key），用于频次计数。"""
        if not text:
            return ""
        t = text.lower()
        t = re.sub(r"\s+", " ", t)
        t = re.sub(r"[^\w\u4e00-\u9fff]", " ", t)
        return t.strip()
