"""VoiceboxSpeaker：通过开源 Voicebox 桌面 App 的 REST API 做克隆语音合成。

为什么用 Voicebox：
  J.A.C. 原用 Qwen3-TTS 做「声音克隆 + 情绪控制」，但 Qwen3-TTS 官方仅验证
  NVIDIA GPU + CUDA，在 macOS（Apple Silicon，无 NVIDIA 卡）上推理数值错乱，
  合成出无语义的「外星人噪音」（见 CHANGELOG 2026-08-05）。
  开源项目 voicebox.sh 是一个本地优先的 TTS 聚合 App（Tauri/Rust），内部集成
  多个引擎，并以 REST API（默认 http://127.0.0.1:17493）对外提供服务。其中
  适合 macOS（Apple Silicon / MLX 加速）且支持「中文 + 声音克隆」的引擎是
  Chatterbox 系列。因此本模块让 J.A.C. 走 Voicebox 的 HTTP 接口，用
  voices/silverwalf_voice.wav 克隆出 J.A.C. 的固定音色，替代易出 bug 的 Qwen3-TTS。

接口约定（已通过 OpenAPI /openapi.json 实测 voicebox API v0.5.0，端点/字段以本文件为准）：
  - GET  /health                        探活（服务未启动即连接失败）
  - GET  /profiles                      列出已建声纹（list[{id, name, ...}]）
  - POST /profiles                      新建声纹（body: {name, language}）
  - POST /profiles/{id}/samples         上传参考音做克隆（multipart 文件字段 "file"）
  - POST /generate                      提交合成任务（body: {text, profile_id, engine?,
                                          language, instruct?}）→ 返回 JSON，含 generation id
                                          （异步：status="generating"，需轮询取音频）
  - GET  /audio/{id}                    按 generation id 取音频字节（audio/x-wav，PCM WAV）；
                                          生成中服务端返回 HTTP 500，需重试直到拿到 >44 字节
  注意：旧实现把 /generate 的 JSON 响应整个当 WAV 写入，导致 afplay 报
  AudioFileOpen failed ('typ?')。正确做法是提交后轮询 /audio/{id} 取真正音频。

引擎选择：本模块**不硬编码 TTS 引擎**。合成时只传 JAC 声纹的 profile_id，由 Voicebox
  用该声纹在 App 内绑定的模型发声（用户需求：声纹是什么模型就用什么模型）。仅当显式
  设置 VOICEBOX_ENGINE 环境变量时才会覆盖此行为。

情绪保留：原 Qwen3-TTS 支持 8 种情绪自然语言控制。Voicebox 底层的 Chatterbox Turbo
支持内联副语言标签（[laugh] [sigh] [gasp] [excited] [whisper] …），本模块把 8 种情绪
映射成这些标签（拼到 text 最前）+ 一条 instruct 自然语言指令。引擎不支持 instruct
时忽略该字段，不影响克隆音色本身。

降级：服务没开 / 合成失败 / 声纹克隆失败 → 一律回退系统 TTS（macOS say -v Tingting），
绝不阻断主程序。
"""
import os
import sys
import time
import threading
import platform
import subprocess
import uuid

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    requests = None
    REQUESTS_AVAILABLE = False

PLATFORM = platform.system()
IS_MACOS = PLATFORM == 'Darwin'
IS_WINDOWS = PLATFORM == 'Windows'
IS_LINUX = PLATFORM == 'Linux'

# ---------- 默认配置（均可用环境变量覆盖） ----------
DEFAULT_URL = "http://127.0.0.1:17493"
DEFAULT_ENGINE = ""                     # 留空=不指定引擎，由 JAC 声纹在 Voicebox 中绑定的引擎决定
DEFAULT_PROFILE_NAME = "JAC"           # 克隆声纹名（自动建/复用）
DEFAULT_REF_WAV = "voices/silverwalf_voice.wav"
DEFAULT_REF_TEXT = "哎，场地限制，我还有更棒的点子没展示呢...看谁能让我火力全开，指不定哪天就能有比999更劲爆的大数字呢。"
DEFAULT_LANGUAGE = "zh"
DEFAULT_FALLBACK_VOICE = "Tingting"    # macOS say 的中文嗓色（兜底用）

# 8 种情绪 -> Chatterbox Turbo 副语言标签 + instruct 自然语言指令
# Chatterbox Turbo 支持内联标签：[laugh] [sigh] [gasp] [chuckle] [cough]
# [sniff] [slow] [fast] [excited] [whisper] 等。
EMOTION_TO_VOICEBOX = {
    "热情": {"tags": ["[excited]"], "instruct": "用热情、活力充沛的语气说"},
    "平静": {"tags": [], "instruct": "用平静、温和的语气说"},
    "关怀": {"tags": ["[whisper]"], "instruct": "用温柔、关切的语气说"},
    "鼓励": {"tags": ["[excited]"], "instruct": "用鼓励、充满希望的语气说"},
    "开心": {"tags": ["[laugh]"], "instruct": "用开心、轻快的语气说"},
    "惊讶": {"tags": ["[gasp]"], "instruct": "用惊讶、夸张的语气说"},
    "悲伤": {"tags": ["[sigh]"], "instruct": "用悲伤、低沉、带哭腔的语气说"},
    "生气": {"tags": [], "instruct": "用愤怒、严厉的语气说"},
}


class VoiceboxSpeaker:
    """通过 Voicebox REST API 做克隆语音合成的播报器（开源、本地优先）。

    接口与现有 Speaker / QwenTTSSpeaker 对齐：speak(text, emotion_hint=None)，
    并暴露 available 标志。初始化时仅做 /health 探活，声纹克隆懒加载到首次
    speak（避免拖慢启动、且克隆 1MB 参考音需要一点时间）。
    """

    def __init__(self,
                 url=None,
                 engine=None,
                 profile_name=None,
                 ref_wav=None,
                 ref_text=None,
                 language=None,
                 fallback_voice=None,
                 output_dir="temp/voice"):
        """初始化实例（仅探活，不加载模型/声纹）"""
        self.base_url = url or os.getenv("VOICEBOX_URL", DEFAULT_URL)
        self.engine = engine or os.getenv("VOICEBOX_ENGINE") or None  # 留空则用声纹绑定的引擎
        self.profile_name = profile_name or os.getenv("VOICEBOX_PROFILE_NAME", DEFAULT_PROFILE_NAME)
        self.ref_wav = ref_wav or os.getenv("VOICEBOX_REF_WAV", DEFAULT_REF_WAV)
        self.ref_text = ref_text or os.getenv("VOICEBOX_REF_TEXT", DEFAULT_REF_TEXT)
        self.language = language or os.getenv("VOICEBOX_LANGUAGE", DEFAULT_LANGUAGE)
        self.fallback_voice = fallback_voice or os.getenv("VOICEBOX_FALLBACK_VOICE", DEFAULT_FALLBACK_VOICE)
        self.output_dir = output_dir

        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir, exist_ok=True)

        self.available = False
        self._profile_id = None
        self._lock = threading.Lock()
        # 串行锁：回灌线程与句子级桥接线程会并发调用 speak，串行化「合成+写临时文件+
        # 播放」避免同毫秒临时文件互覆盖与并发 afplay 重叠（修复"答案没读清/重叠"）
        self._speak_lock = threading.RLock()

        if not REQUESTS_AVAILABLE:
            print("[提示] 未安装 requests，VoiceboxSpeaker 不可用（将回退系统 TTS）。")
            return

        self.session = requests.Session()
        # 绕过本机代理劫持 localhost：本机若开了 HTTP/SOCKS 代理，requests 默认会把
        # 127.0.0.1 的请求也走代理，导致 /generate /audio 被代理成 502/504 而轮询超时
        # （日志表现为「Voicebox 轮询音频超时：None」）。trust_env=False + 显式
        # proxies=None 彻底禁用环境代理；与 omni 的 _ensure_no_proxy() 思路一致（自包含，
        # 不依赖调用方先调过它）。
        self.session.trust_env = False
        self.session.proxies = {"http": None, "https": None}
        # 探活：服务未启动则直接标记不可用，由上层回退；超时设短避免拖慢启动。
        try:
            if self._check_health():
                self.available = True
                print(f"[TTS] Voicebox 服务已连接: {self.base_url}（使用 JAC 声纹绑定的引擎）")
            else:
                print(f"[TTS] Voicebox 服务未响应（{self.base_url}），将回退系统 TTS。")
        except Exception as e:
            print(f"[TTS] Voicebox 服务未启动（{e}），将回退系统 TTS。")

    # ---------- 探活 ----------
    def _check_health(self):
        """探测 Voicebox 服务是否在线（/health 返回 200 即视为可用）。"""
        try:
            r = self.session.get(f"{self.base_url}/health", timeout=2.0)
            return r.status_code == 200
        except Exception:
            return False

    # ---------- 声纹（克隆音色）管理 ----------
    def _resolve_profile(self):
        """解析/准备 J.A.C. 克隆声纹，返回 profile_id（失败返回 None 用默认音色）。

        懒加载 + 加锁：首次 speak 时调用；若已存在同名声纹则直接复用，
        否则新建并上传参考音做克隆。任何异常都不致命——返回 None 让合成走默认音色。
        """
        with self._lock:
            if self._profile_id is not None:
                return self._profile_id
            try:
                # 1) 列出已有声纹，找同名
                r = self.session.get(f"{self.base_url}/profiles", timeout=10)
                r.raise_for_status()
                data = r.json()
                profiles = data.get("profiles", data) if isinstance(data, dict) else data
                for p in profiles:
                    if p.get("name") == self.profile_name:
                        self._profile_id = p.get("id") or p.get("profile_id")
                        print(f"[TTS] 复用已有声纹: {self.profile_name} ({self._profile_id})")
                        return self._profile_id
                # 2) 没找到则新建 + 克隆
                self._profile_id = self._create_profile()
            except Exception as e:
                print(f"[警告] 声纹准备失败（将用默认音色）: {e}")
                self._profile_id = None
            return self._profile_id

    def _create_profile(self):
        """新建声纹并上传参考音做克隆，返回新建的 profile_id。"""
        # 新建声纹（body 字段按调研；失败不影响后续，返回 None 即可）
        r = self.session.post(
            f"{self.base_url}/profiles",
            json={"name": self.profile_name, "language": self.language},
            timeout=15,
        )
        r.raise_for_status()
        pid = (r.json() or {}).get("id") or (r.json() or {}).get("profile_id")
        if not pid:
            return None
        # 上传参考音做克隆（multipart 文件字段名 "file"）
        if os.path.exists(self.ref_wav):
            with open(self.ref_wav, "rb") as f:
                files = {"file": (os.path.basename(self.ref_wav), f, "audio/wav")}
                sr = self.session.post(
                    f"{self.base_url}/profiles/{pid}/samples",
                    files=files, timeout=60,
                )
                sr.raise_for_status()
            print(f"[TTS] 已为 {self.profile_name} 上传参考音克隆音色: {self.ref_wav}")
        return pid

    # ---------- 对外接口 ----------
    def speak(self, text, emotion_hint=None):
        """合成并播放语音；不可用时回退系统 TTS。"""
        with self._speak_lock:
            if not self.available:
                self._fallback_speak(text)
                return

            # 解析情绪 -> 内联标签 + instruct 指令
            em = self._normalize_emotion(emotion_hint)
            entry = EMOTION_TO_VOICEBOX.get(em)
            tags = "".join(entry["tags"]) if entry else ""
            instruct = entry["instruct"] if entry else ""
            gen_text = f"{tags}{text}" if tags else text

            profile_id = self._resolve_profile()
            payload = {
                "text": gen_text,
                "language": self.language,
            }
            # 关键：不指定引擎。只传 JAC 声纹的 profile_id，由 Voicebox 用该声纹在 App 里
            # 绑定的那个模型来发声（用户的需求：声纹是什么模型就用什么模型）。
            # 仅当显式设置 VOICEBOX_ENGINE 环境变量时才覆盖此行为。
            if self.engine:
                payload["engine"] = self.engine
            if profile_id:
                payload["profile_id"] = profile_id
            if instruct:
                payload["instruct"] = instruct

            try:
                # 1) 提交合成任务（异步）：/generate 返回 generation id（JSON），而非音频字节。
                #    旧实现把这段 JSON 直接当 WAV 写入，导致 afplay 报 AudioFileOpen failed ('typ?')。
                resp = self.session.post(f"{self.base_url}/generate", json=payload, timeout=60)
                resp.raise_for_status()
                data = resp.json()
                gen_id = data.get("id")
                if not gen_id:
                    raise RuntimeError(f"Voicebox /generate 未返回 id：{data}")
                # 2) 轮询 /audio/{id} 直到音频就绪（生成中服务端返回 HTTP 500，需重试）
                wav_bytes = self._poll_audio(gen_id)
                # 3) 校验 WAV 魔数（RIFF....WAVE），避免再次出现无效文件
                if not wav_bytes or wav_bytes[:4] != b"RIFF" or wav_bytes[8:12] != b"WAVE":
                    raise RuntimeError("Voicebox 返回的音频不是合法 WAV")
                # 4) 写文件并播放；播放失败（afplay 报错）则以系统 TTS 兜底
                #    文件名用 uuid 而非毫秒时间戳，彻底避免回灌/桥接并发同毫秒互覆盖
                path = os.path.join(self.output_dir, f"voicebox_{uuid.uuid4().hex}.wav")
                with open(path, "wb") as f:
                    f.write(wav_bytes)
                print(f"[TTS] 正在播放（Voicebox / {self.engine or 'JAC声纹引擎'}）: {path}")
                from src.audio.playback import play_wav
                if not play_wav(path):
                    raise RuntimeError("Voicebox 音频播放失败（afplay）")
            except Exception as e:
                print(f"[错误] Voicebox 合成/播放失败: {e}")
                self._fallback_speak(text)

    def _poll_audio(self, gen_id, timeout=None, interval=0.5):
        """轮询 /audio/{id} 直到返回可用音频字节（Voicebox /generate 是异步的）。

        /generate 提交后 status="generating"，此时 GET /audio/{id} 返回 HTTP 500；
        持续轮询，直到拿到非空音频字节即视为就绪并返回（下游 speak() 再做 RIFF 魔数
        校验，确保不是非法响应）。超时则抛异常并附「最近 HTTP 状态」便于一眼区分
        代理(502/504) vs 服务端慢(500 vs 无响应)，由 speak() 的 except 切换到系统 TTS 兜底。

        Args:
            timeout: 总轮询超时（秒）。默认读环境变量 VOICEBOX_POLL_TIMEOUT（120s）；
                     升级结果与长句合成较慢，放宽到 120s 避免误超时。
            interval: 轮询间隔（秒）。
        """
        if timeout is None:
            timeout = float(os.getenv("VOICEBOX_POLL_TIMEOUT", "120"))
        deadline = time.time() + timeout
        last_status = None
        last_err = None
        while time.time() < deadline:
            try:
                # 单次 GET 超时降到 10s：代理/服务端 hang 能更快暴露，窗口内可多轮询
                r = self.session.get(f"{self.base_url}/audio/{gen_id}", timeout=10)
                last_status = r.status_code
                # 音频就绪：HTTP 200 且返回了非空字节（生成中服务端返回 500 → 跳过继续轮询）。
                # 不再依赖「>44 字节」硬判断，避免恰好 44 字节头导致误判；下游 RIFF 魔数校验兜底。
                if r.status_code == 200 and len(r.content) > 0:
                    return r.content
            except Exception as e:  # 生成中服务端返回 500 等，忽略并继续轮询
                last_err = e
            time.sleep(interval)
        status_hint = f"最近HTTP状态={last_status}" if last_status is not None else "无HTTP响应"
        err_hint = f"（{last_err}）" if last_err else ""
        raise RuntimeError(f"Voicebox 轮询音频超时（{gen_id}）：{status_hint}{err_hint}")

    @staticmethod
    def _normalize_emotion(emotion_hint):
        """把任意情绪提示规整为 8 种之一（子串匹配，找不到返回 None）。"""
        if not emotion_hint:
            return None
        s = str(emotion_hint)
        for k in EMOTION_TO_VOICEBOX:
            if k in s:
                return k
        return None

    # ---------- 播放兜底 ----------
    def _fallback_speak(self, text):
        """Voicebox 不可用时的系统 TTS 兜底（与 QwenTTSSpeaker 一致）。"""
        if IS_MACOS:
            try:
                subprocess.run(["say", "-v", self.fallback_voice, text],
                               capture_output=True, text=True)
                return
            except Exception:
                pass
        elif IS_LINUX:
            try:
                subprocess.run(["espeak", text], capture_output=True, text=True)
                return
            except Exception:
                pass
        print(f"[J.A.C.(回退)] {text}")


if __name__ == "__main__":
    sp = VoiceboxSpeaker()
    sp.speak("你好，我是你的助手 J.A.C.，现在由 Voicebox 为我发声。", emotion_hint="热情")
