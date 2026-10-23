# J.A.C. — Just A Code

A local-first multimodal Proactive AI Butler inspired by JARVIS.

[中文说明](#中文说明)

## English

J.A.C. aims to perceive its surroundings, plan ahead, warn of hazards and perform tasks proactively. Smart glasses and MR are optional terminals; the core product is the AI system and service framework. Development primarily targets macOS on Apple Silicon.

### Architecture and current status

**Reliability follow-up (2026-10-22):** the registered native patch now pairs forced-listen input with its queued prefill/decode step, preventing persistent input lag and queue blockage, and removes a duplicate TTS audio-BOS condition. One short two-turn file replay continued and expanded the prior answer; longer-interval follow-ups still produced silence, and other runs missed a first answer or misread visual details. A temporary 16384-context experiment did not reliably restore replies, so production remains at 4096. The user subsequently confirmed that the updated GUI answered the first question and continued the immediate follow-up. This accepts that live short exchange, not general or long-interval context reliability. `verify_native_reliability.py` checks 64 consecutive forced-listen steps, literal speech ownership/sample counts and session closure without devices; generated test audio is saved only with `--save-generated-audio`. Existing installations must rebuild the current combined patch. The user has accepted both fixed speech samples as complete, without obvious pauses, and close to the reference voice. Four 240-second synthetic production sessions completed eight real queries/reports/native speech receipts and closed normally, with no cleanup failures or simulated playback gaps. This is a 16-minute fixed-workload check, not all-day or live-device acceptance; details are in CHANGELOG.

**Live-feedback fixes (2026-10-22):** CPU Whisper now uses a small Chinese/English vocabulary hint, including Python and Lightroom, without command examples, text substitutions or lower confidence thresholds. Neutral short follow-ups can receive two matching acoustic rechecks, and the UI reports an accepted utterance with no native reply after 12 seconds. Native two-turn replay still produced silence or an incomplete acknowledgment despite correct transcription; prompt experiments did not reliably help and were reverted. Contextual follow-up remains unresolved. Lightroom / Lightroom Classic now targets the fixed local Adobe Lightroom Classic application. Explicit local-app requests cannot fall through to website search; explicit websites remain supported. Native playback adds a bounded 0.8-second prebuffer and resume-gap statistics, preserving PCM and cancellation. This adds initial waiting and cannot compensate for sustained slow generation. Reference/cache checks passed, but generated-audio recognition still did not recover the complete task sentence; speech completeness, voice similarity and live smoothness remain unaccepted. Native task speech now preserves in-flight TTS state after literal text is consumed and during LLM context rollover. It requires rebuilding the registered backend patch on existing installations; package versions and model files are unchanged.

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

**Late-stage plan approved on 2026-10-11; not implemented:** a local to-do list and proactive reminders will follow the existing core migrations. MiniCPM will identify tasks, owners, intended executors, current activities and timing; the local application will validate and persist updates to JSON and refresh the GUI. Ambiguous entries await clarification, inferred activities expire, and completion requires user feedback or actual execution evidence. Task changes are saved promptly, independently of the five-summary vector-index batch. Normal silent reviews default to every 45 minutes, configurable to 30–60 minutes or longer; explicit reminder times and significant task events are handled separately. Review frequency is not notification frequency: reminders respect activity and do-not-disturb settings, support snooze/cancel, and suppress duplicates. A validated structured-event channel from MiniCPM and its integration with the pinned backend must first be verified; ordinary model replies are not reliable write instructions. No automatic full-list cloud upload or Git publication, no promise of on-time reminders while the app is closed or the computer sleeps, and no implementation or installation changes are included in this plan. Optional wearables are not prerequisites. See [AGENTS.md](AGENTS.md) and CHANGELOG Appendix A6.

**M0 is accepted with the 30-minute soak explicitly waived. M1 live listening/vision/speech, GUI preview and manual restart were accepted by the user on 2026-10-07.** The production Gateway SDK, SoundDevice capture/native playback and background 640×480 OpenCV capture are available through `main.py --gateway`, with a lightweight GUI via `--gui`. M2a independent Qwen read-only tool execution and Chinese file output are now verified. M2b now connects parallel VAD/Whisper transcription and explicit read-only system tasks and web searches to the Gateway CLI/GUI; the user confirmed successful live text results on 2026-10-08. Native task-result speech is now connected; ChromaDB and cloud tasks are still pending. This acceptance does not establish unlimited sessions or long-term stability.

The pinned Gateway limits video sessions to 300 seconds and audio sessions to 600 seconds. M1 rotates video sessions after 240 input seconds, closes/releases devices and reconnects with the reference voice and bounded assistant history plus supplied confirmed context. Capture visibly pauses during reconnection. It does not reconstruct untranscribed user speech or provide seamless audio across the gap.

### JACBrain independent prototype verified on 2026-10-18

The independent prototype uses official `deepseek-harness-sdk==0.2.1rc2`, its matching runtime and a J.A.C. plugin with the exact local `qwen/qwen3.6-35b-a3b`. `agent.lock.json` pins the official source tag, platform wheels and plugin SHA256. Production voice/GUI now uses the unified JACBrain task interface described below; desktop permissions remain bounded.

`verify_agent.py` accepts an explicit workspace and two filenames, each at most 768 UTF-8 bytes. The Agent reads them, obtains a program-generated diff and submits a Chinese summary. The controller verifies tool evidence, unchanged input hashes, successful runtime shutdown and cancellation before atomically publishing the report and evidence. Default shell, filesystem and cloud tools are disabled. Tasks run serially with 120-second default deadlines, at most 8 model requests / 8 tool calls, and bounded context/output. An explicit provider maps reasoning off to `reasoning_effort="none"`; unexpected thinking output fails closed. The actual loaded model context is checked at startup, without changing models or loading settings.

Install through `setup_new_computer.py --only agent`; this first-stage installer supports macOS 14+ arm64 and Python 3.11. It uses hash-checked wheels, a domestic mirror with official PyPI fallback, and an isolated `.cache/agent/venv`. The plain JavaScript plugin loads directly into the bundled runtime, so no additional Node/pnpm build is needed. Session state, native extraction caches and reports remain under ignored `.cache/agent/` and `output/agent/`. `--inspect-session` restores history for inspection only, without resuming actions.

Three live Qwen cases (changed text, identical text and an instruction embedded in untrusted input) completed and restored their histories. This is a small functional sample, not a general success-rate claim. Production migration is implemented as described below. Live-device listening, long-running concurrent stability and automatic continuation after interruption remain separate, unfinished checks. The three model tiers, long-term memory plan and late-stage reminders are unchanged. See [installation and verification](new_computer_download/READMEfirst.md#17-jacbrain-agent-独立原型), [AGENTS](AGENTS.md) and CHANGELOG Appendix A7. Official upstream: [Python SDK at the pinned commit](https://github.com/deepseek-ai/deepseek-harness/blob/687f282e8625c6b8d11ff58211a90524be2e4317/python/sdk/README.md).

### Production JACBrain tasks on 2026-10-20

`TaskPipeline` now uses `JACBrainTaskRunner`. The official SDK runs only inside `.cache/agent/venv`, with its own process and per-task home. System/web queries use bounded Harness tool rounds. App/URL/filename-search and website-selection proposals are validated as a whole, registered once, then paused through the official pre-step hook (`blocked` is the expected proposal terminal state). The existing executor performs the actual action after cancellation and scope checks; model text never proves completion. Website confirmation/cache paths remain deterministic under the same interface. Duplicate, expired and cancelled requests retain their existing restrictions.

`--only gateway` now also installs the separate Agent environment; existing installations can run `--only agent`. Voice startup checks versions and both plugin hashes before capture, without downloading or falling back to the old model runner. Gateway Python dependencies, model hashes and upstream commits remain pinned. Native regression found a cross-input UTF-8 truncation bug; the registered combined patch now retains partial characters until task text ends. Existing backends must run the update/build step in the setup guide. Production reports stay in `output/m2/qwen/`; task/session/call evidence stays in ignored `.cache/agent/runs/`. The independent text-comparison prototype still uses `output/agent/`. Web context is reduced to bounded source passages with original IDs and provenance; answers can cite only passages actually shown to the model.

The production text probe is `verify_brain_tasks.py --task "查一下电池电量"`, using `.cache/m0/venv/bin/python`; explicit local-action commands actually execute. Source/generation, candidate confirmation, cancellation and real SDK integration have automated regressions. Synthetic-file tests through the real Whisper, Qwen and MiniCPM backend check unique execution, reports and native result audio without opening devices. Short runs do not establish live listening quality or long-term performance; the measured whole-machine swap increase is recorded in CHANGELOG. The next expansion is a separately scoped multi-step tool domain, followed by file editing, code execution and desktop capabilities; none is enabled by this migration.

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

Running `main.py` without arguments now opens the GUI. When the installed project environment exists, the script selects `.cache/m0/venv` before importing dependencies, so VS Code’s Run Python File button also works with a previously selected system/legacy interpreter. For F5 debugging, keep the explicit project interpreter to preserve the debugger. Console and file replay arguments remain explicit. Keep backend checkouts, build outputs and voice frontend resources in a persistent directory, never `/tmp`; an incomplete Git checkout now produces a recovery message in the GUI. See the setup guide, section 10.

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

The 2026-10-08 battery test exposed an ordinary perception-model reply claiming 100% without a corresponding completed tool report; direct macOS and independent Qwen queries returned 80%. With transcription enabled, ordinary text/audio now waits in bounded memory for the user utterance to be classified. System requests discard that ordinary reply and use only verified task speech. Unmatched system wording, playback overlap or failed recognition explicitly reports that no query was executed. New user speech cancels queued ordinary playback; ordinary conversation resumes after a confirmed non-system utterance. This adds transcription latency to conversation. A transcription fault keeps perception input running but blocks unverified speech until restart. The GUI and report show the actual tool, arguments, query timestamp and raw evidence. That October 8 client change needs a GUI restart; the October 20 native UTF-8 fix additionally requires the backend update/build described above.

On 2026-10-09, the user confirmed the live battery query and native result speech returned the correct 80%. This accepts that command's result loop, not voice-clone similarity or long-term stability. Short VAD candidates with less than 300 ms of voiced audio now drop silently and do not cancel queued replies. Valid candidates retain their full onset audio and activate reply cancellation after 300 ms. Genuine transcription-quality failures appear separately as “语音 · 未确认”, at most once per 10 seconds of consecutive failures; confirmed transcription resets the notice cooldown. Failed utterances still cannot execute tools or authorize guessed status speech, and recognition notices do not change an ongoing brain task's state.

Existing installations must rebuild the updated combined patch. `new_computer_download/update_native_backend.py --build` reads the saved GUI engine path, accepts only the pinned commit plus an approved old/current patch, and rolls back an application failure. Stop the backend before rebuilding, then reopen the GUI and start the backend again. Dependencies and official model hashes are unchanged.

### M2a: independent Qwen brain

With `qwen/qwen3.6-35b-a3b` loaded in LM Studio at `http://127.0.0.1:12345`, run `.cache/m0/venv/bin/python verify_toolcall.py` from the project root. It verifies the exact loaded instance, executes real time/battery/status queries and writes UTF-8 Markdown reports plus `verification.json` under ignored `output/m2/qwen/`. An explicit `--task` is supported but exposes only `get_system_info`; it does not enable computer actions or voice routing. No camera, microphone or speaker is opened.

LM Studio 0.4.8+ is required for the native model capability check and `reasoning_effort="none"`. The older `chat_template_kwargs` request still produced reasoning on this machine; the new setting produced zero reasoning tokens and real structured tool calls. See the [official release notes](https://lmstudio.ai/changelog/lmstudio/lmstudio-v0.4.8). Malformed arguments, unlisted tools, incomplete output, missing models, timeouts and cancellation fail explicitly. Each tool execution checks cancellation first. The agent loop returns its final answer once rather than making a second generation request. Ordinary chat still supports SSE; the independent task runner publishes a completed answer as a file.

The three native validation cases completed in 2.02 / 1.69 / 6.04 seconds, each with one real tool call. These are standalone Qwen measurements, not concurrent MiniCPM/Qwen or live voice performance. macOS memory reports use `vm_stat`'s actual page size and label active+wired pages separately from total memory usage. No new runtime dependency is needed; the installer checks the independent Qwen imports without network or device access.

### Web search (2026-10-09)

Say “上网搜索上海明天的天气” or “联网查询 Python asyncio 官方文档” after starting voice with local transcription enabled. Confirmed city-weather questions can also route to search. The local Qwen receives only one search tool with the user's exact query; computer actions remain unavailable. Bing's China RSS search endpoint is tried first, then the fixed global endpoint on failure. This keyless endpoint is not a guaranteed public API; network blocks, verification pages or format changes fail explicitly.

Up to five links and three static source pages are retrieved with existing httpx and Python's standard library. Every URL, IPv4 DNS result and redirect must be public; the connection uses a validated IP with the original Host/TLS SNI and certificate verification. No scripts, browser login, cookies or environment proxies are used. Network work runs in the brain thread with a 25-second deadline and cancellation; audio/video input continues.

Weather directives accept 查寻 and 让我上网查一下 without leaving command fragments in the query. Both city-first and date-first questions search the city weather topic (e.g. 明天海口的天气 → 海口天气); the requested date remains available for passage selection. Missing cities trigger a local native voice clarification without Qwen, network access or a report. Malformed phrases such as 插一下天气 never authorize search. Results for other cities, dictionaries and unrelated novels are filtered before opening source pages; irrelevant China results trigger the fixed global fallback. Original and effective search terms are both shown and saved. Empty selections produce a no_answer record and speech explicitly says no verified answer was obtained. Qwen selects numbered passages from actual source pages, with target weather dates and specific English terms filtering unrelated passages. The program checks the selection and delivers those source excerpts, preserving their numbers and dates instead of accepting freely generated facts. This first version provides excerpts rather than free paraphrases or translations. Excerpt matching cannot establish source truth, freshness or relevance. Unread pages remain marked as snippets; retrieval time is not publication time. The GUI shows real search evidence and source links; the local report preserves full evidence and native speech reads a bounded excerpt. Only the query goes to the search service, with source-page GET requests afterward; conversation history, audio/video and the reference voice stay local.

Run `.cache/m0/venv/bin/python verify_web_search.py --query "上海明天天气"` from the project root for the independent real Qwen/file check, or add `--search-only` to test only search and source access. These checks use no devices. Reports go to ignored `output/m2/search/`; production reports remain in `output/m2/qwen/`. The independent web probe needs no backend or API key. Production voice also requires the Agent environment and native UTF-8 patch update described above.

Short visual questions are explicitly covered by the perception prompt. Rejected visual candidates of at most four seconds can be decoded twice more with 0.3/0.6 seconds of leading silence, preserving every original sample and using no example prompt. Both decodes must pass the unchanged confidence checks and agree on the same neutral visual question; system/web topics, disagreement, overlap, expiration or a new utterance cannot use this recovery. It adds CPU waiting only for eligible failures. Superseded ASR results no longer display old transcripts, rejection notices or submit tools. Rejection identifies the utterance and reason. A real CPU Whisper noisy short-question case recovered after an initial rejection; a 65-second file replay with a populated synthetic frame returned the expected blue-interface description and 2.64 seconds of native audio for the short question. The earlier blank-frame short/long replays stayed silent; synthetic checks do not establish the exact acoustic cause of the user's earlier live failure or replace live confirmation.

### M2c: bounded local computer tasks (updated 2026-10-17)

The production voice path now supports one explicit local action per utterance: opening a listed application, opening an explicit HTTP(S) URL, or searching filenames inside a specified directory. Examples: “打开计算器”, “打开Safari”, “打开Python官网”, and “在下载目录查找文件名包含报告的文件”. The macOS application list is Safari, Calendar, Notes, Calculator, TextEdit and Adobe Lightroom Classic; Lightroom aliases target Classic. Explicit local wording such as “打开电脑上的Lightroom软件” stays local. Known website names are 百度, GitHub, Python, and B站 / 哔哩哔哩 / bilibili. They accept 网页 / 网站 / 官网 / 官方网站, including “打开B站” and “打开B站网页”, with traditional Chinese variants. Literal URLs retain their punctuation and path/query characters. Unlisted names now enter evidence-based website resolution; Qwen selects an observed candidate ID and never invents a URL. Explicit application requests never fall back to opening a website. File searches without a directory ask for the complete request. Paths and filename keywords retain the confirmed transcript; script conversion and homophone correction are not applied to these values.

Qwen receives only one authorized tool with exact argument enums. Changed parameters, multiple calls, negations, quoted requests and unsupported compound actions are rejected before execution. Results and native speech use actual tool evidence, without another model-generated success claim. The GUI distinguishes a submitted launch request, a detected running macOS process, partial search results and failure. Browser handoff does **not** verify page loading; a running process does **not** verify a foreground window.

Searches inspect filenames only, under an explicit non-hidden subdirectory of the user's home. They skip hidden entries and symbolic links, with limits of 20 results, 60,000 entries, 20 nested levels and four seconds of scanning; a separate process has a seven-second hard wait limit. POSIX directory traversal uses `O_NOFOLLOW`. Partial scans cannot establish that a file is absent. Stop/reconnect invalidates pending work and report publication; launching is checked under the same session lock. Already submitted operating-system actions cannot be undone by cancellation. Reports remain under ignored `output/m2/qwen/`.

The local-action feature adds no dependencies or models. The current production entry also requires the Agent installation and native UTF-8 backend update described above; then restart the GUI and enable “本地转写与电脑任务”. `verify_local_tasks.py --task` performs an explicitly supplied real action without capture devices. Windows/Linux retain portable filename search and URL handoff paths, but application launching in this first stage is macOS-only; those platforms have not been tested. Arbitrary commands, desktop clicking/typing, file modification, memory and cloud escalation remain outside this stage. See setup guide section 15 for verification boundaries and commands.

Website resolution (2026-10-17): say “打开小众软件” or another site name without registering it first. Existing app names and explicit URLs keep their paths. The resolver searches domestic-first Bing RSS (global fallback on request failure or unrelated results), groups candidates by host, reads at most three homepages and checks names, titles, visible text, declared canonical URLs and site sections. Qwen selects only observed IDs. Automatic opening requires exactly one eligible HTTPS candidate and model agreement; search position, a claimed probability or a page calling itself official is not sufficient. These signals are heuristics, not proof of ownership. Verification pages, conflicting identity, multiple matches or HTTP-only candidates require an explicit choice. Website identity and reachability are separate; browser handoff still does not confirm loading.

The GUI shows up to three candidate buttons and a real report. Say “打开第一个网站” or click a candidate within 60 seconds; IDs are bound to the current task/session, consumed once and invalidated by another task, stop or reconnect. “取消网站选择” dismisses the prompt. The next choice retains normal ASR quality and overlap checks. All network/model/cache work stays on the task thread. A resolved task has a 60-second total budget and the existing 25-second search limit; cancellation is checked while waiting and again before dispatch.

Dynamic mappings live in ignored `.cache/sites/resolutions.json` (up to 128 entries, seven days for evidence-based choices and 30 days for user-confirmed choices). Cache hits recheck the homepage and must keep the same host; failed checks trigger resolution again. Use “重新查找小众软件网站” to refresh, “忘记小众软件的网站地址” to remove one mapping, or “清除网站缓存” / the GUI button to clear dynamic mappings. Built-in common-site shortcuts remain in source. No conversation history, audio or video is sent to search; only the site query and bounded page requests leave the machine. Search/selection evidence remains in local reports, not runtime counters.

`verify_website.py --name 小众软件` performs real search and Qwen selection without opening a browser. Add `--open` to execute a unique result once; ambiguity does not silently choose. Probe caches are isolated under its output directory. SearXNG and DDGS were reviewed as search providers, but neither replaces the task/session/identity layer; this implementation reuses the existing httpx transport and adds no service, model, dependency or backend patch.

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

**末期计划，尚未实现（2026-10-11 已确认）**：现有核心迁移完成后，最后接入本地待办清单与主动提醒。MiniCPM 判断任务、负责人、拟交给谁执行、当前活动与时间，本地程序校验后及时保存 JSON 并刷新 GUI；歧义待确认，推测活动会过期，完成须有用户反馈或真实执行证据。待办落盘不等待五条摘要的向量索引批次。普通静默巡检默认每 45 分钟，可设 30–60 分钟或更长；明确提醒时间和关键任务事件单独处理。巡检频率不等于提醒频率，提醒结合当前活动和免打扰，支持延后/取消并去重。实施前须验证 MiniCPM 独立结构化事件通道与固定后端接线，普通模型回复不能直接作为写入指令。清单不整份自动上传云端、不进入 Git；不承诺程序退出或电脑休眠时准时提醒。本次不改实现或安装方式，可选眼镜等外设不作为前置条件。详见 [AGENTS.md](AGENTS.md) 与 CHANGELOG 附 A6。

### JACBrain Agent 独立原型已验证（2026-10-18）

独立原型已接通官方 `deepseek-harness-sdk==0.2.1rc2`、同版本运行时和 J.A.C. 专用插件，继续使用本机精确 `qwen/qwen3.6-35b-a3b`。`agent.lock.json` 锁定官方源码标签、平台 wheel 和插件 SHA256。生产语音/GUI 已使用下述统一 JACBrain 任务接口，桌面权限保持原边界。

`verify_agent.py` 接收显式工作目录和两个文件名，每份最多 768 UTF-8 字节。Agent 实际读取、取得程序生成的逐行差异、提交中文摘要；控制器复核工具证据、输入哈希、运行时正常落盘关闭和取消状态后，原子发布报告及证据。禁用默认 shell、文件系统及云端工具。单任务串行，默认 120 秒、最多 8 次模型请求/8 次工具调用，并限制上下文与输出。专用 provider 将关闭思考映射为 `reasoning_effort="none"`，意外思考输出明确失败；启动时读取实际加载容量，不自动换模型或改加载参数。

安装入口为 `setup_new_computer.py --only agent`，首版支持 macOS 14+ arm64 / Python 3.11，全部 wheel 校验 SHA256，国内镜像失败回退官方源。环境独立放入 `.cache/agent/venv`；纯 JavaScript 插件由捆绑运行时直接加载，无需额外 Node/pnpm 构建。状态、原生组件缓存与报告均在忽略的 `.cache/agent/`、`output/agent/`。`--inspect-session` 仅恢复历史供检查，不重放或继续旧动作。

真实 Qwen 的差异文本、相同文本和夹带指令的文本三组任务已完成，历史恢复均通过；小样本不代表通用成功率。现有语音任务生产迁移已实现，详见下节；真人设备听感、长时同跑稳定性和中断后自动续跑仍未完成验收。三层模型、长期记忆及末期提醒顺序保持。完整命令见[安装指南第十七节](new_computer_download/READMEfirst.md#17-jacbrain-agent-独立原型)，状态与边界见 AGENTS 第十节、CHANGELOG 附 A7。

### JACBrain 生产任务迁移（2026-10-20）

`TaskPipeline` 已改用 `JACBrainTaskRunner`。官方 SDK 只在 `.cache/agent/venv` 独立进程和任务 home 中运行。系统/联网查询使用有界 Harness 工具回合；应用、网址、文件名搜索与站名候选选择先整批核验并登记一次，再由官方 pre-step 钩子暂停（提议终态 `blocked` 是预期状态），原执行器复核取消和授权后才实际执行。业务结果取自工具证据。网站确认/缓存路径继续在同一接口中确定性执行，重复、过期、撤销边界保留。

`--only gateway` 现在同时安装独立 Agent 环境；已有机器可运行 `--only agent`。启动语音前检查固定版本及两个插件哈希，不在运行时下载，也不回落旧模型执行器。Gateway Python 依赖、模型哈希与上游 commit 保持不变。原生回归发现跨输入块 UTF-8 截断，已修正登记组合补丁以保留尾字节到任务文字结束；旧后端须按安装指南更新并重新编译。生产报告仍在 `output/m2/qwen/`，任务/会话/调用证据在忽略的 `.cache/agent/runs/`；独立文本比较继续使用 `output/agent/`。联网上下文只保留有界原始段落及编号、地址、日期和抓取时间，答案不得猜选未送入模型的段落。

生产文字探针为 `.cache/m0/venv/bin/python verify_brain_tasks.py --task "查一下电池电量"`；明确本地动作会实际执行。自动回归覆盖来源/代次、候选确认、取消及真实 SDK；真实 Whisper、Qwen、MiniCPM 的合成文件验证核对唯一执行、报告与原生结果音频，不打开设备。短测不能证明真人听感或长时稳定性，本机测得的整机 swap 增长在 CHANGELOG 如实记录。下一步在独立权限域扩展多步骤工具组合，再逐项开放文件编辑、代码执行与桌面能力，本次未开放这些能力。

### 当前实现状态

**可靠性追加修复（2026-10-22）**：完整组合补丁修复强制聆听跳过异步配对处理造成的输入滞后/堵塞，并移除双工 TTS 重复的 audio BOS。一次短间隔两轮合成回放已承接前文展开回答；相隔约 35 秒仍沉默，其他回放也出现首问无输出及画面细节错误。临时 16384 上下文未稳定恢复回答，生产保持 4096。bo s s 随后在更新后的 GUI 确认第一问正常、短追问能接续展开；这接受该组真人短对话，不等于较长间隔或所有普通对话都稳定。新增 `verify_native_reliability.py` 在不开设备时验证连续 64 次强制聆听、字面播报归属/样本数及关闭，仅显式 `--save-generated-audio` 保存固定测试句。已有安装需更新并重新编译完整补丁；bo s s 已确认两条固定测试句内容完整、无明显停顿、音色接近参考。四次 240 秒合成生产会话完成 8 次真实查询/报告/原生播报接收确认，正常关闭且无清理失败、模拟续播空档；仅为 16 分钟固定负载验证，不等于全天或真机综合交互通过。详情见 CHANGELOG，以下真人反馈条目保留修复前证据。

**真人反馈修复（2026-10-22）**：Whisper small 增加含 Python、Lightroom 的有限中英词汇提示，不含执行句示例、不做同音猜改、不降低质量门槛。无工具语义的短展开追问可经两次一致的声学复核恢复；识别放行后 12 秒没有普通回复时明确显示提示。原生两轮回放仍出现转写正确但沉默或只答简短应答，提示实验无稳定改善已撤回；上下文追问尚未修复。原生播放增加 0.8 秒预缓冲和续播空档统计，保留 PCM 与取消边界，首音增加等待；持续供音不足仍可能停顿。原参考及条件缓存校验通过，但生成音频离线复核仍未还原完整任务句，播报完整性、克隆相似度和真人流畅度仍未验收。原生任务播报另修复字面文本送完后提前重置 TTS 尾段及滑窗误清理的问题，已有安装须更新登记补丁并重新编译；包版本和模型文件不变。

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

GUI 已按 bo s s 最新要求改为深蓝渐变科技风格，配青色高光与细网格。「调节参数」按钮在面板展开时持续高亮，收起时恢复暗色，并有悬停/按压反馈。摄像头按真实比例完整嵌入且无黑边；对话区域扩大、流式文本连续拼接，连接日志可单独折叠，顶部只保留一个状态提示。移除文字输入/发送、显示分辨率/缩放和旧模型开关。顶部「启动后端」异步调用固定启动器，校验版本、模型与端口，并收到明确就绪事件后才允许「启动语音」。停止后端先关闭音视频会话，再回收本窗口启动的三进程；退出窗口等待两者完成。外部已运行的后端仅探测和复用，不会由 GUI 终止。「后端路径设置」可选择 Demo / 引擎 / 仓库外模型目录及 SHA256 校验选项，路径仅保存在忽略的 `.cache/gui/backend.json`，也可通过 `JAC_DEMO_DIR` / `JAC_ENGINE_DIR` / `JAC_MODEL_DIR` 指定。启动语音前可调麦克风增益、采集帧率（5–10fps）、会话轮换（5–240 秒）、异常重连次数、音频设备、相机编号、本机 Gateway 与参考 WAV，运行中锁定。图像仍固定每秒上行最新一帧；新增实际生效的“本地转写与电脑任务”开关和本地模型/大脑路径，仍在运行中锁定。`main.py --gui` 与 `python -m src.omni --gui` 使用同一 Gateway 路径，`--gateway` 参数仍兼容，无需新增 GUI 依赖。上一阶段测试共 138 项已分批验证通过（初次全套 136 通过，1 项写真实主目录的旧测试改为隔离目录后通过，再新增 1 项异常清理回归通过）；1440×880 与 1100×700 合成帧预览已检查，未新增真机听感验收。

直接运行无参数 `main.py` 现在默认打开 GUI；检测到已安装的项目环境时，在导入依赖前切换到 `.cache/m0/venv`，VS Code 右上角「运行 Python 文件」误选系统/旧解释器时也可使用。F5 调试仍应明确选择项目解释器，以保留调试连接；终端和文件回放继续使用显式参数。后端源码、编译产物和音色前端须保存在长期目录，不使用 `/tmp`；Git 仓库不完整时，GUI 会保留具体原因和恢复提示。完整步骤见安装指南第 10 节。

### M2b：并行转写与系统任务

生产 CLI/GUI 默认开启本地转写。仍只有一个麦克风输入流：重采样线程将 16k 音频复制到有界旁路，WebRTC VAD 在线程中切句，独立 Python 进程用 CPU INT8 / 2 线程运行多语言 Whisper small。Qt、asyncio 与音频回调不做转写或大脑推理；子进程不打开设备，PCM 只通过内存管道传递。独立入口隔离 OpenCV/PyAV，避免 macOS 运行库冲突。

`transcription.lock.json` 锁定官方转换仓库版本与四项 SHA256，模型默认放在仓库外 `~/.cache/jac/models/whisper-small`。安装器 `--only gateway` 已增加固定依赖、资源下载和 CPU 自检；HTTPS 国内镜像失败或持续过慢时回退官方，下载有时限。`--skip-transcription-model` 只跳过资源阶段，资源未就绪时须先关闭转写才能启动语音，运行期不联网下载。转写不加载 torch/MPS；等价 mel 矩阵求和增加有限值校验，规避本机 Accelerate 浮点状态误报。

首批指令为“查询电脑状态”“查一下电池电量”“查询本机当前时间”“检查内存占用”“生成一份系统状态报告”。系统查询开放 `get_system_info`；联网查询单独开放 `search_web`（见下节），完整句匹配拒绝聊天、否定、转述和未开放的复合操作。句子按会话代次/编号去重；低置信度、与助手播放重叠、超过 15 秒的转写或大脑忙时的新增指令不执行。VAD 在句末静音 600ms 后提交，超过 12 秒的长句直接拒绝，不拆成多个任务。停止/重连取消旧任务及发布；积压/转写异常明确暂停任务和未核验播报，感知上行继续运行。

GUI 独立显示用户原话、任务进度，完成后可点“打开最新报告”。真实 UTF-8 报告保存在忽略的 `output/m2/qwen/`，包含该任务的转写及工具证据；一般对话转写仅保留有界内存，运行统计不含正文/PCM。任务完成后由同一原生音色/播放流播报实际工具证据短句；综合报告播报完成提示，完整文字与文件继续保留。启动前可关闭“本地转写与电脑任务”或指定本地目录/大脑地址；CLI 对应 `--no-transcription` / `--whisper-model-dir` / `--brain-url`。SDK 默认不启用旁路，文件回放需显式 `--transcription`。

固定后端、CPU Whisper、已加载 Qwen 的 60 秒合成文件验证完成唯一系统查询和报告，关闭清理成功；前一段 24 秒验证在会话结束取消未完成任务。该合成文件验证未打开设备，不替代真机识别或长时并行验收。

2026-10-08，bo s s 反馈“测试成功，能正常输出文字结果”，真机语音任务到文字结果的路径按用户反馈验收通过；未新增具体指令、次数、时长、延迟或报告内容核对记录。原生任务结果播报现已接入，参考音色听感仍单独确认。


原生播报使用已登记 C++ 补丁扩展，固定上游原协议没有此功能。任务开启时须在采集前确认 `session.created.capabilities.task_speech=1`，通过 `input.task_speech` 把有界字面短句附在真实音视频块上，复用同一 duplex LLM/TTS/Token2Wav。单项数字来自实际工具证据；综合报告只播报完成提示，不重新推理数字。带任务标识的文本、非静音 PCM 和末窗口样本计数必须一致。GUI“结果音频已接收”表示传输确认；文件探针只收不播放，真机听感单独验收。仅一个待播报，等待聆听、用户静音和播放空闲；等待过期或会话即将轮换时跳过播报，停止/重连使已排队任务音频也失效，文字报告仍可查看。

2026-10-08 电池测试暴露普通感知回复在没有对应已完成工具报告时自称 100%；直接 macOS 与独立 Qwen 查询均返回 80%。启用转写时，普通文字/音频现先在有界内存等待用户句子判定；系统请求丢弃普通抢答，只通过已核验任务播报返回结果。未匹配的系统说法、播放重叠或识别失败明确提示未查询。新用户语音取消排队的普通播放，确认下一句为普通聊天后恢复；普通对话增加转写等待。转写故障保留感知上行，但暂停未核验播报直至重启。GUI 和报告显示真实工具名、参数、查询时间及原始证据。10 月 8 日该项客户端修复只需重开 GUI；10 月 20 日新增的原生 UTF-8 修复还须按前文更新并编译后端。

2026-10-09，bo s s 确认电池真机查询及原生播报正确返回 80%，该指令结果闭环按用户反馈通过；不据此认定音色克隆相似度或长期稳定性通过。累计有声不足 300ms 的 VAD 短片段现静默丢弃，不取消排队回复；有效片段保留句首完整音频，在达到 300ms 后才激活回复取消。真正的转写质量失败独立显示“语音 · 未确认”，连续失败十秒最多一次，成功转写后重置提示间隔。失败语音仍不能执行工具或放行状态猜测，语音提示不会改变在途大脑任务状态。

旧安装需重新编译更新后的组合补丁；`new_computer_download/update_native_backend.py --build` 默认读取已保存的 GUI 引擎路径，只接受固定 commit 的干净源码或登记补丁，陌生源码拒绝覆盖、应用失败回滚。先停止后端，再更新构建、重开 GUI 并启动后端；依赖版本与官方模型 SHA256 不变。

### M2a：独立 Qwen 大脑

在 LM Studio 加载精确实例 `qwen/qwen3.6-35b-a3b` 并启动 `http://127.0.0.1:12345` 后，从项目根运行 `.cache/m0/venv/bin/python verify_toolcall.py`。脚本核对已加载实例，真实查询时间、电池和系统状态，生成 UTF-8 Markdown 报告及 `verification.json`，默认保存在已忽略的 `output/m2/qwen/`。支持显式 `--task`，但仅开放 `get_system_info`；该脚本是独立终端验收入口，生产 GUI/语音路由由上述 M2b 提供。脚本不打开摄像头、麦克风或扬声器。

需要 LM Studio 0.4.8+。本机旧 `chat_template_kwargs` 仍产生思考，现统一使用实测有效的 `reasoning_effort="none"`；不把思考正文恢复为答案，不回退其他模型。畸形参数、任务白名单外工具、截断响应、未加载模型、超时与取消明确失败。每次执行工具前检查取消；最终回答直接交付一次，不重复请求。普通聊天仍支持 SSE，独立任务完成后发布报告文件。

真实三项验证各调用一次工具，耗时 2.02 / 1.69 / 6.04 秒；它们只代表独立 Qwen，不代表双模型并行或语音升级延迟。内存查询改用 `vm_stat` 实际页大小，明确活跃与有线页合计不等于完整占用。复用已有 httpx，无新增依赖；安装自检已加入大脑/任务模块。M2b 已接入上述旁路与路由，真机文字结果已获用户确认；原生任务结果播报现已接入。

### M2c：受限本地电脑任务（2026-10-17 更新）

生产语音已接入三类单动作：“打开计算器”“打开Safari”“打开Python官网”“在下载目录查找文件名包含报告的文件”。应用白名单为 macOS Safari、日历、备忘录、计算器、文本编辑、Adobe Lightroom Classic（Lightroom 别名指向 Classic）；“打开电脑上的Lightroom软件”等明确本机说法保持本地执行，未知本机应用拒绝而不搜索网站；支持明确的标准 HTTP(S) 网址及百度、GitHub、Python、B站／哔哩哔哩／bilibili 名称，可带“网页／网站／官网／官方网站”及繁体写法，例如“打开B站”“打开B站网页”。未预设的站名会自动搜索并核对候选，Qwen 只选查到的编号，不自行猜写域名或纠正同音；明确要求打开应用时不会改为打开网站。网址中的路径、参数和标点原样保留；语音识别不保留完整网址时不执行，可使用已支持的网站名称。搜索必须说明目录和文件名关键词，缺目录只追问，须重新说完整指令。文件路径和关键词保留确认转写的字符，不自动繁简改写或猜同音；若与真实文件名不符，请按 GUI 转写纠正。

Qwen 每句仅获一个工具及精确参数枚举，改参数、重复或多项调用、否定、转述、引用与未支持复合句均不能执行。GUI 分别显示启动请求提交、检测到应用运行、部分搜索与失败；浏览器接收请求不等于页面加载成功，进程运行不等于窗口在前台。结果文件与原生播报从工具证据生成，不让模型重新编造执行状态。

文件搜索只读名称，限定主目录内明确的非隐藏子目录（可说桌面/下载/文档，或完整路径），跳过隐藏项与符号链接；最多 20 条结果、60000 个条目、20 层子目录、4 秒扫描，独立进程 7 秒硬等待上限。POSIX 逐级使用 `O_NOFOLLOW`；达到上限或权限不足标明部分扫描，不能据此声称整个目录没有匹配。停止/重连取消等待、后续派发和旧报告发布，派发与取消使用同一代次锁；已提交给系统的打开动作不能撤回。

生产记录仍在忽略的 `output/m2/qwen/`，独立验收为 `verify_local_tasks.py`。该项本地动作功能没有新增依赖或模型；当前生产入口还须完成前文 Agent 环境安装及原生 UTF-8 后端更新，随后重开 GUI 并开启“本地转写与电脑任务”。Windows/Linux 保留文件搜索和网址交接代码，但本阶段应用白名单仅支持 macOS，未做其他平台实测。任意命令、鼠标键盘控制、文件修改、长期记忆与云端升级仍待后续阶段。验收与命令见安装指南第 15 节。

站名自动解析（2026-10-17）：现在可直接说“打开小众软件”等未预设的名称，不必先登记域名。已知应用、明确网址和常用站名快捷入口保留。独立解析器通过国内优先必应 RSS 搜索（请求失败或结果失配再试全球入口），按主机去重，最多读取三个首页；核对名称、标题、正文、页面声明的规范地址及主站/论坛/下载分站差异。Qwen 只能选择真实候选编号。自动打开须唯一 HTTPS 候选满足证据门槛且 Qwen 同意；搜索排名、模型自报概率、页面自称官网都不足以单独放行。这些线索不是域名所有权证明，不承诺任意站名都能无歧义识别。

身份不明、同名、验证页或仅 HTTP 候选会显示最多三个选择，可点击或在 60 秒内说“打开第一个网站”；选择绑定任务与会话并只消费一次，新任务、停止、重连或超时清除。可说“取消网站选择”。下一句仍需通过原转写质量、播放重叠和时效检查。GUI/原生播报区分待确认、未找到和实际打开请求提交，浏览器交接仍不代表页面加载；正文无法读取不直接断言网站不存在。网络、Qwen 和缓存均在后台，任务总限 60 秒、搜索沿用 25 秒，等待及派发前检查取消。

动态缓存保存在忽略的 `.cache/sites/resolutions.json`，最多 128 条；证据匹配有效 7 天，用户明确选择有效 30 天。复用前重读首页并核对主机，失败重新解析。可说“重新查找小众软件网站”“忘记小众软件的网站地址”，或用“清除网站缓存”及 GUI 按钮清空；此操作管理动态映射，源码内常用站名快捷入口不删除。搜索仅发送站名查询及有界页面请求，不发送对话历史、音视频；候选和选择证据写本地报告，统计不含正文。

独立 `verify_website.py --name 小众软件` 只进行真实搜索和 Qwen 判断，`--open` 才实际打开一次，歧义不会自动选择；探针缓存隔离在 output/。已评估 SearXNG、DDGS 搜索项目，本轮复用现有网络层，无新增依赖、外部服务、模型或后端补丁；安装步骤与命令见安装指南第 16 节。

### 文档与未来终端

[AGENTS.md](AGENTS.md) 是开发者契约；[CHANGELOG.md](CHANGELOG.md) 记录变更和迁移差距。已确认的方案 B 权威 DOCX 在本机 `brainstorming_projectPLAN/` 维护，整个目录不进 Git、不推送。Agent 已获准按确认的架构决策同步该本地目录。模板 `voices/silverwalf_voice.wav` 经 bo s s 明确允许公开推送；模型和实际测试录音录像不推送。`codinglog_by_awaqwq233/` 仍仅由 bo s s 手动维护且不进 Git。

未来可接入全屋摄像头、智能眼镜第一视角，以及 AR / Vision Pro 空间 GUI。

### 联网搜索（2026-10-09）

启动语音并启用“本地转写与电脑任务”后，可说“上网搜索上海明天的天气”“联网查询 Python asyncio 官方文档”，也支持明确城市天气问句。由本地 Qwen 调用搜索，默认先国内必应 RSS、失败再试固定全球入口，免 API 密钥。该入口不是承诺稳定的公开 API；网络受限、验证页、格式变化或无结果会明确失败。

每次最多返回 5 个来源，前三页并发读取静态正文。使用现有 httpx 与标准库；公网 URL、IPv4 DNS 和逐跳重定向核验后连接已核验 IP，保留原始 Host/TLS SNI 和证书验证。不会执行网页脚本，不携带浏览器登录、Cookie 或环境代理。网络工作在后台大脑线程，整次 25 秒上限并可取消，音视频上行保持原节拍。

完整解析“查寻”“让我上网查一下”，兼容地区与日期的不同顺序；“明天海口的天气”实际搜索“海口天气”，原日期保留用于正文筛选。未指定城市时用原生语音本地追问，不调用 Qwen/搜索、不生成报告；“插一下天气”不会获得出网权限。读取正文前过滤其他城市、字典、小说等失配来源，国内入口失配会尝试固定全球入口。原话与实际搜索词均在 GUI 和报告显示；没有相关摘录时状态为 no_answer，只保存查询记录，播报明确未取得答案。Qwen 仅能对用户本句关键词搜索一次，目标天气日期与英文具体词筛选后选择已读段落编号；程序核对后交付原文，保留数字、日期和来源。首版提供来源摘录，尚无自由改写/翻译；原文一致不代表网站内容真实、时效或相关性。来源未读、信息不足会明确说明，抓取时间不是发布时间。GUI 展示实际 `search_web` 调用与来源/读取状态，报告保存链接和原始证据，原生语音读有界摘录。仅搜索关键词发给搜索服务，随后请求结果来源页；对话历史、音视频和参考音不出网。

项目根目录运行 `.cache/m0/venv/bin/python verify_web_search.py --query "上海明天天气"` 可验证真实 Qwen → 搜索/来源 → 文件；加 `--search-only` 仅检验联网。不会打开设备，独立报告保存在忽略的 `output/m2/search/`，生产报告仍在 `output/m2/qwen/`。没有新增依赖、API 密钥或后端重新编译要求，重开 GUI 加载本轮客户端更新。

2026-10-09 视觉短句修复：感知提示明确支持“你看到了什么”。质量拒绝的短视觉候选（最长 4 秒）可保留全 PCM、分别补 300/600ms 前导静音再转写；两次都达到原门槛且为相同中性视觉问句才恢复，不提供示例提示、不降低阈值，不对系统/联网主题、长句、重叠、过期或旧句做恢复。只对合格失败候选增加 CPU 等待。旧句迟到原话/错误提示/工具提交已隔离，真正拒绝显示编号和原因。真实 CPU Whisper 带噪短句从拒绝恢复；有内容的合成画面 65 秒文件验证中，“你看到了什么”返回正确蓝色界面描述和 2.64 秒原生音频。此前纯灰画面的长/短对照均沉默；不将合成验证当作用户那次声学原因或真机验收。
