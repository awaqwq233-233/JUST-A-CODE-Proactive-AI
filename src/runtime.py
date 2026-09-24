"""J.A.C. 运行时：把 main.py 的核心处理流程封装成可在后台线程启动/停止的对象。

GUI 只通过本类与底层交互：
  - start(config) / stop()        启动与停止整条流水线
  - manual_input(text)            把控制台文字送入对话（取代原 stdin 线程）
  - manual_wake()                 手动唤醒
  - on_state_change(running)      状态回调（GUI 用于锁定/解锁开关）

底层复用 main.py 的 handle_user_text / process_response / audio_thread_func /
handle_memory_command 与模块级全局状态，避免逻辑重复。
"""
import os
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
from src.brain.llm import LocalBrain
from src.memory import MemoryManager
from src.judgment.judge import JudgmentEngine
from src.utils.config import Config
from src.audio.speaker_factory import build_speaker, preload_if_needed
from src.utils.net import setup_insecure_ssl
from src.omni import OmniClient, OmniServerLauncher, SYSTEM_PROMPT
from src.omni.client import OmniCallbacks
from src.omni.router import EscalationRouter
from src.audio.voicebox_tts import VoiceboxSpeaker


logger = logging.getLogger("runtime")


class _OmniRuntimeCallbacks(OmniCallbacks):
    """把 omni 事件桥接到共享上下文（状态灯）与控制台。"""

    def __init__(self, runtime):
        """初始化实例（持有 runtime 引用以便触发升级处理）。"""
        super().__init__()
        self.runtime = runtime
        self.context = runtime.context

    def on_state(self, state, info=None):
        """状态变化：打印并重置上下文状态灯。"""
        print(f"[OMNI] 状态: {state}")
        if state == "ready":
            self.context.is_listening = False
            self.context.is_speaking = False
            self.context.is_thinking = False
        elif state in ("closed", "error"):
            self.context.is_listening = False
            self.context.is_speaking = False

    def on_text_delta(self, text):
        """omni 文本增量（打印到控制台，GUI 下进入日志面板）。"""
        print(text, end="", flush=True)

    def on_text_final(self, text):
        """一轮文本结束（换行）。"""
        print()

    def on_listen(self):
        """omni 进入聆听：状态灯切到 Listening。"""
        self.context.is_listening = True
        self.context.is_speaking = False

    def on_audio_chunk(self, pcm_bytes):
        """收到 omni 语音：标记 Speaking（播放由 client 内置播放器负责）。"""
        self.context.is_speaking = True

    def on_call_qwen(self, task):
        """检测到升级令牌：状态灯切到「思考中」并交由 runtime 后台处理。"""
        self.context.is_listening = False
        self.context.is_speaking = False
        self.context.is_thinking = True
        print(f"\n[OMNI升级] 检测到升级令牌，任务：{task}")
        self.runtime._handle_escalation(task)

    def on_error(self, err):
        """异常打印。"""
        print(f"[OMNI] 错误: {err}")


class JACRuntime:
    def __init__(self, context=None, on_state_change=None):
        # 使用 main 模块级全局 context（process_response / 音频线程都读它），
        # 保证视觉摘要与送入大模型的帧来自同一上下文。
        """初始化实例"""
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
        # --- OMNI 全双工模式状态 ---
        self.omni_client = None          # OmniClient 实例（OMNI 模式下非 None）
        self.omni_launcher = None        # OmniServerLauncher（按需启动服务）
        self.omni_mode = False           # 当前是否 OMNI 模式
        self.omni_router = None          # EscalationRouter（升级路由，首次升级时懒创建）
        # 当前升级线程引用（最后一次启动的那个）。用于 stop() 时短暂 join，
        # 避免「点了停止，升级线程还在跑工具调用 / 念结果」。
        self._escalation_thread = None

    # ---------------------------------------------------------------- 生命周期
    def start(self, config: Config):
        if self.running:
            return
        main.running = True
        self.running = True
        # 新一轮启动：清掉上一次会话遗留的升级线程引用（stop() 里 join 前会再查 is_alive）
        self._escalation_thread = None
        self.config = config
        # 把 GUI 选项面板的「工具功能」开关桥接到 main 的 FC 总开关：
        # process_response 在判定是否走 Function Calling 时读的是模块级
        # main.TOOLS_ENABLED（非 config 对象），GUI 路径必须在此覆盖它，
        # 否则 UI 开关形同虚设（始终按环境变量默认值生效）。
        main.TOOLS_ENABLED = config.tools_enabled
        if config.tools_enabled:
            print("[系统] 工具功能（Function Calling）已启用")
        else:
            print("[系统] 工具功能（Function Calling）已禁用")
        # 在任何网络下载（Whisper / Qwen3-TTS 权重）之前应用 SSL 设置；
        # 仅在环境变量 JAC_HF_INSECURE=1 时关闭证书校验（应对代理自签证书环境）
        setup_insecure_ssl()

        # 0) OMNI 全双工模式：直接接管，跳过传统语音闭环（Whisper STT / MiniCPM-v 判断引擎）；
        #    omni 自己完成「看 + 听 + 说」，qwen+tools 仅作为升级后端（M2 接入）。
        #    M7b/M7a 起主对话与回灌改用本地 Voicebox 克隆声纹（替代 omni 自带无克隆 TTS）。
        if config.omni_enabled:
            self._start_omni(config)
            return

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

        # 2) 扬声器：统一走 build_speaker 工厂
        #   优先级 Voicebox（开源克隆 TTS）-> Qwen3-TTS（仅 NVIDIA）-> 系统 TTS 兜底
        self.speaker = build_speaker(config)
        preload_if_needed(self.speaker)

        recognizer = SpeechRecognizer(model_size="tiny", language=config.stt_language)
        recorder = AudioRecorder()
        self.brain = LocalBrain(
            model_path="models/Qwen3.5-9B-Q4_K_M.gguf",
            backend=config.brain_backend,
            lm_studio_model="qwen/qwen3.6-35b-a3b",
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
                cooldown=config.judgment_cooldown,
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

    # ---------------------------------------------------------------- OMNI 全双工模式
    def _start_omni(self, config: Config):
        """OMNI 模式启动：起服务（按需）→ 建 OmniClient → 全双工闭环。

        与传统模式互斥：OMNI 模式下不初始化摄像头/YOLO/Whisper/Voicebox/判断引擎，
        omni 直接以音视频流与用户交流。升级到 qwen+tools 留待 M2。
        """
        self.omni_mode = True
        project_root = os.path.dirname(os.path.abspath(main.__file__))
        ref_audio = config.omni_ref_audio
        if not os.path.isabs(ref_audio):
            ref_audio = os.path.join(project_root, ref_audio)

        # 1) 按需启动本地 llama-omni-server（Q8_0）
        if config.omni_auto_launch:
            self.omni_launcher = OmniServerLauncher(
                host=config.omni_host,
                port=config.omni_port,
                model_dir=config.omni_model_dir,
                quant=config.omni_quant,
                server_bin=config.omni_server_bin,
            )
            print("[OMNI] 正在准备本地 omni 服务（首次加载模型约 10~60s）…")
            if not self.omni_launcher.start(wait=True, timeout=180):
                print("[OMNI] 服务未能就绪，OMNI 模式启动失败。请检查 omni_model_dir / 二进制。")
                self.running = False
                main.running = False
                self.omni_mode = False
                self._notify(False)
                return

        # 2) 本地 Voicebox 克隆 TTS（M7b/M7a 复用）：OMNI 模式下 self.speaker 未走传统
        #    build_speaker（line 134 直接 return），故在此单独创建给 omni 主对话/回灌用
        voicebox_speaker = VoiceboxSpeaker()
        self.speaker = voicebox_speaker  # 统一 self.speaker 语义（OMNI 下即 Voicebox 克隆 TTS）

        # 3) 建立全双工客户端
        cb = _OmniRuntimeCallbacks(self)
        self.omni_client = OmniClient(
            url=config.omni_server_url,
            ref_audio_path=ref_audio,
            system_prompt=SYSTEM_PROMPT,
            callbacks=cb,
            enable_mic=True,
            enable_camera=True,
            enable_playback=True,
            push_interval=0.4,
            video_fps=config.omni_fps,
            # P1 图像降频：图像上行与音频上行解耦（默认 1 帧/秒），避免视觉 token 把
            # KV 快速填满（真机实测每约 30 秒一次上下文滑动 → 模型照示例复读令牌）。
            video_interval=config.omni_video_interval,
            # P0 图像上行总开关：False = 完全不上图（纯音频全双工），用于隔离验证
            # 「视觉 token 吃爆 KV → 上下文每约 30s 被滑动清空 → 模型照 prompt 示例复读」。
            video_enabled=getattr(config, "omni_video_enabled", True),
            # 逐块上行诊断日志（GUI 复选框 / CLI --debug / OMNI_DEBUG=1）
            debug=getattr(config, "omni_debug_log", False),
            mic_gain=config.omni_mic_gain,
            listen_prob_scale=config.omni_listen_prob_scale,  # 压低 listen 偏好，修复全双工只听不说
            # 回声门控：config 值可为 "auto"/"1"/"0" 字符串，由 client.resolve_echo_gate 解析
            echo_gate=getattr(config, "omni_echo_gate", "auto"),
            camera_width=config.camera_width,
            camera_height=config.camera_height,
            voicebox_speaker=voicebox_speaker,
        )
        print("[OMNI] 正在连接 omni 并初始化全双工会话…")
        if not self.omni_client.start(timeout=180):
            print("[OMNI] 客户端未能就绪，OMNI 模式启动失败。")
            self.running = False
            main.running = False
            self.omni_mode = False
            self.omni_client = None
            self._notify(False)
            return

        print("==========================================")
        print("      J.A.C. OMNI 全双工已启动")
        print("==========================================")
        self._notify(True)

    # ---------------------------------------------------------------- M2 升级路由
    def _handle_escalation(self, task: str):
        """处理 omni 发出的 <<CALL_QWEN>> 升级任务（在独立后台线程执行）。

        必须放到后台线程：qwen+tools（LocalBrain.run_agentic）是同步阻塞的 HTTP 请求，
        绝不能在 omni 的 WebSocket 接收循环线程里跑，否则会阻塞主会话收发。

        流程：懒创建 EscalationRouter → 跑 qwen+tools 拿结果 → 经本地 Voicebox
        （JAC 克隆声纹）回灌播报 → 标记升级完成解除主会话静音。
        （注：原 omni 临时 turn_based 回灌因 server 单会话被拒，M7a 起改用本地 Voicebox）
        """
        def _worker():
            # 停止检查点 1：runtime 已停止就直接放弃，不再浪费算力跑 LLM / 工具调用。
            if not self.running:
                print("[OMNI升级] runtime 已停止，丢弃本次升级任务。")
                return
            try:
                if self.omni_router is None:
                    self.omni_router = EscalationRouter()
                # run_agentic 流式 yield 最终回答文本（打字机效果），on_progress 推到控制台；
                # should_stop 是协作取消信号——每个流式分片检查一次，命中即中断并丢弃结果
                result = self.omni_router.escalate(
                    task,
                    on_progress=lambda c: print(c, end="", flush=True),
                    should_stop=lambda: not self.running,
                )
                print()  # 换行，结束流式输出
                # 停止检查点 2（关键）：escalate 是同步阻塞的，无法抢占式中断，可能刚刚才返回。
                # 此处必须早于任何 TTS / GUI 写入——否则会出现「点了停止，J.A.C. 还在念升级结果」。
                # 历史 bug 成因：stop() 会把 self.omni_client 置 None，于是 worker 走 else 的
                # speak_text_via_voicebox 降级分支，反而「保证出声」。
                if not self.running:
                    print("[OMNI升级] runtime 已停止，丢弃升级结果（不播报）。")
                    return
                # 确保答案一定出声：优先经 client 回灌（Voicebox 克隆），否则降级系统 TTS。
                # 关键修复：喂给 TTS 的文本必须是干净的（不带「（升级结果）」前缀），
                # 否则会被 Voicebox 念出来；仅 GUI 实时文字区保留前缀显示（append_reply）。
                clean = (result if result
                         else "抱歉 boss，升级通道暂时拿不到结果，我稍后再试。")
                if self.omni_client is not None and self.omni_client.is_running():
                    self.omni_client.speak_result(clean)              # 干净文本进 TTS
                else:
                    # 客户端不可用（例如已被 stop 置 None）：降级系统 TTS，避免答案静默丢失。
                    # 注意此分支在 stop 竞态下也可能被走到，故上面那道 running 检查是必需的。
                    from src.omni.backfeed import speak_text_via_voicebox
                    speak_text_via_voicebox(None, clean)
                # 把升级结果（带前缀）写入回复缓存，仅供 GUI 实时文字区显示，不参与 TTS
                if self.omni_client is not None:
                    self.omni_client.append_reply(
                        f"\n（升级结果）{result}\n" if result
                        else "\n（升级结果）抱歉 boss，升级通道暂时拿不到结果，我稍后再试。\n"
                    )
            except Exception as e:  # noqa: BLE001
                print(f"[OMNI升级] 异常: {e}")
                # 停止后连错误提示也不该出声
                if not self.running:
                    return
                if self.omni_client is not None and self.omni_client.is_running():
                    try:
                        self.omni_client.speak_result("抱歉 boss，升级处理出错了。")
                        self.omni_client.append_reply("\n（升级结果）抱歉 boss，升级处理出错了。\n")
                    except Exception:  # noqa: BLE001
                        pass
                else:
                    try:
                        from src.omni.backfeed import speak_text_via_voicebox
                        speak_text_via_voicebox(None, "抱歉 boss，升级处理出错了。")
                    except Exception:  # noqa: BLE001
                        pass
            finally:
                if self.omni_client is not None:
                    self.omni_client.mark_escalation_done()
                self.context.is_thinking = False

        # 记录线程引用供 stop() 短暂 join；若已有上一个升级线程在跑，
        # 它会在 should_stop 检查点（每流式分片一次）看到 running=False 自行退出。
        t = threading.Thread(target=_worker, daemon=True, name="omni-escalation")
        self._escalation_thread = t
        t.start()

    def stop(self):
        """停止"""
        if not self.running:
            return
        self.running = False
        main.running = False
        self._audio_stop.set()
        # 升级线程收尾（2026-09-24 修复「停止后仍在跑工具调用与 TTS」）：
        # escalate 内部是同步阻塞的 LLM + 工具循环，无法抢占式中断，只能靠 worker 里的
        # 协作检查点（`should_stop=lambda: not self.running`）在下一个流式分片处自行收手。
        # 这里的短暂 join 是为了「等它走到那个检查点」，**必须早于关 omni_client**——
        # 否则 worker 可能读到 self.omni_client 已是 None 而走降级 TTS 分支，
        # 反而「保证出声」，正是该 bug 的历史成因。
        # 超时取 1.5s：它是 daemon 线程，进程退出会一并回收，不该拖慢停止响应。
        if self._escalation_thread is not None:
            if self._escalation_thread.is_alive():
                self._escalation_thread.join(timeout=1.5)
            self._escalation_thread = None
        # OMNI 模式：关闭全双工客户端（不自杀服务进程，便于复用/与其它入口共存）
        if self.omni_client is not None:
            try:
                self.omni_client.stop()
            except Exception:
                pass
            self.omni_client = None
        self.omni_mode = False
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
        """通知"""
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
        """手动输入（GUI 输入框 / 发送按钮入口）。

        对话分支必须放到独立 worker 线程执行，绝不能留在 Qt 主线程：
        process_response 内部是同步阻塞的 HTTP 请求（视觉查询 think_with_image
        走非流式，timeout 默认 120s），若在主线程执行会直接冻结 Qt 事件循环，
        表现为「程序未响应 / 界面卡死」。记忆命令因含交互式 input() 仍留在主线程。
        """
        text = (text or "").strip()
        if not text:
            return
        # OMNI 全双工模式：omni 以音视频流直接交流；full_duplex 的 input.append 严禁
        # 带 messages（ws_handler.cpp:1075），故手动文字无法注入。升级仅由 omni 语音流
        # 里的 <<CALL_QWEN>> 令牌触发（M2 已接入），经 qwen+tools 处理后回灌播报。
        if self.omni_mode and self.omni_client is not None:
            print("[OMNI] 全双工模式请用语音与 J.A.C. 交流；"
                  "手动文字指令不支持（full_duplex 禁文本注入），"
                  "如需升级能力请直接对 J.A.C. 说话触发 <<CALL_QWEN>>。")
            return
        if text.lower().startswith("记忆"):
            # 记忆命令含交互式 input()，GUI 下仍由 Qt 主线程处理
            main.handle_memory_command(text)
        else:
            # 丢到后台线程，避免阻塞 GUI 主线程导致界面冻结
            threading.Thread(
                target=main.handle_user_text,
                args=(text, self.speaker, self.brain),
                kwargs={"source": "控制台", "bypass_wake": True},
                daemon=True,
                name="manual-input",
            ).start()

    def manual_wake(self):
        """手动唤醒（等价于原空格键）。"""
        main.SYSTEM_STATE = "AWAKE"
        main.LAST_INTERACTION_TIME = time.time()
        if self.speaker is not None:
            try:
                self.speaker.speak("我在，请讲。")
            except Exception:
                pass
