try:
    from llama_cpp import Llama
except ImportError:
    Llama = None

import os
import sys
import re
import json
import platform
import base64
import cv2
import requests
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
    parsed, clean = [], []
    for idx, tc in enumerate(raw):
        fn = tc.get("function", {})
        name = fn.get("name")
        args_raw = fn.get("arguments", {})
        if isinstance(args_raw, str):
            try:
                args = json.loads(args_raw) if args_raw else {}
            except Exception:
                args = {}
            args_str = args_raw
        elif isinstance(args_raw, dict):
            args = args_raw
            args_str = json.dumps(args_raw, ensure_ascii=False)
        else:
            args, args_str = {}, "{}"
        parsed.append({"name": name, "arguments": args})
        clean.append({
            "id": tc.get("id") or f"call_{idx}",
            "type": tc.get("type", "function"),
            "function": {"name": name, "arguments": args_str},
        })
    return parsed, clean


class LocalBrain:
    """
    Local LLM Brain.
    Supports LM Studio / Ollama / llama.cpp backends.
    """

    def __init__(self, model_path="models/Qwen3.5-9B-Q4_K_M.gguf", backend="auto", lm_studio_model=None):
        """初始化实例"""
        self.llm = None
        self.multimodal = False
        self.backend = "mock"
        self.active_model_id = None
        self._explicit_lm_model = lm_studio_model
        # 大脑首选模型（LM Studio 中的实际模型 ID，大小写不敏感模糊匹配）；加载顺序变化时也能正确锁定
        self.brain_model_name = "qwen/qwen3.6-35b-a3b"

        self.lm_studio_url = "http://127.0.0.1:12345/v1/chat/completions"
        self.lm_studio_check_url = "http://127.0.0.1:12345/v1/models"

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
            elif Llama is not None:
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
        """在已加载模型 ID 中选定大脑模型：显式指定 > 模糊匹配首选名 > 第一个。"""
        if not models:
            return None
        ids = [m.get("id", "") for m in models if m.get("id")]
        if not ids:
            return None
        if self._explicit_lm_model and self._explicit_lm_model in ids:
            return self._explicit_lm_model
        if preferred:
            t = self._normalize(preferred)
            for mid in ids:
                n = self._normalize(mid)
                if n == t or t in n or n in t:
                    return mid
        return ids[0]

    def _check_lm_studio(self):
        """检查lmstudio"""
        try:
            r = requests.get(self.lm_studio_check_url, timeout=2)
            if r.status_code == 200:
                models = r.json().get("data", [])
                if models:
                    self.active_model_id = self._pick_lm_model(models, self._explicit_lm_model or self.brain_model_name)
                    print(f"[System] LM Studio loaded model: {self.active_model_id or 'unknown'}")
                return True
            return False
        except requests.exceptions.ConnectionError:
            return False
        except Exception:
            return False

    def _init_lm_studio(self):
        """初始化lmstudio"""
        print(f"[System] LM Studio backend ready")
        print(f"       API: {self.lm_studio_url}")
        try:
            r = requests.get(self.lm_studio_check_url, timeout=2)
            if r.status_code == 200:
                models = r.json().get("data", [])
                if models:
                    self.active_model_id = self._pick_lm_model(models, self._explicit_lm_model or self.brain_model_name)
                self.multimodal = True
                print(f"[System] Current LM Studio model: {self.active_model_id or 'unknown'}")
        except:
            pass

    def _check_ollama(self):
        """检查Ollama"""
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
        """初始化llamacpp"""
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

    def _query_lm_studio(self, messages, temperature, max_tokens, tools=None):
        # 只保证一个合理下限，尊重调用方传入值（原来强拉到 2048 会让每次生成都极慢）
        """查询 LM Studio（非流式）。tools 不为空时进入 function calling 模式，返回 ThinkResult。"""
        if max_tokens < 512:
            max_tokens = 512
        try:
            payload = {
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": False,
                # 禁用 Qwen3 思考链：避免模型先吐大段 thinking 占满 token，大幅降低延迟
                "chat_template_kwargs": {"enable_thinking": False}
            }
            if tools:
                payload["tools"] = tools
                payload["tool_choice"] = "auto"
            if self.active_model_id:
                payload["model"] = self.active_model_id
            resp = requests.post(
                self.lm_studio_url,
                json=payload,
                timeout=120,
                headers={"Content-Type": "application/json"}
            )
            # 个别 LM Studio 聊天模板不支持 enable_thinking 参数：移除后重试一次，
            # 退回"直接输出"模式（对齐 src/judgment/judge.py 的判断引擎兜底逻辑）
            if resp.status_code == 400 and "enable_thinking" in resp.text.lower() and "chat_template_kwargs" in payload:
                payload.pop("chat_template_kwargs", None)
                print("[System] 大脑模型/模板不支持 enable_thinking 参数，已移除 chat_template_kwargs 后重试（保持直接输出）")
                resp = requests.post(
                    self.lm_studio_url,
                    json=payload,
                    timeout=120,
                    headers={"Content-Type": "application/json"}
                )
            if resp.status_code != 200:
                print(f"[Error] LM Studio API returned {resp.status_code}: {resp.text}")
                return ThinkResult(content="Sorry, brain connection has an issue.") if tools else \
                    "Sorry, brain connection has an issue."
            data = resp.json()
            message = data["choices"][0]["message"]
            content = message.get("content") or ""
            # function calling 分支：模型要求调用工具，返回 ThinkResult 让上层执行
            if tools and message.get("tool_calls"):
                parsed, raw = parse_tool_calls(message)
                if parsed:
                    return ThinkResult(content=content, tool_calls=parsed, raw_tool_calls=raw, has_tools=True)
            # 普通纯文本分支
            if not content:
                reasoning = data.get("choices", [{}])[0].get("message", {}).get("reasoning_content", "")
                if reasoning:
                    print("[System] content 为空，尝试从 thinking 中恢复最终回答（避免把思考链当答案念出）")
                    # 模型把最终回答写在思考链末尾：取思考链最后一段非空内容作为回答，
                    # 避免取到开头的提示词回吐（如「【铁律】...」）或步骤分析，导致朗读出废话。
                    tail = [s for s in reasoning.split("\n\n") if s.strip()]
                    recovered = tail[-1].strip() if tail else ""
                    if recovered:
                        return ThinkResult(content=recovered) if tools else recovered
                print(f"[Debug] LM Studio returned empty content: {json.dumps(data, ensure_ascii=False)[:500]}")
                fallback = "（刚才走神了，能再问一次吗？）"
                return ThinkResult(content=fallback) if tools else fallback
            return ThinkResult(content=content) if tools else content
        except requests.exceptions.ReadTimeout:
            print("[Error] 大脑推理超时：模型可能仍在加载，或设备资源不足导致推理过慢。"
                  "请确认模型已在 LM Studio 完全加载；Mac 上可检查内存压力，或调大 llm.py 的 timeout。")
            return ThinkResult(content="My brain is thinking too slowly. Please try again later.") if tools else \
                "My brain is thinking too slowly. Please try again later."
        except requests.exceptions.ConnectionError:
            print("[Error] Cannot connect to LM Studio (127.0.0.1:12345)")
            return ThinkResult(content="Sorry, cannot connect to brain server.") if tools else \
                "Sorry, cannot connect to brain server."
        except Exception as e:
            print(f"[Error] LM Studio request failed: {e}")
            return ThinkResult(content="My brain is having trouble, please try again later.") if tools else \
                "My brain is having trouble, please try again later."

    def _query_lm_studio_stream(self, messages, temperature, max_tokens):
        """流式查询 LM Studio（SSE）。逐块 yield 文本片段，首个 token 即开始返回，降低感知延迟。"""
        if max_tokens < 512:
            max_tokens = 512
        try:
            payload = {
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": True,
                # 禁用 Qwen3 思考链，避免先吐大段 thinking 占满 token
                "chat_template_kwargs": {"enable_thinking": False},
            }
            if self.active_model_id:
                payload["model"] = self.active_model_id
            resp = requests.post(
                self.lm_studio_url,
                json=payload,
                stream=True,
                timeout=120,
                headers={"Content-Type": "application/json"},
            )
            # 个别 LM Studio 聊天模板不支持 enable_thinking 参数：移除后重试一次（对齐 judge.py 兜底）
            if resp.status_code == 400 and "enable_thinking" in resp.text.lower() and "chat_template_kwargs" in payload:
                payload.pop("chat_template_kwargs", None)
                print("[System] 大脑模型/模板不支持 enable_thinking 参数，已移除 chat_template_kwargs 后重试（保持直接输出）")
                resp = requests.post(
                    self.lm_studio_url,
                    json=payload,
                    stream=True,
                    timeout=120,
                    headers={"Content-Type": "application/json"},
                )
            if resp.status_code != 200:
                err = resp.text[:300]
                print(f"[Error] LM Studio streaming returned {resp.status_code}: {err}")
                yield "Sorry, brain connection has an issue."
                return
            # 累计已产出的文本：流结束若全程为空（LM Studio 并发/繁忙偶发返回空 choice），
            # 补一句兜底，避免调用方拿到空串并跳过回复。
            _accumulated = []
            for line in resp.iter_lines():
                if not line:
                    continue
                text = line.decode("utf-8")
                if not text.startswith("data:"):
                    continue
                data = text[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                    delta = obj["choices"][0]["delta"].get("content", "")
                    if delta:
                        _accumulated.append(delta)
                        yield delta
                except Exception:
                    continue
            if not "".join(_accumulated).strip():
                print("[System] 流式返回为空（LM Studio 可能繁忙/并发），补一句兜底回复。")
                yield "（刚才走神了，能再问一次吗？）"
        except requests.exceptions.ReadTimeout:
            yield "My brain is thinking too slowly. Please try again later."
        except requests.exceptions.ConnectionError:
            yield "Sorry, cannot connect to brain server."
        except Exception as e:
            yield f"My brain is having trouble: {e}"

    def _query_ollama(self, messages, temperature, max_tokens, tools=None):
        """查询 Ollama。支持可选 tools 参数做 function calling。"""
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

    def think_with_tools(self, messages, tools, temperature=0.7, max_tokens=1024):
        """带工具调用的推理（非流式）：返回 ThinkResult（可能含 tool_calls）。"""
        if self.backend == "mock":
            text = messages[-1].get("content", "") if messages else ""
            return ThinkResult(content=self._mock_response(text))
        if self.backend == "lm_studio":
            return self._query_lm_studio(messages, temperature, max_tokens, tools=tools)
        elif self.backend == "ollama":
            return self._query_ollama(messages, temperature, max_tokens, tools=tools)
        else:
            # llama_cpp 暂不支持结构化 tool_calls，直接当普通文本返回
            content = self._query_llama_cpp(messages, temperature, max_tokens)
            return ThinkResult(content=content)

    def run_agentic(self, prompt, tools, tool_executor,
                    system_prompt="You are J.A.C., a helpful AI assistant.",
                    temperature=0.7, max_tokens=512, max_iterations=3):
        """工具调用循环（agent 执行）。生成器，流式 yield 最终回答文本（打字机效果）。

        流程：用户问题 -> 模型决定是否调工具 -> 执行并把结果回喂 -> 重复，
        直到模型给出最终自然语言回答；全程只把最后一轮回答流式吐出。
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]
        for _ in range(max_iterations):
            result = self.think_with_tools(messages, tools, temperature, max_tokens)
            if not result.tool_calls:
                # 没有更多工具调用：把当前消息（含工具结果）交给模型，流式输出最终回答
                yield from self._stream_final(messages, temperature, max_tokens)
                return
            # 把 assistant 的工具调用消息追加进上下文（原样回传格式，供下一轮携带工具结果）
            messages.append({
                "role": "assistant",
                "content": result.content or "",
                "tool_calls": result.raw_tool_calls,
            })
            for tc, raw in zip(result.tool_calls, result.raw_tool_calls):
                print(f"[工具] 调用 {tc['name']}({json.dumps(tc['arguments'], ensure_ascii=False)})")
                tool_output = tool_executor(tc["name"], tc["arguments"])
                print(f"[工具] 结果: {str(tool_output)[:200]}")
                messages.append({
                    "role": "tool",
                    "tool_call_id": raw.get("id"),
                    "name": tc["name"],
                    "content": str(tool_output),
                })
        # 超出迭代上限：再请求一次要尽量给出总结
        yield from self._stream_final(messages, temperature, max_tokens)

    def _stream_final(self, messages, temperature, max_tokens):
        """工具循环结束后，用带工具结果的消息再请求一次，流式输出最终自然语言回答。"""
        if self.backend == "lm_studio":
            # 复用流式查询；payload 不带 tools，避免模型又想调工具
            yield from self._query_lm_studio_stream(messages, temperature, max_tokens)
        else:
            r = self.think_with_tools(messages, None, temperature, max_tokens)
            yield r.content

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
