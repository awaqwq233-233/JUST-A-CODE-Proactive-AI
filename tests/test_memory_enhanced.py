"""记忆增强功能测试：持久化往返、向量/混合检索、PII 混合、基础记忆 seed。

不依赖 9B 大脑：
  - 向量检索直接传向量，不加载 fastembed；
  - PII 用本地 FakeBrain（只实现 think()）绕过 LocalBrain/cv2 导入问题；
  - 全部用 tmp_memory_dir 隔离，绝不触碰 ~/.jac。
"""

import json

from memory.store import MemoryStore
from memory.models import MemoryFact, MemoryKind, MemorySource
from memory.recorder import MemoryRecorder
from memory.manager import MemoryManager
from memory.seed import BASE_MEMORIES, seed_base_memories


class FakeBrain:
    """最小 brain 替身，仅实现 think()，返回可控的 PII JSON 字符串。"""

    def __init__(self, pii: bool):
        """初始化实例"""
        self.pii = pii

    def think(self, prompt, **kw):
        """推理"""
        return json.dumps({"pii": self.pii})


class DummyEmbedder:
    """占位 embedder：返回 None，避免测试中真实加载 fastembed / 联网下载模型。"""

    def embed_texts(self, texts, mode="passage"):
        """嵌入texts"""
        return None


def _fact(content, embedding=None, **kw):
    """事实"""
    return MemoryFact(
        content=content,
        kind=kw.pop("kind", MemoryKind.preference),
        source=kw.pop("source", MemorySource.inferred),
        embedding=embedding,
        **kw,
    )


# ---- 持久化往返（验证本地记忆重启后不丢）----

def test_persistence_roundtrip(tmp_memory_dir):
    """测试：persistenceroundtrip"""
    s = MemoryStore(base_dir=tmp_memory_dir)
    s.upsert(_fact("boss 是用户称呼"))
    s.close()  # 触发最终落盘
    s2 = MemoryStore(base_dir=tmp_memory_dir)  # 模拟重启后重新加载
    assert s2.stats()["count"] == 1
    assert s2.get_recent(1)[0].content == "boss 是用户称呼"
    s2.close()


# ---- 向量 / 混合检索 ----

def test_query_by_vector_rank(tmp_memory_dir):
    """测试：queryby向量rank"""
    s = MemoryStore(base_dir=tmp_memory_dir)
    f1 = _fact("jack 名字", embedding=[1.0, 0.0, 0.0])
    f2 = _fact("其它主题", embedding=[0.0, 1.0, 0.0])
    s.upsert(f1)
    s.upsert(f2)
    res = s.query_by_vector([1.0, 0.0, 0.0], k=2)
    assert res[0].fact.id == f1.id
    s.close()


def test_hybrid_blends_and_degrades(tmp_memory_dir):
    """测试：hybridblendsanddegrades"""
    s = MemoryStore(base_dir=tmp_memory_dir)
    f1 = _fact("jack 名字", embedding=[1.0, 0.0, 0.0],
               kind=MemoryKind.profile, source=MemorySource.manual)
    f2 = _fact("无关内容", embedding=[0.0, 1.0, 0.0])
    s.upsert(f1)
    s.upsert(f2)
    # 带向量：jack 向量命中 f1
    res = s.query_hybrid("jack", [1.0, 0.0, 0.0], k=2)
    assert res[0].fact.id == f1.id
    # 无向量：退化为纯关键词检索（f1 含 jack）
    res2 = s.query_hybrid("jack", None, k=2)
    assert res2 and res2[0].fact.id == f1.id
    s.close()


# ---- PII 规则 + LLM 混合 ----

def test_pii_llm_blocks_leaky_statement():
    """测试：pii大模型blocksleakystatement"""
    rec = MemoryRecorder()
    d = rec.classify("记住我同事老王明天生日", brain=FakeBrain(True))
    assert d.should_store is False
    assert d.pii is True
    assert d.reason == "pii_blocked"


def test_pii_llm_allows_normal_preference():
    """测试：pii大模型allows正常preference"""
    rec = MemoryRecorder()
    d = rec.classify("记住我喜欢喝咖啡", brain=FakeBrain(False))
    assert d.should_store is True
    assert d.pii is False


def test_pii_no_brain_conservative():
    """测试：piino大脑conservative"""
    rec = MemoryRecorder()
    # 无 brain：仅正则（未命中关系词）→ 不拦截、不误杀正常存储
    d = rec.classify("记住我同事老王明天生日", brain=None)
    assert d.should_store is True
    assert d.pii is False


# ---- 基础记忆 seed（固定 id 幂等）----

def test_seed_base_memories(tmp_memory_dir):
    """测试：seed基础memories"""
    store = MemoryStore(base_dir=tmp_memory_dir)
    mgr = MemoryManager(store=store, enabled=True, embedder=DummyEmbedder())
    seeded = seed_base_memories(mgr)
    assert seeded == len(BASE_MEMORIES)
    ids = {f.id for f in store.get_recent(limit=100)}
    assert {
        "base_user_address",
        "base_jac_name",
        "base_jac_identity",
        "base_jac_directives",
    } <= ids
    mgr.close()


def test_seed_idempotent(tmp_memory_dir):
    """测试：seedidempotent"""
    store = MemoryStore(base_dir=tmp_memory_dir)
    mgr = MemoryManager(store=store, enabled=True, embedder=DummyEmbedder())
    seed_base_memories(mgr)
    seed_base_memories(mgr)  # 第二次不应产生重复
    assert store.stats()["count"] == len(BASE_MEMORIES)
    mgr.close()
