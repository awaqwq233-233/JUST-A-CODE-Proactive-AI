# J.A.C. Setup Guide

[中文安装指南](#中文安装指南)

## English

Option B was approved on 2026-10-06. M0 has been accepted by the user with the 30-minute soak explicitly waived. This guide provisions the isolated M0/M1 backend, probes and production Gateway CLI/GUI. `setup_new_computer.py --only gateway` (compatible alias: `--only m0`) installs Python 3.11 dependencies including PySide6 and verifies client/GUI imports without opening devices. The installer defaults to Gateway; all/pip/verify also use this current dependency set. Old MiniCPM runtime modes are removed.

M0 requires Python 3.11, CMake, Xcode Command Line Tools, an external GGUF model directory and the two repositories pinned in [backend.lock.json](../backend.lock.json). It uses C++ Metal for inference and a Python Gateway / Worker for orchestration. No NVIDIA machine or speaker-embedding extraction is needed.

The Chinese section below contains the same commands, one per block. Run environment commands from the J.A.C. root. Clone both backend repositories outside J.A.C.; replace placeholder paths with absolute paths.

Create `.cache/m0/venv` and install `requirements-m0.txt`. Use `start_m0_backend.py --preflight --verify-sha` before launching. The launcher binds all ports to loopback, disables upstream session recording, rejects occupied ports and terminates only its own children on Ctrl+C.

The Gateway URL is `ws://127.0.0.1:8006/v1/realtime?mode=video`. Inputs are 16 kHz mono float32 PCM Base64; outputs are 24 kHz mono float32 PCM Base64. The combined engine patch applies the TTS reference using two pinned CPU ONNX frontend models in the external GGUF directory's `voice-frontend/`. Install them with `prepare_native_voice.py --model-dir <GGUF-directory>/voice-frontend --download-models --self-test`; the installer verifies the frontend dependencies and the backend launcher verifies resource hashes. Derived conditions remain in ignored `.cache/voices/`. The client requires an applied-reference hash before opening devices. `--require-audio` checks native output presence; it does not establish perceptual voice similarity. The client supplies the version-specific audio/system prompt suffix; do not reuse it with another backend without checking its template.

The file probe proves protocol and native-audio transport only. The pinned Gateway limits video sessions to 300 seconds and audio sessions to 600 seconds. The M1 production client now rotates video sessions after 240 input seconds, with visible capture pauses and re-injection of the reference voice, supplied confirmed context and bounded assistant history. Untranscribed user speech is not reconstructed.

The 40-second live run, ten rapid lifecycle cycles and two 225-second live sessions passed. The user confirmed satisfactory interaction and accepted M0 without the remaining soak. Original reports retain `soak_30min_verified=false` because that duration was not measured; it no longer blocks migration.

Run `main.py --gateway --gui`, click Start Backend and wait for readiness, then start voice. Alternatively, start the pinned backend separately and run `main.py --gateway --gui` in the Python 3.11 environment. GUI device permission is enabled by default per the user request; capture begins only after clicking Start Voice. Use headphones. Console/probe device runs still require --consent-devices. `main.py --gateway --consent-devices` runs the console client. The Gateway GUI imports no torch/PyAudio/Whisper/YOLO. All runtime entries use Gateway; --gateway remains a compatible optional flag.

`verify_soak_duplex.py --consent-devices` measures 1,800 input seconds across eight 225-second video sessions while keeping the same backend running. It samples local health endpoints and process RSS, and saves aggregate/per-session counts even on failure. Devices restart between sessions; cross-session context restoration is not tested.

Publishing policy: the template `voices/silverwalf_voice.wav` is authorized for GitHub. Model binaries, runtime recordings and the local `brainstorming_projectPLAN/` architecture directory are excluded; cloning the repository does not include that directory.

## 中文安装指南

发布边界：模板 `voices/silverwalf_voice.wav` 已获准推送；模型、实际测试录音录像和本机 `brainstorming_projectPLAN/` 架构目录不提交、不推送。架构目录只在本机维护，克隆 GitHub 仓库不会取得该目录。

方案 B 已于 2026-10-06 确认。M0 已由 bo s s 接受并免做 30 分钟长测，当前进入 M1。独立 Python 3.11 环境已支持后端、探针及生产 Gateway CLI/GUI；`requirements-m0.txt` 新增已验证的 PySide6 6.11.2，安装器推荐 `--only gateway`（兼容 `--only m0`），安装后自检客户端、GUI 与后端控制依赖，不打开设备。现有 `.venv` 保留但不用于当前主程序；默认安装 Gateway，根依赖清单共用固定版本，旧 MiniCPM 运行模式已移除。

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
git -C /absolute/backend/llama.cpp-omni apply /absolute/JAC/new_computer_download/patches/engine-loopback-native-voice.patch
```

该组合补丁让内部引擎遵守 --host，并在会话初始化时应用指定音色的原生合成条件。固定版本自动启用 OpenSSL 时会构造需要证书的 SSLServer；本机内部链路使用 HTTP，因此显式关闭 LLAMA_OPENSSL。

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
  voice-frontend/campplus.onnx
  voice-frontend/speech_tokenizer_v2_25hz.onnx
```

已有文件优先通过 SHA256 复用。若使用 Hugging Face CLI 下载，需先在专用环境安装 `huggingface_hub`；不要因此替换主程序依赖。

```bash
hf download openbmb/MiniCPM-o-4_5-gguf --revision db25077c33951fe163b42986fba0132e279872a2 --include 'MiniCPM-o-4_5-Q4_K_M.gguf' 'audio/*.gguf' 'vision/*.gguf' 'tts/*.gguf' 'token2wav-gguf/*.gguf' --local-dir /absolute/models/MiniCPM-o-4_5-gguf
```

国内网络可为同一命令设置 `HF_ENDPOINT=https://hf-mirror.com`，但下载后必须按 lock 的 SHA256 校验，不能仅依赖文件名或镜像元数据。

**原生音色（2026-10-07 修复）**：组合补丁将指定 TTS 参考音转成本地声纹、token 与 mel 条件，加载到 C++ Token2Wav。依赖新增 onnxruntime 1.24.4 / kaldi-native-fbank 1.22.3，已纳入 Gateway 安装及导入自检；不加载完整 torch 模型。参考音要求 1–30 秒、单一清晰声音。先完成本节的独立资源安装与自检；模型只存放在仓库外。运行时不下载、不发云端，失败不默默使用默认音色。

在 GGUF 下载阶段之后运行以下安装命令。`--model-dir` 应为上文实际 GGUF 目录下的 `voice-frontend` 子目录；两个官方 ONNX（合计约 524MB）的 revision / SHA256 已记录于 backend.lock.json，国内 HTTPS 镜像失败回退官方并验证哈希。自检用项目参考 WAV 生成条件，不开启或播放设备。

```bash
.cache/m0/venv/bin/python new_computer_download/prepare_native_voice.py --model-dir /absolute/models/MiniCPM-o-4_5-gguf/voice-frontend --download-models --self-test
```

已安装旧 loopback 补丁的后端，应在确认源码只有该旧补丁时撤销它，再应用新的完整组合补丁并重新构建；不同时叠加两份补丁、不重置未知源码修改。启动器会核对最终完整 diff，不能只更新 Python 客户端。新资源安装完成后再执行第 6 节预检。官方 GGUF 文件与哈希保持不变。

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

客户端在开启设备前验证 `session.created.voice_conditioning` 中的参考 PCM 哈希；旧二进制未提供确认时明确拒绝。生产 Gateway URL 仍为 video 模式，文件回放验证该路径时应附测试 JPEG。`--require-audio` 只检查原生语音存在，听感相似度需另外试听；技术验证记录见 CHANGELOG 附 A4。

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

M1 采集层、原生播放、GUI 预览与手动启停已于 2026-10-07 获 bo s s 真机验收，反馈为「这次完全正常」。专项回归 57 项通过；这不增加未测的长测时长或循环次数。M2b 已接入并行 VAD/Whisper 与 Gateway 明确系统任务，见第 12 节；原生结果播报已接入，ChromaDB 和 OpenClaw 仍待接入。Qwen 继续使用 LM Studio 127.0.0.1:12345；基础听看说无需加载 Qwen 或启动云端。

### 9 M1 生产入口

在独立 Python 3.11 环境打开 Gateway GUI，可直接在界面中启动后端：

```bash
.cache/m0/venv/bin/python main.py --gateway --gui
```

界面为 Qt 绘制的深蓝渐变科技风格，摄像头保持真实比例且无黑边，对话区连续显示回复，顶部单个状态提示，连接日志单独折叠。点击顶部「启动后端」，等待后端就绪，再戴好耳机并点「启动语音」。设备同意已按 bo s s 要求默认开启，控制区不再显示复选框；GUI 打开或启动后端本身不启用采集。「调节参数」按钮展开时高亮、收起时暗色，并有按压反馈。首次需要通过高级区「后端路径设置」选择固定 Demo、已编译引擎和仓库外模型目录，可启用模型 SHA256 校验；本机已有有效路径已保存到忽略的 `.cache/gui/backend.json`，新机器需自己选择，也可设置 `JAC_DEMO_DIR` / `JAC_ENGINE_DIR` / `JAC_MODEL_DIR`。启动期间可取消，停止后端会先结束音视频会话再回收所属三进程，退出窗口同样等待。外部已运行后端显示为「外部后端已就绪」，可直接启动语音，GUI 不会停止该服务。右侧仅保留有效 Gateway 参数：设备、相机编号、麦克风增益、5–10fps 采集、5–240 秒会话轮换、重连次数、本机 Gateway 与参考 WAV，启动前调整、运行中锁定。分辨率固定 640×480、每秒上行最新 1 帧。旧 MiniCPM 与传统入口已删除；`main.py --gui` / `python -m src.omni --gui` 同样可用。

如果仅需终端中的语音交流：

```bash
.cache/m0/venv/bin/python main.py --gateway --consent-devices
```

可加 `--input-device` / `--output-device` 绑定内建麦克风与耳机。默认每 240 秒上行后暂停设备、重建会话并重注入参考音和有限上下文，Ctrl+C 清理设备；后端由独立启动器管理。统计报告在已忽略的 `output/m1/`，不保存媒体或对话文本。

无需设备的文件回放可使用 `--input-file`、可选 `--image-file`、`--session-seconds` 和 `--sessions`；每个会话回放同一测试文件，先补 4 秒启动静音，不将接收到的原生音频播放出来。文件验证不能替代新生产 GUI 的真机听感验收。

### 10 在 VS Code 中启动 GUI

打开 J.A.C. 项目文件夹。按 `Cmd+Shift+P` → `Python: Select Interpreter` → `Enter interpreter path`，选择项目内 `.cache/m0/venv/bin/python`，不要选择系统 `/opt/homebrew/bin/python3.11` 或旧 `.venv`。已选择过解释器的工作区需要手动切换一次，单改 `python.defaultInterpreterPath` 不会替换已缓存的选择。

本机已在忽略的 `.vscode/launch.json` 配置「J.A.C. · Gateway GUI」：明确指定项目解释器、`main.py`、`--gateway --gui`、工作目录与集成终端。在「运行和调试」中选择该配置，按 F5 启动；GUI 默认设备许可，戴好耳机并点击「启动语音」后开始采集。固定后端可通过 GUI 的「启动后端」按钮启动，也可独立运行。

新机器的 `.vscode/` 不随 Git 分发，可按上述解释器选择后使用第 9 节的 GUI 命令；如自行创建调试配置，类型用 `debugpy`，显式设置 `python` 为项目环境并添加 `--gui`。编辑器右上角「运行 Python 文件」不会自动使用 launch.json 的参数，直接运行不带 `--gui` 的 main.py 会进入终端模式。系统 Python 报缺少 numpy 时，先切换解释器，无需在系统环境重复安装依赖。

### 故障排查

- 停止后画面两侧残留、第二次启动报 RuntimeError / session_failed：2026-10-07 已完善整控件清屏与关闭握手，bo s s 已确认真实 GUI 恢复正常。旧客户端会过早断链；固定版本上游还会在 C++ 清理完成前报告关闭。手动停止现先停采集、保留 8 秒接收尾窗，等待关闭确认与连接关闭，再冷却 1 秒；GUI 显示「停止中…」并禁用启动，约 9 秒后恢复，超时则提供「重试停止」。退出窗口同样等待清理，不强制结束进程。旧版本升级时须先重启已受影响的后端，再按第 9 节重开 GUI；无需重装依赖或模型。该收尾窗口是固定版本兼容处理，不代表上游竞争或长期稳定性已验证。
- 模型 SHA256 不符：保留原文件，检查是否有匹配的备份或重新下载固定 revision。禁止仅改文件名绕过校验。
- queue_done 超时：检查 Worker 已注册、三个健康接口正常，以及 output/m0 下的日志。
- 无 audio 增量：原生 TTS 验收尚未通过，检查全部 Token2Wav / TTS 模块和参考音。
- 中文字体异常：DOCX 使用显式东亚字体；如 Windows 缺少字体，安装对应字体后再检查页面渲染。Python 和 Markdown 文件统一 UTF-8。
- 依赖冲突：当前运行环境为 Python 3.11 `.cache/m0/venv`；根 `requirements.txt` 直接引用 `requirements-m0.txt`，`requirements_fixed.txt` 是历史组件快照。原生音色前端新增 onnxruntime / kaldi-native-fbank；已有机器须更新当前清单并安装 voice-frontend 资源、重新应用组合补丁和构建后端。安装器默认 Gateway，原 `.venv` 保留但不作为主程序环境。

### 11 M2a：独立 Qwen 只读任务与文件验证

This independent check needs LM Studio 0.4.8+ and the exact loaded instance `qwen/qwen3.6-35b-a3b` at `http://127.0.0.1:12345`. It uses the existing Python 3.11/httpx environment, opens no devices, and does not connect Gateway voice escalation. Reports stay under ignored `output/m2/qwen/`.

在 LM Studio 0.4.8+ 加载 `qwen/qwen3.6-35b-a3b`，启用本机 `http://127.0.0.1:12345`。安装器已增加大脑模块导入自检，无新依赖或模型下载阶段；不能用“清单第一项”替代目标已加载实例。探针仅查询时间、电池、CPU、内存，结果是本机 UTF-8 Markdown 文件，不开启设备。

从项目根执行默认三项验收（macOS/Linux）：

```bash
.cache/m0/venv/bin/python verify_toolcall.py
```

显式只读任务示例，仍仅允许系统状态查询：

```bash
.cache/m0/venv/bin/python verify_toolcall.py --url http://127.0.0.1:12345 --model qwen/qwen3.6-35b-a3b --task "查询电脑状态并生成中文报告"
```

Windows 的独立大脑入口使用其项目解释器（不代表 Metal 后端已在 Windows 验收）：

```powershell
.cache\m0\venv\Scripts\python.exe verify_toolcall.py
```

成功以退出码 0 和 `verification.json` 的 `passed=true` 为准；失败退出码 2，失败报告保留，不能只看模型回复。默认三项还核对回答保留真实工具数值；自定义 `--task` 只证明执行/交付，数值核对标为未做。Ctrl+C 取消在途 HTTP 与后续工具/文件发布，已开始的只读系统查询按自身超时返回。单次 HTTP 上限 90 秒，整个任务 120 秒，最多 4 轮。日志/报告不加入 Git，未请求任何模型或测试媒体上传。

普通聊天 SSE 与独立 agent 文件输出分别处理；不把思考正文当答案、截断输出当成功，也不重复生成已经完成的回答。下一阶段才接并行转写、用户任务来源校验及 GUI/Gateway 升级；当前不能直接对语音助手使用这项能力。固定后端、音色补丁、原模型哈希和权威目标架构不变。

### 12 M2b：并行转写与 Gateway 系统任务

The default Gateway install now prepares the pinned multilingual Whisper small resources outside the repository and runs a CPU process self-check. Runtime transcription is offline. CPU VAD/Whisper and Qwen run beside the existing audio stream; the CLI/GUI open no second microphone. The user confirmed successful live voice-task text results on 2026-10-08. Native speech feedback is now connected through the updated, registered C++ patch; rebuild existing installations before enabling tasks.

The later battery-response fix is client-only: restart the GUI. With transcription enabled, ordinary replies wait for classification; system requests use verified task speech. Failed or unmatched recognition explicitly reports no query. The GUI and report expose actual tool arguments, query time and raw evidence. Conversation gains transcription latency; input keeps streaming, while a transcription fault pauses unverified speech until restart.

当前安装器默认安装 CPU 转写依赖，并下载锁定的 Whisper small 四项资源（约 487MB，仓库外 `~/.cache/jac/models/whisper-small`），逐文件 SHA256 和纯 CPU 自检通过才报成功。运行期不下载，不发送云端。下载采用 HTTPS 镜像/官方回退，持续过慢会切源，失败保留明确结果。根依赖和固定独立清单已同步，历史 requirements_fixed 不用于主程序。

从项目根更新安装并自检：

```bash
.cache/m0/venv/bin/python new_computer_download/setup_new_computer.py --only gateway
```

仅安装依赖、暂不准备模型时：

```bash
python3.11 new_computer_download/setup_new_computer.py --only gateway --skip-transcription-model
```

只准备或修复转写资源时：

```bash
.cache/m0/venv/bin/python new_computer_download/prepare_transcription.py --download-models --self-test
```

新机器可指定仓库外目录，GUI 参数中填写同一目录，或设置 JAC_WHISPER_MODEL_DIR。默认 GUI 已启用“本地转写与查询任务”；缺少/损坏模型明确拒绝开始采集，可先关闭该项运行基础听看说。LM Studio 要按第 11 节加载精确 Qwen，默认本机 12345；大脑不可用时任务明确失败，感知继续运行。运行中设置锁定，下次启动生效。

启动 GUI：

```bash
.cache/m0/venv/bin/python main.py --gateway --gui
```

GUI 内先启动后端再启动语音，戴耳机。第一轮使用明确短句：“查询电脑状态”“查一下电池电量”“查询本机当前时间”“生成一份系统状态报告”。界面应出现独立用户转写、大脑任务状态和可打开的真实报告。报告含该条任务转写及实际工具证据，仅在本机 output/m2/qwen/；一般转写只在内存，音视频不落盘。完成的单项查询会通过现有原生音色播报实际工具证据，综合报告播报完成提示。完整结果仍保留在界面和报告；文件探针只收不播放，听感单独验收。

2026-10-08，bo s s 反馈“测试成功，能正常输出文字结果”，真机语音任务文字结果按用户反馈验收通过。此次未新增具体指令、次数、时长、延迟或报告内容核对记录，音色听感仍单独确认。原生任务结果播报已接入；依赖版本与模型不变，旧后端需按下方步骤更新补丁并重新编译。

旧安装升级原生播报：先在 GUI 点击“停止后端”并等待清理完成，再关闭 GUI。已保存后端路径的机器从项目根执行：

```bash
.cache/m0/venv/bin/python new_computer_download/update_native_backend.py --build
```

更新器核对固定 commit，只接受登记的旧音色补丁、当前完整补丁或干净源码。陌生改动不会覆盖；新补丁应用失败恢复原补丁。它不改模型或下载依赖。未保存 GUI 路径时先通过“后端路径设置”选择引擎目录；新机器仍按第 4 节应用最新完整组合补丁构建。构建后重新打开 GUI，先启动后端再启动语音。旧二进制缺少 `task_speech=1` 确认时，任务模式在开启设备前明确拒绝启动。

原生结果播报只排一个短句，等待助手聆听、用户静音和播放空闲；等待 20 秒或已发 30 秒超时、会话剩余不足 15 秒时跳过，文字报告保留。任务 PCM 仍为 24k float32，复用原播放流及同一参考音；停止/重连取消剩余任务音频。GUI“结果音频已接收”确认字面文本及音频传输，不等于人工听感通过。

2026-10-08 电池抢答修复只修改客户端，不需要再次编译后端：关闭并重新打开 GUI。启用“本地转写与查询任务”，戴耳机，等正在播放的语音结束后说“查一下电池电量”。界面应先显示确认的用户原话，再显示“大脑 · 工具已实际查询”、`get_system_info`、查询时间及真实百分比，随后生成报告并播报证据。只出现 J.A.C. 自称“正在查询”或一个数字不能证明工具已调用。

普通回复在 VAD/Whisper 判定前有界暂存，系统查询丢弃模型抢答；普通聊天判定后恢复，因此回复会增加转写等待。识别成其他电池说法、播放重叠或识别失败会显示“未执行查询”，请按界面原话/原因复验，程序不会自动把同音错字改成工具指令。转写异常时感知上行保持，未核验播报暂停，请停止后重启。已播放的设备块无法收回。工具原始证据和调用参数/时间同时保存在报告，可点击“打开最新报告”核对。

2026-10-09 bo s s 已确认电池实际查询和原生结果播报正确。短 VAD 候选不足 300ms 有声时静默过滤，不再反复显示“任务未执行：没有听清”；完整句的识别失败单独显示“语音 · 未确认：没有听清，请重复”，连续失败十秒最多一次，成功转写后重置。它表示另一段输入没被确认，并不表示已经运行的查询失败。有效音频仍完整保存到有界内存供转写，工具白名单、置信度和禁猜限制没有放宽；无需新依赖或后端编译，重开 GUI 即可使用。音色相似度/长时稳定性没有由该次反馈验证。

仅基础听看说的完整命令：

```bash
.cache/m0/venv/bin/python main.py --gateway --gui --no-transcription
```

终端设备入口仍需显式授权：

```bash
.cache/m0/venv/bin/python main.py --gateway --consent-devices --transcription
```

Windows 使用 .cache\m0\venv\Scripts\python.exe 替换解释器；CPU 子进程强制 UTF-8 管道，Qt 保留中文字体回退。但本轮仍只在 Apple Silicon 实跑，不宣称 Windows Metal 后端已验收。

本机安装器已实跑通过：依赖/主进程隔离检查、四项资源 SHA256 与 CPU 就绪自检。真实固定后端 60 秒合成文件联调交付唯一系统报告，设备未打开；这不代表真机识别质量或长时性能已验收。

VAD 在线程以 30ms 判定，至少 300ms 有声和 600ms 句末静音；12 秒长句不会截成多个指令。低置信度、播放期间讲话、过期结果、否定/转述或未支持的复合任务不执行；忙碌时不排队旧任务。停止/重连会取消待完成任务，请会话恢复后重新说；旁路积压/异常提示重启，持续上行不会靠丢弃用户语音追赶。


### 13 通用联网搜索与独立验收

本轮复用既有 `httpx==0.28.1` 和标准库，没有新增依赖、模型、密钥或后端编译步骤；安装器离线导入自检新增 WebSearchClient 与 verify_web_search。仅重开 GUI 即可加载代码。LM Studio 继续加载第 11 节的精确 Qwen，Whisper 按第 12 节准备。

GUI 启动后启用“本地转写与查询任务”，戴耳机，等助手播放结束，再说“上网搜索上海明天的天气”或“联网查询 Python asyncio 官方文档”。应看到确认的用户原话、真实 search_web 查询、来源链接和正文读取状态，再生成报告和原生结果音频。原生播报技术完成确认不替代人工听感。天气请指定城市/日期；其他信息请使用“上网搜索…”或“联网查询…”的明确说法。

独立网络检查在项目根目录执行，不启动设备或 Qwen：

```bash
.cache/m0/venv/bin/python verify_web_search.py --query "上海明天天气" --search-only
```

真实 Qwen + 搜索 + 文件闭环另执行：

```bash
.cache/m0/venv/bin/python verify_web_search.py --query "上海明天天气"
```

退出码 0 与 output/m2/search/verification.json 的 passed=true 表示搜索、至少一页正文、相关来源摘录和对应阶段文件交付通过（search-only 不校验回答）；不代表已核实所有来源的事实真实性/时效/相关性，也不代表真机听感。来源未读、查询无结果或网络验证失败明确显示失败，Ctrl+C 取消在途网络/大脑与报告发布。生产联网报告保存在 output/m2/qwen/，仅用户点“打开最新报告”才打开文件。

默认国内必应 RSS、失败再试固定全球入口，无密钥。入口并非保证长期稳定的公开 API；国内网络或来源网站限制会造成失败，不自动绕验证码，不用过期缓存冒充实时结果。来源只读静态 HTML/文本，不支持需要登录/JavaScript 才能显示的内容或 PDF。禁止私网/本机/非标准端口，IPv4 DNS 固定公网 IP 并保持证书验证，环境代理不继承；DNS 被代理改为私网假 IP 时会拒绝访问。最多 5 条链接、3 页正文、25 秒网络总时限、单页 6 秒、512KiB 字节及 3500 字正文上限。

仅本句查询关键词和来源页面 GET 出网，不发送历史对话、原始音视频或参考音。固定规则分隔天气词/文档词，原话与实际搜索词均记录；目标天气日期与英文具体词先筛选候选。Qwen 只选择已读段落编号，程序逐字核对再交付，首版不接受自由生成的联网事实/链接/发布时间，也不提供自由改写/翻译。程序保留原文不等于确认网页说法真实或相关；实时问题仍需核对来源的城市、日期、发布时间。报告与合成验证产物由既有 output/ 忽略规则排除。
