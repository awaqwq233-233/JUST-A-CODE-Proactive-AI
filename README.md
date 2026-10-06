# J.A.C. — Just A Code

A local-first multimodal Proactive AI Butler inspired by JARVIS.

[中文说明](#中文说明)

## English

J.A.C. aims to perceive its surroundings, plan ahead, warn of hazards and perform tasks proactively. Smart glasses and MR are optional terminals; the core product is the AI system and service framework. Development primarily targets macOS on Apple Silicon.

### Architecture and current status

Option B was approved on 2026-10-06. The three model tiers remain:

| Tier | Runtime | Responsibility |
| --- | --- | --- |
| Perception and proactive judgment | MiniCPM-o-4_5, local C++ Metal | Full-duplex audio/video understanding and native voice output |
| Local brain | qwen/qwen3.6-35b-a3b, LM Studio at 127.0.0.1:12345 | Decisions, tool calls, coding and file output |
| Cloud | OpenClaw with DeepSeek API | Complex or long-running tasks and file output |

The local perception chain is J.A.C. → MiniCPM-o-Demo Gateway → Worker → llama.cpp-omni. The app connects to `ws://127.0.0.1:8006/v1/realtime?mode=video`. Repository commits, model revision and SHA256 hashes are pinned in [backend.lock.json](backend.lock.json).

Input audio is 16 kHz mono float32 PCM, sent in fixed 1-second blocks. Output audio is 24 kHz mono float32 PCM. Voice cloning uses a reference WAV through `session.init.payload.voice`.

The target capture pipeline uses SoundDevice and background OpenCV capture at 640×480 and 5–10 fps. Each audio block carries the latest JPEG frame by default; GUI preview and model input rates are independent. Audio callbacks and the Qt/asyncio main loop must not perform blocking capture, encoding or retrieval.

Target memory uses JSON as the structured source of truth, ChromaDB as a rebuildable index, and BGE-Small-ZH-v1.5 ONNX INT8 embeddings. Summaries are batch-written every five records or on session end. Retrieval runs at session initialization and completed user utterances, using parallel local transcription where needed.

**M0 is accepted by the user; the 30-minute soak was explicitly waived. Migration is now at M1.** The production Gateway SDK, SoundDevice capture/native playback and background 640×480 OpenCV capture are available through `main.py --gateway`, with a lightweight GUI via `--gui`. Legacy modes remain available; Qwen tool escalation, ChromaDB and OpenClaw migration are still pending.

The pinned Gateway limits video sessions to 300 seconds and audio sessions to 600 seconds. M1 rotates video sessions after 240 input seconds, closes/releases devices and reconnects with the reference voice and bounded assistant history plus supplied confirmed context. Capture visibly pauses during reconnection. It does not reconstruct untranscribed user speech or provide seamless audio across the gap.

### Setup and verification

Follow [the setup guide](new_computer_download/READMEfirst.md). Python 3.11 is required for M0/M1. `setup_new_computer.py --only gateway` installs and checks the isolated `.cache/m0/venv` environment, preserving an existing legacy `.venv`.

- [gateway_client.py](src/omni/gateway_client.py): reusable production Gateway client and controlled session reconnection.
- [verify_duplex.py](verify_duplex.py): file-based Gateway protocol probe sharing the production codec.
- [verify_live_duplex.py](verify_live_duplex.py): explicitly consented short camera/microphone/native playback probe.
- [verify_soak_duplex.py](verify_soak_duplex.py): explicitly consented soak with eight video sessions totaling 1,800 input seconds, backend health/RSS samples and per-session reports. Devices restart between sessions; conversation context is not restored.
- [start_m0_backend.py](new_computer_download/start_m0_backend.py): version/model preflight and local three-process launcher.
- [requirements-m0.txt](new_computer_download/requirements-m0.txt): isolated control-plane and probe dependencies.

Model binaries stay outside this project. Probe reports and logs go to ignored `output/m0/`; virtual environments go to ignored `.cache/`. The file probe does not open a microphone, camera or speaker, so live interaction and a 30-minute soak require separate verification.

The 40-second live probe and ten rapid device/WebSocket lifecycle cycles passed on 2026-10-06. Two subsequent 225-second live sessions also passed: P95 679/891 ms, 176.2 seconds of native playback and no device/health-check errors. The user confirmed satisfactory interaction and accepted M0 while waiving the remaining soak. Raw reports retain `soak_30min_verified=false`; the acceptance decision does not claim an unperformed 30-minute test.

Start the pinned backend first, then use the Python 3.11 environment to run `main.py --gateway --gui`, or `main.py --gateway --consent-devices` for the console. The GUI requires explicit device consent and headphones before capture starts. It loads without PyAudio, torch, Whisper or YOLO; selecting legacy mode requires the legacy dependencies.

The user confirmed live listening, vision and replies work. The reported black preview after stop/start was traced to a stopped GUI refresh timer; restart now resumes it on the Qt main thread. Offline restart regressions pass; close and reopen the GUI after updating code to verify the fix on hardware.

### Documentation

- [AGENTS.md](AGENTS.md): architecture, engineering contract and migration status.
- [CHANGELOG.md](CHANGELOG.md): actual changes and appendices covering gaps, memory contracts, tests, privacy and operations.
- Architecture DOCX: the authoritative option B baseline is maintained locally in `brainstorming_projectPLAN/`, excluded from Git and future pushes.
- [Pinned official protocol](https://github.com/OpenBMB/MiniCPM-o-Demo/blob/47709a9210dfd71afa76c058e017fc8c4db5c8d2/docs/en/realtime-protocol-overview.md): Gateway events.
- [Pinned C++ engine](https://github.com/tc-mb/llama.cpp-omni/tree/873056743b74e1a4ce5dcf7290e2298428e214db): Metal inference.

## 中文说明

J.A.C. 是一个本地优先的多模态主动 AI 管家，灵感来自 JARVIS。目标是持续感知环境、提前规划与预警，并主动完成任务。智能眼镜、AR 和 Vision Pro 是可选终端，核心是 AI 系统与主动服务框架。当前主要开发平台为 macOS Apple Silicon。

### 已确认的方案 B

2026-10-06 确认采用官方维护的 MiniCPM-o-Demo Gateway / Worker 与 tc-mb llama.cpp-omni Metal 后端。三层模型保持：

| 层级 | 运行位置 | 职责 |
| --- | --- | --- |
| MiniCPM-o-4_5 感知和主动判断 | 本地 C++ Metal | 全双工音视频理解、主动介入、原生语音输出 |
| qwen/qwen3.6-35b-a3b 本地大脑 | LM Studio 127.0.0.1:12345 | 复杂决策、工具调用、控制电脑、文件输出 |
| 云端 OpenClaw | 服务器接入 DeepSeek API | 复杂或长时间任务、文件输出 |

第一层客户端连接 `ws://127.0.0.1:8006/v1/realtime?mode=video`，经 Worker 调用 C++ 引擎。固定 commit、模型 revision、SHA256 与端口见 [backend.lock.json](backend.lock.json)。

音频上行为 16kHz mono float32 PCM，每 1 秒发送一块；下行为 24kHz mono float32 PCM。音色直接来自参考 WAV，通过 `session.init.payload.voice` 传递。模型二进制保存在项目外。

目标采集层使用 SoundDevice 和独立 OpenCV 线程，640×480、采集 5–10fps；每个音频块默认附最新一帧 JPEG。GUI 预览与模型上行频率独立，采集、编码、重采样和记忆检索不得阻塞主事件循环。

目标记忆层为 JSON 事实真源 + ChromaDB 可重建索引 + BGE-Small-ZH-v1.5 ONNX INT8。每 5 条摘要或会话结束批量提交，检索仅在会话初始化和完整句子结束后触发。本地 Whisper 为并行转写提供查询文本。

### 当前实现状态

**M0 已由用户确认通过，30 分钟长测明确免测；现已进入 M1。** 新生产 Gateway 客户端、SoundDevice 采集/原生播放和 640×480 后台视频采集已接入 `main.py --gateway`，`--gui` 打开轻量 GUI。旧模式保留兼容入口；Qwen 工具升级、ChromaDB 和 OpenClaw 尚未迁移。

固定版本 Gateway 的视频会话限 300 秒、音频会话限 600 秒。M1 默认每 240 秒上行后关闭并重建视频会话，重新注入参考音、已确认上下文和有界助手历史；重连时明确暂停采集，未转写的用户原话不会凭空恢复，目前存在采集间隙。

完整安装流程见 [READMEfirst.md](new_computer_download/READMEfirst.md)。M0/M1 使用 Python 3.11 与独立 `.cache/m0/venv`，通过 `setup_new_computer.py --only gateway` 安装并自检，保留旧 `.venv`；旧 `--only m0` 参数仍兼容。

- `verify_duplex.py`：用本地媒体文件验证排队、初始化、流式输入输出和关闭。
- `verify_live_duplex.py`：需明确设备同意的短时摄像头/麦克风与原生播放探针。
- `verify_soak_duplex.py`：需明确设备同意的分会话长测；8 段累计 1800 秒上行，记录后端健康、RSS 和逐段统计。段间重开设备，不恢复对话上下文。
- `start_m0_backend.py`：检查 commit / GGUF / 端口，启动和回收本机三进程。
- `requirements-m0.txt`：M0 独立依赖；生产依赖清理在迁移后完成。
- `output/m0/`：已忽略的运行日志和探针报告。

文件探针不打开麦克风、摄像头或扬声器，真实听说和画面由设备测试验证；M0 的 30 分钟项已由用户明确豁免。

2026-10-06 的 40 秒真机探针、10 次设备/WS 快速启停以及 2 段各 225 秒真机验证通过。两段累计上行 450 秒、逐段 P95 679/891ms、原生语音收播合计 176.2 秒，设备异常与健康检查失败均为零。用户确认短测效果良好，接受 M0 并免做 30 分钟长测；原始报告保留 `soak_30min_verified=false`，表示该时长未实测。

先用固定版本启动器启动后端，再使用 Python 3.11 环境运行 `main.py --gateway --gui` 或 `main.py --gateway --consent-devices`。GUI 启动设备前须明确勾选同意并戴好耳机；Gateway 路径不加载旧 PyAudio/torch/Whisper/YOLO，选择旧模式才需要旧依赖。

用户已确认真机听看说正常。停止再启动后预览黑屏的问题已定位并修复：重启时在 Qt 主线程恢复画面刷新定时器，离屏启停回归通过。更新代码后须关闭并重新打开 GUI，再复验实际画面。

### 文档与未来终端

[AGENTS.md](AGENTS.md) 是开发者契约；[CHANGELOG.md](CHANGELOG.md) 记录变更和迁移差距。已确认的方案 B 权威 DOCX 在本机 `brainstorming_projectPLAN/` 维护，整个目录不进 Git、不推送。Agent 已获准按确认的架构决策同步该本地目录。模板 `voices/silverwalf_voice.wav` 经 bo s s 明确允许公开推送；模型和实际测试录音录像不推送。`codinglog_by_awaqwq233/` 仍仅由 bo s s 手动维护且不进 Git。

未来可接入全屋摄像头、智能眼镜第一视角，以及 AR / Vision Pro 空间 GUI。
