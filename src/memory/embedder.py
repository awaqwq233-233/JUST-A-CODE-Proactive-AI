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

_DEFAULT_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"


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

    # ------------------------- lazy load -------------------------

    def _ensure_loaded(self) -> bool:
        """确保已加载"""
        if self._model is not None:
            return self.available
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
            if "Connection" in str(e) or "HF" in str(e) or "download" in str(e).lower():
                hint = "（国内网络请先设置 HF_ENDPOINT=https://hf-mirror.com 再启动）"
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
