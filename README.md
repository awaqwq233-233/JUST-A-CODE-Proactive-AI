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

Input audio is 16 kHz mono float32 PCM, sent in fixed 1-second blocks. Output audio is 24 kHz mono float32 PCM. Voice cloning sends the reference WAV through `session.init.payload.voice`. The approved combined engine patch now applies its TTS reference to native Token2Wav using a local CPU/ONNX frontend (CAMPPlus speaker embedding, S3 reference tokens and mel features). The two additional ONNX files are pinned separately in `backend.lock.json` and live in the external GGUF directory's `voice-frontend/` subdirectory. Derived conditions are isolated by PCM content hash under ignored `.cache/voices/`; runtime never downloads models or sends the reference to the cloud. The client checks the backend's applied-reference hash before opening devices and rejects an unpatched backend or failed voice preparation instead of silently using its default voice. Native output, reference switching, failure rejection and two production-client file sessions were verified without opening devices; perceptual similarity still needs the user's listening feedback. See CHANGELOG Appendix A4.

The target capture pipeline uses SoundDevice and background OpenCV capture at 640×480 and 5–10 fps. Each audio block carries the latest JPEG frame by default; GUI preview and model input rates are independent. Audio callbacks and the Qt/asyncio main loop must not perform blocking capture, encoding or retrieval.

Target memory uses JSON as the structured source of truth, ChromaDB as a rebuildable index, and BGE-Small-ZH-v1.5 ONNX INT8 embeddings. Summaries are batch-written every five records or on session end. Retrieval runs at session initialization and completed user utterances, using parallel local transcription where needed.

**M0 is accepted with the 30-minute soak explicitly waived. M1 live listening/vision/speech, GUI preview and manual restart were accepted by the user on 2026-10-07.** The production Gateway SDK, SoundDevice capture/native playback and background 640×480 OpenCV capture are available through `main.py --gateway`, with a lightweight GUI via `--gui`. Next is Qwen brain/tool escalation; parallel transcription, ChromaDB and OpenClaw migration are still pending. This acceptance does not establish unlimited sessions or long-term stability.

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

Open the GUI in the Python 3.11 environment and use its Start Backend button, or start the pinned backend separately before running `main.py --gateway --gui`, or `main.py --gateway --consent-devices` for the console. As requested by the user, GUI device consent is enabled by default and its checkbox is removed; devices open only when Start Voice is clicked. Use headphones. It loads without PyAudio, torch, Whisper or YOLO. The old MiniCPM client, :9060 launcher, polling judge and traditional runtime have been removed; all entry points now use Gateway.

The user confirmed live listening, vision and replies work. Restart retesting on 2026-10-07 exposed residual preview pixels and early shutdown leaving the Worker/C++ backend busy. The preview now repaints its entire surface; stopping runs in the background and disables restart until cleanup finishes. Manual shutdown uses the same eight-second receive tail as session rotation, waits for `session.closed` and transport closure, then allows a one-second cooldown. Expect roughly nine seconds of shutdown, with a responsive GUI. Three real-backend restart cycles using synthetic frames and silence passed without accessing devices; the user subsequently confirmed the real GUI now works normally. These are client-side compatibility measures, not a source-level repair of upstream races. Restart an already affected backend once and reopen the GUI to load the fix; no dependency reinstall is needed.

The Gateway GUI uses a deep-blue gradient technology theme with cyan accents and subtle grids. The Parameters toggle has a persistent cyan selected state while the panel is open, a dark unselected state while closed, and distinct press/hover feedback. It has an original-aspect camera preview without black bars, a larger continuous reply transcript, separate collapsible connection logs, and a single status indicator at the top. Text input, send, display-resolution/zoom controls and obsolete model toggles are removed. Start Backend launches the pinned launcher asynchronously, validates repositories/models/ports and enables voice only after the explicit readiness event. Stopping Backend first closes the media session, then lets the launcher reclaim its three owned processes; closing the window waits for both. Already-running external backends are detected and reused without being terminated. Configure Demo/engine/external model directories and optional SHA256 checks through Backend Path Settings; these machine-specific paths are stored only in ignored `.cache/gui/backend.json` (or supplied with `JAC_DEMO_DIR` / `JAC_ENGINE_DIR` / `JAC_MODEL_DIR`). Before starting voice, adjust microphone gain, camera capture fps (5–10), session rotation (5–240 seconds), reconnect attempts, audio devices, camera index, local Gateway URL and reference WAV. Settings lock during operation. Image upload remains fixed at one latest frame per second. Qwen controls will appear when that integration exists. `main.py --gui` and `python -m src.omni --gui` use the same Gateway path; `--gateway` remains compatible. No new GUI dependency is required. The previous 138-test suite was verified in batches (the first full run had 136 passes and one test writing to the real home directory; its fixture was isolated and passed, alongside one newly added cleanup test). Synthetic screenshots were checked at 1440×880 and 1100×700 without accessing devices.

For VS Code, select the project interpreter `.cache/m0/venv/bin/python` and launch with `--gui`. This machine has an ignored F5 configuration named “J.A.C. · Gateway GUI” with an explicit interpreter; the editor’s Run Python File button does not apply launch.json arguments. See the setup guide, section 10.

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

音频上行为 16kHz mono float32 PCM，每 1 秒发送一块；下行为 24kHz mono float32 PCM。参考 WAV 通过 `session.init.payload.voice` 传递。经确认扩展的组合引擎补丁现已将 TTS 参考音接入原生 Token2Wav：本地 CPU／ONNX 前端生成 CAMPPlus 声纹、S3 参考语音 token 与 mel 条件，最终合成仍为 C++ Metal。两个新增 ONNX 的版本和 SHA256 单独记录在 `backend.lock.json`，位于仓库外 GGUF 目录的 `voice-frontend/` 子目录；派生条件按 PCM 内容哈希隔离缓存到忽略的 `.cache/voices/`。运行时不下载模型、不向云端发送参考音。客户端在开启设备前核对后端已应用的参考音哈希；旧后端或音色准备失败明确拒绝，不默默回落默认音色。已验证原生输出、参考音切换、失败拒绝及生产客户端两次文件会话重建；听感相似度仍待 bo s s 试听确认。详见 CHANGELOG 附 A4。模型二进制保存在项目外。

目标采集层使用 SoundDevice 和独立 OpenCV 线程，640×480、采集 5–10fps；每个音频块默认附最新一帧 JPEG。GUI 预览与模型上行频率独立，采集、编码、重采样和记忆检索不得阻塞主事件循环。

目标记忆层为 JSON 事实真源 + ChromaDB 可重建索引 + BGE-Small-ZH-v1.5 ONNX INT8。每 5 条摘要或会话结束批量提交，检索仅在会话初始化和完整句子结束后触发。本地 Whisper 为并行转写提供查询文本。

### 当前实现状态

**M0 已通过且 30 分钟长测免测；M1 听看说、GUI 预览与手动启停已于 2026-10-07 获用户真机验收。** 新生产 Gateway 客户端、SoundDevice 采集/原生播放和 640×480 后台视频采集已接入 `main.py --gateway`，`--gui` 打开轻量 GUI。下一步接入 Qwen 大脑/工具升级；并行转写、ChromaDB 和 OpenClaw 尚未迁移。旧 MiniCPM 客户端、:9060 启动器、轮询 judge 与传统运行入口已移除，所有入口统一使用 Gateway。当前验收不等于无限会话或长期稳定性验证。

固定版本 Gateway 的视频会话限 300 秒、音频会话限 600 秒。M1 默认每 240 秒上行后关闭并重建视频会话，重新注入参考音、已确认上下文和有界助手历史；重连时明确暂停采集，未转写的用户原话不会凭空恢复，目前存在采集间隙。

完整安装流程见 [READMEfirst.md](new_computer_download/READMEfirst.md)。M0/M1 使用 Python 3.11 与独立 `.cache/m0/venv`，通过 `setup_new_computer.py --only gateway` 安装并自检，保留旧 `.venv`；旧 `--only m0` 参数仍兼容。

- `verify_duplex.py`：用本地媒体文件验证排队、初始化、流式输入输出和关闭。
- `verify_live_duplex.py`：需明确设备同意的短时摄像头/麦克风与原生播放探针。
- `verify_soak_duplex.py`：需明确设备同意的分会话长测；8 段累计 1800 秒上行，记录后端健康、RSS 和逐段统计。段间重开设备，不恢复对话上下文。
- `start_m0_backend.py`：检查 commit / GGUF / 端口，启动和回收本机三进程。
- `requirements-m0.txt`：当前主程序与后端控制依赖；根 `requirements.txt` 共用该清单，`requirements_fixed.txt` 仅为历史快照。
- `output/m0/`：已忽略的运行日志和探针报告。

文件探针不打开麦克风、摄像头或扬声器，真实听说和画面由设备测试验证；M0 的 30 分钟项已由用户明确豁免。

2026-10-06 的 40 秒真机探针、10 次设备/WS 快速启停以及 2 段各 225 秒真机验证通过。两段累计上行 450 秒、逐段 P95 679/891ms、原生语音收播合计 176.2 秒，设备异常与健康检查失败均为零。用户确认短测效果良好，接受 M0 并免做 30 分钟长测；原始报告保留 `soak_30min_verified=false`，表示该时长未实测。

GUI 内可直接启动固定后端，也可独立启动后端后使用 Python 3.11 环境运行 `main.py --gateway --gui` 或 `main.py --gateway --consent-devices`。按 bo s s 要求，GUI 设备同意默认开启且不显示复选框，只有点击「启动语音」才开启设备，请戴好耳机；主程序不加载旧 PyAudio/torch/Whisper/YOLO，旧模式不再可选。

用户已确认真机听看说正常。2026-10-07 重启复验暴露了残影和过早断链导致 Worker/C++ 后端仍忙的问题：预览现完整重绘并清除上一帧，停止在后台执行，清理完成前禁止重启。手动停止与会话轮换使用相同的 8 秒接收尾窗，等待 `session.closed` 和连接关闭后再冷却 1 秒，通常约 9 秒；GUI 保持响应。真实后端合成帧/静音三次启停通过后，bo s s 又确认真实 GUI「这次完全正常」。这些是客户端兼容处理，不声称上游底层竞争已由源码修复。已受旧版本影响的后端先重启一次，再关闭重开 GUI 加载修复，无需重装依赖。

GUI 已按 bo s s 最新要求改为深蓝渐变科技风格，配青色高光与细网格。「调节参数」按钮在面板展开时持续高亮，收起时恢复暗色，并有悬停/按压反馈。摄像头按真实比例完整嵌入且无黑边；对话区域扩大、流式文本连续拼接，连接日志可单独折叠，顶部只保留一个状态提示。移除文字输入/发送、显示分辨率/缩放和旧模型开关。顶部「启动后端」异步调用固定启动器，校验版本、模型与端口，并收到明确就绪事件后才允许「启动语音」。停止后端先关闭音视频会话，再回收本窗口启动的三进程；退出窗口等待两者完成。外部已运行的后端仅探测和复用，不会由 GUI 终止。「后端路径设置」可选择 Demo / 引擎 / 仓库外模型目录及 SHA256 校验选项，路径仅保存在忽略的 `.cache/gui/backend.json`，也可通过 `JAC_DEMO_DIR` / `JAC_ENGINE_DIR` / `JAC_MODEL_DIR` 指定。启动语音前可调麦克风增益、采集帧率（5–10fps）、会话轮换（5–240 秒）、异常重连次数、音频设备、相机编号、本机 Gateway 与参考 WAV，运行中锁定。图像仍固定每秒上行最新一帧；Qwen 未接入，因此不提供假开关。`main.py --gui` 与 `python -m src.omni --gui` 使用同一 Gateway 路径，`--gateway` 参数仍兼容，无需新增 GUI 依赖。上一阶段测试共 138 项已分批验证通过（初次全套 136 通过，1 项写真实主目录的旧测试改为隔离目录后通过，再新增 1 项异常清理回归通过）；1440×880 与 1100×700 合成帧预览已检查，未新增真机听感验收。

VS Code 请选择项目解释器 `.cache/m0/venv/bin/python`，并带 `--gui` 启动。本机已配置忽略的 F5 项「J.A.C. · Gateway GUI」；右上角「运行 Python 文件」不使用 launch.json 的参数，系统 Python 缺少 numpy 时应切换环境。完整步骤见安装指南第 10 节。

### 文档与未来终端

[AGENTS.md](AGENTS.md) 是开发者契约；[CHANGELOG.md](CHANGELOG.md) 记录变更和迁移差距。已确认的方案 B 权威 DOCX 在本机 `brainstorming_projectPLAN/` 维护，整个目录不进 Git、不推送。Agent 已获准按确认的架构决策同步该本地目录。模板 `voices/silverwalf_voice.wav` 经 bo s s 明确允许公开推送；模型和实际测试录音录像不推送。`codinglog_by_awaqwq233/` 仍仅由 bo s s 手动维护且不进 Git。

未来可接入全屋摄像头、智能眼镜第一视角，以及 AR / Vision Pro 空间 GUI。
