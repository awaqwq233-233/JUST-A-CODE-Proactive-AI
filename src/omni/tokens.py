"""升级令牌 `<<CALL_QWEN>>` 的识别与安全过滤（client 与 voicebox_bridge 共用）。

为什么单独抽一个模块：这两个模块都需要「找令牌 / 扣留令牌前缀 / 过滤残留标记」，
而 `client.py` 已经 import `voicebox_bridge.py`，把工具塞进任何一侧都会造成职责错位或
循环依赖。集中在这里也让「令牌长什么样」只有一个事实来源。

⚠️ 真机铁律（2026-09-13，bo s s 实测确认）：**模型并不总能原样吐出 `<<CALL_QWEN>>`**。
真机日志里出现了 `<<CALL_ QWEN>>`（中间夹空格与换行），bo s s 已确认该空格是模型自己
生成的。任何「精确字符串匹配」的实现（`find` / `==`）都会漏掉这类变体，后果是：
  1. 畸形令牌被当普通对话**显示并朗读**（Voicebox 反复念同一句）；
  2. 扣留逻辑也失效——`<<CALL_` 是合法前缀会被扣住，但下一个字符是空格就不再是前缀，
     于是被当作"安全文本"外发。
所以这里提供三件套：
  - `find_call_token()`：容忍空白/换行/大小写的令牌查找；
  - `token_prefix_suffix_len()`：同样容忍空白的「疑似令牌前缀」判定（供扣留用）；
  - `sanitize_for_speech()`：硬安全网——含 `<` 或 `>>` 的片段一律不外发/不朗读。
    语音助手的正常输出里不可能出现尖括号，宁可少说几个字也不念出内部标记。
"""
import re

# 升级令牌（内部指令，绝不朗读、绝不显示）
CALL_TOKEN = "<<CALL_QWEN>>"

# 容忍空白/换行/大小写的令牌正则：模型可能吐出 `<<CALL_ QWEN>>`、`<< CALL_QWEN >>` 等
CALL_TOKEN_RE = re.compile(r"<<\s*C\s*A\s*L\s*L\s*_\s*Q\s*W\s*E\s*N\s*>>", re.IGNORECASE)

# 判定「疑似令牌前缀」时按令牌长度的 2 倍取样尾部（容忍中间夹空白）
_MAX_TAIL = 2 * len(CALL_TOKEN)

# 硬安全网的触发字符：语音正常输出里不会出现的尖括号（`<<`/`>>` 以及落单的 `<`/`>`）
_MARKER_CHARS = ("<", ">")

# 标记残留「词」过滤：段边界会把令牌切成只剩 `QWEN` / `CALL_` 这种裸词，
# 它们本身不含尖括号，靠 _MARKER_CHARS 截不到——按「是令牌的子串且长度≥3」判定并删除。
_RESIDUE_WORD_RE = re.compile(r"[A-Za-z_]{3,}")
_TOKEN_UPPER = re.sub(r"[^A-Z_]", "", CALL_TOKEN.upper())


def find_call_token(text: str):
    """在文本里查找升级令牌（容忍空白/换行/大小写）。

    Args:
        text: 待查找的累积文本。

    Returns:
        re.Match | None: 命中则返回匹配对象（`m.start()` / `m.end()` 可直接用于切片），
        未命中返回 None。
    """
    if not text:
        return None
    return CALL_TOKEN_RE.search(text)


def token_prefix_suffix_len(s: str) -> int:
    """返回 s 末尾「可能是升级令牌前缀」的字符数（0=尾巴安全，可立即外发）。

    P0-a 根因（2026-09-13 真机复现）：服务端 `make_text_delta` 是**按 token 逐片**下发
    文本的，`<<CALL_QWEN>>` 必然被切成 `<<CALL_Q` 这类碎片；碎片到达那一刻缓冲里还没有
    完整令牌，于是走了「正常对话」分支被广播并朗读出去（Voicebox 里那段 `<<CALL_Q` 怪音
    就是这么来的）。本函数用于 holdback：把这些「可能是令牌前缀」的尾部字符扣在缓冲里，
    等下一片 delta 到齐确认构不成令牌之后再放行。

    与 `CALL_TOKEN_RE` 保持一致：判定时**忽略空白并忽略大小写**，否则 `<<CALL_ ` 这种
    带尾巴空格的畸形前缀会被误认为安全文本放出去。

    Args:
        s: 待检查的文本缓冲。

    Returns:
        int: 需要扣留在末尾的字符数（0 ~ len(CALL_TOKEN)-1）。
    """
    if not s:
        return 0
    seg = s[-_MAX_TAIL:]
    upper = CALL_TOKEN.upper()
    # 从长到短找：只有「去掉空白后是令牌前缀」的尾巴才需要扣留
    for n in range(len(seg), 0, -1):
        flat = re.sub(r"\s+", "", seg[-n:]).upper()
        if not flat or len(flat) >= len(upper):
            continue
        if upper.startswith(flat):
            return n
    return 0


def sanitize_for_speech(text: str) -> str:
    """硬安全网：截掉含尖括号标记的片段，并抹掉裸标记词，确保绝不朗读/显示内部标记。

    为什么需要（真机实证）：模型会吐出畸形令牌（`<<CALL_ QWEN>>`），若某一轮
    检测/扣留刚好没覆盖到（例如段边界切断了前缀），残留就会作为普通台词被
    Voicebox 念出来——真机日志里同一句被反复朗读就是这个现象。两道网：
      1. 从第一个 `<` / `>>` 起截断（语音正常输出不含这些字符）；
      2. 抹掉「是令牌子串且长度≥3」的裸词（如 `QWEN`、`CALL_`）——段边界会把
         令牌切得只剩这种裸词，它们本身不含尖括号，第一道网截不到。

    Args:
        text: 待外发的文本片段。

    Returns:
        str: 已清洗的安全文本（可能为空串，调用方应跳过空串）。
    """
    if not text:
        return text
    cut = len(text)
    for marker in _MARKER_CHARS:
        i = text.find(marker)
        if 0 <= i < cut:
            cut = i
    safe = text[:cut] if cut < len(text) else text
    if not safe:
        return safe
    return _RESIDUE_WORD_RE.sub(
        lambda m: "" if m.group(0).upper() in _TOKEN_UPPER else m.group(0), safe
    )
