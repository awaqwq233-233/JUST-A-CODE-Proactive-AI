"""真实 Harness + 本机假模型，验证生产任务唯一所有权及原权限边界。"""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from src.brain.agent import production
from src.brain.agent.production import AgentUnavailable, HarnessBrain, JACBrainTaskRunner, VoiceToolBoundary
from src.brain.agent.profile import ROOT, MODEL
from src.brain.lm_studio import BrainError, BrainCancelled
from src.omni.gateway_client import GatewayCallbacks
from src.omni.task_pipeline import TaskPipeline, Utterance
from src.omni.task_speech import speech_summary
from src.tools.local_intents import local_instruction
from src.tools.website_resolver import WebsiteCache


@contextmanager
def model_server(mode="normal", final="bo s s，电池电量为 80%。"):
    """从精确 schema 返回调用，支持坏批次、截断、迟到和网页选择注入。"""
    requests = []
    class Handler(BaseHTTPRequestHandler):
        """仅服务隔离测试用 SSE，不请求外网。"""
        def log_message(self, *args):
            """不输出测试请求正文。"""
        def do_POST(self):
            """模拟工具回合与最终回复，保留实际请求供断言。"""
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            if mode == "slow":
                time.sleep(1.5)
            spec = body["tools"][0]["function"]
            arguments = {key: value["enum"][0] for key, value in spec["parameters"]["properties"].items()}
            if spec["name"] == "select_website":
                arguments = {"decision": "ask", "candidate_id": 0}
            call = {"index": 0, "id": "call-" + str(len(requests)), "type": "function",
                    "function": {"name": spec["name"], "arguments": json.dumps(arguments)}}
            calls = [call]
            if mode == "extra":
                calls.append({**call, "index": 1, "id": "unauthorized", "function": {"name": "bash", "arguments": "{}"}})
            if mode == "duplicate":
                calls.append({**call, "index": 1, "id": "duplicate"})
            if mode == "wrong":
                call["function"]["arguments"] = json.dumps({**arguments, "extra": "not-granted"})
            first = len(requests) == 1 or mode == "repeat"
            delta, finish = ({"tool_calls": calls}, "tool_calls") if first else ({"content": final}, "stop")
            if mode == "truncated":
                finish = "length"
            if mode == "reasoning":
                delta = {"reasoning_content": "不允许的思考"}
            chunks = [{"id": "mock", "object": "chat.completion.chunk", "model": MODEL,
                       "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                      {"id": "mock", "object": "chat.completion.chunk", "model": MODEL,
                       "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                       "usage": {"prompt_tokens": 500, "completion_tokens": 30, "total_tokens": 530}}]
            raw = ("".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n").encode()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class Executor:
    """只记录测试动作，防止回归实际打开应用或浏览器。"""
    def __init__(self):
        """初始化唯一动作记录。"""
        self.calls = []
    def execute(self, name, args, should_stop, lock, on_dispatch):
        """核对取消后返回 submitted，刻意不证明页面已加载。"""
        if should_stop():
            raise BrainCancelled("取消")
        self.calls.append((name, args))
        on_dispatch()
        return {"status": "submitted", **args}


@pytest.fixture
def installed():
    """仅实际 SDK 集成用例需要独立安装，纯权限测试不依赖它。"""
    if not (ROOT / ".cache/agent/venv/bin/python").is_file():
        pytest.skip("独立 Harness 环境未安装")


def test_live_sdk_system_evidence_and_identity(installed, tmp_path, monkeypatch):
    """实际 SDK 两回合读取真实工具反馈，报告与原生摘要绑定同一任务。"""
    invoked, evidence = [], []
    def execute(name, arguments):
        """以确定系统结果核对模型最终回答不会替代执行依据。"""
        invoked.append((name, arguments))
        return "电池电量：80%"
    monkeypatch.setattr("src.brain.task_runner.execute_tool", execute)
    with model_server() as (url, requests):
        job = JACBrainTaskRunner(brain=HarnessBrain(url, lambda: 8192), output_dir=tmp_path)
        result = job.run("查一下电池电量", info_types=("battery",), task_context={"utterance_id": "u1", "generation": 8},
                         on_tool_result=evidence.append)
    assert len(invoked) == 1 and len(requests) == 2
    assert result.task_id == evidence[0]["task_id"] == evidence[0]["agent_session_id"]
    assert evidence[0]["tool_call_id"] == "call-1"
    assert "80%" in speech_summary(result, ("battery",))
    assert '"generation": 8' in result.path.read_text()
    assert all(r["reasoning_effort"] == "none" and r["max_tokens"] == 768 for r in requests)
    assert any(m["role"] == "tool" and "80%" in str(m["content"]) for m in requests[1]["messages"])
    from src.brain.agent.runner import inspect_session
    assert inspect_session(result.task_id)["actions_replayed"] is False


def test_local_proposal_only_dispatches_once_after_completion(installed, tmp_path):
    """模型的成功文字不会执行动作，完整核验后外层只派发一次并使用实际状态。"""
    executor = Executor()
    with model_server(final="已打开页面且加载完成。") as (url, requests):
        job = JACBrainTaskRunner(brain=HarnessBrain(url, lambda: 8192), output_dir=tmp_path, local_executor=executor)
        fields = local_instruction("打开Safari")
        result = job.run("打开Safari", local_action=fields)
    assert len(executor.calls) == 1 and len(requests) == 1
    assert result.status == "submitted" and "加载完成" not in result.answer
    state = json.loads((job.run_dir / "calls.json").read_text())
    assert state["calls"][0]["status"] == "proposed"
    assert job.brain.runtime["finish_reason"] == "blocked"
    assert speech_summary(result, fields) == result.answer


@pytest.mark.parametrize("mode", ["extra", "duplicate", "wrong", "truncated", "reasoning"])
def test_bad_model_turn_never_dispatches_local_action(installed, tmp_path, mode):
    """有多调用、越权、截断或重复提议时整任务拒绝，零系统副作用。"""
    executor = Executor()
    with model_server(mode) as (url, requests):
        job = JACBrainTaskRunner(brain=HarnessBrain(url, lambda: 8192), output_dir=tmp_path, local_executor=executor)
        with pytest.raises(BrainError):
            job.run("打开Safari", local_action=local_instruction("打开Safari"))
    assert not executor.calls and not list(tmp_path.iterdir())


def test_cancelled_proposal_cannot_dispatch(installed, tmp_path):
    """取消模型 HTTP 等待后及时回收进程，不在迟到回复后执行动作。"""
    executor, cancelled = Executor(), threading.Event()
    with model_server("slow") as (url, requests):
        job = JACBrainTaskRunner(brain=HarnessBrain(url, lambda: 8192), output_dir=tmp_path, local_executor=executor)
        timer = threading.Timer(.4, cancelled.set)
        timer.start()
        try:
            with pytest.raises(BrainCancelled):
                job.run("打开Safari", local_action=local_instruction("打开Safari"), should_stop=cancelled.is_set)
        finally:
            timer.join()
    assert not executor.calls and not list(tmp_path.iterdir())


def test_proposal_stops_before_model_can_repeat(installed, tmp_path):
    """即使模型每次都提议相同动作，登记后拒绝进入第二步，只执行一次。"""
    executor = Executor()
    with model_server("repeat") as (url, requests):
        job = JACBrainTaskRunner(brain=HarnessBrain(url, lambda: 8192), output_dir=tmp_path, local_executor=executor)
        result = job.run("打开Safari", local_action=local_instruction("打开Safari"))
    assert result.status == "submitted" and len(requests) == len(executor.calls) == 1
    assert job.brain.runtime["steps"] == 1


def test_runtime_timeout_reclaims_worker_without_tools(installed, tmp_path):
    """运行时等待超过任务预算后取消子进程，不把迟到回包转成工具执行。"""
    called = []
    tools = [{"type": "function", "function": {"name": "get_system_info", "description": "查询电量",
             "parameters": {"type": "object", "properties": {"info_type": {"type": "string", "enum": ["battery"]}},
                            "required": ["info_type"]}}}]
    with model_server("slow") as (url, requests):
        brain = HarnessBrain(url, lambda: 8192)
        brain.task_id, brain.run_dir = "timeout-test", tmp_path
        started = time.monotonic()
        with pytest.raises(BrainCancelled, match="超时"):
            brain._run("查电量", "只查询电池", tools, lambda *args: called.append(args), None, .6, 4)
        assert time.monotonic() - started < 3
    assert not called and brain.runtime is None


def test_cancel_after_dispatch_retains_action_evidence_without_report(installed, tmp_path):
    """已派发动作不能撤回；取消后保留派发证据但不发布完成报告。"""
    stopped = threading.Event()
    class CancelAfterDispatch(Executor):
        """模拟系统收到动作后会话恰好停止。"""
        def execute(self, name, args, should_stop, lock, on_dispatch):
            """记录一次交接并触发撤销，不重复派发。"""
            result = super().execute(name, args, should_stop, lock, on_dispatch)
            stopped.set()
            return result
    executor = CancelAfterDispatch()
    with model_server() as (url, requests):
        job = JACBrainTaskRunner(brain=HarnessBrain(url, lambda: 8192), output_dir=tmp_path, local_executor=executor)
        with pytest.raises(BrainCancelled):
            job.run("打开Safari", local_action=local_instruction("打开Safari"), should_stop=stopped.is_set)
    state = json.loads((job.run_dir / "state.json").read_text())
    assert len(executor.calls) == 1 and state["status"] == "cancelled"
    assert state["events"][0]["event"] == "on_action_dispatched" and not list(tmp_path.iterdir())


def test_unapproved_read_batch_executes_nothing(installed, tmp_path, monkeypatch):
    """只读任务也整批校验，后一个越权调用不能留下前一个已查询的部分结果。"""
    called = []
    monkeypatch.setattr("src.brain.task_runner.execute_tool", lambda *args: called.append(args))
    with model_server("extra") as (url, requests):
        job = JACBrainTaskRunner(brain=HarnessBrain(url, lambda: 8192), output_dir=tmp_path)
        with pytest.raises(BrainError):
            job.run("查一下电池电量", info_types=("battery",))
    assert not called and not list(tmp_path.iterdir())


def test_web_projection_keeps_original_passage_ids(installed, tmp_path):
    """模型只选原始段落编号，程序用完整原文交付，超长页面不挤爆上下文。"""
    text = "这是一段已读取并核实来自当前页面的测试来源文字。"
    class Search:
        """返回有大量段落的确定来源。"""
        def search(self, query, stop):
            """构造完整证据与可裁剪的模型投影。"""
            return {"results": [{"id": 1, "title": "测试来源", "url": "https://example.com/",
                                 "page": {"status": "read", "text": text,
                                          "passages": [{"id": i, "text": text} for i in range(1, 31)]}}]}
    with model_server(final='{"selections":[{"source_id":1,"passage_id":1}]}') as (url, requests):
        job = JACBrainTaskRunner(brain=HarnessBrain(url, lambda: 8192), output_dir=tmp_path, search_client=Search())
        result = job.run("上网搜索测试来源", web_query="测试来源")
    assert text in result.answer and len(requests) == 2
    assert len(result.trace[0]["output"]) > 1000


def test_missing_installation_fails_before_decoder_start(monkeypatch, tmp_path):
    """缺少独立环境时不启动转写或采集，也不回落旧 runner。"""
    monkeypatch.setattr(production, "CACHE", tmp_path)
    called = []
    pipeline = TaskPipeline(GatewayCallbacks(), decoder_factory=lambda path: SimpleNamespace(start=lambda: called.append(True)))
    with pytest.raises(AgentUnavailable):
        pipeline.start()
    assert not called


def test_web_projection_preserves_provenance_and_rejects_hidden_passage():
    """裁剪保留日期和最终地址，模型猜测未见段落编号不能通过原文核验。"""
    text = "已读取的页面正文用于核验摘录。" * 10
    page = {"status": "read", "url": "https://example.com/final", "text": text,
            "date_metadata": {"published": "2026-10-20"}, "retrieved_at": "2026-10-20T12:00:00+08:00",
            "passages": [{"id": i, "text": text} for i in range(2, 32)]}
    results = [{"id": 7, "title": "来源", "url": "https://example.com/", "page": page}]
    projected = json.loads(production.compact_web_output(json.dumps({"results": results})))
    visible = projected["results"][0]["page"]
    assert all(visible[key] == page[key] for key in ("url", "date_metadata", "retrieved_at"))
    assert visible["passages"][0]["id"] == 2 and len(visible["passages"]) < 30
    job = JACBrainTaskRunner()
    job.brain.web_projection = projected
    assert text in job.ground_web_answer('{"selections":[{"source_id":7,"passage_id":2}]}', results)
    with pytest.raises(BrainError, match="来源原文"):
        job.ground_web_answer('{"selections":[{"source_id":7,"passage_id":31}]}', results)


def test_production_scope_cannot_be_widened(tmp_path):
    """实际完整句的授权范围必须与任务参数相同，不能把 battery 改成 all。"""
    job = JACBrainTaskRunner(output_dir=tmp_path)
    with pytest.raises(BrainError, match="范围"):
        job.run("查一下电池电量", info_types=("all",))


def test_boundary_deduplicates_exact_call_and_rejects_changed_args(tmp_path):
    """传输重复使用原结果；相同 ID 修改参数会毒化任务并拒绝后续执行。"""
    schema = [{"function": {"name": "get_system_info", "parameters": {"type": "object", "properties": {
        "info_type": {"type": "string", "enum": ["battery"]}}, "required": ["info_type"]}}}]
    invoked = []
    boundary = VoiceToolBoundary("test", schema, lambda name, args: invoked.append(args) or "80%", None,
                                 time.monotonic()+10, tmp_path / "calls.json")
    call = {"id": "one", "name": "get_system_info", "arguments": {"info_type": "battery"}}
    boundary.execute({"name": "__validate_batch__", "args": {"calls": [call]}, "call_id": "batch-1", "session_id": "test"})
    request = {"name": call["name"], "args": call["arguments"], "call_id": "one", "session_id": "test"}
    assert boundary.execute(request) == boundary.execute(request) == "80%" and len(invoked) == 1
    with pytest.raises(BrainError):
        boundary.execute({**request, "args": {"info_type": "all"}})
    with pytest.raises(BrainError):
        boundary.execute(request)


def test_default_pipeline_uses_harness_and_invalidates_late_result(installed, tmp_path, monkeypatch):
    """默认生产构造路径不用旧大脑；重连取消等待，不发布旧报告或播报。"""
    executor, events, speech = Executor(), [], []
    original = production.JACBrainTaskRunner
    monkeypatch.setattr("src.brain.llm.LocalBrain", lambda *a, **k: pytest.fail("不能回落旧执行器"))
    with model_server("slow") as (url, requests):
        def factory(base_url):
            """仅替换系统动作与容量读取，仍让管线走默认 JACBrain 构造分支。"""
            return original(brain=HarnessBrain(base_url, lambda: 8192), output_dir=tmp_path, local_executor=executor)
        monkeypatch.setattr(production, "JACBrainTaskRunner", factory)
        callbacks = GatewayCallbacks()
        callbacks.on_task_event = lambda state, detail: events.append((state, detail))
        pipeline = TaskPipeline(callbacks, brain_url=url, decoder_factory=lambda path: SimpleNamespace())
        pipeline.on_result_speech = lambda *args: speech.append(args)
        pipeline.begin_session()
        thread = threading.Thread(target=pipeline._task_loop)
        thread.start()
        try:
            utterance = Utterance("voice-old", pipeline.generation, time.monotonic(), np.zeros(0), False)
            assert pipeline.submit(utterance, "打开Safari", local_instruction("打开Safari"))
            end = time.monotonic()+3
            while not requests and time.monotonic() < end:
                time.sleep(.01)
            assert requests
            pipeline.invalidate()
            pipeline.begin_session()
            pipeline.stopped.set()
            thread.join(3)
            assert not thread.is_alive()
        finally:
            pipeline.stopped.set()
            thread.join(3)
    assert not executor.calls and not speech and not list(tmp_path.iterdir())
    assert not any(state in {"completed", "submitted"} for state, _ in events)


def test_harness_website_ask_then_gui_choice_is_single_owner(installed, tmp_path):
    """真实 SDK 选择候选，GUI 确认由原权限域一次派发；确认路径不重跑模型。"""
    candidate = {"id": 1, "title": "样例站", "url": "https://example.com/",
                 "evidence": {"auto_eligible": False, "identity_conflict": True},
                 "page": {"status": "read", "title": "样例站", "text": "样例站"}}
    class Search:
        """保存需要用户确认的候选，拒绝把测试变成公网访问。"""
        def search(self, name, stop):
            """返回单个证据不足候选。"""
            return {"name": name, "candidates": [candidate]}
        def preflight(self, url, stop):
            """仅核对确认的公开 URL。"""
            assert url == candidate["url"] and not stop()
    executor, events, speech = Executor(), [], []
    with model_server(final="请用户确认。") as (url, requests):
        job = JACBrainTaskRunner(brain=HarnessBrain(url, lambda: 8192), output_dir=tmp_path / "reports",
                                website_search=Search(), website_cache=WebsiteCache(tmp_path / "cache.json"), local_executor=executor)
        callbacks = GatewayCallbacks()
        callbacks.on_task_event = lambda state, detail: events.append((state, detail))
        pipeline = TaskPipeline(callbacks, decoder_factory=lambda path: SimpleNamespace(), runner_factory=lambda: job)
        pipeline.on_result_speech = lambda identity, generation, result, fields: speech.append((result, fields))
        pipeline.begin_session()
        thread = threading.Thread(target=pipeline._task_loop)
        thread.start()
        try:
            utterance = Utterance("voice-site", pipeline.generation, time.monotonic(), np.zeros(0), False)
            assert pipeline.submit(utterance, "打开样例站", ("website", "样例站"))
            end = time.monotonic()+4
            while (pipeline.busy or not pipeline.website_pending) and time.monotonic() < end:
                time.sleep(.01)
            assert pipeline.website_pending and not executor.calls
            token = pipeline.website_pending["token"]
            assert not pipeline.choose_website("old-token", 1)
            assert pipeline.choose_website(token, 1)
            assert not pipeline.choose_website(token, 1)
            end = time.monotonic()+4
            while pipeline.busy and time.monotonic() < end:
                time.sleep(.01)
            assert len(executor.calls) == 1 and len(requests) == 1
            assert len(speech) == 2 and speech[-1][0].status == "submitted"
            assert speech_summary(*speech[-1]) == speech[-1][0].answer
            state = json.loads((job.run_dir / "state.json").read_text())
            assert state["source"]["kind"] == "gui" and state["source"]["generation"] == pipeline.generation
            assert state["runtime"] is None
        finally:
            pipeline.stopped.set()
            pipeline.invalidate()
            thread.join(3)


def test_text_probe_preserves_cancellation_callable(monkeypatch, capsys, tmp_path):
    """统一文字探针传递可调用取消判据，不提前求值或绕过范围路由。"""
    import verify_brain_tasks
    class Runner:
        """不执行真实模型的 CLI 参数检查。"""
        def __init__(self, *args, **kwargs):
            """接受当前探针初始化参数。"""
        def run(self, task, should_stop, **scope):
            """检查正确取消语义与精确电池范围。"""
            assert callable(should_stop) and not should_stop() and scope == {"info_types": ("battery",)}
            return SimpleNamespace(task_id="test", status="completed", path=tmp_path / "report.md", elapsed_seconds=0, trace=[])
    monkeypatch.setattr(verify_brain_tasks, "JACBrainTaskRunner", Runner)
    monkeypatch.setattr(verify_brain_tasks, "check_installation", lambda: None)
    assert verify_brain_tasks.main(["--task", "查一下电池电量"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "completed"
