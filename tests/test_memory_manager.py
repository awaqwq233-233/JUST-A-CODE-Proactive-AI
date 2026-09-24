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


class _OfflineEmbedder:
    """离线空 embedder：让测试完全不触碰 fastembed / HuggingFace。

    为什么必须注入（2026-09-24 定位）：`MemoryEmbedder` 虽是 lazy 加载，但
    `fastembed.TextEmbedding(...)` **构造时**会向 HuggingFace 查询模型 revision；
    在无网络或代理异常的环境下会进入 3~9 秒的重试链（实测 stderr：
    `ProxyError ... Tunnel connection failed: 502 Bad Gateway` 后 `sleeping for 9.0
    seconds, 1 retries left`），把「显式保存落库」这一本应毫秒级的断言拖过超时。

    本类用例验证的是「规则阶段是否落库」「限流是否误伤显式保存」，**与向量检索无关**，
    所以注入返回 None 的空实现（`MemoryManager` 已判 None 即跳过向量）属于正确隔离，
    而不是放宽断言。真实 embedder 路径由 `test_retrieve_injects_matched_memory` 覆盖。
    """

    def embed_texts(self, texts, mode="passage"):
        """返回 None → 调用方跳过向量，走纯关键词路径。"""
        return None


def _wait_until(predicate, timeout=15.0, interval=0.05):
    """轮询等待 predicate() 为真；在 timeout 内成立返回 True，否则 False。

    为什么不能用固定 `time.sleep(0.5)` 就断言（2026-09-24 修复）：
    `MemoryManager` 的后台 worker 在**首次落库前**会 lazy 加载本地 embedding 模型
    （fastembed，实测冷启动约 1.3s，日志为「[Embedder] 已加载向量模型 … 维度 384」），
    而落库链路是 `record_turn → 队列 → worker → classify → embed → store.upsert`。
    固定 sleep 会先于落库完成就读取 stats，导致「生产代码完全正确、测试却假失败」。
    轮询把等待改成「以结果为条件」，机器快慢都不再影响判定。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class _RaiseBrain:
    """任何 think 调用都抛，用于证明规则阶段不触达 LLM。"""

    backend = "mock"

    def think(self, *a, **k):
        """推理"""
        raise RuntimeError("LLM must NOT be called for rule-stage")

    def think_with_image(self, *a, **k):
        """推理带图像"""
        raise RuntimeError("LLM must NOT be called for rule-stage")


def test_retrieve_empty_returns_blank(tmp_memory_dir):
    """测试：检索emptyreturnsblank"""
    mgr = MemoryManager(base_dir=tmp_memory_dir, enabled=True)
    assert mgr.retrieve_for_prompt("随便说说") == ""
    mgr.close()


def test_retrieve_injects_matched_memory(tmp_memory_dir):
    """测试：检索injectsmatched记忆"""
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
    """测试：录音turnnonblockingstores"""
    mgr = MemoryManager(
        base_dir=tmp_memory_dir, enabled=True,
        min_classify_interval=0.0, brain=_RaiseBrain(),
        embedder=_OfflineEmbedder(),   # 隔离向量链路（见该类 docstring）
    )
    # 显式保存 → 规则阶段落库（不依赖 LLM）
    mgr.record_turn("记住我喜欢爬山", "好的")
    # 后台 worker 异步处理，轮询等待其落库
    ok = _wait_until(lambda: mgr.stats["count"] >= 1, timeout=5.0)
    mgr.flush()
    assert ok, f"显式保存未在超时内落库: {mgr.stats}"
    assert any(f.content == "我喜欢爬山" for f in mgr.store.get_recent())
    mgr.close()


def test_ratelimit_keeps_explicit_saves(tmp_memory_dir):
    """测试：ratelimitkeepsexplicitsaves"""
    mgr = MemoryManager(
        base_dir=tmp_memory_dir, enabled=True,
        min_classify_interval=100.0, brain=_RaiseBrain(),
        embedder=_OfflineEmbedder(),   # 隔离向量链路（见该类 docstring）
    )
    mgr.record_turn("记住我喜欢喝茶", "好的")
    mgr.record_turn("记住我不住在北京", "好的")  # 间隔极短，若限流误伤会丢
    # 限流只作用于 LLM 分支：两条显式保存都必须落库（同样要等 worker 处理完）
    ok = _wait_until(lambda: mgr.stats["count"] >= 2, timeout=5.0)
    mgr.flush()
    assert ok, f"限流误伤了显式保存（应落 2 条）: {mgr.stats}"
    mgr.close()


def test_classify_exception_caught(tmp_memory_dir):
    """测试：classify异常caught"""
    class _BoomBrain:
        backend = "mock"

        def think(self, *a, **k):
            """推理"""
            raise RuntimeError("boom")

        def think_with_image(self, *a, **k):
            """推理带图像"""
            raise RuntimeError("boom")

    mgr = MemoryManager(
        base_dir=tmp_memory_dir, enabled=True,
        min_classify_interval=0.0, brain=_BoomBrain(),
        embedder=_OfflineEmbedder(),   # 隔离向量链路（见该类 docstring）
    )
    # 不抛异常即通过（纯弱意图，无显式保存词）
    mgr.record_turn("以后每天多喝水", "好的")
    # 反向等待：给足时间让 worker 处理完，期间一旦出现记录就失败。
    # （直接 sleep 后断言 count==0 会因为「还没处理完」而假通过，失去回归价值。）
    assert not _wait_until(lambda: mgr.stats["count"] > 0, timeout=3.0), \
        f"弱意图不应落库，却出现了记录: {mgr.stats}"
    mgr.flush()
    mgr.close()


def test_disabled_manager_is_noop():
    """测试：disabled管理器是否noop"""
    mgr = MemoryManager(enabled=False)
    assert mgr.retrieve_for_prompt("x") == ""
    mgr.record_turn("记住点什么", "好的")  # 不崩
    assert mgr.stats is None
    mgr.close()
