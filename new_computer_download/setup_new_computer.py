#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
J.A.C. 新电脑「一键依赖补全」工具
======================================================================

用途
----
把 J.A.C. 项目迁移到一台全新的电脑（Windows / macOS / Linux 均可）后，
运行本工具即可把项目补全到「能直接跑 main.py」的状态：

  1. Python 包依赖（按当前平台自动过滤，避免 Windows 专属包在 Mac/Linux 上装失败；
     已安装的包会自动跳过，只下载/安装缺失项；含 GUI 依赖 PySide6）
  2. 系统级依赖（portaudio 麦克风录音库、ffmpeg 音视频库）
  3. ffmpeg 可执行文件（跨平台放到项目根目录，main.py 能直接找到）
  4. 大模型权重（Qwen3.5-9B 大脑 / mmproj 投影 / MiniCPM-o 判断引擎 / Qwen3-TTS / YOLOv8）
  5. 记忆向量检索的 embedding 模型权重（fastembed + 默认 paraphrase-multilingual-MiniLM-L12-v2，
     用于记忆的语义向量召回；国内走 HF 镜像下载，下载失败自动降级关键词检索，不影响主功能）

网络问题应对（针对国内网络 / 弱网）
------------------------------
  * pip 默认走清华镜像，安装失败自动重试。
  * 模型下载优先用系统 curl；仅 Windows 加 --ssl-no-revoke（绕过证书吊销检查），
    macOS/Linux 自动省略该 Windows 专属选项，避免 "unknown option" 报错、
    --retry 重试、 -C - 断点续传（中断后可继续，不从头再来）。
  * 默认走 HuggingFace 国内镜像 hf-mirror.com；证书仍报错可用 --insecure 关校验。
  * 每个模型独立下载，单个失败不影响其余，最后汇总报告。

用法（任选其一）
----------------
  python setup_new_computer.py                 # 默认：全部补全（自动建 venv）
  python setup_new_computer.py --only pip      # 只装 Python 包
  python setup_new_computer.py --only models   # 只下模型
  python setup_new_computer.py --skip-models   # 跳过模型（假设已从旧机拷贝 models/）
  python setup_new_computer.py --include-big   # 连 35B 备用大模型也下（默认跳过，省 ~11.6GB）
  python setup_new_computer.py --only embed    # 只预下载记忆 embedding 模型
  python setup_new_computer.py --skip-embed    # 跳过 embedding 模型（首次运行 main.py 时自动联网下）
  python setup_new_computer.py --torch cuda    # Linux/Windows 装带 CUDA 的 torch
  python setup_new_computer.py --no-venv       # 不建虚拟环境，直接装到当前 Python
  python setup_new_computer.py --insecure      # 模型下载关闭 SSL 校验（仅可信内网，有中间人风险）
  python setup_new_computer.py --dry-run       # 只打印将做什么，不改动任何东西

说明
----
  * 本工具自身只依赖 Python 标准库 + 系统 curl，可在全新机器上直接跑。
  * 默认会在项目根目录建一个 .venv 虚拟环境并安装进去（避免污染系统 Python / 免 sudo）；
    若你已自己建好 venv 并激活，加 --no-venv 即可直接装进当前解释器。
  * 若已把旧机器的整个 models/ 目录拷过来，工具会自动检测到文件已存在并跳过下载。
  * 每个 Python 包安装前会先探测是否能 import 成功，已装的自动跳过、只装缺失项，
    既省时间也省流量（PySide6 等 GUI 依赖也在其中）。
"""

import argparse
import importlib
import importlib.util  # 显式导入，供 _package_installed 的 find_spec 使用
import json
import os
import platform
import shutil
import ssl
import subprocess
import sys
import urllib.request  # 仅用于无 curl 时的回退下载

# ----------------------------------------------------------------------------
# 路径与平台
# ----------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)          # new_computer_download 的上一级 = 项目根
VENV_DIR = os.path.join(PROJECT_ROOT, ".venv")
IS_WINDOWS = platform.system() == "Windows"
IS_MACOS = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"

# pip 国内镜像（pypi.org 在部分网络下被掐断）
DEFAULT_PIP_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"
DEFAULT_PIP_TRUSTED = "pypi.tuna.tsinghua.edu.cn"
HF_MIRROR = "https://hf-mirror.com"
HF_OFFICIAL = "https://huggingface.co"

# ----------------------------------------------------------------------------
# 依赖清单（按平台过滤，避免 Windows 专属 / 构建专用 / Mac 专属包在别的平台报错）
# ----------------------------------------------------------------------------
# 核心运行时（全平台通用）
BASE_PACKAGES = [
    "numpy",
    "opencv-python",
    "ultralytics",
    "openai-whisper",
    "sounddevice",
    "soundfile",
    "transformers",
    "huggingface_hub",
    "qwen-tts",
    "onnxruntime",
    "fastembed",  # 记忆向量检索的轻量 embedding 生成（基于 ONNX Runtime，CPU 可跑，跨平台）
    "llama-cpp-python==0.3.26",
    "webrtcvad-wheels",
    "pyttsx3",
    "imageio-ffmpeg",
    "requests",
    "tqdm",
    "pyaudio",
    # 现代化 GUI（PySide6 桌面界面）依赖，全平台通用
    "PySide6",
]
# 仅 Windows 需要的包（在 macOS/Linux 上会安装失败，必须排除）
WINDOWS_ONLY = [
    "pywin32",
    "comtypes",
    "pipwin",
    "pypiwin32",
    "pefile",
    "pyreadline3",
]
# 仅 macOS 需要的包（项目当前未实际用到 mlx-vlm，故留空，避免引入不稳定依赖）
MAC_ONLY = []
# 构建/打包专用，运行不需要，排除
BUILD_ONLY = ["pyinstaller", "pyinstaller-hooks-contrib", "mlx-vlm"]

# pip 包名 -> 可 import 的模块名（部分包名与 import 名不一致）
IMPORT_OVERRIDES = {
    "opencv-python": "cv2",
    "openai-whisper": "whisper",
    "qwen-tts": "qwen_tts",
    "llama-cpp-python": "llama_cpp",
    "webrtcvad-wheels": "webrtcvad",
    "imageio-ffmpeg": "imageio_ffmpeg",
    "pywin32": "win32api",
    # PySide6 / 其余包：import 名 == pip 名（中划线转下划线即可）
}


def _import_name_for(pip_spec):
    """把 pip 安装名（可能带 ==/>= 版本约束）映射到可 import 的模块名。"""
    base = pip_spec.split("==")[0].split("<")[0].split(">")[0].split("~")[0].strip()
    return IMPORT_OVERRIDES.get(base, base.replace("-", "_"))


def _package_installed(pip_spec):
    """检测某个 pip 包是否已安装（仅判断存在性，不校验版本）。"""
    try:
        return importlib.util.find_spec(_import_name_for(pip_spec)) is not None
    except Exception:  # noqa: BLE001
        return False


def _filter_missing(pkgs):
    """拆成 (缺失待装, 已安装跳过) 两个列表，并打印逐项检测结论。

    用于实现「只下载/安装缺少的部分」：已 import 成功的包直接跳过。
    """
    missing, present = [], []
    for p in pkgs:
        if _package_installed(p):
            present.append(p)
            log(f"   [已安装] {p}（跳过）")
        else:
            missing.append(p)
    return missing, present


# ----------------------------------------------------------------------------
# 小工具
# ----------------------------------------------------------------------------
def log(msg):
    print(msg, flush=True)


def hr(title=""):
    if title:
        log(f"\n===== {title} =====")
    else:
        log("=" * 60)


def curl_available():
    return shutil.which("curl") is not None


def sudo_prefix():
    """Linux 非 root 时返回 ['sudo']，否则返回空列表。"""
    if IS_LINUX and os.geteuid() != 0:
        return ["sudo"]
    return []


def run_cmd(cmd, check=True, capture=False, **kw):
    log("  $ " + " ".join(str(c) for c in cmd))
    if capture:
        return subprocess.run(cmd, capture_output=True, text=True, **kw)
    return subprocess.run(cmd, **kw)


def download_file(url, dest, insecure, retries=3, timeout=60, quiet=False):
    """用 curl 下载（断点续传 + 重试 + 跳过证书吊销检查）；无 curl 时回退 urllib。"""
    dest = os.path.abspath(dest)
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)

    if curl_available():
        base = ["curl", "-L"] + (["--ssl-no-revoke"] if IS_WINDOWS else []) + [
            "--retry", str(retries),
            "--retry-delay", "2", "-C", "-", "--connect-timeout", str(timeout),
            "-o", dest, url]
        if insecure:
            base.append("-k")
        if quiet:
            base.append("-s")
        rc = subprocess.run(base).returncode
        if rc == 0 and os.path.exists(dest) and os.path.getsize(dest) > 0:
            return True
        # 部分服务器对不存在的文件拒绝 -C -，去掉续传参数再试一次
        if rc != 0 and not os.path.exists(dest):
            retry = ["curl", "-L"] + (["--ssl-no-revoke"] if IS_WINDOWS else []) + [
                "--retry", str(retries),
                "--retry-delay", "2", "--connect-timeout", str(timeout),
                "-o", dest, url]
            if insecure:
                retry.append("-k")
            if quiet:
                retry.append("-s")
            rc = subprocess.run(retry).returncode
            if rc == 0 and os.path.exists(dest) and os.path.getsize(dest) > 0:
                return True
        if rc != 0:
            log(f"   [curl 失败] 退出码 {rc}：{url}")
        return False

    # 回退：Python urllib（无续传，仅适合小文件 / 直链）
    log("   [回退] 未找到 curl，使用 Python urllib 下载（不支持断点续传）")
    try:
        ctx = None
        if insecure:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(url, timeout=timeout, context=ctx) as resp, \
                open(dest, "wb") as f:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
        return os.path.exists(dest) and os.path.getsize(dest) > 0
    except Exception as e:  # noqa: BLE001
        log(f"   [urllib 失败] {e}")
        return False


def hf_list_files(repo_id, host, insecure):
    """列出 HF 仓库文件；主镜像失败自动切官方。返回 (文件列表, 实际使用的 host)。"""
    import json
    api = f"{host}/api/models/{repo_id}"
    ssl_flag = ["--ssl-no-revoke"] if IS_WINDOWS else []
    cmd = ["curl", "-fsSL"] + ssl_flag + ["--connect-timeout", "30", api]
    if insecure:
        cmd.append("-k")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        alt = HF_OFFICIAL if host != HF_OFFICIAL else HF_MIRROR
        api2 = f"{alt}/api/models/{repo_id}"
        cmd2 = ["curl", "-fsSL"] + ssl_flag + ["--connect-timeout", "30", api2]
        if insecure:
            cmd2.append("-k")
        r = subprocess.run(cmd2, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"无法列出仓库文件: {api}\n{r.stderr[:200]}")
        host = alt
    data = json.loads(r.stdout)
    files = [s["rfilename"] for s in data.get("siblings", [])]
    return files, host


def download_hf_files(repo_id, target_dir, wanted, insecure, use_mirror):
    """从 HF 仓库下载 wanted 中列出的文件（支持 *.gguf 通配）。返回是否全部成功。"""
    host = HF_MIRROR if use_mirror else HF_OFFICIAL
    try:
        all_files, host = hf_list_files(repo_id, host, insecure)
    except Exception as e:  # noqa: BLE001
        log(f"   [跳过] 列文件失败：{e}")
        return False

    # 展开通配
    targets = []
    for w in wanted:
        if w.endswith(".gguf") and "*" in w:
            pat = w.replace(".gguf", "").rstrip("*").replace("*", "")
            matched = [f for f in all_files if f.endswith(".gguf") and pat.replace("*", "") in f]
            targets.extend(matched)
        elif w in all_files:
            targets.append(w)
        else:
            # 文件名与仓库不一致，仍直接尝试下载（可能只是命名差异）
            log(f"   [提示] 仓库中未列出 {w}，仍尝试按此文件名下载")
            targets.append(w)
    targets = sorted(set(targets))
    if not targets:
        log(f"   [跳过] 仓库 {repo_id} 中没有匹配的文件：{wanted}")
        return False

    base = f"{host}/{repo_id}/resolve/main"
    ok = True
    for fn in targets:
        dst = os.path.join(target_dir, *fn.split("/"))
        if os.path.exists(dst) and os.path.getsize(dst) > 0:
            log(f"   [已存在] 跳过 {fn}")
            continue
        url = f"{base}/{fn}"
        log(f"   ↓ {fn}")
        if not download_file(url, dst, insecure):
            ok = False
    return ok


def download_hf_repo_all(repo_id, target_dir, insecure, use_mirror):
    """下载整个 HF 仓库（用于 Qwen3-TTS 各变体）。"""
    host = HF_MIRROR if use_mirror else HF_OFFICIAL
    try:
        all_files, host = hf_list_files(repo_id, host, insecure)
    except Exception as e:  # noqa: BLE001
        log(f"   [失败] 列文件失败：{e}")
        return False
    base = f"{host}/{repo_id}/resolve/main"
    ok = True
    for fn in all_files:
        dst = os.path.join(target_dir, *fn.split("/"))
        if os.path.exists(dst) and os.path.getsize(dst) > 0:
            continue
        log(f"   ↓ {fn}")
        if not download_file(f"{base}/{fn}", dst, insecure):
            ok = False
    return ok


# ----------------------------------------------------------------------------
# 虚拟环境：默认建一个项目级 .venv 并重新在其中运行本脚本
# ----------------------------------------------------------------------------
def ensure_venv(args):
    if args.no_venv:
        return
    # 已经在目标 venv 里了
    if os.path.abspath(sys.prefix) == os.path.abspath(VENV_DIR):
        return
    if not os.path.isdir(VENV_DIR):
        log(f"[venv] 在项目根创建虚拟环境：{VENV_DIR}")
        try:
            subprocess.check_call([sys.executable, "-m", "venv", VENV_DIR])
        except subprocess.CalledProcessError as e:
            log(f"[venv] 创建失败（{e}），将直接装到当前 Python。若需要虚拟环境请先安装 venv 组件。")
            return
    if IS_WINDOWS:
        venv_py = os.path.join(VENV_DIR, "Scripts", "python.exe")
    else:
        venv_py = os.path.join(VENV_DIR, "bin", "python")
    if not os.path.exists(venv_py):
        log("[venv] 未找到虚拟环境解释器，直接装到当前 Python。")
        return
    log("[venv] 重新在项目的虚拟环境中运行本工具…")
    os.execv(venv_py, [venv_py, os.path.abspath(__file__)] + sys.argv[1:])


# ----------------------------------------------------------------------------
# 步骤 1：Python 包
# ----------------------------------------------------------------------------
def step_pip(args):
    hr("步骤 1/6  安装 Python 包依赖")
    index = None if args.no_mirror else (args.mirror or DEFAULT_PIP_INDEX)
    trusted = None if args.no_mirror else DEFAULT_PIP_TRUSTED
    pip_base = [sys.executable, "-m", "pip", "install"]
    if index:
        pip_base += ["-i", index, "--trusted-host", trusted]

    # 升级 pip
    log("[pip] 升级 pip …")
    subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "pip",
                    "-i", index or "https://pypi.org/simple",
                    "--trusted-host", trusted or "pypi.org"],
                   capture_output=True)
    if args.dry_run:
        all_pkgs = list(BASE_PACKAGES)
        if IS_WINDOWS:
            all_pkgs += WINDOWS_ONLY
        if IS_MACOS:
            all_pkgs += MAC_ONLY
        missing, present = _filter_missing(all_pkgs)
        log("  [dry-run] 已安装（将跳过）：" + (", ".join(present) if present else "无"))
        log("  [dry-run] 缺失（将安装，含镜像）：" + (", ".join(missing) if missing else "无（无需下载）"))
        if not missing:
            log("  [dry-run] 结论：所有 Python 包已就绪，本次不会下载任何内容。")
        return True

    # torch 特殊处理：macOS 用默认 wheel（支持 MPS）；Linux/Windows 默认装 CPU 版省空间
    torch_pkgs = ["torch", "torchvision", "torchaudio"]
    if IS_MACOS:
        log("[pip] torch 目标：macOS / Apple Silicon MPS（默认 wheel）")
        torch_index = None
    else:
        if args.torch == "cuda":
            log("[pip] torch 目标：CUDA 版")
            torch_index = None
        else:
            log("[pip] torch 目标：CPU 版（省空间；如需 GPU 用 --torch cuda）")
            torch_index = "https://download.pytorch.org/whl/cpu"
    torch_missing, _ = _filter_missing(torch_pkgs)
    if torch_missing:
        log(f"[pip] 安装缺失的 torch 组件：{torch_missing}")
        _pip_install(torch_missing, pip_base, torch_index)
    else:
        log("[pip] torch 已全部安装，跳过")

    # 基础包
    log("[pip] 检测基础运行时包（仅安装缺失项）…")
    pkgs = list(BASE_PACKAGES)
    if IS_WINDOWS:
        pkgs += WINDOWS_ONLY
    if IS_MACOS:
        pkgs += MAC_ONLY
    pkgs_missing, _ = _filter_missing(pkgs)
    if not pkgs_missing:
        log("[pip] 基础包已全部安装，无需下载 ✅")
        return True
    log(f"[pip] 需安装的缺失包（共 {len(pkgs_missing)} 个）：{pkgs_missing}")
    failed = _pip_install(pkgs_missing, pip_base, None)

    if failed:
        log("\n[警告] 以下包安装失败，可稍后手动安装或参考部署指南排错：")
        for p in failed:
            log(f"   - {p}")
        return False
    log("[pip] Python 包安装完成 ✅")
    return True


def _pip_install(pkgs, pip_base, index_url):
    """先整批装，失败再逐个装（避免一个失败拖垮全部）。返回失败列表。"""
    if index_url:
        cmd = pip_base + ["--extra-index-url", index_url] + pkgs
    else:
        cmd = pip_base + pkgs
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode == 0:
        return []
    log(f"   [整批安装失败，改为逐个安装并重试]")
    failed = []
    for p in pkgs:
        c = pip_base + ([ "--extra-index-url", index_url ] if index_url else []) + [p]
        rr = subprocess.run(c, capture_output=True, text=True)
        if rr.returncode != 0:
            failed.append(p)
            log(f"   [失败] {p}")
    return failed


# ----------------------------------------------------------------------------
# 步骤 2：系统级依赖
# ----------------------------------------------------------------------------
def step_system(args):
    hr("步骤 2/6  安装系统级依赖（portaudio / ffmpeg）")
    if args.dry_run:
        if IS_MACOS:
            log("  [dry-run] macOS: brew install portaudio ffmpeg")
        elif IS_LINUX:
            log("  [dry-run] Linux: 用包管理器安装 portaudio19-dev python3-dev ffmpeg")
        else:
            log("  [dry-run] Windows: 无需系统包（ffmpeg 用 imageio-ffmpeg 复制；如需编译依赖装 VS 生成工具）")
        return True

    if IS_MACOS:
        if shutil.which("brew") is None:
            log("[macOS] 未检测到 Homebrew。请先安装：/bin/bash -c \"$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\"")
        else:
            log("[macOS] brew install portaudio ffmpeg …")
            subprocess.run(["brew", "install", "portaudio", "ffmpeg"])
    elif IS_LINUX:
        if shutil.which("apt-get"):
            log("[Linux] apt 安装 portaudio19-dev python3-dev ffmpeg …")
            subprocess.run(sudo_prefix() + ["apt-get", "update"])
            subprocess.run(sudo_prefix() + ["apt-get", "install", "-y",
                                            "portaudio19-dev", "python3-dev", "ffmpeg"])
        elif shutil.which("dnf"):
            log("[Linux] dnf 安装 portaudio-devel python3-devel ffmpeg …")
            subprocess.run(sudo_prefix() + ["dnf", "install", "-y",
                                            "portaudio-devel", "python3-devel", "ffmpeg"])
        elif shutil.which("pacman"):
            log("[Linux] pacman 安装 portaudio ffmpeg …")
            subprocess.run(sudo_prefix() + ["pacman", "-S", "--noconfirm", "portaudio", "ffmpeg"])
        else:
            log("[Linux] 未识别到包管理器，请手动安装 portaudio / ffmpeg 开发包。")
    else:
        log("[Windows] 无需系统级包；若后续 pyaudio/llama-cpp 编译失败，请安装：")
        log("   https://visualstudio.microsoft.com/visual-cpp-build-tools/ （勾选“使用 C++ 的桌面开发”）")
    return True


# ----------------------------------------------------------------------------
# 步骤 3：ffmpeg 可执行文件（跨平台放到项目根，main.py 能直接找到）
# ----------------------------------------------------------------------------
def step_ffmpeg(args):
    hr("步骤 3/6  配置 ffmpeg 可执行文件")
    if args.dry_run:
        log(f"  [dry-run] 用 imageio-ffmpeg 复制 ffmpeg 到项目根（Windows: ffmpeg.exe，其他: ffmpeg）")
        return True

    log("[ffmpeg] pip 安装 imageio-ffmpeg（提供跨平台二进制）…")
    subprocess.run([sys.executable, "-m", "pip", "install", "imageio-ffmpeg",
                    "-i", (args.mirror or DEFAULT_PIP_INDEX),
                    "--trusted-host", DEFAULT_PIP_TRUSTED], capture_output=True)
    try:
        import imageio_ffmpeg  # noqa: F401
    except ImportError:
        log("[ffmpeg] imageio-ffmpeg 安装失败，请手动安装 ffmpeg 并确保在 PATH 中。")
        return False

    import imageio_ffmpeg as iff
    exe = iff.get_ffmpeg_exe()
    if IS_WINDOWS:
        target = os.path.join(PROJECT_ROOT, "ffmpeg.exe")
    else:
        target = os.path.join(PROJECT_ROOT, "ffmpeg")
    if os.path.exists(target) and os.path.getsize(target) > 0:
        log(f"[ffmpeg] 已存在，跳过复制：{target}")
    else:
        shutil.copy2(exe, target)
        log(f"[ffmpeg] 已复制：{target}")
    if not IS_WINDOWS:
        try:
            os.chmod(target, 0o755)
        except OSError:
            pass
    # 验证
    try:
        out = subprocess.check_output([target, "-version"], stderr=subprocess.STDOUT)
        log("[ffmpeg] 运行正常：" + out.decode("utf-8", "ignore").splitlines()[0])
    except Exception as e:  # noqa: BLE001
        log(f"[ffmpeg] 验证失败：{e}")
        return False
    return True


# ----------------------------------------------------------------------------
# 步骤 4：模型权重
# ----------------------------------------------------------------------------
def load_models_config(args):
    path = args.models_config or os.path.join(SCRIPT_DIR, "models_config.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f), path


def _already_has(target_dir, files):
    if not files:
        return False
    return all(os.path.exists(os.path.join(target_dir, *fn.split("/"))) for fn in files)


def step_models(args):
    hr("步骤 4/6  下载模型权重")
    if args.skip_models:
        log("[models] 已指定 --skip-models，跳过所有模型下载（假设已从旧机拷贝 models/）。")
        return True

    cfg, cfg_path = load_models_config(args)
    use_mirror = not args.no_mirror
    insecure = args.insecure
    any_failed = False

    for m in cfg.get("models", []):
        name = m.get("name", "?")
        mtype = m.get("type")
        required = m.get("required", False)
        # 默认跳过的“大文件”模型
        if m.get("skip_by_default") and not args.include_big:
            log(f"\n[models] 跳过（默认不下载，用 --include-big 可包含）：{name}")
            log(f"         说明：{m.get('note','')}")
            continue
        # 已存在则跳过
        tdir = os.path.join(PROJECT_ROOT, m.get("target_dir", "."))
        if mtype in ("hf_files",) and _already_has(tdir, m.get("files")):
            log(f"\n[models] 已存在，跳过：{name}")
            continue
        if mtype == "direct" and os.path.exists(os.path.join(PROJECT_ROOT, m.get("target", ""))):
            log(f"\n[models] 已存在，跳过：{name}")
            continue
        if mtype == "tts" and os.path.isdir(os.path.join(PROJECT_ROOT, m.get("target_dir", "models/qwen_tts"))):
            # TTS 目录存在即视为已下（粗略判断）
            log(f"\n[models] TTS 目录已存在，跳过：{name}")
            continue

        log(f"\n[models] 处理：{name}")
        log(f"         说明：{m.get('note','')}")
        ok = False
        try:
            if mtype == "hf_files":
                repo = m.get("repo_id")
                ok = download_hf_files(repo, tdir, m.get("files", []), insecure, use_mirror)
            elif mtype == "tts":
                ok = _download_tts(m, tdir, insecure, use_mirror, args)
            elif mtype == "direct":
                url = m.get("url")
                dst = os.path.join(PROJECT_ROOT, m.get("target", os.path.basename(url)))
                if not os.path.exists(dst):
                    log(f"   ↓ {url}")
                    ok = download_file(url, dst, insecure)
                else:
                    ok = True
            else:
                log(f"   [未知类型] {mtype}，跳过")
        except Exception as e:  # noqa: BLE001
            log(f"   [异常] {e}")
            ok = False

        if not ok:
            any_failed = True
            if required:
                log(f"   [!] 必需模型下载失败：{name}（可用 --insecure 重试，或手动按部署指南下载）")
            else:
                log(f"   [!] 下载失败（非必需，可忽略或稍后手动补）：{name}")

    if any_failed:
        log("\n[models] 部分模型未下载成功，详见上方。可加 --insecure 重试，或手动按部署指南/配置文件下载。")
    else:
        log("\n[models] 模型下载处理完成 ✅（已存在的自动跳过）")
    return not any_failed


def _download_tts(m, tdir, insecure, use_mirror, args):
    size = m.get("size") or args.ts_size
    mode = m.get("mode") or args.ts_mode
    repos = []
    if mode == "all":
        repos = [
            f"Qwen/Qwen3-TTS-12Hz-{size}-Base",
            f"Qwen/Qwen3-TTS-12Hz-{size}-CustomVoice",
            f"Qwen/Qwen3-TTS-12Hz-{size}-VoiceDesign",
        ]
    else:
        repos = [f"Qwen/Qwen3-TTS-12Hz-{size}-{mode.capitalize()}"]
    repos.append(f"Qwen/Qwen3-TTS-Tokenizer-12Hz")
    ok = True
    for repo in repos:
        local = os.path.join(tdir, repo.split("/")[-1])
        log(f"   ↓ {repo} -> {local}")
        if not download_hf_repo_all(repo, local, insecure, use_mirror):
            ok = False
    return ok


# ----------------------------------------------------------------------------
# 步骤（记忆向量模型）：预下载 embedding 模型权重
# ----------------------------------------------------------------------------
# 与 embedder.py 的默认模型保持一致；可用 MEMORY_EMBED_MODEL 环境变量覆盖
EMBED_MODEL_DEFAULT = "paraphrase-multilingual-MiniLM-L12-v2"


def step_embed_model(args):
    hr("步骤 5/6  预下载记忆 embedding 模型权重")
    if args.skip_embed:
        log("[embed] 已指定 --skip-embed，跳过 embedding 模型下载。")
        log("        说明：首次运行 main.py 时会自动联网下载；若持续失败，记忆自动降级为关键词检索，不影响主功能。")
        return True
    if not _package_installed("fastembed"):
        log("[embed] fastembed 尚未安装，跳过 embedding 模型预下载。请先运行步骤 1（pip）后再执行本步。")
        return False

    model = os.environ.get("MEMORY_EMBED_MODEL") or EMBED_MODEL_DEFAULT
    if args.dry_run:
        log(f"  [dry-run] 将预下载 embedding 模型：{model}")
        log(f"  [dry-run] 国内走镜像 HF_ENDPOINT={HF_MIRROR}；权重落到 HF 缓存目录（跨平台默认 ~/.cache/huggingface/hub）")
        return True

    # 国内网络：未显式设置镜像时自动切到 hf-mirror.com，确保权重可下载
    if not args.no_mirror and "HF_ENDPOINT" not in os.environ:
        os.environ["HF_ENDPOINT"] = HF_MIRROR
        log(f"[embed] 已设置 HF 镜像：{HF_MIRROR}")

    try:
        from fastembed import TextEmbedding
        log(f"[embed] 实例化并预热 embedding 模型：{model}（首次会联网下载 ONNX 权重）")
        emb = TextEmbedding(model_name=model)
        # 触发实际下载 + 维度探测（warmup）
        list(emb.embed(["warmup 预热"]))
        log("[embed] embedding 模型已就绪 ✅（记忆向量语义检索可用）")
        return True
    except Exception as e:  # noqa: BLE001
        log(f"[embed] embedding 模型下载/加载失败（非致命）：{e}")
        log("        仍可正常运行：首次运行 main.py 会自动重试下载；若持续失败，记忆自动降级为关键词检索。")
        return False


# ----------------------------------------------------------------------------
# 步骤 6（可选）：自检
# ----------------------------------------------------------------------------
def step_verify(args):
    hr("步骤 6/6  自检：关键模块 / 工具 / 模型")
    if args.dry_run:
        log("  [dry-run] 将检查关键 Python 模块导入、ffmpeg、模型文件。")
        return True

    import importlib
    modules = ["torch", "cv2", "ultralytics", "whisper", "pyaudio",
               "qwen_tts", "llama_cpp", "transformers", "sounddevice",
               "webrtcvad", "pyttsx3", "huggingface_hub", "imageio_ffmpeg",
               "fastembed", "PySide6"]
    missing = []
    for mod in modules:
        try:
            importlib.import_module(mod)
        except Exception:  # noqa: BLE001
            missing.append(mod)
    if missing:
        log(f"[自检] 以下模块未导入成功（可能需重装）：{missing}")
    else:
        log("[自检] 全部关键 Python 模块导入成功 ✅")

    # ffmpeg
    candidates = [os.path.join(PROJECT_ROOT, "ffmpeg.exe"),
                  os.path.join(PROJECT_ROOT, "ffmpeg"),
                  "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg",
                  "/usr/bin/ffmpeg"]
    found = next((c for c in candidates if os.path.exists(c)), None)
    if found is None:
        found = shutil.which("ffmpeg")
    log(f"[自检] ffmpeg：{'找到 ' + found if found else '未找到（请检查步骤 3）'}")

    # 模型
    expect_models = [
        os.path.join("models", "Qwen3.5-9B-Q4_K_M.gguf"),
        os.path.join("models", "Qwen3-TTS-12Hz-1.7B-Base"),
    ]
    for rel in expect_models:
        p = os.path.join(PROJECT_ROOT, rel)
        if os.path.exists(p):
            log(f"[自检] 模型就绪：{rel}")
        else:
            log(f"[自检] 模型缺失（不影响装包，运行前需补）：{rel}")
    return True


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(
        description="J.A.C. 新电脑一键依赖补全工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--only", choices=["all", "pip", "system", "ffmpeg", "models", "embed", "verify"],
                   default="all", help="只运行指定阶段（默认 all）")
    p.add_argument("--skip-models", action="store_true", help="跳过所有大模型下载（假设已从旧机拷贝）")
    p.add_argument("--skip-embed", action="store_true", help="跳过记忆 embedding 模型预下载（首次运行 main.py 时自动联网下）")
    p.add_argument("--include-big", action="store_true", help="连 35B 备用大模型也下载（默认跳过）")
    p.add_argument("--torch", choices=["auto", "cpu", "cuda"], default="auto",
                   help="torch 安装变体（auto: macOS=MPS, 其他=CPU）")
    p.add_argument("--ts-size", choices=["1.7B", "0.6B"], default="1.7B", help="Qwen3-TTS 尺寸")
    p.add_argument("--ts-mode", choices=["clone", "custom", "design", "all"], default="clone",
                   help="Qwen3-TTS 变体")
    p.add_argument("--no-venv", action="store_true", help="不建虚拟环境，装到当前 Python")
    p.add_argument("--mirror", default=None, help="pip 镜像地址（默认清华）")
    p.add_argument("--no-mirror", action="store_true", help="pip 不使用镜像")
    p.add_argument("--insecure", action="store_true", help="模型下载关闭 SSL 校验（仅可信内网）")
    p.add_argument("--models-config", default=None, help="自定义 models_config.json 路径")
    p.add_argument("--dry-run", action="store_true", help="只打印将做什么，不改动")
    return p.parse_args()


def main():
    args = parse_args()
    # torch auto 映射到 cpu/cuda 语义
    if args.torch == "auto":
        args.torch = "cpu"  # macOS 仍走默认 wheel（MPS），此处仅影响 Linux/Windows 是否用 CPU 索引

    ensure_venv(args)  # 可能在此重新 exec 进 venv

    hr("J.A.C. 新电脑依赖补全工具")
    log(f"项目根目录 : {PROJECT_ROOT}")
    log(f"运行平台   : {'Windows' if IS_WINDOWS else 'macOS' if IS_MACOS else 'Linux'}")
    log(f"Python     : {sys.executable}  ({platform.python_version()})")
    log(f"阶段       : {args.only}")
    if args.dry_run:
        log("模式       : DRY-RUN（不改动任何东西）")

    steps = {
        "pip": step_pip,
        "system": step_system,
        "ffmpeg": step_ffmpeg,
        "models": step_models,
        "embed": step_embed_model,
        "verify": step_verify,
    }
    if args.only == "all":
        order = ["pip", "system", "ffmpeg", "models", "embed", "verify"]
    else:
        order = [args.only]

    all_ok = True
    for s in order:
        try:
            ok = steps[s](args)
        except Exception as e:  # noqa: BLE001
            log(f"[阶段 {s} 异常] {e}")
            ok = False
        all_ok = all_ok and bool(ok)

    hr("完成")
    if all_ok:
        log("所有阶段成功 ✅")
    else:
        log("存在失败/警告的阶段，请查看上方日志。多数情况可重试或按部署指南手动补。")
    if not args.no_venv and os.path.isdir(VENV_DIR):
        log(f"\n提醒：依赖装在了项目虚拟环境 {VENV_DIR}")
        if IS_WINDOWS:
            log(f"  运行前先激活：{os.path.join(VENV_DIR, 'Scripts', 'activate.bat')}")
            log(f"  然后：python main.py")
        else:
            log(f"  运行前先激活：source {os.path.join(VENV_DIR, 'bin', 'activate')}")
            log(f"  然后：python main.py")


if __name__ == "__main__":
    main()
