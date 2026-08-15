"""MiniCPM-o-4_5 全双工最小闭环演示（CLI，不依赖 GUI）。

用途：在终端直接验证 OMNI 模式「能对话、能播 omni 语音、视频预览」，
无需启动 PySide6 界面。适合 bo s s 真机（带摄像头 + 麦克风）快速验收。

流程：
  1. 按需启动 llama-omni-server（Q8_0）。
  2. 启动 OmniClient 全双工会话（声纹克隆 + 实时推流）。
  3. 控制台实时打印 omni 文本；omni 语音通过扬声器播放；
     可选 OpenCV 窗口预览摄像头（--preview）。
  4. Ctrl+C 优雅退出。

用法：
  python -m src.omni                       # 默认自动起服务 + 全双工
  python -m src.omni --no-auto-launch      # 假定服务已在跑（9060）
  python -m src.omni --no-play             # 不播放 omni 语音（避免回授啸叫）
  python -m src.omni --preview             # 额外开 OpenCV 窗口看摄像头
  python -m src.omni --url ws://... --model-dir /path
"""
import argparse
import os
import sys
import threading
import time

# 让脚本可直接 `python -m src.omni` 运行（把项目根加入 sys.path）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from src.omni.client import OmniClient, OmniCallbacks  # noqa: E402
from src.omni.server_launcher import OmniServerLauncher  # noqa: E402
from src.omni.prompts import SYSTEM_PROMPT  # noqa: E402


class _ConsoleCallbacks(OmniCallbacks):
    """演示用回调：把 omni 文本/状态打印到控制台。"""

    def __init__(self, client=None):
        # 聆听态去重：只在「刚进入聆听」时打印一次，避免 listen 事件每帧刷屏
        self._was_listening = False
        # 已通过 on_text_delta 打印的字符数（用于 on_text_final 去重，避免每轮 done 刷屏「本轮结束」）
        self._printed_len = 0
        # 持有 omni 客户端引用（升级结果需经其回灌播报）与懒创建的升级路由器
        self._client = client
        self._router = None

    def on_state(self, state, info=None):
        print(f"\n[omni] 状态: {state}" + (f" ({str(info)[:8]}...)" if info else ""))

    def on_text_delta(self, text):
        # 出现文本即视为离开聆听态，收尾聆听行并重置标志
        if self._was_listening:
            self._was_listening = False
            print()
        # 把升级令牌 <<CALL_QWEN>> 替换成友好前缀，避免把原始令牌字面吐到控制台
        display = text.replace("<<CALL_QWEN>>", "[升级→大脑] ")
        print(display, end="", flush=True)
        self._printed_len += len(text)

    def on_text_final(self, text):
        # full_duplex 下 response.done 的文本通常与 delta 累积一致（冗余）；
        # 仅当 final 文本比已打印更长（delta 漏发）时补印缺失部分，否则静默，避免噪声。
        if text and len(text) > self._printed_len:
            extra = text[self._printed_len:]
            if extra.strip():
                print(extra, end="", flush=True)
        if text:
            self._printed_len = len(text)

    def on_listen(self):
        if not self._was_listening:
            self._was_listening = True
            print("\n[omni] 🎧 聆听中…", end="", flush=True)

    def on_call_qwen(self, task):
        # 升级令牌触发点：打印以便真机验收时直观看到路由是否被激活
        print(f"\n[omni] ⚡ 升级令牌触发: {task}", flush=True)
        # 真机闭环：后台线程跑 qwen+tools 升级，结果经 omni turn_based 回灌播报
        self._start_escalation(task)

    def _start_escalation(self, task):
        """后台线程执行升级路由（qwen+tools 同步阻塞，绝不能阻塞 omni 接收循环）。

        流程：懒创建 EscalationRouter → escalate 拿结果 → 经 omni 临时 turn_based
        会话用 JAC 克隆声纹回灌播报 → 标记升级完成（解除主会话静音）。
        任何异常都转成兜底播报，不让本轮升级静默失败。
        """
        def _worker():
            try:
                from src.omni.router import EscalationRouter
                if self._router is None:
                    self._router = EscalationRouter()
                # run_agentic 流式 yield 最终回答文本（打字机效果），on_progress 推到控制台
                result = self._router.escalate(
                    task, on_progress=lambda c: print(c, end="", flush=True)
                )
                print()  # 结束流式输出换行
                if self._client is not None and self._client.is_running():
                    if result:
                        self._client.speak_result_via_turnbased(f"（升级结果）{result}")
                    else:
                        self._client.speak_result_via_turnbased(
                            "抱歉 boss，升级通道暂时拿不到结果，我稍后再试。"
                        )
                else:
                    print("[omni] 客户端已不可用，跳过回灌播报。")
            except Exception as e:  # noqa: BLE001
                print(f"\n[omni] 升级异常: {e}")
                if self._client is not None and self._client.is_running():
                    try:
                        self._client.speak_result_via_turnbased("抱歉 boss，升级处理出错了。")
                    except Exception:  # noqa: BLE001
                        pass
            finally:
                if self._client is not None:
                    self._client.mark_escalation_done()
        threading.Thread(target=_worker, daemon=True, name="omni-escalation").start()

    def on_error(self, err):
        print(f"\n[omni] ⚠️ 错误: {err}")


def _preview_thread(client: OmniClient, stop_ev: threading.Event):
    """可选：OpenCV 窗口实时预览摄像头（从 client 最新帧缓存取）。"""
    import cv2
    try:
        while not stop_ev.is_set():
            with client._latest_jpg_lock:
                jpg = client._latest_jpg
            if jpg is not None:
                arr = np_from_jpg(jpg)
                if arr is not None:
                    cv2.imshow("J.A.C. OMNI 预览", arr)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break
            time.sleep(0.05)
    finally:
        cv2.destroyAllWindows()


def np_from_jpg(jpg_bytes: bytes):
    """jpeg 字节 → numpy BGR 数组（用于预览）。"""
    import numpy as np
    import cv2
    arr = np.frombuffer(jpg_bytes, dtype=np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def main():
    """演示入口。"""
    ap = argparse.ArgumentParser(description="J.A.C. OMNI 全双工演示")
    ap.add_argument("--url", default="ws://127.0.0.1:9060/backend")
    ap.add_argument("--model-dir",
                    default=os.path.expanduser(
                        "~/Desktop/work/coding/jac_omni_backend/models/MiniCPM-o-4_5-gguf"))
    ap.add_argument("--quant", default="Q8_0")
    ap.add_argument("--ref", default=os.path.join(_PROJECT_ROOT, "voices", "silverwalf_voice.wav"))
    ap.add_argument("--no-auto-launch", action="store_true", help="不自动启动服务（假定已在跑）")
    ap.add_argument("--no-play", action="store_true", help="不播放 omni 语音")
    ap.add_argument("--preview", action="store_true", help="额外开 OpenCV 窗口预览摄像头")
    args = ap.parse_args()

    # 1) 启动服务
    launcher = OmniServerLauncher(
        port=9060, model_dir=args.model_dir, quant=args.quant,
    )
    if not args.no_auto_launch:
        if not launcher.start(wait=True, timeout=180):
            print("[演示] 服务启动失败，退出。")
            return 1

    # 2) 启动客户端：先建 client 再把引用注入回调（令牌触发时回调需回灌播报）
    client = OmniClient(
        url=args.url,
        ref_audio_path=args.ref,
        system_prompt=SYSTEM_PROMPT,
        enable_playback=not args.no_play,
    )
    cb = _ConsoleCallbacks(client)
    # client 内部读的是 self.cb（__init__: self.cb = callbacks or OmniCallbacks()）；
    # 若挂到 client.callbacks 是另一个属性、client 永远读不到，会导致🎧/文本/令牌回调全部静默。
    client.cb = cb
    print("[演示] 正在连接 omni 并等待模型加载完成（约 10~60s），请耐心等待，"
          "出现「🎧 聆听中…」才算真正就绪…")
    if not client.start(timeout=180):
        print("[演示] 客户端未能就绪，退出。")
        launcher.stop()
        return 1

    # 3) 可选预览线程
    preview_stop = threading.Event()
    pt = None
    if args.preview:
        pt = threading.Thread(target=_preview_thread, args=(client, preview_stop), daemon=True)
        pt.start()

    print("\n[演示] 全双工已就绪，请对着麦克风说话（推荐戴耳机避免回授啸叫）。Ctrl+C 退出。\n")
    try:
        while client.is_running():
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[演示] 收到退出信号。")
    finally:
        client.stop()
        preview_stop.set()
        launcher.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
