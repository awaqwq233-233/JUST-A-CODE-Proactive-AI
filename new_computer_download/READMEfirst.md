# READMEfirst — J.A.C. 安装与运行指南 / Setup & Run Guide

> 这是 J.A.C. 的**第一份安装文档**。项目所有模型（大脑 / 判断 / TTS）均由**外部 AI 软件**（LM Studio / Voicebox）管理，**项目内不下载任何本地 GGUF 或 TTS 权重**。
> This is the **first setup doc** for J.A.C. All models (brain / judgment / TTS) are managed by **external AI software** (LM Studio / Voicebox) — the project downloads **no local GGUF or TTS weights**.

[中文安装指南（含国内镜像方法）](#中文安装指南国内网络推荐)

---

## English — Official / Foreign-Network Method

### 1. Prerequisites

- **OS**: macOS (primary dev platform). Windows/Linux code is kept but untested on a Windows dev machine.
- **Python**: 3.10 or 3.11 (recommended for best compatibility).
- **Homebrew** (macOS): for `portaudio` + `ffmpeg`.
  ```bash
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  ```
- **LM Studio**: download from https://lmstudio.ai — hosts the brain model.
- **Voicebox** (macOS primary TTS): the open-source cloning TTS app, REST API at `http://127.0.0.1:17493`.
- A usable **camera** and **microphone** (with OS permission granted).

### 2. Clone & create a virtual environment

```bash
git clone <your-repo-url> JAC && cd JAC
python3 -m venv .venv && source .venv/bin/activate
```

### 3. Install Python dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

> Note: `qwen-tts` is installed as a dependency (used only as the NVIDIA fallback TTS). No local TTS weights are downloaded — Voicebox is the macOS default.

### 4. Ensure FFmpeg is available

```bash
python setup_ffmpeg.py      # copies an ffmpeg binary into the project root if missing
```

Or install via Homebrew: `brew install ffmpeg`.

### 5. (Optional) Pre-download the memory embedding model

The memory subsystem uses `fastembed` with the default model
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`. It can auto-download on
first run, but you may pre-fetch it:

```bash
python new_computer_download/setup_new_computer.py --only embed
```

If download fails, memory automatically falls back to keyword retrieval — the main app still works.

### 6. Load the brain model in LM Studio

1. Open LM Studio → Search / load the model with identifier **`qwen/qwen3.6-35b-a3b`**.
   - The identifier **must match exactly** (code matches it precisely; a different id won't be picked up).
   - It is **natively multimodal** and **thinking is disabled** (`enable_thinking=False`).
2. Start the local server on **`127.0.0.1:12345`** (Developer tab → Start Server).
3. (Optional, for proactive mode) Also load **MiniCPM-o** in LM Studio. J.A.C. enables the judgment engine by default (`JUDGMENT_ENGINE_ENABLED=True`); if MiniCPM-o is not loaded, it auto-enters passive mode.

### 7. Set up the TTS voice in Voicebox

1. Install and launch **Voicebox**.
2. Import `voices/silverwalf_voice.wav` and create a cloned voiceprofile named **`JAC`**.
3. Voicebox auto-reuses the **JAC** profile; if the service is down, J.A.C. falls back to system `say -v Tingting`.

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

### Troubleshooting (EN)

- **Brain connection fails / all thinking errors**: LM Studio must be running with `qwen/qwen3.6-35b-a3b` loaded and the local server started at `127.0.0.1:12345`. The default `backend="lm_studio"`.
- **TTS silent / Voicebox errors**: ensure the Voicebox app is running and reachable at `127.0.0.1:17493`. A wrong proxy can break Voicebox's HuggingFace access — disable problematic proxies for localhost.
- **torch version**: the script installs the correct wheel per platform (macOS = MPS default; Linux/Windows = CPU unless `--torch cuda`). Do **not** mix CUDA/CPU wheels manually.
- **PySide6 install 403 on mirror**: the Tsinghua mirror may return 403 for large packages; the helper auto-retries from official `pypi.org`. You can also run `pip install PySide6 -i https://pypi.org/simple`.
- **fastembed pin**: pin `fastembed==0.5.1`. Newer versions have file mappings that don't match the HF mirror and cause 404s.
- **Microphone / camera permission denied (macOS)**: grant access in System Settings → Privacy & Security → Microphone / Camera for the Terminal / app running the script.
- **Embedding model download fails**: set `HF_ENDPOINT=https://huggingface.co` or use `--insecure` only on a trusted LAN (MITM risk). Memory degrades to keyword search.

---

## 中文安装指南（国内网络推荐）

### 前置条件

- **系统**：macOS（主开发平台）。Windows/Linux 兼容代码保留，但不再保证 Windows 开发机跑通。
- **Python**：3.10 或 3.11（兼容性最佳）。
- **Homebrew**（macOS）：用于装 `portaudio` 与 `ffmpeg`。
  ```bash
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  ```
- **LM Studio**：从 https://lmstudio.ai 下载，承载大脑模型。
- **Voicebox**（macOS 主力 TTS）：开源克隆 TTS App，REST API 在 `http://127.0.0.1:17493`。
- 可用的**摄像头**与**麦克风**（已在系统设置里授权）。

### 方法一：海外网络 / 官方源（最简单）

```bash
git clone <你的仓库地址> JAC && cd JAC
python3 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python setup_ffmpeg.py
# 可选：预下载记忆 embedding 模型
python new_computer_download/setup_new_computer.py --only embed
```

然后按下方「加载模型」三步（LM Studio + Voicebox）操作后运行：

```bash
python main.py
```

### 方法二：国内网络 / 镜像加速（推荐国内用户）

国内访问 pypi.org / HuggingFace 常被墙或极慢，请用镜像。

```bash
git clone <你的仓库地址> JAC && cd JAC
python3 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip

# 用清华镜像装依赖（大包如 PySide6 若镜像返回 403，脚本会自动回退官方源）
pip install -r requirements.txt \
  -i https://pypi.tuna.tsinghua.edu.cn/simple \
  --trusted-host pypi.tuna.tsinghua.edu.cn

# ffmpeg 二进制（同样可走镜像）
python setup_ffmpeg.py

# 可选：预下载记忆 embedding 模型（国内自动走 HF 镜像 hf-mirror.com）
python new_computer_download/setup_new_computer.py --only embed
```

> **关于模型权重**：本项目**不在项目内下载任何本地 GGUF 或 TTS 权重**。大脑走 LM Studio、TTS 走 Voicebox，详见下方「加载模型」。

### 加载模型（两种方法通用）

**① 在 LM Studio 加载大脑**

1. 打开 LM Studio → 搜索并加载标识符为 **`qwen/qwen3.6-35b-a3b`** 的模型。
   - **标识符必须精确匹配**（代码按此 id 精确匹配；填错不会被识别）。
   - 该模型**原生多模态**、且**禁用思考模式**（`enable_thinking=False`）。
2. 在 Developer 页签启动本地服务器，地址 **`127.0.0.1:12345`**。
3. （可选，开启主动模式）在 LM Studio 额外加载 **MiniCPM-o**；J.A.C. 默认开启判断引擎（`JUDGMENT_ENGINE_ENABLED=True`），不加载则自动进入被动模式。

**② 在 Voicebox 配置 TTS 声纹**

1. 安装并启动 **Voicebox**。
2. 导入 `voices/silverwalf_voice.wav`，建立名为 **`JAC`** 的克隆声纹。
3. Voicebox 会自动复用 **JAC** 声纹；服务未启动时自动回退系统 `say -v Tingting`。

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

脚本默认走清华镜像；embedding 模型国内自动走 `HF_ENDPOINT=hf-mirror.com`。若仍报证书/吊销错误，可在可信内网用 `--insecure` 关闭 SSL 校验（有中间人风险，仅限可信局域网）。

### 排错（中文）

- **大脑连不上 / 思考全部报错**：确认 LM Studio 已加载 `qwen/qwen3.6-35b-a3b` 并已在 `127.0.0.1:12345` 启动本地服务器（默认 `backend="lm_studio"`）。
- **TTS 没声音 / Voicebox 报错**：确认 Voicebox App 已运行且能访问 `127.0.0.1:17493`。**本机代理**可能导致 Voicebox 连不上 HuggingFace——请对 localhost 关闭有问题的代理。
- **torch 版本**：脚本按平台装对应 wheel（macOS = 默认 MPS；Linux/Windows = CPU，除非 `--torch cuda`）。不要手动混装 CUDA/CPU wheel。
- **PySide6 在清华镜像 403**：大包可能在镜像返回 403，辅助脚本会自动回退官方源 `pypi.org` 重试；也可手动 `pip install PySide6 -i https://pypi.org/simple`。
- **fastembed 版本**：钉死 **`fastembed==0.5.1`**。新版文件映射与 HF 镜像不匹配会导致 404。
- **麦克风 / 摄像头权限被拒（macOS）**：在「系统设置 → 隐私与安全性 → 麦克风 / 摄像头」给运行脚本的终端/App 授权。
- **embedding 模型下载失败**：可设 `HF_ENDPOINT=https://huggingface.co`，或在可信局域网用 `--insecure`（有中间人风险）。记忆会自动降级为关键词检索，主功能不受影响。

---

## 一键脚本与本项目的关系 / How the one-click script fits

`new_computer_download/setup_new_computer.py` 只负责**环境依赖**（Python 包、系统库、ffmpeg、可选 embedding 模型、外部软件加载指引）。**模型权重永远不在项目内下载**——大脑在 LM Studio、TTS 在 Voicebox、检测 `yolov8n.pt` 首次运行由 `ultralytics` 自动下载。

The `setup_new_computer.py` helper only provisions **environment dependencies** (Python packages, system libs, ffmpeg, optional embedding model, external-software guidance). **Model weights are never downloaded inside the project** — the brain lives in LM Studio, TTS in Voicebox, and `yolov8n.pt` auto-downloads via `ultralytics` on first run.
