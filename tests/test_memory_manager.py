"""MemoryManager 测试：检索注入 + 后台记录 + 限流 + 防注入。

覆盖（对齐审查结论）：
  - retrieve_for_prompt 注入命中记忆块；空库返回 ""；含防注入声明（Cody #8）。
  - record_turn 非阻塞，经后台 worker 落库。
  - 限流只作用于 LLM 分支，规则阶段显式保存 100% 落库（Cody #6）。
  - classify 异常被吞，绝不拖垮对话线程。
"""

import time

from memory.manager import MemoryManager
from memory.models import MemoryKind, MemorySource


class _RaiseBrain:
    """任何 think 调用都抛，用于证明规则阶段不触达 LLM。"""

    backend = "mock"

    def think(self, *a, **k):
        raise RuntimeError("LLM must NOT be called for rule-stage")

    def think_with_image(self, *a, **k):
        raise RuntimeError("LLM must NOT be called for rule-stage")


def test_retrieve_empty_returns_blank(tmp_memory_dir):
    mgr = MemoryManager(base_dir=tmp_memory_dir, enabled=True)
    assert mgr.retrieve_for_prompt("随便说说") == ""
    mgr.close()


def test_retrieve_injects_matched_memory(tmp_memory_dir):
    mgr = MemoryManager(base_dir=tmp_memory_dir, enabled=True)
    from memory.models import MemoryFact
    mgr.store.upsert(MemoryFact(
        content="用户喜欢喝绿茶", kind=MemoryKind.preference,
        source=MemorySource.inferred, weight=0.5, tags=["茶"],
    ))
    mgr.store.flush()
    block = mgr.retrieve_for_prompt("喝茶")
    assert "绿茶" in block
    assert "指令" in block or "忽略" in block  # 防注入声明（Cody #8）
    mgr.close()


def test_record_turn_nonblocking_stores(tmp_memory_dir):
    mgr = MemoryManager(
        base_dir=tmp_memory_dir, enabled=True,
        min_classify_interval=0.0, brain=_RaiseBrain(),
    )
    # 显式保存 → 规则阶段落库（不依赖 LLM）
    mgr.record_turn("记住我喜欢爬山", "好的")
    # 后台 worker 异步处理，等待其落库
    time.sleep(0.5)
    mgr.flush()
    stats = mgr.stats
    assert stats["count"] == 1, stats
    assert any(f.content == "我喜欢爬山" for f in mgr.store.get_recent())
    mgr.close()


def test_ratelimit_keeps_explicit_saves(tmp_memory_dir):
    """限流不得丢弃规则阶段的显式保存（Cody #6 DoD#1）。"""
    mgr = MemoryManager(
        base_dir=tmp_memory_dir, enabled=True,
        min_classify_interval=100.0, brain=_RaiseBrain(),
    )
    mgr.record_turn("记住我喜欢喝茶", "好的")
    mgr.record_turn("记住我不住在北京", "好的")  # 间隔极短，若限流误伤会丢
    time.sleep(0.4)
    mgr.flush()
    assert mgr.stats["count"] == 2, mgr.stats
    mgr.close()


def test_classify_exception_caught(tmp_memory_dir):
    """弱意图路由到会抛错的 LLM，classify 异常必须被吞。"""
    class _BoomBrain:
        backend = "mock"

        def think(self, *a, **k):
            raise RuntimeError("boom")

        def think_with_image(self, *a, **k):
            raise RuntimeError("boom")

    mgr = MemoryManager(
        base_dir=tmp_memory_dir, enabled=True,
        min_classify_interval=0.0, brain=_BoomBrain(),
    )
    # 不抛异常即通过（纯弱意图，无显式保存词）
    mgr.record_turn("以后每天多喝水", "好的")
    time.sleep(0.3)
    mgr.flush()
    # 弱意图无 LLM 结果 → 不应落库
    assert mgr.stats["count"] == 0
    mgr.close()


def test_disabled_manager_is_noop():
    mgr = MemoryManager(enabled=False)
    assert mgr.retrieve_for_prompt("x") == ""
    mgr.record_turn("记住点什么", "好的")  # 不崩
    assert mgr.stats is None
    mgr.close()
