# J.A.C. 项目说明（AGENTS.md）

## 项目概述

J.A.C. = "Just A Code"。这是一个**本地优先的多模态 AI 管家原型**，灵感来自 JARVIS：通过摄像头/麦克风感知用户的环境，基于当前场景做推理，用语音 TTS 回应，目标是无需用户显式触发即可主动行动。

长期产品愿景是**打造一个强人工智能管家（Proactive AI Butler）**：

- 它能**主动感知**环境、提前**规划与预警**、在危险发生前发出**警示**、并能**应急接管**。
- 智能眼镜 / MR 等可穿戴终端只是**可选的随身交互外设**，而非项目的主目标或核心愿景——J.A.C. 的本质是 **AI 架构、系统与整套主动服务框架**，可穿戴终端只是承载它的一种形态。
- 主机（当前开发期）：MacBook Pro 级别的本地机器做低延迟的感知与推理。
- 云或局域网服务器承担更重的推理、长期记忆、路由到更大的模型，以及本地算力不足时的外部 API。
- 助手最终要支持主动感知、agent 式任务执行、外部 API 调用、语音/HUD 输出，以及闭环任务循环。

当前代码库是一个 **macOS 优先的 Python 桌面原型**（同时保留 Windows/Linux 的跨平台兼容代码，但不保证在 Windows 开发机上跑通）。

---

## 新架构（2026-10-01 定案，唯一权威基准）

> **结论先行**：bo s s 于 2026-10-01 以 `brainstorming_projectPLAN/10月1日新架构.docx` 定案一套**新架构**，此后所有开发**严格以此为准**。本节是架构的唯一权威定义；`README.md` / `CHANGELOG.md` / `new_computer_download/READMEfirst.md` 均已按本节口径改写。**⚠️ 当前代码尚未按此改造**（见「代码迁移状态」），文档描述的是**目标架构**。

### 一、三层模型架构

J.A.C. 由三层模型协同，按「本地低延迟 → 本地重推理 → 云端长任务」分级：

| 层 | 模型 | 运行位置 | 职责 | 输出 |
|---|---|---|---|---|
| **感知/主动判断层** | `MiniCPM-o-4_5` | 本地 llama.cpp（Metal）`ws://127.0.0.1:8080/duplex` | 全双工主动判断：持续听/说、主动打招呼、判断是否介入；能力不足时向上发起升级 | **语音**（自带音色克隆 TTS） |
| **大脑层** | `qwen/qwen3.6-35b-a3b` | LM Studio `127.0.0.1:12345` | 复杂决策：Function Calling / Tool Use / Agentic Coding（控制电脑） | **文件** |
| **云端层** | 云端 OpenClaw | 服务器（接入 DeepSeek API，有公网地址） | 更复杂、长时间任务 | **文件** |

升级链：`MiniCPM-o-4_5` 解决不了 → 发起调用 `qwen/qwen3.6-35b-a3b` → 仍不够 → 交给云端 OpenClaw。

### 二、输入层

- **音频**：用 **SoundDevice** 实时重采样。**MiniCPM-o 要求麦克风输入必须是 `16kHz / 16bit / Mono PCM`**。
- **视频**：用 **OpenCV** 采集。**不要在主事件循环（asyncio loop / Qt Main Thread）里同步调用 `cv2.read()` 和 JPEG 编码**——应开独立后台线程采集，经线程安全 Queue 以**最高 10fps** 频率发往 WebSocket。超过 10fps 对全双工理解无增益，徒增带宽与 CPU。
  - **强制将摄像头分辨率降为 640×480，帧率限制在 5–10 fps**。
- **上行通道**：通过 **WebSocket** 将 PCM 音频流与 JPEG 视频帧发送给 `ws://127.0.0.1:8080/duplex`（端点具体契约参考 `llama-cpm` 仓库 `examples/duplex` 文档）。
- 至此音视频信号传入后端 `MiniCPM-o-4_5`，进行全双工主动判断。

### 三、后端（MiniCPM-o-4_5 全双工）

- **环境**：后端使用 **python3.11**。
- **定制版 llama.cpp**：使用**面壁官方适配全双工的 llama.cpp 分支** `llama-cpm`：

  ```bash
  git clone https://github.com/OpenBMB/llama.cpp.git llama-cpm
  cd llama-cpm
  make GGML_METAL=1 -j18   # 必须开 Metal；M5 Pro 18 核 CPU 用满编译
  ```

- **GGUF 模型**（INT4 量化，约 5.5GB）：

  ```bash
  huggingface-cli download openbmb/MiniCPM-o-4_5-gguf minicpm-o-4_5-q4_k_m.gguf --local-dir ./models
  ```

- **启动全双工 Server**（严格限制内存，最终精确参数）：

  ```bash
  ./llama-server \
    -m ./models/minicpm-o-4_5-q4_k_m.gguf \
    --host 127.0.0.1 --port 8080 \
    -ngl 999 \          # 全部 offload 到 M5 Pro 20 核 GPU
    -c 4096 \           # 【关键】上下文限 4K，全双工下足以支撑多轮对话
    -t 8 \              # 仅 8 个 CPU 线程预处理，剩余 10 核留给其他服务
    --mlock \           # 锁内存，防 macOS 将模型 Swap 到 SSD 导致卡顿
    --no-mmap \         # 禁用内存映射，提升 Apple Silicon 读取稳定性
    --flash-attn        # 开 Flash Attention，大幅降低 KV Cache 内存占用
  ```

- **⚠️ 强制 Metal（GPU）而非 MPS**：PyTorch 的 MPS 后端对全双工流式生成有 Bug、易 OOM。llama.cpp 底层用 GGML 的 Metal 实现，是 Apple Silicon 上跑大模型唯一稳定且极速的路径。**不要用 PyTorch 加载 HF 权重跑全双工。**
- **协议**：MiniCPM-o 全双工模式**不使用标准 OpenAI Chat API**，而是基于 **WebSocket 长连接**（端点 `/duplex`）。

### 四、音色克隆（语音输出由 MiniCPM-o 完成）

- **离线提取 Speaker Embedding（只做一次）**：在**有 NVIDIA GPU 的机器**（或 Colab / AutoDL 临时租卡）上用 HF 原版模型从样本音频（建议 5~10 秒清晰人声、无背景噪音）提取音色向量，`torch.save` 成 `.pt` 后拷回 Mac：

  ```python
  from transformers import AutoModel, AutoTokenizer
  import torch, torchaudio

  model = AutoModel.from_pretrained('openbmb/MiniCPM-o-4_5', trust_remote_code=True, torch_dtype=torch.float16).cuda()
  tokenizer = AutoTokenizer.from_pretrained('openbmb/MiniCPM-o-4_5', trust_remote_code=True)
  wav, sr = torchaudio.load("my_voice_sample.wav")
  if sr != 16000:
      wav = torchaudio.functional.resample(wav, sr, 16000)
  with torch.no_grad():
      spk_emb = model.get_speaker_embedding(wav.cuda(), sr=16000)
  torch.save(spk_emb.cpu(), "my_custom_voice.pt")
  ```

- **启动时加载自定义音色**：

  ```bash
  ./llama-server ... --tts-speaker-emb ./my_custom_voice.pt
  ```

- **客户端 init 消息激活该音色**：

  ```json
  {
    "type": "init",
    "system_prompt": "你是我的私人助手，请用我的声音和我对话",
    "voice_id": "custom",
    "tts_speaker_emb_path": "./my_custom_voice.pt"
  }
  ```

- ⚠️ 不同版本 `llama-cpm` 对自定义音色的 API 参数名可能略有差异，**必须查阅所编译仓库 `examples/duplex/README.md` 的最新文档**，确认 `--tts-speaker-emb` 与 `voice_id` 的确切字段名。

### 五、输出层

- **语音输出**：由 `MiniCPM-o-4_5` 完成（自带音色克隆 TTS）。
- **文件输出**：由 `qwen/qwen3.6-35b-a3b` 或云端 OpenClaw 完成。

### 六、记忆层

| 组件 | 选型 |
|---|---|
| 向量数据库 | **ChromaDB** |
| Embedding 模型 | **BGE-Small-ZH-v1.5（ONNX INT8 版）** |
| 结构化存储 | **JSON** |

两条**性能铁律**（ChromaDB 在 Apple Silicon 上的坑）：

1. **批量写库**：ChromaDB 频繁 `add` 后内存不会立即释放给 OS。**不要每次对话结束都立刻写库**——在客户端内存缓存最近 5 条摘要，满 5 条或会话结束时批量执行一次 `collection.add()`，再清理缓存，内存曲线才平滑。
2. **限制检索时机**：**不要把 `retrieve_memories` 放进音频流回调**（用户每说一个字就触发一次向量检索，瞬间卡死主线程）。只在两个时刻检索：① WebSocket 连接建立、发 `init` 消息前（用用户开场白检索）；② VAD 检测到用户说完一整句（长静音后）、准备把这句话发给大模型前。

### 七、GUI

配置**图形化 GUI**，包含：**画面预览、设置参数、对话文本记录**。

### 八、DLC（未来可选）

- **小米 miloco 系列**：全屋摄像头监看，将有人的摄像头接入 `MiniCPM-o-4_5`。
- **智能眼镜**：将画面和音频传给 MacBook Pro（随身携带），实现第一视角监看。
- **光波导（AR）/ 空间计算（Vision Pro）**：实现 3D GUI 覆盖，例如 3D 立体导航、主动提醒后方来车等。

---

## 代码迁移状态（旧架构 → 新架构，待改造）

> 本小节如实记录「当前代码」与「新架构」的对应关系。**代码尚未迁移**，以下每一项均为待办改造点；详细差距见 `CHANGELOG.md` 附 A（已按新架构口径改写）。

| 维度 | 新架构（目标） | 当前代码（旧实现） | 迁移动作 |
|---|---|---|---|
| omni 后端 | OpenBMB `llama-cpm` 分支，GGUF **Q4_K_M**，端口 **8080**，端点 `/duplex` | llama.cpp-omni master 分支，GGUF **Q8_0**，端口 **9060**，OpenAI Realtime 风格 `/backend` | 重编译后端 + 重接 WS 协议 |
| 上下文/参数 | `-c 4096 -t 8 --flash-attn` | `-c 8192`（无 flash-attn） | 改启动参数 |
| 语音输出 | MiniCPM-o 原生 speaker embedding（`.pt` + `--tts-speaker-emb`） | Voicebox App（REST `:17493`）+ Qwen3-TTS + 系统 TTS | 移除 Voicebox 依赖，接 o 版自带 TTS |
| 记忆 | **ChromaDB** + BGE-Small-ZH-v1.5（ONNX INT8） + JSON | fastembed + paraphrase-multilingual-MiniLM + 自研 MemoryStore | 重写 `src/memory/` |
| 模型层数 | 三层（o-4_5 + qwen + 云端 OpenClaw） | 两层（o-4_5 + qwen，无 OpenClaw） | 新增云端 OpenClaw 通道 |
| 音频输入 | SoundDevice 重采样 16k/16bit/Mono | PyAudio + WebRTC VAD（Whisper 转写） | 改采集层 |
| 视频输入 | 独立线程 + 640×480 + 5~10fps + Queue | OpenCV 主循环 1280×720，omni 1 帧/秒 | 改采集层 |
| 运行环境 | python3.11 | python3.10/3.11（推荐） | 锁定 3.11 |
| 判断引擎 | 由 MiniCPM-o-4_5 全双工承担（不再单独轮询） | `src/judgment/judge.py` 用 `minicpm-v-4_5` 标准 chat API 每 4s 轮询 | 移除 judge 轮询，并入 o 版全双工 |

## 文档同步硬性规定（Agent 必读）

本仓库有四类**必须随代码改动同步**的文档，任一代码/配置/依赖变更后都必须检查并更新对应条目：

1. `README.md` — GitHub 首页文档（双语：英文在前、中文在后）。每次改动后确保其描述与项目真实状态一致。
2. `AGENTS.md` — 本文件，开发者契约。架构/运行方式/依赖/文件路径变化必须同步。
3. `CHANGELOG.md` — 变更日志。**同时是「文档归口中心」**：原 `codingLOG.md`（差距笔记）与原 `docs/memory/` 四份记忆子文档已于 2026-10-01 全量并入本文件（附 A 差距笔记 / 附 B 记忆契约 / 附 C 记忆测试计划与实现真值 / 附 D 用户指南 / 附 E 隐私说明 / 附 F 运维 Runbook），源文件已删除。每次变更追加一条用户可读的改动说明；涉及差距或记忆子系统的修订，**直接改 `CHANGELOG.md` 对应附录**，不再新建 `docs/` 专项。
4. `brainstorming_projectPLAN/10月1日新架构.docx` — **新架构权威基准（2026-10-01 定案）**。自 2026-10-06 起，Agent 获准编辑和同步 `brainstorming_projectPLAN/`；涉及目标架构的实质性变更时，必须先取得 bo s s 明确确认，并在修改后同步 `AGENTS.md`、`README.md`、`CHANGELOG.md` 与安装文档。

**查看改动时的强制动作**：每当 Agent 需要了解「最近改了什么 / 当前实现状态」，必须优先读取 `CHANGELOG.md` 与 `AGENTS.md`；需要「当前代码与新架构的差距」时读 `CHANGELOG.md` 附 A（已按新架构口径改写）。

配套检查项（每次提交/对话后）：

- 检查 `.gitignore` 是否需要新增忽略（如新增大体积/二进制产物）。
- 检查 `requirements.txt` 是否新增依赖；若有，同步更新 `new_computer_download/` 下的一键安装脚本与 `new_computer_download/READMEfirst.md`。
- `codinglog_by_awaqwq233/` 仍只由 bo s s 手动维护，Agent 不得自动编辑；`brainstorming_projectPLAN/` 已获准由 Agent 按 bo s s 确认的架构决策编辑和同步。
- **开发平台**：当前以 macOS（Apple Silicon）为主开发机，保持跨平台兼容代码；Windows 开发机已不再使用。

## 当前开发状态（2026-10-01 更新）

> 结论先行：**新架构已定案（`10月1日新架构.docx`），项目由「暂停审视」转入「按新架构重新实施」阶段**。**代码尚未迁移**，当前代码基线仍为旧架构（全量回归 134 passed），文档已先行为新架构打底。

- **已定案的新架构要点**（详见上文）：三层模型（MiniCPM-o-4_5 全双工 + qwen3.6-35b 大脑 + 云端 OpenClaw）；omni 后端换 OpenBMB `llama-cpm` 分支（Q4_K_M、`:8080`、`/duplex`）；语音输出改 o 版原生音色克隆（替换 Voicebox）；记忆改 ChromaDB + BGE-Small-ZH-v1.5；输入改 SoundDevice + 640×480 独立线程采集；环境锁定 python3.11。
- **旧架构已定性的 MiniCPM-o 能力天花板**（历史结论，仍有效，作为新架构「为何换后端分支/参数」的动机留存）：
  1. **视觉分辨率仅 grid 1x1 / 64 视觉 token**（服务端日志 `image encoded ... grid: 1x1`），细节视觉问答不可行（真机曾把 boss 本人误判成"电脑桌面"）。
  2. **音频理解（ASR）质量差**：内建麦离嘴远、人声 RMS 仅 0.03~0.065，模型听不清寒暄，会从 prompt 示例里"捡"输出。
  3. **"说/听切换"不稳**：`listen_prob_scale` 1.0 偏沉默、0.8 偏抢话，无稳定工作点；它是开口意愿旋钮（`listen_bias=(scale-1.0)*2.0`），不是 VAD、不影响听清。
- **旧架构已定位但未修的 bug**（待新架构落地时一并处理或作废）：①qwen `enable_thinking=false` 未生效 → `tool_calls` 空、升级通道实际空转；②「真提问被自己拦截」——护栏判据失准（P2）；③无 WebRTC AEC（旧方案见已删除的 `docs/webrtc_aec_plan.md`；新架构戴耳机规避回声，AEC 需求重新评估）。
- ⚠️ **口径提醒**：`CHANGELOG.md` 附 A 中「视觉 token 吃爆 KV → 每 30s 清空 → 复读示例」的旧假设，已被 2026-09-28 的 A1 实验（全程 `--no-video` 零视频帧，幻觉依旧）**证伪**，勿再据此修 bug。

## 重要文件与目录

- `main.py`：多模态运行主入口（旧架构，待按新架构改造）。
- `src/capture/camera.py`：摄像头封装（旧：主循环 1280×720；新架构改独立线程 640×480）。
- `src/analysis/detector.py`：YOLOv8 检测器封装。
- `src/audio/recorder.py`：VAD 麦克风录音（旧：PyAudio + WebRTC VAD；新架构改 SoundDevice）。
- `src/audio/stt.py`：Whisper 语音识别（旧架构非流式转写；新架构音频直通 o 版全双工，STT 仅保留作可观测/校验）。
- `src/audio/`（`voicebox_tts.py` / `qwen_tts.py` / `speaker_factory.py` / `tts.py` / `playback.py`）：旧架构 TTS 链（Voicebox → Qwen3-TTS → 系统 TTS）——**新架构语音输出改由 MiniCPM-o 自带音色克隆承担，此链待移除/降级**。
- `src/brain/llm.py`：`LocalBrain`，多后端大脑层（lm_studio / ollama / llama_cpp / auto）——大脑层 `qwen/qwen3.6-35b-a3b` 在新架构中保留为第二层。
- `src/tools/`：Function Calling 工具层（装手）——新架构中归属大脑层 `qwen/qwen3.6-35b-a3b` 的 Tool Use / Agentic Coding。
- `src/judgment/judge.py`：旧架构的独立主动判断引擎（`minicpm-v-4_5` 每 4s 轮询）——**新架构中该职责由 MiniCPM-o-4_5 全双工承担，此模块待移除**。
- `src/memory/`：旧架构记忆子系统（fastembed + 自研 MemoryStore）——**新架构改 ChromaDB + BGE-Small-ZH-v1.5，此目录待重写**。
- `src/omni/`：旧架构全双工 omni 模块（client.py / tokens.py / voicebox_bridge.py / backfeed.py / router.py / server_launcher.py / prompts.py）——**新架构换 `llama-cpm` 后端 + `/duplex` 协议 + o 版自带 TTS，此目录待重写**。
- `voices/silverwalf_voice.wav`：旧架构 Voicebox 克隆参考音。新架构改走 o 版 speaker embedding（`.pt`），此 wav 仅作提取 speaker embedding 的原始样本。
- `temp/`：运行时临时音频文件。
- `requirements.txt` / `requirements_fixed.txt`：依赖快照。
- `Modelfile`：Ollama 构建定义。
- `new_computer_download/READMEfirst.md`：新机器一键环境搭建与安装指南（已按新架构改写）。
- `setup_ffmpeg.py`：从 imageio-ffmpeg 复制二进制为项目根 `ffmpeg`。
- `verify_model.py` / `verify_toolcall.py`：模型/工具调用校验脚本。
- `codinglog_by_awaqwq233/`：bo s s 的个人记录，仍禁止 Agent 编辑。
- `brainstorming_projectPLAN/`：架构规划目录；自 2026-10-06 起允许 Agent 在 bo s s 确认架构决策后编辑和同步。

通常不参与编辑的大体积/二进制产物：

- `.venv/` / `.cache/` / `__pycache__/`
- 模型二进制（`*.gguf`、`*.pt`、`*.bin`、`*.onnx`）——由外部 AI 软件或模型仓库管理，不进本项目。
- `temp/` 下的运行时音频

## 模型与资产

所有**推理模型均不存放在项目内**，由外部 AI 软件 / 外部仓库管理：

- **感知/主动判断层** `MiniCPM-o-4_5`：GGUF **Q4_K_M**（约 5.5GB），由 OpenBMB `llama-cpm` 分支（Metal 编译）加载，跑在 `ws://127.0.0.1:8080/duplex`。GGUF 由 `huggingface-cli` 从 `openbmb/MiniCPM-o-4_5-gguf` 下载到 `llama-cpm/models/`（仓库外）。
- **大脑层** `qwen/qwen3.6-35b-a3b`：在 **LM Studio** 中加载（`127.0.0.1:12345`），原生多模态、`enable_thinking=False`。代码按模型标识符精确匹配。
- **云端层**：云端 OpenClaw，接入 **DeepSeek API**，有公网地址，处理更复杂/长时间任务。
- **音色克隆**：MiniCPM-o 的 speaker embedding（`.pt`），离线从样本音频（`voices/silverwalf_voice.wav`）在 NVIDIA 机器上提取，`--tts-speaker-emb` 加载。
- **记忆**：ChromaDB 向量库 + `BGE-Small-ZH-v1.5`（ONNX INT8）embedding，权重由 ONNX runtime 加载（仓库外缓存）。
- **物体检测** `yolov8n.pt`：首次运行由 `ultralytics` 自动下载到缓存，不进仓库。

## 设置与运行

完整安装与配置见 **`new_computer_download/READMEfirst.md`**（双语，已按新架构改写）。**运行环境锁定 python3.11**。

快速开始：

```bash
# 1. 创建并激活虚拟环境（python3.11）
python3.11 -m venv .venv && source .venv/bin/activate

# 2. 安装依赖（国内可加清华镜像）
pip install -r requirements.txt

# 3. 编译并启动 MiniCPM-o-4_5 全双工后端（OpenBMB llama-cpm 分支）
git clone https://github.com/OpenBMB/llama.cpp.git llama-cpm
cd llama-cpm && make GGML_METAL=1 -j18
huggingface-cli download openbmb/MiniCPM-o-4_5-gguf minicpm-o-4_5-q4_k_m.gguf --local-dir ./models
./llama-server -m ./models/minicpm-o-4_5-q4_k_m.gguf \
  --host 127.0.0.1 --port 8080 -ngl 999 -c 4096 -t 8 \
  --mlock --no-mmap --flash-attn --tts-speaker-emb ./my_custom_voice.pt

# 4. LM Studio 加载大脑模型 qwen/qwen3.6-35b-a3b，本地服务器 127.0.0.1:12345

# 5. 运行原型（GUI）
python main.py
```

### 运行前置条件

- **MiniCPM-o-4_5 后端**：按上文编译并启动 `llama-cpm` 的 `llama-server`（`:8080`，Metal）。
- **LM Studio**：加载 `qwen/qwen3.6-35b-a3b`（大脑层），`127.0.0.1:12345`。
- **云端 OpenClaw**：处理更长任务时需服务器在跑（可选）。
- 可用的摄像头、麦克风；项目根或 PATH 中的 FFmpeg。
- **运行环境 python3.11**（后端 MiniCPM-o 部分同）。

## 工程指导（未来工作）

- 坚持本地优先设计。唤醒词检测、VAD、基础感知、紧急交互留在本地（MiniCPM-o-4_5 全双工层）。
- 分层升级：本地 MiniCPM-o-4_5 → 大脑 qwen3.6-35b → 云端 OpenClaw，按「简单规则 → 小模型 → 大模型」逐级上抛，避免让大模型决定每个底层路由。
- **采集与推理解耦**：音频用 SoundDevice 实时重采样（16k/16bit/Mono）；视频在独立后台线程用 OpenCV 采集，经线程安全 Queue 以最高 10fps（640×480，5~10fps）上送，**绝不在主事件循环里同步 `cv2.read()` / JPEG 编码**。
- **记忆性能铁律**：ChromaDB 写库走「缓存 5 条摘要 → 批量 `collection.add()`」；检索只在「WS 建立发 init 前」与「VAD 判定说完一句后」两处触发，严禁放音频回调里。
- **语音输出由 MiniCPM-o 承担**：音色克隆用 o 版 speaker embedding（`.pt`），不用外部 Voicebox；文件输出才走大脑层/云端层。
- 谨慎对待隐私与安全：主动常开感知必须包含可见的同意、本地过滤、日志控制，以及在录音/识别人物/向云 API 发送数据前的清晰边界。
- 任何新的 agent/工具执行功能，对高风险操作必须显式白名单与确认。
- 延迟优化优先做流式与流水线：流式 ASR、增量推理、流式/提前 TTS。
- 记忆从 JSON 结构化摘要起步，向量检索用 ChromaDB。

## 已知限制（当前代码，待新架构落地后重新评估）

- 当前代码仍为**旧架构**：omni 走 llama.cpp-omni `:9060`（Q8_0）、语音走 Voicebox、记忆走 fastembed + MemoryStore、判断引擎走 `minicpm-v-4_5` 轮询——**与已定案新架构不一致，属待迁移项**，勿据此误判新架构。
- 运行强依赖外部软件（LM Studio / 后端 llama-server / 可选云端 OpenClaw）。
- 旧架构 STT 仍为 Whisper tiny 非流式；旧架构 TTS 为句子级桥接而非 token 级（新架构语音输出改 o 版自带 TTS 后此限制重新评估）。
- 无 WebRTC AEC（新架构以戴耳机规避回声，AEC 需求待重新评估）。
- 无云端 OpenClaw 集成（新架构新增项，代码未落地）。
