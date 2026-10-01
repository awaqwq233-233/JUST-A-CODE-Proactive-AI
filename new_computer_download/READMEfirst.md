# READMEfirst — J.A.C. 安装与运行指南 / Setup & Run Guide

> 这是 J.A.C. 的**第一份安装文档**，已按 **2026-10-01 新架构**改写。三层模型：**MiniCPM-o-4_5（本地 llama.cpp 全双工 + 语音）→ qwen/qwen3.6-35b-a3b（LM Studio 大脑）→ 云端 OpenClaw（DeepSeek，可选）**。模型权重全部**在项目之外**（外部仓库 / 外部 AI 软件），项目内不下载任何本地 GGUF / TTS 权重。
> This is the **first setup doc** for J.A.C., rewritten for the **new architecture (2026-10-01)**. Three tiers: **MiniCPM-o-4_5 (local llama.cpp full-duplex + voice) → qwen/qwen3.6-35b-a3b (LM Studio brain) → cloud OpenClaw (DeepSeek, optional)**. All model weights live **outside the project** (external repos / external AI software).

[中文安装指南（含国内镜像方法）](#中文安装指南国内网络推荐)

---

## English — Official / Foreign-Network Method

### 1. Prerequisites

- **OS**: macOS (primary dev platform, Apple Silicon). Windows/Linux code is kept but untested on a Windows dev machine.
- **Python**: **3.11** (locked by the new architecture).
- **Xcode Command Line Tools + Homebrew** (macOS): for `portaudio`, `ffmpeg`, and the Metal toolchain.
  ```bash
  xcode-select --install
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  brew install ffmpeg portaudio
  ```
- **LM Studio**: download from https://lmstudio.ai — hosts the brain model.
- A usable **camera** and **microphone** (with OS permission granted).
- **One-time, on a machine with an NVIDIA GPU** (or Colab / AutoDL): extract the speaker embedding for voice cloning (see §7).

### 2. Clone & create a virtual environment (Python 3.11)

```bash
git clone <your-repo-url> JAC && cd JAC
python3.11 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip
```

### 3. Install Python dependencies

```bash
pip install -r requirements.txt
```

> Note: the memory subsystem now uses **ChromaDB** + **BGE-Small-ZH-v1.5 (ONNX INT8)**; `chromadb` and `onnxruntime` are project dependencies. No local TTS weights are downloaded — voice is produced by MiniCPM-o's built-in voice cloning.

### 4. Ensure FFmpeg is available

```bash
python setup_ffmpeg.py      # copies an ffmpeg binary into the project root if missing
```

Or `brew install ffmpeg`.

### 5. Build & launch the MiniCPM-o-4_5 full-duplex backend

The perception / proactive-judgment tier is served by the OpenBMB full-duplex llama.cpp fork (`llama-cpm`), built with Metal:

```bash
# 1) Clone the OpenBMB full-duplex fork
git clone https://github.com/OpenBMB/llama.cpp.git llama-cpm
cd llama-cpm
# 2) Build with Metal (M5 Pro has 18 CPU cores)
make GGML_METAL=1 -j18
# 3) Download the INT4 GGUF (~5.5 GB)
huggingface-cli download openbmb/MiniCPM-o-4_5-gguf minicpm-o-4_5-q4_k_m.gguf --local-dir ./models
# 4) Launch the full-duplex server (with your custom voice, see §7)
./llama-server \
  -m ./models/minicpm-o-4_5-q4_k_m.gguf \
  --host 127.0.0.1 --port 8080 \
  -ngl 999 -c 4096 -t 8 \
  --mlock --no-mmap --flash-attn \
  --tts-speaker-emb ./my_custom_voice.pt
```

- **Metal (GPU), not MPS.** PyTorch MPS is buggy / OOM-prone for full-duplex streaming; GGML's Metal backend is the only stable, fast path on Apple Silicon. Do **not** load HF weights in PyTorch for full duplex.
- Full-duplex uses a **WebSocket long connection** at `ws://127.0.0.1:8080/duplex` — **not** the standard OpenAI Chat API.

### 6. Load the brain model in LM Studio

1. Open LM Studio → load the model with identifier **`qwen/qwen3.6-35b-a3b`** (identifier must match exactly; natively multimodal; thinking disabled).
2. Start the local server on **`127.0.0.1:12345`** (Developer tab → Start Server).
3. (Optional) For long-running tasks, run the **cloud OpenClaw** (DeepSeek API, public address). Local runs work without it.

### 7. Set up the voice (one-time, needs an NVIDIA GPU)

Voice output is done by MiniCPM-o's built-in voice cloning. Extract a **speaker embedding** once on an NVIDIA machine:

```python
from transformers import AutoModel, AutoTokenizer
import torch, torchaudio

model = AutoModel.from_pretrained('openbmb/MiniCPM-o-4_5', trust_remote_code=True, torch_dtype=torch.float16).cuda()
tokenizer = AutoTokenizer.from_pretrained('openbmb/MiniCPM-o-4_5', trust_remote_code=True)
wav, sr = torchaudio.load("my_voice_sample.wav")   # 5–10 s clean voice, no background noise
if sr != 16000:
    wav = torchaudio.functional.resample(wav, sr, 16000)
with torch.no_grad():
    spk_emb = model.get_speaker_embedding(wav.cuda(), sr=16000)
torch.save(spk_emb.cpu(), "my_custom_voice.pt")
```

Copy `my_custom_voice.pt` to your Mac, pass `--tts-speaker-emb ./my_custom_voice.pt` to `llama-server`, and the client `init` message activates it (`voice_id: "custom"`). ⚠️ The API parameter names can vary between `llama-cpm` versions — check `examples/duplex/README.md` in the branch you compiled.

### 8. Run

```bash
python main.py
```

- `q` quit · `SPACE` manual wake · console text input talks directly (bypasses wake word).

### One-click helper

```bash
python new_computer_download/setup_new_computer.py          # all steps (auto venv)
python new_computer_download/setup_new_computer.py --dry-run   # preview only
```

> ⚠️ The one-click helper currently provisions the **previous-architecture** dependencies (e.g. `fastembed`); it is **not yet updated** for ChromaDB / BGE-Small-ZH. Use the manual steps above until it is migrated.

### Troubleshooting (EN)

- **Backend won't start / no full-duplex**: build `llama-cpm` with `GGML_METAL=1`; verify `llama-server` listens on `127.0.0.1:8080`. Do **not** run via PyTorch/MPS.
- **Brain connection fails / all thinking errors**: LM Studio must have `qwen/qwen3.6-35b-a3b` loaded and the server started at `127.0.0.1:12345`.
- **No voice**: `--tts-speaker-emb ./my_custom_voice.pt` must point at a valid `.pt`, and the client `init` must set `voice_id`.
- **ChromaDB memory growth**: batch-write summaries (cache ~5, one `collection.add()`); retrieve only at WS-init and after VAD end-of-sentence.
- **Microphone / camera permission denied (macOS)**: grant access in System Settings → Privacy & Security → Microphone / Camera.
- **Model downloads blocked (CN)**: use `HF_ENDPOINT=https://hf-mirror.com` for the GGUF / HF models.

---

## 中文安装指南（国内网络推荐）

### 前置条件

- **系统**：macOS（主开发平台，Apple Silicon）。Windows/Linux 兼容代码保留，但不再保证 Windows 开发机跑通。
- **Python**：**3.11**（新架构锁定版本）。
- **Xcode 命令行工具 + Homebrew**（macOS）：用于 `portaudio`、`ffmpeg` 与 Metal 编译链。
  ```bash
  xcode-select --install
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  brew install ffmpeg portaudio
  ```
- **LM Studio**：从 https://lmstudio.ai 下载，承载大脑模型。
- 可用的**摄像头**与**麦克风**（已在系统设置里授权）。
- **一次性、需在有 NVIDIA GPU 的机器上**（或 Colab / AutoDL）：提取音色克隆的 speaker embedding（见「配置音色」）。

### 方法一：海外网络 / 官方源（最简单）

```bash
git clone <你的仓库地址> JAC && cd JAC
python3.11 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python setup_ffmpeg.py
```

然后按下方「① 编译后端 → ② 加载大脑 → ③ 配置音色」三步操作后运行：

```bash
python main.py
```

### 方法二：国内网络 / 镜像加速（推荐国内用户）

国内访问 pypi.org / HuggingFace / GitHub 常被墙或极慢，请用镜像。

```bash
git clone <你的仓库地址> JAC && cd JAC
python3.11 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip

# 用清华镜像装依赖（大包如 PySide6 若镜像返回 403，脚本会自动回退官方源）
pip install -r requirements.txt \
  -i https://pypi.tuna.tsinghua.edu.cn/simple \
  --trusted-host pypi.tuna.tsinghua.edu.cn

# ffmpeg 二进制
python setup_ffmpeg.py

# MiniCPM-o-4_5 GGUF 下载（国内走 HF 镜像 hf-mirror.com）
HF_ENDPOINT=https://hf-mirror.com huggingface-cli download \
  openbmb/MiniCPM-o-4_5-gguf minicpm-o-4_5-q4_k_m.gguf --local-dir ./models
```

> **关于模型权重**：本项目**不在项目内下载任何本地 GGUF / TTS 权重**。MiniCPM-o-4_5 的 GGUF 下载到 `llama-cpm/models/`（仓库外），大脑走 LM Studio，详见下方。

### ① 编译并启动 MiniCPM-o-4_5 全双工后端

```bash
# 1) 克隆面壁官方全双工分支
git clone https://github.com/OpenBMB/llama.cpp.git llama-cpm
cd llama-cpm
# 2) 开 Metal 编译（M5 Pro 18 核 CPU 用满）
make GGML_METAL=1 -j18
# 3) 下载 INT4 量化 GGUF（约 5.5GB，国内加 HF_ENDPOINT=https://hf-mirror.com）
huggingface-cli download openbmb/MiniCPM-o-4_5-gguf minicpm-o-4_5-q4_k_m.gguf --local-dir ./models
# 4) 启动全双工 Server（带上你的自定义音色，见 ③）
./llama-server \
  -m ./models/minicpm-o-4_5-q4_k_m.gguf \
  --host 127.0.0.1 --port 8080 \
  -ngl 999 -c 4096 -t 8 \
  --mlock --no-mmap --flash-attn \
  --tts-speaker-emb ./my_custom_voice.pt
```

- **强制 Metal（GPU）而非 MPS**：PyTorch MPS 全双工流式有 Bug、易 OOM；GGML 的 Metal 后端是 Apple Silicon 上唯一稳定且极速的路径。**不要用 PyTorch 加载 HF 权重跑全双工。**
- 全双工用 **WebSocket 长连接**（`ws://127.0.0.1:8080/duplex`），**不是**标准 OpenAI Chat API。

### ② 在 LM Studio 加载大脑

1. 打开 LM Studio → 加载标识符为 **`qwen/qwen3.6-35b-a3b`** 的模型（标识符必须精确匹配；原生多模态、禁用思考）。
2. 在 Developer 页签启动本地服务器，地址 **`127.0.0.1:12345`**。
3. （可选）长时间任务：运行**云端 OpenClaw**（DeepSeek API，公网地址）。本地运行不依赖它。

### ③ 配置音色（一次性，需 NVIDIA GPU）

语音输出由 MiniCPM-o 自带音色克隆完成。在 NVIDIA 机器上一次性提取 **speaker embedding**：

```python
from transformers import AutoModel, AutoTokenizer
import torch, torchaudio

model = AutoModel.from_pretrained('openbmb/MiniCPM-o-4_5', trust_remote_code=True, torch_dtype=torch.float16).cuda()
tokenizer = AutoTokenizer.from_pretrained('openbmb/MiniCPM-o-4_5', trust_remote_code=True)
wav, sr = torchaudio.load("my_voice_sample.wav")   # 5~10 秒清晰人声、无背景噪音
if sr != 16000:
    wav = torchaudio.functional.resample(wav, sr, 16000)
with torch.no_grad():
    spk_emb = model.get_speaker_embedding(wav.cuda(), sr=16000)
torch.save(spk_emb.cpu(), "my_custom_voice.pt")
```

把 `my_custom_voice.pt` 拷回 Mac，`llama-server` 加 `--tts-speaker-emb ./my_custom_voice.pt`，客户端 `init` 消息以 `voice_id: "custom"` 激活。⚠️ 不同版本 `llama-cpm` 的参数名可能略有差异，请查阅所编译分支 `examples/duplex/README.md`。

### 运行

```bash
python main.py
```

- `q` 退出 · `空格` 手动唤醒 · 控制台输入文字直接对话（绕过唤醒词）。

### 一键辅助脚本

```bash
python new_computer_download/setup_new_computer.py            # 全部步骤（自动建 venv）
python new_computer_download/setup_new_computer.py --dry-run  # 仅预览，不改任何东西
```

> ⚠️ 一键脚本目前仍按**旧架构依赖**（如 `fastembed`）安装，**尚未更新**为 ChromaDB / BGE-Small-ZH；脚本迁移前请用上方手动步骤。

### 排错（中文）

- **后端起不来 / 无全双工**：确认用 `GGML_METAL=1` 编译 `llama-cpm`，`llama-server` 监听 `127.0.0.1:8080`。不要走 PyTorch/MPS。
- **大脑连不上 / 思考全部报错**：确认 LM Studio 已加载 `qwen/qwen3.6-35b-a3b` 并已在 `127.0.0.1:12345` 启动本地服务器。
- **没声音**：`--tts-speaker-emb` 必须指向合法 `.pt`，且客户端 `init` 里 `voice_id` 已设置。
- **ChromaDB 内存上涨**：批量写库（缓存约 5 条再一次 `collection.add()`）；检索只在 WS 建立发 init 前、VAD 判定说完一句后触发。
- **麦克风 / 摄像头权限被拒（macOS）**：在「系统设置 → 隐私与安全性 → 麦克风 / 摄像头」给运行脚本的终端/App 授权。
- **国内模型下载失败**：GGUF / HF 模型加 `HF_ENDPOINT=https://hf-mirror.com`。

---

## 一键脚本与本项目的关系 / How the one-click script fits

`new_computer_download/setup_new_computer.py` 只负责**环境依赖**（Python 包、系统库、ffmpeg）。**模型权重永远不在项目内下载**——MiniCPM-o-4_5 的 GGUF 在 `llama-cpm/models/`（仓库外）、大脑在 LM Studio、检测 `yolov8n.pt` 首次运行由 `ultralytics` 自动下载。⚠️ 脚本尚未随新架构更新（仍装旧依赖 `fastembed`），迁移后改为装 `chromadb` / `onnxruntime` + BGE-Small-ZH。

The `setup_new_computer.py` helper only provisions **environment dependencies** (Python packages, system libs, ffmpeg). **Model weights are never downloaded inside the project** — the MiniCPM-o-4_5 GGUF lives in `llama-cpm/models/` (outside the repo), the brain lives in LM Studio, and `yolov8n.pt` auto-downloads via `ultralytics` on first run. ⚠️ The helper is **not yet updated** for the new architecture (still installs `fastembed`); after migration it installs `chromadb` / `onnxruntime` + BGE-Small-ZH.
