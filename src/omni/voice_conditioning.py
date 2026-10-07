"""本地 CPU/ONNX 音色前端；派生条件由原生 C++ Token2Wav 使用。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile

import numpy as np

from .realtime_protocol import load_audio

ROOT = Path(__file__).resolve().parents[2]
FRONTEND_VERSION = 1
BUNDLE_FILES = {"spk_f32.bin": "<f4", "prompt_tokens_i32.bin": "<i4", "prompt_mel_btc_f32.bin": "<f4"}


def frontend_lock() -> dict:
    """读取独立锁定的官方音色前端资源，不变更原有 GGUF 哈希。"""
    return json.loads((ROOT / "backend.lock.json").read_text(encoding="utf-8"))["voice_frontend"]


def file_digest(path: Path) -> str:
    """逐块计算文件 SHA256，避免将 ONNX 权重完整复制进内存。"""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_models(directory: Path) -> None:
    """拒绝仓库内权重、缺失模块与哈希不符的音色模型。"""
    directory = Path(directory).expanduser().resolve()
    if directory == ROOT or ROOT in directory.parents:
        raise ValueError("音色 ONNX 模型必须保存在仓库外")
    for name, digest in frontend_lock()["files"].items():
        path = directory / name
        if not path.is_file() or file_digest(path) != digest:
            raise ValueError(f"音色前端资源缺失或 SHA256 不符: {name}")


def download_models(directory: Path) -> None:
    """以 HTTPS 下载固定资源，国内镜像失败回退官方，校验后原子落盘。"""
    import httpx
    directory = Path(directory).expanduser().resolve()
    if directory == ROOT or ROOT in directory.parents:
        raise ValueError("音色 ONNX 模型必须保存在仓库外")
    directory.mkdir(parents=True, exist_ok=True)
    lock = frontend_lock()
    for name, digest in lock["files"].items():
        destination = directory / name
        if destination.is_file() and file_digest(destination) == digest:
            continue
        errors = []
        for host in ("https://hf-mirror.com", "https://huggingface.co"):
            url = f"{host}/{lock['repo_id']}/resolve/{lock['revision']}/assets/token2wav/{name}?download=true"
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=directory, delete=False, suffix=".part") as stream:
                    temporary = Path(stream.name)
                    with httpx.Client(follow_redirects=True, timeout=60) as client, client.stream("GET", url) as response:
                        response.raise_for_status()
                        for block in response.iter_bytes(1024 * 1024):
                            stream.write(block)
                if file_digest(temporary) != digest:
                    raise ValueError("下载内容 SHA256 不符")
                temporary.replace(destination)
                break
            except (OSError, ValueError, httpx.HTTPError) as error:
                errors.append(type(error).__name__)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        else:
            raise RuntimeError(f"音色资源下载失败: {name} ({', '.join(errors)})")
    validate_models(directory)


def speaker_features(samples: np.ndarray) -> np.ndarray:
    """用原生 Kaldi fbank 复现 StepAudio2 CAMPPlus 的 80 维均值归一化输入。"""
    import kaldi_native_fbank as knf
    options = knf.FbankOptions()
    options.frame_opts.dither = 0
    options.frame_opts.samp_freq = 16000
    options.mel_opts.num_bins = 80
    fbank = knf.OnlineFbank(options)
    fbank.accept_waveform(16000, samples.tolist())
    fbank.input_finished()
    features = np.stack([fbank.get_frame(i) for i in range(fbank.num_frames_ready)]).astype("float32")
    return (features - features.mean(axis=0, keepdims=True))[None]


def tokenizer_features(samples: np.ndarray) -> np.ndarray:
    """按 S3Tokenizer 的 400/160 Hann STFT 生成 128 维 log-mel，不加载 torch。"""
    import librosa
    spectrum = librosa.stft(samples, n_fft=400, hop_length=160, window="hann", center=True, pad_mode="reflect")
    power = np.abs(spectrum[:, :-1]) ** 2
    filters = librosa.filters.mel(sr=16000, n_fft=400, n_mels=128)
    log_mel = np.log10(np.maximum(np.einsum("mf,ft->mt", filters, power), 1e-10))
    log_mel = np.maximum(log_mel, log_mel.max() - 8)
    return np.ascontiguousarray(((log_mel + 4) / 4)[None], dtype="float32")


def prompt_mel(samples: np.ndarray, token_count: int) -> np.ndarray:
    """复现 StepAudio2 的 24k 1920/480 mel，并补齐 token*2 帧的参考条件。"""
    import librosa
    import soxr
    wave = soxr.resample(samples, 16000, 24000).astype("float32")
    wave = np.pad(wave, (720, 720), mode="reflect")
    spectrum = librosa.stft(wave, n_fft=1920, hop_length=480, window="hann", center=False)
    magnitude = np.sqrt(np.abs(spectrum) ** 2 + 1e-9)
    filters = librosa.filters.mel(sr=24000, n_fft=1920, n_mels=80, fmin=0, fmax=8000)
    mel = np.log(np.maximum(np.einsum("mf,ft->mt", filters, magnitude), 1e-5)).T
    target = token_count * 2
    if len(mel) < target:
        mel = np.pad(mel, ((0, target - len(mel)), (0, 0)), mode="edge")
    return np.ascontiguousarray(mel[:target], dtype="<f4")


def extract_condition(samples: np.ndarray, model_dir: Path) -> dict[str, np.ndarray]:
    """仅在缓存缺失时用两个 CPU ONNX 模型提取声纹、语音 token 与 mel。"""
    import onnxruntime as ort
    validate_models(model_dir)
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    speaker = ort.InferenceSession(str(model_dir / "campplus.onnx"), options, providers=["CPUExecutionProvider"])
    embedding = speaker.run(None, {speaker.get_inputs()[0].name: speaker_features(samples)})[0].reshape(-1)
    del speaker
    tokenizer = ort.InferenceSession(str(model_dir / "speech_tokenizer_v2_25hz.onnx"), options,
                                    providers=["CPUExecutionProvider"])
    features = tokenizer_features(samples)
    inputs = tokenizer.get_inputs()
    feed = {inputs[0].name: features, inputs[1].name: np.array([features.shape[-1]], dtype="int32")}
    tokens = np.asarray(tokenizer.run(None, feed)[0][0], dtype="<i4")
    del tokenizer
    if embedding.shape != (192,) or len(tokens) < 4 or not np.isfinite(embedding).all():
        raise ValueError("音色前端返回无效声纹或语音 token")
    if np.any(tokens < 0) or np.any(tokens >= 6561):
        raise ValueError("音色参考 token 超出模型词表")
    # 固定 C++ setup_cache 要求尾部附 3 个 lookahead token，mel 不包含这 3 个 token。
    return {"spk_f32.bin": np.asarray(embedding, dtype="<f4"),
            "prompt_tokens_i32.bin": np.concatenate((tokens, tokens[:3])),
            "prompt_mel_btc_f32.bin": prompt_mel(samples, len(tokens))}


def validate_bundle(directory: Path, voice_hash: str) -> dict:
    """校验完整缓存、前端版本、内容哈希和 native bundle 尺寸，损坏时拒绝复用。"""
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if (manifest["frontend_version"] != FRONTEND_VERSION or manifest["voice_sha256"] != voice_hash
            or manifest["models"] != frontend_lock()["files"]):
        raise ValueError("音色缓存版本或参考音不符")
    arrays = {}
    for name, dtype in BUNDLE_FILES.items():
        path = directory / name
        if file_digest(path) != manifest["files"][name]:
            raise ValueError("音色缓存校验失败")
        arrays[name] = np.fromfile(path, dtype=dtype)
    spk, tokens, mel = (arrays[name] for name in BUNDLE_FILES)
    if (spk.size != 192 or tokens.size < 7 or manifest["token_count"] != tokens.size - 3
            or mel.size != (tokens.size - 3) * 160
            or not np.isfinite(spk).all() or not np.isfinite(mel).all()
            or np.linalg.norm(spk) == 0 or np.any(tokens < 0) or np.any(tokens >= 6561)):
        raise ValueError("音色缓存尺寸或数值无效")
    return manifest


def prepare_voice(audio: Path, model_dir: Path, cache_dir: Path) -> dict:
    """以规范化 PCM 的哈希隔离参考音缓存；原子提交，失败不使用默认音色。"""
    samples = load_audio(Path(audio))
    if not 16000 <= len(samples) <= 30 * 16000:
        raise ValueError("音色参考须为 1–30 秒")
    if np.max(np.abs(samples)) > 1.05 or np.sqrt(np.mean(samples.astype("float64") ** 2)) < 1e-4:
        raise ValueError("音色参考为空白或振幅无效")
    digest = hashlib.sha256(samples.tobytes()).hexdigest()
    cache_dir = Path(cache_dir).expanduser().resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    directory = cache_dir / f"v{FRONTEND_VERSION}-{digest}"
    if directory.exists():
        manifest = validate_bundle(directory, digest)
        return {"bundle_dir": str(directory), "voice_sha256": digest, "cache_hit": True,
                "token_count": manifest["token_count"]}
    condition = extract_condition(samples, Path(model_dir))
    with tempfile.TemporaryDirectory(prefix=".prepare-", dir=cache_dir) as temporary:
        temporary = Path(temporary)
        for name, values in condition.items():
            values.tofile(temporary / name)
        manifest = {"frontend_version": FRONTEND_VERSION, "voice_sha256": digest,
                    "models": frontend_lock()["files"], "token_count": len(condition["prompt_tokens_i32.bin"]) - 3,
                    "files": {name: file_digest(temporary / name) for name in BUNDLE_FILES}}
        (temporary / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        validate_bundle(temporary, digest)
        try:
            temporary.rename(directory)
        except FileExistsError:
            validate_bundle(directory, digest)
    return {"bundle_dir": str(directory), "voice_sha256": digest, "cache_hit": False,
            "token_count": manifest["token_count"]}
