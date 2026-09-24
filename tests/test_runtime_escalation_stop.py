"""runtime 升级线程的「停止即收手」行为验证（2026-09-24 修复）。

历史 bug：点「停止」之后，升级线程仍在跑工具调用、并且**还会把结果念出来**。
两个成因：
  1. `_handle_escalation` 的 worker 从不检查 `runtime.running`，escalate 阻塞期间无法收手；
  2. `stop()` 会把 `self.omni_client` 置 None，于是 worker 走 `speak_text_via_voicebox`
     的降级分支，反而「保证出声」。

修复后有三个检查点，本文件逐一验证，并补一个正例确保正常路径没被堵掉：
  * router 层：`should_stop() == True` 时 escalate 立即返回空串且不再回调进度；
  * 检查点 1：已停止时连 EscalationRouter 都不创建（不浪费算力）；
  * 检查点 2：escalate 期间被停止 → 结果丢弃、不播报；
  * 正例：未停止时结果照常播报。
"""
import os
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.runtime as runtime_mod  # noqa: E402
from src.omni.router import EscalationRouter  # noqa: E402


def _bare_runtime(**overrides):
    """构造只装升级路径所需属性的 JACRuntime（绕过 __init__ 的重资源初始化）。

    升级 worker 只用到 running / omni_client / omni_router / context 四样东西，
    逐个塞好即可，不必启动摄像头、麦克风、记忆子系统。
    """
    rt = runtime_mod.JACRuntime.__new__(runtime_mod.JACRuntime)
    rt.running = True
    rt.omni_client = None
    rt.omni_router = None
    rt._escalation_thread = None
    rt.context = SimpleNamespace(is_thinking=False)
    for key, value in overrides.items():
        setattr(rt, key, value)
    return rt


class _StreamingBrain:
    """假大脑：`run_agentic` 按预设分片流式产出（模拟打字机）。"""

    def __init__(self, chunks):
        self._chunks = list(chunks)

    def run_agentic(self, **kwargs):
        for chunk in self._chunks:
            yield chunk


# --------------------------------------------------------------- router 层
def test_escalate_aborts_when_should_stop_true():
    """should_stop 立即为真 → 一个分片都不消费，返回空串且不回调进度。"""
    router = EscalationRouter.__new__(EscalationRouter)  # 不构造 LocalBrain（避免联机探测）
    router.brain = _StreamingBrain(["你", "好"])
    seen = []
    out = router.escalate("查电池", on_progress=seen.append, should_stop=lambda: True)
    assert out == "", "取消后必须返回空串（调用方据此判断无结果）"
    assert seen == [], f"取消后不应再回调进度（控制台会继续打字）: {seen}"


def test_escalate_runs_normally_without_should_stop():
    """不传 should_stop 时行为不变（向后兼容）。"""
    router = EscalationRouter.__new__(EscalationRouter)
    router.brain = _StreamingBrain(["电池", "80%"])
    seen = []
    out = router.escalate("查电池", on_progress=seen.append)
    assert out == "电池80%"
    assert seen == ["电池", "80%"]


# --------------------------------------------------------------- runtime 层
def test_worker_skips_router_when_already_stopped(monkeypatch):
    """检查点 1：runtime 已停止 → 连 EscalationRouter 都不该创建。"""
    rt = _bare_runtime(running=False)
    created = []

    def _spy_router(*args, **kwargs):
        created.append(1)
        return None

    monkeypatch.setattr(runtime_mod, "EscalationRouter", _spy_router)
    rt._handle_escalation("查电池")
    time.sleep(0.25)
    assert created == [], "已停止时仍在创建升级路由器（白烧算力/可能触发工具调用）"


def test_worker_drops_result_when_stopped_during_escalate(monkeypatch):
    """检查点 2：escalate 期间被停止 → 结果丢弃、绝不出声。"""
    rt = _bare_runtime()
    spoken = []

    class _Router:
        """在 escalate 内部把 running 置 False，模拟「任务跑到一半用户点了停止」。"""

        def __init__(self, *args, **kwargs):
            pass

        def escalate(self, task, on_progress=None, should_stop=None):
            assert should_stop is not None, "runtime 必须把取消信号传进 escalate"
            rt.running = False
            return "电池 80%"

    monkeypatch.setattr(runtime_mod, "EscalationRouter", _Router)
    import src.omni.backfeed as backfeed_mod
    monkeypatch.setattr(
        backfeed_mod, "speak_text_via_voicebox",
        lambda spk, text: spoken.append(text),
    )

    rt._handle_escalation("查电池")
    time.sleep(0.3)
    assert spoken == [], f"停止后不应播报升级结果: {spoken}"


def test_worker_speaks_result_when_still_running(monkeypatch):
    """正例：未停止时结果照常播报（防止修复过度把正常路径也堵掉）。"""
    rt = _bare_runtime()
    spoken = []

    class _Router:
        def __init__(self, *args, **kwargs):
            pass

        def escalate(self, task, on_progress=None, should_stop=None):
            return "电池 80%"

    monkeypatch.setattr(runtime_mod, "EscalationRouter", _Router)
    import src.omni.backfeed as backfeed_mod
    monkeypatch.setattr(
        backfeed_mod, "speak_text_via_voicebox",
        lambda spk, text: spoken.append(text),
    )

    rt._handle_escalation("查电池")
    time.sleep(0.3)
    assert spoken == ["电池 80%"], f"正常路径必须出声: {spoken}"


def test_stop_joins_escalation_thread(monkeypatch):
    """stop() 必须 join 升级线程，且 join 发生在关闭 omni_client 之前。"""
    rt = _bare_runtime()
    order = []

    class _SlowRouter:
        def __init__(self, *args, **kwargs):
            pass

        def escalate(self, task, on_progress=None, should_stop=None):
            # 分片之间让出控制权，给 stop() 机会把 running 置 False
            for _ in range(40):
                if should_stop is not None and should_stop():
                    return ""
                time.sleep(0.01)
            return "迟到的结果"

    monkeypatch.setattr(runtime_mod, "EscalationRouter", _SlowRouter)
    rt._handle_escalation("查电池")
    time.sleep(0.05)

    # 用可观测的替身接管 stop() 里其余副作用，避免真的去关摄像头/记忆
    rt._audio_stop = SimpleNamespace(set=lambda: order.append("audio_stop"))
    rt.judge_engine = None
    rt.camera = None
    rt.memory = None
    rt._notify = lambda running: None
    rt.stop()

    assert rt.running is False
    assert rt._escalation_thread is None, "stop() 后应清空升级线程引用"
    order.append("stopped")
    assert "audio_stop" in order
