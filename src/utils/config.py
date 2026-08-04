"""J.A.C. 运行时配置（集中管理，替代散落在 main.py 顶部的常量）。

GUI 的选项面板直接绑定一个 Config 实例；按「启动」时把它传给 JACRuntime。
所有字段都支持从环境变量读取（保持与旧 main.py 常量兼容）。
"""
import os
from dataclasses import dataclass, field
from typing import List


@dataclass
class Config:
    # --- 前导判断引擎（主动感知）---
    judgment_engine_enabled: bool = True
    judgment_interval: float = 4.0          # 每隔几秒判断一次（秒）
    judgment_timeout: float = 15.0          # 单次判断请求最长等待（秒）
    judgment_cooldown: float = 20.0         # 介入后冷却时长（秒）：避免同一场景反复触发
    judgment_model_name: str = "minicpm-v-4_5"

    # --- TTS 选择（新增开关，覆盖原"默认优先 Qwen、不可用时回退"的隐式逻辑）---
    use_qwen_tts: bool = True

    # --- 大脑推理后端 ---
    brain_backend: str = "lm_studio"         # lm_studio | llama_cpp | ollama | auto

    # --- 唤醒 ---
    wake_words: List[str] = field(default_factory=lambda: [
        "jac", "j.a.c", "杰克", "接客", "你好",
        "hello jac", "hi jac", "你好 jac", "hey jac",
    ])
    awake_timeout: int = 20                  # 唤醒后维持活跃秒数

    # --- 记忆子系统 ---
    memory_enabled: bool = True
    memory_capture_person_id: bool = False

    # --- 摄像头（采集分辨率固定，绝不随 GUI 缩放变化）---
    camera_width: int = 1280
    camera_height: int = 720

    @classmethod
    def load(cls) -> "Config":
        """加载"""
        def truthy(key: str, default: bool = True) -> bool:
            """真值判断"""
            v = os.environ.get(key)
            if v is None:
                return default
            return v.strip().lower() not in ("0", "false", "no", "off")

        return cls(
            judgment_engine_enabled=truthy("JUDGMENT_ENGINE_ENABLED", True),
            judgment_interval=float(os.environ.get("JUDGMENT_INTERVAL", "4.0")),
            judgment_timeout=float(os.environ.get("JUDGMENT_TIMEOUT", "15.0")),
            judgment_cooldown=float(os.environ.get("JUDGMENT_COOLDOWN", "20.0")),
            judgment_model_name=os.environ.get("JUDGMENT_MODEL_NAME", "minicpm-v-4_5"),
            use_qwen_tts=truthy("USE_QWEN_TTS", True),
            brain_backend=os.environ.get("JAC_BRAIN_BACKEND", "lm_studio"),
            awake_timeout=int(os.environ.get("AWAKE_TIMEOUT", "20")),
            memory_enabled=truthy("MEMORY_ENABLED", True),
            memory_capture_person_id=truthy("MEMORY_CAPTURE_PERSON_ID", False),
        )
