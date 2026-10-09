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

**M0 is accepted with the 30-minute soak explicitly waived. M1 live listening/vision/speech, GUI preview and manual restart were accepted by the user on 2026-10-07.** The production Gateway SDK, SoundDevice capture/native playback and background 640×480 OpenCV capture are available through `main.py --gateway`, with a lightweight GUI via `--gui`. M2a independent Qwen read-only tool execution and Chinese file output are now verified. M2b now connects parallel VAD/Whisper transcription and explicit read-only system tasks to the Gateway CLI/GUI; the user confirmed successful live text results on 2026-10-08. Native task-result speech is now connected; ChromaDB and cloud tasks are still pending. This acceptance does not establish unlimited sessions or long-term stability.

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

The Gateway GUI uses a deep-blue gradient technology theme with cyan accents and subtle grids. The Parameters toggle has a persistent cyan selected state while the panel is open, a dark unselected state while closed, and distinct press/hover feedback. It has an original-aspect camera preview without black bars, a larger continuous reply transcript, separate collapsible connection logs, and a single status indicator at the top. Text input, send, display-resolution/zoom controls and obsolete model toggles are removed. Start Backend launches the pinned launcher asynchronously, validates repositories/models/ports and enables voice only after the explicit readiness event. Stopping Backend first closes the media session, then lets the launcher reclaim its three owned processes; closing the window waits for both. Already-running external backends are detected and reused without being terminated. Configure Demo/engine/external model directories and optional SHA256 checks through Backend Path Settings; these machine-specific paths are stored only in ignored `.cache/gui/backend.json` (or supplied with `JAC_DEMO_DIR` / `JAC_ENGINE_DIR` / `JAC_MODEL_DIR`). Before starting voice, adjust microphone gain, camera capture fps (5–10), session rotation (5–240 seconds), reconnect attempts, audio devices, camera index, local Gateway URL and reference WAV. Settings lock during operation. Image upload remains fixed at one latest frame per second. The local transcription/system-task switch and local model/brain paths are now available; settings still lock during operation. `main.py --gui` and `python -m src.omni --gui` use the same Gateway path; `--gateway` remains compatible. No new GUI dependency is required. The previous 138-test suite was verified in batches (the first full run had 136 passes and one test writing to the real home directory; its fixture was isolated and passed, alongside one newly added cleanup test). Synthetic screenshots were checked at 1440×880 and 1100×700 without accessing devices.

For VS Code, select the project interpreter `.cache/m0/venv/bin/python` and launch with `--gui`. This machine has an ignored F5 configuration named “J.A.C. · Gateway GUI” with an explicit interpreter; the editor’s Run Python File button does not apply launch.json arguments. See the setup guide, section 10.

### Documentation

- [AGENTS.md](AGENTS.md): architecture, engineering contract and migration status.
- [CHANGELOG.md](CHANGELOG.md): actual changes and appendices covering gaps, memory contracts, tests, privacy and operations.
- Architecture DOCX: the authoritative option B baseline is maintained locally in `brainstorming_projectPLAN/`, excluded from Git and future pushes.
- [Pinned official protocol](https://github.com/OpenBMB/MiniCPM-o-Demo/blob/47709a9210dfd71afa76c058e017fc8c4db5c8d2/docs/en/realtime-protocol-overview.md): Gateway events.
- [Pinned C++ engine](https://github.com/tc-mb/llama.cpp-omni/tree/873056743b74e1a4ce5dcf7290e2298428e214db): Metal inference.

### M2b: parallel transcription and system tasks

Production CLI/GUI now enable local transcription by default. The microphone still has one capture stream: the resampling worker copies 16 kHz audio into a bounded side queue, WebRTC VAD segments it on another thread, and a separate Python process runs multilingual Whisper small with CPU INT8 and two threads. The Qt/asyncio loop and audio callbacks never run recognition or Qwen. The worker opens no devices and receives only memory PCM; its standalone entry avoids loading OpenCV alongside PyAV.

`transcription.lock.json` pins the public Systran model revision and four SHA256 hashes. Models live outside the repository, defaulting to `~/.cache/jac/models/whisper-small`. `setup_new_computer.py --only gateway` now installs the pinned dependencies, prepares these resources using HTTPS mirror/official fallback and runs a CPU self-check. Downloads have time and throughput limits. `--skip-transcription-model` skips only the resource stage; disable transcription before starting voice if resources are absent. Runtime stays offline. No torch or MPS transcription model is loaded. The equivalent mel matrix product uses a checked NumPy summation kernel to avoid this machine's Accelerate floating-point flag issue.

Supported explicit commands include “查询电脑状态”, “查一下电池电量”, “查询本机当前时间”, “检查内存占用” and “生成一份系统状态报告”. Only `get_system_info` is exposed. Whole-sentence matching rejects negations, quoted/reported requests, ordinary chat and unlisted compound actions. Each utterance has a session generation and unique ID. Poor recognition, audio overlapping assistant playback, results older than 15 seconds, duplicate IDs and commands arriving while Qwen is busy do not execute. VAD waits 600 ms after speech; utterances over 12 seconds are rejected rather than split into tasks. Restarting/rotating a session cancels its unfinished tasks and prevents old results from publishing. Queue or transcription faults pause automatic tasks with an explicit message while the perception stream continues.

The GUI shows confirmed user transcripts, task progress and an “Open latest report” button. Reports are real UTF-8 Markdown files under ignored `output/m2/qwen/`. They include the recognized task and actual tool evidence; general conversation transcripts remain in bounded memory and runtime statistics contain no text or PCM. Completed tasks now use the same native voice/playback stream for short evidence-based feedback; full text and files remain available. `--no-transcription` keeps the listening/vision/speech path; `--whisper-model-dir` and `--brain-url` configure the local resources. SDK `GatewayClient` retains opt-in transcription; file replay enables it only with `--transcription`. A 60-second synthetic-file run through the real pinned Gateway, CPU Whisper and loaded Qwen completed one system query and one report, with successful shutdown. A preceding 24-second run correctly cancelled its unfinished task at rotation. These runs opened no devices and do not validate live recognition or long-term concurrency.

On 2026-10-08, the user reported successful live testing with normal text results. The live voice-task-to-text path is accepted based on that feedback. No additional per-command, timing or report-content measurements were recorded. Native task-result speech is now connected; reference-voice listening acceptance remains separate.


Native speech uses a registered extension of the pinned C++ patch, not an upstream protocol feature. The client requires `session.created.capabilities.task_speech=1` before capture when tasks are enabled. `input.task_speech` carries a bounded literal utterance alongside real audio/video. The existing duplex LLM thread evaluates its exact tokens and passes hidden states into native TTS/Token2Wav; status numbers come from actual tool evidence and are not regenerated by the perception model. Tagged text/audio and a final-window completion event are checked before claiming received task audio. Only one utterance waits for listening, user silence and idle playback; expiry or rotation skips speech while preserving the report. Cancellation flags also suppress already-queued task audio. File probes receive audio without playing it.

The 2026-10-08 battery test exposed an ordinary perception-model reply claiming 100% without a corresponding completed tool report; direct macOS and independent Qwen queries returned 80%. With transcription enabled, ordinary text/audio now waits in bounded memory for the user utterance to be classified. System requests discard that ordinary reply and use only verified task speech. Unmatched system wording, playback overlap or failed recognition explicitly reports that no query was executed. New user speech cancels queued ordinary playback; ordinary conversation resumes after a confirmed non-system utterance. This adds transcription latency to conversation. A transcription fault keeps perception input running but blocks unverified speech until restart. The GUI and report show the actual tool, arguments, query timestamp and raw evidence. The client fix needs a GUI restart; it does not require another backend rebuild.

On 2026-10-09, the user confirmed the live battery query and native result speech returned the correct 80%. This accepts that command's result loop, not voice-clone similarity or long-term stability. Short VAD candidates with less than 300 ms of voiced audio now drop silently and do not cancel queued replies. Valid candidates retain their full onset audio and activate reply cancellation after 300 ms. Genuine transcription-quality failures appear separately as “语音 · 未确认”, at most once per 10 seconds of consecutive failures; confirmed transcription resets the notice cooldown. Failed utterances still cannot execute tools or authorize guessed status speech, and recognition notices do not change an ongoing brain task's state.

Existing installations must rebuild the updated combined patch. `new_computer_download/update_native_backend.py --build` reads the saved GUI engine path, accepts only the pinned commit plus an approved old/current patch, and rolls back an application failure. Stop the backend before rebuilding, then reopen the GUI and start the backend again. Dependencies and official model hashes are unchanged.

### M2a: independent Qwen brain

With `qwen/qwen3.6-35b-a3b` loaded in LM Studio at `http://127.0.0.1:12345`, run `.cache/m0/venv/bin/python verify_toolcall.py` from the project root. It verifies the exact loaded instance, executes real time/battery/status queries and writes UTF-8 Markdown reports plus `verification.json` under ignored `output/m2/qwen/`. An explicit `--task` is supported but exposes only `get_system_info`; it does not enable computer actions or voice routing. No camera, microphone or speaker is opened.

LM Studio 0.4.8+ is required for the native model capability check and `reasoning_effort="none"`. The older `chat_template_kwargs` request still produced reasoning on this machine; the new setting produced zero reasoning tokens and real structured tool calls. See the [official release notes](https://lmstudio.ai/changelog/lmstudio/lmstudio-v0.4.8). Malformed arguments, unlisted tools, incomplete output, missing models, timeouts and cancellation fail explicitly. Each tool execution checks cancellation first. The agent loop returns its final answer once rather than making a second generation request. Ordinary chat still supports SSE; the independent task runner publishes a completed answer as a file.

The three native validation cases completed in 2.02 / 1.69 / 6.04 seconds, each with one real tool call. These are standalone Qwen measurements, not concurrent MiniCPM/Qwen or live voice performance. macOS memory reports use `vm_stat`'s actual page size and label active+wired pages separately from total memory usage. No new runtime dependency is needed; the installer checks the independent Qwen imports without network or device access.

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

**M0 已通过且 30 分钟长测免测；M1 听看说、GUI 预览与手动启停已于 2026-10-07 获用户真机验收。** 新生产 Gateway 客户端、SoundDevice 采集/原生播放和 640×480 后台视频采集已接入 `main.py --gateway`，`--gui` 打开轻量 GUI。M2a 独立 Qwen 只读工具调用与中文文件输出已实测通过；M2b 已接入 Gateway CLI/GUI 的并行转写和明确系统指令路由，并于 2026-10-08 获用户确认真机文字结果正常；原生结果播报已接入，ChromaDB 与云端任务尚未接入。旧 MiniCPM 客户端、:9060 启动器、轮询 judge 与传统运行入口已移除，所有入口统一使用 Gateway。当前验收不等于无限会话或长期稳定性验证。

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

GUI 已按 bo s s 最新要求改为深蓝渐变科技风格，配青色高光与细网格。「调节参数」按钮在面板展开时持续高亮，收起时恢复暗色，并有悬停/按压反馈。摄像头按真实比例完整嵌入且无黑边；对话区域扩大、流式文本连续拼接，连接日志可单独折叠，顶部只保留一个状态提示。移除文字输入/发送、显示分辨率/缩放和旧模型开关。顶部「启动后端」异步调用固定启动器，校验版本、模型与端口，并收到明确就绪事件后才允许「启动语音」。停止后端先关闭音视频会话，再回收本窗口启动的三进程；退出窗口等待两者完成。外部已运行的后端仅探测和复用，不会由 GUI 终止。「后端路径设置」可选择 Demo / 引擎 / 仓库外模型目录及 SHA256 校验选项，路径仅保存在忽略的 `.cache/gui/backend.json`，也可通过 `JAC_DEMO_DIR` / `JAC_ENGINE_DIR` / `JAC_MODEL_DIR` 指定。启动语音前可调麦克风增益、采集帧率（5–10fps）、会话轮换（5–240 秒）、异常重连次数、音频设备、相机编号、本机 Gateway 与参考 WAV，运行中锁定。图像仍固定每秒上行最新一帧；新增实际生效的“本地转写与系统任务”开关和本地模型/大脑路径，仍在运行中锁定。`main.py --gui` 与 `python -m src.omni --gui` 使用同一 Gateway 路径，`--gateway` 参数仍兼容，无需新增 GUI 依赖。上一阶段测试共 138 项已分批验证通过（初次全套 136 通过，1 项写真实主目录的旧测试改为隔离目录后通过，再新增 1 项异常清理回归通过）；1440×880 与 1100×700 合成帧预览已检查，未新增真机听感验收。

VS Code 请选择项目解释器 `.cache/m0/venv/bin/python`，并带 `--gui` 启动。本机已配置忽略的 F5 项「J.A.C. · Gateway GUI」；右上角「运行 Python 文件」不使用 launch.json 的参数，系统 Python 缺少 numpy 时应切换环境。完整步骤见安装指南第 10 节。

### M2b：并行转写与系统任务

生产 CLI/GUI 默认开启本地转写。仍只有一个麦克风输入流：重采样线程将 16k 音频复制到有界旁路，WebRTC VAD 在线程中切句，独立 Python 进程用 CPU INT8 / 2 线程运行多语言 Whisper small。Qt、asyncio 与音频回调不做转写或大脑推理；子进程不打开设备，PCM 只通过内存管道传递。独立入口隔离 OpenCV/PyAV，避免 macOS 运行库冲突。

`transcription.lock.json` 锁定官方转换仓库版本与四项 SHA256，模型默认放在仓库外 `~/.cache/jac/models/whisper-small`。安装器 `--only gateway` 已增加固定依赖、资源下载和 CPU 自检；HTTPS 国内镜像失败或持续过慢时回退官方，下载有时限。`--skip-transcription-model` 只跳过资源阶段，资源未就绪时须先关闭转写才能启动语音，运行期不联网下载。转写不加载 torch/MPS；等价 mel 矩阵求和增加有限值校验，规避本机 Accelerate 浮点状态误报。

首批指令为“查询电脑状态”“查一下电池电量”“查询本机当前时间”“检查内存占用”“生成一份系统状态报告”。仅开放 `get_system_info`，完整句匹配拒绝聊天、否定、转述和未开放的复合操作。句子按会话代次/编号去重；低置信度、与助手播放重叠、超过 15 秒的转写或大脑忙时的新增指令不执行。VAD 在句末静音 600ms 后提交，超过 12 秒的长句直接拒绝，不拆成多个任务。停止/重连取消旧任务及发布；积压/转写异常明确暂停任务和未核验播报，感知上行继续运行。

GUI 独立显示用户原话、任务进度，完成后可点“打开最新报告”。真实 UTF-8 报告保存在忽略的 `output/m2/qwen/`，包含该任务的转写及工具证据；一般对话转写仅保留有界内存，运行统计不含正文/PCM。任务完成后由同一原生音色/播放流播报实际工具证据短句；综合报告播报完成提示，完整文字与文件继续保留。启动前可关闭“本地转写与系统任务”或指定本地目录/大脑地址；CLI 对应 `--no-transcription` / `--whisper-model-dir` / `--brain-url`。SDK 默认不启用旁路，文件回放需显式 `--transcription`。

固定后端、CPU Whisper、已加载 Qwen 的 60 秒合成文件验证完成唯一系统查询和报告，关闭清理成功；前一段 24 秒验证在会话结束取消未完成任务。该合成文件验证未打开设备，不替代真机识别或长时并行验收。

2026-10-08，bo s s 反馈“测试成功，能正常输出文字结果”，真机语音任务到文字结果的路径按用户反馈验收通过；未新增具体指令、次数、时长、延迟或报告内容核对记录。原生任务结果播报现已接入，参考音色听感仍单独确认。


原生播报使用已登记 C++ 补丁扩展，固定上游原协议没有此功能。任务开启时须在采集前确认 `session.created.capabilities.task_speech=1`，通过 `input.task_speech` 把有界字面短句附在真实音视频块上，复用同一 duplex LLM/TTS/Token2Wav。单项数字来自实际工具证据；综合报告只播报完成提示，不重新推理数字。带任务标识的文本、非静音 PCM 和末窗口样本计数必须一致。GUI“结果音频已接收”表示传输确认；文件探针只收不播放，真机听感单独验收。仅一个待播报，等待聆听、用户静音和播放空闲；等待过期或会话即将轮换时跳过播报，停止/重连使已排队任务音频也失效，文字报告仍可查看。

2026-10-08 电池测试暴露普通感知回复在没有对应已完成工具报告时自称 100%；直接 macOS 与独立 Qwen 查询均返回 80%。启用转写时，普通文字/音频现先在有界内存等待用户句子判定；系统请求丢弃普通抢答，只通过已核验任务播报返回结果。未匹配的系统说法、播放重叠或识别失败明确提示未查询。新用户语音取消排队的普通播放，确认下一句为普通聊天后恢复；普通对话增加转写等待。转写故障保留感知上行，但暂停未核验播报直至重启。GUI 和报告显示真实工具名、参数、查询时间及原始证据。这次客户端修复只需重开 GUI，无需再次编译后端。

2026-10-09，bo s s 确认电池真机查询及原生播报正确返回 80%，该指令结果闭环按用户反馈通过；不据此认定音色克隆相似度或长期稳定性通过。累计有声不足 300ms 的 VAD 短片段现静默丢弃，不取消排队回复；有效片段保留句首完整音频，在达到 300ms 后才激活回复取消。真正的转写质量失败独立显示“语音 · 未确认”，连续失败十秒最多一次，成功转写后重置提示间隔。失败语音仍不能执行工具或放行状态猜测，语音提示不会改变在途大脑任务状态。

旧安装需重新编译更新后的组合补丁；`new_computer_download/update_native_backend.py --build` 默认读取已保存的 GUI 引擎路径，只接受固定 commit 的干净源码或登记补丁，陌生源码拒绝覆盖、应用失败回滚。先停止后端，再更新构建、重开 GUI 并启动后端；依赖版本与官方模型 SHA256 不变。

### M2a：独立 Qwen 大脑

在 LM Studio 加载精确实例 `qwen/qwen3.6-35b-a3b` 并启动 `http://127.0.0.1:12345` 后，从项目根运行 `.cache/m0/venv/bin/python verify_toolcall.py`。脚本核对已加载实例，真实查询时间、电池和系统状态，生成 UTF-8 Markdown 报告及 `verification.json`，默认保存在已忽略的 `output/m2/qwen/`。支持显式 `--task`，但仅开放 `get_system_info`；该脚本是独立终端验收入口，生产 GUI/语音路由由上述 M2b 提供。脚本不打开摄像头、麦克风或扬声器。

需要 LM Studio 0.4.8+。本机旧 `chat_template_kwargs` 仍产生思考，现统一使用实测有效的 `reasoning_effort="none"`；不把思考正文恢复为答案，不回退其他模型。畸形参数、任务白名单外工具、截断响应、未加载模型、超时与取消明确失败。每次执行工具前检查取消；最终回答直接交付一次，不重复请求。普通聊天仍支持 SSE，独立任务完成后发布报告文件。

真实三项验证各调用一次工具，耗时 2.02 / 1.69 / 6.04 秒；它们只代表独立 Qwen，不代表双模型并行或语音升级延迟。内存查询改用 `vm_stat` 实际页大小，明确活跃与有线页合计不等于完整占用。复用已有 httpx，无新增依赖；安装自检已加入大脑/任务模块。M2b 已接入上述旁路与路由，真机文字结果已获用户确认；原生任务结果播报现已接入。

### 文档与未来终端

[AGENTS.md](AGENTS.md) 是开发者契约；[CHANGELOG.md](CHANGELOG.md) 记录变更和迁移差距。已确认的方案 B 权威 DOCX 在本机 `brainstorming_projectPLAN/` 维护，整个目录不进 Git、不推送。Agent 已获准按确认的架构决策同步该本地目录。模板 `voices/silverwalf_voice.wav` 经 bo s s 明确允许公开推送；模型和实际测试录音录像不推送。`codinglog_by_awaqwq233/` 仍仅由 bo s s 手动维护且不进 Git。

未来可接入全屋摄像头、智能眼镜第一视角，以及 AR / Vision Pro 空间 GUI。
