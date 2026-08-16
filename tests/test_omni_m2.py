#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M2 离线单测：不依赖 omni 服务 / LM Studio，验证令牌解析与路由接线。

覆盖：
  1. parse_call_qwen 纯函数（命中 / 未命中 / 任务含中文 / 令牌后无换行）。
  2. OmniClient._on_text 流式令牌检测（令牌跨多个 delta 分片到达也能命中，且幂等）。
  3. EscalationRouter.escalate 接线（mock 大脑，验证 run_agentic 被正确调用 +
     流式文本被聚合返回）。
"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.omni.router import parse_call_qwen, EscalationRouter
from src.omni.client import OmniClient, OmniCallbacks


class _CaptureCb(OmniCallbacks):
    """捕获 on_call_qwen 的任务与触发次数。"""
    def __init__(self):
        super().__init__()
        self.tasks = []
        self.text_seen = []

    def on_text_delta(self, text):
        self.text_seen.append(text)

    def on_call_qwen(self, task):
        self.tasks.append(task)


def test_parse_call_qwen():
    # 命中 + 单行任务
    assert parse_call_qwen("<<CALL_QWEN>>帮 boss 打开 Safari") == "帮 boss 打开 Safari"
    # 令牌后带换行（取换行前）
    assert parse_call_qwen("前缀<<CALL_QWEN>>查天气\n后续") == "查天气"
    # 未命中
    assert parse_call_qwen("今天天气不错") is None
    # 空串
    assert parse_call_qwen("") is None
    # 令牌后无内容（任务尚未完整）→ 空串 "" 表示已命中但未齐
    assert parse_call_qwen("<<CALL_QWEN>>") == ""
    print("[OK] parse_call_qwen 各分支通过")


def test_client_streaming_token():
    cb = _CaptureCb()
    # 不启动服务，仅构造客户端测试 _on_text 逻辑
    client = OmniClient(
        url="ws://127.0.0.1:9060/backend",
        ref_audio_path="voices/silverwalf_voice.wav",
        system_prompt="test",
        callbacks=cb,
        enable_mic=False, enable_camera=False, enable_playback=False,
    )
    # 模拟「令牌前用户真实发言」：刷新最近人声时间，使升级护栏放行（符合真实链路）
    client._last_speech_ts = time.monotonic()
    # 模拟流式分片：令牌被拆成两段到达
    client._on_text("<<CALL")
    client._on_text("_QWEN>>帮")
    client._on_text("boss 打开")
    client._on_text("Safari\n然后说你好")  # 换行结束任务
    assert len(cb.tasks) == 1, f"应只触发一次，实际: {cb.tasks}"
    assert cb.tasks[0] == "帮boss 打开Safari", f"任务解析错误: {cb.tasks[0]}"
    # 触发后应幂等：后续文本不再触发
    client._on_text("<<CALL_QWEN>>不应再次触发")
    assert len(cb.tasks) == 1, f"不应重复触发，实际: {cb.tasks}"
    # 静音标志应已置位
    client._audio_lock.acquire()
    suppressed = client._suppress_audio
    client._audio_lock.release()
    assert suppressed is True, "命中令牌后主会话应进入静音"
    print("[OK] OmniClient 流式令牌检测通过（跨分片 + 幂等 + 静音）")


def test_router_wiring():
    # mock 大脑：run_agentic 是生成器，yield 两段最终回答
    class _FakeBrain:
        def __init__(self):
            self.calls = []

        def run_agentic(self, prompt, tools, tool_executor,
                        system_prompt="", temperature=0.7, max_tokens=512,
                        max_iterations=3):
            self.calls.append((prompt, tools, system_prompt))
            yield "boss，"
            yield "已帮你打开 Safari。"

    router = EscalationRouter()
    router.brain = _FakeBrain()
    progress = []
    result = router.escalate("帮 boss 打开 Safari", on_progress=progress.append)
    assert result == "boss，已帮你打开 Safari。", f"聚合结果错误: {result}"
    assert "".join(progress) == result, "on_progress 应收到完整流式文本"
    assert len(router.brain.calls) == 1
    prompt, tools, sys_p = router.brain.calls[0]
    assert "帮 boss 打开 Safari" in prompt
    assert tools is not None and len(tools) > 0, "应传入工具 schema"
    assert sys_p, "应传入大脑侧系统提示"
    print("[OK] EscalationRouter 接线通过（prompt/tools/流式聚合）")


if __name__ == "__main__":
    test_parse_call_qwen()
    test_client_streaming_token()
    test_router_wiring()
    print("\n===== M2 离线单测全部通过 =====")
