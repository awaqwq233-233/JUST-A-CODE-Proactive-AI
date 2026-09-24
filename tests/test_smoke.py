"""Phase 0 smoke test：验证测试基线可跑，且 conftest 的 mock_brain 可用。

Scope:
  * test_smoke_baseline             -> 纯 `assert True`，不依赖任何项目代码。
  * test_mock_brain_fixture         -> 验证 conftest + pythonpath=src + mock_brain
                                       fixture（离线、返回可解析的确定性 JSON）。
  * test_mock_brain_can_be_scripted -> 验证 mock_brain 的队列编排能力。

⚠️ 契约同步说明（2026-09-24 修复）：
mock_brain 返回的 JSON 契约已随 `src/memory/recorder.py` 演进为
    {"should_store", "reason", "kind", "confidence", "content", "tags"}
而本文件仍停留在 memory 子系统 Phase 0 的早期草案（{"decision", "type", ...}），
`mock_brain.queue_decision()` 也早已改成 `queue_decision(should_store, *, kind=...)`。
两处不同步导致本文件 2 个用例长期假失败（代码正确、断言过时）。
真值来源是 `MemoryRecorder._parse_llm_json()` 与 `src/memory/prompts.py` 的 schema 定义。
"""

import json


def test_smoke_baseline():
    """测试：smokebaseline"""
    assert 1 == 1


def test_mock_brain_fixture(mock_brain):
    """测试：mock大脑fixture"""
    # Offline: never tried to talk to LM Studio / Ollama / llama.cpp.
    assert mock_brain.backend == "mock"

    # 默认回复是确定性 JSON，形状与 MemoryRecorder 的解析契约一致
    out = mock_brain.think("anything")
    decision = json.loads(out)
    assert decision == {
        "should_store": False,
        "kind": None,
        "content": "",
        "tags": [],
        "reason": "low_confidence",
        "confidence": 0.3,
    }

    # think_with_image 复用同一份离线 JSON 契约
    out_img = mock_brain.think_with_image("anything", frame=None)
    assert json.loads(out_img)["should_store"] is False


def test_mock_brain_can_be_scripted(mock_brain):
    """测试：mock大脑能否bescripted"""
    # kind 必须落在 MemoryKind 的合法取值内：profile | preference | convention | event | topic
    mock_brain.queue_decision(True, kind="event", content="buy milk", tags=["errand"])
    parsed = json.loads(mock_brain.think("remember to buy milk"))
    assert parsed["should_store"] is True
    assert parsed["kind"] == "event"
    assert parsed["content"] == "buy milk"
    assert parsed["tags"] == ["errand"]

    # 队列消费完后回落默认（保守不记）
    assert json.loads(mock_brain.think("next"))["should_store"] is False
