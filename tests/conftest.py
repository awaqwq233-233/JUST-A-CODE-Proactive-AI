"""
Shared pytest fixtures for the J.A.C. memory test-suite (Phase 0 baseline + Phase 6).

Fixtures provided:
  * mock_brain    -> an OFFLINE LocalBrain stand-in returning deterministic,
                      test-controllable JSON in the SHAPE MemoryRecorder
                      actually parses (should_store / kind / reason / confidence /
                      content / tags).
  * tmp_memory_dir -> an isolated temp directory (str) for store persistence
                      tests, so ~/.jac is never touched.

Harness-hardening note (Phase 0 fix): ``src/brain/llm.py`` imports heavy,
optional vision/audio deps (e.g. ``cv2``) at module load. Those are NOT
required by memory tests, which run fully offline. To keep the suite
collectable on lean CI / dev machines, ``LocalBrain`` is imported lazily and
the ``mock_brain`` fixture *skips* (not errors) when the import is
unavailable. This lets pure MemoryStore unit tests run green without cv2.
"""

import json

import pytest

try:
    from brain.llm import LocalBrain

    _HAVE_BRAIN = True
except Exception:  # pragma: no cover - optional heavy deps (cv2, ...) absent
    _HAVE_BRAIN = False


if _HAVE_BRAIN:

    class MockBrain(LocalBrain):
        """Offline stand-in for LocalBrain used in tests.

        Construction delegates to ``LocalBrain(backend="mock")``, which guarantees
        no network probe and no model load. We then override ``think()`` /
        ``think_with_image()`` to return a *deterministic*, *scriptable* JSON
        string in the exact shape ``MemoryRecorder._llm_stage`` parses:

            {"should_store": bool, "reason": str, "kind": str|null,
             "confidence": float, "content": str, "tags": [str]}

        Reusable controls:
            brain.queue_response("raw text ...")          # force next output
            brain.queue_decision(True, kind="topic", content="...")  # force a decision
            MockBrain(default_should_store=True, kind="preference")  # default reply
        """

        def __init__(self, default_should_store=False, **default_fields):
            super().__init__(backend="mock")  # guaranteed offline, no model/network
            self.default_should_store = default_should_store
            self.default_fields = default_fields
            self._queue = []  # FIFO of raw strings to return before falling back

        # -- test-control helpers -------------------------------------------------
        def queue_response(self, raw_text):
            """Queue a raw string to be returned by the next think() call."""
            self._queue.append(raw_text)

        def queue_decision(
            self, should_store, *, kind=None, reason="user_stated",
            content="", tags=None, confidence=0.5,
        ):
            """Queue a JSON decision string (recorder-compatible shape) for next call."""
            self.queue_response(json.dumps(
                {
                    "should_store": should_store,
                    "reason": reason,
                    "kind": kind,
                    "confidence": confidence,
                    "content": content,
                    "tags": tags or ([kind] if kind else []),
                },
                ensure_ascii=False,
            ))

        # -- internal -------------------------------------------------------------
        def _default_json(self):
            payload = dict(self.default_fields)
            payload.setdefault("kind", None)
            payload.setdefault("content", "")
            payload.setdefault("tags", [])
            payload.setdefault("reason", "low_confidence")
            payload.setdefault("confidence", 0.3)
            return json.dumps(
                {"should_store": self.default_should_store, **payload},
                ensure_ascii=False,
            )

        # -- overridden interface -------------------------------------------------
        def think(self, prompt, system_prompt="", temperature=0.7, max_tokens=120):
            if self._queue:
                return self._queue.pop(0)
            return self._default_json()

        def think_with_image(
            self, prompt, frame=None, system_prompt="", temperature=0.7, max_tokens=200
        ):
            if self._queue:
                return self._queue.pop(0)
            return self._default_json()

else:  # pragma: no cover - LocalBrain import failed (optional dep missing)
    MockBrain = None


@pytest.fixture
def mock_brain():
    """A fresh, offline, deterministic brain for each test (function-scoped).

    Skips cleanly when LocalBrain cannot be imported (e.g. the optional
    ``cv2`` dependency is missing on a lean CI runner), instead of breaking
    collection for the whole suite.
    """
    if not _HAVE_BRAIN:
        pytest.skip("LocalBrain unavailable (optional dep missing, e.g. cv2)")
    return MockBrain()


@pytest.fixture
def tmp_memory_dir(tmp_path):
    """Isolated temp dir (str) for memory-store tests; never touches ~/.jac."""
    d = tmp_path / "memory"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)
