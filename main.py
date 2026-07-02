import cv2
import sys
import time
import threading
import queue
import os
import random
import platform

# --- 平台检测 ---
PLATFORM = platform.system()  # 'Windows', 'Darwin' (macOS), 'Linux'
IS_WINDOWS = PLATFORM == 'Windows'
IS_MACOS = PLATFORM == 'Darwin'
IS_LINUX = PLATFORM == 'Linux'

print(f"[系统] 检测到平台: {PLATFORM}")

# --- 自动配置 FFmpeg (解决 WinError 2) ---
# 我们已经通过 setup_ffmpeg.py 将 ffmpeg.exe 复制到了项目根目录
# 现在需要确保项目根目录在 PATH 环境变量中
try:
    project_root = os.path.dirname(os.path.abspath(__file__))
    
    if IS_WINDOWS:
        ffmpeg_path = os.path.join(project_root, "ffmpeg.exe")
    else:
        ffmpeg_path = os.path.join(project_root, "ffmpeg")
    
    if os.path.exists(ffmpeg_path):
        print(f"[系统] 检测到本地 ffmpeg: {ffmpeg_path}")
        if project_root not in os.environ["PATH"]:
            os.environ["PATH"] = project_root + os.pathsep + os.environ["PATH"]
            print(f"[系统] 已将项目根目录添加到 PATH")
    else:
        print("[警告] 未找到 ffmpeg，尝试使用系统安装的 ffmpeg...")
        if IS_MACOS:
            os.system("which ffmpeg")
except Exception as e:
    print(f"[警告] FFmpeg 配置异常: {e}")
# -----------------------------------------

# 导入模块
from src.capture.camera import Camera
from src.analysis.detector import VisionDetector
from src.audio.tts import Speaker
try:
    from src.audio.genie_tts import GenieSpeaker
except Exception:
    GenieSpeaker = None
from src.audio.stt import SpeechRecognizer
from src.audio.recorder import AudioRecorder
from src.brain.llm import LocalBrain
from src.utils.context import SharedContext

# 全局状态
running = True
conversation_running = False
# 消息队列 (日志)
log_queue = queue.Queue()

# 上下文管理器 (新增)
context = SharedContext()

# 唤醒词配置
WAKE_WORDS = ["jac", "j.a.c", "杰克", "接客", "你好", "hello jac", "hi jac", "你好 jac","hey jac"]
SYSTEM_STATE = "SLEEP" # SLEEP | AWAKE
LAST_INTERACTION_TIME = 0
AWAKE_TIMEOUT = 20 # 唤醒后维持 20 秒活跃状态

def check_wake_word(text):
    """检查文本中是否包含唤醒词"""
    text_lower = text.lower()
    for word in WAKE_WORDS:
        if word in text_lower:
            return True
    return False

def process_response(text, brain, speaker):
    """
    核心对话逻辑：思考 -> 回复
    """
    global conversation_running
    conversation_running = True
    context.is_thinking = True
    print(f"[交互] 正在思考: {text}")
    
    try:
        # 获取当前的视觉摘要
        vision_info = context.get_vision_summary()
        # print(f"[视觉感知] {vision_info}")
        
        # 构建更智能的 System Prompt
        system_prompt = (
            "你是一个叫 J.A.C. 的全功能语音助手，J.A.C. 的全称是 Just A Code。"
            "你不仅拥有广博的知识，还具备智能眼镜的视觉感知能力，可以通过摄像头看到周围的环境。"
            f"【当前视觉信息】：{vision_info}\n"
            "请根据视觉信息和用户的提问，选择最合适的情绪（可选：热情、平静、关怀、鼓励、开心、惊讶、悲伤、生气）。"
            "【重要】请严格按照以下格式回复："
            "[情绪] 回复内容"
            "例如：[开心] 哇，这只猫真可爱！"
            "例如：[关怀] 你看起来有点累，要注意休息哦。"
            "如果用户问看到了什么，请直接描述视觉信息，并带上[平静]或[热情]的情绪。"
        )
        
        # 随机温度
        temperature = random.uniform(0.65, 0.95)
        full_response = brain.think(text, system_prompt=system_prompt, temperature=temperature, max_tokens=140)
        print(f"[J.A.C 原始回复] {full_response}")
        
        context.is_thinking = False

        # 解析情绪与内容
        import re
        emotion = "平静" # 默认
        response_text = full_response
        
        match = re.match(r"^\s*[\[【](.*?)[\]】]\s*(.*)", full_response, re.DOTALL)
        if match:
            emotion = match.group(1)
            response_text = match.group(2)
        else:
            match_colon = re.match(r"^(\w{2})\s*[：:]\s*(.*)", full_response, re.DOTALL)
            if match_colon and match_colon.group(1) in ["热情", "平静", "关怀", "鼓励", "开心", "惊讶"]:
                emotion = match_colon.group(1)
                response_text = match_colon.group(2)

        print(f"[解析结果] 情绪: {emotion}, 内容: {response_text}")
        
        # 回答
        context.is_speaking = True
        try:
            speaker.speak(response_text, emotion_hint=emotion)
        except TypeError:
            speaker.speak(response_text)
        context.is_speaking = False

    finally:
        conversation_running = False

def audio_thread_func(speaker, recognizer, recorder, brain):
    """
    音频主循环：监听 -> 识别 -> (唤醒判断) -> 响应
    """
    global running, SYSTEM_STATE, LAST_INTERACTION_TIME
    print("[系统] 语音监听服务已启动 (VAD Enabled)。")
    print(f"[系统] 当前状态: {SYSTEM_STATE}。请说 'Hey J.A.C' 或 '你好' 来唤醒我。")
    
    if not os.path.exists("temp"):
        os.mkdir("temp")

    while running:
        if context.is_speaking:
            time.sleep(0.5)
            continue
            
        # 1. 监听并录音 (VAD)
        # 这一步会阻塞，直到检测到说话并结束
        # 如果是睡眠模式，可以只监听短语；如果是唤醒模式，可以监听更长
        try:
            context.is_listening = True
            filename = f"temp/cmd_{int(time.time())}.wav"
            
            # 这里的 listen_and_record 是阻塞的，直到有人说话
            # 为了避免一直卡住无法退出，内部最好有超时或定期检查 running
            # 但目前的实现依赖于有人说话。
            # 如果没人说话，它会一直在这里等待 VAD 触发
            recorder.listen_and_record(output_filename=filename, silence_timeout=1.0)
            
            context.is_listening = False
            
            # 2. 识别
            # print("[交互] 正在识别...")
            text = recognizer.transcribe(filename)
            
            # 删除临时文件
            try:
                os.remove(filename)
            except:
                pass

            if not text or len(text.strip()) < 1:
                continue
                
            print(f"[听写] {text}")
            
            # 3. 唤醒逻辑判断
            current_time = time.time()
            
            # 如果超时未交互，自动休眠
            if SYSTEM_STATE == "AWAKE" and (current_time - LAST_INTERACTION_TIME > AWAKE_TIMEOUT):
                print("[系统] 超时未交互，进入休眠模式。")
                SYSTEM_STATE = "SLEEP"
            
            if SYSTEM_STATE == "SLEEP":
                # 检查唤醒词
                if check_wake_word(text):
                    print(f"[系统] 检测到唤醒词！进入唤醒状态。")
                    SYSTEM_STATE = "AWAKE"
                    LAST_INTERACTION_TIME = current_time
                    speaker.speak("我在。", emotion_hint="热情")
                    
                    # 如果唤醒词后面还有内容，直接处理
                    # 比如 "你好JAC，现在几点了"
                    # 简单的过滤掉唤醒词本身可能比较复杂，这里直接把整句扔给 LLM 也可以，
                    # 或者只在 text 比较长的时候处理
                    if len(text) > 5:
                        process_response(text, brain, speaker)
                else:
                    # 忽略噪音
                    # print("[系统] 未唤醒，忽略。")
                    pass
                    
            elif SYSTEM_STATE == "AWAKE":
                # 已经在对话中，直接处理
                LAST_INTERACTION_TIME = current_time
                
                # 如果用户说 "再见" 或 "退下"
                if "再见" in text or "休息" in text:
                    speaker.speak("好的，有需要随时叫我。", emotion_hint="平静")
                    SYSTEM_STATE = "SLEEP"
                else:
                    process_response(text, brain, speaker)
                    
        except Exception as e:
            print(f"[错误] 语音循环异常: {e}")
            time.sleep(1)

def main():
    global running
    global conversation_running
    print("==========================================")
    print("      J.A.C. - Just A Code (多模态版)      ")
    print("==========================================")
    
    # 1. 初始化
    camera = Camera(camera_id=0)
    if not camera.start(): return

    detector = VisionDetector(mode='hybrid')
    # 优先使用 Genie-TTS，如果配置不可用则回退到 pyttsx3
    speaker = None
    if GenieSpeaker is not None:
        speaker = GenieSpeaker()
    if speaker is None or getattr(speaker, "available", False) is False:
        speaker = Speaker()
    
    # 根据电脑配置选择模型大小
    # recognizer = SpeechRecognizer(model_size="base") 
    recognizer = SpeechRecognizer(model_size="tiny") 
    
    recorder = AudioRecorder()
    brain = LocalBrain(model_path="models/qwen1_5-1_8b-chat-q4_k_m.gguf")

    print("\n[操作提示]")
    print("  - 按 'q' 键: 退出")
    print("  - 按 'SPACE' (空格): 触发多模态对话")
    print("==========================================\n")

    audio_thread = threading.Thread(target=audio_thread_func, 
                                    args=(speaker, recognizer, recorder, brain))
    audio_thread.start()
    
    frame_count = 0
    start_time = time.time()
    fps = 0

    try:
        while running:
            # --- 视觉 ---
            ret, frame = camera.get_frame()
            if not ret: break
            
            # 检测并获取结果
            annotated_frame, results = detector.detect(frame)
            
            # 关键：更新共享上下文
            context.update_vision(results)
            
            # FPS
            frame_count += 1
            if frame_count >= 10:
                fps = frame_count / (time.time() - start_time)
                frame_count = 0
                start_time = time.time()
            
            # UI 绘制
            cv2.putText(annotated_frame, f"FPS: {fps:.1f}", (10, 30), 
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
            
            # 状态指示灯
            status_text = "Ready"
            status_color = (0, 255, 0)
            if context.is_listening:
                status_text = "Listening..."
                status_color = (0, 255, 255) # 黄色
            elif context.is_thinking:
                status_text = "Thinking..."
                status_color = (255, 0, 255) # 紫色
            elif context.is_speaking:
                status_text = "Speaking..."
                status_color = (255, 100, 0) # 蓝色

            cv2.putText(annotated_frame, status_text, (10, 70), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, status_color, 2)
            
            # 显示当前看到的文字摘要 (调试用)
            # summary = context.get_vision_summary()
            # cv2.putText(annotated_frame, summary[:30], (10, 110), 
            #             cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)

            cv2.imshow('J.A.C Multimodal Interface', annotated_frame)
            
            # --- 交互 ---
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                running = False
                break
            elif key == 32: # Space 键
                # 手动唤醒
                print("[交互] 手动唤醒触发")
                SYSTEM_STATE = "AWAKE"
                LAST_INTERACTION_TIME = time.time()
                if speaker:
                    speaker.speak("我在，请讲。", emotion_hint="热情")
                
    except KeyboardInterrupt:
        running = False
    finally:
        camera.stop()
        cv2.destroyAllWindows()
        print("[系统] 程序已结束。")

if __name__ == "__main__":
    main()
