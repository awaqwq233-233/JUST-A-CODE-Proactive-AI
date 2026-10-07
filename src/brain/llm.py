"""多后端大脑兼容接口；当前独立任务显式选择本机 LM Studio。"""

Llama = None  # 旧本地后端仅在实际选择时导入，不影响当前 HTTP 大脑。

import importlib.util
import os
import json
import platform
import base64
import time

from .lm_studio import LMStudioClient, BrainError, check_cancelled
from src.tools.validation import validate_arguments
from dataclasses import dataclass, field


@dataclass
class ThinkResult:
    """带工具调用的推理结果。

    content     最终/中间自然语言文本
    tool_calls  解析后的工具调用列表：[{name, arguments(dict)}]
    raw_tool_calls 原样回传模型所需的格式（含 id / function.arguments 字符串），用于下一轮携带工具结果
    has_tools   本次是否真的触发了工具调用
    """
    content: str = ""
    tool_calls: list = field(default_factory=list)
    raw_tool_calls: list = field(default_factory=list)
    has_tools: bool = False


def parse_tool_calls(message):
    """把模型返回的 message.tool_calls 解析成两项：
    - parsed：训练循环用的 [{name, arguments(dict)}]
    - clean：原样回传 LM Studio 的格式（含 id 与 function.arguments 字符串）

    兼容两种来源：
    - LM Studio：arguments 是 JSON 字符串，id 由模型给出
    - Ollama：   arguments 直接是 dict，id 可能缺失（此处补一个）
    """
    raw = message.get("tool_calls") or []
    if not isinstance(raw, list):
        raise BrainError("tool_calls 必须是列表")
    parsed, clean, ids = [], [], set()
    for idx, tc in enumerate(raw):
        if not isinstance(tc, dict) or tc.get("type", "function") != "function":
            raise BrainError("工具调用结构异常")
        fn = tc.get("function")
        if not isinstance(fn, dict) or not isinstance(fn.get("name"), str) or not fn["name"]:
            raise BrainError("工具调用缺少名称")
        name = fn["name"]
        args_raw = fn.get("arguments", {})
        try:
            args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
        except (ValueError, TypeError) as exc:
            raise BrainError("工具参数不是完整 JSON，拒绝执行") from exc
        if not isinstance(args, dict):
            raise BrainError("工具参数须为 JSON 对象")
        call_id = tc.get("id") or f"call_{idx}"
        if not isinstance(call_id, str) or call_id in ids:
            raise BrainError("工具调用 ID 无效或重复")
        ids.add(call_id)
        parsed.append({"name": name, "arguments": args})
        clean.append({"id": call_id, "type": "function",
                      "function": {"name": name, "arguments": args_raw if isinstance(args_raw, str)
                                   else json.dumps(args, ensure_ascii=False)}})
    return parsed, clean


class LocalBrain:
    """
    Local LLM Brain.
    Supports LM Studio / Ollama / llama.cpp backends.
    """

    def __init__(self, model_path="models/Qwen3.5-9B-Q4_K_M.gguf", backend="auto",
                 lm_studio_model=None, lm_studio_url="http://127.0.0.1:12345"):
        """初始化实例"""
        self.llm = None
        self.multimodal = False
        self.backend = "mock"
        self.active_model_id = None
        self._explicit_lm_model = lm_studio_model
        # 当前本机实例须精确匹配，不依赖模型列表的排列顺序。
        self.brain_model_name = "qwen/qwen3.6-35b-a3b"

        self.lm_client = LMStudioClient(lm_studio_url, lm_studio_model or self.brain_model_name)
        self.lm_studio_url = self.lm_client.base_url + "/v1/chat/completions"
        self.lm_studio_check_url = self.lm_client.base_url + "/api/v1/models"

        self.ollama_base_url = "http://localhost:11434"
        self.ollama_model_name = "qwen2.5:7b"

        if backend == "lm_studio":
            self.backend = "lm_studio"
        elif backend == "ollama":
            self.backend = "ollama"
        elif backend == "llama_cpp":
            self.backend = "llama_cpp"
        elif backend == "auto":
            if self._check_lm_studio():
                print("[System] Detected LM Studio, using LM Studio backend")
                self.backend = "lm_studio"
            elif self._check_ollama():
                print("[System] Detected Ollama, using Ollama backend")
                self.backend = "ollama"
            elif importlib.util.find_spec("llama_cpp") is not None:
                print("[System] No API server found, switching to llama.cpp backend (CPU)")
                self.backend = "llama_cpp"
            else:
                print("[System] No backend available, using mock mode")
                return

        if self.backend == "lm_studio":
            self._init_lm_studio()
        elif self.backend == "ollama":
            self._init_ollama()
        elif self.backend == "llama_cpp":
            self._init_llama_cpp(model_path)

        if self._explicit_lm_model and self.backend in ("lm_studio",):
            print(f"[System] LM Studio model hint: {self._explicit_lm_model}")

    @staticmethod
    def _normalize(name):
        """规范化"""
        return name.lower().replace("-gguf", "").replace(".gguf", "").replace("_", "-").strip()

    def _pick_lm_model(self, models, preferred):
        """仅接受精确模型 ID，绝不退回清单首项或相似名称。"""
        target = self._explicit_lm_model or preferred
        return target if any(m.get("id") == target for m in models) else None

    def _check_lm_studio(self):
        """探测指定模型是否已加载；auto 仅为未迁移组件保留。"""
        try:
            self.active_model_id = self.lm_client.require_loaded_model()
            return True
        except BrainError:
            return False

    def _init_lm_studio(self):
        """核对本机精确模型实例，不自动加载、卸载或切换模型。"""
        self.active_model_id = self.lm_client.require_loaded_model()
        self.multimodal = True

    def _check_ollama(self):
        """检查Ollama"""
        import requests
        try:
            r = requests.get(f"{self.ollama_base_url}/api/tags", timeout=2)
            return r.status_code == 200
        except requests.exceptions.ConnectionError:
            return False
        except Exception:
            return False

    def _init_ollama(self):
        """初始化Ollama"""
        print(f"[System] Ollama backend ready, model: {self.ollama_model_name}")

    def _init_llama_cpp(self, model_path):
        """仅在实际选择旧本地后端时导入，当前独立任务不走 auto。"""
        global Llama
        try:
            from llama_cpp import Llama
        except ImportError:
            Llama = None
        if Llama is None:
            print("[Warning] llama-cpp-python not installed")
            return
        if not os.path.exists(model_path):
            print(f"[Warning] Model file not found: {model_path}")
            return
        print(f"[System] Loading brain model: {model_path} ...")
        llama_args = {
            "model_path": model_path,
            "n_ctx": 2048,
            "n_threads": min(8, os.cpu_count() or 4),
            "verbose": False,
        }
        mmproj_path = self._find_mmproj(model_path)
        if mmproj_path:
            print(f"[System] Found multimodal projection: {mmproj_path}")
            llama_args["mmproj"] = mmproj_path
        sys_plat = platform.system()
        if sys_plat == "Windows":
            # Windows 上 batch 调小以兼容老显卡 / 显存碎片
            llama_args["n_batch"] = 512
        elif sys_plat == "Darwin":
            # Apple Silicon / M 系列：启用 Metal GPU，全量 offload 到统一内存
            llama_args["n_gpu_layers"] = -1
            print("[System] macOS (Metal) 已启用 GPU offload (n_gpu_layers=-1)")
        # Linux 默认走 CPU；如有 CUDA 可在此或启动时设 n_gpu_layers 启用 GPU
        try:
            self.llm = Llama(**llama_args)
            if mmproj_path:
                self.multimodal = True
                print("[System] Multimodal vision mode enabled")
            print("[System] Brain loaded successfully")
        except Exception as e:
            print(f"[Error] Brain loading failed: {e}")

    def _find_mmproj(self, model_path):
        """查找多模态投影"""
        model_dir = os.path.dirname(model_path) or "."
        base_name = os.path.basename(model_path)
        model_prefix = base_name.rsplit("-", 1)[0]
        for f in os.listdir(model_dir):
            if f.startswith("mmproj-") and f.endswith(".gguf"):
                full_path = os.path.join(model_dir, f)
                if model_prefix in f:
                    return full_path
        for f in os.listdir(model_dir):
            if f.startswith("mmproj-") and f.endswith(".gguf"):
                return os.path.join(model_dir, f)
        return None

    def think(self, prompt, system_prompt="You are J.A.C., a helpful AI assistant. J.A.C. stands for Just A Code.", temperature=0.7, max_tokens=1024):
        """推理（默认 max_tokens=1024，可容纳约 500 字中文回复）"""
        if self.backend == "mock":
            return self._mock_response(prompt)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]
        if self.backend == "lm_studio":
            return self._query_lm_studio(messages, temperature, max_tokens)
        elif self.backend == "ollama":
            return self._query_ollama(messages, temperature, max_tokens)
        else:
            return self._query_llama_cpp(messages, temperature, max_tokens)

    def think_stream(self, prompt, system_prompt="You are J.A.C., a helpful AI assistant. J.A.C. stands for Just A Code.", temperature=0.7, max_tokens=768):
        """流式推理：yield 文本片段（token），形成"持续思考"的打字机效果。

        仅 lm_studio 后端真正走 SSE 流式；其余后端退化为一次性返回（包装成单元素生成器），
        调用方无需区分即可统一用 `for chunk in brain.think_stream(...)` 消费。
        """
        if self.backend == "mock":
            yield self._mock_response(prompt)
            return
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]
        if self.backend == "lm_studio":
            yield from self._query_lm_studio_stream(messages, temperature, max_tokens)
        else:
            # 非流式后端：直接一次性返回（保持接口一致）
            yield self.think(prompt, system_prompt, temperature, max_tokens)

    def think_with_image(self, prompt, frame, system_prompt="You are J.A.C., a helpful AI assistant.", temperature=0.7, max_tokens=1024):
        """推理带图像（默认 max_tokens=1024，可容纳约 250 字视觉描述）"""
        if self.backend == "mock":
            return self._mock_response(prompt)
        if self.backend not in ("lm_studio", "ollama") and not self.multimodal:
            print("[System] Multimodal not available, falling back to text mode")
            return self.think(prompt, system_prompt, temperature, max_tokens)
        try:
            import cv2
            ret, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if not ret:
                return self.think(prompt, system_prompt, temperature, max_tokens)
            img_b64 = base64.b64encode(buffer).decode("utf-8")
        except Exception as e:
            print(f"[Warning] Image processing failed: {e}")
            return self.think(prompt, system_prompt, temperature, max_tokens)
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}
                ]
            }
        ]
        if self.backend == "lm_studio":
            return self._query_lm_studio(messages, temperature, max_tokens)
        elif self.backend == "ollama":
            ollama_messages = [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "data": img_b64},
                        {"type": "text", "text": prompt}
                    ]
                }
            ]
            return self._query_ollama(ollama_messages, temperature, max_tokens)
        else:
            return self._query_llama_cpp(messages, temperature, max_tokens)

    def _query_lm_studio(self, messages, temperature, max_tokens, tools=None, should_stop=None):
        """使用本机统一契约，失败抛异常，不生成貌似成功的兜底回答。"""
        reply = self.lm_client.complete(messages, temperature, max_tokens, tools, should_stop)
        parsed, raw = parse_tool_calls({"tool_calls": reply.tool_calls})
        if tools is not None:
            return ThinkResult(content=reply.content, tool_calls=parsed,
                               raw_tool_calls=raw, has_tools=bool(parsed))
        if parsed:
            raise BrainError("普通回答收到意外工具调用")
        return reply.content

    def _query_lm_studio_stream(self, messages, temperature, max_tokens):
        """保留普通聊天 SSE，连接由统一 HTTP 客户端完整释放。"""
        yield from self.lm_client.stream(messages, temperature, max_tokens)

    def _query_ollama(self, messages, temperature, max_tokens, tools=None):
        """查询 Ollama。支持可选 tools 参数做 function calling。"""
        import requests
        try:
            body = {
                "model": self.ollama_model_name,
                "messages": messages,
                "stream": False,
                "think": False,  # 禁用 Qwen3 思考链，直接输出结果
                "options": {
                    "temperature": temperature,
                    "num_predict": max_tokens
                },
            }
            if tools:
                body["tools"] = tools
            resp = requests.post(
                f"{self.ollama_base_url}/api/chat",
                json=body,
                timeout=120
            )
            if resp.status_code != 200:
                print(f"[Error] Ollama API returned {resp.status_code}: {resp.text}")
                return ThinkResult(content="Sorry, brain connection has an issue.") if tools else \
                    "Sorry, brain connection has an issue."
            data = resp.json()
            message = data.get("message", {})
            content = message.get("content") or ""
            # function calling 分支：Ollama 的 tool_calls.arguments 已是 dict
            if tools and message.get("tool_calls"):
                parsed, raw = parse_tool_calls(message)
                if parsed:
                    return ThinkResult(content=content, tool_calls=parsed, raw_tool_calls=raw, has_tools=True)
            if not content:
                fallback = "（刚才走神了，能再问一次吗？）"
                return ThinkResult(content=fallback) if tools else fallback
            return ThinkResult(content=content) if tools else content
        except requests.exceptions.ConnectionError:
            print("[Error] Cannot connect to Ollama service (127.0.0.1:11434)")
            return ThinkResult(content="Sorry, cannot connect to brain server.") if tools else \
                "Sorry, cannot connect to brain server."
        except Exception as e:
            print(f"[Error] Ollama request failed: {e}")
            return ThinkResult(content="My brain is having trouble, please try again later.") if tools else \
                "My brain is having trouble, please try again later."

    def _query_llama_cpp(self, messages, temperature, max_tokens, tools=None):
        """查询llamacpp（暂不支持结构化 tool_calls，tools 参数被忽略）。"""
        if self.llm is None:
            text = "".join(m.get("content","") for m in messages if m.get("role")=="user")
            if isinstance(text, list):
                text = " ".join(str(t) for t in text if isinstance(t, str))
            return self._mock_response(text)
        try:
            output = self.llm.create_chat_completion(
                messages=messages, max_tokens=max_tokens, temperature=temperature
            )
            return output['choices'][0]['message']['content']
        except Exception as e:
            print(f"[Error] Thinking failed: {e}")
            return "My brain is having trouble, please try again later."

    def supports_tools(self):
        """当前后端是否支持结构化 function calling（装手能力）。"""
        return self.backend in ("lm_studio", "ollama")

    def think_with_tools(self, messages, tools, temperature=0.7, max_tokens=1024, should_stop=None):
        """带工具调用的推理（非流式）；LM Studio 等待期间支持取消。"""
        check_cancelled(should_stop)
        if self.backend == "mock":
            text = messages[-1].get("content", "") if messages else ""
            return ThinkResult(content=self._mock_response(text))
        if self.backend == "lm_studio":
            return self._query_lm_studio(messages, temperature, max_tokens, tools=tools, should_stop=should_stop)
        elif self.backend == "ollama":
            return self._query_ollama(messages, temperature, max_tokens, tools=tools)
        else:
            # llama_cpp 暂不支持结构化 tool_calls，直接当普通文本返回
            content = self._query_llama_cpp(messages, temperature, max_tokens)
            return ThinkResult(content=content)

    def run_agentic(self, prompt, tools, tool_executor,
                    system_prompt="You are J.A.C., a helpful AI assistant.",
                    temperature=0.2, max_tokens=1024, max_iterations=4,
                    should_stop=None, timeout=120):
        """有限工具循环；逐次执行前取消/校验，直接交付最终回答而不重复生成。"""
        if not 1 <= max_iterations <= 8 or not 0 < timeout <= 300:
            raise ValueError("工具循环次数须为 1–8，总时限须为 0–300 秒")
        allowed = {tool["function"]["name"]: tool["function"]["parameters"] for tool in tools}
        deadline = time.monotonic() + timeout

        def stopped():
            """向请求层传递用户取消和整个任务的总时限。"""
            check_cancelled(should_stop)
            return time.monotonic() >= deadline

        messages = [{"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}]
        for _ in range(max_iterations):
            check_cancelled(stopped)
            result = self.think_with_tools(messages, tools, temperature, max_tokens, should_stop=stopped)
            check_cancelled(stopped)
            if not result.tool_calls:
                if not result.content.strip():
                    raise BrainError("任务未返回最终回答")
                yield result.content
                return
            # 整批校验通过后才执行第一项，避免后续畸形请求导致部分执行。
            for tc in result.tool_calls:
                if tc["name"] not in allowed:
                    raise BrainError("模型请求了本任务未授权的工具")
                try:
                    validate_arguments(allowed[tc["name"]], tc["arguments"])
                except ValueError as exc:
                    raise BrainError(str(exc)) from exc
            messages.append({"role": "assistant", "content": result.content or "",
                             "tool_calls": result.raw_tool_calls})
            for tc, raw in zip(result.tool_calls, result.raw_tool_calls):
                check_cancelled(stopped)
                tool_output = tool_executor(tc["name"], tc["arguments"])
                check_cancelled(stopped)
                messages.append({"role": "tool", "tool_call_id": raw["id"],
                                 "name": tc["name"], "content": str(tool_output)})
        raise BrainError("工具循环达到次数上限，未生成最终回答")

    def _mock_response(self, text):
        """模拟响应（纯文本，不带情绪标签）"""
        if isinstance(text, list):
            text = " ".join(str(t) for t in text)
        if "hello" in text.lower():
            return "Hello! I am J.A.C., glad to serve you."
        elif "name" in text.lower():
            return "My name is J.A.C."
        else:
            return f"I heard you say: {text}"

if __name__ == "__main__":
    brain = LocalBrain(backend="auto")
    print("J.A.C: " + brain.think("hello, introduce yourself"))
