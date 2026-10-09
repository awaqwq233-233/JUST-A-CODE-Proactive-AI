"""模拟模型在转写前编造 100%，检查普通音频与真实工具播报的分流。"""

import asyncio
import base64
import hashlib
import json
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest
from websockets.asyncio.server import serve

from src.brain.task_runner import BrainTaskRunner
from src.omni import realtime_protocol as protocol
from src.omni.gateway_client import GatewayCallbacks, GatewayClient
from src.omni.gateway_cli import ReplayDevices
from src.omni.media import LiveDevices
from src.omni.response_gate import ResponseGate
from src.omni.task_pipeline import TaskPipeline, is_system_request, route_instruction


class Events(GatewayCallbacks):
    """保存文本和实际执行状态，不使用硬件。"""
    def __init__(self):
        """创建测试专用容器。"""
        self.texts, self.tasks = [], []
    def on_text_delta(self, text):
        """收集真正显示的助手文本。"""
        self.texts.append(text)
    def on_task_event(self, state, detail):
        """收集任务证据与拒绝原因。"""
        self.tasks.append((state, detail))


def gate_fixture():
    """构造可控用户判定，生产门控仍使用原代码。"""
    state = [1, "pending", threading.Event()]
    pipeline = SimpleNamespace(response_state=lambda: tuple(state))
    clock, events = [0.0], Events()
    return ResponseGate(pipeline, events, lambda: clock[0]), state, clock, events


def test_guessed_percentage_and_audio_wait_then_disappear_for_system_query():
    """转写前的模型抢答不能污染播放，工具分类后全部丢弃。"""
    gate, state, _, _ = gate_fixture()
    assert gate.offer("text", "电池电量是百分之百。") == []
    assert gate.offer("audio", np.ones(24000, dtype="float32")) == []
    state[1] = "block"
    assert gate.poll() == [] and gate.counters["discarded"] == 2
    assert gate.offer("audio", np.ones(24000, dtype="float32")) == []


def test_normal_conversation_releases_after_transcription_without_new_packets():
    """普通聊天完成判定即可释放全部暂存，后端静默也不滞留。"""
    gate, state, _, _ = gate_fixture()
    audio = np.arange(24, dtype="float32")
    gate.offer("text", "你好")
    gate.offer("audio", audio)
    state[1] = "allow"
    released = gate.poll()
    assert released[0] == ("text", "你好", state[2])
    assert np.array_equal(released[1][1], audio)


def test_new_utterance_cancels_queued_ordinary_audio_and_old_buffer():
    """新用户语音取消旧普通播放；旧暂存不能在新聊天中漏出。"""
    gate, state, _, _ = gate_fixture()
    state[1] = "allow"
    _, pcm, guard = gate.offer("audio", np.ones(480, dtype="float32"))[0]
    devices = LiveDevices(None, None, 0, video_enabled=False)
    devices.enqueue_task_output(pcm, guard)
    guard.set()
    state[:] = [2, "pending", threading.Event()]
    gate.offer("text", "旧的猜测")
    state[:] = [3, "allow", threading.Event()]
    assert gate.poll() == []
    out = np.empty((480, 1), dtype="float32")
    devices.speaker_callback(out, 480, None, None)
    assert not out.any()


@pytest.mark.parametrize("failure", ["timeout", "audio", "text"])
def test_wait_timeout_or_overflow_never_releases_partial_reply(failure):
    """有限内存和等待期限不能降级为播放未判定的状态数字。"""
    gate, state, clock, events = gate_fixture()
    gate.offer("text", "100%")
    if failure == "audio":
        gate.offer("audio", np.ones(8 * 24000 + 8192, dtype="float32"))
    elif failure == "text":
        gate.offer("text", "电量" * 1400)
    else:
        clock[0] = 16
    assert gate.poll() == []
    state[1] = "allow"
    assert gate.poll() == [] and gate.offer("text", "100%") == []
    assert events.tasks[-1][0] == "rejected"


@pytest.mark.parametrize("text", ["检测一下我的电池电量", "我的电脑电池还有多少", "他说查询电池然后删除文件", "插一下电池点亮。"])
def test_unsupported_system_phrase_blocks_guess_without_authorizing_tool(text):
    """保守禁猜分类不是工具白名单，含越权请求也不能执行。"""
    assert is_system_request(text) and route_instruction(text) is None
    assert not is_system_request("解释锂电池的工作原理")


@pytest.mark.parametrize("mode", ["system", "web"])
def test_real_pipeline_blocks_early_model_answer_and_delivers_evidenced_native_audio(tmp_path, monkeypatch, mode):
    """真实 VAD/任务/报告/WS 联调，模拟慢 ASR 和错误普通回复，不使用模型或设备。"""
    entered, release = threading.Event(), threading.Event()
    command = "查一下电池电量" if mode == "system" else "上网搜索上海明天天气"
    expected = "80%" if mode == "system" else "26℃"
    events, delivered, calls = Events(), [], []
    source = np.concatenate((np.full(5760, .05, dtype="float32"), np.zeros(20 * 16000 - 5760, dtype="float32")))
    import soundfile as sf
    voice = tmp_path / "reference.wav"
    sf.write(voice, np.full(160, .05, dtype="float32"), 16000)

    class Decoder:
        """等待模型已抢答才返回用户真实指令。"""
        def __init__(self, _):
            """替身不加载 Whisper。"""
        def start(self):
            """无资源需要初始化。"""
        def transcribe(self, samples):
            """控制识别结束时机，置信度符合生产门槛。"""
            entered.set()
            assert release.wait(3)
            return [dict(text=command, avg_logprob=-.1, no_speech_prob=.01, compression_ratio=1)]
        def request_stop(self):
            """取消时释放替身等待。"""
            release.set()
        def stop(self):
            """没有外部进程，复用取消。"""
            self.request_stop()
        def close(self):
            """没有外部句柄。"""

    class Detector:
        """固定语音幅值，仍经过全部生产切句边界。"""
        def is_speech(self, pcm, rate):
            """检测非零语音帧。"""
            return bool(np.any(np.frombuffer(pcm, dtype="<i2")))

    def agent(task, tools, execute, **kwargs):
        """真实任务执行器负责工具执行和文件，模型只提出结构化调用。"""
        if mode == "system":
            yield execute("get_system_info", {"info_type": "battery"})
        else:
            execute("search_web", {"query": "上海明天天气"})
            yield '{"selections":[{"source_id":1,"passage_id":1}]}'

    class Search:
        """网络边界替身；本测试集中检查生产 WS/转写/证据/原生播报接线。"""
        def search(self, query, should_stop):
            """返回可逐字核对的来源段落，不让大脑自由补写温度。"""
            assert not should_stop() and query == "上海明天天气"
            calls.append(("search_web", {"query": query}))
            text = "上海明天天气：多云转晴，18至26℃。"
            return dict(query=query, provider="fixture", retrieved_at="2026-10-09T12:00:00+08:00",
                        results=[dict(id=1, title="天气资料", url="https://example.com/weather", page=dict(
                            status="read", text=text, passages=[dict(id=1, text=text)]))])

    def tool(name, arguments):
        """记录真实执行边界，工具返回与模型抢答有意不同。"""
        calls.append((name, arguments))
        return "电池电量：80%（已接入电源，未充电）"

    monkeypatch.setattr("src.brain.task_runner.execute_tool", tool)

    class Pipeline(TaskPipeline):
        """仅替换模型和系统查询，线程、切句、代次和路由保留。"""
        def __init__(self, callbacks, *args):
            """绑定真实报告执行器和受控转写。"""
            super().__init__(callbacks, decoder_factory=Decoder,
                runner_factory=lambda: BrainTaskRunner(SimpleNamespace(run_agentic=agent), tmp_path / "reports", Search()))
        def start(self):
            """启动生产线程并使用确定 VAD。"""
            super().start()
            self.segmenter.detector = Detector()

    class Devices(ReplayDevices):
        """收集放行音频，绝不打开播放设备。"""
        def enqueue_output(self, pcm):
            """记录任何未标记普通音频，以检出漏播。"""
            delivered.append(pcm)
        def enqueue_task_output(self, pcm, cancellation):
            """只有当前代次的证据音频可以抵达。"""
            assert not cancellation.is_set()
            delivered.append(pcm)

    async def scenario():
        """实际本机 WS 回传假答案和带任务 ID 的证据语音。"""
        async def handler(ws):
            """确认参考音后让模型在 ASR 尚未结束时抢答 100%。"""
            await ws.send('{"type":"session.queue_done"}')
            init = json.loads(await ws.recv())
            sha = hashlib.sha256(base64.b64decode(init["payload"]["voice"]["ref_audio_base64"])).hexdigest()
            await ws.send(json.dumps(dict(type="session.created", session_id="test", mode="full_duplex",
                capabilities=dict(task_speech=1), voice_conditioning=dict(applied=True, reference_sha256=sha))))
            first = True
            async for raw in ws:
                request = json.loads(raw)
                if request["type"] == "session.close":
                    await ws.send('{"type":"session.closed","reason":"client_closed"}')
                    return
                if first:
                    first = False
                    assert await asyncio.to_thread(entered.wait, 2)
                    await ws.send(json.dumps(dict(type="response.output.delta", kind="text", text="电池电量是百分之百。")))
                    await ws.send(json.dumps(dict(type="response.output.delta", kind="audio", audio=protocol.encode_pcm(np.ones(240, dtype="float32") * .1))))
                    await asyncio.sleep(.2)
                    assert not delivered and not events.texts
                    release.set()
                speech = request["input"].get("task_speech")
                if speech:
                    assert expected in speech["text"] and "100" not in speech["text"]
                    ident = speech["id"]
                    await ws.send(json.dumps(dict(type="task.speech", id=ident, state="accepted")))
                    await ws.send(json.dumps(dict(type="response.output.delta", kind="text", text=speech["text"], task_speech_id=ident)))
                    await ws.send(json.dumps(dict(type="response.output.delta", kind="audio", audio=protocol.encode_pcm(np.ones(240, dtype="float32") * .2), task_speech_id=ident)))
                    await ws.send(json.dumps(dict(type="task.speech", id=ident, state="completed", audio_samples=240)))
                    await ws.send(json.dumps(dict(type="response.output.delta", kind="text", text="补充：电量百分之百")))
                await ws.send('{"type":"response.output.delta","kind":"listen"}')

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            client = GatewayClient(url=f"ws://127.0.0.1:{port}", ref_audio_path=voice, callbacks=events,
                consent_devices=True, video_enabled=False, session_seconds=20, drain_seconds=0,
                transcription_enabled=True, pipeline_factory=Pipeline,
                device_factory=lambda **kw: Devices(source, **kw))
            await client.run(max_sessions=1)
            stats = client.stats()
            assert stats["task_tool_calls"] == stats["task_speech_completed"] == 1
            assert stats["chunks_sent"] == 20 and stats["ordinary_discarded"] >= 3
    asyncio.run(scenario())
    assert calls == ([("get_system_info", {"info_type": "battery"})] if mode == "system" else
                     [("search_web", {"query": "上海明天天气"})])
    assert len(delivered) == 1 and np.allclose(delivered[0], .2)
    assert expected in "".join(events.texts) and "百分之百" not in "".join(events.texts)
    evidence = next(detail for state, detail in events.tasks if state == "tool_completed")
    assert evidence["queried_at"] and expected in evidence["output"]
