# J.A.C. Project Notes

## Project Overview

J.A.C. means "Just A Code". The project is a local-first multimodal AI assistant prototype inspired by JARVIS: it should perceive the user's environment through camera/audio, reason over the current scene, speak back with emotion-aware TTS, and eventually act proactively without explicit user triggering.

The long-term product vision is an intelligent glasses assistant:

- Glasses / MR device capture real-world video and audio.
- A MacBook-class host performs low-latency local perception and reasoning.
- A cloud or LAN server handles heavier reasoning, long-term memory, routing to larger models, and external APIs when local compute is insufficient.
- The assistant should eventually support proactive perception, agentic task execution, external API use, voice/HUD output, and a closed-loop task cycle.

The current codebase is a Python desktop prototype, not the final glasses/cloud architecture yet.

## Current Implementation

The runnable entry point is `main.py`. It wires together:

- OpenCV camera capture through `src/capture/camera.py`.
- YOLOv8 object detection through `src/analysis/detector.py`.
- Shared visual/status context through `src/utils/context.py`.
- VAD-based microphone recording through `src/audio/recorder.py`.
- Whisper speech-to-text through `src/audio/stt.py`.
- Local LLM reasoning through `src/brain/llm.py`.
- Genie-TTS voice synthesis through `src/audio/genie_tts.py`, with fallback to system `pyttsx3` / macOS `say` through `src/audio/tts.py`.

Runtime flow:

1. `main.py` starts the camera and object detector.
2. A background audio thread listens with VAD.
3. Wake words include `jac`, `j.a.c`, `杰克`, `接客`, `你好`, `hello`, and `hi`.
4. After wake-up, recognized user speech is sent to `LocalBrain`.
5. The system prompt includes the current visual summary from `SharedContext`.
6. The model is expected to reply as `[情绪] 回复内容`.
7. The emotion tag is parsed and passed to the speaker.
8. The OpenCV window shows camera frames, YOLO annotations, FPS, and current state such as `Listening...`, `Thinking...`, or `Speaking...`.

Keyboard controls in the prototype:

- `q`: quit.
- `SPACE`: manually wake J.A.C.

## Important Files And Directories

- `main.py`: main multimodal runtime.
- `src/capture/camera.py`: camera wrapper, Windows/macOS aware.
- `src/analysis/detector.py`: YOLOv8 detector wrapper.
- `src/audio/recorder.py`: PyAudio + WebRTC VAD recorder.
- `src/audio/stt.py`: OpenAI Whisper wrapper.
- `src/audio/tts.py`: basic cross-platform system TTS wrapper.
- `src/audio/genie_tts.py`: Genie-TTS / GPT-SoVITS ONNX speaker with emotion/reference-audio handling and fallback.
- `src/brain/llm.py`: llama.cpp / GGUF local model wrapper, with mock mode if the model is unavailable.
- `src/utils/context.py`: thread-safe shared context for current visual detections and state flags.
- `models/`: local GGUF model directory. Current expected model is `qwen1_5-1_8b-chat-q4_k_m.gguf`.
- `GenieData/` and `genie_assets/`: Genie-TTS model/data/audio assets.
- `temp/`: runtime temporary audio files.
- `ffmpeg.exe`: local FFmpeg binary used by audio/media dependencies on Windows.
- `requirements.txt`: full Python dependency snapshot.
- `DEPLOY_GUIDE.txt`: Windows/macOS offline migration and deployment guide.
- `codingLOG.md`: notes on current gaps from the final assistant goal.
- `codinglog_by_awaqwq233/`: project background, expected architecture, progress notes, and research docs.
- `build.py`: PyInstaller build helper for `JAC_Prototype`.
- `fix_install.py`: dependency repair helper, especially PyAudio, llama-cpp-python, and VAD on Windows.
- `setup_ffmpeg.py`: copies an `imageio-ffmpeg` binary into the project root as `ffmpeg.exe`.
- `verify_model.py`: verifies llama-cpp-python and the local GGUF model.

Generated or bulky local artifacts that are not usually useful to edit:

- `.venv/`
- `.cache/`
- `__pycache__/`
- model binaries (`*.gguf`, `*.pt`, `*.onnx`, `*.bin`)
- runtime audio under `temp/`

## Models And Assets

Current local reasoning model:

- `models/qwen1_5-1_8b-chat-q4_k_m.gguf`
- Recommended in `models/README.txt` as Qwen1.5-1.8B-Chat-GGUF because it is small, fast, and has good Chinese ability.

Current object detector:

- `yolov8n.pt`
- Loaded by `ObjectDetector` with confidence threshold `0.5`.

Current STT:

- Whisper, currently instantiated in `main.py` with `model_size="tiny"` for speed.

Current TTS:

- Preferred: Genie-TTS using ONNX files in `genie_assets/onnx`.
- Fallback: `pyttsx3`, or macOS `say`.
- `genie_assets/prompt_wav.json` currently maps the normal reference voice to `zh_vo_Main_Linaxita_2_1_10_26.wav`.

## Setup And Run

The deployment guide recommends Python 3.10 or 3.11 for best compatibility.

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

Run the prototype:

```bash
python main.py
```

Required local hardware/runtime conditions:

- A working camera.
- A working microphone.
- FFmpeg available in the project root or system PATH.
- The GGUF model present in `models/` for real LLM replies.
- Genie-TTS ONNX assets present for the preferred voice path, otherwise the system falls back to basic TTS.

## Current Progress From Logs

The project was restarted on 2026-06-23 after gaokao. Current direction from `codinglog_by_awaqwq233/当前进度.docx`:

- Continue building with vibe coding.
- Digitize the paper architecture diagram.
- Reconsider the architecture using newer tools and models such as OpenClaw, Xiaomi MiLoco 2.0, and newer Qwen-family models that can run on a 48 GB MacBook-class machine.
- Rework the voice model path because current voice clone / ONNX / response behavior has bugs.
- Research whether a small judgment model can think while receiving streaming input.
- Set up GitHub submission/open-source tooling.
- Explore server connection modules after stable servers are available, including `awaqwq233.cloud` and `awaqwq233.com`.

The root `codingLOG.md` lists the main gap from the final goal:

- Interaction is still partly passive; the target is wake word + VAD + continuous perception.
- There is no real function-calling / tool-execution layer yet.
- Memory is short-term only; the target is persistent memory via JSON/vector DB with periodic summarization and pruning.
- End-to-end latency is high; the target is streaming STT/LLM/TTS.

Note: the code has already added a VAD listening loop and wake-word state machine, so `codingLOG.md` is partly older than `main.py`. Treat it as architectural gap notes, not exact implementation status.

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
- Be careful with thread state in `main.py`: `context.is_speaking`, `context.is_listening`, and `conversation_running` are used to avoid feedback loops and overlapping interactions.
- Treat `temp/` audio files as disposable runtime output.
- Avoid committing large model/audio/build artifacts unless the project explicitly wants binary assets tracked.
- Watch privacy and safety carefully. The vision documents explicitly want proactive always-on perception, which means future implementations should include visible consent, local filtering, logging controls, and clear boundaries before recording, identifying people, or sending data to cloud APIs.
- For any new agent/tool execution feature, require explicit allowlists and confirmations for risky operations. The current assistant can talk and observe; executing system actions is a major trust boundary.
- For latency improvements, prioritize streaming and pipelining: streaming ASR, incremental reasoning, and streaming/early TTS.
- For memory, start simple with structured JSON summaries before adding vector databases.
- If changing TTS, note that the project owner currently considers the voice stack buggy and expects a rework.

## Known Limitations

- The local LLM wrapper uses a small Qwen1.5 1.8B model with `n_ctx=2048` and `n_threads=4`; quality and memory are limited.
- The vision summary only counts YOLO object labels; it does not yet do OCR, face recognition, depth, scene graphs, or full visual-language understanding.
- Whisper is used non-streamingly over saved WAV files, so latency remains significant.
- Wake-word detection is currently string matching after Whisper transcription, not a dedicated low-latency wake-word model.
- `AudioRecorder.listen_and_record()` can block waiting for VAD-triggered speech; shutdown responsiveness may need attention.
- There is no persistent memory, tool/function calling, scheduler, cloud offload, OpenClaw/MCP integration, or agent framework in the current code.
- There are no automated tests in the current project tree.

## Build Notes

`build.py` uses PyInstaller:

```bash
python build.py
```

It creates an onedir console build named `JAC_Prototype` and collects `ultralytics` assets. Expect packaging to require special care for model files, FFmpeg, Genie-TTS assets, Whisper cache/model files, and platform audio permissions.
