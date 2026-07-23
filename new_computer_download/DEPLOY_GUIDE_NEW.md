# J.A.C. 新电脑部署指南（Windows / macOS / Linux 通用）

本指南配合 `new_computer_download/` 目录下的「一键依赖补全」工具使用，目标是在一台**全新的电脑**上把 J.A.C. 跑起来。

---

## 0. 先想清楚：迁移 vs 全新下载

你有两种把项目弄到新电脑的方式，**可组合**：

| 方式 | 做法 | 适合 |
|---|---|---|
| **A. 整目录拷贝（最省事）** | 把旧电脑上整个 `J.A.C/` 项目文件夹（含 `models/`、`voices/`、已装好的环境）拷到新电脑 | 同平台迁移、有移动硬盘/U 盘、不想重新下 10GB+ 模型 |
| **B. 只拷代码 + 本工具补全** | 只拷源码与脚本，模型/依赖用 `setup_new_computer.py` 重新下载 | 跨平台（Win→Mac）、不想拷大文件 |

> 推荐：**代码 + 配置 + `voices/` 一定带上**；`models/` 很大，可用方式 B 重新下载（工具会自动跳过已存在的文件）。
> 注意：`voices/`（声音克隆参考音）和 `yolov8n.pt` 不自动从网上下，要么随代码拷贝，要么让工具预取（YOLO 会，voices 需手动拷）。

---

## 1. 前提条件（新电脑先装好这些）

### 通用
- **Python 3.10 或 3.11**（推荐 3.10.11 兼容性最好；3.12/3.13 多数包已有 wheel，但个别可能需编译）。
  - Windows：官网 https://www.python.org/downloads/ ，安装时务必勾选 **Add Python to PATH**。
  - macOS：建议 `brew install python@3.10`；或直接装官方 pkg。
  - Linux：`sudo apt install python3.10 python3.10-venv`（Ubuntu/Debian）。
- **Git**（若从仓库克隆而非直接拷贝文件夹）。
- **磁盘空间**：至少 15–20GB 空闲（大脑 5.6GB + 判断 4.8GB + TTS 2–4GB + 依赖 + 系统包）。
- **内存**：8GB 最低，16GB+ 推荐（48GB 的 MacBook Pro M5 非常宽裕）。

### macOS 额外
- 安装 **Xcode 命令行工具**：`xcode-select --install`
- 安装 **Homebrew**（工具会自动用它装 portaudio/ffmpeg）：
  ```bash
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  ```

### Linux 额外
- 构建/录音依赖由工具用包管理器自动装（`portaudio19-dev` / `python3-dev` / `ffmpeg`）。
- 若以普通用户运行，工具会在需要时自动加 `sudo`，请确保你有 sudo 权限。

### Windows 额外
- 若后续编译报错（pyaudio / llama-cpp-python），装 **Visual Studio 生成工具**：
  https://visualstudio.microsoft.com/visual-cpp-build-tools/ ，勾选「使用 C++ 的桌面开发」。

---

## 2. 一键执行（推荐）

把 `J.A.C/new_computer_download/` 目录放到新电脑上，然后：

**macOS / Linux：**
```bash
cd J.A.C/new_computer_download
bash run.sh
# 或 ./run.sh
```

**Windows：**
```
双击 run.bat
# 或在 CMD/PowerShell 中：run.bat
```

工具会自动完成：
1. 在项目根建一个 `.venv` 虚拟环境（避免污染系统 Python、免 sudo），并切进去重跑；
2. 升级 pip，安装全部 Python 依赖（**按平台过滤**，Windows 专属包不会在 Mac/Linux 上误装）；
3. 装系统级依赖（macOS: `brew install portaudio ffmpeg`；Linux: `apt/dnf/pacman` 装 portaudio/ffmpeg）；
4. 把跨平台的 `ffmpeg` 可执行文件放到项目根（Windows 为 `ffmpeg.exe`，Mac/Linux 为 `ffmpeg`），`main.py` 能直接找到；
5. 下载模型权重（大脑 / mmproj / MiniCPM-o / Qwen3-TTS / YOLOv8）；已存在则跳过；
6. 自检关键模块、ffmpeg、模型是否就绪。

完成后按提示激活虚拟环境，再运行 `python main.py` 即可。

### 常用参数
```bash
bash run.sh --skip-models        # 已从旧机拷了整个 models/，跳过模型下载
bash run.sh --include-big        # 连 35B 备用大模型也下（默认跳过，省 ~11.6GB）
bash run.sh --torch cuda         # Linux/Windows 装带 CUDA 的 torch（有 N 卡时）
bash run.sh --ts-size 0.6B       # 下载更小的 TTS 模型（约 2GB）
bash run.sh --insecure           # 模型下载关闭 SSL 校验（仅限被代理/防火墙拦截的内网，有中间人风险）
bash run.sh --no-venv            # 不建虚拟环境，直接装进当前 Python
bash run.sh --dry-run            # 只打印会做什么，不改动任何东西
```
（Windows 把 `bash run.sh` 换成 `run.bat` 即可，参数写法相同。）

---

## 3. 模型来源与「需核实」清单

工具的下载清单在 `new_computer_download/models_config.json`，**请在新电脑上按需修改**。默认值如下（⚠️ 仓库名/文件名可能随社区更新变化，下载失败时先来这里核对）：

| 模型 | 默认 HuggingFace 仓库 | 文件 | 必需？ |
|---|---|---|---|
| 大脑 Qwen3.5-9B | `Qwen/Qwen3.5-9B-GGUF` | `Qwen3.5-9B-Q4_K_M.gguf` | ★ 必需 |
| 多模态投影 | `Qwen/Qwen3.5-9B-GGUF` | `mmproj-Qwen3.5-9B-BF16.gguf` | 否（llama_cpp 后端才要） |
| 判断引擎 MiniCPM-o | `openbmb/MiniCPM-o_2_6-gguf` | `MiniCPM-o-4_5-Q4_K_S.gguf` | 否（主动判断才要） |
| 备用 35B | `Qwen/Qwen3.6-35B-A3B-GGUF` | `*.gguf` | 否（默认跳过） |
| Qwen3-TTS | `Qwen/Qwen3-TTS-12Hz-1.7B-*` | clone/custom/design + Tokenizer | ★ 必需 |
| YOLOv8n | GitHub Release 直链 | `yolov8n.pt` | 否（自动下） |

> 若某个 GGUF 仓库名/文件名对不上，工具会提示「未列出/下载失败」而**不会中断其余下载**。此时：
> 1. 到 https://huggingface.co 或 https://hf-mirror.com 搜索正确的仓库；
> 2. 改 `models_config.json` 里对应的 `repo_id` / `files`；
> 3. 重跑 `bash run.sh --only models`。

> 也可用 **ModelScope** 国内源手动下载（需先 `pip install modelscope`）：
> ```bash
> modelscope download --model Qwen/Qwen3-TTS-12Hz-1.7B-Base --local_dir ./models/qwen_tts/Qwen3-TTS-12Hz-1.7B-Base
> ```

---

## 4. 手动回退（工具失败时用）

### 4.1 Python 依赖（手动）
```bash
# 用清华镜像
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
```
> ⚠️ 直接 `pip install -r requirements.txt` 在 **macOS/Linux 会失败**（里面含 `pywin32`、`mlx-vlm` 等平台专属/构建包）。手动时请改用工具，或对 `requirements.txt` 做平台过滤后再装。

macOS 单独注意：
```bash
brew install portaudio
pip install pyaudio
```
Linux 单独注意：
```bash
sudo apt install portaudio19-dev python3-dev ffmpeg
```

### 4.2 ffmpeg（手动）
- macOS：`brew install ffmpeg`
- Linux：`sudo apt install ffmpeg`
- Windows：下载 https://www.gyan.dev/ffmpeg/builds/ ，把 `ffmpeg.exe` 放到项目根目录（与 `main.py` 同级）。

### 4.3 模型（手动）
参考第 3 节的仓库，用 `huggingface-cli` 或 `modelscope` 下载到对应目录；或用现成的 `download_models.py`（仅 TTS）：
```bash
python download_models.py                 # 默认 1.7B-Base + 分词器
python download_models.py --source modelscope
```

---

## 5. 网络问题排查（国内网络常见）

- **Qwen3-TTS / 模型下载报 `CERTIFICATE_VERIFY_FAILED` / `CRYPT_E_REVOCATION_OFFLINE`**
  - 工具默认用系统 `curl` 并加 `--ssl-no-revoke`（跳过 Windows 证书吊销检查），多数情况直接成功。
  - 仍失败：`bash run.sh --insecure`（关闭 SSL 校验，**仅限可信内网**，有中间人风险）。
- **pip 装包连接 pypi.org 被掐断（`SSLEOFError` / 超时）**
  - 工具默认走清华镜像；手动时加 `-i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn`。
- **下载大文件中断**（显存/网络抖动导致 5GB 模型只下了一半）
  - 工具用 `curl -C -` **断点续传**，直接重跑同一命令即可从断点继续，不用从头下。
- **HuggingFace 国内镜像（hf-mirror.com）抽风**
  - 工具会自动切回官方 `huggingface.co`；仍不行可挂代理或改用 ModelScope（见第 3 节）。
- **模型文件过大、磁盘不够**
  - 用 `bash run.sh --skip-models` 先跳过，保证程序能装；模型之后再补。或只用最小集：大脑 + TTS。

---

## 6. 运行 J.A.C.

补全完成后：

```bash
# 激活虚拟环境（工具建的 .venv）
# macOS / Linux:
source ../.venv/bin/activate
# Windows (CMD):
#   ..\.venv\Scripts\activate.bat

python main.py
```

首次运行你会看到：
1. 摄像头画面启动；
2. 控制台显示 `J.A.C. - Just A Code (多模态版)`；
3. 视觉检测器初始化（默认 YOLOv8，`conf=0.5`）；
4. 按 **空格** 手动唤醒，或说唤醒词（jac / 杰克 / 你好 jac …）。

> **重要**：项目默认 `backend="lm_studio"`，所以运行前需先启动 **LM Studio** 并在 `127.0.0.1:12345` 加载 `Qwen3.5-9B`（主动判断还需另加载 MiniCPM-o）。否则「思考」会连接失败。
> 纯本地 GGUF 推理可改 `main.py` 中 `LocalBrain(..., backend="llama_cpp")`，并确保对应 GGUF 在 `models/`。

### macOS 隐私授权
首次运行系统会弹窗请求**摄像头 / 麦克风**权限，点「允许」；若误拒，去「系统设置 → 隐私与安全性」里给终端/IDE 授权。

---

## 7. 已知问题 / 注意事项

- `voices/` 声音克隆参考音**不自动下载**，请随代码从旧机拷贝（体积很小，已保留在仓库）。
- `MiniCPM-o` 判断引擎默认开启但需 LM Studio 加载对应模型才真正激活；未加载时自动降级为被动模式，不影响主对话。
- `Qwen3.6-35B` 已下载也未接入代码，默认不下载，勿误读为已启用。
- 本工具安装的是**运行时依赖**，不含 PyInstaller 等构建工具；如需打包请另行安装。
- 若工具报「某包安装失败」，它不会中断整体流程；最后会汇总失败列表，按提示单独 `pip install <包名>` 即可。
- 模型仓库名/文件名可能随官方更新变化，下载失败请优先核对 `models_config.json`（见第 3 节）。
