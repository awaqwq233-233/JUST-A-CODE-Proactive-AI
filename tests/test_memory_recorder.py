"""MemoryRecorder 黄金数据集测试（记录判定 DoD）。

DoD 硬指标（对齐 docs/memory_test_plan.md）：
  - A 类（显式保存）100% 落库；
  - 排除项（闲聊/一次性问答/任务结果/敏感人物/原始转录）100% 不记；
  - 频次阈值（2 次不晋升，3 次晋升 recurring_topic）；
  - pii 双层门控（默认拦截人物身份；capture+explicit 才允许）；
  - 规则顺序 oracle：SMALLTALK→排除，EXPLICIT→记，QUESTION→排除，
    PREFERENCE/DECISION→记，弱意图→需 LLM；
  - reason 非空且 kind 同态（should_store=False ⇒ kind=None）；
  - normalize_topic 确定性（幂等）。

不调用真实 LLM：黄金数据集全部走规则阶段（brain=None），弱意图在
无 LLM 时保守不记。
"""

import json
import os

from memory.recorder import MemoryRecorder, RecordDecision
from memory.models import MemoryKind, MemorySource


FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "record_samples.jsonl")


def _load_samples():
    out = []
    with open(FIXTURE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


SAMPLES = _load_samples()
POSITIVES = [s for s in SAMPLES if s["expect"] == "store"]
NEGATIVES = [s for s in SAMPLES if s["expect"] == "skip"]


def test_golden_coverage():
    # 数据完整性：正负各 >= 20
    assert len(POSITIVES) >= 20, len(POSITIVES)
    assert len(NEGATIVES) >= 20, len(NEGATIVES)


def test_positive_100pct_store():
    # DoD#1：A 类（含显式保存/偏好/决策/画像）必须 100% 落库，
    # 且 kind 同态（落库则 kind 必非空）。精确 kind 由 recorder 内部
    # 映射决定，不在黄金数据集里逐条约束（避免与实现细节过耦合）。
    rec = MemoryRecorder()
    for s in POSITIVES:
        d = rec.classify(s["user"])
        assert d.should_store, f"应记却未记: {s['user']!r}"
        assert d.kind is not None, f"落库却 kind 为空: {s['user']!r}"


def test_negative_100pct_skip():
    rec = MemoryRecorder()
    for s in NEGATIVES:
        d = rec.classify(s["user"])
        assert not d.should_store, f"应忽略却记了: {s['user']!r}"
        # pii 类：默认拦截且标记 pii=True
        if s.get("pii"):
            assert d.pii is True, f"pii 未标记: {s['user']!r}"
        # kind 同态：不记则 kind 必为 None
        assert d.kind is None, f"未记却给 kind: {s['user']!r} kind={d.kind}"


def test_explicit_save_always_stores():
    rec = MemoryRecorder()
    for phrase in ("记住我喜欢喝茶", "记一下开会", "别忘了交税", "请记住密码", "帮我记住址", "记着关灯"):
        d = rec.classify(phrase)
        assert d.should_store and d.source == MemorySource.explicit, phrase


def test_smalltalk_excluded():
    rec = MemoryRecorder()
    for phrase in ("你好", "讲个笑话", "再见", "谢谢"):
        assert not rec.classify(phrase).should_store, phrase


def test_question_excluded():
    rec = MemoryRecorder()
    assert not rec.classify("今天天气怎么样？").should_store
    assert not rec.classify("什么是 transformer").should_store


def test_weak_intent_needs_llm():
    # 无 LLM → 保守不记（纯弱意图，无显式保存词）
    rec = MemoryRecorder()
    d = rec.classify("以后每天多喝水")
    assert not d.should_store
    assert d.reason == "low_confidence"


def test_recurrence_threshold_2_vs_3():
    rec = MemoryRecorder(recurrence_threshold=3)
    assert rec.classify("去公园散步").should_store is False   # 1
    assert rec.classify("去公园散步").should_store is False   # 2
    d3 = rec.classify("去公园散步")                            # 3 → 晋升
    assert d3.should_store and d3.kind == MemoryKind.topic


def test_recurrence_no_double_promote():
    rec = MemoryRecorder(recurrence_threshold=3)
    outs = [rec.classify("并发主题测试语句") for _ in range(3)]
    promoted = [o for o in outs if o.should_store and o.kind == MemoryKind.topic]
    assert len(promoted) == 1


def test_pii_default_blocked():
    rec = MemoryRecorder(capture_person_id=False)
    d = rec.classify("小明是我儿子")
    assert d.should_store is False and d.pii is True


def test_pii_capture_explicit_allowed():
    rec = MemoryRecorder(capture_person_id=True)
    d = rec.classify("记住小明是我儿子")
    assert d.should_store and d.pii is True


def test_reason_nonempty():
    rec = MemoryRecorder()
    for s in SAMPLES:
        d = rec.classify(s["user"])
        assert d.reason, f"reason 为空: {s['user']!r}"


def test_normalize_topic_idempotent():
    rec = MemoryRecorder()
    x = "以后 每天 提醒 我 喝水！！"
    once = rec.normalize_topic(x)
    twice = rec.normalize_topic(once)
    assert once == twice
    assert isinstance(once, str) and once


def test_parse_llm_json_balanced_braces():
    raw = '这是前置文字 {"should_store": true, "kind": "topic", "reason": "x"} 后续文字 {"其它": 1}'
    out = MemoryRecorder._parse_llm_json(raw)
    assert out is not None
    assert out.get("should_store") is True
    assert out.get("kind") == "topic"


def test_parse_llm_json_nested_braces():
    # 值内含大括号（如内容里写了 { }）也应正确成对
    raw = '{"should_store": true, "kind": "topic", "content": "a {b} c", "tags": ["t"]}'
    out = MemoryRecorder._parse_llm_json(raw)
    assert out is not None
    assert out.get("content") == "a {b} c"


def test_parse_llm_json_length_cap():
    huge = "{" + "x" * 50000 + "}"
    out = MemoryRecorder._parse_llm_json(huge)
    # 超过 20000 截断后仍尝试解析（截断版本非法 JSON → None，不崩溃）
    assert out is None
