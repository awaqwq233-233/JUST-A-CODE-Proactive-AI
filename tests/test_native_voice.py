"""原生音色的缓存隔离、失败拒绝和后端确认回归，不开启真实设备。"""
import base64
import hashlib

import numpy as np
import pytest
import soundfile as sf

from src.omni import realtime_protocol as protocol
from src.omni import voice_conditioning as voice


def fake_condition(samples, model_dir):
    """返回合法 native bundle 形状，用于校验缓存生命周期而不加载模型。"""
    return {"spk_f32.bin": np.arange(1, 193, dtype="<f4"),
            "prompt_tokens_i32.bin": np.array([1, 2, 3, 4, 1, 2, 3], dtype="<i4"),
            "prompt_mel_btc_f32.bin": np.zeros((8, 80), dtype="<f4")}


def write_tone(path, frequency=200):
    """生成可区分的测试参考音，避免使用或保存用户实际录音。"""
    x = 0.1 * np.sin(2 * np.pi * frequency * np.arange(16000) / 16000)
    sf.write(path, x, 16000, subtype="FLOAT")
    return path


def test_cache_reuse_switch_and_corruption_are_explicit(tmp_path, monkeypatch):
    """同音色复用、不同参考隔离；损坏缓存必须拒绝，不默默换默认音色。"""
    calls = []

    def extract(samples, model_dir):
        """计数模拟的首次提取，确保重连不重复计算。"""
        calls.append(1)
        return fake_condition(samples, model_dir)

    monkeypatch.setattr(voice, "extract_condition", extract)
    a, b = write_tone(tmp_path / "a.wav"), write_tone(tmp_path / "b.wav", 300)
    cache = tmp_path / "cache"
    first = voice.prepare_voice(a, tmp_path, cache)
    repeated = voice.prepare_voice(a, tmp_path, cache)
    switched = voice.prepare_voice(b, tmp_path, cache)
    assert first["cache_hit"] is False and repeated["cache_hit"] is True
    assert first["voice_sha256"] == repeated["voice_sha256"] != switched["voice_sha256"]
    assert len(calls) == 2
    from pathlib import Path
    (Path(first["bundle_dir"]) / "spk_f32.bin").write_bytes(b"broken")
    with pytest.raises(ValueError, match="缓存校验失败"):
        voice.prepare_voice(a, tmp_path, cache)
    assert len(calls) == 2


@pytest.mark.parametrize("samples", [np.zeros(16000), np.ones(15999) * .1,
                                    np.ones(31 * 16000) * .1, np.ones(16000) * 2,
                                    np.full(16000, np.nan)])
def test_invalid_reference_fails_before_extraction(samples, tmp_path, monkeypatch):
    """静音、时长越界、过载与非有限值不能进入克隆前端。"""
    def forbidden(*args):
        """无效输入不应加载 ONNX 权重。"""
        pytest.fail("invalid reference reached extraction")

    monkeypatch.setattr(voice, "extract_condition", forbidden)
    audio = tmp_path / "invalid.wav"
    sf.write(audio, samples, 16000, subtype="FLOAT")
    with pytest.raises(ValueError):
        voice.prepare_voice(audio, tmp_path, tmp_path / "cache")


@pytest.mark.parametrize("condition", [None, {}, {"applied": False}, {"applied": True, "reference_sha256": "wrong"}, []])
def test_backend_must_acknowledge_the_requested_voice(condition):
    """缺少确认、声纹未应用或哈希不匹配，均不能假装克隆已生效。"""
    init = protocol.build_init("test", np.full(16000, 0.1, dtype="<f4"))
    with pytest.raises(ValueError, match="未确认应用指定音色"):
        protocol.require_voice_condition({"voice_conditioning": condition}, init)
    pcm = base64.b64decode(init["payload"]["voice"]["tts_ref_audio_base64"])
    ack = {"applied": True, "reference_sha256": hashlib.sha256(pcm).hexdigest()}
    protocol.require_voice_condition({"voice_conditioning": ack}, init)
    protocol.require_voice_condition({}, protocol.build_init("test", None))


def test_feature_shapes_finite_values_and_mean_normalization(tmp_path):
    """真实 CPU 特征计算与 native 维度一致，声纹输入逐维均值为零。"""
    samples = voice.load_audio(write_tone(tmp_path / "tone.wav"))
    spk = voice.speaker_features(samples)
    tokenizer = voice.tokenizer_features(samples)
    mel = voice.prompt_mel(samples, 25)
    assert spk.shape[-1] == 80 and tokenizer.shape == (1, 128, 100) and mel.shape == (50, 80)
    assert np.abs(spk.mean(axis=1)).max() < 1e-4
    assert all(np.isfinite(array).all() for array in (spk, tokenizer, mel))


def test_model_validation_requires_external_verified_resources(tmp_path, monkeypatch):
    """前端模型必须位于仓库外且匹配锁定的内容哈希。"""
    digest = hashlib.sha256(b"fixture").hexdigest()
    monkeypatch.setattr(voice, "frontend_lock", lambda: {"files": {"model.onnx": digest}})
    with pytest.raises(ValueError, match="仓库外"):
        voice.validate_models(voice.ROOT / "models")
    with pytest.raises(ValueError, match="SHA256"):
        voice.validate_models(tmp_path)
    (tmp_path / "model.onnx").write_bytes(b"fixture")
    voice.validate_models(tmp_path)
