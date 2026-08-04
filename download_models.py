#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
J.A.C. 一键模型预下载脚本（当前覆盖 Qwen3-TTS 语音模型）

为什么需要它：
  - Qwen3-TTS 的权重在首次 `from_pretrained` 时会自动下载（2~4GB），
    但在离线/弱网环境或想提前缓存时，手动预下载更稳。
  - 本脚本把模型下载到项目内的 `models/qwen_tts/<模型名>` 目录，
    `src/audio/qwen_tts.py` 会自动优先加载这份本地副本，下载后即可离线使用。

用法：
  python download_models.py                 # 默认：下载 1.7B-Base（clone 模式用）+ 分词器
  python download_models.py --size 0.6B     # 改用更小的 0.6B 模型
  python download_models.py --mode custom   # 下载 CustomVoice（强情绪控制用）
  python download_models.py --mode all      # 下载该尺寸下全部三种变体 + 分词器
  python download_models.py --source modelscope   # 改用 ModelScope（国内备用）
  python download_models.py --no-mirror    # 不使用 HuggingFace 国内镜像
  python download_models.py --insecure    # 关闭 SSL 校验（仅限代理/防火墙拦截的内网；有中间人风险）
  python download_models.py --check        # 仅校验本地权重是否完整（含 safetensors 截断检测），不下载

注意：
  - 跨平台（Windows / macOS / Linux），只需能联网运行一次。
  - 下载的是 Qwen3-TTS 模型；项目内的 *.gguf 大脑/判断模型已随仓库提供，不在此脚本范围。
  - 下载走系统 curl（绕开 Python 的 SSL 证书坑，跨平台可用）。正常情况下
    `python download_models.py` 直接成功；Windows 上若报 CRYPT_E_REVOCATION_OFFLINE（连不上
    吊销服务器），脚本在 Windows 上默认加 --ssl-no-revoke 跳过吊销检查（证书链仍校验；
    macOS/Linux 自动省略该 Windows 专属选项以免 "unknown option" 报错）。仍报证书错再加
    --insecure（curl -k 跳过校验，有中间人风险）。
  - 每个文件下载前会检查本地是否已存在且非空，已存在则自动跳过；因此可反复运行本脚本，
    只增量补全缺失的部分（中途失败重跑不会从头再来）。
  - 国内网络访问 pypi.org 常被掐断，装任何包请用清华镜像：
    pip install -U 包名 -i http://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
"""

import argparse
import json
import os
import platform
import struct
import subprocess
import sys

# ---------------- 模型仓库 ID ----------------
MODEL_REPOS = {
    "1.7B": {
        "clone": "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
        "custom": "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
        "design": "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
    },
    "0.6B": {
        "clone": "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
        "custom": "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
        "design": "Qwen/Qwen3-TTS-12Hz-0.6B-VoiceDesign",
    },
}
TOKENIZER_REPO = "Qwen/Qwen3-TTS-Tokenizer-12Hz"
HF_MIRROR = "https://hf-mirror.com"

# 是否关闭 SSL 校验（--insecure / 环境变量 JAC_HF_INSECURE=1），由 main() 设置。
# 关闭后 curl 加 -k、huggingface_hub 回退路径也关校验。
INSECURE = False

# 国内 pip 镜像（pypi.org 在部分网络下被掐断）
PIP_INDEX = os.environ.get("JAC_PIP_INDEX", "http://pypi.tuna.tsinghua.edu.cn/simple")
PIP_TRUSTED = os.environ.get("JAC_PIP_TRUSTED", "pypi.tuna.tsinghua.edu.cn")


def project_root():
    """脚本位于项目根目录，直接返回其所在目录。"""
    return os.path.dirname(os.path.abspath(__file__))


def ensure_hf_lib():
    """确保HuggingFacelib"""
    try:
        import huggingface_hub  # noqa: F401
        return "huggingface_hub"
    except ImportError:
        pass
    try:
        import modelscope  # type: ignore  # noqa: F401
        return "modelscope"
    except ImportError:
        pass
    return None


def setup_ssl(insecure):
    """配置 TLS 证书校验，修复 Windows 上常见的 CERTIFICATE_VERIFY_FAILED。

    安全模式（默认，优先级）：
      1) truststore：让 Python 使用 Windows 系统信任库（浏览器能访问就说明它是对的），
         能解决「服务器没发全中间证书导致 certifi 凑不出链」这类问题，最稳且安全。
      2) certifi：回退到 certifi 的 CA 包（Python 的 requests 默认用它做校验）。

    不安全模式（--insecure / 环境变量 JAC_HF_INSECURE=1）：关闭 SSL 校验。
    仅用于被代理或防火墙做 TLS 拦截的内网环境；会跳过证书校验、存在中间人攻击风险，
    必须显式开启，绝不作为默认。
    """
    if insecure:
        print("[SSL] 已启用 --insecure：关闭 SSL 证书校验（仅限可信内网/代理环境，存在中间人风险）。")
        os.environ["HF_HUB_DISABLE_SSL_VERIFY"] = "1"
        os.environ["PYTHONHTTPSVERIFY"] = "0"
        try:
            import ssl
            ssl._create_default_https_context = ssl._create_unverified_context
        except Exception:
            pass
        try:
            import urllib3
            urllib3.disable_warnings()
        except Exception:
            pass
        # 兜底：让后续新建的 requests.Session 默认不校验（覆盖 huggingface_hub / modelscope）
        try:
            import requests
            _orig = requests.Session.__init__
            def _patched(self, *a, **k):
                """已打补丁"""
                _orig(self, *a, **k)
                self.verify = False
            requests.Session.__init__ = _patched
        except Exception:
            pass
        return

    # 安全模式（优先）：注入系统信任库（Windows 上最稳）
    try:
        import truststore  # type: ignore
        truststore.inject_into_ssl()
        print("[SSL] 已注入系统信任库（Windows 系统 CA，安全且最稳）。")
        return
    except ImportError:
        pass
    except Exception as e:
        print(f"[SSL] truststore 注入失败（{e}），回退 certifi。")

    # 安全模式（回退）：使用 certifi 的 CA 包
    try:
        import certifi
        bundle = certifi.where()
        os.environ.setdefault("REQUESTS_CA_BUNDLE", bundle)
        os.environ.setdefault("CURL_CA_BUNDLE", bundle)
        os.environ.setdefault("SSL_CERT_FILE", bundle)
        print(f"[SSL] 使用 certifi CA 包：{bundle}")
    except ImportError:
        print("[SSL] 未安装 certifi，Python 将回退到系统 CA 存储。")
        print("      若仍报 CERTIFICATE_VERIFY_FAILED，请先：pip install -U certifi")


def _curl_present():
    """curl存在"""
    import shutil
    return shutil.which("curl") is not None


def _hf_list_files(repo_id, mirror, insecure):
    """通过 Hub API 列出仓库内所有文件（含 LFS 分片）。"""
    import json
    import subprocess
    api = f"{mirror}/api/models/{repo_id}"
    # --ssl-no-revoke: 关闭 Windows Schannel 的 CRL/吊销检查。
    # 在连不上吊销服务器的离线/受限网络下，证书链已信任但吊销检查会失败
    # （CRYPT_E_REVOCATION_OFFLINE），加此选项即可正常建立 TLS。
    ssl_flag = ["--ssl-no-revoke"] if platform.system() == "Windows" else []
    cmd = ["curl", "-fsSL"] + ssl_flag + ["--connect-timeout", "30", api]
    if insecure:
        cmd.append("-k")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"获取文件列表失败 {api}：{r.stderr.strip()[:160]}")
    data = json.loads(r.stdout)
    return [s["rfilename"] for s in data.get("siblings", [])]


def _safetensors_sizes(path):
    """读取 safetensors 头部的 (header_len, 声明的数据总字节数)；非 safetensors / 读不到返回 None。"""
    try:
        with open(path, "rb") as f:
            magic = f.read(8)
            if len(magic) < 8:
                return None
            header_len = struct.unpack("<Q", magic)[0]
            header = json.loads(f.read(header_len))
        tensors = {k: v for k, v in header.items() if k != "__metadata__"}
        if not tensors:
            return None
        declared = max(v["data_offsets"][1] for v in tensors.values())
        return header_len, declared
    except Exception:
        return None


def _file_complete(path):
    """判断本地文件是否"完整"（可安全跳过）：
      - 不存在或为空 → 不完整；
      - 是 safetensors：磁盘大小必须 >= 8(长度) + 头部 + 声明的数据字节，否则视为截断（半截下载）→ 不完整；
      - 其它文件：存在且非空即视为完整。
    safetensors 头部会声明全部张量的数据总字节数；截断的半截文件头部虽在、但磁盘大小远小于声明值，
    借此可识破"存在但残缺"的假完整，避免 download_models.py 误跳过、qwen_tts.py 误判已就绪。
    """
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return False
    if path.endswith(".safetensors"):
        sz = _safetensors_sizes(path)
        if sz is None:
            return False
        header_len, declared = sz
        expected = 8 + header_len + declared
        return os.path.getsize(path) >= expected
    return True


def download_hf(repo_id, local_dir, use_mirror):
    """下载 HuggingFace 仓库。

    优先用系统 curl 下载：绕开 Python 的 SSL 证书坑（Windows 上 certifi 经常凑不出
    完整链），且跨平台可用。curl 用系统信任库，正常情况下无需 --insecure 即可成功；
    --insecure 时加 -k 跳过校验。找不到 curl 才回退 huggingface_hub。
    """
    mirror = HF_MIRROR if use_mirror else "https://huggingface.co"
    if _curl_present():
        files = _hf_list_files(repo_id, mirror, INSECURE)
        base = f"{mirror}/{repo_id}/resolve/main"
        for fn in files:
            dst = os.path.join(local_dir, *fn.split("/"))
            # 已完整则跳过（只下载缺少/截断的部分，支持断点续传/重复运行）。
            # 注意：不能只看"存在且非空"——safetensors 半截下载（被中断）文件存在且非空，
            # 但其磁盘大小小于头部声明值，必须续传补齐，否则模型加载会失败。
            if _file_complete(dst):
                print(f"      [已完整] 跳过 {fn}")
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            url = f"{base}/{fn}"
            # -C -：断点续传（文件已部分下载时从断点接着下，配合 _file_complete 识别出的
            # 截断文件，可多次重跑逐步补全，不怕中途网络超时）。
            # --retry-all-errors + 加大重试次数：弱网/超时也自动重试，避免一次抖动就整体失败。
            cmd = ["curl", "-L", "--connect-timeout", "30", "--retry", "8",
                   "--retry-delay", "3", "--retry-all-errors"] + \
                  (["--ssl-no-revoke"] if platform.system() == "Windows" else []) + [
                "-C", "-", "-o", dst, url]
            if INSECURE:
                cmd.append("-k")
            print(f"      └ {fn}")
            r = subprocess.run(cmd)
            if r.returncode != 0:
                raise RuntimeError(f"curl 下载失败：{url}")
        return local_dir
    # 回退：huggingface_hub
    from huggingface_hub import snapshot_download
    if use_mirror:
        os.environ.setdefault("HF_ENDPOINT", HF_MIRROR)
    return snapshot_download(repo_id, local_dir=local_dir)


def download_modelscope(repo_id, local_dir):
    """下载ModelScope"""
    from modelscope.hub.snapshot_download import snapshot_download as ms_snapshot  # type: ignore
    cache_dir = os.path.dirname(local_dir)
    name = os.path.basename(local_dir)
    return ms_snapshot(repo_id, cache_dir=cache_dir, local_dir=os.path.join(cache_dir, name))


def resolve_source(preferred):
    """resolvesource"""
    if preferred == "modelscope":
        return "modelscope", False
    # 默认优先 HuggingFace（国内走镜像）
    lib = ensure_hf_lib()
    if lib == "huggingface_hub":
        return "huggingface", True
    if lib == "modelscope":
        return "modelscope", False
    # 都没装：尝试装 huggingface_hub[cli]（默认走清华镜像，避免 pypi.org 被掐断）
    print("[系统] 未检测到 huggingface_hub / modelscope，尝试安装 huggingface_hub（清华镜像）…")
    import subprocess
    code = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-U", "huggingface_hub",
         "-i", PIP_INDEX, "--trusted-host", PIP_TRUSTED],
        capture_output=True, text=True,
    )
    if code.returncode != 0:
        print("[错误] 安装 huggingface_hub 失败，请手动执行：")
        print(f"  pip install -U huggingface_hub -i {PIP_INDEX} --trusted-host {PIP_TRUSTED}")
        print(code.stderr)
        sys.exit(1)
    return "huggingface", True


def _check_one_variant(base, variant, args):
    """校验单个 Qwen3-TTS 变体目录的完整性，返回 0(完整)/1(有问题)。"""
    vdir = os.path.join(base, variant)
    print(f"==> 校验 {variant}")
    if not os.path.isdir(vdir):
        print(f"  [✗] 目录不存在：{vdir}")
        print(f"      需先运行: python download_models.py --size {args.size} --mode {args.mode}")
        return 1

    # 必需的非权重文件（与 src/audio/qwen_tts.py:_maybe_download_weights 保持一致；
    # 注意官方仓库不含 tokenizer.json，其分词器为 Qwen2Tokenizer 慢速分词器，只需下面三件套）。
    required = [
        "config.json",
        "tokenizer_config.json",
        "vocab.json",
        "merges.txt",
        "speech_tokenizer/preprocessor_config.json",
    ]
    # 权重文件：必须做 safetensors 头部完整性校验，识破"存在但被截断"的半截下载。
    weights = ["model.safetensors", "speech_tokenizer/model.safetensors"]

    problems = []
    for rel in required:
        p = os.path.join(vdir, *rel.split("/"))
        if os.path.exists(p):
            print(f"  [✓] {rel}")
        else:
            problems.append(f"缺失文件 {rel}")
            print(f"  [✗] {rel}  （不存在）")
    for rel in weights:
        p = os.path.join(vdir, *rel.split("/"))
        if not os.path.exists(p):
            problems.append(f"缺失权重 {rel}")
            print(f"  [✗] {rel}  （不存在）")
            continue
        if _file_complete(p):
            print(f"  [✓] {rel}  （{os.path.getsize(p)} 字节，头部完整）")
        else:
            problems.append(f"权重截断 {rel}")
            print(f"  [✗] {rel}  （截断！磁盘 {os.path.getsize(p)} 字节 < 头部声明大小，半截下载）")
    return 0 if not problems else 1


def cmd_check(args):
    """仅校验本地 Qwen3-TTS 权重是否完整，不下载。供排障 / CI 快速确认。"""
    base = os.path.join(project_root(), "models", "qwen_tts")
    print(f"[系统] Qwen3-TTS 权重完整性检查（size={args.size}, mode={args.mode}）\n")
    if args.mode == "all":
        variants = [v.split("/")[-1] for v in MODEL_REPOS[args.size].values()]
    else:
        variants = [MODEL_REPOS[args.size][args.mode].split("/")[-1]]

    rc = 0
    for variant in variants:
        rc = max(rc, _check_one_variant(base, variant, args))

    # 独立语音分词器仓库（Qwen3-TTS-Tokenizer-12Hz）是 Base 内置 speech_tokenizer 的同源完整副本，
    # 仅作可选信息提示，不影响加载判定（模型从 Base 目录加载其内置 speech_tokenizer）。
    tok_dir = os.path.join(base, TOKENIZER_REPO.split("/")[-1])
    if os.path.isdir(tok_dir):
        print(f"\n[提示] 独立分词器仓库已存在：{tok_dir}（可选，模型从 Base 内置 speech_tokenizer 加载）")

    print("\n" + "=" * 56)
    if rc == 0:
        print("[结果] 完整 ✅  Qwen3-TTS 可正常加载，无需再下载。")
    else:
        print("[结果] 不完整 ❌  修复：重跑 "
              f"python download_models.py --size {args.size} --mode {args.mode} [--insecure] "
              "即可增量补全（已完整文件跳过、截断文件自动续传）。")
    print("=" * 56)
    return rc


def main():
    """主"""
    parser = argparse.ArgumentParser(description="J.A.C. 模型预下载（Qwen3-TTS）")
    parser.add_argument("--size", choices=["1.7B", "0.6B"], default="1.7B",
                        help="模型尺寸（默认 1.7B，约 4GB；0.6B 约 2GB）")
    parser.add_argument("--mode", choices=["clone", "custom", "design", "all"],
                        default="clone",
                        help="下载哪种变体（默认 clone，对应默认 TTS 模式）")
    parser.add_argument("--source", choices=["hf", "modelscope", "auto"], default="auto",
                        help="下载源（默认 auto：优先 HuggingFace 国内镜像）")
    parser.add_argument("--no-mirror", action="store_true",
                        help="不使用 HuggingFace 国内镜像（hf-mirror.com）")
    parser.add_argument("--insecure", action="store_true",
                        help="关闭 SSL 证书校验（仅限被代理/防火墙做 TLS 拦截的内网；有中间人风险）")
    parser.add_argument("--check", action="store_true",
                        help="仅校验本地 Qwen3-TTS 权重是否完整（含 safetensors 截断检测），不下载")
    args = parser.parse_args()

    # --check 模式：只校验不下载
    if args.check:
        sys.exit(cmd_check(args))

    insecure = args.insecure or os.environ.get("JAC_HF_INSECURE") == "1"
    global INSECURE
    INSECURE = insecure
    setup_ssl(insecure)

    source, use_mirror = resolve_source(args.source)
    if args.source == "hf":
        use_mirror = not args.no_mirror
    elif args.source == "auto":
        use_mirror = not args.no_mirror

    base = os.path.join(project_root(), "models", "qwen_tts")
    os.makedirs(base, exist_ok=True)

    repos = []
    if args.mode == "all":
        repos = list(MODEL_REPOS[args.size].values())
    else:
        repos = [MODEL_REPOS[args.size][args.mode]]
    # 分词器所有模式都需要
    repos.append(TOKENIZER_REPO)

    print(f"[系统] 下载源: {source}"
          + (f"（镜像 {HF_MIRROR}）" if use_mirror else "")
          + f"\n[系统] 目标目录: {base}\n")

    failed = []
    for repo_id in repos:
        local_dir = os.path.join(base, repo_id.split("/")[-1])
        print(f"==> 下载 {repo_id} -> {local_dir}")
        try:
            if source == "modelscope":
                download_modelscope(repo_id, local_dir)
            else:
                download_hf(repo_id, local_dir, use_mirror)
            print(f"    [完成] {repo_id}\n")
        except Exception as e:
            print(f"    [失败] {repo_id}: {e}\n")
            failed.append(repo_id)

    print("=" * 56)
    if failed:
        print(f"[结果] 部分失败：{failed}")
        print("  排查建议：")
        print("    1) 证书校验失败：python download_models.py --insecure（curl -k 跳过校验，有中间人风险）")
        print("    2) 装包用清华镜像：pip install -U 包名 -i http://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn")
        print("    3) 改用国内源：python download_models.py --source modelscope（需先 pip 装 modelscope）")
        print("    4) 也可手动按 DEPLOY_GUIDE.txt 的命令拉取。")
        sys.exit(1)
    print("[结果] 全部模型已下载到 models/qwen_tts/")
    print("  QwenTTSSpeaker 会自动优先加载本地副本；")
    print("  运行 `python main.py` 即可直接使用（无需再次联网下载）。")
    print("=" * 56)


if __name__ == "__main__":
    main()
