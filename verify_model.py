import requests
import json

def verify():
    print("--------------------------------------------------")
    print("     J.A.C 后端验证工具")
    print("--------------------------------------------------")
    print()

    # 1. 检查 LM Studio
    print("[1] 检查 LM Studio API...")
    try:
        r = requests.get("http://localhost:12345/v1/models", timeout=3)
        if r.status_code == 200:
            models = r.json().get("data", [])
            if models:
                print(f"    [OK] LM Studio 运行中，已加载模型: {models[0].get('id', 'unknown')}")
            else:
                print("    [OK] LM Studio 运行中 (未加载模型)")
            print()
            
            # 2. 简单对话测试
            print("[2] 简单对话测试...")
            resp = requests.post(
                "http://localhost:12345/v1/chat/completions",
                json={
                    "messages": [{"role": "user", "content": "用一句话介绍你自己"}],
                    "max_tokens": 100,
                    "temperature": 0.7,
                    "stream": False
                },
                timeout=30,
                headers={"Content-Type": "application/json"}
            )
            if resp.status_code == 200:
                reply = resp.json()["choices"][0]["message"]["content"]
                print(f"    [OK] 模型回复: {reply[:80]}...")
                print()
                print("--------------------------------------------------")
                print("恭喜！LM Studio 后端已就绪。")
                print("现在可以运行 python main.py 体验 J.A.C. 了。")
                print("--------------------------------------------------")
            else:
                print(f"    [FAIL] API 返回 {resp.status_code}")
        else:
            print(f"    [FAIL] LM Studio API 返回 {r.status_code}")
            print("    请确保 LM Studio 已启动并启用 API 服务器")
    except requests.exceptions.ConnectionError:
        print("    [FAIL] 无法连接到 LM Studio (127.0.0.1:12345)")
        print("    请确保 LM Studio 已启动并启用了 API 服务器")
        print()
        
        # 3. 回退检查 Ollama
        print("[!] 检查 Ollama...")
        try:
            r2 = requests.get("http://localhost:11434/api/tags", timeout=2)
            if r2.status_code == 200:
                print("    [OK] Ollama 运行中")
                print("    代码已配置为自动检测后端，请直接运行 python main.py")
            else:
                print("    [FAIL] 无可用后端")
        except:
            print("    [FAIL] 无可用后端")
            print()
            print("--------------------------------------------------")
            print("请启动 LM Studio 或 Ollama 后再试。")
            print("--------------------------------------------------")

if __name__ == "__main__":
    verify()
