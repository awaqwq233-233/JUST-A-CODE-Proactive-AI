# 与最终目标的差距 (The Gap)

如果要将 J.A.C. (Just A Code) 打造成真正的"全功能语音助手"（类似 Jarvis 或 Google Assistant 的本地版），目前仍存在显著差距。本节记录**截至当前代码的真实状态**，并标注「未解决 / 部分解决 / 已落地」。详细实现见 `AGENTS.md`。

## 1. 交互方式：从被动到主动（部分解决）

- **目标**：唤醒词 + VAD，能自动判断你什么时候说完话，无需物理按键；最终实现持续主动感知与闭环介入。
- **现状**：使用MiniCPM-o-4_5作为判断模型，由于显存不足暂未验证是否可用

## 2. 缺乏"手"——Function Calling（未解决）

- **目标**：工具调用能力，例如查实时天气、控制电脑音量、打开网页、管理日程、搜索文件等。
- **现状**：LLM 仍只输出 `[情绪] 文本`，没有执行系统命令 / 调用工具的能力。代码中**无 function calling / 工具执行层**。

## 3. 记忆力有限（未解决）

- **目标**：长期记忆（Vector DB / JSON），记住用户喜好、历史对话摘要，实现个性化陪伴。
- **现状**：记忆功能已添加，待验证（显存不足无法跑完整循环测试）

## 4. 响应速度 / 流式（未解决）

- **目标**：流式对话——一边思考一边生成语音，大幅降低感知延迟。
- **现状**：MiniCPM-o-4_5是全双工模型，理论上可以实现，但速度可能可以优化

## 5. 其他架构级缺口（未解决）

- 无 agent 执行框架；无 MCP / OpenClaw 集成；无云端/局域网卸载（虽有 `Qwen3.6-35B` 模型已下载但未接入代码，这个模型是无限制的模型，但目前显存不足）。
- 视觉理解仍只有 **YOLO 标签 + LLM 文本摘要**，无 OCR / 人脸识别 / 深度 / 场景图 / 视觉语言理解（旧的 LocateAnything-3B 方案已移除）。
- TTS 语音栈已从 Genie-TTS（GPT-SoVITS）全面切换为开源本地 Qwen3-TTS（已删除 genie_tts.py 与 genie_assets/、GenieData/），支持情绪控制与声音克隆。
- 当前项目树**没有自动化测试**。

> 注：本文件是"差距笔记"，不是精确实现状态。已落地的进展（主动判断引擎雏形、多模态图像问答、多后端大脑）以 `AGENTS.md` 为准。

---

## 修复记录（2026-08-04）：J.A.C.Prototype 运行日志问题排查

本次真机运行暴露 4 类问题，已全部定位并修复（改动文件：`src/brain/llm.py`、`main.py`、`src/audio/qwen_tts.py`、`src/audio/tts.py`、`src/memory/embedder.py`）。

### 问题 1：回复啰嗦、超长、被截断
- **根因**：`think` 默认 `max_tokens=120`、视觉问答 `200`，且 system prompt 的"简短"约束过软，模型吐出 Markdown 长文（如分点描述人物），既难听又易超长。
- **修复**：
  - `llm.py`：`think` 默认 `512`、`think_stream` `768`、`think_with_image` `512`（LM Studio 下限仍是 512）。
  - `main.py`：强化 system prompt / 视觉 prompt / 检测摘要 prompt 的**输出铁律**（仅简体中文、禁 Markdown/列表/编号、单句、≤30 字、视觉≤60 字）。
  - `main.py`：朗读前对 `response_text` 做 **80 字安全截断**（日志记录原始长度），避免 TTS 卡顿。

### 问题 2：全程听不到语音（TTS 静音）
- **根因**：本地 Qwen3-TTS 权重不完整（半截下载）时，`QwenTTSSpeaker.available` 仍被无条件置 `True`，程序死守 Qwen 路径；而旧 `_play` 用 `sounddevice` 直接播放，在部分机器选错输出设备导致**静音且零报错**。系统 TTS 兜底从未真正生效。
- **修复**：
  - `qwen_tts.py`：新增 `qwen_weights_ready()`，`__init__` 在创建时即校验本地权重完整性；不完整则 `available=False`，让 `main.py`/`runtime.py` **直接回退 `Speaker`（系统 TTS）**。
  - `qwen_tts.py`：`_play` 改为"写 WAV + 平台命令播放（macOS afplay / Win SoundPlayer / Linux aplay）"，并打印**发声日志**，可观测、可靠。
  - `qwen_tts.py`：`play_wav` 增加成功/失败日志。
  - `tts.py`：macOS 系统 TTS 优先选明确中文嗓色（Tingting / Mei-Jia / 普通话），并补充日志。

### 问题 3：Embedder 加载失败（向量检索降级）
- **根因**：`fastembed` 直连 HuggingFace 默认源，国内网络被墙，下载失败仅静默降级关键词检索，无镜像、无友好提示。
- **修复**：`embedder.py` 新增 `_apply_hf_mirror()`，加载前自动设置 `HF_ENDPOINT=https://hf-mirror.com`（未显式配置时），并配合 `JAC_HF_INSECURE=1` 关闭 SSL 校验；下载失败仍优雅降级关键词检索，并给出可操作提示。

### 问题 4：flash-attn 警告刷屏
- **根因**：`transformers` 启动提示 "flash-attn is not installed"，无害但刷屏。
- **修复**：`qwen_tts.py` 导入阶段用 `warnings.filterwarnings` 屏蔽该提示，并设置 `TRANSFORMERS_VERBOSITY=error`。

### 运行建议
- 若坚持用 Qwen3-TTS（仅 NVIDIA）：确保 `models/qwen_tts/Qwen3-TTS-12Hz-1.7B-Base` 权重完整；项目已不再内置 `download_models.py` 下载器，缺失时运行时尝试在线拉取、失败则回退系统 TTS 或改用 Voicebox（macOS 默认 TTS），仍可正常发声。
- 国内网络首次启用向量记忆检索：建议启动前 `export HF_ENDPOINT=https://hf-mirror.com`；代理自签证书环境加 `export JAC_HF_INSECURE=1`。

