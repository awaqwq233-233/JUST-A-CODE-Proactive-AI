# J.A.C. — Just A Code

> A local-first, multimodal **Proactive AI Butler** prototype. Perceives your environment through camera + microphone, reasons about the scene, and replies with voice TTS — aiming to act *before* you ask, like a JARVIS-style assistant.

[中文说明](#中文说明)

---

## English

### What it is

J.A.C. is a **local-first multimodal AI butler** inspired by JARVIS. The long-term vision is a **strong-AI butler** that proactively perceives the world, plans ahead, warns of hazards *before* they happen, and can take over in emergencies. Wearable terminals (smart glasses / MR headsets) are **optional peripherals**, not the core goal — J.A.C. is fundamentally an **AI architecture, system, and end-to-end proactive-service framework**; the wearable is just one way to carry it.

The current codebase is a **macOS-first Python desktop prototype** (Windows/Linux compatibility code is kept, but the dev machine is macOS).

### New architecture (finalized 2026-10-01)

Since 2026-10-01 all development follows the **new architecture** defined in `brainstorming_projectPLAN/10月1日新架构.docx`. Three model tiers:

| Tier | Model | Runs at | Role | Output |
| --- | --- | --- | --- | --- |
| Perception / proactive judgment | `MiniCPM-o-4_5` | local llama.cpp (Metal) `ws://127.0.0.1:8080/duplex` | full-duplex listening/speaking + proactive intervention; escalates when out of depth | **voice** (built-in voice cloning TTS) |
| Brain | `qwen/qwen3.6-35b-a3b` | LM Studio `127.0.0.1:12345` | complex decisions: Function Calling / Tool Use / Agentic Coding (controls the computer) | **files** |
| Cloud | OpenClaw | server (DeepSeek API, public address) | complex, long-running tasks | **files** |

**Input**: audio via **SoundDevice** resampled to `16 kHz / 16 bit / Mono PCM`; video via **OpenCV** in a dedicated background thread (never `cv2.read()`/JPEG-encode on the main loop), pushed through a thread-safe Queue at **≤10 fps**, forced **640×480**, 5–10 fps. Both stream to the backend over **WebSocket** at `ws://127.0.0.1:8080/duplex` (see `llama-cpm` `examples/duplex`).

**Backend**: the OpenBMB `llama-cpm` branch (full-duplex fork of llama.cpp), built with Metal, serving `MiniCPM-o-4_5` GGUF **Q4_K_M** with `-ngl 999 -c 4096 -t 8 --mlock --no-mmap --flash-attn`. **Metal (GPU), not MPS** — PyTorch MPS is buggy/OOM-prone for full-duplex streaming. Full-duplex uses a **WebSocket long connection**, not the standard OpenAI Chat API.

**Voice cloning**: offline-extract a speaker embedding (`.pt`) on an NVIDIA machine, then launch with `--tts-speaker-emb ./my_custom_voice.pt`; the client `init` message activates it via `voice_id: "custom"`.

**Memory**: **ChromaDB** + **BGE-Small-ZH-v1.5 (ONNX INT8)** embeddings + **JSON**. Batch-write (cache ~5 summaries, one `collection.add()`), and retrieve only at WS-init (opening remark) and after VAD end-of-sentence — never inside the audio callback.

**GUI**: a graphical GUI with live camera view, settings, and a conversation transcript.

**DLC (future, optional)**: Xiaomi MiLoco whole-home camera monitoring → MiniCPM-o-4_5; smart glasses for first-person view; waveguide AR / Vision Pro spatial-computing 3D GUI overlay.

> ⚠️ The **code is not yet migrated** to this architecture — the current codebase still reflects the previous design (see `AGENTS.md` "代码迁移状态" and `CHANGELOG.md` App. A).

### Architecture (module map)

| Module | Path |
| --- | --- |
| Camera capture | `src/capture/camera.py` |
| YOLOv8 detector | `src/analysis/detector.py` |
| Shared context (thread-safe) | `src/utils/context.py` |
| Audio capture (VAD) | `src/audio/recorder.py` |
| Whisper STT | `src/audio/stt.py` |
| Local brain (multi-backend) | `src/brain/llm.py` |
| Tool layer (Function Calling) | `src/tools/` |
| Full-duplex omni client | `src/omni/` |
| Entry point | `main.py` |

### Requirements & Setup

All model weights live **outside the project** (external AI software / model repos). Full bilingual install guide: **`new_computer_download/READMEfirst.md`**. Runtime env: **Python 3.11**.

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python setup_ffmpeg.py          # copies ffmpeg if missing

# Backend: build OpenBMB llama-cpm + download MiniCPM-o-4_5 GGUF
git clone https://github.com/OpenBMB/llama.cpp.git llama-cpm
cd llama-cpm && make GGML_METAL=1 -j18
huggingface-cli download openbmb/MiniCPM-o-4_5-gguf minicpm-o-4_5-q4_k_m.gguf --local-dir ./models
./llama-server -m ./models/minicpm-o-4_5-q4_k_m.gguf \
  --host 127.0.0.1 --port 8080 -ngl 999 -c 4096 -t 8 \
  --mlock --no-mmap --flash-attn --tts-speaker-emb ./my_custom_voice.pt

# Brain: LM Studio loads qwen/qwen3.6-35b-a3b, server at 127.0.0.1:12345
python main.py
```

### Running

```bash
python main.py
```

- `q` quit · `SPACE` manual wake · console text input talks directly (bypasses wake word).

### Models

- **Perception / proactive**: `MiniCPM-o-4_5` GGUF Q4_K_M via OpenBMB `llama-cpm` (Metal), `ws://127.0.0.1:8080/duplex`.
- **Brain**: `qwen/qwen3.6-35b-a3b` in **LM Studio** (`127.0.0.1:12345`), identifier must match exactly.
- **Cloud**: OpenClaw (DeepSeek API, public address) for long-running tasks.
- **Voice**: MiniCPM-o built-in voice cloning (speaker embedding `.pt`).
- **Memory**: ChromaDB + `BGE-Small-ZH-v1.5` (ONNX INT8).
- **Detection**: `yolov8n.pt` auto-downloaded by `ultralytics` on first run.

### Status & Roadmap

The new architecture is **finalized**; code migration is **pending**. Gap notes live in `CHANGELOG.md` App. A (rewritten to the new architecture).

### Docs map

- `AGENTS.md` — developer contract (new architecture, migration status, run model).
- `CHANGELOG.md` — change log **and doc hub**: App. A gap notes, App. B–F memory contract / test plan / user guide / privacy / runbook.
- `new_computer_download/READMEfirst.md` — install guide.
- `brainstorming_projectPLAN/10月1日新架构.docx` — the authoritative new-architecture spec (owner-maintained).

> Project background docs in `codinglog_by_awaqwq233/` are maintained manually by the owner and are git-ignored.

---

## 中文说明

### 项目是什么

J.A.C. 是一个**本地优先的多模态 AI 管家**原型，灵感来自 JARVIS。长期愿景是打造一个**强人工智能管家**：主动感知环境、提前规划与预警、在危险发生前发出警示、并能应急接管。智能眼镜 / MR 等可穿戴终端只是**可选的随身外设**，并非核心目标——J.A.C. 的本质是 **AI 架构、系统与整套主动服务框架**，可穿戴终端只是承载它的一种形态。

当前代码库是一个 **macOS 优先的 Python 桌面原型**（保留 Windows/Linux 兼容代码，但开发机为 macOS）。

### 新架构（2026-10-01 定案）

自 2026-10-01 起所有开发严格遵循 `brainstorming_projectPLAN/10月1日新架构.docx` 定案的**新架构**，三层模型：

| 层 | 模型 | 运行位置 | 职责 | 输出 |
| --- | --- | --- | --- | --- |
| 感知/主动判断层 | `MiniCPM-o-4_5` | 本地 llama.cpp（Metal）`ws://127.0.0.1:8080/duplex` | 全双工持续听/说 + 主动介入；能力不足时向上升级 | **语音**（自带音色克隆 TTS） |
| 大脑层 | `qwen/qwen3.6-35b-a3b` | LM Studio `127.0.0.1:12345` | 复杂决策：Function Calling / Tool Use / Agentic Coding（控制电脑） | **文件** |
| 云端层 | 云端 OpenClaw | 服务器（DeepSeek API，有公网地址） | 更复杂、长时间任务 | **文件** |

**输入**：音频用 **SoundDevice** 实时重采样为 `16kHz / 16bit / Mono PCM`；视频用 **OpenCV** 在独立后台线程采集（绝不在主循环同步 `cv2.read()` / JPEG 编码），经线程安全 Queue 以 **≤10fps**（强制 **640×480**，5–10fps）上送；两者经 **WebSocket** 发往 `ws://127.0.0.1:8080/duplex`（见 `llama-cpm` `examples/duplex`）。

**后端**：OpenBMB `llama-cpm` 分支（llama.cpp 全双工分支），Metal 编译，加载 `MiniCPM-o-4_5` GGUF **Q4_K_M**，参数 `-ngl 999 -c 4096 -t 8 --mlock --no-mmap --flash-attn`。**强制 Metal（GPU）而非 MPS**——PyTorch MPS 全双工流式有 Bug 易 OOM。全双工用 **WebSocket 长连接**，不用标准 OpenAI Chat API。

**音色克隆**：在 NVIDIA 机器离线提取 speaker embedding（`.pt`），启动时 `--tts-speaker-emb ./my_custom_voice.pt` 加载，客户端 `init` 消息以 `voice_id: "custom"` 激活。

**记忆**：**ChromaDB** + **BGE-Small-ZH-v1.5（ONNX INT8）** + **JSON**。批量写库（缓存约 5 条摘要再一次 `collection.add()`）；只在「WS 建立发 init 前」与「VAD 判定说完一句后」两处检索，严禁放音频回调里。

**GUI**：图形化 GUI，含画面预览、设置参数、对话文本记录。

**DLC（未来可选）**：小米 miloco 全屋摄像头监看 → MiniCPM-o-4_5；智能眼镜第一视角；光波导 AR / Vision Pro 空间计算 3D GUI 覆盖。

> ⚠️ **代码尚未迁移到新架构**——当前代码仍为旧实现（见 `AGENTS.md`「代码迁移状态」与 `CHANGELOG.md` 附 A）。

### 架构（模块一览）

| 模块 | 路径 |
| --- | --- |
| 摄像头采集 | `src/capture/camera.py` |
| YOLOv8 检测 | `src/analysis/detector.py` |
| 共享上下文（线程安全） | `src/utils/context.py` |
| 音频采集（VAD） | `src/audio/recorder.py` |
| Whisper 识别 | `src/audio/stt.py` |
| 本地大脑（多后端） | `src/brain/llm.py` |
| 工具层（Function Calling） | `src/tools/` |
| 全双工 omni 客户端 | `src/omni/` |
| 入口 | `main.py` |

### 环境要求与安装

所有模型权重均**在项目之外**（外部 AI 软件 / 模型仓库）。完整双语安装指南：**`new_computer_download/READMEfirst.md`**。运行环境：**Python 3.11**。

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python setup_ffmpeg.py          # 缺失时复制 ffmpeg

# 后端：编译 OpenBMB llama-cpm + 下载 MiniCPM-o-4_5 GGUF
git clone https://github.com/OpenBMB/llama.cpp.git llama-cpm
cd llama-cpm && make GGML_METAL=1 -j18
huggingface-cli download openbmb/MiniCPM-o-4_5-gguf minicpm-o-4_5-q4_k_m.gguf --local-dir ./models
./llama-server -m ./models/minicpm-o-4_5-q4_k_m.gguf \
  --host 127.0.0.1 --port 8080 -ngl 999 -c 4096 -t 8 \
  --mlock --no-mmap --flash-attn --tts-speaker-emb ./my_custom_voice.pt

# 大脑：LM Studio 加载 qwen/qwen3.6-35b-a3b，服务器 127.0.0.1:12345
python main.py
```

### 运行

```bash
python main.py
```

- `q` 退出 · `空格` 手动唤醒 · 控制台输入文字直接对话（绕过唤醒词）。

### 模型

- **感知/主动判断**：`MiniCPM-o-4_5` GGUF Q4_K_M，经 OpenBMB `llama-cpm`（Metal），`ws://127.0.0.1:8080/duplex`。
- **大脑**：LM Studio 中的 `qwen/qwen3.6-35b-a3b`（`127.0.0.1:12345`），标识符须精确匹配。
- **云端**：OpenClaw（DeepSeek API，公网地址），处理长时间任务。
- **语音**：MiniCPM-o 自带音色克隆（speaker embedding `.pt`）。
- **记忆**：ChromaDB + `BGE-Small-ZH-v1.5`（ONNX INT8）。
- **检测**：`yolov8n.pt` 首次运行由 `ultralytics` 自动下载。

### 当前状态与路线图

新架构已**定案**；代码迁移**待进行**。差距笔记见 `CHANGELOG.md` **附 A**（已按新架构口径改写）。

### 文档导航

- `AGENTS.md` —— 开发者契约（新架构、迁移状态、运行方式）。
- `CHANGELOG.md` —— 变更日志**兼文档归口中心**：附 A 差距笔记、附 B~F 记忆契约 / 测试计划 / 用户指南 / 隐私 / 运维手册。
- `new_computer_download/READMEfirst.md` —— 安装指南。
- `brainstorming_projectPLAN/10月1日新架构.docx` —— 新架构权威基准（bo s s 维护）。

> `codinglog_by_awaqwq233/` 下的项目背景文档由 bo s s 手动维护，已加入 `.gitignore`，不自动同步。
