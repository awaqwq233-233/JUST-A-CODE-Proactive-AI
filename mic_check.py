"""麦克风采集诊断脚本（一次性排障用，可随时删除）。

用途：用与 omni 客户端完全相同的参数（16k / float32 / mono）录制默认输入设备
约 4 秒，实时打印每段 RMS 与峰值，并给出「能否采到真实语音」的结论。

读取为全 0（RMS≈0）即证明：macOS 未授权麦克风 / 默认输入设备选错，
这正是「omni 全双工说话没反应」的根因。
"""
import numpy as np
import pyaudio

RATE = 16000       # 与 omni 客户端一致的采样率
CHUNK = 1024       # 与 omni 客户端一致的帧缓冲


def list_input_devices(p: pyaudio.PyAudio):
    """列出所有可用输入设备（名称/通道/默认采样率），便于排查默认设备选错。"""
    print("=== 可用输入设备 ===")
    for i in range(p.get_device_count()):
        d = p.get_device_info_by_index(i)
        if d.get("maxInputChannels", 0) > 0:
            print(f"  idx={i}  name={d['name']!r}  ch={d['maxInputChannels']}  "
                  f"sr={int(d['defaultSampleRate'])}")
    di = p.get_default_input_device_info()
    print(f"=== 系统默认输入设备: idx={di['index']}  name={di['name']!r} ===")
    return di["index"]


def main():
    """录制默认设备 4 秒，逐块打印 RMS/峰值，最后给结论。"""
    p = pyaudio.PyAudio()
    dev_idx = list_input_devices(p)
    try:
        stream = p.open(format=pyaudio.paFloat32, channels=1, rate=RATE,
                        input=True, input_device_index=dev_idx,
                        frames_per_buffer=CHUNK)
        stream.start_stream()
    except Exception as e:  # noqa: BLE001
        print(f"[错误] 打开麦克风失败: {e}")
        p.terminate()
        return

    print("\n录音 4 秒，请对着麦克风正常说话…\n")
    n_blocks = int(RATE / CHUNK * 4)   # 4 秒对应的块数
    peak_rms = 0.0
    for i in range(n_blocks):
        data = stream.read(CHUNK, exception_on_overflow=False)
        arr = np.frombuffer(data, dtype=np.float32)
        rms = float(np.sqrt(np.mean(arr * arr))) if arr.size else 0.0
        peak = float(np.max(np.abs(arr))) if arr.size else 0.0
        peak_rms = max(peak_rms, rms)
        bar = "#" * int(min(rms * 200, 40))
        print(f"  t={i * CHUNK / RATE:5.2f}s  RMS={rms:.5f}  peak={peak:.5f}  {bar}",
              flush=True)

    stream.stop_stream()
    stream.close()
    p.terminate()

    print("\n=== 结论 ===")
    if peak_rms < 1e-4:
        print("  ❌ 几乎全程静音（RMS≈0）：麦克风没有采到任何声音。")
        print("     请检查：① 系统设置→隐私与安全性→麦克风，给运行 python 的终端/IDE 授权；")
        print("            ② 系统设置→声音→输入，确认默认输入是「MacBook Pro 麦克风」而非聚集设备/外部设备；")
        print("            ③ 输入音量滑块是否拉到了最低。")
    else:
        print(f"  ✅ 麦克风能采到声音（峰值 RMS≈{peak_rms:.4f}），输入侧正常，")
        print("     若 omni 仍无反应，问题在 omni 推流/服务端，需进一步查 WS 链路。")


if __name__ == "__main__":
    main()
