#!/usr/bin/env python3
"""验证 LM Studio 上的大脑模型是否支持 OpenAI 风格的 function calling（tools 参数）。

本脚本仅用于开发期验证，不进入主程序运行链路。
用法：python verify_toolcall.py
可选环境变量：
    LM_STUDIO_URL     LM Studio 的 chat/completions 地址，默认 http://127.0.0.1:12345/v1/chat/completions
    JAC_BRAIN_MODEL   模型标识符，默认 qwen/qwen3.6-35b-a3b（需与 LM Studio 中加载的 id 一致）
"""
import os
import sys
import json

import requests


def main():
    # 从环境变量读取地址与模型，未设置则回退项目约定值
    url = os.environ.get("LM_STUDIO_URL", "http://127.0.0.1:12345/v1/chat/completions")
    model = os.environ.get("JAC_BRAIN_MODEL", "qwen/qwen3.6-35b-a3b")

    # 定义一个最小工具：打开网址，用于探测模型是否会返回 tool_calls
    tools = [
        {
            "type": "function",
            "function": {
                "name": "open_url",
                "description": "在浏览器中打开一个网址",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "要打开的网址"}
                    },
                    "required": ["url"],
                },
            },
        }
    ]

    # 构造与 J.A.C. 主程序一致的请求体（非流式 + 关闭思考链，降延迟）
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "帮我打开百度首页"}],
        "tools": tools,
        "tool_choice": "auto",
        "temperature": 0.2,
        "max_tokens": 200,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }

    # 发起请求，连接失败给出可操作提示
    try:
        resp = requests.post(url, json=payload, timeout=30)
    except requests.ConnectionError:
        print("[连接失败] 请确认 LM Studio 已启动，并在 127.0.0.1:12345 加载了模型。")
        sys.exit(2)

    if resp.status_code != 200:
        print(f"[LM Studio 返回 {resp.status_code}] {resp.text[:300]}")
        sys.exit(2)

    # 解析返回，判断是否存在 tool_calls
    data = resp.json()
    msg = data["choices"][0]["message"]
    tool_calls = msg.get("tool_calls")

    if tool_calls:
        print("[支持] 模型返回了 tool_calls，Function Calling 可直接实现：")
        print(json.dumps(tool_calls, ensure_ascii=False, indent=2))
        print("\n结论：可以开工实现 Function Calling（扩展 brain.think 支持 tools 即可）。")
    else:
        print("[警告] 未返回 tool_calls，模型输出为纯文本：")
        print(msg.get("content"))
        print("\n结论：该模型/配置暂不支持 tool calling，需走提示词式伪 tool-call 降级方案。")


if __name__ == "__main__":
    main()
