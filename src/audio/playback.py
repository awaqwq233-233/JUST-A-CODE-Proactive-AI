"""J.A.C. 共享音频播放工具。

统一用平台系统播放器（macOS: afplay / Windows: PowerShell SoundPlayer /
Linux: aplay）播放 WAV 文件，避免 sounddevice 选错设备导致全程静音。
Qwen3-TTS 与 Voicebox 两套 TTS 引擎共用本模块，避免重复实现。
带结果日志，便于排查「有合成但听不到声音」的问题。
"""
import os
import platform
import subprocess
import threading
import time

PLATFORM = platform.system()
IS_WINDOWS = PLATFORM == 'Windows'
IS_MACOS = PLATFORM == 'Darwin'
IS_LINUX = PLATFORM == 'Linux'

# ============================================================
# 全局「正在出声」状态（供 OMNI 全双工做回声门控）
#
# 背景：J.A.C. 说话（TTS 外放）时，声音会被本机麦克风重新采集并推给 omni，
# omni 把「自己的声音」当成用户发言 → 自问自答 → 幻觉出 <<CALL_QWEN>> 任务
# （真机已复现：RMS 0.022 的回声被判为「检测到人声」，随后自己吐升级令牌）。
# 根治需要 WebRTC AEC，工程上的等价做法是「J.A.C. 说话时把麦克风当听不见」。
# 本模块是所有 TTS 播放的唯一出口，故在此统一维护播放状态，供采集侧查询。
# ============================================================
_play_lock = threading.Lock()
_play_count = 0            # 当前正在进行的 play_wav 调用数（>0 表示正在出声）
_last_play_end = 0.0       # 最近一次 play_wav 结束的 monotonic 时刻
_external_until = 0.0      # 外部播放（不经 play_wav 的音频流）预计结束时刻


def _play_begin():
    """内部：标记一次播放开始（play_wav 入口调用）。"""
    global _play_count
    with _play_lock:
        _play_count += 1


def _play_end():
    """内部：标记一次播放结束，并记录结束时刻（供拖尾保护计算）。"""
    global _play_count, _last_play_end
    with _play_lock:
        _play_count = max(0, _play_count - 1)
        _last_play_end = time.monotonic()


def is_playback_active() -> bool:
    """当前是否正在出声（TTS 播放中），供采集侧做回声门控。

    Returns:
        bool: 有 play_wav 正在阻塞播放，或外部播放窗口尚未结束，则为 True。
    """
    with _play_lock:
        return _play_count > 0 or time.monotonic() < _external_until


def seconds_since_playback_end() -> float:
    """距离最近一次播放结束已过去多少秒（从未播放过返回 inf）。

    用于「拖尾保护」：扬声器停了但房间内仍有混响 / 系统音频缓冲未排空，
    紧随其后的一小段采集仍应视为回声。
    """
    with _play_lock:
        end = max(_last_play_end, _external_until)
    if end <= 0.0:
        return float("inf")
    return max(0.0, time.monotonic() - end)


def mark_external_playback(seconds: float):
    """登记一段「不经 play_wav 的播放」（如 OMNI 原生 PyAudio 音频流）。

    Args:
        seconds: 这段音频预计持续秒数（用于推算结束时刻）。
    """
    global _external_until
    if seconds <= 0:
        return
    with _play_lock:
        _external_until = max(_external_until, time.monotonic() + seconds)


def reset_playback_state():
    """清空播放状态（仅供测试使用）。"""
    global _play_count, _last_play_end, _external_until
    with _play_lock:
        _play_count = 0
        _last_play_end = 0.0
        _external_until = 0.0


def play_wav(path):
    """播放 WAV 文件，返回是否播放成功（bool）。

    按平台选择系统播放器播放指定路径的 WAV：
      - macOS:  afplay（系统原生，最稳）
      - Windows: PowerShell Media.SoundPlayer（同步播放）
      - Linux:  aplay
    任何异常都会被吞掉并打印警告（绝不拖垮主程序），但以返回值告知上层
    播放是否成功——上层可据此决定是否切换到系统 TTS 兜底，避免「有合成却
    全程静音」的问题。

    播放期间会置位全局「正在出声」标志（见 is_playback_active），OMNI 全双工
    采集侧据此把麦克风门控为静音，防止 J.A.C. 听到自己说话而自言自语。
    """
    _play_begin()
    try:
        if IS_MACOS:
            r = subprocess.run(["afplay", path], capture_output=True, text=True)
            if r.returncode != 0:
                print(f"[警告] afplay 播放失败（{path}）：{r.stderr.strip()[:200]}")
                return False
            print(f"[TTS] 播放完成: {path}")
            return True
        elif IS_WINDOWS:
            ps = f'(New-Object Media.SoundPlayer("{path}")).PlaySync()'
            r = subprocess.run(["powershell", "-Command", ps], capture_output=True, text=True)
            if r.returncode != 0:
                print(f"[警告] Windows 播放失败（{path}）：{r.stderr.strip()[:200]}")
                return False
            return True
        elif IS_LINUX:
            r = subprocess.run(["aplay", path], capture_output=True, text=True)
            if r.returncode != 0:
                print(f"[警告] aplay 播放失败（{path}）：{r.stderr.strip()[:200]}")
                return False
            return True
        else:
            print(f"[播放] {path}")
            return True
    except Exception as e:
        print(f"[警告] WAV 播放失败: {e} ({path})")
        return False
    finally:
        _play_end()
