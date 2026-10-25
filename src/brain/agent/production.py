"""生产 JACBrain：复用语音任务权限/证据，由独立 Harness 拥有模型回合。"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import threading
import time
from uuid import uuid4

from src.brain.llm import ThinkResult
from src.brain.lm_studio import BrainCancelled, BrainError, LMStudioClient, check_cancelled
from src.brain.task_runner import BrainTaskRunner
from src.tools.validation import validate_arguments
from .boundary import atomic_json
from .profile import ROOT, MODEL, build_patch
from .runner import CACHE, ToolServer, child_environment, loaded_context, serial_task, stop_child


class AgentUnavailable(BrainError):
    """独立环境缺失或版本不符；不得自动回落旧模型执行路径。"""


def check_installation():
    """在采集前以独立解释器核对全部版本与插件，不调用模型或下载资源。"""
    python = CACHE / "venv/bin/python"
    if not python.is_file():
        raise AgentUnavailable("Agent 环境未安装，请执行 setup_new_computer.py --only agent")
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(ROOT), "PYTHONUTF8": "1"}
    try:
        result = subprocess.run([str(python), "-m", "src.brain.agent.worker", "--check"],
                                cwd=ROOT, env=env, capture_output=True, timeout=5, check=True)
        if json.loads(result.stdout).get("status") != "ready":
            raise ValueError("未确认就绪")
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise AgentUnavailable("Agent 版本或插件自检失败，请重新运行独立安装阶段") from error


def compact_web_output(output, budget=2600):
    """仅裁剪模型可见来源投影，完整证据保留；段落及原始编号不改写。"""
    data = json.loads(output)
    projected = {"results": [], "truncated": True}
    originals = []
    for item in data.get("results", []):
        page = item.get("page", {})
        if page.get("status") != "read" or not page.get("passages"):
            continue
        projected["results"].append({"id": item["id"], "title": item.get("title", "")[:70], "url": item.get("url"),
                                     "page": {"status": "read", "url": page.get("url", item.get("url")),
                                              "date_metadata": page.get("date_metadata", {}),
                                              "retrieved_at": page.get("retrieved_at"), "passages": []}})
        if len(json.dumps(projected, ensure_ascii=False, separators=(",", ":")).encode()) > budget - 800:
            projected["results"].pop()
        else:
            originals.append(item)
    # 每个来源轮流保留一段，避免首个长页面挤掉其余来源。
    for index in range(30):
        for original, target in zip(originals, projected["results"]):
            passages = original.get("page", {}).get("passages", [])
            if index >= len(passages) or not 5 <= len(passages[index].get("text", "")) <= 240:
                continue
            target["page"]["passages"].append(passages[index])
            if len(json.dumps(projected, ensure_ascii=False, separators=(",", ":")).encode()) > budget:
                target["page"]["passages"].pop()
    return json.dumps(projected, ensure_ascii=False, separators=(",", ":"))


def compact_candidates(prompt):
    """保留候选编号、地址和全部判据，缩短网页正文；原候选仍由执行器核验。"""
    data = json.loads(prompt)
    for candidate in data["candidates"]:
        page = candidate.get("page", {})
        candidate.pop("snippet", None)
        candidate["page"] = {"status": page.get("status"), "title": page.get("title", "")[:100],
                             "names": page.get("names", [])[:4], "text": page.get("text", "")[:100]}
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


class VoiceToolBoundary:
    """只读执行或无副作用提议；整批先验、调用去重和拒绝状态在 Python 保持。"""

    def __init__(self, task_id, tools, execute, should_stop, deadline, ledger, on_call=None, on_projection=None,
                 max_calls=4, max_batches=4, reuse_repeated=False):
        """绑定本次 schema 和执行函数，execute=None 表示仅登记提议。"""
        self.task_id, self.execute_tool = task_id, execute
        self.schemas = {item["function"]["name"]: item["function"]["parameters"] for item in tools}
        self.should_stop, self.deadline, self.ledger, self.on_call = should_stop, deadline, ledger, on_call
        self.lock, self.closed = threading.RLock(), threading.Event()
        self.calls, self.batches, self.trace = {}, {}, []
        self.failure = None
        self.on_projection = on_projection
        self.max_calls, self.max_batches = max_calls, max_batches
        self.reuse_repeated = reuse_repeated

    def check(self):
        """终止、外部取消、超时或任何越权都使本任务不可继续。"""
        check_cancelled(self.should_stop)
        if self.closed.is_set() or time.monotonic() >= self.deadline:
            raise BrainCancelled("Agent 已取消或超时")
        if self.failure:
            raise BrainError(self.failure)

    def save(self):
        """每次实际执行前后记录调用 ID 和状态，不依赖运行时是否成功落盘。"""
        atomic_json(self.ledger, {"task_id": self.task_id, "calls": self.trace, "failure": self.failure})

    def validate_batch(self, batch_id, calls):
        """整批参数通过后登记；副作用提议全任务仅一次，失败或重复不恢复执行。"""
        if not isinstance(calls, list) or len(calls) > 4:
            raise BrainError("工具批次超过上限")
        encoded = json.dumps(calls, ensure_ascii=False, sort_keys=True)
        if batch_id in self.batches:
            if self.batches[batch_id] != encoded:
                raise BrainError("批次 ID 被改写")
            return {"validated": True}
        if len(self.batches) >= self.max_batches or len(self.calls) + len(calls) > (1 if self.execute_tool is None else self.max_calls):
            raise BrainError("超过单任务工具或回合预算")
        pending, signatures = {}, {(v["name"], json.dumps(v["arguments"], sort_keys=True)) for v in self.calls.values()}
        for call in calls:
            if not isinstance(call, dict) or set(call) != {"id", "name", "arguments"}:
                raise BrainError("工具批次结构无效")
            name, identity = call["name"], call["id"]
            if not isinstance(identity, str) or not 1 <= len(identity) <= 160 or identity in self.calls or identity in pending:
                raise BrainError("工具调用 ID 重复或无效")
            if name not in self.schemas:
                raise BrainError("工具未获本任务授权")
            validate_arguments(self.schemas[name], call["arguments"])
            signature = (name, json.dumps(call["arguments"], sort_keys=True))
            if signature in signatures and not self.reuse_repeated:
                raise BrainError("同一动作不得用新调用 ID 重试")
            signatures.add(signature)
            pending[identity] = copy.deepcopy(call)
            if self.reuse_repeated:
                prior = next((key for key, item in reversed(list({**self.calls, **pending}.items()))
                              if key != identity and item['name'] == name and item['arguments'] == call['arguments']), None)
                if prior:
                    previous = self.calls.get(prior, pending.get(prior))
                    retry_read = False
                    if name in {'read_source', 'compare_sources', 'read_wechat', 'web_search', 'finish'} and 'result' in previous:
                        try:
                            retry_read = json.loads(previous['result']).get('status') == 'needs_input'
                        except (ValueError, TypeError, AttributeError):
                            pass
                    if not retry_read:
                        pending[identity]['reuse_of'] = prior
        self.batches[batch_id] = encoded
        self.calls.update(pending)
        return {"validated": True}

    def execute(self, request):
        """HTTP 边界只接收本任务身份；提议不调用工具，实际只读结果可反馈模型。"""
        with self.lock:
            self.check()
            try:
                if not isinstance(request, dict) or set(request) != {"name", "args", "call_id", "session_id"}:
                    raise BrainError("工具请求结构无效")
                name, args, identity = request["name"], request["args"], request["call_id"]
                if request["session_id"] != self.task_id or not isinstance(identity, str) or not isinstance(args, dict):
                    raise BrainError("工具不属于本任务")
                if name == "__validate_batch__":
                    if set(args) != {"calls"}:
                        raise BrainError("工具批次参数无效")
                    return self.validate_batch(identity, args["calls"])
                approved = self.calls.get(identity)
                if not approved or approved["name"] != name or approved["arguments"] != args:
                    raise BrainError("工具未经整批批准或参数被改写")
                validate_arguments(self.schemas[name], args)
                if "result" in approved:
                    return approved["result"]
                if approved.get('reuse_of'):
                    prior = self.calls[approved['reuse_of']]
                    if 'result' not in prior:
                        raise BrainError('重复工具的原调用尚未确认，拒绝重新执行')
                    approved['result'] = prior['result']
                    self.trace.append({'call_id': identity, 'session_id': self.task_id, 'name': name,
                                       'arguments': copy.deepcopy(args), 'status': 'reused',
                                       'reused_call_id': approved['reuse_of']})
                    self.save()
                    return approved['result']
                entry = {"call_id": identity, "session_id": self.task_id, "name": name,
                         "arguments": copy.deepcopy(args), "status": "started"}
                self.trace.append(entry)
                self.save()
                if self.on_call:
                    self.on_call(identity)
                if self.execute_tool is None:
                    output = "提议已登记，尚未执行。请结束本轮；不要再次调用工具或声称动作完成。"
                    entry["status"] = "proposed"
                else:
                    output = str(self.execute_tool(name, copy.deepcopy(args)))
                    entry.update(status="completed", output=output)
                    if name == "search_web":
                        output = compact_web_output(output)
                        if self.on_projection:
                            self.on_projection(json.loads(output))
                if len(output.encode("utf-8")) > 131072:
                    raise BrainError("工具结果超过预算")
                self.check()
                approved["result"] = output
                self.save()
                return output
            except Exception as error:
                self.failure = str(error)
                self.save()
                raise


class HarnessBrain:
    """适配既有大脑接口，所有模型回合只由独立 Harness 执行。"""

    def __init__(self, base_url="http://127.0.0.1:12345", context_reader=None):
        """保留可注入的本机容量读取器供离线测试，默认核对精确已加载 Qwen。"""
        self.base_url = LMStudioClient(base_url).base_url
        self.context_reader = context_reader or (lambda: loaded_context(self.base_url))
        self.task_id = self.run_dir = self.current_call = self.runtime = None
        self.web_projection = None

    def _run(self, prompt, system, tools, execute, should_stop, timeout, steps, *, domain="voice", stop_tool=None):
        """在任务线程拥有运行时进程组；终态、持久化和完整证据都通过才返回。"""
        check_cancelled(should_stop)
        context = self.context_reader()
        deadline = time.monotonic() + timeout
        boundary = VoiceToolBoundary(self.task_id, tools, execute, should_stop, deadline,
                                     self.run_dir / "calls.json", lambda value: setattr(self, "current_call", value),
                                     lambda value: setattr(self, "web_projection", value),
                                     max_calls=12 if domain == "assistant" else 4,
                                     max_batches=12 if domain == "assistant" else 4,
                                     reuse_repeated=domain == "assistant")
        server, process, thread = None, None, None
        try:
            persona = system + ("只登记一次工具提议；返回已登记后结束，不重复调用。" if execute is None else "")
            patch = self.run_dir / "jac.patch.json"
            atomic_json(patch, build_patch(context, self.base_url + "/v1", persona=persona, plugin="production_plugin.mjs"))
            plugin_config = self.run_dir / "voice-tools.json"
            atomic_json(plugin_config, {"tools": tools, "max_steps": steps, "session_id": self.task_id,
                                       "proposal_only": execute is None, "domain": domain, "stop_tool": stop_tool})
            config = {"dsh_home": str(self.run_dir / "home"), "profile": "sdk-minimal", "patches": [str(patch)],
                      "cwd": str(self.run_dir), "runtime_cwd": str(ROOT), "provider": "jac-local", "model": MODEL,
                      "reasoning_effort": "off", "max_tokens": 768, "request_timeout_seconds": min(timeout, 90),
                      "initialize_timeout_seconds": 10, "shutdown_timeout_seconds": 1, "session_id": self.task_id,
                      "prompt": prompt, "production": True}
            atomic_json(self.run_dir / "worker.json", config)
            server = ToolServer(boundary)
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .05}, daemon=True)
            thread.start()
            env = child_environment(boundary, server, context)
            env["JAC_VOICE_CONFIG"] = str(plugin_config)
            with (self.run_dir / "worker.out").open("wb") as out, (self.run_dir / "worker.err").open("wb") as err:
                process = subprocess.Popen([str(CACHE / "venv/bin/python"), "-m", "src.brain.agent.worker",
                                            str(self.run_dir / "worker.json")], cwd=ROOT, env=env,
                                           stdout=out, stderr=err, start_new_session=True)
                while process.poll() is None:
                    boundary.check()
                    if any((self.run_dir / name).stat().st_size > 131072 for name in ("worker.out", "worker.err")):
                        raise BrainError("Agent 诊断超过预算")
                    time.sleep(.05)
            boundary.check()
            if process.returncode:
                raise BrainError("Agent 运行失败，见本地任务记录")
            result = json.loads((self.run_dir / "worker.out").read_text(encoding="utf-8"))
            expected_finish = "blocked" if execute is None or stop_tool else "completed"
            if result.get("session_id") != self.task_id or result.get("finish_reason") != expected_finish:
                raise BrainError("Agent 未完整完成回合")
            if len(boundary.trace) != len(boundary.calls) or not boundary.trace:
                raise BrainError("Agent 未提供完整工具证据")
            if execute is None and (len(boundary.trace) != 1 or boundary.trace[0]["status"] != "proposed"):
                raise BrainError("Agent 单动作提议未完整登记")
            self.runtime = {key: value for key, value in result.items() if key != "final_response"}
            self.runtime["context_length"] = context
            return result.get("final_response", ""), boundary.trace
        finally:
            boundary.closed.set()
            if process is not None:
                stop_child(process)
            if server is not None:
                server.shutdown()
                server.server_close()
                thread.join(timeout=1)

    def think_with_tools(self, messages, tools, temperature=0, max_tokens=512, should_stop=None):
        """只收集完整单动作提议；外部原执行器在核验后才实际派发。"""
        if len(messages) != 2 or messages[0]["role"] != "system" or messages[1]["role"] != "user":
            raise BrainError("生产提议只接受本次系统约束与用户任务")
        prompt = messages[1]["content"]
        if tools[0]["function"]["name"] == "select_website":
            prompt = compact_candidates(prompt)
        _, trace = self._run(prompt, messages[0]["content"], tools, None, should_stop, 60, 1)
        return ThinkResult(tool_calls=[{"name": entry["name"], "arguments": entry["arguments"]} for entry in trace], has_tools=True)

    def run_agentic(self, prompt, tools, tool_executor, *, system_prompt, max_tokens=768,
                    max_iterations=4, should_stop=None, timeout=120, temperature=0):
        """使用 Harness 完成只读查询闭环，正文留给原程序做来源核验和报告。"""
        if any(tool["function"]["name"] not in {"get_system_info", "search_web"} for tool in tools):
            raise BrainError("生产 Agent 执行域仅开放只读查询")
        answer, _ = self._run(prompt, system_prompt, tools, tool_executor, should_stop, timeout, min(4, max_iterations))
        yield answer


class JACBrainTaskRunner(BrainTaskRunner):
    """统一生产入口：Harness 模型回合与受控确定性路径只能各拥有任务一次。"""

    def __init__(self, base_url="http://127.0.0.1:12345", *, brain=None, **kwargs):
        """复用既有报告、系统/联网/本地工具，SDK 保持在独立进程中。"""
        super().__init__(brain=brain or HarnessBrain(base_url), **kwargs)
        self.identity, self.manifest, self.run_dir = None, None, None

    def task_identity(self):
        """使报告编号与运行时会话、工具证据和来源记录共享同一身份。"""
        return self.identity

    def report_footer(self):
        """给报告附上有界的任务归属信息，不朗读该控制元数据。"""
        metadata = {key: self.manifest[key] for key in ("task_id", "source", "scope")}
        metadata.update(executor="harness" if self.brain.runtime else "deterministic",
                        runtime=self.brain.runtime)
        return "\n\n## 执行归属\n\n```json\n" + json.dumps(metadata, ensure_ascii=False, indent=2) + "\n```\n"

    def ground_web_answer(self, answer, results):
        """答案只能引用实际送入本轮模型的完整段落，不能猜选被裁掉的编号。"""
        from src.brain.task_runner import grounded_web_answer
        visible = {item["id"]: {p["id"] for p in item["page"]["passages"]}
                   for item in (self.brain.web_projection or {}).get("results", [])}
        restricted = copy.deepcopy(results)
        for item in restricted:
            item["page"]["passages"] = [p for p in item["page"].get("passages", [])
                                         if p["id"] in visible.get(item["id"], set())]
        return grounded_web_answer(answer, restricted)

    def run(self, task, should_stop=None, task_context=None, **options):
        """整项任务串行且不自动回退；模型提议、真实事件和交付结果分别记录。"""
        if options.pop("assistant_task", False):
            from .assistant import AssistantTaskRunner
            inputs = options.pop("inputs", None)
            if set(options) - {"publication_lock", "on_tool_result"}:
                raise BrainError("开放任务不能混用旧单步权限")
            return AssistantTaskRunner(brain=self.brain, output_dir=self.output_dir, search_client=self.search_client).run(
                task, inputs=inputs, should_stop=should_stop, task_context=task_context, **options)
        from src.omni.task_pipeline import route_instruction
        fields = route_instruction(task)
        scope = options.get("local_action") or options.get("website_request")
        if scope is None:
            scope = ("web", options["web_query"]) if options.get("web_query") is not None else options.get("info_types")
        if not fields or tuple(scope or ()) != fields:
            raise BrainError("生产任务范围与来源完整指令不一致")
        check_cancelled(should_stop)
        with serial_task():
            self.identity = "jac-" + uuid4().hex
            self.run_dir = CACHE / "runs" / self.identity
            self.run_dir.mkdir(parents=True, mode=0o700)
            self.brain.task_id, self.brain.run_dir = self.identity, self.run_dir
            self.brain.current_call, self.brain.runtime = None, None
            self.brain.web_projection = None
            self.manifest = {"task_id": self.identity, "source": copy.deepcopy(task_context or {"kind": "explicit_text"}),
                             "request": task, "scope": fields, "status": "running", "events": [], "model": MODEL}
            atomic_json(self.run_dir / "state.json", self.manifest)
            for key in ("on_tool_result", "on_action_dispatched"):
                original = options.get(key)
                def record(entry, callback=original, kind=key):
                    """实际回执立即留证据，独立副本交给当前代次 GUI。"""
                    enriched = {**copy.deepcopy(entry), "task_id": self.identity,
                                "agent_session_id": self.identity if self.brain.runtime or self.brain.current_call else None,
                                "tool_call_id": self.brain.current_call or "program-" + str(len(self.manifest["events"]))}
                    self.manifest["events"].append({"event": kind, **enriched})
                    atomic_json(self.run_dir / "state.json", self.manifest)
                    if callback:
                        callback(enriched)
                options[key] = record
            try:
                result = super().run(task, should_stop=should_stop, **options)
                self.manifest.update(status=result.status, report=str(result.path), runtime=self.brain.runtime)
                return result
            except Exception as error:
                self.manifest.update(status="cancelled" if isinstance(error, BrainCancelled) else "failed", error=str(error))
                raise
            finally:
                atomic_json(self.run_dir / "state.json", self.manifest)
