#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OMNI WebSocket 协议离线单测。

覆盖 ``session.init`` 中采样配置的嵌套位置，防止 ``listen_prob_scale`` 被放到
消息顶层后被 llama.cpp-omni 静默忽略，并重新出现「检测到人声但一直 listen」问题。
"""
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 离线测试仅构造协议消息；缺少多媒体依赖时注入占位，避免拉起硬件或网络。
for _name in ("cv2", "numpy", "pyaudio", "soxr", "soundfile", "websockets", "requests"):
    try:
        __import__(_name)
    except Exception:
        sys.modules[_name] = MagicMock()

from src.omni.client import OmniClient


def test_session_init_puts_sampling_config_inside_payload():
    """服务端可从 ``payload.config`` 读取 Listen 采样参数。"""
    client = OmniClient(
        url="ws://127.0.0.1:9060/backend",
        ref_audio_path="voices/silverwalf_voice.wav",
        system_prompt="test prompt",
        listen_prob_scale=0.35,
        enable_mic=False,
        enable_camera=False,
        enable_playback=False,
        echo_gate=False,
    )

    init_msg = client._build_session_init("reference-audio-base64")

    assert init_msg["type"] == "session.init"
    assert init_msg["payload"]["config"] == {"listen_prob_scale": 0.35}
    assert "config" not in init_msg
