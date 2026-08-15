"""M2 回灌通道：用 omni 自己的克隆声纹（临时 turn_based 会话）把任意文本自然播报。

为什么用 turn_based 而非在 full_duplex 里注入音频（关键约束，来自 llama.cpp-omni 源码）：
  - ws_handler.cpp:1075 规定 full_duplex 的 input.append **严禁**带 messages，
    发送 messages 会直接 fatal；双工下只能用 audio_base64 / video_frames。
  - 若在 full_duplex 注入 audio_base64，omni 会把它当作「用户语音」重新 ASR + 应答，
    造成「双份播报 + 回声」。
  - turn_based 支持 messages 文本 + tts:{enabled:true}：omni 用 session.init 时传入的
    克隆声纹把文本流式 TTS 出来，是唯一干净、无回声的「让 omni 自然播报」通道
    （bo s s 之前的 omni_turnbased_test.py 已实测可用）。

该连接与主 full_duplex 会话相互独立（独立 WS），不污染主会话上下文；播报完成后即关闭。
主会话在升级期间通过 OmniClient._suppress_audio 静音，避免与主播报重叠。
"""
import asyncio
import base64
import json
import threading

import websockets

from .client import _PyAudioPlayer, TARGET_SR  # 复用内置低延迟播放器


async def _speak_turnbased(url: str, ref_audio_b64: str, text: str, play: bool = True):
    """在临时 turn_based 会话里让 omni 朗读 text（JAC 克隆声纹），流式播放。

    Args:
        url: omni WS 地址（与主 full_duplex 同一服务，不同连接）。
        ref_audio_b64: 16k float32 单声道 PCM 的 base64（克隆声纹参考音）。
        text: 要播报的中文文本。
        play: 是否真的播放（False 仅拉取，用于测试）。
    """
    player = _PyAudioPlayer(rate=TARGET_SR) if play else None
    try:
        async with websockets.connect(
            url, max_size=None, open_timeout=30,
            ping_interval=20, ping_timeout=20,
        ) as ws:
            # 1) turn_based 会话初始化（复用同一克隆声纹 JAC）
            await ws.send(json.dumps({
                "type": "session.init",
                "payload": {
                    "mode": "turn_based",
                    "use_tts": True,
                    "voice": {"ref_audio": ref_audio_b64},
                    "system_prompt": "你是 J.A.C.，本地 AI 管家，称呼用户为 boss，用简洁口语播报。",
                },
            }))
            await asyncio.wait_for(ws.recv(), timeout=180)  # session.created

            # 2) 把结果文本作为 user 消息发给 omni，开启 TTS 流式合成
            await ws.send(json.dumps({
                "type": "input.append",
                "input": {
                    "messages": [{"role": "user", "content": text}],
                    "streaming": True,
                    "tts": {"enabled": True},
                },
            }))

            # 3) 收音频增量并播放；response.done 可能携带尾部整段音频
            async for raw in ws:
                e = json.loads(raw)
                et = e.get("type")
                if et == "response.output.delta":
                    if e.get("kind") == "audio" and e.get("audio") and player is not None:
                        player.play(base64.b64decode(e["audio"]))
                elif et == "response.done":
                    da = e.get("audio")
                    if da and player is not None:
                        try:
                            player.play(base64.b64decode(da))
                        except Exception:  # noqa: BLE001
                            pass
                    break
                elif et == "session.closed":
                    break
    finally:
        if player is not None:
            # 等播放队列排空，避免提前 terminate 截断尾音
            try:
                await asyncio.sleep(0.6)
            except Exception:  # noqa: BLE001
                pass
            player.stop()


def speak_text_via_omni(url: str, ref_audio_b64: str, text: str):
    """同步封装：在独立线程 + 独立事件循环里跑 turn_based 播报（不阻塞调用方）。

    调用方（升级后台线程）应 join 等待播报结束，再解除主会话静音，
    确保「主会话静音 → 回灌播报 → 解除静音」的顺序。

    Args:
        url: omni WS 地址。
        ref_audio_b64: 克隆声纹 base64。
        text: 要播报的文本。
    """
    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_speak_turnbased(url, ref_audio_b64, text))
        finally:
            try:
                loop.close()
            except Exception:  # noqa: BLE001
                pass

    t = threading.Thread(target=_run, daemon=True, name="omni-backfeed")
    t.start()
    t.join()  # 等播报线程结束（回灌应等播完再解除主会话静音）
