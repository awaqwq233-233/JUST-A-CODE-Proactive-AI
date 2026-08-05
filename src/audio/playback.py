"""J.A.C. 共享音频播放工具。

统一用平台系统播放器（macOS: afplay / Windows: PowerShell SoundPlayer /
Linux: aplay）播放 WAV 文件，避免 sounddevice 选错设备导致全程静音。
Qwen3-TTS 与 Voicebox 两套 TTS 引擎共用本模块，避免重复实现。
带结果日志，便于排查「有合成但听不到声音」的问题。
"""
import os
import platform
import subprocess

PLATFORM = platform.system()
IS_WINDOWS = PLATFORM == 'Windows'
IS_MACOS = PLATFORM == 'Darwin'
IS_LINUX = PLATFORM == 'Linux'


def play_wav(path):
    """播放 WAV 文件，返回是否播放成功（bool）。

    按平台选择系统播放器播放指定路径的 WAV：
      - macOS:  afplay（系统原生，最稳）
      - Windows: PowerShell Media.SoundPlayer（同步播放）
      - Linux:  aplay
    任何异常都会被吞掉并打印警告（绝不拖垮主程序），但以返回值告知上层
    播放是否成功——上层可据此决定是否切换到系统 TTS 兜底，避免「有合成却
    全程静音」的问题。
    """
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
