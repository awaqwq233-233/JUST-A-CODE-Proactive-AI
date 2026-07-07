try:
    from llama_cpp import Llama
except ImportError:
    Llama = None

import os
import sys
import platform

class LocalBrain:
    """
    本地大脑 (Local LLM)
    使用 llama.cpp 运行量化版大模型。
    """
    def __init__(self, model_path="models/qwen1_5-1_8b-chat-q4_k_m.gguf"):
        """
        初始化大脑
        
        Args:
            model_path (str): GGUF 模型文件的路径
        """
        self.llm = None
        
        if Llama is None:
            print("[警告] 未安装 llama-cpp-python，大脑无法工作。")
            return

        if not os.path.exists(model_path):
            print(f"[警告] 未找到模型文件: {model_path}")
            print("[提示] 请手动下载 GGUF 模型并放入 models 文件夹。")
            print("       当前将使用【模拟模式】进行回复。")
            return

        print(f"[系统] 正在加载大脑模型: {model_path} ...")
        
        # 兼容性参数配置 - 提高加载成功率
        llama_args = {
            "model_path": model_path,
            "n_ctx": 2048,
            "n_threads": 4,
            "verbose": False,
        }
        
        # Windows 上可能需要禁用一些高级优化
        if platform.system() == "Windows":
            llama_args["n_batch"] = 512
            # 尝试不使用 AVX512 等高级指令集
            # 注意：llama-cpp-python 0.3.x 可能不支持某些旧参数
        
        try:
            self.llm = Llama(**llama_args)
            print("[系统] 大脑加载成功！")
        except Exception as e:
            print(f"[错误] 大脑加载失败: {e}")
            print("[提示] 可能是 CPU 不支持当前 llama-cpp-python 的指令集优化。")
            print("       请尝试重新安装兼容的 llama-cpp-python 版本：")
            print("       pip uninstall llama-cpp-python")
            print("       pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu")

    def think(self, prompt, system_prompt="You are J.A.C., a helpful AI assistant. J.A.C. stands for Just A Code.", temperature=0.7, max_tokens=120):
        """
        思考并回答
        
        Args:
            prompt (str): 用户的输入
            system_prompt (str): 系统设定
            temperature (float): 采样温度，控制创造性与多样性
            max_tokens (int): 最大生成长度
            
        Returns:
            response (str): AI 的回复
        """
        if self.llm is None:
            # 模拟模式 (当模型文件不存在时)
            return self._mock_response(prompt)

        # 构建对话格式 (这里以 ChatML 格式为例，Qwen 通用)
        # 不同的模型可能需要不同的 prompt template
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]
        
        try:
            output = self.llm.create_chat_completion(
                messages=messages,
                max_tokens=max_tokens,  # 限制回复长度，保证响应速度
                temperature=temperature  # 创造性
            )
            return output['choices'][0]['message']['content']
        except Exception as e:
            print(f"[错误] 思考出错: {e}")
            return "我的大脑有点混乱，请稍后再试。"

    def _mock_response(self, text):
        """
        模拟回复 (仅用于测试)
        """
        if "你好" in text:
            return "你好！我是 J.A.C，很高兴为你服务。"
        elif "看到" in text:
            return "我正在观察周围的环境。"
        elif "名字" in text:
            return "我的名字是 J.A.C。"
        else:
            return f"我听到了你说：{text}，但我还没装载真正的大脑模型。"

if __name__ == "__main__":
    # 测试代码
    brain = LocalBrain()
    print("J.A.C: " + brain.think("你好，介绍一下你自己"))
