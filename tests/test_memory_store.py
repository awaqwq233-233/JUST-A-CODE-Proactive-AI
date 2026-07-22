"""MemoryStore 单元测试（v1.0.0 契约）。

覆盖：envelope 形状、版本宽松/拒绝、invalid_facts、损坏恢复、
两级清空 + 防复活（Cody #2/#4）、secure 擦除（Cody #9）、归档留存。

不依赖网络或 LM Studio；所有测试用 tmp_memory_dir 隔离，绝不触碰 ~/.jac。
"""

import json
import os

import pytest

from memory.store import (
    MemoryStore,
    MemoryFileCorrupt,
    MemoryVersionIncompatible,
)
from memory.models import MemoryFact, MemoryKind, MemorySource


def _fact(content, kind=MemoryKind.preference, source=MemorySource.inferred, **kw):
    return MemoryFact(content=content, kind=kind, source=source, **kw)


# ---- envelope 形状 + 往返 ----

def test_add_reload_roundtrip(tmp_memory_dir):
    s = MemoryStore(base_dir=tmp_memory_dir)
    s.upsert(_fact("我喜欢喝茶"))
    s.flush()
    s.close()
    s2 = MemoryStore(base_dir=tmp_memory_dir)
    assert len(s2.last_load_report.facts) == 1
    assert s2.last_load_report.facts[0].content == "我喜欢喝茶"
    # envelope 是 {version, facts}
    with open(os.path.join(tmp_memory_dir, "memory.json"), encoding="utf-8") as f:
        env = json.load(f)
    assert env["version"] == "1.0.0"
    assert isinstance(env["facts"], list)
    assert "schema_version" not in env and "user_consent" not in env
    s2.close()


# ---- 版本策略（Cody #3） ----

def test_missing_version_lenient(tmp_memory_dir):
    with open(os.path.join(tmp_memory_dir, "memory.json"), "w", encoding="utf-8") as f:
        json.dump({"facts": [_fact("x").to_dict()]}, f)
    s = MemoryStore(base_dir=tmp_memory_dir)
    assert len(s.last_load_report.facts) == 1
    s.close()


def test_v0_0_0_lenient(tmp_memory_dir):
    with open(os.path.join(tmp_memory_dir, "memory.json"), "w", encoding="utf-8") as f:
        json.dump({"version": "0.0.0", "facts": [_fact("x").to_dict()]}, f)
    s = MemoryStore(base_dir=tmp_memory_dir)
    assert len(s.last_load_report.facts) == 1
    s.close()


def test_major_mismatch_raises(tmp_memory_dir):
    with open(os.path.join(tmp_memory_dir, "memory.json"), "w", encoding="utf-8") as f:
        json.dump({"version": "9.0.0", "facts": []}, f)
    with pytest.raises(MemoryVersionIncompatible):
        MemoryStore(base_dir=tmp_memory_dir)


# ---- invalid_facts ----

def test_invalid_facts_reported(tmp_memory_dir):
    with open(os.path.join(tmp_memory_dir, "memory.json"), "w", encoding="utf-8") as f:
        json.dump({
            "version": "1.0.0",
            "facts": [
                _fact("good").to_dict(),
                {"content": "no id", "kind": "topic", "source": "recurring",
                 "created_at": "2020-01-01T00:00:00+00:00",
                 "updated_at": "2020-01-01T00:00:00+00:00"},
            ],
        }, f)
    s = MemoryStore(base_dir=tmp_memory_dir)
    assert len(s.last_load_report.facts) == 1
    assert len(s.last_load_report.invalid_facts) == 1
    s.close()


# ---- 损坏恢复 ----

def test_corrupt_main_recovers_from_bak(tmp_memory_dir):
    # .bak 语义：永远是「上一次合法主文件」的副本。
    # 因此先写 first 并 flush（main=first, .bak=空），
    # 再写 second 并 flush（main=first+second, .bak=first）。
    good = MemoryStore(base_dir=tmp_memory_dir)
    good.upsert(_fact("first"))
    good.flush()
    good.upsert(_fact("second"))
    good.flush()
    good.close()
    assert os.path.exists(os.path.join(tmp_memory_dir, "memory.json.bak"))
    # 主文件损坏
    with open(os.path.join(tmp_memory_dir, "memory.json"), "w", encoding="utf-8") as f:
        f.write("{ this is not json ")
    s = MemoryStore(base_dir=tmp_memory_dir)
    # 应从 .bak 恢复出 last-good（"first"）
    assert any(f.content == "first" for f in s.last_load_report.facts)
    s.close()


def test_corrupt_main_no_bak_raises(tmp_memory_dir):
    with open(os.path.join(tmp_memory_dir, "memory.json"), "w", encoding="utf-8") as f:
        f.write("broken")
    with pytest.raises(MemoryFileCorrupt):
        MemoryStore(base_dir=tmp_memory_dir)


def test_no_stray_tmp_after_flush(tmp_memory_dir):
    s = MemoryStore(base_dir=tmp_memory_dir)
    s.upsert(_fact("x"))
    s.flush()
    leftovers = [fn for fn in os.listdir(tmp_memory_dir) if fn.endswith(".tmp")]
    assert leftovers == []
    s.close()


# ---- 两级清空 + 防复活（Cody #2/#4） ----

def _seed_four(tmp_memory_dir):
    s = MemoryStore(base_dir=tmp_memory_dir)
    ex = _fact("explicit fact", kind=MemoryKind.preference, source=MemorySource.explicit, tags=["t"])
    inf = _fact("inferred fact", kind=MemoryKind.profile, source=MemorySource.inferred, tags=["t"])
    pii_f = MemoryFact(content="小明是我儿子", kind=MemoryKind.profile,
                       source=MemorySource.explicit, tags=["p"], pii=True)
    top = _fact("a topic", kind=MemoryKind.topic, source=MemorySource.recurring, tags=["topic"])
    for fct in (ex, inf, pii_f, top):
        s.upsert(fct)
    s.flush()
    s._write_archive([ex, inf])  # 让归档也含这些 id
    return s


def test_clear_source_inferred_purges_replicas(tmp_memory_dir):
    s = _seed_four(tmp_memory_dir)
    n = s.clear(source=MemorySource.inferred)
    assert n == 1
    s.flush()
    reload = MemoryStore(base_dir=tmp_memory_dir)
    remaining = {f.source for f in reload.last_load_report.facts}
    assert MemorySource.inferred not in remaining
    assert MemorySource.explicit in remaining
    # .bak 不得复活被清事实
    bak = os.path.join(tmp_memory_dir, "memory.json.bak")
    if os.path.exists(bak):
        with open(bak, encoding="utf-8") as f:
            bak_srcs = {x.get("source") for x in json.load(f).get("facts", [])}
        assert "inferred" not in (bak_srcs or set())
    # 归档不得复活
    arc_srcs = set()
    for fn in os.listdir(tmp_memory_dir):
        if fn.startswith("memory_archive_"):
            with open(os.path.join(tmp_memory_dir, fn), encoding="utf-8") as f:
                arc_srcs |= {x.get("source") for x in json.load(f).get("facts", [])}
    assert "inferred" not in (arc_srcs or set())
    reload.close()


def test_clear_pii(tmp_memory_dir):
    s = _seed_four(tmp_memory_dir)
    n = s.clear(pii=True)
    assert n == 1
    reload = MemoryStore(base_dir=tmp_memory_dir)
    assert all(not f.pii for f in reload.last_load_report.facts)
    reload.close()


def test_clear_kind_topic(tmp_memory_dir):
    s = _seed_four(tmp_memory_dir)
    n = s.clear(kind=MemoryKind.topic)
    assert n == 1
    reload = MemoryStore(base_dir=tmp_memory_dir)
    assert all(f.kind != MemoryKind.topic for f in reload.last_load_report.facts)
    reload.close()


def test_clear_all(tmp_memory_dir):
    s = _seed_four(tmp_memory_dir)
    n = s.clear()
    assert n == 4
    reload = MemoryStore(base_dir=tmp_memory_dir)
    assert len(reload.last_load_report.facts) == 0
    reload.close()


def test_delete_purges_bak(tmp_memory_dir):
    s = _seed_four(tmp_memory_dir)
    target = next(f for f in s.get_recent() if f.source == MemorySource.inferred)
    ok = s.delete(target.id)
    assert ok
    assert not os.path.exists(os.path.join(tmp_memory_dir, "memory.json.bak"))
    reload = MemoryStore(base_dir=tmp_memory_dir)
    assert all(f.id != target.id for f in reload.last_load_report.facts)
    reload.close()


# ---- 归档留存 ----

def test_archive_retention_drops_old(tmp_memory_dir):
    s = MemoryStore(base_dir=tmp_memory_dir)
    # 制造一个很旧的归档月份
    import shutil
    old_path = os.path.join(tmp_memory_dir, "memory_archive_200001.json")
    with open(old_path, "w", encoding="utf-8") as f:
        json.dump({"version": "1.0.0", "facts": [_fact("old").to_dict()]}, f)
    # 制造一个当前月归档，确保轮转逻辑看到两个
    cur_path = os.path.join(tmp_memory_dir, "memory_archive_209901.json")
    with open(cur_path, "w", encoding="utf-8") as f:
        json.dump({"version": "1.0.0", "facts": [_fact("future").to_dict()]}, f)
    # 触发留存清理（compact 或显式调用）
    s._enforce_archive_retention()
    remaining = [fn for fn in os.listdir(tmp_memory_dir) if fn.startswith("memory_archive_")]
    assert "memory_archive_200001.json" not in remaining


# ---- stats ----

def test_stats(tmp_memory_dir):
    s = _seed_four(tmp_memory_dir)
    st = s.stats()
    assert st["count"] == 4
    assert st["pii_count"] == 1
    # ex 与 pii_f 均为 source=explicit → 共 2 条
    assert st["by_source"].get("explicit") == 2
    s.close()
