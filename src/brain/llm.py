try:
    from llama_cpp import Llama
except ImportError:
    Llama = None

import os
import sys
import platform
import base64
import cv2
import requests
import json

class LocalBrain:
    """
    本地大脑 (Local LLM)
    支持 LM Studio / Ollama / llama.cpp 三种后端。
    """
    def __init__(self, model_path="models/Qwen3.5-9B-Q4_K_M.gguf", backend="auto"):
        """
        初始化大脑

        Args:
            model_path (str): GGUF 模型文件的路径 (仅 llama_cpp 后端使用)
            backend (str): 推理后端,可选 "auto" / "lm_studio" / "ollama" / "llama_cpp"
                          - auto: 优先检测 LM Studio -> Ollama -> llama_cpp
                          - lm_studio: 强制使用 LM Studio API
                          - ollama: 强制使用 Ollama API
                          - llama_cpp: 强制使用本地 llama.cpp
        """
        self.llm = None
        self.multimodal = False
        self.backend = "mock"
        
        # LM Studio 连接参数 (OpenAI 兼容 API)
        self.lm_studio_url = "http://127.0.0.1:12345/v1/chat/completions"
        self.lm_studio_check_url = "http://127.0.0.1:12345/v1/models"
        
        # Ollama 连接参数
        self.ollama_base_url = "http://localhost:11434"
        self.ollama_model_name = "qwen2.5:7b"

        # 决定后端
        if backend == "lm_studio":
            self.backend = "lm_studio"
        elif backend == "ollama":
            self.backend = "ollama"
        elif backend == "llama_cpp":
            self.backend = "llama_cpp"
        elif backend == "auto":
            if self._check_lm_studio():
                print("[系统] 检测到 LM Studio，使用 LM Studio 后端 (GPU 加速)")
                self.backend = "lm_studio"
            elif self._check_ollama():
                print("[系统] 检测到 Ollama，使用 Ollama 后端 (GPU 加速)")
                self.backend = "ollama"
            elif Llama is not None:
                print("[系统] 未检测到 API 服务，切换到 llama.cpp 后端 (CPU)")
                self.backend = "llama_cpp"
            else:
                print("[系统] 无可用后端，使用模拟模式。")
                return

        if self.backend == "lm_studio":
            self._init_lm_studio()
        elif self.backend == "ollama":
            self._init_ollama()
        elif self.backend == "llama_cpp":
            self._init_llama_cpp(model_path)

    def _check_lm_studio(self):
        """检查 LM Studio API 是否在运行"""
        try:
            r = requests.get(self.lm_studio_check_url, timeout=2)
            if r.status_code == 200:
                models = r.json().get("data", [])
                if models:
                    print(f"[系统] LM Studio 已加载模型: {models[0].get('id', 'unknown')}")
                return True
            return False
        except requests.exceptions.ConnectionError:
            return False
        except Exception:
            return False

    def _init_lm_studio(self):
        """初始化 LM Studio 后端"""
        print(f"[系统] LM Studio 后端已就绪")
        print(f"       API: {self.lm_studio_url}")
        # LM Studio 如果加载了 VL 模型则支持多模态
        try:
            r = requests.get(self.lm_studio_check_url, timeout=2)
            if r.status_code == 200:
                models = r.json().get("data", [])
                for m in models:
                    mid = m.get("id", "").lower()
                    if "vl" in mid or "vision" in mid or "llava" in mid:
                        self.multimodal = True
                        print("[系统] 检测到视觉模型，多模态已启用！")
        except:
            pass

    def _check_ollama(self):
        """检查 Ollama 服务是否在运行"""
        try:
            r = requests.get(f"{self.ollama_base_url}/api/tags", timeout=2)
            return r.status_code == 200
        except requests.exceptions.ConnectionError:
            return False
        except Exception:
            return False

    def _init_ollama(self):
        print(f"[系统] Ollama 后端已就绪，模型: {self.ollama_model_name}")

    def _init_llama_cpp(self, model_path):
        """初始化 llama.cpp 后端"""
        if Llama is None:
            print("[警告] 未安装 llama-cpp-python。")
            return

        if not os.path.exists(model_path):
            print(f"[警告] 未找到模型文件: {model_path}")
            return

        print(f"[系统] 正在加载大脑模型: {model_path} ...")

        llama_args = {
            "model_path": model_path,
            "n_ctx": 2048,
            "n_threads": 4,
            "verbose": False,
        }

        mmproj_path = self._find_mmproj(model_path)
        if mmproj_path:
            print(f"[系统] 检测到多模态投影文件: {mmproj_path}")
            llama_args["mmproj"] = mmproj_path

        if platform.system() == "Windows":
            llama_args["n_batch"] = 512

        try:
            self.llm = Llama(**llama_args)
            if mmproj_path:
                self.multimodal = True
                print("[系统] 多模态视觉模式已启用！")
            print("[系统] 大脑加载成功！")
        except Exception as e:
            print(f"[错误] 大脑加载失败: {e}")

    def _find_mmproj(self, model_path):
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

    def think(self, prompt, system_prompt="You are J.A.C., a helpful AI assistant. J.A.C. stands for Just A Code.", temperature=0.7, max_tokens=120):
        """思考并回答 (纯文本)"""
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

    def think_with_image(self, prompt, frame, system_prompt="You are J.A.C., a helpful AI assistant.", temperature=0.7, max_tokens=200):
        """思考并回答 (接收图像帧)"""
        if self.backend == "mock":
            return self._mock_response(prompt)

        if not self.multimodal:
            print("[系统] 多模态不可用，降级为纯文本模式。")
            return self.think(prompt, system_prompt, temperature, max_tokens)

        try:
            ret, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if not ret:
                return self.think(prompt, system_prompt, temperature, max_tokens)
            img_b64 = base64.b64encode(buffer).decode("utf-8")
        except Exception as e:
            print(f"[警告] 图像处理失败: {e}")
            return self.think(prompt, system_prompt, temperature, max_tokens)

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                    {"type": "text", "text": prompt}
                ]
            }
        ]

        if self.backend == "lm_studio":
            return self._query_lm_studio(messages, temperature, max_tokens)
        elif self.backend == "ollama":
            # Ollama 多模态用独立格式
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
        """通过 LM Studio (OpenAI 兼容 API) 发送请求"""
        # Qwen3.5 是推理模型，需要足够 token 给思考+回答
        if max_tokens < 2048:
            max_tokens = 2048
        try:
            resp = requests.post(
                self.lm_studio_url,
                json={
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "stream": False
                },
                timeout=120,
                headers={"Content-Type": "application/json"}
            )
            if resp.status_code != 200:
                print(f"[错误] LM Studio API 返回 {resp.status_code}: {resp.text}")
                return "抱歉，大脑连接出了点问题。"
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            if not content:
                reasoning = data.get("choices", [{}])[0].get("message", {}).get("reasoning_content", "")
                if reasoning:
                    print("[系统] content 为空，使用 reasoning_content 作为回答")
                    parts = reasoning.rsplit('\n\n', 1)
                    return parts[-1].strip() if len(parts) > 1 else reasoning
                print(f"[调试] LM Studio 返回了空内容: {json.dumps(data, ensure_ascii=False)[:500]}")
            return content
        except requests.exceptions.ConnectionError:
            print("[错误] 无法连接到 LM Studio (127.0.0.1:12345)")
            print("       请确保 LM Studio 已启动并启用了 API 服务器")
            return "抱歉，无法连接到大脑服务器。"
        except Exception as e:
            print(f"[错误] LM Studio 请求失败: {e}")
            return "我的大脑有点混乱，请稍后再试。"
    def _query_ollama(self, messages, temperature, max_tokens):
        """通过 Ollama API 发送请求"""
        try:
            resp = requests.post(
                f"{self.ollama_base_url}/api/chat",
                json={
                    "model": self.ollama_model_name,
                    "messages": messages,
                    "stream": False,
                    "options": {
                        "temperature": temperature,
                        "num_predict": max_tokens
                    }
                },
                timeout=120
            )
            if resp.status_code != 200:
                print(f"[错误] Ollama API 返回 {resp.status_code}: {resp.text}")
                return "抱歉，大脑连接出了点问题。"
            data = resp.json()
            return data["message"]["content"]
        except requests.exceptions.ConnectionError:
            print("[错误] 无法连接到 Ollama 服务 (127.0.0.1:11434)")
            return "抱歉，无法连接到大脑服务器。"
        except Exception as e:
            print(f"[错误] Ollama 请求失败: {e}")
            return "我的大脑有点混乱，请稍后再试。"

    def _query_llama_cpp(self, messages, temperature, max_tokens):
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
            print(f"[错误] 思考出错: {e}")
            return "我的大脑有点混乱，请稍后再试。"

    def _mock_response(self, text):
        if isinstance(text, list):
            text = " ".join(str(t) for t in text)
        if "你好" in text:
            return "你好！我是 J.A.C，很高兴为你服务。"
        elif "看到" in text:
            return "我正在观察周围的环境。"
        elif "名字" in text:
            return "我的名字是 J.A.C。"
        else:
            return f"我听到了你说：{text}，但我还没装载真正的大脑模型。"

if __name__ == "__main__":
    brain = LocalBrain(backend="auto")
    print("J.A.C: " + brain.think("你好，介绍一下你自己"))
