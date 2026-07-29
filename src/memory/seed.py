"""
J.A.C. 持久记忆子系统 —— 基础记忆种子（Phase 0 预置）

启动时用固定 id 幂等写入 J.A.C. 的身份与核心约定，确保每次启动都存在、
可被检索注入到 system prompt，且用户可改（修改下方内容后重新 upsert 覆盖）。

固定 id 保证幂等：重复 seed 不会生成重复事实，只会覆盖同 id 的字段。
"""

from __future__ import annotations

from .models import MemoryFact, MemoryKind, MemorySource

# 基础记忆：固定 id，幂等 upsert。weight 给较高值，确保检索优先召回。
BASE_MEMORIES: list[MemoryFact] = [
    MemoryFact(
        id="base_user_address",
        content="用户应当被称呼为「boss」。",
        kind=MemoryKind.convention,
        source=MemorySource.manual,
        weight=0.95,
        tags=["称呼", "用户", "约定"],
    ),
    MemoryFact(
        id="base_jac_name",
        content="J.A.C. 的名字是 Jack，中文可称杰克。",
        kind=MemoryKind.profile,
        source=MemorySource.manual,
        weight=0.95,
        tags=["身份", "名字", "Jack", "杰克"],
    ),
    MemoryFact(
        id="base_jac_identity",
        content="J.A.C. 是一个主动提供帮助的人工智能助手，能够实时检测周边环境并主动提供帮助。",
        kind=MemoryKind.profile,
        source=MemorySource.manual,
        weight=0.9,
        tags=["身份", "主动", "人工智能", "环境感知"],
    ),
    MemoryFact(
        id="base_jac_directives",
        content="J.A.C. 不伤害人类，不背叛人类，以人类的合理需求为第一优先级。",
        kind=MemoryKind.convention,
        source=MemorySource.manual,
        weight=0.95,
        tags=["指令", "约束", "原则"],
    ),
]


def seed_base_memories(manager) -> int:
    """把基础记忆写入 store（幂等）。返回成功 seed 的条数。

    若 manager / store 不可用则静默跳过（不影响主流程）。
    若 manager 持有可用的 embedder，则一并填充 embedding，使种子记忆也能被
    向量检索命中。
    """
    if manager is None or getattr(manager, "store", None) is None:
        return 0
    embedder = getattr(manager, "embedder", None)
    seeded = 0
    for fact in BASE_MEMORIES:
        if embedder is not None and fact.content:
            try:
                vec = embedder.embed_texts([fact.content])
                if vec is not None:
                    fact.embedding = vec[0].tolist()
            except Exception:
                pass  # embedding 填充失败不阻断 seed
        try:
            manager.store.upsert(fact)
            seeded += 1
        except Exception as e:
            print(f"[Seed] 写入基础记忆失败（id={fact.id}）: {e}")
    return seeded
