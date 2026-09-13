"""P0-b 背压的 WS 级集成验证：用一个「故意比 0.4s 慢」的假 omni 服务端压测客户端。

真机根因是「客户端固定 0.4s 猛推 + 服务端每段 0.69s」→ 积压线性增长（64s 落后 27s）。
本测试起一个本地假服务端，**每收到一段故意 sleep 0.6s 才回终局事件**，然后断言：

1. 客户端上行节拍会跟着服务端变慢（不再死守 0.4s），平均间隔 ≈ 0.6s；
2. 麦克风缓冲始终不超水位（不会堆积成几十秒）；
3. 正常节奏下不触发水位丢弃（丢弃只在真出问题时兜底）。

不需要加载 8GB 模型、不需要麦克风/摄像头，几秒钟跑完。
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

_SERVER_LAG = 0.6          # 假服务端单段处理耗时（>0.4s 预算，用于复现积压场景）
_FEED_SECS = 0.4           # 假麦克风每轮喂入的音频长度
_RUN_SECS = 4.5            # 观察窗口
_VIDEO_INTERVAL = 1.0      # P1 图像降频间隔（秒/帧）
_FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"jac-fake-frame" * 8 + b"\xff\xd9"


class _FakeBackend:
    """假 omni 服务端：握手 → 每收一段 input.append 慢 _SERVER_LAG 秒回终局事件。

    ⚠️ 刻意复刻真服务端的关键行为（2026-09-13 三轮踩坑）：
    「模型说了话然后切回聆听」的一段会**先发 listen delta、再发 response.done**，
    且两者**共用同一个 response_id**（`ws_handler.cpp:1173` / `:1232`）。
    假服务端必须照这个形态发，否则测不出客户端有没有做去重。
    """

    def __init__(self):
        self.arrivals = []          # (收到时刻 monotonic, 音频秒数, 是否带图)
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
                    await ws.recv()                        # session.init
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
                        # base64 → 字节 → float32 样本数（4 字节/样本）→ 秒
                        audio_secs = len(inp.get("audio_base64", "")) * 3 / 4 / 4 / 16000
                        has_frame = bool(inp.get("video_frames"))
                        self.arrivals.append((time.monotonic(), audio_secs, has_frame))
                        seq += 1
                        rid = f"fake-session_resp_{seq}"
                        await asyncio.sleep(_SERVER_LAG)   # 模拟服务端处理耗时
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
                # max_size=None 必须与真服务端一致：session.init 携带的声纹参考音
                # base64（约 1.06MB）会超过 websockets 默认 1MB 上限。
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


def test_backpressure_adapts_to_slow_server():
    """服务端每段 0.6s 时，客户端节拍应跟着变慢，且缓冲不堆积。"""
    backend = _FakeBackend()
    port = backend.start()
    assert port, "假服务端未起来"

    client = None
    feeder_stop = threading.Event()
    max_buf_secs = []
    try:
        client = OmniClient(
            url=f"ws://127.0.0.1:{port}/backend",
            ref_audio_path="voices/silverwalf_voice.wav",
            system_prompt="x",
            enable_mic=False, enable_camera=False, enable_playback=False,
            flow_control=True, max_buf_secs=1.2, chunk_wait_timeout=1.0,
            video_interval=_VIDEO_INTERVAL,
        )
        # 假摄像头：直接往「最新帧」缓存塞一帧（enable_camera=False 时没有真摄像头），
        # 用于验证图像上行确实按 video_interval 抽帧、而不是每段都带图。
        with client._latest_jpg_lock:
            client._latest_jpg = _FAKE_JPEG

        # 假麦克风：每 0.4s 往缓冲里塞 0.4s 音频（模拟真实采集节奏）。
        # 必须在 start() **之前**启动——start() 会等首个 listen 事件，
        # 而假服务端只在收到 input.append 后才回 listen；没有音频就没有上行。
        def _feed():
            chunk = np.zeros(int(16000 * _FEED_SECS), dtype=np.float32).tobytes()
            while not feeder_stop.is_set():
                with client._mic_lock:
                    client._mic_buf.extend(chunk)
                feeder_stop.wait(_FEED_SECS)

        threading.Thread(target=_feed, daemon=True, name="fake-mic").start()

        assert client.start(timeout=30), "客户端未就绪"

        # 采样缓冲水位，确认没有堆积成几十秒
        t_end = time.monotonic() + _RUN_SECS
        while time.monotonic() < t_end:
            with client._mic_lock:
                pending = len(client._mic_buf)
            max_buf_secs.append(pending / 4 / 16000)
            time.sleep(0.05)
    finally:
        feeder_stop.set()
        if client is not None:
            client.stop()
        backend.stop()

    arrivals = backend.arrivals
    assert len(arrivals) >= 3, f"上行流量过少，链路可能没跑起来: {len(arrivals)} 段"
    gaps = [arrivals[i + 1][0] - arrivals[i][0] for i in range(len(arrivals) - 1)]
    avg_gap = sum(gaps) / len(gaps)
    audio_secs = [s for _, s, _ in arrivals]
    avg_chunk = sum(audio_secs) / len(audio_secs)
    min_chunk = min(audio_secs)
    frames = sum(1 for _, _, f in arrivals if f)
    max_pending = max(max_buf_secs) if max_buf_secs else 0.0
    dropped = client._dropped_secs if client else -1.0

    print(f"  服务端收到 {len(arrivals)} 段，平均间隔 {avg_gap:.2f}s，"
          f"平均单段音频 {avg_chunk:.2f}s（最小 {min_chunk:.2f}s），"
          f"带图 {frames} 帧（图像间隔 {_VIDEO_INTERVAL}s），"
          f"缓冲峰值 {max_pending:.2f}s，水位丢弃 {dropped:.1f}s")

    # 1) 节拍跟着服务端慢下来（若没背压/去重失效，会死守 0.4s 且不断堆积）
    assert avg_gap >= 0.5, f"上行仍是固定 0.4s 猛推（积压未消除）: {avg_gap:.2f}s"
    assert avg_gap <= 1.1, f"节拍过度迟钝: {avg_gap:.2f}s"
    # 2) 核心不变量：上行音频量不得超过真实流逝时间（否则就是在堆积 → 延迟持续增长）
    assert avg_chunk <= avg_gap * 1.2 + 0.05, \
        f"上行音频量({avg_chunk:.2f}s/段)超过处理节拍({avg_gap:.2f}s/段)，仍在积压"
    # 3) 最小块时长：一段两个终局事件也不许把一段拆成两个极小块（真机见过 0.02s）
    assert min_chunk >= 0.3, \
        f"出现极小块 {min_chunk:.2f}s（终局事件去重/最小块时长失效，会白烧服务端一轮算力）"
    # 4) P1 图像降频：带图段数必须明显少于音频段数（图像不再每段都上）
    assert frames < len(arrivals), \
        f"图像降频未生效：{frames}/{len(arrivals)} 段都带了图"
    elapsed = arrivals[-1][0] - arrivals[0][0]
    frame_rate = frames / max(elapsed, 0.1)
    assert frame_rate <= 1.0 / _VIDEO_INTERVAL + 0.35, \
        f"图像上行速率 {frame_rate:.2f} 帧/秒 明显超出设定 1/{_VIDEO_INTERVAL}s"
    # 5) 缓冲不堆积：始终低于水位上限
    assert max_pending <= 1.25, f"麦克风缓冲堆积到 {max_pending:.2f}s（应被水位限制）"
    # 6) 正常节奏下不该触发丢弃兜底
    assert dropped == 0.0, f"正常节奏误触发水位丢弃: {dropped:.2f}s"
    print("PASS: 一段两个终局事件只放行一次；节拍自适应、块长有下限、图像已降频、零丢弃")


def test_no_mic_still_sends_valid_audio_frames():
    """回归（2026-09-13 真机断连）：采集缓冲为空时也绝不能发「无音频」的报文。

    服务端 full_duplex 分支对空音频会 `fail_fast("missing_audio")` → 发 session.closed
    并直接 `ws.close(1000)`，整个会话被打死（真机表现为「怎么说话都不回」+ 客户端只看到
    `received 1000 (OK)`）。所以无论有没有麦克风数据，报文都必须带音频：
    这里完全不启动假麦克风（缓冲恒空），断言每一帧仍然带音频、且会话能继续跑。
    """
    backend = _FakeBackend()
    port = backend.start()
    client = None
    try:
        client = OmniClient(
            url=f"ws://127.0.0.1:{port}/backend",
            ref_audio_path="voices/silverwalf_voice.wav",
            system_prompt="x",
            enable_mic=False, enable_camera=False, enable_playback=False,
            flow_control=True, max_buf_secs=1.2, chunk_wait_timeout=1.0,
        )
        # 注意：故意不启动假麦克风——缓冲恒空，走「补静音」兜底路径。
        # start() 能返回本身就证明静音帧被服务端接受并回了 listen（否则会等 120s 超时）。
        assert client.start(timeout=30), "无音频帧时客户端未能就绪（会话可能已被服务端打死）"
        time.sleep(2.0)
    finally:
        if client is not None:
            client.stop()
        backend.stop()

    arrivals = backend.arrivals
    assert len(arrivals) >= 2, f"上行流量过少，会话可能已中断: {len(arrivals)} 段"
    bad = [s for _, s, _ in arrivals if s <= 0]
    assert not bad, f"存在无音频的上行帧（会触发服务端 fail_fast missing_audio）: {len(bad)} 帧"
    print(f"PASS: 无麦克风数据时仍发出合法音频帧（{len(arrivals)} 段全部带音频），会话未被服务端打死")


if __name__ == "__main__":
    test_backpressure_adapts_to_slow_server()
    test_no_mic_still_sends_valid_audio_frames()
    print("\n全部通过 ✅")
