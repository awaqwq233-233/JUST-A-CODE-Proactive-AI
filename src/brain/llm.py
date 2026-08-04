try:
    from llama_cpp import Llama
except ImportError:
    Llama = None

import os
import sys
import re
import platform
import base64
import cv2
import requests
import json


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
        self.brain_model_name = "qwen/qwen3.5-9b"

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

    def _query_lm_studio(self, messages, temperature, max_tokens):
        # 只保证一个合理下限，尊重调用方传入值（原来强拉到 2048 会让每次生成都极慢）
        """查询lmstudio"""
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
            if self.active_model_id:
                payload["model"] = self.active_model_id
            resp = requests.post(
                self.lm_studio_url,
                json=payload,
                timeout=120,
                headers={"Content-Type": "application/json"}
            )
            if resp.status_code != 200:
                print(f"[Error] LM Studio API returned {resp.status_code}: {resp.text}")
                return "Sorry, brain connection has an issue."
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            if not content:
                reasoning = data.get("choices", [{}])[0].get("message", {}).get("reasoning_content", "")
                if reasoning:
                    print("[System] content 为空，尝试从 thinking 中恢复最终回答（避免把思考链当答案念出）")
                    # Qwen3 偶尔把 [情绪] 最终回答写在思考链末尾：
                    # 取最后一个情绪标签之后的文本作为最终回答。
                    tags = list(re.finditer(r"[\[【](.*?)[\]】]", reasoning))
                    recovered = reasoning[tags[-1].end():].strip() if tags else ""
                    if recovered:
                        return recovered
                    # 没有情绪标签则退一步取思考链最后一段非空内容
                    tail = [s for s in reasoning.split("\n\n") if s.strip()]
                    if tail:
                        return tail[-1].strip()
                print(f"[Debug] LM Studio returned empty content: {json.dumps(data, ensure_ascii=False)[:500]}")
                return "（刚才走神了，能再问一次吗？）"
            return content
        except requests.exceptions.ReadTimeout:
            print("[Error] 大脑推理超时：模型可能仍在加载，或设备资源不足导致推理过慢。"
                  "请确认模型已在 LM Studio 完全加载；Mac 上可检查内存压力，或调大 llm.py 的 timeout。")
            return "My brain is thinking too slowly. Please try again later."
        except requests.exceptions.ConnectionError:
            print("[Error] Cannot connect to LM Studio (127.0.0.1:12345)")
            return "Sorry, cannot connect to brain server."
        except Exception as e:
            print(f"[Error] LM Studio request failed: {e}")
            return "My brain is having trouble, please try again later."

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

    def _query_ollama(self, messages, temperature, max_tokens):
        """查询Ollama"""
        try:
            resp = requests.post(
                f"{self.ollama_base_url}/api/chat",
                json={
                    "model": self.ollama_model_name,
                    "messages": messages,
                    "stream": False,
                    "think": False,  # 禁用 Qwen3 思考链，直接输出结果
                    "options": {
                        "temperature": temperature,
                        "num_predict": max_tokens
                    }
                },
                timeout=120
            )
            if resp.status_code != 200:
                print(f"[Error] Ollama API returned {resp.status_code}: {resp.text}")
                return "Sorry, brain connection has an issue."
            data = resp.json()
            return data["message"]["content"]
        except requests.exceptions.ConnectionError:
            print("[Error] Cannot connect to Ollama service (127.0.0.1:11434)")
            return "Sorry, cannot connect to brain server."
        except Exception as e:
            print(f"[Error] Ollama request failed: {e}")
            return "My brain is having trouble, please try again later."

    def _query_llama_cpp(self, messages, temperature, max_tokens):
        """查询llamacpp"""
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

    def _mock_response(self, text):
        """模拟响应"""
        if isinstance(text, list):
            text = " ".join(str(t) for t in text)
        if "hello" in text.lower():
            return "[happy] Hello! I am J.A.C., glad to serve you."
        elif "name" in text.lower():
            return "[calm] My name is J.A.C."
        else:
            return f"[calm] I heard you say: {text}"

if __name__ == "__main__":
    brain = LocalBrain(backend="auto")
    print("J.A.C: " + brain.think("hello, introduce yourself"))
