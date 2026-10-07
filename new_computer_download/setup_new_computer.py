#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""J.A.C. 安装工具：默认创建 Python 3.11 Gateway 环境并检查 GUI 导入。

默认使用清华 pip 镜像，失败回退官方源。保留 --only m0 兼容别名。
--only all / pip / verify 同样使用当前 Gateway 清单；system / ffmpeg / embed
为尚未迁移组件的独立维护阶段，不接回旧 MiniCPM、Voicebox 或判断轮询。
模型必须放在仓库外，固定版本后端由 start_m0_backend.py 单独启动。
"""

import argparse
import importlib
import importlib.util  # 显式导入，供 _package_installed 的 find_spec 使用
import json
import os
import platform
import re
import shutil
import ssl
import struct
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
# 回退源：部分大包（如 PySide6）在清华镜像返回 403 Forbidden，官方源 pypi.org 通常可用
OFFICIAL_PIP_INDEX = "https://pypi.org/simple"
OFFICIAL_PIP_TRUSTED = "pypi.org"
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
    """导入名称"""
    base = pip_spec.split("==")[0].split("<")[0].split(">")[0].split("~")[0].strip()
    return IMPORT_OVERRIDES.get(base, base.replace("-", "_"))


def _package_installed(pip_spec):
    """包installed"""
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
    """分隔线"""
    if title:
        log(f"\n===== {title} =====")
    else:
        log("=" * 60)


def curl_available():
    """curl可用"""
    return shutil.which("curl") is not None


def sudo_prefix():
    """sudoprefix"""
    if IS_LINUX and os.geteuid() != 0:
        return ["sudo"]
    return []


def run_cmd(cmd, check=True, capture=False, **kw):
    """运行命令"""
    log("  $ " + " ".join(str(c) for c in cmd))
    if capture:
        return subprocess.run(cmd, capture_output=True, text=True, **kw)
    return subprocess.run(cmd, **kw)


# 说明：模型权重（大脑 / 判断 / TTS）现已全部交由 LM Studio / Voicebox 等外部软件管理，
# 项目内不再下载任何本地模型文件，故已移除原有的 download_file / hf_list_files /
# download_hf_files / download_hf_repo_all 等下载辅助函数（无调用者、无悬空引用）。


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


def _log_pip_error(stderr, lines=15):
    """打印 pip 错误输出的关键尾部（去除 ANSI 颜色），便于排查 403 / 超时 / 编译失败。"""
    if not stderr:
        return
    text = re.sub(r"\x1b\[[0-9;]*m", "", stderr)
    snippet = [ln for ln in text.strip().splitlines() if ln.strip()]
    if snippet:
        log("   └─ " + "\n      ".join(snippet[-lines:]))


def _pip_install(pkgs, pip_base, index_url):
    """先整批装，失败改逐个装（避免一个失败拖垮全部），最后对仍失败的包回退官方源重试。
    返回最终失败的包列表。
    """
    if index_url:
        cmd = pip_base + ["--extra-index-url", index_url] + pkgs
    else:
        cmd = pip_base + pkgs
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode == 0:
        return []
    log("   [整批安装失败，改为逐个安装并重试]")
    _log_pip_error(r.stderr)
    failed = []
    for p in pkgs:
        c = pip_base + ([ "--extra-index-url", index_url ] if index_url else []) + [p]
        rr = subprocess.run(c, capture_output=True, text=True)
        if rr.returncode != 0:
            failed.append(p)
            log(f"   [失败] {p}（镜像源）")
            _log_pip_error(rr.stderr)
    if not failed:
        return []
    # 回退官方源 pypi.org：PySide6 等大包在清华镜像可能返回 403，官方源通常可用
    log(f"   [回退官方源 pypi.org 重试 {len(failed)} 个包]")
    still_failed = []
    for p in failed:
        c = pip_base + ["-i", OFFICIAL_PIP_INDEX, "--trusted-host", OFFICIAL_PIP_TRUSTED] + [p]
        rr = subprocess.run(c, capture_output=True, text=True)
        if rr.returncode != 0:
            still_failed.append(p)
            log(f"   [仍失败] {p}")
            _log_pip_error(rr.stderr)
        else:
            log(f"   [官方源成功] {p}")
    return still_failed


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
            # sox：音频格式转换（whisper / soundfile 可选依赖）；cmake：llama-cpp-python 源码编译需要
            log("[macOS] brew install portaudio ffmpeg sox cmake …")
            subprocess.run(["brew", "install", "portaudio", "ffmpeg", "sox", "cmake"])
    elif IS_LINUX:
        if shutil.which("apt-get"):
            log("[Linux] apt 安装 portaudio19-dev python3-dev ffmpeg sox cmake …")
            subprocess.run(sudo_prefix() + ["apt-get", "update"])
            subprocess.run(sudo_prefix() + ["apt-get", "install", "-y",
                                            "portaudio19-dev", "python3-dev",
                                            "ffmpeg", "sox", "cmake"])
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
    rc = subprocess.run([sys.executable, "-m", "pip", "install", "imageio-ffmpeg",
                         "-i", (args.mirror or DEFAULT_PIP_INDEX),
                         "--trusted-host", DEFAULT_PIP_TRUSTED], capture_output=True).returncode
    if rc != 0:
        # 清华镜像个别包可能 403，回退官方源重试一次
        log("[ffmpeg] 清华镜像安装失败，回退官方源 pypi.org 重试…")
        subprocess.run([sys.executable, "-m", "pip", "install", "imageio-ffmpeg",
                        "-i", OFFICIAL_PIP_INDEX, "--trusted-host", OFFICIAL_PIP_TRUSTED],
                       capture_output=True)
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
# 步骤 4：外部 AI 软件指引（模型不再由本工具下载）
# ----------------------------------------------------------------------------
def step_external_software(args):
    """说明固定后端和待接入的大脑，不下载模型或启动设备。"""
    hr("外部模型服务")
    log("感知与语音：按 backend.lock.json 配置仓库外固定版本模型，使用 start_m0_backend.py。")
    log("Qwen 大脑：未来使用 LM Studio 127.0.0.1:12345；本轮 GUI 尚未接线。")
    log("参考音：voices/silverwalf_voice.wav，由 Gateway 原生语音输出使用。")
    return True


# ----------------------------------------------------------------------------
# 步骤（记忆向量模型）：预下载 embedding 模型权重
# ----------------------------------------------------------------------------
# 必须与 src/memory/embedder.py 的 _DEFAULT_MODEL 完全一致（fastembed 要求带
# sentence-transformers/ 命名空间前缀，裸名会报 "is not supported in TextEmbedding"）。
# 可用 MEMORY_EMBED_MODEL 环境变量覆盖本默认值。
EMBED_MODEL_DEFAULT = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def step_embed_model(args):
    """step嵌入模型"""
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

    # 模型由外部软件（LM Studio / Voicebox）管理，项目内不再落盘模型文件，
    # 故不再做本地模型自检；运行前请确保对应外部软件已加载所需模型（见 READMEfirst.md）。
    log("[自检] 模型由外部软件管理，跳过本地模型文件检查（详见 READMEfirst.md）。")
    return True


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------
def step_m0(args):
    """建立当前主程序的 3.11 环境（含 Gateway GUI），保留已有 .venv。"""
    hr("方案 B M0/M1 独立环境")
    interpreter = sys.executable if sys.version_info[:2] == (3, 11) else shutil.which("python3.11")
    if not interpreter:
        log("M0 需要 Python 3.11，请先安装 python3.11。")
        return False
    m0_root = os.path.join(PROJECT_ROOT, ".cache", "m0", "venv")
    m0_python = os.path.join(m0_root, "Scripts", "python.exe") if IS_WINDOWS else os.path.join(m0_root, "bin", "python")
    requirements = os.path.join(SCRIPT_DIR, "requirements-m0.txt")
    index = args.mirror or DEFAULT_PIP_INDEX
    if args.no_mirror:
        index = OFFICIAL_PIP_INDEX
    if args.dry_run:
        log(f"[dry-run] Python 3.11 创建独立环境: {m0_root}")
        log(f"[dry-run] 安装依赖: {requirements}; index={index}")
        log("[dry-run] 保留现有 .venv，不下载模型，不启动服务。")
        return True
    if not os.path.exists(m0_python):
        run_cmd([interpreter, "-m", "venv", m0_root])
    version = run_cmd([m0_python, "-c", "import sys; print('.'.join(map(str, sys.version_info[:2])))"], capture=True)
    if version.stdout.strip() != "3.11":
        log("已有 M0 环境不是 Python 3.11，未覆盖；请检查 .cache/m0/venv。")
        return False
    command = [m0_python, "-m", "pip", "install", "-r", requirements, "--index-url", index]
    result = run_cmd(command, check=False)
    if result.returncode and index != OFFICIAL_PIP_INDEX:
        log("M0 镜像安装失败，回退官方源。")
        result = run_cmd(command[:-1] + [OFFICIAL_PIP_INDEX], check=False)
    if result.returncode:
        return False
    verification = run_cmd(
        [m0_python, "-c", "import sys; from src.omni import GatewayClient; "
         "from PySide6.QtWidgets import QApplication; import gui, main; "
         "from src.omni.backend_control import BackendController; import httpx, fastapi, uvicorn; "
         "import onnxruntime, kaldi_native_fbank; from src.omni.voice_conditioning import prepare_voice; "
         "assert 'torch' not in sys.modules and 'pyaudio' not in sys.modules; "
         "assert 'src.omni.client' not in sys.modules and 'src.judgment.judge' not in sys.modules; "
         "print('Gateway client, GUI and backend dependencies verified')"],
        capture=True, check=False, cwd=PROJECT_ROOT,
    )
    if verification.returncode:
        log("[自检失败] Gateway 或 GUI 依赖未就绪，请检查安装输出。")
        log(verification.stderr or verification.stdout)
        return False
    log("[自检通过] Gateway 客户端、GUI 与后端控制依赖已就绪；未打开设备。")
    log(f"GUI 启动：{m0_python} {os.path.join(PROJECT_ROOT, 'main.py')} --gateway --gui")
    log("仍须按 READMEfirst.md 配置仓库外模型并启动固定版本后端。")
    log("音色前端资源安装：prepare_native_voice.py --model-dir <GGUF目录>/voice-frontend --download-models --self-test")
    return True


def parse_args():
    """解析安装阶段、网络源与预览参数。"""
    p = argparse.ArgumentParser(
        description="J.A.C. 新电脑一键依赖补全工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--only", choices=["all", "pip", "system", "ffmpeg", "external", "embed", "verify", "m0", "gateway"],
                   default="gateway", help="只运行指定阶段（默认 gateway）")
    p.add_argument("--skip-embed", action="store_true", help="跳过记忆 embedding 模型预下载（首次运行 main.py 时自动联网下）")
    p.add_argument("--torch", choices=["auto", "cpu", "cuda"], default="auto",
                   help="torch 安装变体（auto: macOS=MPS, 其他=CPU）")
    p.add_argument("--no-venv", action="store_true", help="不建虚拟环境，装到当前 Python")
    p.add_argument("--mirror", default=None, help="pip 镜像地址（默认清华）")
    p.add_argument("--no-mirror", action="store_true", help="pip 不使用镜像")
    p.add_argument("--insecure", action="store_true", help="联网下载关闭 SSL 校验（仅可信内网，有中间人风险）")
    p.add_argument("--dry-run", action="store_true", help="只打印将做什么，不改动")
    return p.parse_args()


def main():
    """主"""
    args = parse_args()
    if args.only in {"m0", "gateway", "all", "pip", "verify"}:
        raise SystemExit(0 if step_m0(args) else 1)
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
        "external": step_external_software,
        "embed": step_embed_model,
        "verify": step_verify,
    }
    if args.only == "all":
        order = ["pip", "system", "ffmpeg", "external", "embed", "verify"]
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
