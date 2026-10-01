# J.A.C. — Just A Code

> A local-first, multimodal **Proactive AI Butler** prototype. Perceives your environment through camera + microphone, reasons about the scene, and replies with emotion-aware TTS — aiming to act *before* you ask, like a JARVIS-style assistant.

[中文说明](#中文说明)

---

## English

### What it is

J.A.C. is a **local-first multimodal AI butler** inspired by JARVIS. The long-term vision is a **strong-AI butler** that proactively perceives the world, plans ahead, warns of hazards *before* they happen, and can take over in emergencies. Wearable terminals (smart glasses / MR headsets) are **optional peripherals**, not the core goal — J.A.C. is fundamentally an **AI architecture, system, and end-to-end proactive-service framework**; the wearable is just one way to carry it.

The current codebase is a **macOS-first Python desktop prototype** (Windows/Linux compatibility code is kept, but the dev machine is macOS). It is not yet the final glasses/cloud architecture.

### Features

- **Multimodal perception** — OpenCV camera + YOLOv8 detection, PyAudio + WebRTC VAD microphone capture, OpenAI Whisper STT.
- **Local brain** — `qwen/qwen3.6-35b-a3b` loaded in **LM Studio** (native multimodal, thinking disabled), via a multi-backend `LocalBrain` (lm_studio / ollama / llama_cpp / auto).
- **Cloned-voice TTS (neutral, no emotion tags)** — Voicebox (open-source cloning engine, macOS primary) cloning the J.A.C. voice from `voices/silverwalf_voice.wav`, with Qwen3-TTS (NVIDIA-only) and system-TTS fallbacks. Since 2026-08-09 the brain emits plain text only, so J.A.C. always reads neutrally (the emotion interfaces remain available but unused).
- **Proactive judgment engine** — `src/judgment/judge.py` (`minicpm-v-4_5` via LM Studio standard chat API) polls every 4s with a screenshot + transcript to decide whether to intervene; **on by default** (`JUDGMENT_ENGINE_ENABLED=True`); auto passive mode if the model is not loaded. Note: the omni full-duplex path uses a **different** model instance — `MiniCPM-o-4_5` (GGUF Q8_0) in llama.cpp-omni `:9060`.
- **Multimodal Q&A** — sends the real camera frame to the brain for vision questions.
- **Wake-word + console input** — wake words (`jac` / `杰克` / `你好` …) or just type in the console to talk.
- **Persistent memory** — JSON long-term memory + lightweight local vector retrieval (`src/memory/`).
- **Full-duplex OMNI takeover** — local MiniCPM-o-4_5 exchanges continuous audio/video through llama.cpp-omni; its Listen sampling setting is sent in the protocol-required `payload.config` field so it is actually applied by the server. The camera uplink has both a frame-interval setting and a master on/off switch, so you can run audio-only full duplex to isolate how vision tokens consume the context window.

### Architecture (what's inside)

| Module | Path |
| --- | --- |
| Camera capture | `src/capture/camera.py` |
| YOLOv8 detector | `src/analysis/detector.py` |
| Shared context (thread-safe) | `src/utils/context.py` |
| VAD recorder | `src/audio/recorder.py` |
| Whisper STT | `src/audio/stt.py` |
| Local brain (multi-backend) | `src/brain/llm.py` |
| TTS (Voicebox → Qwen3-TTS → system) | `src/audio/voicebox_tts.py`, `src/audio/qwen_tts.py`, `src/audio/speaker_factory.py` |
| Judgment engine | `src/judgment/judge.py` |
| Entry point | `main.py` |

### Requirements & Setup

All models live in **external AI software** (LM Studio / Voicebox) — the project ships **no model weights**.

Full bilingual install guide (English official method + Chinese with domestic-mirror method): **`new_computer_download/READMEfirst.md`**.

Quick start:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python setup_ffmpeg.py          # copies ffmpeg if missing
# 1) LM Studio: load qwen/qwen3.6-35b-a3b, start server at 127.0.0.1:12345
# 2) Voicebox App: clone voices/silverwalf_voice.wav as the "JAC" voice
python main.py
```

### Running

```bash
python main.py
```

- `q` quit · `SPACE` manual wake · console text input talks directly (bypasses wake word).

### Models

- **Brain**: `qwen/qwen3.6-35b-a3b` in **LM Studio** (default `backend="lm_studio"`, `127.0.0.1:12345`). Identifier must match exactly.
- **Judgment**: `minicpm-v-4_5` in LM Studio (optional, enables proactive mode). The omni full-duplex mode instead uses `MiniCPM-o-4_5` (GGUF Q8_0) via llama.cpp-omni on `:9060`.
- **TTS**: Voicebox App clones `voices/silverwalf_voice.wav` → **JAC** voiceprofile. No in-project TTS weights.
- **Detection**: `yolov8n.pt` auto-downloaded by `ultralytics` on first run.

### Status & Roadmap

Implemented: multi-backend brain, proactive judgment engine, multimodal Q&A, SLEEP/AWAKE state machine, console input, persistent memory, **Function Calling tool layer (open apps/web, read-only file search, system info, restricted shell)**.
Not yet: agent framework beyond the tool loop, MCP/OpenClaw integration, live web tools (weather/calendar), streaming STT/LLM/TTS. Gap notes live in `CHANGELOG.md` App. A (merged from the old `codingLOG.md`).

### Docs map

- `AGENTS.md` — developer contract (architecture, run model, deps).
- `CHANGELOG.md` — change log **and doc hub**: App. A gap notes, App. B memory contract, App. C memory test plan / code truth, App. D–F memory user guide / privacy / runbook.
- `docs/minicpmo_master_plan.md`, `docs/webrtc_aec_plan.md` — design plans (the latter: WebRTC AEC, pending review, not implemented).
- `new_computer_download/READMEfirst.md` — install guide.

> Project background docs in `codinglog_by_awaqwq233/` are maintained manually by the owner and are git-ignored.

---

## 中文说明

### 项目是什么

J.A.C. 是一个**本地优先的多模态 AI 管家**原型，灵感来自 JARVIS。长期愿景是打造一个**强人工智能管家**：主动感知环境、提前规划与预警、在危险发生前发出警示、并能应急接管。智能眼镜 / MR 等可穿戴终端只是**可选的随身外设**，并非核心目标——J.A.C. 的本质是 **AI 架构、系统与整套主动服务框架**，可穿戴终端只是承载它的一种形态。

当前代码库是一个 **macOS 优先的 Python 桌面原型**（保留 Windows/Linux 兼容代码，但开发机为 macOS），还不是最终的眼镜/云端架构。

### 功能特性

- **多模态感知** —— OpenCV 摄像头 + YOLOv8 检测、PyAudio + WebRTC VAD 麦克风采集、OpenAI Whisper 语音识别。
- **本地大脑** —— 在 **LM Studio** 中加载 `qwen/qwen3.6-35b-a3b`（原生多模态、禁用思考），走多后端 `LocalBrain`（lm_studio / ollama / llama_cpp / auto）。
- **克隆音色 TTS（纯文本中性朗读）** —— Voicebox（开源克隆引擎，macOS 主力）克隆 `voices/silverwalf_voice.wav` 得到 J.A.C. 音色，Qwen3-TTS（仅 NVIDIA）与系统 TTS 兜底；2026-08-09 起 brain 只输出纯文本，J.A.C. 一律中性朗读（情绪接口保留但未调用）。
- **主动判断引擎** —— `src/judgment/judge.py`（LM Studio 标准 chat API 上的 `minicpm-v-4_5`，每 4s 拿截图＋转录文本问一次要不要介入），**默认开启**（`JUDGMENT_ENGINE_ENABLED=True`）；未加载该模型时自动进入被动模式。注意：OMNI 全双工链路用的是**另一个实例** `MiniCPM-o-4_5`（GGUF Q8_0，跑在 llama.cpp-omni `:9060`）。
- **多模态问答** —— 视觉问题时把真实摄像头帧发给大脑。
- **唤醒词 + 控制台输入** —— 唤醒词（`jac` / `杰克` / `你好` …）或直接控制台输入对话。
- **持久记忆** —— JSON 长期记忆 + 轻量本地向量检索（`src/memory/`）。
- **全双工 OMNI 接管** —— 本地 MiniCPM-o-4_5 通过 llama.cpp-omni 持续交换音视频；Listen 采样参数会放入服务端协议要求的 `payload.config`，确保配置实际生效。图像上行同时提供「帧间隔」与「总开关」两级控制，可一键切到纯音频全双工，用于隔离验证视觉 token 对上下文窗口的消耗。

### 架构（模块一览）

| 模块 | 路径 |
| --- | --- |
| 摄像头采集 | `src/capture/camera.py` |
| YOLOv8 检测 | `src/analysis/detector.py` |
| 共享上下文（线程安全） | `src/utils/context.py` |
| VAD 录音 | `src/audio/recorder.py` |
| Whisper 识别 | `src/audio/stt.py` |
| 本地大脑（多后端） | `src/brain/llm.py` |
| TTS（Voicebox → Qwen3-TTS → 系统） | `src/audio/voicebox_tts.py`、`src/audio/qwen_tts.py`、`src/audio/speaker_factory.py` |
| 判断引擎 | `src/judgment/judge.py` |
| 入口 | `main.py` |

### 环境要求与安装

所有模型均在**外部 AI 软件**（LM Studio / Voicebox）中管理——**项目不内置任何模型权重**。

完整双语安装指南（英文官方方法 + 中文含国内镜像方法）：**`new_computer_download/READMEfirst.md`**。

快速开始：

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python setup_ffmpeg.py          # 缺失时复制 ffmpeg
# 1) LM Studio：加载 qwen/qwen3.6-35b-a3b，在 127.0.0.1:12345 开启服务器
# 2) Voicebox App：克隆 voices/silverwalf_voice.wav 为 “JAC” 声纹
python main.py
```

### 运行

```bash
python main.py
```

- `q` 退出 · `空格` 手动唤醒 · 控制台输入文字直接对话（绕过唤醒词）。

### 模型

- **大脑**：LM Studio 中的 `qwen/qwen3.6-35b-a3b`（默认 `backend="lm_studio"`，`127.0.0.1:12345`），标识符须精确匹配。
- **判断**：LM Studio 中的 `minicpm-v-4_5`（可选，开启主动模式）；OMNI 全双工模式则使用 llama.cpp-omni `:9060` 上的 `MiniCPM-o-4_5`（GGUF Q8_0）。
- **TTS**：Voicebox App 克隆 `voices/silverwalf_voice.wav` → **JAC** 声纹，项目内无 TTS 权重。
- **检测**：`yolov8n.pt` 首次运行由 `ultralytics` 自动下载。

### 当前状态与路线图

已实现：多后端大脑、主动判断引擎、多模态问答、SLEEP/AWAKE 状态机、控制台输入、持久记忆、全双工 **omni 接管模式**（MiniCPM-o 本地多模态 + `<<CALL_QWEN>>` 升级路由到 qwen3.6-35b+工具 + Voicebox 克隆声纹回灌，GUI 右侧面板可开关、与 judge/TTS/tools 互斥）、**Function Calling 工具层（打开应用/网页、只读本地文件搜索、系统状态查询、受限 shell，GUI 右侧面板可开关）**。
尚未实现：工具循环之外的 agent 框架、MCP/OpenClaw 集成、实时联网工具（天气/日程）、token 级流式 TTS（omni 全双工已落地 LLM 流式输出 + M7b 句子级 Voicebox 桥接近似实时，但非 token 级）。差距笔记见 `CHANGELOG.md` **附 A**（原 `codingLOG.md` 已于 2026-10-01 全量并入并删除）。

### 文档导航

- `AGENTS.md` —— 开发者契约（架构、运行方式、依赖）。
- `CHANGELOG.md` —— 变更日志**兼文档归口中心**：附 A 差距笔记、附 B 记忆契约、附 C 记忆测试计划与代码真值、附 D~F 记忆用户指南 / 隐私 / 运维手册。
- `docs/minicpmo_master_plan.md`、`docs/webrtc_aec_plan.md` —— 设计与方案（后者为 WebRTC AEC 方案，待评审未实施）。
- `new_computer_download/READMEfirst.md` —— 安装指南。

> `codinglog_by_awaqwq233/` 下的项目背景文档由 bo s s 手动维护，已加入 `.gitignore`，不自动同步。
