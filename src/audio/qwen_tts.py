import os
import sys
import time
import subprocess
import threading
import platform
import json
import struct
import warnings
from src.utils.net import setup_insecure_ssl

PLATFORM = platform.system()
IS_WINDOWS = PLATFORM == 'Windows'
IS_MACOS = PLATFORM == 'Darwin'
IS_LINUX = PLATFORM == 'Linux'

# 屏蔽 transformers / flash-attn 的提示性噪音（"flash-attn is not installed..."），
# 这是无害的性能提示，不是错误，避免刷屏淹没真正的运行日志。
warnings.filterwarnings("ignore", message=".*flash-attn.*")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")

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


def qwen_weights_ready(mode=None, model_name=None):
    """
    判断本机是否已有完整可用的 Qwen3-TTS 权重副本（无需联网下载即可加载）。

    返回 (ready: bool, detail: str)。
    - ready=True  ：本地副本存在且核心文件齐全，可直接 _ensure_model 离线加载。
    - ready=False ：缺失或不完整（半截下载 / 目录不存在），需联网下载或回退系统 TTS。

    用途：main.py / runtime.py 在创建扬声器前先调用本函数；
    若本地权重不齐，直接走 Speaker（系统 TTS）而非死守 QwenTTSSpeaker 的
    内部 say 兜底——否则会出现"程序认为 TTS 可用、实则全程静音"的坑。
    """
    requested_model = model_name or os.getenv("QWEN_TTS_MODEL") or (
        MODEL_FOR_MODE.get((mode or os.getenv("QWEN_TTS_MODE", "clone")).lower(),
                           MODEL_FOR_MODE["clone"])
    )
    seg = requested_model.split("/")[-1]
    local = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "..", "models", "qwen_tts", seg,
    )
    local = os.path.normpath(local)
    if not os.path.isdir(local):
        return False, f"本地目录不存在: {local}"
    required_files = (
        "config.json",
        "model.safetensors",
        "tokenizer_config.json",
        "vocab.json",
        "merges.txt",
        "speech_tokenizer/preprocessor_config.json",
        "speech_tokenizer/model.safetensors",
    )
    for key in required_files:
        full = os.path.join(local, *key.split("/"))
        if not _safetensors_complete(full):
            return False, f"权重不完整（缺/损坏: {key}）"
    return True, f"本地权重齐全: {local}"


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
        """初始化实例"""
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

        # 引擎包已安装：先快速探测本地权重是否齐全。
        # 若本地权重缺失/不完整，标记 available=False 并给出明确警告，
        # 让上层（main.py / runtime.py）直接回退系统 TTS，避免"程序以为 TTS 可用、
        # 实际 Qwen 模型加载失败后内部 say 兜底也静音"的全程无声音问题。
        ready, detail = qwen_weights_ready(mode=self.mode, model_name=self.model_name)
        if not ready:
            print(f"[TTS] 本地 Qwen3-TTS 权重未就绪（{detail}），"
                  f"QwenTTSSpeaker 标记为不可用，将回退系统 TTS。")
            self.available = False
            return

        # 本地权重齐全：标记可用，模型在首次 speak 时懒加载。
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
        """确保模型"""
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
        """选择设备"""
        if getattr(torch.cuda, "is_available", lambda: False)():
            return "cuda:0"
        mps = getattr(torch.backends, "mps", None)
        if mps is not None and mps.is_available():
            return "mps"
        return "cpu"

    @staticmethod
    def _pick_dtype(torch, dev):
        """选择数据类型"""
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
        """规范化emotion"""
        if not emotion_hint:
            return None
        s = str(emotion_hint)
        for k in EMOTION_INSTRUCT:
            if k in s:
                return k
        return None

    # ---------- 播放 ----------
    def _play(self, wav, sr):
        """播放合成出的音频。

        统一写出 WAV 后用平台命令（macOS: afplay / Windows: SoundPlayer / Linux: aplay）播放，
        并打印发声日志，便于排查"有合成但听不到声音"的问题。

        说明：早期用 sounddevice 直接播放，但它会按默认设备输出、在部分机器上选错设备导致
        全程静音且无任何报错；改为"写文件 + 系统播放器"后可靠得多，且日志可观测。
        """
        path = os.path.join(self.output_dir, f"qwen_{int(time.time() * 1000)}.wav")
        try:
            import soundfile as sf
            sf.write(path, wav, int(sr))
        except Exception as e:
            print(f"[错误] 无法写出 WAV（请安装 soundfile）: {e}")
            return
        print(f"[TTS] 正在播放（Qwen3-TTS）: {path}")
        play_wav(path)

    def _fallback_speak(self, text):
        """fallback朗读"""
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
    """playwav（带结果日志，便于排查静音）"""
    try:
        if IS_MACOS:
            import subprocess
            r = subprocess.run(["afplay", path], capture_output=True, text=True)
            if r.returncode != 0:
                print(f"[警告] afplay 播放失败（{path}）：{r.stderr.strip()[:200]}")
            else:
                print(f"[TTS] 播放完成: {path}")
        elif IS_WINDOWS:
            import subprocess
            ps = f'(New-Object Media.SoundPlayer("{path}")).PlaySync()'
            r = subprocess.run(["powershell", "-Command", ps], capture_output=True, text=True)
            if r.returncode != 0:
                print(f"[警告] Windows 播放失败（{path}）：{r.stderr.strip()[:200]}")
        elif IS_LINUX:
            import subprocess
            r = subprocess.run(["aplay", path], capture_output=True, text=True)
            if r.returncode != 0:
                print(f"[警告] aplay 播放失败（{path}）：{r.stderr.strip()[:200]}")
        else:
            print(f"[播放] {path}")
    except Exception as e:
        print(f"[警告] WAV 播放失败: {e} ({path})")
    """
    确保 qwen-tts 包可用：缺失时自动 pip 安装（国内优先清华镜像），
    并（可选）自动补全本地模型权重。返回是否可用。

    设计取舍：自动安装可能耗时数十秒甚至更久（含 2~4GB 权重下载），
    且需要联网；若失败则上层会回退系统 TTS，不会阻断启动。

    注意：本函数必须是模块级函数（main.py 以 ``qt.ensure_qwen_tts()`` 调用），
    切勿缩进进 QwenTTSSpeaker 类内部，否则会触发
    ``module 'src.audio.qwen_tts' has no attribute 'ensure_qwen_tts'``。
    """
    # 任何下载前先应用 SSL 设置（代理自签证书环境需要 JAC_HF_INSECURE=1）
    setup_insecure_ssl()

    try:
        import qwen_tts  # noqa: F401
        ok = True
    except ImportError:
        ok = False

    if ok:
        if autodownload:
            _maybe_download_weights()
        return True

    if not autoinstall:
        return False

    print("[TTS] 未检测到 qwen-tts 包，尝试自动安装…")
    # 优先清华镜像（国内网络 pypi.org 常被掐断），失败回退默认源
    candidates = [
        [sys.executable, "-m", "pip", "install", "-U", "qwen-tts",
         "-i", "http://pypi.tuna.tsinghua.edu.cn/simple",
         "--trusted-host", "pypi.tuna.tsinghua.edu.cn"],
        [sys.executable, "-m", "pip", "install", "-U", "qwen-tts"],
    ]
    installed = False
    for cmd in candidates:
        try:
            print(f"[TTS] 执行：{' '.join(cmd)}")
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if r.returncode == 0:
                installed = True
                break
            print(f"[TTS] 安装失败：{r.stderr.strip()[:200]}")
        except Exception as e:
            print(f"[TTS] 安装异常：{e}")

    if not installed:
        print("[TTS] qwen-tts 自动安装失败，将回退系统 TTS。")
        return False

    # 刷新导入，使本模块顶部的 QWEN_TTS_AVAILABLE 重新探测为 True
    try:
        import importlib
        importlib.reload(sys.modules[__name__])
    except Exception:
        pass

    if autodownload:
        _maybe_download_weights()
    return True


def _safetensors_complete(path):
    """判断 safetensors 文件是否完整：头部声明的张量数据总字节数必须 <= 磁盘实际大小。

    只检查"存在且非空"会被【截断的半截下载】骗过（头部在、但数据只下了一部分），
    必须比对头部声明大小才能识破。非 safetensors 或读不到头部一律返回 False。
    """
    try:
        if not path.endswith(".safetensors"):
            return os.path.exists(path) and os.path.getsize(path) > 0
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            return False
        with open(path, "rb") as f:
            magic = f.read(8)
            if len(magic) < 8:
                return False
            header_len = struct.unpack("<Q", magic)[0]
            header = json.loads(f.read(header_len))
        tensors = {k: v for k, v in header.items() if k != "__metadata__"}
        if not tensors:
            return False
        declared = max(v["data_offsets"][1] for v in tensors.values())
        expected = 8 + header_len + declared
        return os.path.getsize(path) >= expected
    except Exception:
        return False


def _maybe_download_weights():
    """若本地 Qwen3-TTS 权重缺失或不完整，自动运行 download_models.py 补全（clone 模式默认 1.7B-Base）。

    注意：之前仅凭"目录存在"判断已就绪，会导致下载被代理证书中断的【不完整副本】被误判为就绪、
    从而跳过补全（典型报错：speech_tokenizer 缺 preprocessor_config.json）。这里改为校验关键文件，
    且对 safetensors 额外做头部完整性校验，识破"存在但被截断"的半截下载。
    """
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        local = os.path.join(root, "models", "qwen_tts")
        # clone 模式默认需要的变体（与 MODEL_FOR_MODE["clone"] 一致）
        needed_variants = ["Qwen3-TTS-12Hz-1.7B-Base"]
        # 每个变体必须存在的核心文件；任一缺失/截断即视为不完整、需要补全。
        # 重要：Qwen3-TTS-12Hz-1.7B-Base 官方仓库【并不】包含 tokenizer.json
        # （已实测 HF 仓库文件清单确认）。其文本分词器为 Qwen2Tokenizer（慢速），
        # 仅依赖 tokenizer_config.json + vocab.json + merges.txt，实测可从本地目录
        # 正常加载、中文字符能正确还原，无需 tokenizer.json。把 tokenizer.json 当作
        # 必需文件会误判模型"不完整"→ 反复触发自动下载且永远无法满足。下面只校验真正
        # 决定能否加载的核心文件（含 model.safetensors 与 speech_tokenizer/model.safetensors
        # 两个权重，并对它们做 safetensors 头部完整性校验，防止半截下载骗过校验）。
        required_files = (
            "config.json",
            "model.safetensors",
            "tokenizer_config.json",
            "vocab.json",
            "merges.txt",
            "speech_tokenizer/preprocessor_config.json",
            "speech_tokenizer/model.safetensors",
        )

        missing = []
        for name in needed_variants:
            d = os.path.join(local, name)
            if not os.path.isdir(d):
                missing.append(name)
                continue
            for key in required_files:
                full = os.path.join(d, *key.split("/"))
                if not _safetensors_complete(full):
                    missing.append(name)
                    break
        if not missing:
            return

        script = os.path.join(root, "download_models.py")
        if not os.path.exists(script):
            return
        cmd = [sys.executable, script]
        # 代理自签证书环境：透传 --insecure，让 download_models.py 的 curl 加 -k 完整下载
        if os.environ.get("JAC_HF_INSECURE") == "1":
            cmd.append("--insecure")
        print(f"[TTS] 检测到本地 Qwen3-TTS 权重不完整（缺失变体：{missing}），"
              f"自动运行 download_models.py 补全…")
        # 前台运行；超时 30 分钟兜底
        subprocess.run(cmd, timeout=1800)
    except Exception as e:
        print(f"[TTS] 权重自动下载失败（已忽略，运行时将尝试在线拉取）：{e}")


if __name__ == "__main__":
    sp = QwenTTSSpeaker()
    sp.speak("你好，我是你的助手 J.A.C.，现在由 Qwen3-TTS 为我发声。", emotion_hint="热情")
