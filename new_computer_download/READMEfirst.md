# J.A.C. Setup Guide

[中文安装指南](#中文安装指南)

## English

Option B was approved on 2026-10-06. This guide provisions the isolated M0 backend and probe. `setup_new_computer.py --only m0` installs its isolated environment; the other stages and production `requirements.txt` still serve the legacy application.

M0 requires Python 3.11, CMake, Xcode Command Line Tools, an external GGUF model directory and the two repositories pinned in [backend.lock.json](../backend.lock.json). It uses C++ Metal for inference and a Python Gateway / Worker for orchestration. No NVIDIA machine or speaker-embedding extraction is needed.

The Chinese section below contains the same commands, one per block. Run environment commands from the J.A.C. root. Clone both backend repositories outside J.A.C.; replace placeholder paths with absolute paths.

Create `.cache/m0/venv` and install `requirements-m0.txt`. Use `start_m0_backend.py --preflight --verify-sha` before launching. The launcher binds all ports to loopback, disables upstream session recording, rejects occupied ports and terminates only its own children on Ctrl+C.

The Gateway URL is `ws://127.0.0.1:8006/v1/realtime?mode=video`. Inputs are 16 kHz mono float32 PCM Base64; outputs are 24 kHz mono float32 PCM Base64. The probe sends fixed one-second chunks and reference-WAV voice fields. `--require-audio` fails if no native audio is returned.

The file probe proves protocol and native-audio transport only. It does not validate microphone/camera capture, playback quality, 10 start/stop cycles or a 30-minute soak. The pinned Gateway limits video sessions to 300 seconds and audio sessions to 600 seconds; production migration must implement controlled reconnection.

`verify_live_duplex.py --consent-devices` performs a short live test only after consent and with headphones. It captures on background threads and plays native PCM without recording media. The 2026-10-06 40-second live run passed (P95 679 ms, 31 seconds of native playback); the user confirmed audible, relevant responses. The 30-minute soak remains pending at the user's request.

Publishing policy: the template `voices/silverwalf_voice.wav` is authorized for GitHub. Model binaries, runtime recordings and the local `brainstorming_projectPLAN/` architecture directory are excluded; cloning the repository does not include that directory.

## 中文安装指南

发布边界：模板 `voices/silverwalf_voice.wav` 已获准推送；模型、实际测试录音录像和本机 `brainstorming_projectPLAN/` 架构目录不提交、不推送。架构目录只在本机维护，克隆 GitHub 仓库不会取得该目录。

方案 B 已于 2026-10-06 确认。本阶段先准备独立 M0 环境和后端探针。现有主程序与 `requirements.txt` 仍服务旧实现；安装器新增 `--only m0` 入口，其他阶段保留旧逻辑。

### 1 安装 Python 3.11

macOS 需要 Xcode 命令行工具和 Homebrew。本机已安装 Python 3.11；新机器可执行：

```bash
brew install python@3.11 cmake
```

### 2 建立独立环境

可以用以下命令一键建立独立 M0 环境并安装依赖；加 --dry-run 可预览且不产生修改。

```bash
python3.11 new_computer_download/setup_new_computer.py --only m0
```

以下是同等的手动步骤。

以下命令在 J.A.C. 根目录运行。现有 `.venv` 保留，M0 环境位于已忽略的 `.cache/`。

```bash
python3.11 -m venv .cache/m0/venv
```

安装固定 M0 依赖，包含 Gateway、Worker、媒体文件探针和测试插件。

```bash
.cache/m0/venv/bin/python -m pip install -r new_computer_download/requirements-m0.txt
```

国内可使用清华镜像；失败时保留日志并回退官方源，不关闭证书校验。

```bash
.cache/m0/venv/bin/python -m pip install -r new_computer_download/requirements-m0.txt --index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

### 3 锁定并编译引擎

在项目外的专用后端目录执行。不要覆盖已有用户后端。将路径替换为实际绝对路径。

```bash
git clone https://github.com/tc-mb/llama.cpp-omni.git /absolute/backend/llama.cpp-omni
```

```bash
git -C /absolute/backend/llama.cpp-omni checkout 873056743b74e1a4ce5dcf7290e2298428e214db
```

```bash
git -C /absolute/backend/llama.cpp-omni apply /absolute/JAC/new_computer_download/patches/engine-loopback.patch
```

该补丁只让内部引擎遵守 --host。固定版本自动启用 OpenSSL 时会构造需要证书的 SSLServer；本机内部链路使用 HTTP，因此显式关闭 LLAMA_OPENSSL。

```bash
cmake -S /absolute/backend/llama.cpp-omni -B /absolute/backend/llama.cpp-omni/build -DCMAKE_BUILD_TYPE=Release -DGGML_METAL=ON -DLLAMA_OPENSSL=OFF -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF
```

```bash
cmake --build /absolute/backend/llama.cpp-omni/build --target llama-omni-server --target llama-omni-cli -j18
```

### 4 锁定 Gateway 和 Worker

```bash
git clone https://github.com/OpenBMB/MiniCPM-o-Demo.git /absolute/backend/MiniCPM-o-Demo
```

```bash
git -C /absolute/backend/MiniCPM-o-Demo checkout 47709a9210dfd71afa76c058e017fc8c4db5c8d2
```

本阶段不安装上游完整 torch 依赖，也不需要构建它的移动端前端；使用 M0 独立环境运行 Gateway / Worker。

### 5 模型目录

模型仓库为 `openbmb/MiniCPM-o-4_5-gguf`，固定 revision 为 `db25077c33951fe163b42986fba0132e279872a2`。精确文件清单及 SHA256 在 [backend.lock.json](../backend.lock.json)。

模型目录必须在 J.A.C. 项目之外，并具有以下结构：

```text
MiniCPM-o-4_5-gguf/
  MiniCPM-o-4_5-Q4_K_M.gguf
  audio/MiniCPM-o-4_5-audio-F16.gguf
  vision/MiniCPM-o-4_5-vision-F16.gguf
  tts/MiniCPM-o-4_5-tts-F16.gguf
  tts/MiniCPM-o-4_5-projector-F16.gguf
  token2wav-gguf/encoder.gguf
  token2wav-gguf/flow_matching.gguf
  token2wav-gguf/flow_extra.gguf
  token2wav-gguf/hifigan2.gguf
  token2wav-gguf/prompt_cache.gguf
```

已有文件优先通过 SHA256 复用。若使用 Hugging Face CLI 下载，需先在专用环境安装 `huggingface_hub`；不要因此替换主程序依赖。

```bash
hf download openbmb/MiniCPM-o-4_5-gguf --revision db25077c33951fe163b42986fba0132e279872a2 --include 'MiniCPM-o-4_5-Q4_K_M.gguf' 'audio/*.gguf' 'vision/*.gguf' 'tts/*.gguf' 'token2wav-gguf/*.gguf' --local-dir /absolute/models/MiniCPM-o-4_5-gguf
```

国内网络可为同一命令设置 `HF_ENDPOINT=https://hf-mirror.com`，但下载后必须按 lock 的 SHA256 校验，不能仅依赖文件名或镜像元数据。

### 6 预检与启动

回到 J.A.C. 根目录，替换以下三个目录参数。预检验证固定 commit、模型哈希和端口，不启动服务。

```bash
.cache/m0/venv/bin/python new_computer_download/start_m0_backend.py --demo-dir /absolute/backend/MiniCPM-o-Demo --engine-dir /absolute/backend/llama.cpp-omni --model-dir /absolute/models/MiniCPM-o-4_5-gguf --verify-sha --preflight
```

使用同样路径启动。新克隆没有 config.json 时，脚本创建禁用会话录制的配置；已有配置不会被覆盖，必须设 `recording.enabled=false`。Ctrl+C 回收脚本启动的进程。

```bash
.cache/m0/venv/bin/python new_computer_download/start_m0_backend.py --demo-dir /absolute/backend/MiniCPM-o-Demo --engine-dir /absolute/backend/llama.cpp-omni --model-dir /absolute/models/MiniCPM-o-4_5-gguf
```

端口均只监听 127.0.0.1：Gateway 8006、注册接口 8007、Worker 22400、C++ 引擎 22500。运行日志位于已忽略的 `output/m0/`。

### 7 协议与原生语音探针

在第二个终端、J.A.C. 根目录运行。参考 WAV 通过 voice 字段转换成 16k float32 PCM；不需要 .pt、NVIDIA GPU 或 Voicebox。以下探针只发送仓库已有音频文件，不打开麦克风、摄像头或扬声器。

```bash
.cache/m0/venv/bin/python verify_duplex.py --mode audio --audio /absolute/中文提问.wav --voice voices/silverwalf_voice.wav --chunks 25 --require-audio --require-realtime
```

报告保存在 `output/m0/probe.json`，只包含计数和耗时，不保存原始媒体或对话文本。若用视频模式，可加 `--image /absolute/640x480.jpg` 发送测试帧。

输入文件应包含清楚的中文提问。探针默认先发 4 秒静音，覆盖后端启动保护期，再顺序发送输入，剩余时间补静音；总时长不足会明确失败，不会截断语音。`--require-realtime` 在耗时缺失或 P95 不低于 1 秒时失败，不因协议通了就认定性能通过。

经明确同意并戴好耳机后，可进行 40 秒真实设备测试；此命令会开启设备且自动停止，不录制或上传。

```bash
.cache/m0/venv/bin/python verify_live_duplex.py --consent-devices --seconds 40
```

可以用 `--input-device`、`--output-device` 指定本机 SoundDevice 编号。macOS 首次摄像头授权在启动主线程完成，后台线程只负责读取与编码；若系统弹窗出现，请先确认授权。报告保存在 `output/m0/live-probe.json`。

离线测试：

```bash
.cache/m0/venv/bin/python -m pytest tests/test_m0_duplex_probe.py tests/test_m0_backend_launcher.py tests/test_m0_live_probe.py -q -o addopts=
```

### 8 验收与后续迁移

M0 必须验证真实模型原生音频、听说节拍和性能。报告中的 `processing_p95_ms=null` 表示服务端没有提供足够耗时指标，不能视为实时性能通过。文件探针不能代替实时麦克风、摄像头、扬声器和 30 分钟运行验收。

2026-10-06 已完成 40 秒真机测试：640×480、194 帧、原生语音收播各 31 秒、P95 679ms，音频队列最高 1 块，输入/输出流状态错误均为 0，设备和线程清理通过。另完成 10 次设备与 WS 快速启停，逐次清理通过。bo s s 已确认听感和回答相关性；本轮明确暂不进行 30 分钟长测，因此 M0 尚未完整验收。

固定 Gateway 视频会话 300 秒、音频会话 600 秒。后续客户端必须实现受控重连、重新注入记忆和上下文；旧 /ws/duplex 页面不作为当前客户端协议依据。

M0 通过后才进入采集层、原生播放、GUI、Qwen 工具调用、OpenClaw、ChromaDB 迁移。Qwen 继续使用 LM Studio 127.0.0.1:12345；本阶段不需加载 Qwen 或启动云端。

### 故障排查

- 模型 SHA256 不符：保留原文件，检查是否有匹配的备份或重新下载固定 revision。禁止仅改文件名绕过校验。
- queue_done 超时：检查 Worker 已注册、三个健康接口正常，以及 output/m0 下的日志。
- 无 audio 增量：原生 TTS 验收尚未通过，检查全部 Token2Wav / TTS 模块和参考音。
- 中文字体异常：DOCX 使用显式东亚字体；如 Windows 缺少字体，安装对应字体后再检查页面渲染。Python 和 Markdown 文件统一 UTF-8。
- 依赖冲突：使用独立 M0 环境，不把 websockets 16 直接装进旧主程序环境。
