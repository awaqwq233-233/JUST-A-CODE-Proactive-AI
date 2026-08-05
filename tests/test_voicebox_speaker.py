"""VoiceboxSpeaker 单元测试：mock Voicebox REST API，验证探活/克隆/情绪/降级。

不依赖真实 Voicebox 服务，也不触发 torch / qwen-tts 等重依赖。
运行：pytest tests/test_voicebox_speaker.py -v
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch, mock_open

# 把项目根加入 import 路径，使 `import src...` 可用（命名空间包）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import requests  # noqa: F401
    HAVE_REQUESTS = True
except ImportError:
    HAVE_REQUESTS = False

from src.audio.voicebox_tts import VoiceboxSpeaker


class _FakeResponse:
    """模拟 requests.Response：按端点返回不同内容。"""

    def __init__(self, status_code=200, content=b"", json_data=None):
        self.status_code = status_code
        self._content = content
        self._json = json_data if json_data is not None else {}

    @property
    def content(self):
        return self._content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json


@unittest.skipUnless(HAVE_REQUESTS, "requests 未安装，跳过 Voicebox 测试")
class TestVoiceboxSpeaker(unittest.TestCase):
    """VoiceboxSpeaker 行为测试（全程 mock 网络）。"""

    def _make_session(self, health_ok=True, profiles=None,
                      create_id="pid1", generate_content=b"0" * 100):
        """构造一个模拟的 requests.Session，按 URL 后缀返回对应 FakeResponse。"""
        session = MagicMock()

        def _get(url, **kw):
            if url.rstrip("/").endswith("/health"):
                return _FakeResponse(status_code=200 if health_ok else 503)
            if url.rstrip("/").endswith("/profiles"):
                return _FakeResponse(json_data={"profiles": profiles or []})
            return _FakeResponse()

        def _post(url, **kw):
            if url.rstrip("/").endswith("/profiles"):
                return _FakeResponse(json_data={"id": create_id})
            if "/samples" in url:
                return _FakeResponse(json_data={"ok": True})
            if url.rstrip("/").endswith("/generate"):
                return _FakeResponse(content=generate_content)
            return _FakeResponse()

        session.get.side_effect = _get
        session.post.side_effect = _post
        return session

    def _make_speaker(self, engine=None, **kw):
        """在 patch 掉 requests.Session 的前提下构造 VoiceboxSpeaker。"""
        session = self._make_session(**kw)
        with patch("src.audio.voicebox_tts.requests.Session", return_value=session):
            sp = VoiceboxSpeaker(engine=engine, output_dir="temp/test_voice")
        return sp

    def test_health_ok_sets_available(self):
        """/health 返回 200 时标记 available=True。"""
        sp = self._make_speaker(health_ok=True)
        self.assertTrue(sp.available)

    def test_health_fail_unavailable(self):
        """/health 不可达时 available=False（由上层回退系统 TTS）。"""
        sp = self._make_speaker(health_ok=False)
        self.assertFalse(sp.available)

    def test_profile_auto_clone_when_missing(self):
        """没有同名声纹时，自动新建并上传参考音做克隆。"""
        sp = self._make_speaker(profiles=[])  # 没有现成的 JAC 声纹
        pid = sp._resolve_profile()
        self.assertEqual(pid, "pid1")
        post_urls = [c.args[0] for c in sp.session.post.call_args_list]
        self.assertTrue(any(u.rstrip("/").endswith("/profiles") for u in post_urls))
        self.assertTrue(any("/samples" in u for u in post_urls))

    def test_reuse_existing_profile(self):
        """已有同名声纹时，直接复用、不再新建。"""
        sp = self._make_speaker(profiles=[{"name": "JAC", "id": "existing"}])
        pid = sp._resolve_profile()
        self.assertEqual(pid, "existing")
        post_urls = [c.args[0] for c in sp.session.post.call_args_list]
        self.assertFalse(any(u.rstrip("/").endswith("/profiles") for u in post_urls))

    def test_speak_injects_emotion_tags_and_plays(self):
        """合成时应把情绪标签内联进文本，并调用播放。"""
        sp = self._make_speaker()
        with patch("src.audio.playback.play_wav") as m_play, \
             patch("builtins.open", mock_open()):
            sp.speak("你好世界", emotion_hint="开心")
        m_play.assert_called_once()
        gen_payload = sp.session.post.call_args_list[-1].kwargs["json"]
        self.assertIn("[laugh]", gen_payload["text"])
        # 默认不传 engine：由 JAC 声纹绑定的模型决定
        self.assertNotIn("engine", gen_payload)

    def test_speak_passes_engine_when_explicitly_set(self):
        """显式设置 engine 时，合成请求应带上 engine 字段（覆盖默认行为）。"""
        sp = self._make_speaker(engine="chatterbox")
        with patch("src.audio.playback.play_wav"), patch("builtins.open", mock_open()):
            sp.speak("你好世界")
        gen_payload = sp.session.post.call_args_list[-1].kwargs["json"]
        self.assertEqual(gen_payload.get("engine"), "chatterbox")

    def test_speak_fallback_when_unavailable(self):
        """不可用时 speak 应回退系统 TTS，而非抛错。"""
        sp = self._make_speaker(health_ok=False)
        with patch.object(sp, "_fallback_speak") as m_fb:
            sp.speak("你好")
        m_fb.assert_called_once_with("你好")


if __name__ == "__main__":
    unittest.main()
