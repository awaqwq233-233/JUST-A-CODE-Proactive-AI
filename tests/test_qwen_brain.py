"""M2 执行边界回归：精确模型、思考泄漏、取消、越权与文件交付。"""

import asyncio
import json
from pathlib import Path
import time
from types import SimpleNamespace

import httpx
import pytest

from src.brain.llm import LocalBrain, ThinkResult, parse_tool_calls
from src.brain.lm_studio import LMStudioClient, BrainError, BrainCancelled, MODEL
from src.brain.task_runner import BrainTaskRunner, atomic_write
from src.tools.registry import get_tool_schemas
from src.tools.validation import validate_arguments


def response(content="真实回答", calls=None, finish=None, reasoning=""):
    """构造受测 HTTP 响应，不连接任何真实模型。"""
    return {"choices": [{"finish_reason": finish or ("tool_calls" if calls else "stop"),
                         "message": {"content": content, "tool_calls": calls or [],
                                     "reasoning_content": reasoning}}]}


def call(info_type="time", name="get_system_info", identifier="one"):
    """构造结构化工具请求，便于模拟多项执行和畸形参数。"""
    return {"type": "function", "id": identifier,
            "function": {"name": name, "arguments": json.dumps({"info_type": info_type})}}


def test_request_disables_reasoning_and_preserves_token_limit():
    """小 token 上限不能被偷偷扩大；真实请求不能带无效旧思考参数。"""
    def handle(request):
        """检查网络边界并返回可交付内容。"""
        body = json.loads(request.content)
        assert body["reasoning_effort"] == "none"
        assert "chat_template_kwargs" not in body
        assert body["max_tokens"] == 100 and body["model"] == MODEL
        return httpx.Response(200, json=response())

    client = LMStudioClient(transport=httpx.MockTransport(handle))
    assert client.complete([{"role": "user", "content": "查询"}], max_tokens=100).content == "真实回答"


@pytest.mark.parametrize("data", [
    response(reasoning="不能交付的思考"), response(content="", finish="stop"),
    response(content="截断", finish="length"), response(calls=[call()], finish="length"),
    {"choices": []}, response(content=23),
])
def test_invalid_completion_never_becomes_answer(data):
    """思考、空、截断或异常结构均不能成为成功结果或待执行请求。"""
    with pytest.raises(BrainError):
        LMStudioClient.parse_response(data)


@pytest.mark.parametrize("loaded", [False, True])
def test_requires_exact_loaded_model(loaded):
    """下载清单和相似模型不能替代已加载的目标实例。"""
    def handle(request):
        """提供包含干扰模型的原生清单。"""
        entry = {"key": MODEL, "loaded_instances": [{"id": MODEL}] if loaded else [],
                 "capabilities": {"trained_for_tool_use": True, "reasoning": {"allowed_options": ["off", "on"]}}}
        return httpx.Response(200, json={"models": [
            {"loaded_instances": [{"id": "qwen/similar-model"}], "capabilities": {}}, entry]})

    client = LMStudioClient(transport=httpx.MockTransport(handle))
    if loaded:
        assert client.require_loaded_model() == MODEL
    else:
        with pytest.raises(BrainError, match="未加载"):
            client.require_loaded_model()


@pytest.mark.parametrize("url", ["https://example.com", "http://127.0.0.1:12345/v1", "http://u:p@localhost:12345", "http://localhost:12345?key=x"])
def test_rejects_nonlocal_or_ambiguous_urls(url):
    """仅本机根地址可进入大脑调用层，不受代理变量影响。"""
    with pytest.raises(ValueError):
        LMStudioClient(url)


def test_cancels_request_while_waiting():
    """在途 HTTP 未返回时也可取消，不能等到回答后才检查。"""
    async def run():
        """异步构造慢模型和取消事件，确认底层请求也已取消。"""
        stop = asyncio.Event()
        cancelled = asyncio.Event()

        async def handle(request):
            """模拟长推理，任务取消后记录连接处理退出。"""
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return httpx.Response(200, json=response())

        client = LMStudioClient(transport=httpx.MockTransport(handle))
        pending = asyncio.create_task(client.acomplete([], should_stop=stop.is_set))
        await asyncio.sleep(0.05)
        start = time.monotonic()
        stop.set()
        with pytest.raises(BrainCancelled):
            await pending
        assert time.monotonic() - start < 0.5 and cancelled.is_set()

    asyncio.run(run())


@pytest.mark.parametrize("arguments", ["{broken", "[]", "null", "", 1])
def test_malformed_arguments_are_not_defaulted(arguments):
    """无效 JSON 不能默默变成空参数并执行默认查询。"""
    request = call()
    request["function"]["arguments"] = arguments
    with pytest.raises(BrainError):
        parse_tool_calls({"tool_calls": [request]})


@pytest.mark.parametrize("arguments", [{"info_type": "delete"}, {"info_type": 1}, {"unknown": "x"}])
def test_schema_rejects_invalid_fields(arguments):
    """参数枚举、类型和未知字段在执行前被拒绝。"""
    schema = next(t["function"]["parameters"] for t in get_tool_schemas() if t["function"]["name"] == "get_system_info")
    with pytest.raises(ValueError):
        validate_arguments(schema, arguments)


def test_stop_between_tools_prevents_second_execution(monkeypatch):
    """第一项执行后停止时第二项绝不能执行，且不返回伪成功。"""
    brain = LocalBrain(backend="mock")
    parsed, raw = parse_tool_calls({"tool_calls": [call(), call("battery", identifier="two")]})
    monkeypatch.setattr(brain, "think_with_tools", lambda *a, **k: ThinkResult(tool_calls=parsed, raw_tool_calls=raw))
    invoked = []

    def execute(name, arguments):
        """第一项工具触发取消判据。"""
        invoked.append(arguments)
        return "工具结果"

    with pytest.raises(BrainCancelled):
        list(brain.run_agentic("查询", get_tool_schemas(), execute, should_stop=lambda: bool(invoked)))
    assert len(invoked) == 1


def test_unlisted_tool_rejected_before_any_execution(monkeypatch):
    """同批第二项越权时，第一项也不得先执行。"""
    brain = LocalBrain(backend="mock")
    parsed, raw = parse_tool_calls({"tool_calls": [call(), call(name="run_command", identifier="two")]})
    monkeypatch.setattr(brain, "think_with_tools", lambda *a, **k: ThinkResult(tool_calls=parsed, raw_tool_calls=raw))
    invoked = []
    tools = [t for t in get_tool_schemas() if t["function"]["name"] == "get_system_info"]
    with pytest.raises(BrainError, match="未授权"):
        list(brain.run_agentic("查询", tools, lambda *a: invoked.append(a)))
    assert invoked == []


def test_final_answer_is_not_generated_twice(monkeypatch):
    """已有最终回答时不能丢弃并再次调用模型。"""
    brain = LocalBrain(backend="mock")
    requests = []

    def answer(*args, **kwargs):
        """返回终态并记录次数。"""
        requests.append(args)
        return ThinkResult(content="实际最终回答")

    monkeypatch.setattr(brain, "think_with_tools", answer)
    assert list(brain.run_agentic("任务", [], lambda *a: None)) == ["实际最终回答"]
    assert len(requests) == 1


def test_iteration_limit_is_failure(monkeypatch):
    """达到次数上限不能另行请求一个看似完成的总结。"""
    brain = LocalBrain(backend="mock")
    parsed, raw = parse_tool_calls({"tool_calls": [call()]})
    monkeypatch.setattr(brain, "think_with_tools", lambda *a, **k: ThinkResult(tool_calls=parsed, raw_tool_calls=raw))
    with pytest.raises(BrainError, match="上限"):
        list(brain.run_agentic("任务", get_tool_schemas(), lambda *a: "结果", max_iterations=1))


def test_no_evidence_no_report(tmp_path):
    """模型未调用真实工具时不允许发布报告。"""
    brain = SimpleNamespace(run_agentic=lambda *a, **k: iter(["凭空报告"]))
    with pytest.raises(BrainError, match="真实工具"):
        BrainTaskRunner(brain, tmp_path).run("状态")
    assert list(tmp_path.iterdir()) == []


def test_report_contains_actual_tool_evidence(tmp_path, monkeypatch):
    """交付包含真实工具返回，模型不能指定文件路径。"""
    def agent(task, tools, executor, **kwargs):
        """模拟一个被允许的真实查询过程。"""
        assert [t["function"]["name"] for t in tools] == ["get_system_info"]
        executor("get_system_info", {"info_type": "time"})
        yield "中文结果"

    monkeypatch.setattr("src.brain.task_runner.execute_tool", lambda *a: "实际时间：2026-10-07 10:20:30")
    returned = []
    result = BrainTaskRunner(SimpleNamespace(run_agentic=agent), tmp_path).run("../模型路径", on_tool_result=returned.append)
    assert result.path.parent == tmp_path and result.path.name.startswith("system-status-")
    assert "实际时间：2026-10-07 10:20:30" in result.path.read_text(encoding="utf-8")
    assert returned == result.trace and returned[0]["queried_at"]
    assert "get_system_info" in result.path.read_text(encoding="utf-8")


@pytest.mark.parametrize("status, expected", [("not charging", "未充电"), ("charging", "充电中"), ("discharging", "放电中")])
def test_battery_uses_actual_percentage_and_distinguishes_not_charging(monkeypatch, status, expected):
    """macOS 保留真实百分比，not charging 不能因为含 charging 被误报。"""
    from src.tools import system_info
    monkeypatch.setattr(system_info.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(system_info.subprocess, "run", lambda *a, **kw:
        SimpleNamespace(stdout=f"-InternalBattery-0 (id=123) 80%; {status}; present: true"))
    result = system_info.get_system_info({"info_type": "battery"})
    assert "80%" in result and expected in result


def test_cancel_before_atomic_publish_removes_temporary_file(tmp_path):
    """发布前取消时不覆盖既有文件，也不留下半成品。"""
    path, checks = tmp_path / "report.md", []
    path.write_text("原文件", encoding="utf-8")

    def stop():
        """第二个边界检查触发取消。"""
        checks.append(True)
        return len(checks) == 2

    with pytest.raises(BrainCancelled):
        atomic_write(path, "新文件", stop)
    assert path.read_text(encoding="utf-8") == "原文件"
    assert list(tmp_path.iterdir()) == [path]


def test_memory_uses_vm_stat_page_size(monkeypatch):
    """Apple Silicon 的 16KiB 页不得按 4KiB 算成四分之一。"""
    from src.tools import system_info

    def command(args, **kwargs):
        """提供可计算的一 GiB 活跃内存与十六 GiB 总内存。"""
        text = str(16 * 1024 ** 3) if args[0] == "sysctl" else (
            "Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages active: 65536.\nPages wired down: 0.\n")
        return SimpleNamespace(stdout=text)

    monkeypatch.setattr(system_info.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(system_info.subprocess, "run", command)
    assert "1.0 GiB / 共 16.0 GiB" in system_info._get_memory()
