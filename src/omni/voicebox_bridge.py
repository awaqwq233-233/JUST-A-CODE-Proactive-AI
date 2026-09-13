"""M7b 句子级流式桥接：把 omni 的 text delta 攒成句，逐句送 Voicebox 克隆合成 + 播放。

为什么需要：omni 全双工自带的 TTS 无 JAC 克隆声纹、音质差（"响一下就结束"）。
业界标准做法是用「句子级流式」替代 token 级：Voicebox 是整句合成引擎，没有 token
级增量接口，故不按 token 硬切，而是按标点 / 句子边界把 omni 的 text delta 攒成句，
攒够一句（遇句号 / 问号 / 换行）就送 Voicebox 合成这一句并播放，下一句继续攒。

效果：保留「说一句听一句」的近似实时感 + 拿到 JAC 克隆声纹；代价是每句多一个
「攒句 + 合成」延迟（通常几十 ~ 几百 ms，人耳基本无感，因为人说话本身也有停顿）。
音质 / 克隆远好于 omni 自带 TTS。

线程模型：feed() 在 omni 接收协程（asyncio 事件循环）里调用，只做字符串累积 + 切句
入队，绝不阻塞；合成 + 播放在独立 daemon 播放线程里串行执行（Voicebox 的 speak 是
阻塞的，且必须保证句序，否则句子会乱序播报）。
"""
import queue
import threading
import time

from .tokens import (
    find_call_token,
    sanitize_for_speech,
    token_prefix_suffix_len as _token_prefix_suffix_len,
)

# 句末标点集合（遇到即切句）
_SENT_END = set("。！？!?；;\n")
# 单句最大字数（超长强制切，防 omni 迟迟不给标点导致主对话卡住）
_MAX_CHARS = 40
# 尾句最大等待秒数（距上一句超过此值无新 delta 也 flush，防最后一句永不触发）
_MAX_WAIT = 2.0


class VoiceboxBridge:
    """句子级攒句 → Voicebox 克隆合成 + 顺序播放的桥接器。"""

    def __init__(self, speaker, max_chars: int = _MAX_CHARS, max_wait: float = _MAX_WAIT):
        """初始化桥接器。

        Args:
            speaker: 具备 speak(text) 方法的合成器（鸭子类型，实际传 VoiceboxSpeaker），
                     内部用它做克隆合成 + 播放；为 None 时本桥接器等于空操作。
            max_chars: 单句强制切分字数上限。
            max_wait: 尾句超时 flush 秒数。
        """
        self._spk = speaker
        self._max_chars = max_chars
        self._max_wait = max_wait
        self._buf = ""                       # 当前攒句缓冲
        self._last_flush = time.time()       # 上次切句时间（用于尾句超时）
        self._q = queue.Queue()              # 待播句子队列（保序）
        self._stop = False
        self._play_thread = threading.Thread(
            target=self._run, daemon=True, name="voicebox-bridge"
        )
        self._play_thread.start()

    def feed(self, delta: str):
        """喂入 text delta：累积并按句边界切句入队（非阻塞，可在 asyncio 事件循环里调用）。

        三道防线（纵深防御，client 侧已做前置拦截，这里防止漏网）：
          1. 命中升级令牌（**容忍空白/换行/大小写**的变体，如真机出现过的
             `<<CALL_ QWEN>>`）→ 截断到令牌之前，绝不朗读"问题本身"；
          2. 末尾「疑似令牌前缀」的字符做 holdback，不参与切句、不入朗读队列，
             等下一片 delta 到齐确认不是令牌前缀后再放行；
          3. 入队前过 `sanitize_for_speech` 硬安全网——含 `<` / `>>` / 裸标记词的
             片段一律截掉（段边界会把令牌切成只剩 `QWEN` 这种裸碎片，必须挡住）。
        """
        if not delta:
            return
        self._buf += delta
        # 防线 1：完整令牌（含变体）→ 截到令牌之前
        m = find_call_token(self._buf)
        if m is not None:
            self._buf = self._buf[:m.start()]
        # 防线 2：holdback——扣掉「可能是令牌前缀」的尾巴，只在安全区（head）上切句
        hold = _token_prefix_suffix_len(self._buf)
        head = self._buf[:len(self._buf) - hold] if hold else self._buf
        # 按句末标点切句：每遇到一个标点，把之前的整句切出并入队
        while True:
            cut = -1
            for i, ch in enumerate(head):
                if ch in _SENT_END:
                    cut = i
                    break
            if cut < 0:
                break
            sentence = head[:cut + 1].strip()
            head = head[cut + 1:]
            if sentence:
                self._enqueue(sentence)
        # 超长强制切（防无标点长句卡住主对话）；仍在安全区内切，不含 holdback 尾巴
        if len(head) >= self._max_chars:
            sentence = head.strip()
            head = ""
            if sentence:
                self._enqueue(sentence)
        # 安全区剩余 + 扣留的尾巴一起回写缓冲
        self._buf = head + (self._buf[len(self._buf) - hold:] if hold else "")
        self._last_flush = time.time()

    def _enqueue(self, sentence: str):
        """防线 3：入朗读队列前过一遍硬安全网（含标记残留的片段一律丢弃）。"""
        safe = sanitize_for_speech(sentence)
        if not safe:
            print(f"[TTS] 🛡 已拦截疑似标记残留（不朗读）: {sentence!r}", flush=True)
            return
        self._q.put(safe)

    def _drain_tail(self) -> str:
        """取出缓冲里「可以正常朗读」的尾句，并**丢弃**疑似令牌前缀的尾巴。

        P0-a：`<<CALL_Q` 这类悬空的令牌碎片永远不会变成合法台词，只能丢弃；
        若把它当尾句 flush 出去，就会被 Voicebox 念出来（这正是真机复现的 bug）。
        """
        hold = _token_prefix_suffix_len(self._buf)
        if hold:
            self._buf = self._buf[:len(self._buf) - hold]
        tail = self._buf.strip()
        self._buf = ""
        return sanitize_for_speech(tail)

    def flush_remaining(self):
        """正常会话结束时调用：把缓冲里残留的尾句入队（不清空队列，让其自然播完）。"""
        tail = self._drain_tail()
        if tail:
            self._q.put(tail)

    def flush_and_stop(self):
        """升级令牌触发时调用：把残留尾句入队，然后清空未播队列（避免与回灌重叠）。

        当前正在播的句子由 speak() 自然播完（不强行中断，避免爆音）；队列里尚未播放的
        主对话后续句子被丢弃，交给回灌通道播报升级结果，避免主对话尾巴与回灌声音重叠。
        """
        tail = self._drain_tail()
        if tail:
            self._q.put(tail)
        while not self._q.empty():
            try:
                self._q.get_nowait()
            except queue.Empty:
                break

    def _run(self):
        """播放线程：从队列取句 → 调 speaker.speak 合成 + 播放（阻塞，串行保序）。"""
        while not self._stop:
            try:
                sentence = self._q.get(timeout=0.2)
            except queue.Empty:
                # 超时尾句 flush：缓冲有残留且距上次切句超过 max_wait 仍无新 delta。
                # P0-a：只放行「安全区」（不含疑似令牌前缀的尾巴），否则句子会卡着不播；
                # 扣留的前缀继续等下一片确认——真到轮末由 flush_remaining 统一丢弃，
                # 所以悬空的 `<<CALL_Q` 永远不会被念出来。
                if self._buf and time.time() - self._last_flush >= self._max_wait:
                    hold = _token_prefix_suffix_len(self._buf)
                    head = self._buf[:len(self._buf) - hold] if hold else self._buf
                    self._buf = self._buf[len(self._buf) - hold:] if hold else ""
                    if head.strip():
                        self._enqueue(head.strip())
                continue
            if sentence is None:
                break
            try:
                if self._spk is not None:
                    self._spk.speak(sentence)
            except Exception:  # noqa: BLE001
                # 单句失败不影响后续；VoiceboxSpeaker.speak 内部已自带降级（系统 TTS）
                pass

    def stop(self):
        """停止桥接器（释放播放线程）。"""
        self._stop = True
        try:
            self._q.put(None)
        except Exception:  # noqa: BLE001
            pass
