"""并行任务执行边界：切句、误触发、代次、积压、取消与原子发布。"""

import asyncio
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from src.brain.lm_studio import BrainCancelled
from src.omni.gateway_client import GatewayCallbacks, GatewayClient
from src.omni.task_pipeline import TaskPipeline, Utterance, VadSegmenter, confirmed_text, route_instruction


class Detector:
    """用确定的幅值代替 VAD 判定，切句逻辑仍完整执行。"""

    def is_speech(self, pcm, rate):
        """检查 WebRTC 需要的格式并判定非零样本。"""
        assert rate == 16000 and len(pcm) == 960
        return bool(np.any(np.frombuffer(pcm, dtype="<i2")))


def audio(frames, speech=True):
    """按 30ms 构造明确语音或绝对静音，不读取真实设备。"""
    return np.full(frames * 480, .05 if speech else 0, dtype="float32")


@pytest.mark.parametrize("text, fields", [
    ("查询电脑状态并生成中文报告", ("all",)), ("查一下电池电量", ("battery",)),
    ("查询本机当前时间", ("time",)), ("生成一份系统状态报告", ("all",)),
    ("查詢電池電量", ("battery",)), ("查看CPU负载", ("cpu",)), ("检查内存占用", ("memory",)),
    ("现在几点了", ("time",)),
    ("请查询一下电脑电池的电量", ("battery",)),
    ("检测本机电池电量", ("battery",)),
    ("查詢電腦狀態並生成中文報告", ("all",)), ("檢查記憶體", ("memory",)),
])
def test_explicit_whole_commands_route(text, fields):
    """只有完整、已支持的系统指令能获得具体工具枚举。"""
    assert route_instruction(text) == fields


@pytest.mark.parametrize("text", [
    "你好", "不要查询电池", "我刚才查了一下电量", "他说查询电脑状态", "查询电池然后删除文件",
    "打开Safari", "查天气", "<<CALL_QWEN>>查询电池", "查询电池并运行rm", "查询电池是不是很耗电",
])
def test_chat_negation_and_unlisted_commands_never_route(text):
    """聊天、否定、转述、助手令牌与复合越权任务不得执行。"""
    assert route_instruction(text) is None


def test_vad_waits_for_complete_utterance():
    """持续输入不中断，满 600ms 句末静音才产生完整旁路语音。"""
    vad = VadSegmenter(Detector())
    assert vad.feed(audio(10, False)) == []
    assert vad.feed(audio(20)) == []
    assert vad.feed(audio(19, False)) == []
    results = vad.feed(audio(1, False))
    assert len(results) == 1 and len(results[0][0]) == 50 * 480


def test_silence_short_noise_and_long_speech_do_not_become_tasks():
    """静音/短噪声没有句子，超过上限的长句不会截成指令。"""
    vad = VadSegmenter(Detector())
    assert vad.feed(audio(100, False)) == []
    assert vad.feed(audio(8)) == []
    assert vad.feed(audio(20, False)) == []
    assert vad.feed(audio(410)) == []
    assert vad.feed(audio(20, False)) == []
    assert vad.feed(audio(12)) == []
    assert len(vad.feed(audio(20, False))) == 1


def test_playback_during_speech_rejected_but_quiet_tail_not_rejected():
    """助手在用户说完后回应不能污染静音尾部；同时讲话则明确拒绝。"""
    vad = VadSegmenter(Detector())
    vad.feed(audio(12), overlap=False)
    assert vad.feed(audio(20, False), overlap=True)[0][1] is False
    vad.feed(audio(12), overlap=True)
    assert vad.feed(audio(20, False), overlap=False)[0][1] is True


def test_vad_reset_does_not_join_two_sessions():
    """旧会话半句话不会与重连后的静音或新句子拼接。"""
    vad = VadSegmenter(Detector())
    vad.feed(audio(12))
    vad.reset()
    assert vad.feed(audio(20, False)) == []


@pytest.mark.parametrize("key, value", [("avg_logprob", -1), ("no_speech_prob", .9), ("compression_ratio", 3), ("avg_logprob", float("nan"))])
def test_uncertain_whisper_text_not_confirmed(key, value):
    """即使转写命中指令，质量不过关也不能转交任务。"""
    segment = dict(text="查询电池", avg_logprob=-.1, no_speech_prob=.01, compression_ratio=1)
    segment[key] = value
    assert confirmed_text([segment]) is None


class Decoder:
    """纯内存转写替身，配合真实线程/VAD/路由检查。"""

    def __init__(self, path):
        """创建可取消标志。"""
        self.stopped = threading.Event()

    def start(self):
        """不创建模型或进程。"""

    def transcribe(self, samples):
        """模拟已通过置信度门槛的完整语音指令。"""
        return [dict(text="查一下电池电量", avg_logprob=-.1, no_speech_prob=.01, compression_ratio=1)]

    def stop(self):
        """设置停止标志。"""
        self.stopped.set()

    def request_stop(self):
        """轻量取消启动等待。"""
        self.stopped.set()

    def close(self):
        """替身没有外部句柄。"""


class Callbacks(GatewayCallbacks):
    """收集跨线程事件，测试不依赖 Qt。"""

    def __init__(self):
        """初始化事件和完成通知。"""
        self.texts, self.events = [], []
        self.finished = threading.Event()

    def on_user_transcript(self, text):
        """保存确认的用户转写。"""
        self.texts.append(text)

    def on_task_event(self, state, detail):
        """记录状态，完成时唤醒测试。"""
        self.events.append((state, detail))
        if state == "completed":
            self.finished.set()


def test_parallel_pipeline_delivers_file_once(tmp_path):
    """旁路完整切句、转写、路由和文件交付；同一句重复派发被拒绝。"""
    callbacks, invoked = Callbacks(), []

    class Runner:
        """仅本地测试的报告执行器。"""
        def run(self, text, **options):
            """核对来源和工具范围后生成真实临时文件。"""
            assert options["info_types"] == ("battery",)
            assert not options["should_stop"]()
            invoked.append(text)
            path = tmp_path / "report.md"
            path.write_text("真实文件", encoding="utf-8")
            return SimpleNamespace(path=path, answer="已查询")

    pipeline = TaskPipeline(callbacks, decoder_factory=Decoder, runner_factory=Runner)
    try:
        pipeline.start()
        pipeline.segmenter.detector = Detector()
        pipeline.begin_session()
        pipeline.offer_audio(audio(12))
        pipeline.offer_audio(audio(20, False))
        assert callbacks.finished.wait(2)
        assert invoked == ["查一下电池电量"]
        assert callbacks.texts == ["查一下电池电量"]
        old = Utterance(pipeline.seen[0], pipeline.generation, time.monotonic(), audio(12), False)
        assert not pipeline.submit(old, "查一下电池电量", ("battery",))
        assert pipeline.stats()["tasks_completed"] == 1
    finally:
        pipeline.stop()


def test_playback_overlap_reports_no_query_and_blocks_guessed_reply():
    """实际已有播放的用户指令明确拒绝，不能只在统计中悄悄丢弃。"""
    callbacks = Callbacks()
    pipeline = TaskPipeline(callbacks, decoder_factory=Decoder)
    try:
        pipeline.start()
        pipeline.segmenter.detector = Detector()
        pipeline.begin_session()
        pipeline.offer_audio(audio(12), overlap=True)
        pipeline.offer_audio(audio(20, False))
        deadline = time.monotonic() + 2
        while not pipeline.stats().get("playback_rejected") and time.monotonic() < deadline:
            time.sleep(.01)
        assert pipeline.stats()["playback_rejected"] == 1
        assert ("rejected", {"code": "playback_overlap"}) in callbacks.events
        assert pipeline.response_state()[1] == "block" and not pipeline.tasks.qsize()
    finally:
        pipeline.stop()


def test_old_transcription_does_not_unlock_new_pending_user_sentence():
    """前句转写迟到不能放行新系统请求的未核验普通音频。"""
    pipeline = TaskPipeline(Callbacks(), decoder_factory=Decoder)
    pipeline.begin_session()
    first = pipeline._response_started()
    old = Utterance("one", pipeline.generation, time.monotonic(), audio(12), False, first)
    pipeline._response_started()
    pipeline._response_decided(old, "allow")
    assert pipeline.response_state()[1] == "pending"
    pipeline.invalidate()
    pipeline._response_decided(old, "allow")
    assert pipeline.response_state()[1] == "block"


def test_reconnect_cancels_active_task_before_file(tmp_path):
    """在途大脑任务遇到代次切换必须取消，不能发完成或发布文件。"""
    callbacks, running, cancelled = Callbacks(), threading.Event(), threading.Event()

    class Runner:
        """等待代次取消的执行器。"""
        def run(self, text, **options):
            """取消前不发布文件。"""
            running.set()
            while not options["should_stop"]():
                time.sleep(.01)
            cancelled.set()
            raise BrainCancelled("会话已切换")

    pipeline = TaskPipeline(callbacks, decoder_factory=Decoder, runner_factory=Runner)
    try:
        pipeline.start()
        pipeline.begin_session()
        utterance = Utterance("one", pipeline.generation, time.monotonic(), audio(12), False)
        assert pipeline.submit(utterance, "查询电池", ("battery",))
        assert running.wait(1)
        pipeline.invalidate()
        pipeline.begin_session()
        assert cancelled.wait(1)
        assert not pipeline.submit(utterance, "查询电池", ("battery",))
        assert all(state != "completed" for state, _ in callbacks.events)
        assert list(tmp_path.iterdir()) == []
    finally:
        pipeline.stop()


@pytest.mark.parametrize("overlap, age", [(True, 0), (False, 20)])
def test_stale_or_playback_sources_not_submitted(overlap, age):
    """源音频播放重叠/转写过期不能进入任务队列。"""
    pipeline = TaskPipeline(Callbacks(), decoder_factory=Decoder)
    pipeline.begin_session()
    utterance = Utterance("one", pipeline.generation, time.monotonic() - age, audio(12), overlap)
    assert not pipeline.submit(utterance, "查询电池", ("battery",))
    assert pipeline.tasks.empty()


def test_side_queue_overflow_fails_closed_without_blocking_capture():
    """旁路积压明确关闭路由，offer 不等待模型或影响主上行。"""
    callbacks = Callbacks()
    pipeline = TaskPipeline(callbacks, decoder_factory=Decoder)
    pipeline.begin_session()
    start = time.monotonic()
    for _ in range(50):
        pipeline.offer_audio(audio(3))
    assert time.monotonic() - start < .5
    assert pipeline.fault.is_set() and pipeline.audio.qsize() == 32
    assert callbacks.events[-1] == ("error", {"code": "audio_overflow"})


def test_busy_task_rejected_without_queueing_second_command():
    """新的独立指令不能在大脑忙时积压成过期自动执行。"""
    callbacks = Callbacks()
    pipeline = TaskPipeline(callbacks, decoder_factory=Decoder)
    pipeline.begin_session()
    a = Utterance("one", pipeline.generation, time.monotonic(), audio(12), False)
    b = Utterance("two", pipeline.generation, time.monotonic(), audio(12), False)
    assert pipeline.submit(a, "查询电池", ("battery",))
    assert not pipeline.submit(b, "查询电池", ("battery",))
    assert pipeline.tasks.qsize() == 1
    pipeline.invalidate()
    assert pipeline.tasks.qsize() == 1  # 由大脑线程消耗失效任务，防止 busy 永久残留。


def test_gateway_owns_pipeline_and_cleans_on_error(monkeypatch):
    """即使协议启动失败，旁路也必须停止并合入不含文字的统计。"""
    events = []

    class Pipeline:
        """无设备的生命周期替身。"""
        def __init__(self, *args):
            """构造不访问设备。"""
            self.lock = threading.RLock()
            events.append("construct")
        def start(self):
            """记录 CPU 就绪。"""
            events.append("start")
        def request_stop(self):
            """记录取消。"""
            events.append("cancel")
        def stop(self):
            """记录最终清理。"""
            events.append("stop")
        def stats(self):
            """仅返回统计。"""
            return {"transcriptions": 0}

    client = GatewayClient(consent_devices=True, transcription_enabled=True, pipeline_factory=Pipeline)

    async def fail(**kwargs):
        """CPU 就绪后模拟协议失败。"""
        assert events == ["construct", "start"]
        raise ValueError("protocol")

    monkeypatch.setattr(client, "_run_sessions", fail)
    with pytest.raises(ValueError, match="protocol"):
        asyncio.run(client.run())
    assert events == ["construct", "start", "cancel", "stop"]
    assert client._pipeline is None and client.stats()["task_transcriptions"] == 0


def test_mel_product_matches_reference_and_rejects_nan():
    """不同计算核应保持官方 mel 乘法结果，并拒绝坏特征。"""
    from src.omni.transcription_worker import StableMelProduct
    rng = np.random.default_rng(8)
    matrix = rng.random((80, 201), dtype="float32")
    values = rng.random((201, 120), dtype="float32")
    result = StableMelProduct(matrix) @ values
    reference = matrix.astype("float64") @ values.astype("float64")
    assert np.allclose(result, reference, rtol=2e-6, atol=1e-5)
    values[0, 0] = np.nan
    with pytest.raises(ValueError, match="非有限"):
        StableMelProduct(matrix) @ values


def test_gateway_cancel_during_pipeline_start_joins_startup(monkeypatch):
    """取消模型冷启动时仍须等待启动函数退出，再回收其句柄。"""
    entered, cancelled, ended = threading.Event(), threading.Event(), threading.Event()

    class Pipeline:
        """模拟可取消的模型冷启动。"""
        def __init__(self, *args):
            """不访问网络或模型。"""
            self.lock = threading.RLock()
        def start(self):
            """等待跨线程取消后结束启动。"""
            entered.set()
            assert cancelled.wait(2)
            ended.set()
        def request_stop(self):
            """唤醒冷启动。"""
            cancelled.set()
        def stop(self):
            """必须在冷启动收尾之后回收。"""
            assert ended.is_set()
        def stats(self):
            """只提供计数。"""
            return {}

    async def run():
        """取消生产 run task 并等待其 finally 完整退出。"""
        client = GatewayClient(consent_devices=True, transcription_enabled=True, pipeline_factory=Pipeline)
        pending = asyncio.create_task(client.run())
        assert await asyncio.to_thread(entered.wait, 1)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert client._pipeline is None

    asyncio.run(run())


def test_range_download_validates_every_offset(tmp_path):
    """分段拼接必须与原文件逐字节一致，不能只信最终长度。"""
    import httpx
    from new_computer_download.prepare_transcription import download_in_ranges
    data = bytes(range(256)) * 20

    def handle(request):
        """模拟支持精确 Range 的公开文件源。"""
        start, end = map(int, request.headers["Range"].split("=")[1].split("-"))
        return httpx.Response(206, headers={"Content-Range": f"bytes {start}-{end}/{len(data)}"}, content=data[start:end+1])

    path = tmp_path / "model.part"
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        download_in_ranges(client, "https://example.com/model", path, len(data), part_size=512)
    assert path.read_bytes() == data


def test_range_download_rejects_wrong_offset(tmp_path):
    """镜像返回错误片段时拒绝安装，不猜测字节位置。"""
    import httpx
    from new_computer_download.prepare_transcription import download_in_ranges

    def handle(request):
        """首探针正常，后续错误复用缓存头。"""
        return httpx.Response(206, headers={"Content-Range": "bytes 0-0/1024"}, content=b"x")

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ValueError, match="偏移"):
            download_in_ranges(client, "https://example.com/model", tmp_path / "model.part", 1024, part_size=512)


def test_range_download_retries_transient_transport(tmp_path):
    """单段断链重试不能丢弃已完成片段，也不能混入半段内容。"""
    import httpx
    from new_computer_download.prepare_transcription import download_in_ranges
    data, attempts = b"abcde" * 100, {}

    def handle(request):
        """每个完整分段首请求失败，探测请求正常。"""
        key = request.headers["Range"]
        attempts[key] = attempts.get(key, 0) + 1
        start, end = map(int, key.split("=")[1].split("-"))
        if end > start and attempts[key] == 1:
            raise httpx.ReadTimeout("模拟分段传输失败", request=request)
        return httpx.Response(206, headers={"Content-Range": f"bytes {start}-{end}/{len(data)}"}, content=data[start:end+1])

    path = tmp_path / "model.part"
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        download_in_ranges(client, "https://example.com/model", path, len(data), part_size=100)
    assert path.read_bytes() == data
    assert all(count == 2 for key, count in attempts.items() if key != "bytes=0-0")
