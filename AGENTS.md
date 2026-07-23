# J.A.C. 项目说明（AGENTS.md）

## 项目概述

J.A.C. = "Just A Code"。这是一个**本地优先的多模态 AI 助手原型**，灵感来自 JARVIS：通过摄像头/麦克风感知用户的环境，基于当前场景做推理，用带情绪的 TTS 回应，未来目标是无需用户显式触发即可主动行动。

长期产品愿景是**智能眼镜助手**：

- 眼镜 / MR 设备采集真实世界的视频与音频。
- MacBook 级别的主机做低延迟的本地感知与推理。
- 云或局域网服务器承担更重的推理、长期记忆、路由到更大的模型，以及本地算力不足时的外部 API。
- 助手最终要支持主动感知、agent 式任务执行、外部 API 调用、语音/HUD 输出，以及闭环任务循环。

当前代码库是一个 **Python 桌面原型**，还不是最终的眼镜/云端架构。

## 当前实现

可运行入口是 `main.py`。它把以下模块串联起来：

- OpenCV 摄像头采集：`src/capture/camera.py`（自动探测摄像头 ID，默认 1280×720）。
- YOLOv8 物体检测：`src/analysis/detector.py`（仅 YOLOv8，`conf=0.5`）。
- 线程安全共享上下文：`src/utils/context.py`（在旧版基础上新增转录环形缓冲、最新帧缓存、介入标志）。
- VAD 麦克风录音：`src/audio/recorder.py`（PyAudio + WebRTC VAD，阈值/预热/最短时长已调优）。
- Whisper 语音识别：`src/audio/stt.py`（默认 `model_size="tiny"`，**非流式**）。
- 本地大脑推理：`src/brain/llm.py`（`LocalBrain`，多后端：lm_studio / ollama / llama_cpp / auto）。
- Qwen3-TTS 语音合成：`src/audio/qwen_tts.py`（开源本地 TTS，支持情绪/语气自然语言控制与 3 秒声音克隆，默认克隆保住 J.A.C. 音色），兜底为 `src/audio/tts.py`（pyttsx3 / macOS `say`）。
- **主动判断引擎（新增）**：`src/judgment/judge.py`（`JudgmentEngine`，连接 LM Studio 上的 MiniCPM-o，持续判断是否需要主动介入）。

### 运行流程（`main.py`）

1. 初始化摄像头、YOLO 检测器、扬声器（`QwenTTSSpeaker`，不可用则回退 `Speaker`）、Whisper、`AudioRecorder`、`LocalBrain`（**默认 `backend="lm_studio"`，模型 `Qwen3.5-9B-Q4_K_M.gguf`**）。
2. 启动三条线程：音频主循环（监听→识别→唤醒判断→响应）、**控制台输入线程（新增）**、判断引擎线程（daemon）。
3. 主循环每帧：取帧 → YOLO 检测 → 更新 `SharedContext`（视觉摘要 + 缓存最新帧）→ 绘制 FPS / 状态灯（Listening/Thinking/Speaking）→ `cv2.imshow`。
4. 唤醒词集合：`jac` / `j.a.c` / `杰克` / `接客` / `你好` / `hello jac` / `hi jac` / `你好 jac` / `hey jac`。
5. 唤醒后进入 `AWAKE` 状态；`SYSTEM_STATE` 在 **20 秒（AWAKE_TIMEOUT）** 无交互后自动回到 `SLEEP`；用户说「再见/休息」立即休眠。
6. 用户输入进入 `handle_user_text` → `process_response`：取视觉摘要 → 若判定为视觉相关问题（看到/看见/有什么/画面/是谁…）且后端支持图像，则把真实摄像头帧发给 `brain.think_with_image()`；否则用文本 `brain.think()`（带上视觉摘要）。
7. 模型回复格式为 `[情绪] 回复内容`，解析情绪标签后交给扬声器（`emotion_hint`）。
8. 若主动判断引擎已激活，主循环每帧检查介入请求，确认后新开 daemon 线程主动回应（绕过唤醒词）。

### 多模态图像问答（新增能力）

`LocalBrain.think_with_image(prompt, frame)` 把当前帧编码为 JPEG base64，按 OpenAI 多模态消息格式发送：`lm_studio` / `ollama` 原生支持；`llama_cpp` 通过 `_find_mmproj()` 自动挂载 `mmproj-*.gguf` 投影。图像请求失败时降级为基于 YOLO 检测摘要的文本回答（`build_text_only_vision_reply`）。

### 键盘与输入控制

- `q`：退出。
- `SPACE`（空格）：手动唤醒（「我在，请讲。」）。
- **控制台 stdin 文本输入（新增，旧文档未记录）**：任意时刻回车输入文字，以 `source="控制台"`、`bypass_wake=True` 直接进入思考，绕过唤醒词。

## 重要文件与目录

- `main.py`：多模态运行主入口。
- `src/capture/camera.py`：摄像头封装，Windows/macOS 感知。
- `src/analysis/detector.py`：YOLOv8 检测器封装。
- `src/audio/recorder.py`：PyAudio + WebRTC VAD 录音器。
- `src/audio/stt.py`：OpenAI Whisper 封装。
- `src/audio/tts.py`：跨平台系统 TTS 兜底封装。
- `src/audio/qwen_tts.py`：Qwen3-TTS 语音合成（开源本地 TTS，支持情绪/语气控制与声音克隆），带系统 TTS 兜底降级。
- `src/brain/llm.py`：`LocalBrain`，llama.cpp / LM Studio / Ollama / auto 多后端，含 `think_with_image`。
- `src/judgment/judge.py`：**新增**，主动判断引擎（MiniCPM-o via LM Studio）。
- `src/judgment/__init__.py`：**新增**。
- `src/utils/context.py`：线程安全的共享上下文（视觉摘要、状态标志、转录缓冲、帧缓存、介入标志）。
- `models/`：本地 GGUF 模型目录（见下）。
- `voices/`：Qwen3-TTS 声音克隆参考音（J.A.C. 音色）。
- `temp/`：运行时临时音频文件。
- `ffmpeg.exe`：Windows 本地 FFmpeg 二进制。
- `requirements.txt` / `requirements_fixed.txt`：依赖快照（`requirements.txt` 较新，`requirements_fixed.txt` 为旧稳定版）。
- `DEPLOY_GUIDE.txt`：Windows/macOS 离线迁移与部署指南。
- `Modelfile`：**新增**，Ollama 构建定义（jac-qwen3.5）。
- `codingLOG.md`：与最终目标的差距笔记。
- `codinglog_by_awaqwq233/`：项目背景、预期架构、进度与研究文档。
- `build.py`：`JAC_Prototype` 的 PyInstaller 打包辅助。
- `fix_install.py`：依赖修复辅助（PyAudio / llama-cpp-python / VAD on Windows）。
- `setup_ffmpeg.py`：从 imageio-ffmpeg 复制二进制为项目根 `ffmpeg.exe`。
- `verify_model.py`：校验 llama-cpp-python 与本地 GGUF 模型。
- `docs/memory/`：记忆子系统文档集合——`schema.md`（JSON 数据契约，v1.0.0 锁定）、`runbook.md`（运维/排障）、`privacy.md`（隐私细则）、`README.md`（用户指南）。记忆数据文件默认在用户目录 `~/.jac/memory/`（见 `runbook.md` §3.3），不进仓库。

通常不参与编辑的大体积/二进制产物：

- `.venv/` / `.cache/` / `__pycache__/`
- 模型二进制（`*.gguf`、`*.pt`、`*.bin`）
- `temp/` 下的运行时音频

## 模型与资产

当前本地推理模型（`models/` 下 4 个 GGUF）：

- `Qwen3.5-9B-Q4_K_M.gguf`：当前「大脑」模型（约 5.6GB），`main.py` 默认指定，默认通过 `lm_studio` 后端加载（127.0.0.1:12345）。
- `mmproj-Qwen3.5-9B-BF16.gguf`：Qwen3.5 的多模态投影（约 0.9GB），`llama_cpp` 后端做视觉问答时自动挂载。
- `MiniCPM-o-4_5-Q4_K_S.gguf`：主动判断引擎模型（约 4.8GB），需在 LM Studio 加载后由 `JudgmentEngine` 使用。
- `Qwen3.6-35B-A3B-...-IQ2_M.gguf`（约 11.6GB）：**已下载但代码/配置均未引用**，疑似备用大模型或未来云端/服务器卸载预留——勿误读为已启用。

> 注意：旧的 `models/qwen1_5-1_8b-chat-q4_k_m.gguf` 与 `models/README.txt` 已不存在，删去相关描述。

当前物体检测器：`yolov8n.pt`（`conf=0.5`）。旧架构设想的 NVIDIA LocateAnything-3B 视觉理解大模型**已移除**——`detector.py` 注释明确：视觉理解现由 JACbrain（Qwen3.5-9B）以文本方式处理，视觉只靠 YOLO 标签 + LLM 文本摘要。

当前 STT：Whisper，`model_size="tiny"`，非流式。

当前 TTS：默认 Qwen3-TTS（`src/audio/qwen_tts.py`，开源本地 TTS，支持情绪/语气自然语言控制与 3 秒声音克隆，克隆参考音在 `voices/`）；不可用时代码自动降级到 pyttsx3 / macOS `say`。

## 设置与运行

部署指南推荐 Python 3.10 / 3.11 以获得最佳兼容性。

基础安装：

```bash
pip install -r requirements.txt
```

依赖安装失败（Windows）：

```bash
python fix_install.py
```

FFmpeg 缺失：

```bash
python setup_ffmpeg.py
```

校验 GGUF 模型：

```bash
python verify_model.py
```

### 运行前置条件（重要变化）

- **默认 `backend="lm_studio"`**，因此运行前需启动 **LM Studio** 并在 `127.0.0.1:12345` 加载 `Qwen3.5-9B`（如需主动判断，另加载 `MiniCPM-o`）。否则所有思考请求会连接失败。
- 若想纯本地 GGUF 推理，需把 `main.py` 中 `LocalBrain(..., backend="lm_studio")` 改为 `"llama_cpp"` 或 `"auto"`（`auto` 会探测可用后端），并确保对应 GGUF 在 `models/`。
- Ollama 用法：用附带的 `Modelfile` 构建 `jac-qwen3.5`，再把 backend 改为 `"ollama"`。

运行原型：

```bash
python main.py
```

所需本地硬件/运行时条件：

- 可用的摄像头、可用的麦克风。
- 项目根或系统 PATH 中的 FFmpeg。
- 运行中的 LM Studio（默认）或本地 GGUF 模型（改 backend 后）。
- Qwen3-TTS 引擎（`pip install -U qwen-tts`）与模型权重（见 `download_models.py`，自动下载到 `models/qwen_tts/`）。

## 当前进度（来自日志）

项目于 2026-06-23 高考后重启。方向（来自 `codinglog_by_awaqwq233/当前进度.docx`）：

- 继续用 vibe coding 推进。
- 把纸质架构图数字化。
- 用更新的工具/模型重新审视架构：OpenClaw、小米 MiLoco 2.0、能在 48GB MacBook 级机器运行的新 Qwen 系列。
- 重做语音模型路径：TTS 后端已从 Genie-TTS（GPT-SoVITS/ONNX）全面切换为开源本地 Qwen3-TTS（情绪自然语言控制 + 声音克隆，参考音在 `voices/`），旧 Genie 代码与资产已删除。
- 研究小判断模型能否在流式输入时持续思考。
- 搭建 GitHub 提交/开源工具链。
- 在稳定服务器可用后探索服务器连接模块（含 `awaqwq233.cloud`、`awaqwq233.com`）。

**代码中已落地的进展（相对旧文档）：**

- 大脑从 Qwen1.5-1.8B 升级为 Qwen3.5-9B，并抽象出多后端 `LocalBrain`。
- 新增 `src/judgment` 主动判断引擎雏形（MiniCPM-o via LM Studio，每 4s 判断是否主动介入）——对应愿景里的「核心判断 / 持续感知」。
- 新增多模态图像问答 `think_with_image()`（视觉问题时发送真实摄像头帧）。
- 新增 `SLEEP`/`AWAKE` 状态机 + 20s 超时自动休眠。
- 新增控制台文本输入实时对话（绕过唤醒词）。
- 唤醒词扩展；TTS 后端从 Genie-TTS 全面切换为开源本地 Qwen3-TTS（情绪自然语言控制 + 声音克隆）。

`codingLOG.md` 列出的与最终目标的差距中，**以下仍为未实现项**：function calling / 工具执行层、持久记忆（JSON/向量库）、agent 执行框架、MCP / OpenClaw 集成、流式 STT/LLM/TTS。注意 `codingLOG.md` 部分内容早于 `main.py`，应作为架构差距笔记而非精确实现状态。

## 预期未来架构

规划文档描述了一个由「J.A.C. Brain」驱动的系统：

- 输入层：设备信号、实时音频、视觉帧。
- 感知/预处理：语音转写、CNN/视觉分析，把解析结果缓冲进记忆。
- 核心判断：一个「多模态小判断模型」或判断模型集群，持续决定 J.A.C. 是否应介入。
- 调节/安全模块：校验判断是否正确，拦截不应静默执行的操作，误报时回到判断循环。
- J.A.C. Brain：更大的推理模型（可能 Qwen 系或改进版小米 MiLoco 2.0），负责复杂分析与任务规划。
- Agent 执行：内部技能与外部 API，可能通过 OpenClaw/MCP 类集成。
- 外部模型 API：Gemini、ChatGPT、Grok、Claude、Qwen、DeepSeek 等。
- 输出层：App/HUD 结果展示 + 情绪化 TTS。
- 闭环：输出反馈到下一轮判断，形成持续主动服务。

硬件预期（来自文档）：

- 主机：未来的 MacBook Pro 14" M5 Pro 级，48GB+ 统一内存，1TB SSD。
- 可能外设：小米 AI 眼镜、Apple Vision Pro，或便携相机/MR 设备。
- 便携供电：背包内高功率充电宝。
- 服务器：LAN/公网服务器承担更重模型，概念目标约双 22GB RTX 2080 Ti + 128GB RAM。

## 工程指导（未来工作）

- 坚持本地优先设计。尽量把唤醒词检测、VAD、基础感知、紧急交互留在本地。
- 优先简单规则，其次小模型，最后大模型——尤其用于介入判断与延迟敏感路径。
- 避免让大模型决定每个底层路由选择；用任务路由表与显式策略，除非确实需要模型判断。
- 保持摄像头/音频采集与模型推理通过清晰的 context/state 对象松耦合。`SharedContext` 是该模式的种子。
- 注意 `main.py` 的线程状态：`context.is_speaking`、`context.is_listening`、`context.is_thinking`、`conversation_running` 用于避免反馈循环与重叠交互。
- **新增后端（云端/外部 API）应在 `LocalBrain` 内扩展**，而非绕过它直接发请求，以保持统一的多模态接口与 mock 兜底。
- 把 `temp/` 音频当作可丢弃的运行时产物。
- 不要提交大体积模型/音频/打包产物，除非项目明确要跟踪二进制资产。
- 谨慎对待隐私与安全。愿景明确要求「主动常开感知」，未来实现必须包含可见的同意、本地过滤、日志控制，以及在录音/识别人物/向云 API 发送数据前的清晰边界。
- 任何新的 agent/工具执行功能，对高风险操作必须显式白名单与确认。当前助手能说、能看；执行系统动作是重大信任边界。
- 延迟优化优先做流式与流水线：流式 ASR、增量推理、流式/提前 TTS。
- 记忆从结构化 JSON 摘要起步，再考虑向量数据库。
- 更换 TTS 时保持 `speak(text, emotion_hint)` 统一接口与系统 TTS 兜底不变；当前已实现为 Qwen3-TTS + 参考音克隆。

## 已知限制

- **运行强依赖 LM Studio**：`main.py` 默认 `backend="lm_studio"`，必须本地 12345 端口加载 `Qwen3.5-9B`；否则思考全部失败。纯本地 GGUF 需改 backend。
- **双模型显存压力**：开启主动判断需 LM Studio 同时加载 `Qwen3.5-9B` + `MiniCPM-o`，资源占用大。默认 `JUDGMENT_ENGINE_ENABLED=False`，未检测到时自动进入被动模式（不报错也不主动）。
- VAD 录音仍可能阻塞在「等待说话」，影响关闭响应（旧限制仍在）。
- STT/LLM/TTS **均非流式**，端到端延迟仍高。
- 无 function calling、无持久记忆、无 agent/MCP/OpenClaw 集成（目标未实现）。
- `Qwen3.6-35B` 大模型已下载但代码未接入，勿误以为已启用。
- `requirements.txt` 已装 `fastapi`/`uvicorn`/`websockets` 等 web 栈，但 `src/` 下无对应 server 代码——属依赖传递或预留骨架，勿误读为「已有 API 服务」。
- 当前项目树**没有自动化测试**。

## 构建说明

`build.py` 使用 PyInstaller：

```bash
python build.py
```

它创建一个名为 `JAC_Prototype` 的 onedir 控制台构建，并收集 `ultralytics` 资产。模型文件、FFmpeg、Qwen3-TTS 模型（`models/qwen_tts/`）、Whisper 缓存/模型文件、平台音频权限等需要特别处理。
