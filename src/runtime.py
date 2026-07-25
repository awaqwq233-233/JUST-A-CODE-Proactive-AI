"""J.A.C. 运行时：把 main.py 的核心处理流程封装成可在后台线程启动/停止的对象。

GUI 只通过本类与底层交互：
  - start(config) / stop()        启动与停止整条流水线
  - manual_input(text)            把控制台文字送入对话（取代原 stdin 线程）
  - manual_wake()                 手动唤醒
  - on_state_change(running)      状态回调（GUI 用于锁定/解锁开关）

底层复用 main.py 的 handle_user_text / process_response / audio_thread_func /
handle_memory_command 与模块级全局状态，避免逻辑重复。
"""
import time
import threading
import logging

import cv2

import main  # 复用 main 的对话逻辑与全局状态（不调用 main()）

from src.capture.camera import Camera
from src.analysis.detector import VisionDetector
from src.audio.tts import Speaker
from src.audio.stt import SpeechRecognizer
from src.audio.recorder import AudioRecorder
from src.audio.qwen_tts import QwenTTSSpeaker, QWEN_TTS_AVAILABLE
from src.brain.llm import LocalBrain
from src.memory import MemoryManager
from src.judgment.judge import JudgmentEngine
from src.utils.config import Config


logger = logging.getLogger("runtime")


class JACRuntime:
    def __init__(self, context=None, on_state_change=None):
        # 使用 main 模块级全局 context（process_response / 音频线程都读它），
        # 保证视觉摘要与送入大模型的帧来自同一上下文。
        self.context = context if context is not None else main.context
        self.on_state_change = on_state_change  # callable(bool) -> None
        self.running = False
        self._vision_thread = None
        self._audio_stop = threading.Event()
        self._audio_thread = None
        self.judge_engine = None
        self.camera = None
        self.speaker = None
        self.brain = None
        self.memory = None
        self._fps = 0.0

    # ---------------------------------------------------------------- 生命周期
    def start(self, config: Config):
        if self.running:
            return
        main.running = True
        self.running = True
        self.config = config

        # 1) 摄像头（采集分辨率固定，绝不被 GUI 缩放影响）
        self.camera = Camera(
            camera_id=None,
            width=config.camera_width,
            height=config.camera_height,
        )
        if not self.camera.start():
            print("[系统] 摄像头启动失败，无法启动 J.A.C.")
            self.running = False
            main.running = False
            self._notify(False)
            return
        detector = VisionDetector()

        # 2) 扬声器：use_qwen_tts 开关决定（覆盖原隐式回退）
        speaker = None
        if config.use_qwen_tts and QwenTTSSpeaker is not None and QWEN_TTS_AVAILABLE:
            speaker = QwenTTSSpeaker()
        if speaker is None or not getattr(speaker, "available", False):
            speaker = Speaker()
        self.speaker = speaker
        if getattr(speaker, "_ensure_model", None) is not None and getattr(speaker, "available", False):
            threading.Thread(target=speaker._ensure_model, daemon=True, name="tts-preload").start()

        recognizer = SpeechRecognizer(model_size="tiny")
        recorder = AudioRecorder()
        self.brain = LocalBrain(
            model_path="models/Qwen3.5-9B-Q4_K_M.gguf",
            backend=config.brain_backend,
            lm_studio_model="qwen/qwen3.5-9b",
        )

        main.memory = MemoryManager(
            brain=self.brain,
            enabled=config.memory_enabled,
            capture_person_id=config.memory_capture_person_id,
        )

        # 3) 前导判断引擎（按开关 + 滑块）
        main.JUDGMENT_ACTIVATED = False
        if config.judgment_engine_enabled:
            je = JudgmentEngine(
                model_name=config.judgment_model_name,
                interval=config.judgment_interval,
                timeout=config.judgment_timeout,
            )
            je.set_context(self.context)
            if je.check_available():
                main.JUDGMENT_ACTIVATED = True
                print("[系统] 前导判断引擎已启用 (MiniCPM-o)")
            else:
                print("[系统] 前导判断引擎未就绪（未检测到判断模型），进入被动模式")
            self.judge_engine = je
            threading.Thread(target=je.run, daemon=True, name="judgment").start()
        else:
            print("[系统] 前导判断引擎已手动禁用")

        # 4) 音频线程（复用 main.audio_thread_func，内部读 main.running）
        self._audio_stop.clear()
        self._audio_thread = threading.Thread(
            target=main.audio_thread_func,
            args=(self.speaker, recognizer, recorder, self.brain, self._audio_stop),
            daemon=True,
        )
        self._audio_thread.start()

        # 5) 视觉主循环（后台线程，无 cv2.imshow）
        self._vision_thread = threading.Thread(
            target=self._vision_loop, args=(detector,), daemon=True, name="vision"
        )
        self._vision_thread.start()

        print("==========================================")
        print("      J.A.C.Prototype 已启动")
        print("==========================================")
        self._notify(True)

    def stop(self):
        if not self.running:
            return
        self.running = False
        main.running = False
        self._audio_stop.set()
        if self.judge_engine is not None:
            try:
                self.judge_engine.stop()
            except Exception:
                pass
        if self.camera is not None:
            try:
                self.camera.stop()
            except Exception:
                pass
        if self.memory is not None:
            try:
                self.memory.close()
            except Exception:
                pass
        print("[系统] J.A.C.Prototype 已停止。")
        self._notify(False)

    def _notify(self, running):
        if self.on_state_change is not None:
            try:
                self.on_state_change(running)
            except Exception:
                pass

    # ---------------------------------------------------------------- 主循环
    def _vision_loop(self, detector):
        frame_count, start_time = 0, time.time()
        try:
            while self.running and main.running:
                ret, frame = self.camera.get_frame()
                if not ret:
                    break

                annotated_frame, results = detector.detect(frame)

                # 关键：更新共享上下文
                self.context.update_vision(results)
                self.context.set_frame(frame)               # 原始无框帧（LLM 视觉问答用）
                self.context.set_annotated_frame(annotated_frame)  # 带框帧（GUI 显示用）

                # FPS
                frame_count += 1
                if frame_count >= 10:
                    self._fps = frame_count / (time.time() - start_time)
                    frame_count, start_time = 0, time.time()

                self._draw_overlay(annotated_frame)

                # 前导判断引擎介入检查
                if main.JUDGMENT_ACTIVATED and self.judge_engine is not None:
                    intervention = self.judge_engine.get_intervention()
                    if intervention is not None and not main.conversation_running:
                        print(f"[主动介入] 判断引擎: {intervention.reason}")
                        vision_info = self.context.get_vision_summary()
                        transcript_context = intervention.transcript
                        full_context = (
                            f"[系统主动介入] {intervention.reason}\n"
                            f"当前视觉: {vision_info}\n"
                            f"最近音频: {transcript_context}"
                        )
                        threading.Thread(
                            target=lambda ctx=full_context: main.process_response(
                                ctx, self.brain, self.speaker
                            ),
                            daemon=True,
                        ).start()

                time.sleep(0.001)
        except Exception as e:
            print(f"[错误] 视觉主循环异常: {e}")
        finally:
            # 循环退出即触发一次清理（保活），仅在仍处于运行态时
            if self.running:
                self.stop()

    def _draw_overlay(self, frame):
        """在带框画面上叠加 FPS 与状态灯（保持原视觉反馈）。"""
        cv2.putText(frame, f"FPS: {self._fps:.1f}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)

        status_text = "Ready"
        status_color = (0, 255, 0)
        ctx = self.context
        if ctx.is_listening:
            status_text = "Listening..."
            status_color = (0, 255, 255)
        elif ctx.is_thinking:
            status_text = "Thinking..."
            status_color = (255, 0, 255)
        elif ctx.is_speaking:
            status_text = "Speaking..."
            status_color = (255, 100, 0)

        cv2.putText(frame, status_text, (10, 70),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, status_color, 2)

    # ---------------------------------------------------------------- 外部指令
    def manual_input(self, text: str):
        """把控制台/输入框文字送入对话（取代原 stdin 线程）。"""
        text = (text or "").strip()
        if not text:
            return
        if text.lower().startswith("记忆"):
            main.handle_memory_command(text)
        else:
            main.handle_user_text(
                text, self.speaker, self.brain,
                source="控制台", bypass_wake=True,
            )

    def manual_wake(self):
        """手动唤醒（等价于原空格键）。"""
        main.SYSTEM_STATE = "AWAKE"
        main.LAST_INTERACTION_TIME = time.time()
        if self.speaker is not None:
            try:
                self.speaker.speak("我在，请讲。", emotion_hint="热情")
            except Exception:
                pass
