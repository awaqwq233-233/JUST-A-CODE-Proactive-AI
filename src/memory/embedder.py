"""
J.A.C. 持久记忆子系统 —— 轻量 embedding 生成器（Phase 4，向量检索）

封装 fastembed（基于 ONNX Runtime，无需 torch，CPU 可跑，跨平台）。
设计原则：
  - **lazy init**：构造仅记录参数，第一次 embed 才 import fastembed 并加载模型，
    避免无网络/缺包时 import 阶段崩溃。
  - **降级**：模型不可用（缺包/无网络/下载失败）→ ``available=False``，
    ``embed_texts`` 返回 ``None``，调用方自动回退纯关键词检索。
  - **模型可配置**：默认 ``paraphrase-multilingual-MiniLM-L12-v2``（中英通吃、免前缀）；
    可选 ``BAAI/bge-small-zh-v1.5``（中文更准，需 query/passage 前缀）。
    环境变量 ``MEMORY_EMBED_MODEL`` 可覆盖。

国内首次加载需从 HuggingFace 下载权重，请在运行环境设置：
    HF_ENDPOINT=https://hf-mirror.com
（详见 DEPLOY_GUIDE.txt）
"""

from __future__ import annotations

import os
from typing import List, Optional

# 注意：fastembed 的 TextEmbedding 要求带命名空间前缀的模型名（裸名会报
# "is not supported in TextEmbedding"）。paraphrase-multilingual 系列是真正的
# 多语言模型，中英通吃、无需 query/passage 前缀，作为默认最稳妥。
_DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def _apply_hf_mirror():
    """加载向量模型前，自动配置 HuggingFace 镜像与按需关闭 SSL 校验。

    国内网络直连 huggingface.co 常被掐断，导致 fastembed 权重下载失败、
    Embedder 无声降级为关键词检索。这里：
      - 未显式设置 HF_ENDPOINT 时，默认指向 hf-mirror.com 镜像；
      - 若 JAC_HF_INSECURE=1（代理/防火墙 TLS 拦截环境），关闭 SSL 证书校验，
        使下载能正常进行（仅限可信内网）。

    已在环境变量显式配置的情况下尊重用户设置，不覆盖。
    """
    if not os.environ.get("HF_ENDPOINT"):
        os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
        print("[Embedder] 未检测到 HF_ENDPOINT，已默认使用镜像 https://hf-mirror.com 下载向量模型。")
    else:
        print(f"[Embedder] 使用现有 HF_ENDPOINT={os.environ['HF_ENDPOINT']} 下载向量模型。")

    # 向量模型缓存目录：fastembed 在未显式设置 FASTEMBED_CACHE_PATH 时，默认会把
    # 权重落到系统临时目录（macOS 为 /var/folders/.../T/fastembed_cache）。该目录在
    # 重启或清理临时文件后会被清空，导致每次启动都要重新联网下载。这里固定到一个
    # 用户级持久目录，避免重复下载、也顺带让缓存与 HF 主缓存（~/.cache/huggingface）
    # 区分开，便于排查。
    if not os.environ.get("FASTEMBED_CACHE_PATH"):
        _fb_cache = os.path.join(os.path.expanduser("~"), ".cache", "fastembed")
        os.makedirs(_fb_cache, exist_ok=True)
        os.environ["FASTEMBED_CACHE_PATH"] = _fb_cache
        print(f"[Embedder] 向量模型缓存目录固定为 {_fb_cache}（避免临时目录被清理后重复下载）")

    if os.environ.get("JAC_HF_INSECURE") == "1":
        os.environ["HF_HUB_DISABLE_SSL_VERIFY"] = "1"
        os.environ["PYTHONHTTPSVERIFY"] = "0"
        try:
            import ssl
            ssl._create_default_https_context = ssl._create_unverified_context
        except Exception:
            pass
        print("[Embedder] JAC_HF_INSECURE=1：已关闭 SSL 证书校验（仅限可信内网/代理环境）。")


class MemoryEmbedder:
    """轻量文本向量生成器（lazy、可降级）。"""

    def __init__(self, model_name: Optional[str] = None) -> None:
        """初始化实例"""
        self.model_name = (model_name or os.environ.get("MEMORY_EMBED_MODEL") or _DEFAULT_MODEL).strip()
        # bge / e5 系列需要 query: / passage: 前缀才能发挥检索效果
        self._needs_prefix = ("bge" in self.model_name.lower()) or ("e5" in self.model_name.lower())
        self._model = None
        self._dim: Optional[int] = None
        self.available: bool = False
        # 加载熔断标志：保证 _ensure_loaded 全进程只真正尝试一次，避免刷屏
        self._load_attempted: bool = False

    # ------------------------- lazy load -------------------------

    def _ensure_loaded(self) -> bool:
        """确保已加载。

        熔断机制：整个进程生命周期内只尝试加载一次。无论成功或失败，
        后续调用直接返回缓存的 available 状态，不再重复连接 HuggingFace /
        重试模型加载，避免每轮对话（每次 embed）都刷屏打印镜像信息与失败原因。
        """
        # 已尝试过加载（成功或失败）：直接返回缓存结果，不再重试、不再打印
        if self._load_attempted:
            return self.available
        self._load_attempted = True

        # 下载权重前先应用 HF 镜像 / SSL 设置（国内网络必需），提高首次加载成功率
        try:
            _apply_hf_mirror()
        except Exception as e:
            print(f"[Embedder] 配置 HF 镜像失败（可忽略）：{e}")
        try:
            from fastembed import TextEmbedding  # 延迟 import，缺包时不崩
        except Exception as e:
            print(f"[Embedder] fastembed 不可用，向量检索降级为关键词（{e}）")
            self.available = False
            self._model = None
            return False
        try:
            self._model = TextEmbedding(model_name=self.model_name)
            # 探测维度（嵌入一个空串以拿到模型输出维度）
            probe = list(self._model.embed(["维度探测"]))
            if probe:
                self._dim = len(probe[0])
            self.available = True
            print(f"[Embedder] 已加载向量模型 {self.model_name}（维度 {self._dim}）")
        except Exception as e:
            self.available = False
            self._model = None
            hint = ""
            err = str(e)
            if "Connection" in err or "HF" in err or "download" in err.lower():
                hint = "（已自动尝试 HF 镜像 hf-mirror.com；若仍失败可设置 JAC_HF_INSECURE=1 处理代理自签证书，或保持关键词检索）"
            print(f"[Embedder] 模型加载失败，向量检索降级为关键词：{e}{hint}")
        return self.available

    # ------------------------- 编码接口 -------------------------

    def embed_texts(self, texts: List[str], mode: str = "passage") -> Optional["list"]:
        """批量编码，返回 list[np.ndarray]（每条一个向量），失败返回 None。

        ``mode`` 仅对需要前缀的模型生效：
          - "query"   检索查询，加 ``query: `` 前缀；
          - "passage" 入库文本，加 ``passage: `` 前缀。
        """
        if not texts:
            return None
        if not self._ensure_loaded() or self._model is None:
            return None
        try:
            if self._needs_prefix:
                prefix = "query: " if mode == "query" else "passage: "
                texts = [prefix + t for t in texts]
            vecs = list(self._model.embed(texts))
            return vecs
        except Exception as e:
            print(f"[Embedder] 编码失败，本次跳过向量（{e}）")
            return None

    @property
    def dim(self) -> Optional[int]:
        """维度"""
        if self._dim is None:
            self._ensure_loaded()
        return self._dim
