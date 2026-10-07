# J.A.C. Setup Guide

[中文安装指南](#中文安装指南)

## English

Option B was approved on 2026-10-06. M0 has been accepted by the user with the 30-minute soak explicitly waived. This guide provisions the isolated M0/M1 backend, probes and production Gateway CLI/GUI. `setup_new_computer.py --only gateway` (compatible alias: `--only m0`) installs Python 3.11 dependencies including PySide6 and verifies client/GUI imports without opening devices. Other stages retain legacy dependencies.

M0 requires Python 3.11, CMake, Xcode Command Line Tools, an external GGUF model directory and the two repositories pinned in [backend.lock.json](../backend.lock.json). It uses C++ Metal for inference and a Python Gateway / Worker for orchestration. No NVIDIA machine or speaker-embedding extraction is needed.

The Chinese section below contains the same commands, one per block. Run environment commands from the J.A.C. root. Clone both backend repositories outside J.A.C.; replace placeholder paths with absolute paths.

Create `.cache/m0/venv` and install `requirements-m0.txt`. Use `start_m0_backend.py --preflight --verify-sha` before launching. The launcher binds all ports to loopback, disables upstream session recording, rejects occupied ports and terminates only its own children on Ctrl+C.

The Gateway URL is `ws://127.0.0.1:8006/v1/realtime?mode=video`. Inputs are 16 kHz mono float32 PCM Base64; outputs are 24 kHz mono float32 PCM Base64. The probe sends fixed one-second chunks and reference-WAV voice fields. `--require-audio` fails if no native audio is returned.

The file probe proves protocol and native-audio transport only. The pinned Gateway limits video sessions to 300 seconds and audio sessions to 600 seconds. The M1 production client now rotates video sessions after 240 input seconds, with visible capture pauses and re-injection of the reference voice, supplied confirmed context and bounded assistant history. Untranscribed user speech is not reconstructed.

The 40-second live run, ten rapid lifecycle cycles and two 225-second live sessions passed. The user confirmed satisfactory interaction and accepted M0 without the remaining soak. Original reports retain `soak_30min_verified=false` because that duration was not measured; it no longer blocks migration.

After starting the pinned backend, run `main.py --gateway --gui` in the Python 3.11 environment. Explicit device consent and headphones are required before capture starts. `main.py --gateway --consent-devices` runs the console client. The Gateway GUI imports no legacy torch/PyAudio/Whisper/YOLO; legacy modes require their existing dependencies.

`verify_soak_duplex.py --consent-devices` measures 1,800 input seconds across eight 225-second video sessions while keeping the same backend running. It samples local health endpoints and process RSS, and saves aggregate/per-session counts even on failure. Devices restart between sessions; cross-session context restoration is not tested.

Publishing policy: the template `voices/silverwalf_voice.wav` is authorized for GitHub. Model binaries, runtime recordings and the local `brainstorming_projectPLAN/` architecture directory are excluded; cloning the repository does not include that directory.

## 中文安装指南

发布边界：模板 `voices/silverwalf_voice.wav` 已获准推送；模型、实际测试录音录像和本机 `brainstorming_projectPLAN/` 架构目录不提交、不推送。架构目录只在本机维护，克隆 GitHub 仓库不会取得该目录。

方案 B 已于 2026-10-06 确认。M0 已由 bo s s 接受并免做 30 分钟长测，当前进入 M1。独立 Python 3.11 环境已支持后端、探针及生产 Gateway CLI/GUI；`requirements-m0.txt` 新增已验证的 PySide6 6.11.2，安装器推荐 `--only gateway`（兼容 `--only m0`），安装后自检客户端、GUI 与后端控制依赖，不打开设备。现有 `.venv`、生产依赖和其他安装阶段保留旧模式兼容。

### 1 安装 Python 3.11

macOS 需要 Xcode 命令行工具和 Homebrew。本机已安装 Python 3.11；新机器可执行：

```bash
brew install python@3.11 cmake
```

### 2 建立独立环境

可以用以下命令一键建立独立 M0/M1 环境并安装依赖；加 --dry-run 可预览且不产生修改。

```bash
python3.11 new_computer_download/setup_new_computer.py --only gateway
```

macOS/Linux 也可从项目根目录使用包装脚本；它会原样转发安装参数：

```bash
bash new_computer_download/run.sh --only gateway
```

Windows 可使用 `new_computer_download\run.bat --only gateway`，先确保 Python 3.11 可用；脚本采用 UTF-8 控制台编码。当前 Metal 后端仍以 macOS Apple Silicon 为开发和验收平台，Windows 后端运行未验收。

以下是同等的手动步骤。

以下命令在 J.A.C. 根目录运行。现有 `.venv` 保留，M0 环境位于已忽略的 `.cache/`。

```bash
python3.11 -m venv .cache/m0/venv
```

安装固定 M0/M1 依赖，包含 Gateway、Worker、生产媒体客户端、Qt GUI 和测试插件。Qt 包体积较大，安装器优先使用国内镜像，失败再回退官方源。

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

已明确同意 30 分钟设备长测并戴好耳机后，运行：

```bash
.cache/m0/venv/bin/python verify_soak_duplex.py --consent-devices
```

长测保持同一组后端常驻，8 段各上行 225 秒，累计 1800 秒；初始化、尾部播放与段间设备释放重开使总墙钟时间略长于 30 分钟。每段有 4 秒启动保护静音，不恢复上段对话上下文，因此该测试不等同于无缝常开客户端验收。`--monitor-pid` 可重复指定后端 PID，报告含每 30 秒健康/RSS 样本；默认保存至已忽略的 `output/m0/soak-probe.json`，不含原始媒体或对话文本。验收检查逐段 P95 < 1000ms、原生音频收播一致、设备状态异常为零、全部清理与后端健康；安静段不要求模型强制发言，但全程必须收到原生音频。

只需两段验证时，可使用 `--seconds 450`：自动均分为两段各 225 秒；两段通过不会设置 `soak_30min_verified=true`。

离线测试：

```bash
.cache/m0/venv/bin/python -m pytest tests/test_m0_duplex_probe.py tests/test_m0_backend_launcher.py tests/test_m0_live_probe.py tests/test_m0_soak_probe.py tests/test_gateway_client.py tests/test_gateway_desktop_runtime.py tests/test_gateway_gui_restart.py -q -o addopts=
```

### 8 验收与后续迁移

M0 已根据真实模型/设备验证与 bo s s 明确确认标记通过，30 分钟长测免测。报告中的 `processing_p95_ms=null` 仍不能视为实时性能通过；已执行的数据和用户验收决策分别记录。

2026-10-06 已完成 40 秒真机测试、10 次启停与两段各 225 秒验证；bo s s 已确认效果良好并接受 M0。两段累计上行 450 秒、P95 678.662/891.148ms、原生音频收播各合计 176.2 秒；音频队列峰值 1、播放队列峰值 3、设备异常均 0，16 次健康采样通过。原始报告保持实测范围，30 分钟未测但已由用户豁免，不再作为进入 M1 的前置阻塞项。

固定 Gateway 视频会话 300 秒、音频会话 600 秒。后续客户端必须实现受控重连、重新注入记忆和上下文；旧 /ws/duplex 页面不作为当前客户端协议依据。

M1 采集层、原生播放、GUI 预览与手动启停已于 2026-10-07 获 bo s s 真机验收，反馈为「这次完全正常」。专项回归 57 项通过；这不增加未测的长测时长或循环次数。下一步接入 Qwen 大脑/工具升级，之后推进并行转写、ChromaDB 和 OpenClaw。Qwen 继续使用 LM Studio 127.0.0.1:12345；基础听看说无需加载 Qwen 或启动云端。

### 9 M1 生产入口

固定版本后端启动后，在独立 Python 3.11 环境打开轻量 Gateway GUI：

```bash
.cache/m0/venv/bin/python main.py --gateway --gui
```

选择「方案 B Gateway」，勾选设备同意并戴好耳机后点击启动。新入口复用原界面的画面预览、音量条、参数和实时文本；旧 Listen/回声门控/图像间隔参数在 Gateway 下禁用，图像固定每秒最新一帧。不带 `--gateway` 的终端入口仍运行传统实现；在新 GUI 选择旧模式时，需要完整旧依赖。

如果仅需终端中的语音交流：

```bash
.cache/m0/venv/bin/python main.py --gateway --consent-devices
```

可加 `--input-device` / `--output-device` 绑定内建麦克风与耳机。默认每 240 秒上行后暂停设备、重建会话并重注入参考音和有限上下文，Ctrl+C 清理设备；后端由独立启动器管理。统计报告在已忽略的 `output/m1/`，不保存媒体或对话文本。

无需设备的文件回放可使用 `--input-file`、可选 `--image-file`、`--session-seconds` 和 `--sessions`；每个会话回放同一测试文件，先补 4 秒启动静音，不将接收到的原生音频播放出来。文件验证不能替代新生产 GUI 的真机听感验收。

### 故障排查

- 停止后画面两侧残留、第二次启动报 RuntimeError / session_failed：2026-10-07 已完善整控件清屏与关闭握手，bo s s 已确认真实 GUI 恢复正常。旧客户端会过早断链；固定版本上游还会在 C++ 清理完成前报告关闭。手动停止现先停采集、保留 8 秒接收尾窗，等待关闭确认与连接关闭，再冷却 1 秒；GUI 显示「停止中…」并禁用启动，约 9 秒后恢复，超时则提供「重试停止」。退出窗口同样等待清理，不强制结束进程。旧版本升级时须先重启已受影响的后端，再按第 9 节重开 GUI；无需重装依赖或模型。该收尾窗口是固定版本兼容处理，不代表上游竞争或长期稳定性已验证。
- 模型 SHA256 不符：保留原文件，检查是否有匹配的备份或重新下载固定 revision。禁止仅改文件名绕过校验。
- queue_done 超时：检查 Worker 已注册、三个健康接口正常，以及 output/m0 下的日志。
- 无 audio 增量：原生 TTS 验收尚未通过，检查全部 Token2Wav / TTS 模块和参考音。
- 中文字体异常：DOCX 使用显式东亚字体；如 Windows 缺少字体，安装对应字体后再检查页面渲染。Python 和 Markdown 文件统一 UTF-8。
- 依赖冲突：使用独立 M0 环境，不把 websockets 16 直接装进旧主程序环境。
