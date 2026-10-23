"""原生探针的验收边界：错误归属、丢音、重复完成不能成为通过结果。"""

import numpy as np
import pytest

from src.omni import realtime_protocol as protocol
from verify_native_reliability import NativeObserver


def prepared():
    """构造已接受的固定任务，不访问设备或后端。"""
    observer = NativeObserver()
    observer.add("probe-1", "测试。")
    observer.accept(dict(type="task.speech", id="probe-1", state="accepted"))
    observer.accept(dict(type="response.output.delta", task_speech_id="probe-1", kind="text", text="测试。"))
    return observer


def audio_event(samples):
    """生成带正确任务标识的协议音频增量。"""
    return dict(type="response.output.delta", task_speech_id="probe-1", kind="audio",
                audio=protocol.encode_pcm(samples))


@pytest.mark.parametrize("samples,declared", [(np.zeros(10), 10), (np.ones(10) * .1, 11)])
def test_silence_and_missing_audio_cannot_pass(samples, declared):
    """静音或样本丢失即使后端声称完成也必须失败。"""
    observer = prepared()
    observer.accept(audio_event(samples))
    with pytest.raises(ValueError, match="不匹配"):
        observer.accept(dict(type="task.speech", id="probe-1", state="completed", audio_samples=declared))


def test_wrong_identity_and_wrong_text_are_rejected():
    """其他任务输出或被改写的文字不能证明本测试句已播报。"""
    observer = prepared()
    with pytest.raises(ValueError, match="不属于"):
        observer.accept(dict(type="task.speech", id="other", state="completed", audio_samples=0))
    with pytest.raises(ValueError, match="偏离"):
        observer.accept(dict(type="response.output.delta", task_speech_id="probe-1", kind="text", text="额外文字"))


def test_completed_is_unique_and_report_contains_no_pcm():
    """有效技术结果仅完成一次，报告不泄漏缓存的波形。"""
    observer = prepared()
    observer.accept(audio_event(np.ones(10) * .1))
    completed = dict(type="task.speech", id="probe-1", state="completed", audio_samples=10)
    observer.accept(completed)
    assert observer.report()["jobs"][0]["done"] is True
    assert "pcm" not in observer.report()["jobs"][0]
    with pytest.raises(ValueError, match="重复终态"):
        observer.accept(completed)
    with pytest.raises(ValueError, match="生命周期"):
        observer.accept(audio_event(np.ones(10) * .1))


def test_failed_backend_is_not_a_finished_test():
    """原生失败立即中止，不能仅凭收到音频而判为通过。"""
    observer = prepared()
    with pytest.raises(ValueError, match="失败"):
        observer.accept(dict(type="task.speech", id="probe-1", state="error", code="busy"))
    assert not observer.report()["jobs"][0]["done"]
