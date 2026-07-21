# J.A.C. Project Notes (English)

## Project Overview

J.A.C. stands for "Just A Code". This project is a **local-first multimodal AI assistant prototype** inspired by JARVIS: it perceives the user's environment through camera/audio, reasons over the current scene, speaks back with emotion-aware TTS, and is eventually meant to act proactively without an explicit trigger.

The long-term product vision is an **intelligent-glasses assistant**:

- Glasses / MR device captures real-world video and audio.
- A MacBook-class host performs low-latency local perception and reasoning.
- A cloud or LAN server handles heavier reasoning, long-term memory, routing to larger models, and external APIs when local compute is insufficient.
- The assistant should eventually support proactive perception, agentic task execution, external API use, voice/HUD output, and a closed-loop task cycle.

The current codebase is a **Python desktop prototype**, not the final glasses/cloud architecture yet.

## Current Implementation

The runnable entry point is `main.py`. It wires together:

- OpenCV camera capture through `src/capture/camera.py` (auto-detects camera id, defaults to 1280×720).
- YOLOv8 object detection through `src/analysis/detector.py` (YOLOv8 only, `conf=0.5`).
- Thread-safe shared context through `src/utils/context.py` (adds a transcription ring buffer, latest-frame cache, and intervention flags on top of the old version).
- VAD-based microphone recording through `src/audio/recorder.py` (PyAudio + WebRTC VAD, thresholds/warmup/min-duration tuned).
- Whisper speech-to-text through `src/audio/stt.py` (default `model_size="tiny"`, **non-streaming**).
- Local "brain" reasoning through `src/brain/llm.py` (`LocalBrain`, multi-backend: `lm_studio` / `ollama` / `llama_cpp` / `auto`).
- Genie-TTS voice synthesis through `src/audio/genie_tts.py` (GPT-SoVITS ONNX, with emotion reference-audio switching), falling back to `src/audio/tts.py` (pyttsx3 / macOS `say`).
- **Proactive judgment engine (new)**: `src/judgment/judge.py` (`JudgmentEngine`, connects to MiniCPM-o on LM Studio and continuously decides whether to intervene).

### Runtime flow (`main.py`)

1. Initialize camera, YOLO detector, speaker (prefer `GenieSpeaker`, fall back to `Speaker`), Whisper, VAD recorder, `LocalBrain` (**default `backend="lm_studio"`, model `Qwen3.5-9B-Q4_K_M.gguf`**).
2. Start three threads: audio main loop (listen → transcribe → wake-word check → respond), **console-input thread (new)**, and the judgment-engine thread (daemon).
3. Main loop per frame: grab frame → YOLO detect → update `SharedContext` (vision summary + cache latest frame) → draw FPS / status light (Listening/Thinking/Speaking) → `cv2.imshow`.
4. Wake words: `jac` / `j.a.c` / `杰克` / `接客` / `你好` / `hello jac` / `hi jac` / `你好 jac` / `hey jac`.
5. After wake-up the system enters `AWAKE`; `SYSTEM_STATE` auto-returns to `SLEEP` after **20 seconds (`AWAKE_TIMEOUT`)** of no interaction; saying "再见/休息" ("goodbye/rest") puts it to sleep immediately.
6. User input goes to `handle_user_text` → `process_response`: get vision summary → if it is a visual query (看到/看见/有什么/picture/who…) and the backend supports images, send the real camera frame via `brain.think_with_image()`; otherwise a text `brain.think()` (with the vision summary).
7. The model is expected to reply as `[emotion] content`; the emotion tag is parsed and passed to the speaker (`emotion_hint`).
8. If the judgment engine is active, the main loop checks for intervention requests each frame and, if found, spawns a daemon thread to respond proactively (bypassing the wake word).

### Multimodal image Q&A (new capability)

`LocalBrain.think_with_image(prompt, frame)` encodes the current frame as a JPEG base64 and sends it in OpenAI multimodal message format: native for `lm_studio` / `ollama`; for `llama_cpp` it auto-mounts `mmproj-*.gguf` via `_find_mmproj()`. On image-request failure it falls back to a YOLO-summary-based text answer (`build_text_only_vision_reply`).

### Keyboard and input controls

- `q`: quit.
- `SPACE`: manually wake ("我在，请讲。" / "I'm here, please speak.").
- **Console stdin text input (new, not in the old docs)**: at any time, type text and press Enter to feed it as user speech with `source="console"`, `bypass_wake=True`, going straight into thinking (bypassing the wake word).

## Important Files And Directories

- `main.py`: main multimodal runtime.
- `src/capture/camera.py`: camera wrapper, Windows/macOS aware.
- `src/analysis/detector.py`: YOLOv8 detector wrapper.
- `src/audio/recorder.py`: PyAudio + WebRTC VAD recorder.
- `src/audio/stt.py`: OpenAI Whisper wrapper.
- `src/audio/tts.py`: basic cross-platform system TTS wrapper.
- `src/audio/genie_tts.py`: Genie-TTS / GPT-SoVITS ONNX speaker with emotion/reference-audio handling and fallback.
- `src/brain/llm.py`: `LocalBrain`, llama.cpp / LM Studio / Ollama / auto backends, with `think_with_image`.
- `src/judgment/judge.py`: **new**, proactive judgment engine (MiniCPM-o via LM Studio).
- `src/judgment/__init__.py`: **new**.
- `src/utils/context.py`: thread-safe shared context (vision summary, state flags, transcription buffer, frame cache, intervention flag).
- `models/`: local GGUF model directory (see below).
- `GenieData/` and `genie_assets/`: Genie-TTS model/data/audio assets.
- `temp/`: runtime temporary audio files.
- `ffmpeg.exe`: local FFmpeg binary used by audio/media dependencies on Windows.
- `requirements.txt` / `requirements_fixed.txt`: dependency snapshots (`requirements.txt` is newer, `requirements_fixed.txt` is the older stable one).
- `DEPLOY_GUIDE.txt`: Windows/macOS offline migration and deployment guide.
- `Modelfile`: **new**, Ollama build definition (jac-qwen3.5).
- `codingLOG.md`: notes on the gap from the final assistant goal.
- `codinglog_by_awaqwq233/`: project background, expected architecture, progress notes, and research docs.
- `build.py`: PyInstaller build helper for `JAC_Prototype`.
- `fix_install.py`: dependency repair helper (PyAudio, llama-cpp-python, VAD on Windows).
- `setup_ffmpeg.py`: copies an `imageio-ffmpeg` binary into the project root as `ffmpeg.exe`.
- `verify_model.py`: verifies llama-cpp-python and the local GGUF model.

Generated or bulky local artifacts that are not usually useful to edit:

- `.venv/` / `.cache/` / `__pycache__/`
- model binaries (`*.gguf`, `*.pt`, `*.onnx`, `*.bin`)
- runtime audio under `temp/`

## Models And Assets

Current local reasoning models (4 GGUF files in `models/`):

- `Qwen3.5-9B-Q4_K_M.gguf`: the current "brain" model (~5.6GB), specified by `main.py`, loaded by default via the `lm_studio` backend (127.0.0.1:12345).
- `mmproj-Qwen3.5-9B-BF16.gguf`: Qwen3.5 multimodal projection (~0.9GB), auto-mounted by the `llama_cpp` backend for visual Q&A.
- `MiniCPM-o-4_5-Q4_K_S.gguf`: the judgment-engine model (~4.8GB), used by `JudgmentEngine` once loaded in LM Studio.
- `Qwen3.6-35B-A3B-...-IQ2_M.gguf` (~11.6GB): **downloaded but not referenced by code or config** — likely a spare large model or future cloud/server offload. Do not assume it is active.

> Note: the old `models/qwen1_5-1_8b-chat-q4_k_m.gguf` and `models/README.txt` no longer exist; remove related descriptions.

Current object detector: `yolov8n.pt` (`conf=0.5`). The old NVIDIA LocateAnything-3B vision-understanding model **has been removed** — per `detector.py`'s comment, vision understanding is now handled textually by JACbrain (Qwen3.5-9B); vision consists only of YOLO labels + an LLM text summary.

Current STT: Whisper, `model_size="tiny"`, non-streaming.

Current TTS: preferred Genie-TTS (ONNX under `genie_assets/onnx`); fallback pyttsx3 / macOS `say`. `genie_tts.py` is enhanced vs. the old version: it switches reference audio by emotion (`ref_<emotion>.wav`), uses random `1~4.mp3` samples (80% chance), and auto-falls back to system TTS on model incompatibility (sets `available=False`).

## Setup And Run

The deployment guide recommends Python 3.10 / 3.11 for best compatibility.

Basic install:

```bash
pip install -r requirements.txt
```

If dependencies fail on Windows:

```bash
python fix_install.py
```

If FFmpeg is missing:

```bash
python setup_ffmpeg.py
```

Verify the GGUF model:

```bash
python verify_model.py
```

### Prerequisites to run (important change)

- **The default `backend="lm_studio"`** means you must start **LM Studio** and load `Qwen3.5-9B` at `127.0.0.1:12345` before running (and optionally MiniCPM-o for proactive judgment). Otherwise every thinking request fails to connect.
- For pure local GGUF inference, change `LocalBrain(..., backend="lm_studio")` in `main.py` to `"llama_cpp"` or `"auto"` (`auto` probes available backends), and keep the corresponding GGUF in `models/`.
- Ollama usage: build `jac-qwen3.5` with the bundled `Modelfile`, then set backend to `"ollama"`.

Run the prototype:

```bash
python main.py
```

Required local hardware/runtime conditions:

- A working camera and a working microphone.
- FFmpeg available in the project root or system PATH.
- A running LM Studio (default) or a local GGUF model (after changing backend).
- Genie-TTS ONNX assets (optional; falls back to system TTS if missing).

## Current Progress From Logs

The project was restarted on 2026-06-23 after gaokao. Direction (from `codinglog_by_awaqwq233/当前进度.docx`):

- Keep building with vibe coding.
- Digitize the paper architecture diagram.
- Reconsider the architecture using newer tools and models such as OpenClaw, Xiaomi MiLoco 2.0, and newer Qwen-family models that can run on a 48 GB MacBook-class machine.
- Rework the voice model path because current voice clone / ONNX / response behavior has bugs.
- Research whether a small judgment model can think while receiving streaming input.
- Set up GitHub submission/open-source tooling.
- Explore server connection modules after stable servers are available, including `awaqwq233.cloud` and `awaqwq233.com`.

**Progress already landed in code (vs. the old docs):**

- The brain upgraded from Qwen1.5-1.8B to Qwen3.5-9B and was abstracted into a multi-backend `LocalBrain`.
- Added `src/judgment` proactive judgment-engine prototype (MiniCPM-o via LM Studio, decides every 4s whether to intervene) — the first landing of the vision's "core judgment / continuous perception".
- Added multimodal image Q&A `think_with_image()` (sends the real camera frame on visual queries).
- Added `SLEEP`/`AWAKE` state machine + 20s auto-sleep timeout.
- Added console text-input live dialogue (bypassing the wake word).
- Expanded wake words; Genie-TTS emotion reference-audio switching and random samples.

The `codingLOG.md` gap list is still accurate on the **unimplemented** items: function calling / tool execution, persistent memory (JSON/vector DB), agent framework, MCP / OpenClaw integration, and streaming STT/LLM/TTS. Note that `codingLOG.md` is partly older than `main.py`; treat it as architectural gap notes, not exact implementation status.

## Expected Future Architecture

The planning docs describe a system driven by "J.A.C. Brain":

- Input layer: device signals, realtime audio, and visual frames.
- Perception/preprocessing: speech-to-text, CNN/vision analysis, and buffering parsed information in memory.
- Core judgment: a "multimodal small judgment model" or judgment model cluster that continuously decides whether J.A.C. should intervene.
- Regulation/safety module: checks whether a judgment is correct, blocks unwanted silent execution, and returns to the judgment cycle on false positives.
- J.A.C. Brain: larger reasoning model, possibly Qwen-style or modified Xiaomi MiLoco 2.0, responsible for complex analysis and task planning.
- Agent execution: internal skills and external APIs, potentially through OpenClaw/MCP-like integrations.
- External model APIs: Gemini, ChatGPT, Grok, Claude, Qwen, DeepSeek, etc.
- Output layer: app/HUD result display and emotion-aware TTS.
- Closed loop: output feeds back to the next judgment cycle for continuous proactive service.

Hardware expectations from docs:

- Host: future MacBook Pro 14" M5 Pro-class machine, 48 GB+ unified memory, 1 TB SSD.
- Possible peripherals: Xiaomi AI glasses, Apple Vision Pro, or portable camera/MR devices.
- Portable power: high-output power banks in a backpack.
- Server: LAN/public server for heavier models, with current conceptual target around dual 22 GB RTX 2080 Ti and 128 GB RAM.

## Engineering Guidance For Future Work

- Preserve the local-first design. Keep wake-word detection, VAD, basic perception, and urgent interactions local whenever possible.
- Prefer simple rules before small models, and small models before large models, especially for intervention judgment and latency-sensitive paths.
- Avoid making a large model decide every low-level routing choice. Use task routing tables and explicit policies unless model judgment is truly needed.
- Keep camera/audio capture and model inference loosely coupled through clear context/state objects. `SharedContext` is the current seed of that pattern.
- **New backends (cloud/external APIs) should be added inside `LocalBrain`**, not by bypassing it with direct requests, so the unified multimodal interface and mock fallback are preserved.
- Be careful with thread state in `main.py`: `context.is_speaking`, `context.is_listening`, `context.is_thinking`, and `conversation_running` are used to avoid feedback loops and overlapping interactions.
- Treat `temp/` audio files as disposable runtime output.
- Avoid committing large model/audio/build artifacts unless the project explicitly wants binary assets tracked.
- Watch privacy and safety carefully. The vision documents explicitly want proactive always-on perception, which means future implementations should include visible consent, local filtering, logging controls, and clear boundaries before recording, identifying people, or sending data to cloud APIs.
- For any new agent/tool execution feature, require explicit allowlists and confirmations for risky operations. The current assistant can talk and observe; executing system actions is a major trust boundary.
- For latency improvements, prioritize streaming and pipelining: streaming ASR, incremental reasoning, and streaming/early TTS.
- For memory, start simple with structured JSON summaries before adding vector databases.
- If changing TTS, note that the project owner currently considers the voice stack buggy and expects a rework.

## Known Limitations

- **Strong LM Studio dependency**: `main.py` hardcodes `backend="lm_studio"`, so LM Studio must be running locally on port 12345 with `Qwen3.5-9B` loaded; otherwise all thinking fails. Pure local GGUF requires changing the backend.
- **Dual-model memory pressure**: enabling proactive judgment requires LM Studio to load both `Qwen3.5-9B` + `MiniCPM-o`, which is resource-heavy. `JUDGMENT_ENGINE_ENABLED=False` by default; if not detected it silently enters passive mode (no error, no intervention).
- Genie-TTS is still a known buggy area (model-version incompatibility triggers fallback).
- `AudioRecorder.listen_and_record()` can still block waiting for VAD-triggered speech; shutdown responsiveness may need attention.
- STT/LLM/TTS are all non-streaming, so end-to-end latency remains high.
- No function calling, no persistent memory, no agent/MCP/OpenClaw integration (goals unimplemented).
- `Qwen3.6-35B` is downloaded but not wired in — do not assume it is active.
- `requirements.txt` installs `fastapi`/`uvicorn`/`websockets`, but there is no corresponding server code under `src/` — these are transitive or placeholder deps; do not assume an API server exists.
- There are no automated tests in the current project tree.

## Build Notes

`build.py` uses PyInstaller:

```bash
python build.py
```

It creates an onedir console build named `JAC_Prototype` and collects `ultralytics` assets. Expect packaging to require special care for model files, FFmpeg, Genie-TTS assets, Whisper cache/model files, and platform audio permissions.
