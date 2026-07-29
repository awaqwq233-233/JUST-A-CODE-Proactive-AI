"""GUI / 运行时相关逻辑的轻量验证（不依赖摄像头、模型、显示器）。

用 monkeypatch 把摄像头、检测器、语音、识别、大脑、记忆全部替换成假对象，
只验证：
  - Config 默认值与 USE_QWEN_TTS 解析
  - SharedContext 带框帧缓冲的线程安全读写
  - JACRuntime._vision_loop 能把带框帧写入 context，且 stop() 后 running=False
  - manual_input 正确路由到 handle_user_text / handle_memory_command
"""
import time

import numpy as np
import pytest

import main
import src.runtime as rt
from src.utils.config import Config
from src.utils.context import SharedContext


# ----------------------------- Config -----------------------------
def test_config_load_defaults():
    c = Config.load()
    assert c.judgment_engine_enabled is True
    assert c.judgment_interval == 4.0
    assert c.judgment_timeout == 15.0
    assert c.use_qwen_tts is True
    assert c.camera_width == 1280 and c.camera_height == 720


def test_config_use_qwen_tts_env(monkeypatch):
    """测试：配置useqwen语音合成环境"""
    monkeypatch.setenv("USE_QWEN_TTS", "false")
    c = Config.load()
    assert c.use_qwen_tts is False
    monkeypatch.setenv("USE_QWEN_TTS", "true")
    assert Config.load().use_qwen_tts is True


# ----------------------------- annotated frame buffer -----------------------------
def test_annotated_frame_buffer_independent():
    ctx = SharedContext()
    frame = np.zeros((100, 120, 3), dtype=np.uint8)
    ctx.set_annotated_frame(frame)
    out = ctx.get_annotated_frame()
    assert out is not None and out.shape == (100, 120, 3)
    out[:] = 255  # 修改取出的副本不应影响缓存
    assert ctx.get_annotated_frame()[0, 0, 0] == 0
    ctx.set_annotated_frame(None)  # 不应写入
    assert ctx.get_annotated_frame() is not None


# ----------------------------- fake components -----------------------------
class FakeCamera:
    def __init__(self, *a, **k):
        """初始化实例"""
        self.stopped = False

    def start(self):
        """启动"""
        return True

    def get_frame(self):
        """获取画面帧"""
        return True, np.zeros((200, 300, 3), dtype=np.uint8)

    def stop(self):
        """停止"""
        self.stopped = True


class FakeDetector:
    def detect(self, frame):
        """检测"""
        return np.ones((200, 300, 3), dtype=np.uint8) * 128, [
            {"label": "person", "confidence": 0.9}
        ]


class FakeSpeaker:
    available = True
    _ensure_model = None

    def speak(self, *a, **k):
        """朗读"""
        pass


class FakeRecognizer:
    def __init__(self, *a, **k):
        """初始化实例"""
        pass

    def transcribe(self, *a, **k):
        """转写"""
        return ""


class FakeRecorder:
    def __init__(self, *a, **k):
        """初始化实例"""
        pass

    def listen_and_record(self, *a, **k):
        """监听与录音"""
        time.sleep(0.02)

    def __getattr__(self, name):
        # p.terminate 等兜底
        """属性读取"""
        return lambda *a, **k: None


class FakeBrain:
    def __init__(self, *a, **k):
        """初始化实例"""
        pass


class FakeMemory:
    def __init__(self, *a, **k):
        """初始化实例"""
        pass

    def close(self):
        """关闭"""
        pass


@pytest.fixture
def fake_runtime(monkeypatch):
    """伪运行时"""
    monkeypatch.setattr(rt, "Camera", FakeCamera)
    monkeypatch.setattr(rt, "VisionDetector", FakeDetector)
    monkeypatch.setattr(rt, "Speaker", FakeSpeaker)
    # qwen_tts 现为运行时懒加载；通过打桩 src.audio.qwen_tts 模块属性强制回退系统 TTS
    import src.audio.qwen_tts as _qwen_tts_mod
    monkeypatch.setattr(_qwen_tts_mod, "QWEN_TTS_AVAILABLE", False)
    monkeypatch.setattr(_qwen_tts_mod, "QwenTTSSpeaker", None)
    monkeypatch.setattr(rt, "SpeechRecognizer", FakeRecognizer)
    monkeypatch.setattr(rt, "AudioRecorder", FakeRecorder)
    monkeypatch.setattr(rt, "LocalBrain", FakeBrain)
    monkeypatch.setattr(rt, "MemoryManager", FakeMemory)
    monkeypatch.setattr(main, "JUDGMENT_ACTIVATED", False)


# ----------------------------- vision loop -----------------------------
def test_vision_loop_writes_frame_and_stops(fake_runtime):
    ctx = SharedContext()
    runtime = rt.JACRuntime(context=ctx)
    runtime.start(Config())

    time.sleep(0.3)
    af = ctx.get_annotated_frame()
    assert af is not None, "annotated frame should be written by vision loop"
    assert af.shape == (200, 300, 3)
    assert runtime._fps >= 0.0

    runtime.stop()
    assert runtime.running is False
    assert main.running is False


# ----------------------------- manual input routing -----------------------------
def test_manual_input_routing(monkeypatch):
    calls = {}

    def fake_handle(text, speaker, brain, source="控制台", bypass_wake=True):
        """伪处理"""
        calls["text"] = text
        calls["source"] = source

    def fake_memory(text):
        """伪记忆"""
        calls["memory"] = text

    monkeypatch.setattr(main, "handle_user_text", fake_handle)
    monkeypatch.setattr(main, "handle_memory_command", fake_memory)

    runtime = rt.JACRuntime(context=SharedContext())
    runtime.speaker = None
    runtime.brain = None

    runtime.manual_input("你好 J.A.C")
    assert calls.get("text") == "你好 J.A.C"
    assert calls.get("source") == "控制台"

    runtime.manual_input("记忆 列表")
    assert calls.get("memory") == "记忆 列表"

    runtime.manual_input("   ")
    assert calls.get("text") == "你好 J.A.C"  # 空输入被忽略，不更新
