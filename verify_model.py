import os
import sys

def verify():
    print("--------------------------------------------------")
    print("      J.A.C 模型验证工具")
    print("--------------------------------------------------")
    
    # 1. 检查 llama-cpp-python
    try:
        from llama_cpp import Llama
        print("[OK] 依赖库 llama-cpp-python 已安装。")
    except ImportError:
        print("[FAIL] 未找到 llama-cpp-python！")
        print("       请运行: pip install llama-cpp-python")
        return

    # 2. 检查模型文件
    model_path = "models/qwen1_5-1_8b-chat-q4_k_m.gguf"
    if not os.path.exists(model_path):
        print(f"[FAIL] 未找到模型文件: {model_path}")
        print("       请参考 models/README.txt 下载模型。")
        return
    else:
        print(f"[OK] 发现模型文件: {model_path}")
        size_mb = os.path.getsize(model_path) / (1024 * 1024)
        print(f"     文件大小: {size_mb:.2f} MB")

    # 3. 尝试加载
    print("\n[INFO] 正在尝试加载模型 (这可能需要几秒钟)...")
    try:
        llm = Llama(model_path=model_path, verbose=False)
        print("[OK] 模型加载成功！")
        
        print("\n[INFO] 进行简单的对话测试...")
        output = llm.create_chat_completion(
            messages=[{"role": "user", "content": "你好，你是谁？"}],
            max_tokens=50
        )
        print(f"[J.A.C 回复] {output['choices'][0]['message']['content']}")
        print("\n--------------------------------------------------")
        print("恭喜！你的本地大脑已经准备就绪。")
        print("现在可以运行 python main.py 体验完整版 J.A.C 了。")
        print("--------------------------------------------------")
        
    except Exception as e:
        print(f"[FAIL] 模型加载或推理失败: {e}")

if __name__ == "__main__":
    verify()
