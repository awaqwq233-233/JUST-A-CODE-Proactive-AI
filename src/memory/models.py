"""
J.A.C. 持久记忆子系统 —— 数据模型（Phase 1，v1.0.0）

严格对齐 docs/memory/schema.md（v1.0.0 锁定契约）：
  - 单条记忆 = ``MemoryFact``，顶层信封为 ``{"version", "facts"}``。
  - 必填 6 字段：id / content / kind / source / created_at / updated_at。
  - 可选 5 字段：weight / tags / pii / ttl / embedding。
  - 不再使用旧版的 MemoryRecord / MemoryType / importance(IntEnum) /
    consent_scoped / occurrences / last_accessed / access_count，
    也不再把 user_consent / 顶层 updated_at 写进 memory.json。

纯标准库，无第三方依赖，便于独立测试。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


def _now_iso() -> str:
    """UTC ISO8601，带 ``Z`` 后缀（对齐 schema.md 示例 ``2026-07-01T04:00:00Z``）。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class MemoryKind(str, Enum):
    """记忆分类（schema.md §4，RATIFY 五值）。"""

    profile = "profile"          # 用户画像事实
    preference = "preference"     # 偏好 / 习惯
    convention = "convention"    # 项目约定
    event = "event"              # 重要决策 / 事件
    topic = "topic"              # 反复出现的主题（频次升级产出）


class MemorySource(str, Enum):
    """记忆来源（schema.md §5，RATIFY 五值，非三值）。"""

    explicit = "explicit"        # 用户主动告知（A 显式意图）
    inferred = "inferred"        # 系统从对话推断出的偏好（B 隐式推断）
    recurring = "recurring"      # 频次升级产出（C 路径）
    judgment = "judgment"        # 判断引擎介入（D 决策）
    manual = "manual"            # 用户 / CLI 手动编辑（E）


# 检索注入时的中文标签
_MEMORY_KIND_LABELS: dict[MemoryKind, str] = {
    MemoryKind.profile: "用户画像",
    MemoryKind.preference: "偏好",
    MemoryKind.convention: "约定",
    MemoryKind.event: "事件",
    MemoryKind.topic: "主题",
}

# 必填字段（缺失 → 计入 invalid_facts）
REQUIRED_FIELDS = ("id", "content", "kind", "source", "created_at", "updated_at")


@dataclass
class MemoryFact:
    """单条长期记忆（v1.0.0 锁定字段）。

    字段严格对应 schema.md §3，无任何额外字段。``updated_at`` 同时承担
    recency + heat：检索访问时由 store 统一 bump（折叠旧版 last_accessed /
    access_count）。``occurrences`` 不持久化，由 recorder 运行时维护。
    """

    # 唯一 id（UUID4 字符串）
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    # 记忆文本，非空
    content: str = ""
    # 分类（见 MemoryKind）
    kind: MemoryKind = MemoryKind.profile
    # 来源（见 MemorySource）
    source: MemorySource = MemorySource.manual
    # 创建时间（UTC ISO8601，带 Z）
    created_at: str = field(default_factory=_now_iso)
    # 更新时间（UTC ISO8601，带 Z）；创建时 == created_at
    updated_at: str = field(default_factory=_now_iso)
    # 重要性 / 召回优先级 [0,1]，默认 0.5（inferred 应更低）
    weight: float = 0.5
    # 检索精度标签，默认空
    tags: list[str] = field(default_factory=list)
    # 敏感标记，默认 false（非 PII）
    pii: bool = False
    # 易失类过期时间（ISO8601）或 null
    ttl: Optional[str] = None
    # 保留字段，未来向量库衔接点，v1 恒 null
    embedding: Optional[list[float]] = None

    def to_dict(self) -> dict:
        """序列化为可 JSON 化的字典（枚举转 value / bool 保持 / float 保持）。"""
        return {
            "id": self.id,
            "content": self.content,
            "kind": self.kind.value,
            "source": self.source.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "weight": self.weight,
            "tags": list(self.tags),
            "pii": self.pii,
            "ttl": self.ttl,
            "embedding": list(self.embedding) if self.embedding is not None else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MemoryFact":
        """从字典（JSON 反序列化）构造，对枚举 / 数值做容错默认值。

        调用方需先确保 6 个必填字段存在（见 ``MemoryStore._parse_facts``）。
        """
        raw_kind = d.get("kind")
        try:
            kind = MemoryKind(raw_kind) if raw_kind is not None else MemoryKind.profile
        except ValueError:
            kind = MemoryKind.profile

        raw_source = d.get("source")
        try:
            source = MemorySource(raw_source) if raw_source is not None else MemorySource.manual
        except ValueError:
            source = MemorySource.manual

        weight = d.get("weight", 0.5)
        try:
            weight = float(weight)
        except (ValueError, TypeError):
            weight = 0.5

        embedding = d.get("embedding", None)
        if embedding is not None:
            try:
                embedding = [float(x) for x in embedding]
            except (ValueError, TypeError):
                embedding = None

        now = _now_iso()
        return cls(
            id=d.get("id") or str(uuid.uuid4()),
            content=d.get("content", ""),
            kind=kind,
            source=source,
            created_at=d.get("created_at") or now,
            updated_at=d.get("updated_at") or now,
            weight=weight,
            tags=list(d.get("tags", []) or []),
            pii=bool(d.get("pii", False)),
            ttl=d.get("ttl"),
            embedding=embedding,
        )


@dataclass
class RetrievalResult:
    """检索结果：命中的事实 + 相关性分数。"""

    fact: MemoryFact
    score: float

    def to_prompt_line(self) -> str:
        """返回注入 ``system_prompt`` 用的单行文本，形如 ``[偏好] 内容``。"""
        label = _MEMORY_KIND_LABELS.get(self.fact.kind, self.fact.kind.value)
        return f"[{label}] {self.fact.content}"
