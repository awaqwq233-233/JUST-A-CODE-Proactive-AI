"""P0 变量分离实验的两个开关验证：图像上行总开关（video_enabled）与调试日志开关（debug）。

背景（2026-09-17 诊断）：真机日志显示每带一帧图就写约 64 个视觉 token 进 KV，n_ctx=8192
时上下文每约 30 秒被滑动清空一次，清完模型只剩 system prompt，于是照 `prompts.py` 里的
示例复读「查一下这台电脑的电池电量百分比」。要坐实这条机制，必须能「一键把所有图像上行
关掉」，然后对比幻觉是否消失。

本测试覆盖三个层次：
1. 配置层：`Config.load()` 的默认值与 `OMNI_VIDEO_ENABLED` / `OMNI_DEBUG` 环境变量覆盖；
2. 单元层：`OmniClient.video_enabled` / `_debug` 的参数解析与 `_should_attach_frame()` 行为；
3. WS 集成层：关掉开关后，真实上行报文里**一个 `video_frames` 都不许出现**（用假服务端抓包）。
"""
import asyncio
import json
import os
import sys
import threading
import time

import numpy as np
import websockets

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.omni.client import OmniClient
from src.utils.config import Config

_FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"jac-fake-frame" * 8 + b"\xff\xd9"
_REF_AUDIO = "voices/silverwalf_voice.wav"


def _make_client(**kwargs):
    """构造一个不连服务、不采集、不播放的 OmniClient（仅用于解析开关与单元行为）。"""
    base = dict(
        url="ws://127.0.0.1:1/backend",         # 故意指向不可用端口：这些用例不 start()
        ref_audio_path=_REF_AUDIO,
        system_prompt="x",
        enable_mic=False, enable_camera=False, enable_playback=False,
    )
    base.update(kwargs)
    return OmniClient(**base)


# --------------------------------------------------------------- 1) 配置层
def test_config_defaults_and_env_override(monkeypatch):
    """默认：图像上行开、调试日志关；环境变量可各自覆盖。"""
    monkeypatch.delenv("OMNI_VIDEO_ENABLED", raising=False)
    monkeypatch.delenv("OMNI_DEBUG", raising=False)
    cfg = Config.load()
    assert cfg.omni_video_enabled is True, "图像上行默认必须是开的（不然默认就看不见 boss）"
    assert cfg.omni_debug_log is False, "调试日志默认必须是关的（会刷屏）"

    monkeypatch.setenv("OMNI_VIDEO_ENABLED", "0")
    monkeypatch.setenv("OMNI_DEBUG", "1")
    cfg2 = Config.load()
    assert cfg2.omni_video_enabled is False
    assert cfg2.omni_debug_log is True

    # 常见真值写法都要认（truthy() 的语义）
    monkeypatch.setenv("OMNI_VIDEO_ENABLED", "false")
    monkeypatch.setenv("OMNI_DEBUG", "off")
    cfg3 = Config.load()
    assert cfg3.omni_video_enabled is False
    assert cfg3.omni_debug_log is False
    print("PASS: Config 默认值与 OMNI_VIDEO_ENABLED / OMNI_DEBUG 覆盖均正确")


# --------------------------------------------------------------- 2) 单元层
def test_video_enabled_resolution(monkeypatch):
    """显式传参优先；未传时读 OMNI_VIDEO_ENABLED（缺省=开）。"""
    monkeypatch.delenv("OMNI_VIDEO_ENABLED", raising=False)
    assert _make_client().video_enabled is True
    assert _make_client(video_enabled=False).video_enabled is False
    assert _make_client(video_enabled=True).video_enabled is True

    monkeypatch.setenv("OMNI_VIDEO_ENABLED", "0")
    assert _make_client().video_enabled is False, "环境变量关闭未生效"
    # 显式传 True 必须压过环境变量（CLI/GUI 语义：显式指定优先）
    assert _make_client(video_enabled=True).video_enabled is True
    print("PASS: video_enabled 解析（显式 > 环境变量 > 默认开）正确")


def test_debug_flag_resolution(monkeypatch):
    """显式传参优先；未传时读 OMNI_DEBUG（缺省=关）。"""
    monkeypatch.delenv("OMNI_DEBUG", raising=False)
    assert _make_client(debug=True)._debug is True
    assert _make_client(debug=False)._debug is False
    assert _make_client()._debug is False

    for truthy_val in ("1", "true", "on", "yes", "TRUE"):
        monkeypatch.setenv("OMNI_DEBUG", truthy_val)
        assert _make_client()._debug is True, f"OMNI_DEBUG={truthy_val} 应判为开"
    for falsy_val in ("0", "false", "no", "off", ""):
        monkeypatch.setenv("OMNI_DEBUG", falsy_val)
        assert _make_client()._debug is False, f"OMNI_DEBUG={falsy_val!r} 应判为关"
    print("PASS: debug 解析（显式 > 环境变量 > 默认关）正确")


def test_should_attach_frame_switch_off_is_always_false():
    """总开关关闭时：不论时间怎么推进、间隔怎么设，都绝不上图。"""
    off = _make_client(video_enabled=False, video_interval=1.0)
    assert [off._should_attach_frame(1000.0 + i * 5.0) for i in range(10)] == [False] * 10

    # 间隔设成 0（旧行为=每段都带图）也必须被总开关压住——总开关优先级最高
    off0 = _make_client(video_enabled=False, video_interval=0.0)
    assert [off0._should_attach_frame(1000.0 + i * 5.0) for i in range(5)] == [False] * 5

    # 对照组：开着时首段必带图（证明开关不是「恒 False」的实现假象）
    on = _make_client(video_enabled=True, video_interval=1.0)
    assert on._should_attach_frame(1000.0) is True, "首段应带图"
    assert on._should_attach_frame(1000.2) is False, "距上帧不足 1.0s，不该再带图"
    print("PASS: 总开关关闭时 _should_attach_frame 恒 False（且压过 video_interval<=0）")


# --------------------------------------------------------------- 3) WS 集成层
class _FakeBackend:
    """假 omni 服务端：握手 → 记录每段 input.append 是否带图 → 回终局事件。"""

    def __init__(self, lag: float = 0.15):
        self.arrivals = []              # (收到时刻, 音频秒数, 是否带图)
        self._lag = lag
        self._loop = None
        self._server = None
        self._stop = threading.Event()
        self._thread = None
        self.port = None

    def start(self):
        """在后台线程起 WS 服务并返回监听端口。"""
        ready = threading.Event()

        def _run():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

            async def _handler(ws):
                seq = 0
                try:
                    await ws.recv()                       # session.init
                    await ws.send(json.dumps({
                        "type": "session.created", "session_id": "fake-session",
                    }))
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except Exception:  # noqa: BLE001
                            continue
                        if msg.get("type") != "input.append":
                            continue
                        inp = msg.get("input", {})
                        audio_secs = len(inp.get("audio_base64", "")) * 3 / 4 / 4 / 16000
                        self.arrivals.append(
                            (time.monotonic(), audio_secs, bool(inp.get("video_frames"))))
                        seq += 1
                        rid = f"fake-session_resp_{seq}"
                        await asyncio.sleep(self._lag)
                        # 一段两个终局事件、同一个 response_id（与真服务端一致）
                        await ws.send(json.dumps({
                            "type": "response.output.delta", "kind": "text",
                            "text": "好的。", "response_id": rid,
                        }))
                        await ws.send(json.dumps({
                            "type": "response.output.delta", "kind": "listen",
                            "response_id": rid,
                        }))
                        await ws.send(json.dumps({
                            "type": "response.done", "text": "好的。", "response_id": rid,
                        }))
                except Exception:  # noqa: BLE001
                    pass

            async def _main():
                # max_size=None 与真服务端一致（声纹参考音 base64 约 1.06MB，超默认 1MB 上限）
                self._server = await websockets.serve(_handler, "127.0.0.1", 0, max_size=None)
                self.port = self._server.sockets[0].getsockname()[1]
                ready.set()
                while not self._stop.is_set():
                    await asyncio.sleep(0.1)
                self._server.close()
                await self._server.wait_closed()

            try:
                self._loop.run_until_complete(_main())
            except Exception:  # noqa: BLE001
                ready.set()

        self._thread = threading.Thread(target=_run, daemon=True, name="fake-omni")
        self._thread.start()
        ready.wait(timeout=10)
        return self.port

    def stop(self):
        """停掉假服务端。"""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)


def _run_ws_case(video_enabled: bool, run_secs: float = 2.5):
    """跑一次真实上行，返回假服务端抓到的 arrivals 列表。

    Args:
        video_enabled: 图像上行总开关的值。
        run_secs: 观察窗口（秒）。

    Returns:
        list: [(收到时刻, 音频秒数, 是否带图), ...]
    """
    backend = _FakeBackend()
    port = backend.start()
    assert port, "假服务端未起来"

    client = None
    feeder_stop = threading.Event()
    try:
        client = OmniClient(
            url=f"ws://127.0.0.1:{port}/backend",
            ref_audio_path=_REF_AUDIO,
            system_prompt="x",
            enable_mic=False, enable_camera=False, enable_playback=False,
            flow_control=True, max_buf_secs=1.2, chunk_wait_timeout=1.0,
            video_interval=1.0, video_enabled=video_enabled, debug=False,
        )
        # 假摄像头：直接往「最新帧」缓存塞一帧。**关键**：即使有帧可用，
        # 关掉总开关后也不许发出去——这正是本用例要验证的语义。
        with client._latest_jpg_lock:
            client._latest_jpg = _FAKE_JPEG

        def _feed():
            chunk = np.zeros(int(16000 * 0.4), dtype=np.float32).tobytes()
            while not feeder_stop.is_set():
                with client._mic_lock:
                    client._mic_buf.extend(chunk)
                feeder_stop.wait(0.4)

        threading.Thread(target=_feed, daemon=True, name="fake-mic").start()
        assert client.start(timeout=30), "客户端未就绪"
        time.sleep(run_secs)
    finally:
        feeder_stop.set()
        if client is not None:
            client.stop()
        backend.stop()
    return backend.arrivals


def test_ws_no_video_frames_when_switch_off():
    """WS 集成：关掉总开关后，上行报文里一个 video_frames 都不许出现（假帧明明可用）。"""
    arrivals = _run_ws_case(video_enabled=False)
    assert len(arrivals) >= 3, f"上行流量过少，链路可能没跑起来: {len(arrivals)} 段"
    frames = sum(1 for _, _, f in arrivals if f)
    assert frames == 0, f"总开关已关，仍有 {frames}/{len(arrivals)} 段带了图"
    bad = [s for _, s, _ in arrivals if s <= 0]
    assert not bad, f"存在无音频的上行帧（会触发服务端 fail_fast missing_audio）: {len(bad)} 帧"
    print(f"PASS: 总开关关闭后 {len(arrivals)} 段上行全部纯音频、零视频帧")


def test_ws_video_frames_present_when_switch_on():
    """对照组：开着总开关时必须有段带图，否则上面的断言毫无意义。"""
    arrivals = _run_ws_case(video_enabled=True)
    assert len(arrivals) >= 3, f"上行流量过少: {len(arrivals)} 段"
    frames = sum(1 for _, _, f in arrivals if f)
    assert frames >= 1, f"总开关开着却一帧图都没发（对照组失效）: {len(arrivals)} 段"
    assert frames < len(arrivals), f"图像降频失效：{frames}/{len(arrivals)} 段都带图"
    print(f"PASS: 对照组 {frames}/{len(arrivals)} 段带图（1 帧/秒抽帧生效）")


if __name__ == "__main__":
    test_should_attach_frame_switch_off_is_always_false()
    test_ws_no_video_frames_when_switch_off()
    test_ws_video_frames_present_when_switch_on()
    print("\n全部通过 ✅")
