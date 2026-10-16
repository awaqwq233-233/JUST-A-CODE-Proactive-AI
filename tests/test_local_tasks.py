"""本地任务授权、执行证据、取消和生产语音接线回归；不打开真实应用。"""

import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from src.brain.lm_studio import BrainError, BrainCancelled
from src.brain.task_runner import BrainTaskRunner
from src.tools.local_intents import local_instruction, validate_url, is_local_request
from src.tools.local_actions import LocalActionExecutor, command_result, local_answer
from src.tools.local_file_worker import scan_files
from src.omni.task_pipeline import route_instruction, TaskPipeline, Utterance
from src.omni.task_speech import speech_summary
from src.omni.gateway_client import GatewayCallbacks


@pytest.mark.parametrize("text,name", [
    ("打开Safari", "open_app"), ("请打开计算器。", "open_app"), ("請打開備忘錄", "open_app"),
    ("启动日历", "open_app"), ("打开网址 https://www.python.org/", "open_url"),
    ("打开Python官网", "open_url"), ("在下载目录查找文件名包含报告的文件", "search_files"),
    ("在桌面找报告文件", "search_files"), ("在文档文件夹里搜索会议纪要文件", "search_files"),
])
def test_explicit_local_routes(text, name):
    """支持完整单句与中文别名，语音路径和独立路径使用同一授权。"""
    fields = route_instruction(text)
    assert fields[:2] == ("local", name)
    assert fields == local_instruction(text)


@pytest.mark.parametrize("text", [
    "不要打开Safari", "他说打开Safari", "如果打开Safari", "我想知道打开Safari的方法",
    "打开Safari然后打开日历", "打开Safari并删除文件", "打开终端", '打开"Safari"',
    "<<CALL_QWEN>>打开Safari", "打开Safari\n", "打开Safari;rm -rf /", "打开file:///etc/passwd",
    "在/找报告文件", "在~/.ssh查找key文件", "在~/Downloads/../Documents查找报告文件",
    "在下载目录找报告文件然后删除", "打开javascript:alert(1)",
    "在下载目录查找报告文件并删除文件", "在下载目录查找报告文件和删除文件",
])
def test_negation_quotes_compounds_and_unknown_actions_rejected(text):
    """不从否定、转述、复合句和控制令牌抽取子指令。"""
    assert route_instruction(text) is None


@pytest.mark.parametrize("url", [
    "https://u:p@example.com", "https://example.com/\n", "https://example.com/%0afoo",
    "https://127.0.0.1/", "http://localhost/", "https://example.com:22/", "file:///tmp/x",
    "https://example.com\\@evil.com", "https://example.com/#fragment", "https://2130706433/",
])
def test_invalid_urls_never_reach_browser(url):
    """网址在启动器前拒绝异常协议、凭据、控制字符和明显本机地址。"""
    with pytest.raises(ValueError):
        validate_url(url)


def test_scope_required_and_web_education_still_routes():
    """缺少目录只追问；明确搜索使用方法仍是联网任务。"""
    assert route_instruction("帮我查找报告文件") == ("local_input",)
    assert route_instruction("搜索本地文件") == ("local_input",)
    assert route_instruction("上网搜索如何打开Safari")[0] == "web"
    assert is_local_request("打开不支持的应用")


def test_payload_characters_are_preserved(monkeypatch, tmp_path):
    """繁简兼容只处理指令词，不误改真实路径、文件名或网址末尾标点。"""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    fields = local_instruction(f"請在{tmp_path}/備份目錄查找測試報告文件")
    assert json.loads(fields[2]) == dict(path=str(tmp_path / "備份"), query="測試報告", max_results=20)
    assert json.loads(local_instruction("打开网址https://example.com/?q=hello!")[2])["url"].endswith("hello!")


class Brain:
    """构造唯一 schema 调用，可替换模型输出以检查权限边界。"""

    def __init__(self, alter=None):
        """保存模型输出变换与调用计数。"""
        self.alter, self.calls = alter, 0

    def think_with_tools(self, messages, tools, **kwargs):
        """从枚举读取精确参数，不执行系统命令。"""
        self.calls += 1
        schema = tools[0]["function"]
        calls = [dict(name=schema["name"], arguments={key: spec["enum"][0]
                 for key, spec in schema["parameters"]["properties"].items()})]
        if self.alter:
            calls = self.alter(calls)
        return SimpleNamespace(tool_calls=calls, content="模型自称成功不能作为证据")


class Executor:
    """记录动作，只返回指定结构化证据。"""

    def __init__(self, status="submitted"):
        """选择待验证的实际状态。"""
        self.calls, self.status = [], status

    def execute(self, name, arguments, should_stop, dispatch_lock, on_dispatch):
        """模拟动作已提交和独立结果。"""
        self.calls.append((name, arguments))
        on_dispatch()
        return dict(status=self.status, **arguments)


@pytest.mark.parametrize("alter", [
    lambda calls: [], lambda calls: calls * 2,
    lambda calls: [dict(name="run_command", arguments={"command": "date"})],
    lambda calls: [dict(name="open_app", arguments={"app_name": "Notes"})],
    lambda calls: [dict(name="open_app", arguments={"app_name": "Safari", "extra": "x"})],
])
def test_qwen_cannot_change_or_repeat_action(tmp_path, alter):
    """整批先验收，错误参数和多调用均在任何副作用前拒绝。"""
    executor = Executor()
    runner = BrainTaskRunner(Brain(alter), tmp_path, local_executor=executor)
    with pytest.raises(BrainError):
        runner.run("打开Safari", local_action=local_instruction("打开Safari"))
    assert not executor.calls and not list(tmp_path.iterdir())


@pytest.mark.parametrize("status", ["submitted", "completed"])
def test_results_and_speech_use_evidence_not_model_claim(tmp_path, status):
    """启动提交与进程存在有不同交付和播报，不让模型重复生成结论。"""
    brain, executor, events = Brain(), Executor(status), []
    fields = local_instruction("打开Safari")
    result = BrainTaskRunner(brain, tmp_path, local_executor=executor).run(
        "打开Safari", local_action=fields, on_action_dispatched=lambda event: events.append(event))
    assert brain.calls == 1 and len(executor.calls) == 1 and len(events) == 1
    assert result.status == status and result.path.is_file()
    assert "模型自称" not in result.answer
    assert speech_summary(result, fields) == result.answer
    assert ("已检测到" in result.answer) == (status == "completed")


def test_runner_rechecks_original_sentence_before_model(tmp_path):
    """伪造路由参数不能绕过完整用户句授权。"""
    brain = Brain()
    with pytest.raises(BrainError):
        BrainTaskRunner(brain, tmp_path).run("不要打开Safari", local_action=local_instruction("打开Safari"))
    assert brain.calls == 0


def test_stop_while_qwen_waits_prevents_launch(tmp_path):
    """Qwen 迟到的合法调用也不能在停止后触发系统动作。"""
    stopped, executor = threading.Event(), Executor()

    def cancel(calls):
        """在模型返回时使原代次失效。"""
        stopped.set()
        return calls

    with pytest.raises(BrainCancelled):
        BrainTaskRunner(Brain(cancel), tmp_path, local_executor=executor).run(
            "打开Safari", stopped.is_set, local_action=local_instruction("打开Safari"))
    assert not executor.calls


def test_cancel_checked_inside_dispatch_lock(monkeypatch):
    """已在锁外通过检查的调用，拿到派发锁后仍须复核取消。"""
    invoked = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: invoked.append(a))
    with pytest.raises(BrainCancelled):
        command_result(["unused"], lambda: True, threading.RLock())
    assert not invoked


def test_running_helper_is_cancelled_and_reaped():
    """取消真实睡眠辅助进程，无真实应用或设备副作用。"""
    started = time.monotonic()
    with pytest.raises(BrainCancelled):
        command_result([sys.executable, "-c", "import time; time.sleep(10)"],
                       lambda: time.monotonic() - started > .2)
    assert time.monotonic() - started < 2


def test_scan_is_scoped_skips_links_and_reports_partial(tmp_path, monkeypatch):
    """真实临时目录验证搜索范围、隐藏项、符号链接、结果上限和无匹配。"""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    root, outside = tmp_path / "Downloads", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "报告-secret.txt").write_text("private", encoding="utf-8")
    (root / "link").symlink_to(outside, target_is_directory=True)
    (root / ".报告.txt").write_text("hidden", encoding="utf-8")
    (root / "报告.txt").write_text("visible", encoding="utf-8")
    args = dict(path=str(root), query="报告", max_results=20)
    data = scan_files(args)
    assert data["matches"] == [str(root / "报告.txt")] and data["status"] == "completed"
    assert scan_files(dict(args, query="无匹配"))["matches"] == []
    assert scan_files(dict(args, max_results=1))["status"] == "partial"
    with pytest.raises(ValueError):
        scan_files(dict(args, path=str(root / "link")))


def test_mac_launch_evidence_and_no_shell(monkeypatch):
    """精确 bundle id 启动，进程核验仅允许声明正在运行。"""
    calls = []

    def command(argv, *args, **kwargs):
        """提供启动器确认和真实格式进程列表。"""
        calls.append(argv)
        if argv[0] == "/bin/ps":
            return 0, "/System/Applications/Calculator.app/Contents/MacOS/Calculator\n", ""
        return 0, "", ""

    monkeypatch.setattr("src.tools.local_actions.platform.system", lambda: "Darwin")
    monkeypatch.setattr("src.tools.local_actions.command_result", command)
    result = LocalActionExecutor().execute("open_app", {"app_name": "Calculator"})
    assert result["status"] == "completed"
    assert calls[0] == ["/usr/bin/open", "-b", "com.apple.calculator"]
    result = LocalActionExecutor().execute("open_url", {"url": "https://www.python.org/"})
    assert result["status"] == "submitted"


def test_failed_launch_not_reported_as_completed(monkeypatch):
    """系统拒绝启动明确失败，不因模型文本变成成功。"""
    monkeypatch.setattr("src.tools.local_actions.platform.system", lambda: "Darwin")
    monkeypatch.setattr("src.tools.local_actions.command_result", lambda *a, **k: (1, "", "failure"))
    assert LocalActionExecutor().execute("open_app", {"app_name": "Safari"})["status"] == "failed"


def test_task_pipeline_delivers_local_scope_and_deduplicates(tmp_path):
    """真实任务线程向 runner 传精确权限，重复句不启动第二次动作。"""
    events, finished, executor = [], threading.Event(), Executor()
    callbacks = GatewayCallbacks()

    def event(state, detail):
        """记录状态且在终态唤醒测试。"""
        events.append((state, detail))
        if state == "submitted":
            finished.set()

    callbacks.on_task_event = event
    pipeline = TaskPipeline(callbacks, decoder_factory=lambda path: SimpleNamespace(),
                            runner_factory=lambda: BrainTaskRunner(Brain(), tmp_path, local_executor=executor))
    pipeline.begin_session()
    fields = local_instruction("打开Safari")
    utterance = Utterance("one", pipeline.generation, time.monotonic(), np.zeros(16000), False)
    assert pipeline.submit(utterance, "打开Safari", fields)
    assert not pipeline.submit(utterance, "打开Safari", fields)
    thread = threading.Thread(target=pipeline._task_loop)
    thread.start()
    try:
        assert finished.wait(3)
        assert len(executor.calls) == 1
        assert [s for s, _ in events] == ["running", "action_dispatched", "tool_completed", "submitted"]
        assert pipeline.stats()["tasks_submitted"] == pipeline.stats()["tasks_launch_submitted"] == 1
    finally:
        pipeline.stopped.set()
        pipeline.invalidate()
        thread.join(2)


@pytest.mark.parametrize("text,overlap,quality", [
    ("打开Safari然后删除文件", False, -.1), ("打开Safari", True, -.1),
    ("打开Safari", False, -2),
])
def test_rejected_local_speech_never_allows_normal_model_claim(text, overlap, quality):
    """真实转写线程拒绝复合、重叠和低置信度动作，并保持普通回复禁猜。"""
    finished = threading.Event()
    callbacks = GatewayCallbacks()
    callbacks.on_task_event = lambda state, detail: finished.set()
    decoder = SimpleNamespace(transcribe=lambda samples: [dict(text=text, avg_logprob=quality,
                              no_speech_prob=.01, compression_ratio=1)])
    pipeline = TaskPipeline(callbacks, decoder_factory=lambda path: decoder)
    pipeline.begin_session()
    sequence = pipeline._response_started()
    pipeline.utterances.put(Utterance("speech", pipeline.generation, time.monotonic(),
                                    np.zeros(16000), overlap, sequence))
    thread = threading.Thread(target=pipeline._decode_loop)
    thread.start()
    try:
        assert finished.wait(2)
        with pipeline.lock:
            assert pipeline.response_policy == "block"
            assert pipeline.tasks.empty()
    finally:
        pipeline.stopped.set()
        thread.join(2)


def test_cancel_after_dispatch_does_not_publish_success(tmp_path):
    """已提交的启动请求不能撤回，但取消后不得发布旧成功报告。"""
    stopped, executor = threading.Event(), Executor()
    with pytest.raises(BrainCancelled):
        BrainTaskRunner(Brain(), tmp_path, local_executor=executor).run(
            "打开Safari", stopped.is_set, local_action=local_instruction("打开Safari"),
            on_action_dispatched=lambda entry: stopped.set())
    assert len(executor.calls) == 1 and not list(tmp_path.iterdir())


def test_restart_invalidates_queued_local_action():
    """停止/重连已发生时，队列中的旧句不能创建大脑或启动动作。"""
    created = []
    pipeline = TaskPipeline(GatewayCallbacks(), decoder_factory=lambda path: SimpleNamespace(),
                            runner_factory=lambda: created.append(True))
    pipeline.begin_session()
    utterance = Utterance("old", pipeline.generation, time.monotonic(), np.zeros(16000), False)
    assert pipeline.submit(utterance, "打开Safari", local_instruction("打开Safari"))
    pipeline.invalidate()
    pipeline.begin_session()
    thread = threading.Thread(target=pipeline._task_loop)
    thread.start()
    try:
        deadline = time.monotonic() + 2
        while pipeline.busy and time.monotonic() < deadline:
            time.sleep(.01)
        assert not pipeline.busy and not created
    finally:
        pipeline.stopped.set()
        thread.join(2)


def test_missing_directory_only_clarifies_without_model():
    """缺少目录只返回固定追问，不创建大脑、不查文件、不发布报告。"""
    events, finished = [], threading.Event()
    callbacks = GatewayCallbacks()

    def event(state, detail):
        """收集需要补充信息的唯一事件。"""
        events.append((state, detail))
        finished.set()

    def forbidden():
        """任何模型初始化都说明追问错误进入了执行路径。"""
        raise AssertionError("不应创建大脑")

    callbacks.on_task_event = event
    pipeline = TaskPipeline(callbacks, decoder_factory=lambda path: SimpleNamespace(), runner_factory=forbidden)
    pipeline.begin_session()
    utterance = Utterance("clarify", pipeline.generation, time.monotonic(), np.zeros(16000), False)
    assert pipeline.submit(utterance, "查找报告文件", ("local_input",))
    thread = threading.Thread(target=pipeline._task_loop)
    thread.start()
    try:
        assert finished.wait(2)
        assert [state for state, _ in events] == ["needs_input"]
        assert "path" not in events[0][1]
        assert not pipeline.stats().get("tool_calls", 0)
    finally:
        pipeline.stopped.set()
        thread.join(2)


@pytest.mark.parametrize("state", ["submitted", "partial", "failed"])
def test_console_exposes_actual_local_report(state, capsys):
    """终端同样能找到新终态的真实文件，不只输出一个空状态名。"""
    from src.omni.gateway_cli import ConsoleCallbacks
    ConsoleCallbacks().on_task_event(state, {"path": "/example/本地任务.md"})
    assert "/example/本地任务.md" in capsys.readouterr().out


def test_empty_partial_scan_does_not_claim_saved_matching_paths():
    """零匹配仅保存搜索记录，部分扫描不能冒充全目录无文件。"""
    answer = local_answer("search_files", dict(status="partial", matches=[]))
    assert "本次扫描未找到" in answer and "部分扫描" in answer
    assert "真实路径" not in answer
