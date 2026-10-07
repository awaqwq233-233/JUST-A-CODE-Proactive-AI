"""方案 B 客户端的真实本机 WebSocket 契约测试，不打开设备。"""

import asyncio
import json
import queue
import subprocess
import sys
import threading

import numpy as np
import pytest
from websockets.asyncio.server import serve

from src.omni.gateway_client import GatewayClient, GatewayCallbacks
from src.omni import realtime_protocol as protocol
from src.omni.gateway_cli import main as cli_main


class FakeDevices:
    """模拟顺序采集与即时播放，所有统计均来自模拟数据。"""

    instances = []

    def __init__(self, **kwargs):
        """创建已准备的媒体源，不连接真实摄像头或声卡。"""
        self.instances.append(self)
        self.audio = self
        self.camera_ready = threading.Event()
        self.camera_ready.set()
        self.playback = queue.Queue()
        self.pending_output = np.zeros(0, dtype="<f4")
        self.error = None
        self.captured_frames = self.audio_queue_max = self.playback_queue_max = 1
        self.played_samples = self.input_status_events = self.output_status_events = 0
        self.mic_rms_current = 0.1
        self.started = self.stopped = False
        self.index = 0

    def start(self):
        """记录模拟设备启动。"""
        self.started = True

    def get(self, *args):
        """返回可区分顺序的完整一秒波形。"""
        self.index += 1
        return np.full(16000, self.index / 10, dtype="<f4")

    def latest_video(self):
        """返回仅用于编码契约测试的 JPEG 标记。"""
        return b"\xff\xd8\xff\xd9"

    def latest_frame(self):
        """模拟 GUI 预览帧副本。"""
        return np.zeros((480, 640, 3), dtype="uint8")

    def enqueue_output(self, samples):
        """模拟播放计数，验证收到的音频顺序和总量。"""
        self.played_samples += len(samples)

    def finish_input(self):
        """模拟暂停上行，无真实采集线程。"""

    def stop(self):
        """记录设备清理。"""
        self.stopped = True
        return True


def test_two_sessions_restore_context_and_keep_exact_audio(tmp_path):
    """真实 WS 验证排队/init/固定节拍/音频独立接收/轮换重建与上下文重注入。"""
    import soundfile as sf
    voice = tmp_path / "voice.wav"
    sf.write(voice, np.zeros(16000, dtype="float32"), 16000)
    inits, inputs = [], []
    FakeDevices.instances = []

    async def scenario():
        """使用本机随机端口提供与固定版本相同的生命周期事件。"""
        async def handler(ws):
            """先排队后初始化，独立返回文本/音频及无节拍意义的 response.done。"""
            await ws.send(json.dumps({"type": "session.queue_done"}))
            init = json.loads(await ws.recv())
            assert init["type"] == "session.init"
            inits.append(init)
            await ws.send(json.dumps({"type": "session.created", "session_id": str(len(inits)), "mode": "full_duplex"}))
            while True:
                message = json.loads(await ws.recv())
                if message["type"] == "session.close":
                    await ws.send(json.dumps({"type": "session.closed", "reason": "client_closed"}))
                    return
                inputs.append(message)
                key = len(inputs)
                metrics = {"cost_all_ms": 321}
                await ws.send(json.dumps({"type": "response.output.delta", "kind": "text", "text": "助手已回应。",
                                          "input_id": key, "metrics": metrics}))
                await ws.send(json.dumps({"type": "response.output.delta", "kind": "audio",
                                          "audio": protocol.encode_pcm(np.ones(2400, dtype="<f4")),
                                          "input_id": key, "metrics": metrics}))
                await ws.send(json.dumps({"type": "response.done"}))
                await ws.send(json.dumps({"type": "response.output.delta", "kind": "listen"}))

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            client = GatewayClient(url=f"ws://127.0.0.1:{port}", ref_audio_path=voice,
                                   consent_devices=True, device_factory=FakeDevices,
                                   context_provider=lambda: "用户已确认：使用简体中文。",
                                   session_seconds=5, drain_seconds=0.05)
            await client.run(max_sessions=2)
            stats = client.stats()
            assert stats["sessions_completed"] == 2 and stats["chunks_sent"] == 10
            assert stats["native_audio_samples"] == stats["played_audio_samples"] == 24000
            assert stats["processing_p95_ms"] == 321
            assert stats["cleanup_failures"] == 0

    asyncio.run(scenario())
    assert len(inits) == 2
    assert "voice" in inits[0]["payload"] and "voice" in inits[1]["payload"]
    assert "用户已确认" in inits[1]["payload"]["system_prompt"]
    assert "近期助手输出" in inits[1]["payload"]["system_prompt"]
    assert all(device.started and device.stopped for device in FakeDevices.instances)
    for index, message in enumerate(inputs):
        samples = protocol.decode_pcm(message["input"]["audio"])
        assert len(samples) == 16000
        assert np.all(samples == np.float32((index % 5 + 1) / 10))
        assert message["input"].get("force_listen", False) == (index % 5 < 4)


def test_cancel_during_queue_closes_resources(tmp_path):
    """排队阶段取消必须退出，不打开设备或发出新会话。"""
    import soundfile as sf
    voice = tmp_path / "voice.wav"
    sf.write(voice, np.zeros(160, dtype="float32"), 16000)
    FakeDevices.instances = []

    async def scenario():
        """服务器仅发排队事件，验证取消不等待完整 120 秒超时。"""
        queued = asyncio.Event()

        async def handler(ws):
            """模拟尚未获配 Worker 的连接。"""
            await ws.send(json.dumps({"type": "session.queued"}))
            queued.set()
            await ws.wait_closed()

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            client = GatewayClient(url=f"ws://127.0.0.1:{port}", ref_audio_path=voice,
                                   consent_devices=True, device_factory=FakeDevices, drain_seconds=0)
            task = asyncio.create_task(client.run(max_sessions=1))
            await queued.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    asyncio.run(scenario())
    assert len(FakeDevices.instances) == 1
    assert FakeDevices.instances[0].stopped and not FakeDevices.instances[0].started


def test_device_consent_rejected_before_hardware_or_network():
    """未同意设备访问时不能加载参考音或开始网络会话。"""
    with pytest.raises(ValueError, match="明确同意"):
        asyncio.run(GatewayClient().run(max_sessions=1))
    with pytest.raises(SystemExit) as failure:
        cli_main([])
    assert failure.value.code == 2


def test_gateway_entrypoint_does_not_import_legacy_dependencies():
    """独立 3.11 环境可加载生产客户端，不隐式加载 PyAudio 或 torch。"""
    command = "from src.omni import GatewayClient; import sys; assert 'pyaudio' not in sys.modules; assert 'torch' not in sys.modules"
    subprocess.run([sys.executable, "-c", command], check=True, timeout=15)


def test_manual_stop_waits_for_close_ack_before_restart(tmp_path):
    """用真实线程及 WS 延迟关闭确认，停止必须等待 Worker 释放后才能重启。"""
    import soundfile as sf
    voice = tmp_path / "voice.wav"
    sf.write(voice, np.zeros(160, dtype="float32"), 16000)
    FakeDevices.instances = []

    async def scenario():
        """模拟关闭耗时的 Worker；提前断开即保留 busy，第二次建会话将失败。"""
        busy = False
        acknowledged = 0
        close_requested = asyncio.Event()

        async def handler(ws):
            """只有客户端保持连接等待 session.closed 才完成关闭确认。"""
            nonlocal busy, acknowledged
            await ws.send(json.dumps({"type": "session.queue_done"}))
            if busy:
                await ws.send(json.dumps({"type": "error", "error": {"code": "session_failed"}}))
                return
            busy = True
            await ws.recv()
            await ws.send(json.dumps({"type": "session.created", "session_id": "manual", "mode": "full_duplex"}))
            async for raw in ws:
                if json.loads(raw)["type"] == "session.close":
                    close_requested.set()
                    await asyncio.sleep(0.25)
                    try:
                        await ws.send(json.dumps({"type": "session.closed", "reason": "client_closed"}))
                    except Exception:
                        return
                    busy = False
                    acknowledged += 1
                    return

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            client = GatewayClient(url=f"ws://127.0.0.1:{port}", ref_audio_path=voice,
                                   consent_devices=True, device_factory=FakeDevices, drain_seconds=0)
            try:
                for _ in range(3):
                    assert await asyncio.to_thread(client.start, 3)
                    close_requested.clear()
                    stopper = asyncio.create_task(asyncio.to_thread(client.stop))
                    await asyncio.wait_for(close_requested.wait(), 2)
                    with pytest.raises(RuntimeError, match="停止尚未完成"):
                        await asyncio.to_thread(client.start, 3)
                    # 第二个停止调用不能再次取消正在执行的关闭握手。
                    await asyncio.gather(stopper, asyncio.to_thread(client.stop))
                    assert not busy, "停止返回时 Worker 尚未完成关闭"
                assert acknowledged == 3
            finally:
                await asyncio.to_thread(client.stop)

    asyncio.run(scenario())
    assert all(device.stopped for device in FakeDevices.instances)


def test_server_error_code_is_preserved_without_payload(tmp_path):
    """启动拒绝保留安全错误代码，不再只有 RuntimeError，也不输出后端原始消息。"""
    import soundfile as sf
    voice = tmp_path / "voice.wav"
    sf.write(voice, np.zeros(160, dtype="float32"), 16000)

    async def scenario():
        """模拟 Gateway 返回会话失败，验证错误没有被关闭收尾覆盖。"""
        async def handler(ws):
            """拒绝初始化并携带不应显示的原始消息。"""
            await ws.send(json.dumps({"type": "session.queue_done"}))
            await ws.recv()
            await ws.send(json.dumps({"type": "error", "error": {
                "code": "session_failed", "message": "private_payload_not_for_gui"}}))

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            client = GatewayClient(url=f"ws://127.0.0.1:{port}", ref_audio_path=voice,
                                   consent_devices=True, device_factory=FakeDevices)
            assert not await asyncio.to_thread(client.start, 3)
            assert "session_failed" in client.last_error
            assert "private_payload_not_for_gui" not in client.last_error
            await asyncio.to_thread(client.stop)

    asyncio.run(scenario())


def test_manual_stop_drains_inflight_input_before_close(tmp_path):
    """停止必须先停采集并给已发送输入收尾，避免中断 C++ 尚未结束的 decode。"""
    import soundfile as sf
    voice = tmp_path / "voice.wav"
    sf.write(voice, np.zeros(160, dtype="float32"), 16000)

    async def scenario():
        """模拟在途推理，关闭过早时明确拒绝。"""
        received, processed = asyncio.Event(), asyncio.Event()
        close_after_drain = []

        async def processing():
            """模拟推理仍在使用当前输入的短窗口。"""
            await asyncio.sleep(0.1)
            processed.set()

        async def handler(ws):
            """收到输入后异步处理，关闭时记录是否已经完成。"""
            await ws.send(json.dumps({"type": "session.queue_done"}))
            await ws.recv()
            await ws.send(json.dumps({"type": "session.created", "session_id": "drain", "mode": "full_duplex"}))
            tasks = []
            try:
                async for raw in ws:
                    message = json.loads(raw)
                    if message["type"] == "input.append":
                        tasks.append(asyncio.create_task(processing()))
                        received.set()
                    elif message["type"] == "session.close":
                        close_after_drain.append(processed.is_set())
                        await ws.send(json.dumps({"type": "session.closed", "reason": "client_closed"}))
                        return
            finally:
                await asyncio.gather(*tasks)

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            client = GatewayClient(url=f"ws://127.0.0.1:{port}", ref_audio_path=voice,
                                   consent_devices=True, device_factory=FakeDevices, drain_seconds=0.25)
            assert await asyncio.to_thread(client.start, 3)
            await asyncio.wait_for(received.wait(), 2)
            await asyncio.to_thread(client.stop)
            assert close_after_drain == [True]
            assert client.stats()["close_handshakes"] == 1

    asyncio.run(scenario())
