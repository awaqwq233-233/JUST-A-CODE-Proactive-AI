import threading
import time
from collections import deque


class SharedContext:
    """
    Shared context manager
    Used for sharing information between different threads (vision, audio, brain).
    """
    def __init__(self):
        self._lock = threading.Lock()

        # Transcription ring buffer (for judgment engine)
        self._transcriptions = deque(maxlen=20)

        # Vision memory
        self.current_objects = []
        self.last_seen_time = 0.0

        # Status flags
        self.is_listening = False
        self.is_thinking = False
        self.is_speaking = False

        # Latest frame buffer
        self._current_frame = None

        # Intervention state (set by judgment engine, consumed by main loop)
        self.intervention_requested = False
        self.intervention_reason = ""

    def update_vision(self, results):
        detected = []
        if results and isinstance(results, list):
            for item in results:
                if isinstance(item, dict) and "label" in item and "confidence" in item:
                    label = item["label"]
                    confidence = item["confidence"]
                    detected.append(f"{label} ({confidence:.2f})")
        with self._lock:
            self.current_objects = detected
            self.last_seen_time = time.time()

    def get_vision_summary(self):
        with self._lock:
            if time.time() - self.last_seen_time > 2.0:
                return "I currently see nothing (no recent vision data)."
            if not self.current_objects:
                return "I don't see any particular objects."
            counts = {}
            for obj in self.current_objects:
                name = obj.split("(")[0].strip()
                counts[name] = counts.get(name, 0) + 1
            summary = ", ".join([f"{v}x {k}" for k, v in counts.items()])
            return f"I can see: {summary}."

    def set_frame(self, frame):
        if frame is not None:
            with self._lock:
                self._current_frame = frame.copy()

    def get_frame(self):
        with self._lock:
            if self._current_frame is None:
                return None
            return self._current_frame.copy()

    # --- Audio transcription buffer (for judgment engine) ---

    def push_transcription(self, text):
        text = (text or "").strip()
        if not text:
            return
        with self._lock:
            self._transcriptions.append((time.time(), text))

    def get_recent_transcriptions(self, window=15.0):
        now = time.time()
        recent = []
        with self._lock:
            for ts, text in self._transcriptions:
                if now - ts <= window:
                    recent.append(text)
        if not recent:
            return ""
        return " | ".join(recent)

    def push_and_get_recent(self, text, window=15.0):
        text = (text or "").strip()
        now = time.time()
        recent = []
        with self._lock:
            if text:
                self._transcriptions.append((now, text))
            for ts, t in self._transcriptions:
                if now - ts <= window:
                    recent.append(t)
        if not recent:
            return ""
        return " | ".join(recent)

    # --- Intervention state ---

    def request_intervention(self, reason):
        with self._lock:
            self.intervention_requested = True
            self.intervention_reason = reason

    def consume_intervention(self):
        with self._lock:
            if self.intervention_requested:
                self.intervention_requested = False
                reason = self.intervention_reason
                self.intervention_reason = ""
                return True, reason
            return False, ""

    def clear_intervention(self):
        with self._lock:
            self.intervention_requested = False
            self.intervention_reason = ""
