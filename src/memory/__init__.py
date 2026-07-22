"""
J.A.C. 持久记忆子系统（v1.0.0）

包导出。Phase 1-3：数据模型（models）、存储层（store）、记录判定（recorder）、
编排门面（manager）。严格对齐 docs/memory/schema.md（v1.0.0 锁定契约）。
"""

from .models import (
    MemoryFact,
    MemoryKind,
    MemorySource,
    RetrievalResult,
)
from .store import (
    LoadReport,
    MemoryFileCorrupt,
    MemoryStore,
    MemoryVersionIncompatible,
)
from .recorder import MemoryRecorder, RecordDecision
from .manager import MemoryManager
from . import prompts

__all__ = [
    "MemoryFact",
    "MemoryKind",
    "MemorySource",
    "RetrievalResult",
    "LoadReport",
    "MemoryStore",
    "MemoryFileCorrupt",
    "MemoryVersionIncompatible",
    "MemoryRecorder",
    "RecordDecision",
    "MemoryManager",
    "prompts",
]
