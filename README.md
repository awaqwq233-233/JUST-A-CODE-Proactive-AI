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
- **Emotion-aware TTS** — Voicebox (open-source cloning engine, macOS primary) cloning the J.A.C. voice from `voices/silverwalf_voice.wav`, with Qwen3-TTS (NVIDIA-only) and system-TTS fallbacks.
- **Proactive judgment engine** — `src/judgment/judge.py` (MiniCPM-o via LM Studio) continuously decides whether to intervene; **on by default** (`JUDGMENT_ENGINE_ENABLED=True`); auto passive mode if MiniCPM-o not loaded.
- **Multimodal Q&A** — sends the real camera frame to the brain for vision questions.
- **Wake-word + console input** — wake words (`jac` / `杰克` / `你好` …) or just type in the console to talk.
- **Persistent memory** — JSON long-term memory + lightweight local vector retrieval (`src/memory/`).

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
- **Judgment**: MiniCPM-o in LM Studio (optional, enables proactive mode).
- **TTS**: Voicebox App clones `voices/silverwalf_voice.wav` → **JAC** voiceprofile. No in-project TTS weights.
- **Detection**: `yolov8n.pt` auto-downloaded by `ultralytics` on first run.

### Status & Roadmap

Implemented: multi-backend brain, proactive judgment engine, multimodal Q&A, SLEEP/AWAKE state machine, console input, persistent memory, **Function Calling tool layer (open apps/web, read-only file search, system info, restricted shell)**.
Not yet: agent framework beyond the tool loop, MCP/OpenClaw integration, live web tools (weather/calendar), streaming STT/LLM/TTS. See `codingLOG.md` for the gap notes (read `CHANGELOG.md` + `codingLOG.md` for the latest changes).

### Docs map

- `AGENTS.md` — developer contract (architecture, run model, deps).
- `CHANGELOG.md` — change log.
- `codingLOG.md` — gap notes vs. final goal.
- `docs/memory/` — memory subsystem docs.
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
- **带情绪 TTS** —— Voicebox（开源克隆引擎，macOS 主力）克隆 `voices/silverwalf_voice.wav` 得到 J.A.C. 音色，Qwen3-TTS（仅 NVIDIA）与系统 TTS 兜底。
- **主动判断引擎** —— `src/judgment/judge.py`（LM Studio 上的 MiniCPM-o）持续判断是否介入，**默认开启**（`JUDGMENT_ENGINE_ENABLED=True`）；未加载 MiniCPM-o 时自动进入被动模式。
- **多模态问答** —— 视觉问题时把真实摄像头帧发给大脑。
- **唤醒词 + 控制台输入** —— 唤醒词（`jac` / `杰克` / `你好` …）或直接控制台输入对话。
- **持久记忆** —— JSON 长期记忆 + 轻量本地向量检索（`src/memory/`）。

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
- **判断**：LM Studio 中的 MiniCPM-o（可选，开启主动模式）。
- **TTS**：Voicebox App 克隆 `voices/silverwalf_voice.wav` → **JAC** 声纹，项目内无 TTS 权重。
- **检测**：`yolov8n.pt` 首次运行由 `ultralytics` 自动下载。

### 当前状态与路线图

已实现：多后端大脑、主动判断引擎、多模态问答、SLEEP/AWAKE 状态机、控制台输入、持久记忆、全双工 **omni 接管模式**（MiniCPM-o 本地多模态 + `<<CALL_QWEN>>` 升级路由到 qwen3.6-35b+工具 + Voicebox 克隆声纹回灌，GUI 右侧面板可开关、与 judge/TTS/tools 互斥）、**Function Calling 工具层（打开应用/网页、只读本地文件搜索、系统状态查询、受限 shell，GUI 右侧面板可开关）**。
尚未实现：工具循环之外的 agent 框架、MCP/OpenClaw 集成、实时联网工具（天气/日程）、token 级流式 TTS（omni 全双工已落地 LLM 流式输出 + M7b 句子级 Voicebox 桥接近似实时，但非 token 级）。差距笔记见 `codingLOG.md`（了解最新改动请读 `CHANGELOG.md` 与 `codingLOG.md`）。

### 文档导航

- `AGENTS.md` —— 开发者契约（架构、运行方式、依赖）。
- `CHANGELOG.md` —— 变更日志。
- `codingLOG.md` —— 与最终目标的差距笔记。
- `docs/memory/` —— 记忆子系统文档。
- `new_computer_download/READMEfirst.md` —— 安装指南。

> `codinglog_by_awaqwq233/` 下的项目背景文档由 bo s s 手动维护，已加入 `.gitignore`，不自动同步。
