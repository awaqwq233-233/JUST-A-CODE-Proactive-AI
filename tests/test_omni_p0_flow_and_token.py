"""P0-a / P0-b 回归单测（对应 2026-09-13 真机根因修复）。

为什么必须新增：原有 `test_omni_m3_token.py` 把 `<<CALL_QWEN>>` **一次性整段**喂进
`_on_text`，而真机 omni 服务端是**按 token 逐片**下发文本的（`ws_handler.cpp`
的 `make_text_delta(frag)`），令牌一定会被切成 `<<CALL_Q` 这样的碎片。碎片到达的
那一刻缓冲里没有完整令牌，于是走了「正常对话」分支被朗读——Voicebox 里合成出
`<<CALL_Q` 那段怪音就是这么来的。所以旧测试永远抓不到这个 bug。

覆盖点：
1. 分片下发的令牌，任何碎片都不得进入朗读队列 / 显示流（P0-a）；
2. 正常文本仍能按增量流畅外发（holdback 不吞字）；
3. VoiceboxBridge 自身对令牌前缀也有兜底（纵深防御）；
4. P0-b 音频水位：超水位丢最旧的，保住「模型听到的是现在」；
5. P0-b 背压：等终局事件再放行下一段，超时兜底不死等。
"""
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.omni.client import OmniClient, _token_prefix_suffix_len, resolve_echo_gate
from src.omni.voicebox_bridge import VoiceboxBridge, _token_prefix_suffix_len as bridge_hold

TOKEN = "<<CALL_QWEN>>"


class _FakeWS:
    """假 WebSocket：按脚本 yield 下行事件，用于驱动 `_receiver_loop`。"""

    def __init__(self, events):
        self._events = list(events)
        self.sent = []

    def __aiter__(self):
        async def _gen():
            for ev in self._events:
                yield json.dumps(ev)
        return _gen()

    async def send(self, data):
        self.sent.append(data)


class _FakeSpeaker:
    """假 speaker：只记录被朗读的句子，不真正合成。"""

    def __init__(self):
        self.spoken = []

    def speak(self, text):
        self.spoken.append(text)


class _RecordingBridge:
    """替换 VoiceboxBridge，记录所有被 feed 的文本。"""

    def __init__(self, speaker, *args, **kwargs):
        self._spk = speaker
        self.fed = []

    def feed(self, delta):
        self.fed.append(delta)

    def flush_remaining(self):
        pass

    def flush_and_stop(self):
        pass


class _RecordingCallbacks:
    """记录广播到控制台/GUI 的文本，以及触发的升级任务。"""

    def __init__(self):
        self.shown = []
        self.tasks = []

    def on_text_delta(self, text):
        self.shown.append(text)

    def on_text_final(self, text):
        pass

    def on_state(self, state, info=None):
        pass

    def on_listen(self):
        pass

    def on_call_qwen(self, task):
        self.tasks.append(task)

    def on_audio_chunk(self, pcm):
        pass

    def on_mic_level(self, rms):
        pass

    def on_error(self, err):
        pass


def _make_client(**kwargs):
    """构造不连网络的 OmniClient，并把桥接器/回调换成记录器。"""
    fake = _FakeSpeaker()
    client = OmniClient(
        url="ws://127.0.0.1:9999/backend",
        ref_audio_path="voices/silverwalf_voice.wav",
        system_prompt="x",
        voicebox_speaker=fake,
        enable_mic=False,
        enable_camera=False,
        enable_playback=True,
        **kwargs,
    )
    rec = _RecordingBridge(fake)
    client._voicebox_bridge = rec
    cb = _RecordingCallbacks()
    client.cb = cb
    client._token_seen = False
    client._call_qwen_fired = False
    client._hallucinated = False
    client._text_buf = ""
    client._shown_len = 0
    client._pending_task = None
    client._pending_timer = None
    client._escalation_done = False
    # 模拟「令牌前用户真实发言」，让升级护栏放行（合法升级场景）
    client._last_speech_ts = time.monotonic()
    return client, rec, cb


# ============================ P0-a ============================
def test_prefix_helper():
    """前缀判定工具：只有真正的令牌前缀才需要扣留。"""
    assert _token_prefix_suffix_len("") == 0
    assert _token_prefix_suffix_len("你好世界") == 0
    assert _token_prefix_suffix_len("电脑的") == 0
    assert _token_prefix_suffix_len("<") == 1
    assert _token_prefix_suffix_len("<<") == 2
    assert _token_prefix_suffix_len("<<CALL_Q") == 8
    assert _token_prefix_suffix_len("查一下<<CALL_QWEN>") == 12
    # 完整令牌本身不算「前缀」（由 find 命中分支处理）
    assert _token_prefix_suffix_len(TOKEN) == 0
    assert bridge_hold("<<CALL_Q") == 8, "VoiceboxBridge 侧兜底判定应与 client 一致"
    print("PASS: 令牌前缀判定工具正确")


def test_split_token_fragments_never_spoken():
    """核心回归：令牌被逐片下发时，任何碎片都不得进入朗读队列或显示流。"""
    client, rec, cb = _make_client()
    # 真机服务端的分片形态：<< / CALL / _Q / WEN >> 逐片到达
    fragments = ["好的", "，", "我", "来", "<<", "CALL", "_Q", "WEN", ">>", "查一下电脑的电池电量。"]
    for f in fragments:
        client._on_text(f)
        joined_fed = "".join(rec.fed)
        assert "<<CALL" not in joined_fed, f"碎片泄漏进朗读队列: {joined_fed!r}（喂到 {f!r}）"
        joined_shown = "".join(cb.shown)
        assert "<<CALL" not in joined_shown, f"碎片泄漏到显示流: {joined_shown!r}（喂到 {f!r}）"
    # 完整令牌凑齐后应触发升级，且任务描述（内部指令）绝不朗读、绝不显示
    assert client._call_qwen_fired is True, "分片令牌凑齐后升级未触发"
    joined = "".join(rec.fed) + "".join(cb.shown)
    assert "CALL_QWEN" not in joined, f"令牌或任务描述泄漏: {joined!r}"
    assert "查一下" not in joined, f"内部任务描述被朗读/显示: {joined!r}"
    print("PASS: 分片令牌全程零泄漏，升级正常触发")


def test_plain_text_not_swallowed_by_holdback():
    """holdback 不能吞掉正常文本：不以 '<' 结尾的内容必须立即外发。"""
    client, rec, cb = _make_client()
    client._on_text("boss 我在，请讲。")
    assert "".join(cb.shown) == "boss 我在，请讲。", f"正常文本被扣留: {cb.shown!r}"
    assert "".join(rec.fed) == "boss 我在，请讲。"
    # 以 '<' 结尾：安全区照常外发，疑似前缀（连同其前导空白）被扣留
    client._on_text("温度 <")
    assert "".join(cb.shown) == "boss 我在，请讲。温度", \
        f"安全区应照常外发、只扣留疑似前缀（含前导空白）: {cb.shown!r}"
    # 补片到齐后：整段里含 '<' —— 硬安全网会从 '<' 起截断（语音台词不会含尖括号，
    # 而畸形令牌恰恰长这样，宁可少说几个字也不冒念出标记的风险）。
    # 尾部残留的空格是 holdback 连前导空白一起扣留带来的噪声，无音频意义（按 rstrip 比较）。
    client._on_text("10 度。")
    assert "".join(cb.shown).rstrip() == "boss 我在，请讲。温度", \
        f"含 '<' 的片段应被安全网截断: {cb.shown!r}"
    assert "<" not in "".join(cb.shown), f"尖括号残留不得外发: {cb.shown!r}"
    print("PASS: holdback 不吞字；含尖括号的片段由安全网截断")


def test_turn_end_flushes_holdback():
    """轮末释放：扣留的尾巴要在回合结束时补出来；含尖括号的部分由安全网截断。"""
    client, rec, cb = _make_client()
    client._on_text("好的呀")
    client._on_text("<")
    assert client._text_buf[client._shown_len:] == "<", "尾巴未被扣留"
    client._flush_text_holdback()
    assert "".join(cb.shown) == "好的呀", f"安全网应从 '<' 起截断: {cb.shown!r}"
    print("PASS: 轮末释放 holdback 尾巴（含标记部分被截断）")


def test_chunk_boundary_must_not_release_holdback():
    """真机回归（2026-09-13 二轮）：段边界落在令牌中间时，绝不能释放 holdback。

    full_duplex 下 `response.done` 是**每段一次**（不是每轮一次）。真机日志铁证：
    段边界正好停在 `<<CALL_QW`（8 字符），若在段末释放，就会把这段碎片当普通文本
    播出去（Voicebox 里那段 `<<CALL_Q` 怪音），并且把升级任务截断成「查一下」。
    因此只有 listen（轮末）或文本静默超时才允许释放。
    """
    client, rec, cb = _make_client()
    events = [
        {"type": "response.output.delta", "kind": "text", "text": "<<"},
        {"type": "response.output.delta", "kind": "text", "text": "CALL"},
        {"type": "response.output.delta", "kind": "text", "text": "_Q"},
        {"type": "response.done", "text": "<<CALL_Q"},   # ← 段边界落在令牌中间
        {"type": "response.output.delta", "kind": "text", "text": "WEN"},
        {"type": "response.output.delta", "kind": "text", "text": ">>"},
        {"type": "response.output.delta", "kind": "text", "text": "查一下电脑的电池。"},
    ]
    asyncio.run(client._receiver_loop(_FakeWS(events)))
    joined = "".join(cb.shown) + "".join(rec.fed)
    assert "CALL" not in joined, f"令牌碎片在段边界泄漏了: {joined!r}"
    assert client._call_qwen_fired is True, "跨段拼齐的令牌未触发升级"
    assert client._pending_task is None or "查一下电脑的电池" in (client._pending_task or ""), \
        "任务描述被段边界截断"
    print("PASS: 段边界不释放 holdback，跨段令牌仍能拼齐并正确升级")


def test_malformed_token_variants_never_leak():
    """真机回归（2026-09-13 三轮）：模型吐出的**畸形令牌**也必须被拦下。

    bo s s 已确认：日志里的 `<<CALL_ QWEN>>`（中间夹空格/换行）是**模型自己生成的**，
    不是复制粘贴。精确 `find("<<CALL_QWEN>>")` 匹配不到它，后果是碎片被当普通对话
    显示并朗读（真机里同一句被 Voicebox 反复念）。这里覆盖几种变体形态。
    """
    variants = [
        # 变体 1：令牌中间夹空格（真机实测形态）
        ["<<CALL_", " QWEN>>", "查一下最近的新闻。"],
        # 变体 2：夹换行
        ["<<CALL_\n", "QWEN>>\n", "查一下天气。"],
        # 变体 3：大小写混写 + 空格
        ["<< call_ qwen >> ", "打开浏览器。"],
        # 变体 4：跨段边界（listen 每段都会来，绝不能把令牌拦腰截断）
        ["好的，我", "<<CALL_", " ", "QWEN>>", "查一下时间。"],
    ]
    for i, fragments in enumerate(variants, 1):
        client, rec, cb = _make_client()
        for f in fragments:
            client._on_text(f)
        joined = "".join(cb.shown) + "".join(rec.fed)
        assert "CALL" not in joined.upper(), f"变体{i}: 令牌残留被外发: {joined!r}"
        assert "QWEN" not in joined.upper(), f"变体{i}: 令牌残留被外发: {joined!r}"
        assert client._call_qwen_fired is True, f"变体{i}: 畸形令牌未触发升级"
        assert cb.tasks and cb.tasks[-1].strip(), f"变体{i}: 升级任务为空"
        assert any(k in cb.tasks[-1] for k in ("新闻", "天气", "浏览器", "时间")), \
            f"变体{i}: 升级任务内容不对: {cb.tasks}"
    print("PASS: 四种畸形令牌变体全部被识别，零残留外发")


def test_malformed_token_does_not_break_display_flow():
    """畸形令牌之前的正常台词要照常显示/朗读，只截掉令牌部分。"""
    client, rec, cb = _make_client()
    client._on_text("好的，我帮你")
    client._on_text("<<CALL_ QWEN>>")
    client._on_text("查一下最近的新闻。")
    joined = "".join(cb.shown)
    assert joined == "好的，我帮你", f"令牌前的正常台词应完整显示: {joined!r}"
    assert "新闻" not in joined, "内部任务描述不得显示"
    assert client._call_qwen_fired is True
    print("PASS: 畸形令牌前的正常台词完整保留，任务描述不外泄")


def test_safety_net_blocks_residual_marker():
    """硬安全网：段边界切剩的裸标记碎片（如 `QWEN>>`）绝不能显示/朗读。"""
    client, rec, cb = _make_client()
    # 模拟"缓冲被清过、只剩裸碎片"的最坏情况
    client._on_text("QWEN>>查一下")
    assert "".join(cb.shown) == "", f"裸标记碎片被显示了: {cb.shown!r}"
    assert "".join(rec.fed) == "", f"裸标记碎片被朗读了: {rec.fed!r}"
    # 正常中文/英文台词不受影响
    client._on_text("boss 我在")
    assert "".join(cb.shown) == "boss 我在"
    print("PASS: 硬安全网拦住裸标记碎片，且不误伤正常台词")


def test_idle_flush_releases_holdback():
    """静默兜底：模型这轮说完却不回 listen 时，超时也要把尾巴放出来（不丢正常字）。"""
    client, rec, cb = _make_client()
    client._hold_idle_secs = 0.2

    async def _drive():
        task = asyncio.create_task(client._hold_flush_loop())
        await asyncio.sleep(0.7)
        client._stop_ev.set()
        await asyncio.wait_for(task, timeout=2.0)

    client._on_text("好的呀")
    client._on_text("<")
    assert client._text_idle_deadline > 0, "扣留时未登记静默截止时间"
    assert "".join(cb.shown) == "好的呀", "静默未超时就不该释放"
    asyncio.run(_drive())
    # 尾巴里的 '<' 被安全网截断，但正常部分必须放出来（不能因为扣留就丢字）
    assert "".join(cb.shown) == "好的呀", f"静默兜底未正确释放: {cb.shown!r}"
    assert client._text_idle_deadline == 0.0, "释放后应清掉截止时间"
    print("PASS: 文本静默超时可兜底释放 holdback")


def test_bridge_defense_in_depth():
    """VoiceboxBridge 自身兜底：碎片既不朗读，也不会被 flush 成尾句念出来。"""
    spk = _FakeSpeaker()
    bridge = VoiceboxBridge(spk, max_wait=0.2)
    try:
        bridge.feed("你好。")
        bridge.feed("<<")
        bridge.feed("CALL_Q")
        time.sleep(0.5)          # 超过 max_wait，触发超时 flush 路径
        assert spk.spoken == ["你好。"], f"桥接层朗读了异常内容: {spk.spoken!r}"
        bridge.flush_remaining()  # 轮末兜底：悬空前缀应被丢弃而不是朗读
        time.sleep(0.3)
        assert spk.spoken == ["你好。"], f"flush_remaining 把令牌碎片念出来了: {spk.spoken!r}"
    finally:
        bridge.stop()
    print("PASS: VoiceboxBridge 纵深防御生效（碎片零朗读）")


def test_bridge_does_not_stall_on_holdback():
    """扣留尾巴不能让句子卡住：安全区该在超时后正常播出去。"""
    spk = _FakeSpeaker()
    bridge = VoiceboxBridge(spk, max_wait=0.2)
    try:
        bridge.feed("今天天气不错")
        bridge.feed("<")           # 尾巴被扣留，但「今天天气不错」必须照常播出
        deadline = time.time() + 2.0
        while time.time() < deadline and not spk.spoken:
            time.sleep(0.05)
        assert spk.spoken == ["今天天气不错"], f"安全区被 holdback 卡住: {spk.spoken!r}"
    finally:
        bridge.stop()
    print("PASS: holdback 不阻塞安全区播出")


# ============================ P0-b ============================
def test_take_audio_watermark_drops_oldest():
    """超水位时丢最旧的音频，保住实时性（宁可断点，也不要半分钟前的回答）。"""
    client, _, _ = _make_client(max_buf_secs=1.0, flow_control=False)
    sr = 16000
    # 塞入 3 秒音频：每秒用不同的 float32 常量标记，便于识别留下的是哪一段
    frames = []
    for sec in range(3):
        frames.append(b"".join(
            int.to_bytes(0, 4, "little", signed=True) for _ in range(sr)
        ) if sec == 0 else b"")
    # 用真实数值构造：第 0 秒全是 0.1，第 1 秒 0.2，第 2 秒 0.3
    import numpy as np
    buf = bytearray()
    for val in (0.1, 0.2, 0.3):
        arr = np.full(sr, val, dtype=np.float32)
        buf.extend(arr.tobytes())
    with client._mic_lock:
        client._mic_buf = buf
    audio = client._take_audio()
    got_secs = len(audio) / 4 / sr
    assert abs(got_secs - 1.0) < 0.01, f"水位裁剪长度不对: {got_secs:.3f}s"
    arr = np.frombuffer(audio, dtype=np.float32)
    assert abs(float(arr[0]) - 0.3) < 1e-6, f"应保留最新的音频，实际首样本={float(arr[0])}"
    assert client._dropped_secs > 1.9, f"丢弃计数不对: {client._dropped_secs:.2f}s"
    print("PASS: 超水位丢最旧、留最新")


def test_take_audio_no_trim_under_watermark():
    """未超水位不裁剪，音频一字节都不能少。"""
    import numpy as np
    client, _, _ = _make_client(max_buf_secs=1.2, flow_control=False)
    arr = np.zeros(16000, dtype=np.float32)      # 恰好 1.0 秒
    with client._mic_lock:
        client._mic_buf = bytearray(arr.tobytes())
    audio = client._take_audio()
    assert len(audio) == len(arr.tobytes()), "未超水位却被裁剪"
    assert client._dropped_secs == 0.0
    print("PASS: 未超水位不裁剪")


def test_flow_control_waits_for_terminal_event():
    """背压：上一段没被消费完就不放行下一段（收到终局事件立即放行）。"""
    client, _, _ = _make_client(flow_control=True, chunk_wait_timeout=1.0)
    client.push_interval = 0.4

    async def _scenario():
        client._chunk_done_ev = asyncio.Event()
        # 情况 1：事件未置位 → 必须等（0.3s 后置位，等待应约等于 0.3s 而不是固定 0.4s 节拍）
        ev = client._chunk_done_ev
        ev.clear()
        loop = asyncio.get_running_loop()
        loop.call_later(0.3, client._signal_chunk_done)
        t0 = time.monotonic()
        await client._wait_chunk_slot()
        waited = time.monotonic() - t0
        assert 0.25 <= waited <= 0.6, f"背压等待异常: {waited:.3f}s"
        assert not ev.is_set(), "放行后事件应被清空，避免下轮误判"
        # 情况 2：服务端不回任何终局事件 → 按 chunk_wait_timeout（1.0s）超时兜底，不死等
        t0 = time.monotonic()
        await client._wait_chunk_slot()
        waited = time.monotonic() - t0
        assert 0.8 <= waited <= 1.5, f"超时兜底失效: {waited:.3f}s"
        client._chunk_done_ev = None

    asyncio.run(_scenario())
    print("PASS: 背压按终局事件放行，超时兜底不死等")


def test_flow_control_disabled_uses_fixed_cadence():
    """关闭背压时退回固定节拍（与旧行为一致，便于对照真机表现）。"""
    client, _, _ = _make_client(flow_control=False)
    client.push_interval = 0.25

    async def _scenario():
        t0 = time.monotonic()
        await client._wait_chunk_slot()
        waited = time.monotonic() - t0
        assert 0.2 <= waited <= 0.5, f"固定节拍失效: {waited:.3f}s"

    asyncio.run(_scenario())
    print("PASS: 关闭背压时退回固定节拍")


def test_env_kill_switch():
    """环境变量 OMNI_FLOW_CONTROL=0 应能关闭背压（真机对照排查用）。"""
    os.environ["OMNI_FLOW_CONTROL"] = "0"
    try:
        client, _, _ = _make_client(flow_control=True)
        assert client._flow_control is False, "环境变量未能关闭背压"
    finally:
        os.environ.pop("OMNI_FLOW_CONTROL", None)
    print("PASS: 背压环境变量开关生效")


# ==================== A: 终局事件按 response_id 去重（三轮） ====================
def test_chunk_done_deduped_by_response_id():
    """真机回归（三轮）：服务端「一段」会发两个终局事件且共用同一 response_id。

    `ws_handler.cpp:1173`（listen delta）与 `:1232`（response.done）传的是同一个
    response_id。若两个都当「本段完成」去放行下一段，一段会被算两次 →
    推流次数翻倍、每块变小 → 服务端每轮都要重做图像编码（VPM 190ms，与块大小无关）
    → 真实消费速率腰斩 → 积压超水位丢帧（真机累计丢 5.1s，音频被切碎成残句）。
    """
    client, _, _ = _make_client()

    async def _scenario():
        client._chunk_done_ev = asyncio.Event()
        ev = client._chunk_done_ev
        ev.clear()
        client._signal_chunk_done("sess_resp_1")
        assert ev.is_set(), "首个终局事件应放行下一段"
        ev.clear()
        client._signal_chunk_done("sess_resp_1")       # 同一段的第二个终局事件
        assert not ev.is_set(), "同一 response_id 的第二个终局事件不得重复放行"
        client._signal_chunk_done("sess_resp_2")       # 新一段
        assert ev.is_set(), "新段的终局事件应放行"
        client._chunk_done_ev = None

    asyncio.run(_scenario())
    print("PASS: 同一 response_id 的两个终局事件只放行一次")


def test_receiver_counts_one_completion_per_chunk():
    """端到端计数：假 WS 回「listen + response.done 同 id」时，只应计到 2 次完成（2 段）。"""
    client, rec, cb = _make_client()
    events = [
        {"type": "response.output.delta", "kind": "text", "text": "好的。"},
        {"type": "response.output.delta", "kind": "listen", "response_id": "s_resp_7"},
        {"type": "response.done", "text": "好的。", "response_id": "s_resp_7"},
        {"type": "response.output.delta", "kind": "text", "text": "嗯。"},
        {"type": "response.output.delta", "kind": "listen", "response_id": "s_resp_8"},
        {"type": "response.done", "text": "嗯。", "response_id": "s_resp_8"},
    ]
    accepted = []

    async def _scenario():
        client._chunk_done_ev = asyncio.Event()
        orig_signal = client._signal_chunk_done      # 先抓住原方法，避免自递归

        def _spy(rid=None):
            ev = client._chunk_done_ev
            ev.clear()                       # 模拟推流协程已消费本次放行
            orig_signal(rid)
            if ev.is_set():
                accepted.append(rid)

        client._signal_chunk_done = _spy
        await client._receiver_loop(_FakeWS(events))
        client._chunk_done_ev = None

    asyncio.run(_scenario())
    assert accepted == ["s_resp_7", "s_resp_8"], \
        f"每段应只计一次完成，实际放行 {accepted}（重复放行会导致推流翻倍→积压丢帧）"
    print("PASS: 每段只产生一次背压放行")


# ==================== B: 推流下限（三轮） ====================
def test_wait_min_chunk_waits_for_enough_audio():
    """最小块时长：缓冲不足时先等攒够，够了一刻不等——防「极小块白烧一轮服务端算力」。"""
    import numpy as np
    client, _, _ = _make_client()
    client._min_chunk_secs = 0.2

    async def _scenario():
        t0 = time.monotonic()
        await client._wait_min_chunk()
        empty_wait = time.monotonic() - t0
        assert 0.15 <= empty_wait <= 0.45, f"空缓冲应等待攒块（上限 min+0.1s），实际 {empty_wait:.2f}s"
        with client._mic_lock:
            client._mic_buf = bytearray(
                np.zeros(int(16000 * 0.3), dtype=np.float32).tobytes()
            )
        t0 = time.monotonic()
        await client._wait_min_chunk()
        assert time.monotonic() - t0 < 0.05, "缓冲已够时不应再等"

    asyncio.run(_scenario())
    print("PASS: 最小块时长生效（不足则等、足够即走）")


def test_min_push_interval_blocks_flood():
    """最小推流间隔：终局信号提前到达时也不许比 push_interval 更密。"""
    client, _, _ = _make_client(flow_control=True, chunk_wait_timeout=0.2)
    client.push_interval = 0.4
    client._min_push_interval = 0.3

    async def _scenario():
        client._chunk_done_ev = asyncio.Event()
        client._chunk_done_ev.set()          # 信号一直就绪（最坏情况：历史信号积压）
        client._last_push_ts = time.monotonic()
        t0 = time.monotonic()
        await client._wait_chunk_slot()
        waited = time.monotonic() - t0
        assert 0.25 <= waited <= 0.7, f"最小间隔未生效，实际只等了 {waited:.3f}s"
        client._chunk_done_ev = None

    asyncio.run(_scenario())
    print("PASS: 最小推流间隔拦住小块洪泛")


# ==================== D: 人声判据（三轮） ====================
def test_speech_frame_judgement_table():
    """用真机日志实测数值做表驱动：判据必须能分开底噪与人声。"""
    client, _, _ = _make_client()
    # (RMS, 峰值, 是否人声) —— 数值取自 2026-09-13 真机日志
    cases = [
        (0.002, 0.010, False),   # 底噪下沿
        (0.013, 0.045, False),   # 底噪上沿（旧 RMS-only 判据在 0.02 附近会抖）
        (0.020, 0.088, True),    # 人声下沿（旧判据 = 边界值）
        (0.021, 0.162, True),    # 真机实际人声帧
        (0.043, 0.205, True),
        (0.056, 0.229, True),
    ]
    for rms, peak, expect in cases:
        got = client._is_speech_frame(rms, peak)
        assert got is expect, f"RMS={rms} 峰值={peak} 判为 {got}，期望 {expect}"
    # 峰值阈值必须低于真机人声峰值下沿、高于底噪峰值上沿
    assert 0.045 < client._speech_peak_th < 0.088, \
        f"峰值阈值 {client._speech_peak_th} 未落在可分离区间"
    print("PASS: 人声判据（峰值 或 RMS）在真机数值上全部分类正确")


def test_real_speech_not_misjudged_as_hallucination():
    """真机回归（三轮 Q1）：真实提问在链路延迟后到达，不得被判成静音期幻觉。

    真机症状：人声 RMS 仅 0.021（旧判据在 0.02 边缘抖），且令牌到达时距上次人声
    已超过旧窗口 3.0s（积压 + 服务端一轮 + TTS 排队的端到端延迟），于是升级被拦、
    用户请求静默丢失。现窗口放宽到 6.0s、判据改峰值。
    """
    client, rec, cb = _make_client()
    client._last_speech_ts = time.monotonic() - 4.5      # 4.5s 前说过话
    client._on_text("<<CALL_QWEN>>查一下这台电脑的电池电量。")
    assert cb.tasks == ["查一下这台电脑的电池电量"], \
        f"真实提问被误判为幻觉而拦截: {cb.tasks}"
    assert client._speech_window >= 6.0, "窗口应放宽以覆盖端到端延迟"
    print("PASS: 链路延迟内的真实提问可正常触发升级")


def test_genuine_silence_hallucination_still_blocked():
    """反向保护：完全没人说过话时的幻觉令牌仍必须拦住（不能为了修误杀而放开）。"""
    client, rec, cb = _make_client()
    client._last_speech_ts = 0.0                          # 从未检测到人声
    client._on_text("<<CALL_QWEN>>查一下天气。")
    assert cb.tasks == [], f"静音期幻觉不得升级: {cb.tasks}"
    # 超过窗口的真实人声（如 20 秒前说过）也不该为当前令牌背书
    client._reset_escalation_state()
    client._last_speech_ts = time.monotonic() - 20.0
    client._on_text("<<CALL_QWEN>>查一下天气。")
    assert cb.tasks == [], f"过期人声不得为令牌背书: {cb.tasks}"
    print("PASS: 静音期/过期人声的幻觉令牌仍被拦住")


# ==================== E: 回声门控判定（三轮） ====================
def test_echo_gate_decision_uses_both_devices():
    """门控 auto 判定必须同时看输入与输出：输入本身是耳机时不得关门控（会自激）。"""
    from unittest.mock import patch
    cases = [
        # (输入设备名, 输出设备名, 期望门控, 说明)
        ("MacBook Pro麦克风", "昊原的AirPods", False, "耳机输出 + 独立麦克风 → 硬件隔离，可关"),
        ("昊原的AirPods", "昊原的AirPods", True, "输入也是耳机 → 耳机麦会采到自己，必须开"),
        ("MacBook Pro麦克风", "内建扬声器", True, "扬声器外放 → 必须开"),
        ("", "", True, "设备未知 → 保守开启"),
    ]
    for in_name, out_name, expect, why in cases:
        with patch("src.omni.client.detect_audio_devices", return_value=(in_name, out_name)):
            gate, reason = resolve_echo_gate("auto")
        assert gate is expect, f"{why}: 期望门控={expect}，实际={gate}（{reason}）"
        assert reason.startswith("自动检测")
    print("PASS: 门控自动判定同时考虑输入与输出设备")


def test_echo_guard_excludes_playback_window_without_gate():
    """自身播报窗口的排除不再随门控开关短路（真机三轮）。"""
    os.environ.pop("OMNI_ECHO_GUARD", None)
    try:
        client, rec, cb = _make_client(echo_gate=False)
        from src.audio import playback
        client._last_speech_ts = time.monotonic()
        playback.mark_external_playback(2.0)
        client._on_text("<<CALL_QWEN>>打开浏览器。")
        assert cb.tasks == [], f"自身播报窗口内不得升级: {cb.tasks}"
        playback.reset_playback_state()
    finally:
        os.environ.pop("OMNI_ECHO_GUARD", None)
    print("PASS: 门控关闭时仍排除自身播报窗口")


# ==================== P1: 图像降频（1 帧/秒） ====================
def test_video_interval_decimates_frames():
    """图像降频：音频照常每段都上，图像按 video_interval 抽帧。"""
    client, _, _ = _make_client(video_interval=1.0)
    assert client.video_interval == 1.0
    assert client._frames_sent == 0
    # 第一段必带图（模型需要初始画面）
    assert client._should_attach_frame(1000.0) is True
    assert client._frames_sent == 1
    # 间隔内的段不带图
    assert client._should_attach_frame(1000.3) is False
    assert client._should_attach_frame(1000.9) is False
    # 跨过间隔后带图
    assert client._should_attach_frame(1001.0) is True
    assert client._frames_sent == 2
    print("PASS: 图像按间隔抽帧、首段必带图")


def test_video_interval_long_run_average():
    """长期平均必须准确落在 1/间隔——块节奏不是间隔整数倍，故计时用累加而非赋值。"""
    client, _, _ = _make_client(video_interval=1.0)
    t0 = 1000.0
    chunks, frames = 50, 0
    for i in range(chunks):
        if client._should_attach_frame(t0 + i * 0.6):     # 真实块节奏约 0.6s/段
            frames += 1
    elapsed = 49 * 0.6
    rate = frames / elapsed
    assert 0.9 <= rate <= 1.1, f"长期平均帧率应≈1.0 帧/秒，实际 {rate:.2f}"
    print(f"PASS: 50 段/{elapsed:.1f}s 共带图 {frames} 帧 → 平均 {rate:.2f} 帧/秒")


def test_video_interval_zero_keeps_legacy_behavior():
    """video_interval<=0 退回旧行为（每段都带图），便于真机对照排查。"""
    client, _, _ = _make_client(video_interval=0)
    assert all(client._should_attach_frame(1000.0 + i * 0.05) for i in range(5)), \
        "<=0 时应每段都带图"
    print("PASS: video_interval<=0 退回每段带图")


def test_video_interval_env_override():
    """环境变量 OMNI_VIDEO_INTERVAL 可覆盖默认值。"""
    os.environ["OMNI_VIDEO_INTERVAL"] = "2.5"
    try:
        client, _, _ = _make_client()
        assert abs(client.video_interval - 2.5) < 1e-6, \
            f"环境变量未生效: {client.video_interval}"
    finally:
        os.environ.pop("OMNI_VIDEO_INTERVAL", None)
    print("PASS: 图像间隔环境变量生效")


if __name__ == "__main__":
    test_prefix_helper()
    test_split_token_fragments_never_spoken()
    test_plain_text_not_swallowed_by_holdback()
    test_turn_end_flushes_holdback()
    test_chunk_boundary_must_not_release_holdback()
    test_malformed_token_variants_never_leak()
    test_malformed_token_does_not_break_display_flow()
    test_safety_net_blocks_residual_marker()
    test_idle_flush_releases_holdback()
    test_bridge_defense_in_depth()
    test_bridge_does_not_stall_on_holdback()
    test_take_audio_watermark_drops_oldest()
    test_take_audio_no_trim_under_watermark()
    test_flow_control_waits_for_terminal_event()
    test_flow_control_disabled_uses_fixed_cadence()
    test_env_kill_switch()
    test_chunk_done_deduped_by_response_id()
    test_receiver_counts_one_completion_per_chunk()
    test_wait_min_chunk_waits_for_enough_audio()
    test_min_push_interval_blocks_flood()
    test_speech_frame_judgement_table()
    test_real_speech_not_misjudged_as_hallucination()
    test_genuine_silence_hallucination_still_blocked()
    test_echo_gate_decision_uses_both_devices()
    test_echo_guard_excludes_playback_window_without_gate()
    test_video_interval_decimates_frames()
    test_video_interval_long_run_average()
    test_video_interval_zero_keeps_legacy_behavior()
    test_video_interval_env_override()
    print("\n全部通过 ✅")
