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

**Migration is at M0. The production entry point and `src/omni/` still run the old architecture.** ChromaDB, cloud escalation, new capture and native playback are not yet wired into the main program. The isolated M0 scripts validate the chosen backend before production changes.

The pinned Gateway limits video sessions to 300 seconds and audio sessions to 600 seconds. The future always-on client must reconnect and restore context.

### Setup and verification

Follow [the setup guide](new_computer_download/READMEfirst.md). Python 3.11 is required for M0. Keep an existing legacy `.venv`; create a separate `.cache/m0/venv`.

- [verify_duplex.py](verify_duplex.py): file-based Gateway protocol probe.
- [verify_live_duplex.py](verify_live_duplex.py): explicitly consented short camera/microphone/native playback probe.
- [start_m0_backend.py](new_computer_download/start_m0_backend.py): version/model preflight and local three-process launcher.
- [requirements-m0.txt](new_computer_download/requirements-m0.txt): isolated control-plane and probe dependencies.

Model binaries stay outside this project. Probe reports and logs go to ignored `output/m0/`; virtual environments go to ignored `.cache/`. The file probe does not open a microphone, camera or speaker, so live interaction and a 30-minute soak require separate verification.

The 40-second live probe passed on 2026-10-06: 31 seconds of native audio played, processing P95 679 ms, audio queue peak one chunk. The user confirmed audible, relevant responses. Ten rapid device/WebSocket lifecycle cycles also passed. The 30-minute soak is deferred; M0 is not fully signed off.

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

**当前执行 M0，主程序仍是旧架构。** `main.py`、GUI、`src/omni/`、记忆、云端升级和播放尚未按方案 B 切换。独立 M0 脚本先证明后端契约可运行。

固定版本 Gateway 的视频会话限 300 秒，音频会话限 600 秒；常开客户端需要受控重连及上下文恢复。

完整安装流程见 [READMEfirst.md](new_computer_download/READMEfirst.md)。M0 使用 Python 3.11 与独立 `.cache/m0/venv`，保留旧 `.venv`。

- `verify_duplex.py`：用本地媒体文件验证排队、初始化、流式输入输出和关闭。
- `verify_live_duplex.py`：需明确设备同意的短时摄像头/麦克风与原生播放探针。
- `start_m0_backend.py`：检查 commit / GGUF / 端口，启动和回收本机三进程。
- `requirements-m0.txt`：M0 独立依赖；生产依赖清理在迁移后完成。
- `output/m0/`：已忽略的运行日志和探针报告。

文件探针不打开麦克风、摄像头或扬声器。实时听说、画面、10 次启停和 30 分钟运行仍须另外验收。

2026-10-06 的 40 秒真机探针已通过：原生语音播放 31 秒，处理耗时 P95 679ms，音频队列最高 1 块，用户确认可听且内容相关。10 次设备/WS 快速启停通过。30 分钟长测按用户要求稍后进行，M0 尚未完整验收。

### 文档与未来终端

[AGENTS.md](AGENTS.md) 是开发者契约；[CHANGELOG.md](CHANGELOG.md) 记录变更和迁移差距。已确认的方案 B 权威 DOCX 在本机 `brainstorming_projectPLAN/` 维护，整个目录不进 Git、不推送。Agent 已获准按确认的架构决策同步该本地目录。模板 `voices/silverwalf_voice.wav` 经 bo s s 明确允许公开推送；模型和实际测试录音录像不推送。`codinglog_by_awaqwq233/` 仍仅由 bo s s 手动维护且不进 Git。

未来可接入全屋摄像头、智能眼镜第一视角，以及 AR / Vision Pro 空间 GUI。
