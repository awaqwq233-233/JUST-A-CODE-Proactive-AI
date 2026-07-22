"""
Phase 0 smoke test: proves the test baseline runs before any feature code exists.

Scope:
  * test_smoke_baseline        -> pure `assert True`, no project deps required.
  * test_mock_brain_fixture    -> exercises conftest + pythonpath=src + the
                                  reusable mock_brain fixture (offline, JSON).

The memory package (src/memory) does not exist yet (Archi adds it in Phase 1),
so we deliberately do NOT `import memory` here — that import will be added by
later-phase tests once the code lands.
"""

import json


def test_smoke_baseline():
    """The absolute minimum: pytest can collect and run a test."""
    assert 1 == 1


def test_mock_brain_fixture(mock_brain):
    """mock_brain is offline and returns deterministic, parseable JSON."""
    # Offline: never tried to talk to LM Studio / Ollama / llama.cpp.
    assert mock_brain.backend == "mock"

    # Default reply is the deterministic JSON the recorder will parse.
    out = mock_brain.think("anything")
    decision = json.loads(out)
    assert decision == {"decision": "DAILY", "type": None, "content": "", "tags": []}

    # think_with_image mirrors the same offline JSON contract.
    out_img = mock_brain.think_with_image("anything", frame=None)
    assert json.loads(out_img)["decision"] == "DAILY"


def test_mock_brain_can_be_scripted(mock_brain):
    """Later-phase tests can force specific decisions via the queue helpers."""
    mock_brain.queue_decision("TASK", type="todo", content="buy milk", tags=["errand"])
    parsed = json.loads(mock_brain.think("remember to buy milk"))
    assert parsed["decision"] == "TASK"
    assert parsed["type"] == "todo"
    assert parsed["content"] == "buy milk"
    assert parsed["tags"] == ["errand"]

    # After the queued response is consumed, falls back to the default.
    assert json.loads(mock_brain.think("next"))["decision"] == "DAILY"
