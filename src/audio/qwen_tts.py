import os
import time
import threading
import platform

PLATFORM = platform.system()
IS_WINDOWS = PLATFORM == 'Windows'
IS_MACOS = PLATFORM == 'Darwin'
IS_LINUX = PLATFORM == 'Linux'

# 外部推理引擎包 qwen-tts（官方 PyPI: pip install -U qwen-tts）
# 注意：这里导入的是外部包，不是本文件。未安装时 available=False，由上层回退系统 TTS。
try:
    import qwen_tts  # noqa: F401
    QWEN_TTS_AVAILABLE = True
except ImportError:
    qwen_tts = None
    QWEN_TTS_AVAILABLE = False


# 现有 8 种情绪 -> 自然语言指令（用于 custom/design 模式的 instruct 参数，
# 以及 clone 模式里作为文本前缀，依靠 Qwen3-TTS 的语义理解自适应语气）。
EMOTION_INSTRUCT = {
    "热情": "用热情、活力充沛的语气说",
    "平静": "用平静、温和的语气说",
    "关怀": "用温柔、关切的语气说",
    "鼓励": "用鼓励、充满希望的语气说",
    "开心": "用开心、轻快的语气说",
    "惊讶": "用惊讶、夸张的语气说",
    "悲伤": "用悲伤、低沉、带哭腔的语气说",
    "生气": "用愤怒、严厉的语气说",
}

DEFAULT_REF_WAV = "voices/silverwalf_voice.wav"
DEFAULT_REF_TEXT = "哎，场地限制，我还有更棒的点子没展示呢...看谁能让我火力全开，指不定哪天就能有比999更劲爆的大数字呢。"

# 各模式对应的默认模型（可用环境变量 QWEN_TTS_MODEL 覆盖）
MODEL_FOR_MODE = {
    "clone": "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
    "custom": "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
    "design": "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
}


class QwenTTSSpeaker:
    """
    使用 Qwen3-TTS 进行语音合成的播报器（开源、本地优先）。

    支持三种模式（环境变量 QWEN_TTS_MODE）：
      - clone  （默认）: 用 Base 模型 + 参考音做 3 秒声音克隆，保住 J.A.C. 音色。
      - custom : 用 CustomVoice 模型 + 内置说话人 + instruct 显式控制情绪/风格。
      - design : 用 VoiceDesign 模型，通过自然语言描述设计音色/情绪。

    接口与现有 Speaker 对齐：speak(text, emotion_hint=None)，
    并提供 available 标志。模型在首次 speak 时懒加载（2~4GB），避免拖慢启动。
    跨平台兼容 Windows / macOS / Linux，失败自动回退系统 TTS。
    """

    def __init__(self,
                 model_name=None,
                 mode=None,
                 ref_audio=None,
                 ref_text=None,
                 speaker=None,
                 language=None,
                 device=None,
                 dtype=None,
                 output_dir="temp/voice"):
        self.mode = (mode or os.getenv("QWEN_TTS_MODE", "clone")).lower()
        if self.mode not in MODEL_FOR_MODE:
            self.mode = "clone"

        default_model = MODEL_FOR_MODE[self.mode]
        self.model_name = model_name or os.getenv("QWEN_TTS_MODEL", default_model)
        self.ref_audio = ref_audio or os.getenv("QWEN_TTS_REF", DEFAULT_REF_WAV)
        self.ref_text = ref_text or os.getenv("QWEN_TTS_REF_TEXT", DEFAULT_REF_TEXT)
        self.speaker = speaker or os.getenv("QWEN_TTS_SPEAKER", "Vivian")
        self.language = language or os.getenv("QWEN_TTS_LANG", "Chinese")
        self.device = device or os.getenv("QWEN_TTS_DEVICE")  # 留空自动选择
        self.dtype = dtype  # None 时按设备自动选
        self.output_dir = output_dir

        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir, exist_ok=True)

        self.available = False
        self._model = None
        self._lock = threading.Lock()
        self._clone_prompt = None

        if not QWEN_TTS_AVAILABLE:
            print("[提示] 未安装 qwen-tts，QwenTTSSpeaker 不可用（将回退系统 TTS）。")
            return

        # 引擎包已安装：标记可用，模型在首次 speak 时懒加载。
        # 若加载失败会在运行时翻转为 False 并回退。
        self.available = True

    # ---------- 模型加载 ----------
    def _resolve_model_name(self):
        """
        返回实际加载用的模型标识：
          - 若 model_name 是仓库 ID（含 '/') 且项目内 models/qwen_tts/<末段> 已存在，
            优先使用本地副本（离线可用，且 download_models.py 下载即生效）。
          - 否则原样返回，由 qwen-tts 在 from_pretrained 时自动下载。
        """
        name = self.model_name
        if "/" in name and not os.path.exists(name):
            seg = name.split("/")[-1]
            local = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "..", "models", "qwen_tts", seg,
            )
            if os.path.isdir(local):
                return os.path.normpath(local)
        return name

    def _ensure_model(self):
        if self._model is not None:
            return True
        with self._lock:
            if self._model is not None:
                return True
            try:
                import torch
                from qwen_tts import Qwen3TTSModel

                dev = self.device or self._pick_device(torch)
                dt = self.dtype or self._pick_dtype(torch, dev)

                resolved = self._resolve_model_name()
                print(f"[系统] 正在加载 Qwen3-TTS 模型 {resolved} "
                      f"(device={dev}, dtype={dt}) …")
                try:
                    self._model = Qwen3TTSModel.from_pretrained(
                        resolved,
                        device_map=dev,
                        dtype=dt,
                    )
                except Exception as e:
                    # 本地副本加载失败（如不完整）时，回退到仓库 ID 自动下载
                    if resolved != self.model_name:
                        print(f"[提示] 本地副本加载失败，尝试自动下载 {self.model_name}: {e}")
                        self._model = Qwen3TTSModel.from_pretrained(
                            self.model_name,
                            device_map=dev,
                            dtype=dt,
                        )
                    else:
                        raise

                # 预构建克隆提示以复用，避免每次合成重复计算参考音
                if self.mode == "clone" and os.path.exists(self.ref_audio) and self.ref_text:
                    try:
                        self._clone_prompt = self._model.create_voice_clone_prompt(
                            ref_audio=self.ref_audio,
                            ref_text=self.ref_text,
                            x_vector_only_mode=False,
                        )
                        print("[系统] 声音克隆参考已就绪。")
                    except Exception as e:
                        print(f"[警告] 预构建声音克隆提示失败（将逐次克隆）: {e}")

                print("[系统] Qwen3-TTS 已就绪。")
                return True
            except Exception as e:
                print(f"[错误] Qwen3-TTS 加载失败: {e}")
                self.available = False
                return False

    @staticmethod
    def _pick_device(torch):
        if getattr(torch.cuda, "is_available", lambda: False)():
            return "cuda:0"
        mps = getattr(torch.backends, "mps", None)
        if mps is not None and mps.is_available():
            return "mps"
        return "cpu"

    @staticmethod
    def _pick_dtype(torch, dev):
        if dev.startswith("cuda"):
            return torch.bfloat16
        if dev == "mps":
            return torch.float16
        return torch.float32

    # ---------- 对外接口 ----------
    def speak(self, text, emotion_hint=None):
        if not self.available or not self._ensure_model():
            self._fallback_speak(text)
            return

        instruct = EMOTION_INSTRUCT.get(self._normalize_emotion(emotion_hint))
        try:
            if self.mode == "custom":
                args = dict(text=text, language=self.language, speaker=self.speaker)
                if instruct:
                    args["instruct"] = instruct
                wavs, sr = self._model.generate_custom_voice(**args)
            elif self.mode == "design":
                args = dict(text=text, language=self.language)
                if instruct:
                    args["instruct"] = instruct
                wavs, sr = self._model.generate_voice_design(**args)
            else:  # clone（默认）
                args = dict(text=text, language=self.language,
                            ref_audio=self.ref_audio, ref_text=self.ref_text)
                if self._clone_prompt is not None:
                    args["voice_clone_prompt"] = self._clone_prompt
                wavs, sr = self._model.generate_voice_clone(**args)

            if not wavs:
                raise RuntimeError("Qwen3-TTS 返回空音频")
            wav = wavs[0]
            self._play(wav, sr)
        except Exception as e:
            print(f"[错误] Qwen3-TTS 合成失败: {e}")
            self._fallback_speak(text)

    def _normalize_emotion(self, emotion_hint):
        if not emotion_hint:
            return None
        s = str(emotion_hint)
        for k in EMOTION_INSTRUCT:
            if k in s:
                return k
        return None

    # ---------- 播放 ----------
    def _play(self, wav, sr):
        # 优先用 sounddevice 直接播放（无中间文件）
        try:
            import sounddevice as sd
            sd.play(wav, int(sr))
            sd.wait()
            return
        except Exception:
            pass

        # 回退：写出 WAV 后用平台命令播放
        path = os.path.join(self.output_dir, f"qwen_{int(time.time() * 1000)}.wav")
        try:
            import soundfile as sf
            sf.write(path, wav, int(sr))
        except Exception as e:
            print(f"[错误] 无法写出 WAV（请安装 soundfile）: {e}")
            return
        play_wav(path)

    def _fallback_speak(self, text):
        """跨平台系统 TTS 兜底（macOS say / Linux espeak / 其它仅打印）。"""
        if IS_MACOS:
            try:
                import subprocess
                subprocess.run(["say", "-v", "Tingting", text],
                               capture_output=True, text=True)
                return
            except Exception:
                pass
        elif IS_LINUX:
            try:
                import subprocess
                subprocess.run(["espeak", text], capture_output=True, text=True)
                return
            except Exception:
                pass
        print(f"[J.A.C.(回退)] {text}")


def play_wav(path):
    """跨平台 WAV 播放（不依赖额外 Python 包，使用系统命令）。"""
    try:
        if IS_MACOS:
            import subprocess
            subprocess.run(["afplay", path], capture_output=True)
        elif IS_WINDOWS:
            import subprocess
            ps = f'(New-Object Media.SoundPlayer("{path}")).PlaySync()'
            subprocess.run(["powershell", "-Command", ps], capture_output=True)
        elif IS_LINUX:
            import subprocess
            subprocess.run(["aplay", path], capture_output=True)
        else:
            print(f"[播放] {path}")
    except Exception as e:
        print(f"[警告] WAV 播放失败: {e} ({path})")


if __name__ == "__main__":
    sp = QwenTTSSpeaker()
    sp.speak("你好，我是你的助手 J.A.C.，现在由 Qwen3-TTS 为我发声。", emotion_hint="热情")
