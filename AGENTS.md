# J.A.C. 项目说明（AGENTS.md）

## 项目概述

J.A.C. = "Just A Code"。这是一个**本地优先的多模态 AI 管家原型**，灵感来自 JARVIS：通过摄像头/麦克风感知用户的环境，基于当前场景做推理，用语音 TTS 回应，目标是无需用户显式触发即可主动行动。

长期产品愿景是**打造一个强人工智能管家（Proactive AI Butler）**：

- 它能**主动感知**环境、提前**规划与预警**、在危险发生前发出**警示**、并能**应急接管**。
- 智能眼镜 / MR 等可穿戴终端只是**可选的随身交互外设**，而非项目的主目标或核心愿景——J.A.C. 的本质是 **AI 架构、系统与整套主动服务框架**，可穿戴终端只是承载它的一种形态。
- 主机（当前开发期）：MacBook Pro 级别的本地机器做低延迟的感知与推理。
- 云或局域网服务器承担更重的推理、长期记忆、路由到更大的模型，以及本地算力不足时的外部 API。
- 助手最终要支持主动感知、agent 式任务执行、外部 API 调用、语音/HUD 输出，以及闭环任务循环。

当前代码库是一个 **macOS 优先的 Python 桌面原型**（同时保留 Windows/Linux 的跨平台兼容代码，但不保证在 Windows 开发机上跑通），还不是最终的眼镜/云端架构。

## 文档同步硬性规定（Agent 必读）

本仓库有四类**必须随代码改动同步**的文档，任一代码/配置/依赖变更后都必须检查并更新对应条目：

1. `README.md` — GitHub 首页文档（双语：英文在前、中文在后）。每次改动后确保其描述与项目真实状态一致。
2. `AGENTS.md` — 本文件，开发者契约。架构/运行方式/依赖/文件路径变化必须同步。
3. `CHANGELOG.md` — 变更日志。每次变更追加一条用户可读的改动说明。
4. `codingLOG.md` — 与最终目标的差距笔记（根目录文件，区别于 `codinglog_by_awaqwq233/` 文件夹，后者**禁止**自动改动）。

**查看改动时的强制动作**：每当 Agent 需要了解「最近改了什么 / 当前实现状态」，必须优先读取 `CHANGELOG.md` 与 `codingLOG.md` 这两个文件，而不是依赖记忆或过时摘要。

配套检查项（每次提交/对话后）：

- 检查 `.gitignore` 是否需要新增忽略（如新增大体积/二进制产物）。
- 检查 `requirements.txt` 是否新增依赖；若有，同步更新 `new_computer_download/` 下的一键安装脚本与 `new_computer_download/READMEfirst.md`。
- `codinglog_by_awaqwq233/` 文件夹**只由 bo s s 手动维护**，Agent 不得自动编辑或同步其内容。
- **开发平台**：当前以 macOS 为主开发机，保持跨平台兼容代码；Windows 开发机已不再使用（旧 `build.py` / `fix_install.py` 等 Windows 专用脚本已删除）。**macOS 27 适配良好**——此前 GUI 渲染崩溃根因为渲染代码 bug（已在 gui.py 修复）、TTS 异常为本机代理导致 Voicebox 连不上 HuggingFace（已通过改用本地 Voicebox 解决），后续文档不再归咎系统版本。

## 当前实现

可运行入口是 `main.py`。它把以下模块串联起来：

- OpenCV 摄像头采集：`src/capture/camera.py`（自动探测摄像头 ID，默认 1280×720）。
- YOLOv8 物体检测：`src/analysis/detector.py`（仅 YOLOv8，`conf=0.5`；权重首次运行由 ultralytics 自动下载，不进仓库）。
- 线程安全共享上下文：`src/utils/context.py`（转录环形缓冲、最新帧缓存、介入标志）。
- VAD 麦克风录音：`src/audio/recorder.py`（PyAudio + WebRTC VAD，阈值/预热/最短时长已调优）。
- Whisper 语音识别：`src/audio/stt.py`（默认 `model_size="tiny"`，**非流式**；强制 `language="zh"` 简体中文，并内置繁→简兜底归一化，根治自动检测漂移导致的繁体/乱码）。
- 本地大脑推理：`src/brain/llm.py`（`LocalBrain`，多后端：lm_studio / ollama / llama_cpp / auto）。
- 语音合成：统一走 `build_speaker` 工厂——**Voicebox（开源克隆引擎，macOS 主力）→ Qwen3-TTS（仅 NVIDIA）→ 系统 TTS 兜底**。克隆参考音固定为 `voices/silverwalf_voice.wav`（唯一音色）。
- **主动判断引擎**：`src/judgment/judge.py`（`JudgmentEngine`，连接 LM Studio 上的 MiniCPM-o，持续判断是否需要主动介入，**默认开启** `JUDGMENT_ENGINE_ENABLED=True`；若 LM Studio 未加载 MiniCPM-o 则自动进入被动模式，不报错也不主动）。

### 运行流程（`main.py`）

1. 初始化摄像头、YOLO 检测器、扬声器（统一走 `build_speaker` 工厂：Voicebox → Qwen3-TTS(仅 NVIDIA) → 系统 TTS 兜底；macOS 上 Qwen 禁用、由 Voicebox 接管）、Whisper、`AudioRecorder`、`LocalBrain`（**默认 `backend="lm_studio"`，大脑模型为 LM Studio 中的标识符 `qwen/qwen3.6-35b-a3b`（原生支持视觉、禁用思考模式）**）。
2. 启动三条线程：音频主循环（监听→识别→唤醒判断→响应）、**控制台输入线程**、判断引擎线程（daemon）。
3. 主循环每帧：取帧 → YOLO 检测 → 更新 `SharedContext`（视觉摘要 + 缓存最新帧）→ 绘制 FPS / 状态灯（Listening/Thinking/Speaking）→ `cv2.imshow`。
4. 唤醒词集合：`jac` / `j.a.c` / `杰克` / `接客` / `你好` / `hello jac` / `hi jac` / `你好 jac` / `hey jac`。
5. 唤醒后进入 `AWAKE` 状态；`SYSTEM_STATE` 在 **20 秒（AWAKE_TIMEOUT）** 无交互后自动回到 `SLEEP`；用户说「再见/休息」立即休眠。
6. 用户输入进入 `handle_user_text` → `process_response`：取视觉摘要 → 若判定为视觉相关问题（看到/看见/有什么/画面/是谁…）且后端支持图像，则把真实摄像头帧发给 `brain.think_with_image()`；否则用文本分支处理（见下「Function Calling（装手）」）。
7. 模型输出**纯文本回复**（不再带 `[情绪]` 标签），经 `_strip_boilerplate` 清洗与残留括号清除后，直接交给扬声器**中性朗读**（不再传 `emotion_hint`）。
8. 若主动判断引擎已激活，主循环每帧检查介入请求，确认后新开 daemon 线程主动回应（绕过唤醒词）。

### Function Calling（装手）

非视觉文本分支在 `process_response` 中接入了 agent 式工具调用（2026-08-11 落地），让 J.A.C. 真正"有手"：

- **开关与前提**：默认开启（`TOOLS_ENABLED` 环境变量可关）；仅当后端支持结构化 function calling（`brain.supports_tools()`，当前 `lm_studio` / `ollama`）且 `src/tools` 有工具时启用，否则降级为普通流式对话。**GUI 右侧可折叠选项面板提供「工具功能（Function Calling）」勾选框**（启动前配置，与主动模型/TTS 开关一致），由 `JACRuntime.start` 桥接到 `main.TOOLS_ENABLED` 生效。
- **大脑侧**：`src/brain/llm.py` 新增 `think_with_tools(messages, tools)`（发送 `tools` + `tool_choice=auto`，解析 `tool_calls`）与 `run_agentic(prompt, tools, tool_executor)`（工具调用循环生成器，流式吐出最终回答，保留打字机效果）。
- **工具层**：`src/tools/` 提供四个白名单工具——`open_url` / `open_app`（打开网页/应用）、`search_files`（只读本地文件搜索，限定用户目录）、`get_system_info`（时间/电池/CPU/内存）、`run_command`（**受限 shell**：仅白名单命令，拦截 `rm`/`sudo` 等危险操作）。所有工具经 `get_tool_schemas()` 生成 OpenAI 风格 schema 发给模型，`execute_tool(name, arguments)` 执行并回喂结果。
- **循环**：模型要调工具 → `execute_tool` 执行 → 结果作为 `tool` 消息回喂模型 → 重复直到模型给出最终自然语言回答 → TTS 朗读。工具执行任何异常都被转成错误文本回喂，不让整轮对话崩溃。
- **安全边界**：工具只做打开应用/网页、只读搜索、状态查询、受限命令；绝不做删除/提权/任意写；`search_files` 仅扫用户目录，`run_command` 白名单 + `shell=False` 双重防注入。

### 多模态图像问答

`LocalBrain.think_with_image(prompt, frame)` 把当前帧编码为 JPEG base64，按 OpenAI 多模态消息格式发送：`lm_studio` / `ollama` 原生支持；`llama_cpp` 通过 `_find_mmproj()` 自动挂载 `mmproj-*.gguf` 投影。图像请求失败时降级为基于 YOLO 检测摘要的文本回答（`build_text_only_vision_reply`）。

### 键盘与输入控制

- `q`：退出。
- `SPACE`（空格）：手动唤醒（「我在，请讲。」）。
- **控制台 stdin 文本输入**：任意时刻回车输入文字，以 `source="控制台"`、`bypass_wake=True` 直接进入思考，绕过唤醒词。

## 重要文件与目录

- `main.py`：多模态运行主入口。
- `src/capture/camera.py`：摄像头封装，Windows/macOS 感知。
- `src/analysis/detector.py`：YOLOv8 检测器封装。
- `src/audio/recorder.py`：PyAudio + WebRTC VAD 录音器。
- `src/audio/stt.py`：OpenAI Whisper 封装；`SpeechRecognizer` 强制 `language="zh"`（环境变量 `STT_LANGUAGE` 可覆盖），`_to_simplified()` 兜底把繁体残字统一为简体（优先 `opencc`，否则内置常用字映射）。
- `src/audio/tts.py`：跨平台系统 TTS 兜底封装。
- `src/audio/playback.py`：共享 WAV 播放工具（`afplay` / PowerShell / `aplay`），Voicebox 与系统 TTS 共用。
- `src/audio/voicebox_tts.py`：Voicebox 克隆 TTS（开源，REST API `http://127.0.0.1:17493`，macOS 友好主力 TTS），自动克隆 JAC 声纹 + 8 种情绪映射 + 系统 TTS 兜底。
- `src/audio/speaker_factory.py`：统一扬声器选择工厂 `build_speaker(config)`（Voicebox → Qwen3-TTS → 系统 TTS）。
- `src/audio/qwen_tts.py`：Qwen3-TTS 语音合成（开源本地 TTS，支持情绪/语气控制与声音克隆，仅 NVIDIA 平台启用），带系统 TTS 兜底降级。
- `src/brain/llm.py`：`LocalBrain`，llama.cpp / LM Studio / Ollama / auto 多后端，含 `think_with_image` 与 Function Calling 的 `think_with_tools` / `run_agentic`。
- `src/tools/`：**Function Calling 工具层（装手）**——`registry.py`（工具注册表 + OpenAI schema）、`executor.py`（安全分发执行）、`open_actions.py` / `search_files.py` / `system_info.py` / `shell.py`（四个白名单工具）。
- `src/judgment/judge.py`：主动判断引擎（MiniCPM-o via LM Studio）。
- `src/utils/context.py`：线程安全的共享上下文（视觉摘要、状态标志、转录缓冲、帧缓存、介入标志）。
- `voices/`：TTS 声音克隆参考音。`silverwalf_voice.wav` 为唯一克隆音色参考（体积小、有意保留进版本库，见 `.gitignore` 注释）。
- `temp/`：运行时临时音频文件。
- `requirements.txt` / `requirements_fixed.txt`：依赖快照（`requirements.txt` 较新，`requirements_fixed.txt` 为旧稳定版）。
- `Modelfile`：Ollama 构建定义（jac-qwen3.5）。
- `codingLOG.md`：与最终目标的差距笔记（Agent 查看改动时必读）。
- `codinglog_by_awaqwq233/`：项目背景、预期架构、进度与研究文档——**只由 bo s s 手动维护，Agent 不得自动编辑**。
- `setup_ffmpeg.py`：从 imageio-ffmpeg 复制二进制为项目根 `ffmpeg`（macOS/Linux）或 `ffmpeg.exe`（Windows）。
- `verify_model.py`：校验 `llama_cpp` 兜底后端所需的本地 GGUF 模型（仅在使用 `llama_cpp` / `auto` backend 且本地有 GGUF 时需要）。
- `docs/memory/`：记忆子系统文档集合——`schema.md`（JSON 数据契约，v1.0.0 锁定）、`runbook.md`（运维/排障）、`privacy.md`（隐私细则）、`README.md`（用户指南）。记忆数据文件默认在用户目录 `~/.jac/memory/`（见 `runbook.md` §3.3），不进仓库。
- `new_computer_download/`：到新机器的一键环境搭建脚本与详细安装指南（`READMEfirst.md` 为双语安装首页）。

通常不参与编辑的大体积/二进制产物：

- `.venv/` / `.cache/` / `__pycache__/`
- 模型二进制（`*.gguf`、`*.pt`、`*.bin`）——项目不再内置，由外部 AI 软件管理。
- `temp/` 下的运行时音频

## 模型与资产

所有**推理模型均不存放在项目内**，由外部 AI 软件管理：

- **大脑模型** `qwen/qwen3.6-35b-a3b`：在 **LM Studio** 中加载（默认 `backend="lm_studio"`，`127.0.0.1:12345`），原生多模态、`enable_thinking=False` 禁用思考。代码按模型标识符精确匹配，不依赖任何本地 GGUF 文件。
- **主动判断引擎模型** MiniCPM-o：同样在 LM Studio 中另行加载，由 `src/judgment/judge.py` 使用；未加载时自动进入被动模式（不报错也不主动）。
- **TTS 声纹**：由 **Voicebox** App 托管，克隆 `voices/silverwalf_voice.wav` 得到名为 **JAC** 的声纹。项目内不再存放 TTS 权重。
- **物体检测** `yolov8n.pt`：首次运行由 `ultralytics` 自动下载到缓存，不进仓库。

> 旧版 `models/` 目录（GGUF / Qwen3-TTS 权重）已移除：项目不再内置任何大模型权重，所有模型走 LM Studio / Voicebox 等外部软件。`build.py` / `fix_install.py` / `download_models.py` / `DEPLOY_GUIDE.txt` 等旧 Windows/模型下载脚本已删除。

当前 STT：Whisper，`model_size="tiny"`，非流式；强制简体中文输出并兜底繁→简归一化（避免识别成繁体/乱码）。

当前 TTS：默认走 `build_speaker` 工厂，选择链 **Voicebox（开源克隆引擎，REST API）→ Qwen3-TTS（仅 NVIDIA）→ 系统 TTS 兜底**。

- **Voicebox（macOS 主力）**：`src/audio/voicebox_tts.py`，调开源 Voicebox App 的 `http://127.0.0.1:17493` REST API；自动建/复用名为 **JAC** 的克隆声纹（用 `voices/silverwalf_voice.wav`），支持中文 + 声音克隆；8 种情绪映射成 Chatterbox Turbo 副语言标签（`[laugh]/[sigh]/[gasp]/[excited]/[whisper]`）+ instruct；服务未启动自动回退系统 `say -v Tingting`。
- **Qwen3-TTS（仅 NVIDIA）**：`src/audio/qwen_tts.py`，开源本地 TTS，支持情绪/语气自然语言控制与 3 秒声音克隆。macOS 无 NVIDIA 卡，已默认禁用（见 CHANGELOG 2026-08-05）；可用 `QWEN_TTS_FORCE=1` 强开。默认克隆模式（`clone`）使用 `voices/silverwalf_voice.wav`，参考文本见 `qwen_tts.py` 的 `DEFAULT_REF_TEXT`；可用环境变量 `QWEN_TTS_REF` / `QWEN_TTS_REF_TEXT` 临时覆盖。
- 配置项（`src/utils/config.py`，均可用环境变量覆盖）：`use_voicebox_tts` / `voicebox_url` / `voicebox_engine`(默认留空，由 JAC 声纹绑定的模型决定；设 `VOICEBOX_ENGINE` 可覆盖) / `voicebox_profile_name`(JAC) / `voicebox_ref_wav` / `voicebox_ref_text` / `voicebox_language`(zh) / `voicebox_fallback_voice`(Tingting)。

## 设置与运行

完整安装与配置见 **`new_computer_download/READMEfirst.md`**（双语：英文官方方法 + 中文含国内镜像方法）。推荐 Python 3.10 / 3.11。

快速开始：

```bash
# 1. 创建并激活虚拟环境（推荐）
python3 -m venv .venv && source .venv/bin/activate        # macOS/Linux
# Windows:  .venv\Scripts\activate

# 2. 安装依赖
pip install -r requirements.txt
# 国内网络可加清华镜像： -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn

# 3. 确保 FFmpeg 可用（项目根或系统 PATH）
python setup_ffmpeg.py     # 缺失时从 imageio-ffmpeg 复制

# 4. 启动外部 AI 软件并加载模型
#    - LM Studio：加载大脑模型 qwen/qwen3.6-35b-a3b（标识符必须精确匹配），开启本地服务器 127.0.0.1:12345
#    - Voicebox App：克隆 voices/silverwalf_voice.wav 为 JAC 声纹（macOS 主力 TTS）

# 5. 运行原型
python main.py
```

### 运行前置条件

- **默认 `backend="lm_studio"`**，因此运行前需启动 **LM Studio** 并在 `127.0.0.1:12345` 加载 `qwen/qwen3.6-35b-a3b`（如需主动判断，另加载 `MiniCPM-o`）。否则所有思考请求会连接失败。
- 若想纯本地 GGUF 推理，需把 `main.py` 中 `LocalBrain(..., backend="lm_studio")` 改为 `"llama_cpp"` 或 `"auto"`（`auto` 会探测可用后端），并确保对应 GGUF 由外部放置（项目不再内置）。
- Ollama 用法：用附带的 `Modelfile` 构建 `jac-qwen3.5`，再把 backend 改为 `"ollama"`。

所需本地硬件/运行时条件：

- 可用的摄像头、可用的麦克风。
- 项目根或系统 PATH 中的 FFmpeg。
- 运行中的 **LM Studio**（默认）或本地 GGUF 模型（改 backend 后）。
- **Voicebox App**（macOS 主力 TTS）：从官方渠道安装，克隆 J.A.C. 声纹即可，项目不再内置 TTS 权重、也无需 `download_models.py`。

## 当前进度（来自日志）

项目于 2026-06-23 高考后重启。方向（来自 `codinglog_by_awaqwq233/当前进度.docx`）：

- 继续用 vibe coding 推进。
- 把纸质架构图数字化。
- 用更新的工具/模型重新审视架构：OpenClaw、小米 MiLoco 2.0、能在 48GB MacBook 级机器运行的新 Qwen 系列。
- 重做语音模型路径：TTS 后端已从 Genie-TTS（GPT-SoVITS/ONNX）全面切换为 **Voicebox（开源本地克隆引擎，macOS 主力；Qwen3-TTS 仅 NVIDIA 兜底）**，克隆参考音固定为 `voices/silverwalf_voice.wav`，旧 Genie 代码与资产已删除。
- 研究小判断模型能否在流式输入时持续思考。
- 搭建 GitHub 提交/开源工具链。
- 在稳定服务器可用后探索服务器连接模块。

**代码中已落地的进展（相对旧文档）：**

- 大脑从 Qwen1.5-1.8B 升级为 `qwen/qwen3.6-35b-a3b`（经 LM Studio 加载），并抽象出多后端 `LocalBrain`。
- 新增 `src/judgment` 主动判断引擎雏形（MiniCPM-o via LM Studio，每 4s 判断是否主动介入）——对应愿景里的「核心判断 / 持续感知」。
- 新增多模态图像问答 `think_with_image()`（视觉问题时发送真实摄像头帧）。
- 新增 `SLEEP`/`AWAKE` 状态机 + 20s 超时自动休眠。
- 新增控制台文本输入实时对话（绕过唤醒词）。
- TTS 后端从 Genie-TTS 全面切换为 **Voicebox（克隆 J.A.C. 声纹）+ Qwen3-TTS 仅 NVIDIA 兜底**。

`codingLOG.md` 列出的与最终目标的差距中，**以下仍为未实现项**：function calling / 工具执行层、agent 执行框架、MCP / OpenClaw 集成、流式 STT/LLM/TTS。注意 `codingLOG.md` 部分内容早于 `main.py`，应作为架构差距笔记而非精确实现状态。

> **已落地（曾列于未实现项，现已实现并集成）**：持久记忆（JSON 长期记忆 + 轻量本地向量检索，见 `src/memory/` 与 `docs/memory/`）。`codingLOG.md` 中「记忆功能待验证」指端到端未在真机跑过，并非代码空缺。

## 预期未来架构

规划文档描述了一个由「J.A.C. Brain」驱动的系统：

- 输入层：设备信号、实时音频、视觉帧。
- 感知/预处理：语音转写、CNN/视觉分析，把解析结果缓冲进记忆。
- 核心判断：一个「多模态小判断模型」或判断模型集群，持续决定 J.A.C. 是否应介入。
- 调节/安全模块：校验判断是否正确，拦截不应静默执行的操作，误报时回到判断循环。
- J.A.C. Brain：更大的推理模型（可能 Qwen 系或改进版小米 MiLoco 2.0），负责复杂分析与任务规划。
- Agent 执行：内部技能与外部 API，可能通过 OpenClaw/MCP 类集成。
- 外部模型 API：Gemini、ChatGPT、Grok、Claude、Qwen、DeepSeek 等。
- 输出层：App/HUD 结果展示 + 语音 TTS（纯文本中性朗读）。
- 闭环：输出反馈到下一轮判断，形成持续主动服务。

硬件预期（来自文档，可穿戴终端仅为外设）：

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
- 不要提交大体积模型/音频/打包产物，除非项目明确要跟踪二进制资产。`voices/` 下的参考音（J.A.C. 音色，体积小）有意保留、不忽略，迁移或克隆后按需 `git add voices/` 提交；模型权重一律走外部 AI 软件、不进仓库。
- 谨慎对待隐私与安全。愿景明确要求「主动常开感知」，未来实现必须包含可见的同意、本地过滤、日志控制，以及在录音/识别人物/向云 API 发送数据前的清晰边界。
- 任何新的 agent/工具执行功能，对高风险操作必须显式白名单与确认。当前助手能说、能看；执行系统动作是重大信任边界。
- 延迟优化优先做流式与流水线：流式 ASR、增量推理、流式/提前 TTS。
- 记忆从结构化 JSON 摘要起步，再考虑向量数据库。
- 更换 TTS 时保持 `speak(text, emotion_hint)` 统一接口与系统 TTS 兜底不变；当前已实现为 **Voicebox（克隆 `voices/silverwalf_voice.wav` 声纹）+ 系统 TTS 兜底**。

## 已知限制

- **运行强依赖 LM Studio**：`main.py` 默认 `backend="lm_studio"`，必须本地 12345 端口加载 `qwen/qwen3.6-35b-a3b`；否则思考全部失败。纯本地 GGUF 需改 backend。
- **双模型资源**：开启主动判断需 LM Studio 同时加载 `qwen/qwen3.6-35b-a3b` + `MiniCPM-o`；当前 M5 Pro 48G 统一内存已验证可同时承载（2026-08-11）。默认 `JUDGMENT_ENGINE_ENABLED=True`，未检测到 MiniCPM-o 时自动进入被动模式（不报错也不主动）。
- VAD 录音仍可能阻塞在「等待说话」，影响关闭响应（旧限制仍在）。
- STT/LLM/TTS **均非流式**，端到端延迟仍高。
- 无 function calling、无 agent/MCP/OpenClaw 集成（目标未实现）；持久记忆（JSON 长期记忆 + 轻量向量检索）已实现，见 `src/memory/` 与 `docs/memory/`。
- `Qwen3.6-35B` 大模型（`qwen/qwen3.6-35b-a3b`）**现已接入为默认大脑**（经 LM Studio 按标识符加载）；本地不再内置 GGUF 备份，运行完全依赖 LM Studio 加载的模型。
- `requirements.txt` 已装 `fastapi`/`uvicorn`/`websockets` 等 web 栈，但 `src/` 下无对应 server 代码——属依赖传递或预留骨架，勿误读为「已有 API 服务」。
- 当前项目树**没有自动化测试**。
