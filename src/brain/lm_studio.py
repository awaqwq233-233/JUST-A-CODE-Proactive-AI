"""本机 LM Studio 契约：精确模型、零思考输出、有限请求与协作取消。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import time
from urllib.parse import urlsplit

import httpx

MODEL = "qwen/qwen3.6-35b-a3b"


class BrainError(RuntimeError):
    """大脑请求或输出不满足执行契约，不能作为成功回答。"""


class BrainCancelled(BrainError):
    """调用者已取消任务，不再执行工具或发布文件。"""


def check_cancelled(should_stop=None):
    """每个有副作用的边界前检查取消；判据异常也停止执行。"""
    if should_stop is not None:
        try:
            stopped = should_stop()
        except Exception as exc:
            raise BrainCancelled("取消判据异常，任务已停止") from exc
        if stopped:
            raise BrainCancelled("任务已取消")


@dataclass
class Completion:
    """仅包含可交付内容、结构化工具请求和计数，不保留思考正文。"""

    content: str
    tool_calls: list
    usage: dict


class LMStudioClient:
    """使用已有 httpx 依赖访问 loopback；不加载或自动切换其他模型。"""

    def __init__(self, base_url="http://127.0.0.1:12345", model=MODEL,
                 timeout=90, transport=None):
        """校验本机地址和请求时限；transport 供离线协议测试注入。"""
        url = urlsplit(base_url)
        if (url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost", "::1"}
                or url.username or url.password or url.query or url.fragment
                or url.path not in {"", "/"}):
            raise ValueError("大脑地址须为无路径的本机 HTTP 地址")
        if not model or not 0 < timeout <= 300:
            raise ValueError("模型标识不能为空，请求超时须为 0–300 秒")
        self.base_url, self.model = base_url.rstrip("/"), model
        self.timeout, self.transport = timeout, transport
        self.last_usage = {}

    def require_loaded_model(self):
        """通过原生清单核对目标实例已加载且支持工具和关闭思考。"""
        try:
            with httpx.Client(trust_env=False, transport=self.transport, timeout=5) as client:
                response = client.get(self.base_url + "/api/v1/models")
                response.raise_for_status()
                models = response.json()["models"]
            for model in models:
                for instance in model.get("loaded_instances", []):
                    if instance.get("id") == self.model:
                        caps = model.get("capabilities", {})
                        if (not caps.get("trained_for_tool_use")
                                or "off" not in caps.get("reasoning", {}).get("allowed_options", [])):
                            raise BrainError("目标模型未声明工具或关闭思考能力")
                        return self.model
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise BrainError("无法核对 LM Studio 已加载模型；需要支持 /api/v1/models 的版本") from exc
        raise BrainError(f"目标模型未加载：{self.model}；不会回退到其他模型")

    def payload(self, messages, temperature, max_tokens, tools=None, stream=False):
        """统一使用实测有效的 reasoning_effort，尊重调用者的 token 上限。"""
        body = dict(model=self.model, messages=messages, temperature=temperature,
                    max_tokens=max_tokens, stream=stream, reasoning_effort="none")
        if tools:
            body.update(tools=tools, tool_choice="auto")
        return body

    @staticmethod
    def parse_response(data):
        """拒绝截断、仅思考、空响应和异常结构，不从思考中猜答案。"""
        try:
            choice = data["choices"][0]
            message = choice["message"]
            usage = data.get("usage") or {}
            reasoning_tokens = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)
            if message.get("reasoning_content") or reasoning_tokens:
                raise BrainError("关闭思考未生效，拒绝将思考内容当作任务结果")
            content = message.get("content") or ""
            calls = message.get("tool_calls") or []
            if not isinstance(content, str) or not isinstance(calls, list):
                raise BrainError("大脑响应类型异常")
            expected_finish = "tool_calls" if calls else "stop"
            if choice.get("finish_reason") != expected_finish:
                raise BrainError("大脑响应未完整结束，拒绝执行或发布截断内容")
            if not calls and not content.strip():
                raise BrainError("大脑未返回回答或工具调用")
            return Completion(content, calls, usage)
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise BrainError("大脑响应结构异常") from exc

    async def acomplete(self, messages, temperature=0.2, max_tokens=1024,
                        tools=None, should_stop=None):
        """请求等待期间每 100ms 检查取消；超时取消 HTTP 并关闭连接。"""
        check_cancelled(should_stop)
        body = self.payload(messages, temperature, max_tokens, tools)
        try:
            async with httpx.AsyncClient(trust_env=False, transport=self.transport,
                                         timeout=httpx.Timeout(self.timeout, connect=5)) as client:
                pending = asyncio.create_task(client.post(self.base_url + "/v1/chat/completions", json=body))
                deadline = time.monotonic() + self.timeout
                try:
                    while not pending.done():
                        check_cancelled(should_stop)
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise BrainError("大脑请求超时")
                        await asyncio.wait({pending}, timeout=min(0.1, remaining))
                    check_cancelled(should_stop)
                    response = await pending
                    response.raise_for_status()
                    completion = self.parse_response(response.json())
                    self.last_usage = completion.usage
                    return completion
                finally:
                    if not pending.done():
                        pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
        except (httpx.HTTPError, ValueError) as exc:
            raise BrainError("LM Studio 请求失败；未产生可交付结果") from exc

    def complete(self, messages, temperature=0.2, max_tokens=1024,
                 tools=None, should_stop=None):
        """同步调用桥接；GUI/asyncio 调用者必须放在后台线程或用 acomplete。"""
        return asyncio.run(self.acomplete(messages, temperature, max_tokens, tools, should_stop))

    def stream(self, messages, temperature=0.2, max_tokens=1024):
        """保留普通聊天 SSE；要求完整结束、零思考，流连接退出时释放。"""
        body = self.payload(messages, temperature, max_tokens, stream=True)
        deadline, finished, received = time.monotonic() + self.timeout, False, False
        try:
            with httpx.Client(trust_env=False, transport=self.transport,
                              timeout=httpx.Timeout(min(self.timeout, 15), connect=5)) as client:
                with client.stream("POST", self.base_url + "/v1/chat/completions", json=body) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if time.monotonic() >= deadline:
                            raise BrainError("大脑流式请求超时")
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if raw == "[DONE]":
                            break
                        event = json.loads(raw)
                        for choice in event.get("choices", []):
                            delta = choice.get("delta") or {}
                            if delta.get("reasoning_content") or delta.get("tool_calls"):
                                raise BrainError("普通聊天流返回了思考或工具请求")
                            if choice.get("finish_reason") is not None:
                                if choice["finish_reason"] != "stop":
                                    raise BrainError("大脑文本流被截断")
                                finished = True
                            if delta.get("content"):
                                received = True
                                yield delta["content"]
            if not finished or not received:
                raise BrainError("大脑文本流未完整返回")
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise BrainError("大脑文本流请求失败") from exc
