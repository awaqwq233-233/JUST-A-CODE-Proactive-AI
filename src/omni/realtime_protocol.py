"""固定版本 Gateway Realtime 的 PCM、媒体与生命周期编码。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, urlencode

import numpy as np
import soundfile as sf
import soxr

INPUT_RATE = 16000
OUTPUT_RATE = 24000
SAMPLES_PER_CHUNK = 16000


def load_audio(path: Path) -> np.ndarray:
    """读取音频并转换成连续、有限值的 16k mono float32 波形。"""
    samples, rate = sf.read(path, dtype="float32", always_2d=True)
    mono = samples.mean(axis=1)
    if rate != INPUT_RATE:
        mono = soxr.resample(mono, rate, INPUT_RATE)
    if not len(mono) or not np.isfinite(mono).all():
        raise ValueError("输入音频为空或含非有限值")
    return np.asarray(mono, dtype="<f4")


def encode_pcm(samples: np.ndarray) -> str:
    """把 mono float32 波形编码成协议要求的 little-endian Base64。"""
    samples = np.asarray(samples, dtype="<f4")
    if samples.ndim != 1 or not np.isfinite(samples).all():
        raise ValueError("PCM 必须是有限值的一维波形")
    return base64.b64encode(samples.tobytes()).decode("ascii")


def decode_pcm(value: str) -> np.ndarray:
    """校验下行 Base64，并返回 24k 播放流使用的 float32 样本。"""
    raw = base64.b64decode(value, validate=True)
    if not raw or len(raw) % 4:
        raise ValueError("下行音频字节数不是非空 float32 数组")
    samples = np.frombuffer(raw, dtype="<f4")
    if not np.isfinite(samples).all():
        raise ValueError("下行音频含非有限值")
    return samples


def build_init(prompt: str, voice: np.ndarray | None) -> dict:
    """生成固定 C++ 后端的 init，保留参考音频后的 system 模板边界。"""
    # 锁定的 ws_handler.cpp 将 system_prompt 覆盖到 omni_assistant_prompt（后缀），
    # 而非完整 system 内容。普通文本会被上游加上 user 前缀，且丢失 audio_end。
    # 显式补齐后缀，使参考音和助手指令留在同一 system 消息内；不改变后端源码。
    suffix = f"<|audio_end|>\n{prompt}\n<|im_end|>\n"
    payload = {"system_prompt": suffix, "config": {"length_penalty": 1.1}}
    if voice is not None:
        reference = encode_pcm(voice)
        payload["voice"] = {
            "ref_audio_base64": reference,
            "tts_ref_audio_base64": reference,
        }
    return {"type": "session.init", "payload": payload}


def require_voice_condition(created: dict, init: dict) -> None:
    """请求克隆时须取得对应 PCM 哈希的 native 条件确认，否则拒绝启动设备。"""
    voice = init.get("payload", {}).get("voice", {})
    reference = voice.get("tts_ref_audio_base64") or voice.get("ref_audio_base64")
    if not reference:
        return
    expected = hashlib.sha256(base64.b64decode(reference, validate=True)).hexdigest()
    condition = created.get("voice_conditioning") or {}
    if (not isinstance(condition, dict) or condition.get("applied") is not True
            or condition.get("reference_sha256") != expected):
        raise ValueError("后端未确认应用指定音色，请使用已更新并重新编译的固定后端")


def build_input(samples: np.ndarray, jpeg: bytes | None = None) -> dict:
    """组装恰好一秒音频和可选的一帧 JPEG，不包含旧协议字段。"""
    if len(samples) != SAMPLES_PER_CHUNK:
        raise ValueError("每块必须恰好有 16000 个样本")
    body = {"audio": encode_pcm(samples)}
    if jpeg is not None:
        if not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9"):
            raise ValueError("视频帧必须是完整 JPEG")
        body.update(video_frames=[base64.b64encode(jpeg).decode("ascii")], max_slice_nums=1)
    return {"type": "input.append", "input": body}


def input_chunks(samples: np.ndarray | None, count: int) -> list[np.ndarray]:
    """将样本顺序分块并补尾部静音，绝不重复或丢弃输入语音。"""
    samples = np.zeros(0, dtype="<f4") if samples is None else samples
    result = []
    for index in range(count):
        chunk = np.zeros(SAMPLES_PER_CHUNK, dtype="<f4")
        source = samples[index * SAMPLES_PER_CHUNK:(index + 1) * SAMPLES_PER_CHUNK]
        chunk[:len(source)] = source
        result.append(chunk)
    return result


def gateway_url(url: str, mode: str) -> str:
    """限制客户端连接本机 Gateway，防止将参考音频意外发送给云服务。"""
    parts = urlsplit(url)
    if parts.scheme not in {"ws", "wss"} or parts.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("客户端只允许连接本机 ws/wss Gateway")
    if parts.username or parts.password:
        raise ValueError("URL 中不得包含凭据")
    return urlunsplit((parts.scheme, parts.netloc, "/v1/realtime", urlencode({"mode": mode}), ""))


def check_event(message: str | bytes) -> dict:
    """解析服务端事件并立即报告结构化协议错误。"""
    event = json.loads(message)
    if not isinstance(event, dict) or not isinstance(event.get("type"), str):
        raise ValueError("服务端事件必须是包含 type 的 JSON 对象")
    if event["type"] == "error":
        error = event.get("error") or {}
        code = error.get("code", "unknown") if isinstance(error, dict) else "unknown"
        if (not isinstance(code, str) or len(code) > 64 or not code.isascii()
                or not code.replace("_", "").replace("-", "").isalnum()):
            code = "unknown"
        raise RuntimeError(f"服务端错误: {code}")
    return event


async def receive_until(ws, expected: str, timeout: float, events: Counter) -> dict:
    """在总时限内等待指定生命周期事件，拒绝提前关闭的会话。"""
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"等待 {expected} 超时")
        event = check_event(await asyncio.wait_for(ws.recv(), remaining))
        events[event["type"]] += 1
        if event["type"] == expected:
            return event
        if event["type"] == "session.closed":
            raise RuntimeError(f"会话提前关闭: {event.get('reason', 'unknown')}")
