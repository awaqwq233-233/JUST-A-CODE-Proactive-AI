import cv2
import sys
import time
import threading
import queue
import os
import random
import platform
import logging
import argparse
from src.judgment.judge import JudgmentEngine, InterventionRequest
from src.utils.config import Config

# --- 平台检测 ---
PLATFORM = platform.system()  # 'Windows', 'Darwin' (macOS), 'Linux'
IS_WINDOWS = PLATFORM == 'Windows'
IS_MACOS = PLATFORM == 'Darwin'
IS_LINUX = PLATFORM == 'Linux'

print(f"[系统] 检测到平台: {PLATFORM}")

# --- 自动配置 FFmpeg (跨平台) ---
# 只要能找到 ffmpeg，就把其所在目录加入 PATH，避免下游调用找不到。
#   Windows: 项目根目录的 ffmpeg.exe（由 setup_ffmpeg.py 复制）
#   macOS:   Homebrew 安装（Apple Silicon /opt/homebrew/bin，Intel /usr/local/bin）
#   Linux:   系统 /usr/bin/ffmpeg 或用户安装路径
#   兜底:     用 shutil.which 在系统 PATH 中查找
try:
    import shutil
    project_root = os.path.dirname(os.path.abspath(__file__))

    ffmpeg_candidates = []
    if IS_WINDOWS:
        ffmpeg_candidates.append(os.path.join(project_root, "ffmpeg.exe"))
    else:
        # 项目本地（若有）
        ffmpeg_candidates.append(os.path.join(project_root, "ffmpeg"))
        # macOS Homebrew 常见位置
        ffmpeg_candidates.append("/opt/homebrew/bin/ffmpeg")
        ffmpeg_candidates.append("/usr/local/bin/ffmpeg")
        # Linux 常见位置
        ffmpeg_candidates.append("/usr/bin/ffmpeg")
        ffmpeg_candidates.append("/usr/local/bin/ffmpeg")

    ffmpeg_dir = None
    for cand in ffmpeg_candidates:
        if os.path.exists(cand):
            ffmpeg_dir = os.path.dirname(cand)
            print(f"[系统] 检测到本地 ffmpeg: {cand}")
            break

    if ffmpeg_dir is None:
        path_ff = shutil.which("ffmpeg")
        if path_ff:
            ffmpeg_dir = os.path.dirname(path_ff)
            print(f"[系统] 在系统 PATH 中找到 ffmpeg: {path_ff}")

    if ffmpeg_dir:
        if ffmpeg_dir not in os.environ["PATH"].split(os.pathsep):
            os.environ["PATH"] = ffmpeg_dir + os.pathsep + os.environ["PATH"]
            print(f"[系统] 已将 ffmpeg 目录加入 PATH: {ffmpeg_dir}")
    else:
        print("[警告] 未找到 ffmpeg（项目目录、常见安装路径与系统 PATH 均未发现）。")
        if IS_MACOS:
            print("        macOS:  brew install ffmpeg")
        elif IS_LINUX:
            print("        Linux:   sudo apt install ffmpeg  (或对应发行版包管理器)")
        else:
            print("        Windows: 运行  python setup_ffmpeg.py")
except Exception as e:
    print(f"[警告] FFmpeg 配置异常: {e}")
# -----------------------------------------

# 导入模块
from src.capture.camera import Camera
from src.analysis.detector import VisionDetector
from src.audio.tts import Speaker
# qwen_tts 不在顶层 import：见下方 _load_qwen_tts（懒加载，避免阻塞 GUI 启动）
from src.audio.stt import SpeechRecognizer
from src.audio.recorder import AudioRecorder
from src.brain.llm import LocalBrain
from src.utils.context import SharedContext
from src.memory import MemoryManager, seed_base_memories
from src.utils.net import setup_insecure_ssl


def _load_qwen_tts():
    """懒加载 Qwen3-TTS。

    qwen_tts 会拉起沉重的 transformers / huggingface_hub 导入链（首次可能耗时数十秒），
    若放在模块顶层 import，会阻塞 GUI 窗口弹出与控制台交互。故改为在真正创建扬声器时才加载，
    且仅加载一次（之后由 sys.modules 缓存）。返回 (QwenTTSSpeaker, available)。

    若包未安装，这里会触发自动安装（清华镜像优先）与权重补全，安装成功才返回可用。
    """
    try:
        from src.audio import qwen_tts as qt
        if not qt.ensure_qwen_tts():
            print("[TTS] Qwen3-TTS 不可用（自动安装失败），将回退系统 TTS。")
            return None, False
        # 安装后重新加载模块，刷新模块级 QWEN_TTS_AVAILABLE 标志
        import importlib
        importlib.reload(qt)
        from src.audio.qwen_tts import QwenTTSSpeaker, QWEN_TTS_AVAILABLE
        if QWEN_TTS_AVAILABLE:
            return QwenTTSSpeaker, True
        print("[TTS] Qwen3-TTS 不可用，将回退系统 TTS。")
        return None, False
    except Exception as e:
        print(f"[TTS] Qwen3-TTS 不可用（{e}），将回退系统 TTS。")
        return None, False


# 全局状态
running = True
conversation_running = False
DISPLAY_ENABLED = True  # 是否创建 OpenCV 显示窗口；纯终端模式(无 Metal)时置 False
conversation_lock = threading.Lock()
# 单飞锁：保证同一时刻只有一个 process_response 在跑（语音/手动输入/判断介入三来源并发时防卡死）
think_lock = threading.Lock()
# 消息队列 (日志)
log_queue = queue.Queue()

# 上下文管理器 (新增)
context = SharedContext()

WAKE_WORDS = ["jac", "j.a.c", "杰克", "接客", "你好", "hello jac", "hi jac", "你好 jac","hey jac"]
SYSTEM_STATE = "SLEEP" # SLEEP | AWAKE
LAST_INTERACTION_TIME = 0
AWAKE_TIMEOUT = 20 # 唤醒后维持 20 秒活跃状态

# --- 前导判断引擎配置（MiniCPM-o 视觉版，真机默认开启主动感知）---
# 真机（Mac 48GB 统一内存）：默认开启主动判断。启动时检测到判断模型即自动激活；
# 若未加载判断模型则自动降级为被动模式（仍可控制台/唤醒词交互）。设为 False 可手动关闭。
JUDGMENT_ENGINE_ENABLED = True
JUDGMENT_MODEL_NAME = "minicpm-v-4_5"  # LM Studio / llama_cpp 中 MiniCPM-o(视觉版) 的实际模型 ID（大小写不敏感模糊匹配）
# 判断间隔（秒）。真机默认 4.0，主动感知更及时；如需更省资源可用 JUDGMENT_INTERVAL 调大。
JUDGMENT_INTERVAL = float(os.environ.get("JUDGMENT_INTERVAL", "4.0"))
# 判断请求超时（秒）。真机默认 15.0；若判断模型加载中或资源紧张偶发超时，可调大 JUDGMENT_TIMEOUT。
JUDGMENT_TIMEOUT = float(os.environ.get("JUDGMENT_TIMEOUT", "15.0"))
# 介入后冷却（秒）。默认 20.0；避免同一画面反复触发主动介入、淹没大脑与用户对话。
JUDGMENT_COOLDOWN = float(os.environ.get("JUDGMENT_COOLDOWN", "20.0"))

# --- 大脑推理后端 ---
# 默认 lm_studio（需 LM Studio 在 127.0.0.1:12345 加载 qwen3.5-9b）。
# Mac 本地优先也可设为 llama_cpp（直接加载 GGUF，无需 LM Studio，更适合眼镜主机）；或 ollama。
BRAIN_BACKEND = os.environ.get("JAC_BRAIN_BACKEND", "lm_studio")
JUDGMENT_ACTIVATED = False         # 判断引擎实际运行标记

# --- 记忆子系统配置（J.A.C. 长期记忆）---
# 总开关：默认开启；设 MEMORY_ENABLED=false 可禁用。
MEMORY_ENABLED = os.environ.get("MEMORY_ENABLED", "true").lower() not in ("0", "false", "no", "off")
# 人物身份捕获：默认关闭（不记具体人物身份，PII 双层门控第一层）。
MEMORY_CAPTURE_PERSON_ID = os.environ.get("MEMORY_CAPTURE_PERSON_ID", "false").lower() in ("1", "true", "yes", "on")
memory = None  # MemoryManager 实例，在 main() 中初始化

def check_wake_word(text):
    """检查文本中是否包含唤醒词"""
    text_lower = text.lower()
    for word in WAKE_WORDS:
        if word in text_lower:
            return True
    return False

def is_visual_query(text):
    """判断用户是否在问视觉相关问题"""
    visual_keywords = [
        "看到", "看见", "有什么", "什么东西", "看看", "画面", "图像",
        "我面前", "前面", "周围", "环境", "是谁", "在干嘛", "在做什么",
        "你看到了什么", "你现在看到什么", "看到了什么"
    ]
    return any(keyword in text for keyword in visual_keywords)

def build_text_only_vision_reply(user_text, vision_info, brain, temperature):
    """
    纯文本模型的视觉降级回答：
    不发截图，改为把当前检测摘要整理后交给 LLM。
    """
    vision_prompt = (
        "你是 J.A.C.，正在根据摄像头检测摘要回答用户的视觉问题。"
        "你看不到原始图片，只能依据下面这份实时检测摘要回答，不能编造未检测到的细节。"
        f"\n【检测摘要】{vision_info}"
        f"\n【用户问题】{user_text}"
        "\n【格式】第一句必须是 [情绪]（情绪可选：热情、平静、关怀、鼓励、开心、惊讶），随后紧跟一句口语化回答。"
        "【铁律】必须且仅用简体中文；不得输出推理过程、Markdown、列表或编号；"
        "回答控制在 250 字以内，自然流畅即可。若摘要信息不足，请明确说明只能根据检测结果判断。"
    )

    response = brain.think(
        vision_prompt,
        system_prompt="你是一个谨慎的视觉问答助手。",
        temperature=temperature,
        max_tokens=256
    )

    if response and response.strip():
        return response

    if "一片漆黑" in vision_info:
        return "[平静] 我眼前暂时没有拿到最新画面，你可以稍等一下再问我一次。"
    return f"[平静] {vision_info}"

def _strip_boilerplate(text):
    """
    剔除模型偶尔回吐的系统提示词废话行（思考链 / 格式说明 / 铁律等）。

    现象：Qwen3 在 content 为空、答在 reasoning 末尾时，原始输出会混入
    「【铁律】...必须且仅用简体中文」「（可选：热情、平静...）口语化描述」等提示词
    片段，直接朗读会很怪。这里按行过滤掉明显是指令的行，保留真正的描述。
    """
    import re
    if not text:
        return text
    kept = []
    for ln in text.split("\n"):
        s = ln.strip()
        if not s:
            continue
        if re.search(r"铁律|可选：|口语化描述|根据视觉信息|推理过程|"
                     r"编号列表|项目符号|输出格式|必须且仅用|Markdown|思考链|"
                     r"再次检查|再次确认", s):
            continue
        kept.append(s)
    return "\n".join(kept).strip()


def process_response(text, brain, speaker):
    """
    核心对话逻辑：思考 -> 回复

    单飞保护：同一时刻只允许一个 process_response 在跑（语音/手动输入/判断介入三来源并发时），
    避免多线程同时调用 TTS 与大脑导致资源争抢、主程序卡死。上一轮未结束则本轮直接跳过。
    """
    global conversation_running

    if not think_lock.acquire(blocking=False):
        print("[提示] 上一轮思考/说话尚未结束，本次输入已跳过（防止并发卡死）。")
        return

    conversation_running = True
    context.is_thinking = True
    print(f"[交互] 正在思考: {text}")

    full_response = ""
    try:
        # 获取当前的视觉摘要
        vision_info = context.get_vision_summary()
        # 检索长期记忆并拼入 prompt（无相关记忆则为空串）
        # 🟠#7 检索异常绝不能拖垮整轮对话，单独包裹。
        mem_block = ""
        if memory is not None:
            try:
                mem_block = memory.retrieve_for_prompt(text, vision_info)
            except Exception as e:
                print(f"[记忆] 检索注入失败，本轮跳过：{e}")
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
            "【输出铁律（必须严格遵守）】："
            "1. 必须且仅用简体中文回答，不得出现英文、繁体中文或其他语言。"
            "2. 不要输出任何推理过程、思考链、Markdown 标题、项目符号、编号列表、分隔线或代码块。"
            "3. 普通回答控制在 500 字以内（视觉描述最多 250 字），自然流畅即可，不必强行压缩。"
            "4. 直接给结论，不展开、不铺垫、不解释。"
        )
        if mem_block:
            system_prompt += f"\n{mem_block}\n"

        # 随机温度
        temperature = random.uniform(0.65, 0.95)
        is_visual = is_visual_query(text)

        if is_visual:
            frame = context.get_frame()
            can_try_image = frame is not None and getattr(brain, "backend", "") in ("lm_studio", "ollama", "llama_cpp")

            if can_try_image:
                print("[视觉] 检测到视觉查询，尝试将当前摄像头画面发送给大脑模型...")
                vision_prompt = text
                if "看到" in text or "看见" in text or "有什么" in text or "什么东西" in text:
                    vision_prompt = "请详细描述这张图片中有什么物体、人物和环境。"

                img_system_prompt = (
                    "你是一个视觉分析助手。请准确描述图像内容。"
                    "【格式】第一句必须是 [情绪]（情绪可选：热情、平静、关怀、鼓励、开心、惊讶），随后紧跟一句口语化描述。"
                    "【铁律】必须且仅用简体中文；不得输出推理过程、Markdown、列表或编号；"
                    "视觉描述控制在 250 字以内，自然流畅即可。"
                )
                if mem_block:
                    img_system_prompt += f"\n{mem_block}\n"

                full_response = brain.think_with_image(
                    vision_prompt,
                    frame,
                    system_prompt=img_system_prompt,
                    temperature=temperature,
                    max_tokens=200
                )

                if not full_response or "抱歉，大脑连接出了点问题" in full_response:
                    print("[视觉] 图像请求未得到有效结果，改用检测摘要回答。")
                    full_response = build_text_only_vision_reply(text, vision_info, brain, temperature)
            else:
                print("[警告] 没有可用的摄像头帧，改用检测摘要回答视觉问题。")
                full_response = build_text_only_vision_reply(text, vision_info, brain, temperature)
        else:
            # 流式文本思考：逐 token 打印，形成"持续思考"的打字机效果
            for chunk in brain.think_stream(text, system_prompt=system_prompt, temperature=temperature, max_tokens=256):
                full_response += chunk
                # 终端下 end="" 营造打字机效果；GUI 下每个 chunk 也会实时进入控制台队列
                print(chunk, end="", flush=True)
            print()  # 流式结束后补一个换行
            # 兜底：流式偶发返回空（LM Studio 并发/繁忙）时，补一次非流式请求重试，
            # 避免 full_response 为空导致本轮直接"跳过回复"。
            if not full_response or not full_response.strip():
                print("[提示] 流式返回为空，改用非流式请求重试一次。")
                full_response = brain.think(text, system_prompt=system_prompt,
                                            temperature=temperature, max_tokens=256)

        print(f"[J.A.C 原始回复] {full_response}")

        context.is_thinking = False

        if not full_response or not full_response.strip():
            print("[提示] 大脑返回为空，跳过回复。")
            return

        # 解析情绪与内容（鲁棒抽取）
        # 模型偶尔把系统提示词 / 思考链一起吐出来（content 为空、真正答句在输出末尾），
        # 此时「[情绪] 描述」在末尾。优先取【最后一个情绪标记】之后，其次取最后一个
        # 「情绪词，」之后，最后兜底取最后一段；并剔除提示词式废话。
        import re
        EMOTIONS = "热情|平静|关怀|鼓励|开心|惊讶|悲伤|生气"
        emotion = "平静"  # 默认
        em_bracket = list(re.finditer(r"[\[【](%s)[\]】]" % EMOTIONS, full_response))
        em_word = list(re.finditer(r"(%s)[，:,：]" % EMOTIONS, full_response))
        if em_bracket:
            emotion = em_bracket[-1].group(1)
            after = full_response[em_bracket[-1].end():]
        elif em_word:
            emotion = em_word[-1].group(1)
            after = full_response[em_word[-1].end():]
        else:
            after = full_response
        after = _strip_boilerplate(after)
        response_text = after.strip() if after.strip() else full_response.strip()
        # 清掉可能残留的 [xxx]/【xxx】 标签，避免把「[平静]」读出来
        response_text = re.sub(r"\s*[\[【][^\[\]【】]*[\]】]\s*", "", response_text).strip()

        # TTS 安全截断：仅当模型抽风产出极端超长文本（>400 字）时才截断，
        # 正常 500 字以内的回复原样朗读，避免语音引擎卡顿、用户听不完。
        if len(response_text) > 400:
            print(f"[提示] 回复过长（{len(response_text)} 字），截断到 400 字后朗读。")
            response_text = response_text[:400]

        print(f"[解析结果] 情绪: {emotion}, 内容: {response_text}")

        # 回答
        context.is_speaking = True
        try:
            speaker.speak(response_text, emotion_hint=emotion)
        except TypeError:
            speaker.speak(response_text)
        context.is_speaking = False

        # 记录判定（后台线程，非阻塞）：把本轮对话交给记忆子系统评估是否值得长期记住
        if memory is not None:
            try:
                window = context.get_recent_transcriptions(window=15.0)
                memory.record_turn(text, response_text, window)
            except Exception as e:
                print(f"[记忆] 记录异常（已忽略）: {e}")

    finally:
        conversation_running = False
        think_lock.release()

def handle_user_text(text, speaker, brain, source="语音", bypass_wake=False):
    """
    统一处理来自语音或控制台的用户输入

    注意：不再在 with conversation_lock 内调用 process_response，
    避免与 process_response 内部的 think_lock 形成重入死锁。
    状态字段（SYSTEM_STATE / LAST_INTERACTION_TIME）的并发更新属非关键路径，
    由 process_response 的 think_lock 单飞保证真正的重活不会并发。
    """
    global SYSTEM_STATE, LAST_INTERACTION_TIME

    text = (text or "").strip()
    if not text:
        return

    current_time = time.time()

    if SYSTEM_STATE == "AWAKE" and (current_time - LAST_INTERACTION_TIME > AWAKE_TIMEOUT):
        print("[系统] 超时未交互，进入休眠模式。")
        SYSTEM_STATE = "SLEEP"
        if memory is not None:
            memory.flush()

    if source == "控制台":
        print(f"[控制台] {text}")
    elif source == "语音":
        print(f"[听写] {text}")

    if bypass_wake:
        SYSTEM_STATE = "AWAKE"
        LAST_INTERACTION_TIME = current_time
        process_response(text, brain, speaker)
        return

    if SYSTEM_STATE == "SLEEP":
        if check_wake_word(text):
            print("[系统] 检测到唤醒词！进入唤醒状态。")
            SYSTEM_STATE = "AWAKE"
            LAST_INTERACTION_TIME = current_time
            speaker.speak("我在。", emotion_hint="热情")

            if len(text) > 5:
                process_response(text, brain, speaker)
        return

    LAST_INTERACTION_TIME = current_time
    if "再见" in text or "休息" in text:
        speaker.speak("好的，有需要随时叫我。", emotion_hint="平静")
        SYSTEM_STATE = "SLEEP"
        if memory is not None:
            memory.flush()
    else:
        process_response(text, brain, speaker)

def handle_memory_command(text):
    """处理记忆相关控制台命令：记忆 列表 / 导出 <路径> / 清除 [<id>|全部]"""
    global memory
    if memory is None or memory.store is None:
        print("[记忆] 长期记忆未启用。")
        return
    parts = text.strip().split()
    if len(parts) < 2:
        print("[记忆] 可用命令：记忆 列表 / 记忆 导出 <路径> / 记忆 路径 / 记忆 清除 全部 / 记忆 清除 <id>")
        return
    cmd = parts[1]
    if cmd == "列表":
        facts = memory.store.get_recent(limit=100)
        if not facts:
            print("[记忆] 暂无记忆。")
        else:
            for i, f in enumerate(facts, 1):
                print(f"[记忆 {i}] [{f.kind.value}/{f.source.value}] {f.content} (weight={f.weight}, pii={f.pii})")
            print(f"[记忆] 共 {len(facts)} 条。")
    elif cmd == "导出":
        path = parts[2] if len(parts) > 2 else "memory_export.json"
        try:
            memory.store.export(path)
            print(f"[记忆] 已导出到：{path}")
        except Exception as e:
            print(f"[记忆] 导出失败：{e}")
    elif cmd == "路径":
        print(f"[记忆] 存储目录：{memory.store.base_dir}")
        print(f"[记忆] 主文件：{memory.store.base_dir}/memory.json")
    elif cmd == "清除":
        if len(parts) > 2 and parts[2] != "全部":
            fid = parts[2]
            ok = memory.store.clear_by_id(fid)
            print(f"[记忆] 删除 {fid}：{'成功' if ok else '未找到'}")
        else:
            confirm = input("[记忆] 确认清除【全部】记忆？(输入 yes 确认) ")
            if confirm.strip().lower() == "yes":
                memory.store.clear_all()
                print("[记忆] 已清除全部记忆。")
            else:
                print("[记忆] 已取消清除。")
    else:
        print("[记忆] 未知命令。可用：记忆 列表 / 导出 <路径> / 路径 / 清除 全部 / 清除 <id>")


def manual_input_thread_func(speaker, brain):
    """
    控制台输入线程：手动输入文本并直接进入思考流程
    """
    global running
    print("[系统] 控制台输入已启用，直接输入文字并回车即可让 J.A.C 思考。")

    while running:
        try:
            text = input()
        except EOFError:
            break
        except Exception as e:
            print(f"[错误] 控制台输入异常: {e}")
            time.sleep(1)
            continue

        if not running:
            break

        if not text.strip():
            continue

        # 记忆子系统控制台命令
        if text.strip().lower().startswith("记忆"):
            handle_memory_command(text.strip())
            continue

        handle_user_text(text, speaker, brain, source="控制台", bypass_wake=True)

def audio_thread_func(speaker, recognizer, recorder, brain, stop_event=None):
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
            recorder.listen_and_record(output_filename=filename, silence_timeout=1.0, stop_event=stop_event, context=context)
            
            context.is_listening = False
            
            # 2. 识别
            # print("[交互] 正在识别...")
            text = recognizer.transcribe(filename)
            context.push_transcription(text)
            
            # 删除临时文件
            try:
                os.remove(filename)
            except:
                pass

            if not text or len(text.strip()) < 1:
                continue

            handle_user_text(text, speaker, brain, source="语音", bypass_wake=False)
                    
        except Exception as e:
            print(f"[错误] 语音循环异常: {e}")
            time.sleep(1)

def main():
    """主"""
    global running
    global conversation_running
    global DISPLAY_ENABLED
    # 在任何网络下载（Whisper / Qwen3-TTS 权重）之前应用 SSL 设置；
    # 仅在环境变量 JAC_HF_INSECURE=1 时关闭证书校验（应对代理自签证书环境）
    setup_insecure_ssl()
    print("==========================================")
    print("      J.A.C. - Just A Code (多模态版)      ")
    print("==========================================")
    
    # 1. 初始化
    camera = Camera(camera_id=None)  # 使用 None 自动检测默认摄像头
    if not camera.start(): return

    detector = VisionDetector()
    # 扬声器选择：优先 Qwen3-TTS（本地优先、开源、支持情绪/声音克隆），
    # 不可用时回退系统 TTS（pyttsx3 / macOS say / espeak）。
    speaker = None
    QwenTTSSpeaker, QWEN_TTS_AVAILABLE = _load_qwen_tts()
    if QwenTTSSpeaker is not None and QWEN_TTS_AVAILABLE:
        speaker = QwenTTSSpeaker()
    if speaker is None or not getattr(speaker, "available", False):
        speaker = Speaker()

    # 预加载 Qwen3-TTS 模型（后台线程）：避免首次发声才加载导致慢一截
    if getattr(speaker, "_ensure_model", None) is not None and getattr(speaker, "available", False):
        threading.Thread(target=speaker._ensure_model, daemon=True, name="tts-preload").start()
    
    # 根据电脑配置选择模型大小
    # recognizer = SpeechRecognizer(model_size="base") 
    recognizer = SpeechRecognizer(model_size="tiny") 
    
    recorder = AudioRecorder()
    brain = LocalBrain(model_path="models/Qwen3.5-9B-Q4_K_M.gguf", backend=BRAIN_BACKEND, lm_studio_model="qwen/qwen3.5-9b")

    # --- 记忆子系统（长期记忆）---
    global memory
    try:
        memory = MemoryManager(
            brain=brain,
            enabled=MEMORY_ENABLED,
            capture_person_id=MEMORY_CAPTURE_PERSON_ID,
        )
        if MEMORY_ENABLED:
            if memory is not None:
                try:
                    seeded = seed_base_memories(memory)
                except Exception as e:
                    seeded = 0
                    print(f"[记忆] 基础记忆 seed 失败（可忽略）: {e}")
                path = memory.store.base_dir
                count = (memory.store.stats() or {}).get("count", 0)
                print(f"[记忆] 长期记忆已启用：已加载 {count} 条记忆 @ {path}（本次 seed 基础记忆 {seeded} 条）")
            else:
                print("[记忆] 长期记忆初始化失败，本次未启用。")
            print("       记忆仅保存在你本机，不会上传；")
            print("       可用控制台命令「记忆 列表 / 记忆 导出 <路径> / 记忆 路径 / 记忆 清除 全部」管理。")
    except Exception as e:
        memory = None
        print(f"[记忆] 初始化失败，已临时禁用长期记忆: {e}")

    print("\n[操作提示]")
    print("  - 按 'q' 键: 退出")
    print("  - 按 'SPACE' (空格): 触发多模态对话")
    print("  - 在控制台直接输入文字并回车: 作为你说的话进入思考")
    print("==========================================\n")

    audio_stop = threading.Event()
    audio_thread = threading.Thread(target=audio_thread_func,
                                    args=(speaker, recognizer, recorder, brain, audio_stop),
                                    daemon=True)
    audio_thread.start()

    manual_input_thread = threading.Thread(
        target=manual_input_thread_func,
        args=(speaker, brain),
        daemon=True
    )
    manual_input_thread.start()
    
    # --- 前导判断引擎（真机默认开启主动感知）---
    judge_engine = None
    if JUDGMENT_ENGINE_ENABLED:
        judge_engine = JudgmentEngine(
            model_name=JUDGMENT_MODEL_NAME,
            interval=JUDGMENT_INTERVAL,
            timeout=JUDGMENT_TIMEOUT,
            cooldown=JUDGMENT_COOLDOWN,
        )
        judge_engine.set_context(context)

        judge_thread = threading.Thread(target=judge_engine.run, daemon=True, name="judgment")

        if judge_engine.check_available():
            JUDGMENT_ACTIVATED = True
            print("[系统] 前导判断引擎已启用 (MiniCPM-o)")
        else:
            JUDGMENT_ACTIVATED = False
            print("[系统] 前导判断引擎未就绪（未检测到判断模型），进入被动模式")

        judge_thread.start()
    else:
        JUDGMENT_ACTIVATED = False
        print("[系统] 前导判断引擎已手动禁用（JUDGMENT_ENGINE_ENABLED=False）")
    
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
            # 缓存最新帧，用于多模态视觉查询
            context.set_frame(frame)
            
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

            # 显示渲染：仅在有窗口时(imshow 在 macOS 27 等系统上会触发 Qt/OpenCV 的
            # Metal 崩溃，纯终端模式 DISPLAY_ENABLED=False 完全跳过，零渲染、零 Metal)。
            if DISPLAY_ENABLED:
                cv2.imshow('J.A.C Multimodal Interface', annotated_frame)
                key = cv2.waitKey(1) & 0xFF
                if key == ord('q'):
                    running = False
                    audio_stop.set()
                    break
                elif key == 32:  # Space 键：手动唤醒
                    # 手动唤醒
                    print("[交互] 手动唤醒触发")
                    SYSTEM_STATE = "AWAKE"
                    LAST_INTERACTION_TIME = time.time()
                    if speaker:
                        speaker.speak("我在，请讲。", emotion_hint="热情")
            else:
                # 纯终端模式：不创建任何窗口。退出用 Ctrl+C(KeyboardInterrupt)；
                # 对话用语音唤醒或直接在控制台输入文字回车。
                time.sleep(0.005)
            
            # --- 前导判断引擎介入检查 ---
            if JUDGMENT_ACTIVATED:
                intervention = judge_engine.get_intervention()
                if intervention is not None and not conversation_running:
                    print(f"[主动介入] 判断引擎: {intervention.reason}")
                    context.is_listening = False
                    context.is_thinking = False
                    vision_info = context.get_vision_summary()
                    transcript_context = intervention.transcript
                    full_context = (
                        f"[系统主动介入] {intervention.reason}\n"
                        f"当前视觉: {vision_info}\n"
                        f"最近音频: {transcript_context}"
                    )
                    threading.Thread(
                        target=lambda ctx=full_context: process_response(ctx, brain, speaker),
                        daemon=True
                    ).start()
                
    except KeyboardInterrupt:
        running = False
    finally:
        audio_stop.set()
        if locals().get('judge_engine') is not None:
            judge_engine.stop()
        if memory is not None:
            try:
                memory.close()
            except Exception:
                pass
        camera.stop()
        if DISPLAY_ENABLED:
            cv2.destroyAllWindows()
        # 等待音频线程退出（stop_event 已置位，录音循环会在一个 chunk 内退出），再关闭 PyAudio
        try:
            audio_thread.join(timeout=2)
        except Exception:
            pass
        try:
            recorder.p.terminate()
        except Exception:
            pass
        print("[系统] 程序已结束。")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="J.A.C. 多模态助手")
    parser.add_argument("--headless", action="store_true",
                        help="纯终端模式：完全不创建任何窗口（无 Qt / 无 OpenCV 窗口），"
                             "避开 macOS 27 等系统上 Qt/OpenCV 的 Metal 渲染崩溃")
    parser.add_argument("--console", action="store_true",
                        help="同 --headless，纯终端模式（推荐在 Metal 不兼容环境使用）")
    args = parser.parse_args()
    config = Config.load()
    if args.headless or args.console:
        # 纯终端模式：禁用一切窗口渲染，功能通过语音唤醒 + 控制台输入完成
        DISPLAY_ENABLED = False
        main()
    else:
        from gui import run_gui
        run_gui(config)
