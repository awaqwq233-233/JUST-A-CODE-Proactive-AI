#!/usr/bin/env python3
"""安装独立、锁定且可校验的 JACBrain 环境；不触碰 Gateway 依赖或下载模型。"""
import argparse
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".cache/agent"
LOCK = ROOT / "agent.lock.json"
REQUIREMENTS = ROOT / "new_computer_download/requirements-agent.txt"
MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
OFFICIAL = "https://pypi.org/simple"


def verify_plugin(lock):
    """确认本地源码与锁文件中的插件哈希一致。"""
    plugin = lock["plugin"]
    if hashlib.sha256((ROOT / plugin["path"]).read_bytes()).hexdigest() != plugin["sha256"]:
        raise RuntimeError("Agent 插件与 agent.lock.json 不一致")


def self_test():
    """在隔离 home 检查版本及实际插件加载，不请求模型、不执行文件工具。"""
    sys.path.insert(0, str(ROOT))
    from deepseek_harness import DeepSeekHarness
    from src.brain.agent.profile import MODEL, build_patch
    from src.brain.agent.worker import ShutdownAck
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    verify_plugin(lock)
    for package in lock["packages"]:
        if version(package["name"]) != package["version"]:
            raise RuntimeError("独立依赖版本不符：" + package["name"])
    work = CACHE / "self-test" / uuid.uuid4().hex
    work.mkdir(parents=True)
    patch = work / "patch.json"
    patch.write_text(json.dumps(build_patch(8192), ensure_ascii=False), encoding="utf-8")
    with DeepSeekHarness(dsh_home=str(work / "home"), cwd=str(work), profile="sdk-minimal",
                         patches=(str(patch),), provider="jac-local", model=MODEL,
                         reasoning_effort="off", max_tokens=768, initialize_timeout_seconds=20,
                         env={"JAC_LOCAL_KEY": "local-no-secret", "DO_NOT_TRACK": "1",
                              "PKG_NATIVE_CACHE_PATH": str(CACHE / "native-cache")}) as harness:
        harness.client.request("shutdown", None, response_model=ShutdownAck, timeout_seconds=8)
    print("Agent 自检通过：固定 SDK/runtime、插件和本机 provider 初始化；未调用模型或设备。")


def download_wheels(command, mirror):
    """镜像非零退出或整体超时均回退官方源；官方失败明确抛出，不循环重试。"""
    indexes = [mirror] if mirror == OFFICIAL else [mirror, OFFICIAL]
    for index, url in enumerate(indexes):
        try:
            subprocess.run(command + [url], cwd=ROOT, timeout=600, check=True)
            return
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            if index == len(indexes) - 1:
                raise
            print("Agent 镜像下载失败或超时，回退官方 PyPI；仍须通过相同 SHA256。")


def install(*, mirror=MIRROR, dry_run=False):
    """下载时强制哈希和 wheel；镜像失败回退官方，离线安装到独立 Python 3.11。"""
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    verify_plugin(lock)
    interpreter = sys.executable if sys.version_info[:2] == (3, 11) else shutil.which("python3.11")
    if not interpreter:
        raise RuntimeError("需要 Python 3.11；未修改当前 Python 环境")
    if platform.system() != "Darwin" or platform.machine() != "arm64" or int(platform.mac_ver()[0].split(".")[0]) < 14:
        raise RuntimeError("本阶段锁定并验证的是 macOS 14+ arm64；其他平台尚未开放安装")
    python = CACHE / "venv/bin/python"
    if dry_run:
        print(f"[dry-run] 创建/复用 {python}；校验下载 7 个锁定 wheel；镜像失败回退官方。")
        print("[dry-run] 安装后执行插件/provider 离线初始化及 pip check；不装入 Gateway。")
        return
    if not python.exists():
        subprocess.run([interpreter, "-m", "venv", str(CACHE / "venv")], check=True)
    actual = subprocess.check_output([str(python), "-c", "import sys; print(sys.version_info[:2])"], text=True).strip()
    if actual != "(3, 11)":
        raise RuntimeError("已有 Agent 环境不是 Python 3.11；未覆盖")
    wheels = CACHE / "wheels"
    wheels.mkdir(parents=True, exist_ok=True)
    base = [str(python), "-m", "pip", "--disable-pip-version-check", "--no-cache-dir"]
    download = base + ["download", "--only-binary=:all:", "--require-hashes", "-r", str(REQUIREMENTS),
                       "--dest", str(wheels), "--timeout", "20", "--retries", "1", "--index-url"]
    download_wheels(download, mirror)
    for package in lock["packages"]:
        if hashlib.sha256((wheels / package["filename"]).read_bytes()).hexdigest() != package["sha256"]:
            raise RuntimeError("下载制品 SHA256 不符：" + package["name"])
    subprocess.run(base + ["install", "--no-index", "--find-links", str(wheels), "--only-binary=:all:",
                            "--require-hashes", "--force-reinstall", "-r", str(REQUIREMENTS)], check=True, timeout=180)
    env = {key: os.environ[key] for key in ("PATH", "TMPDIR", "LANG") if key in os.environ}
    env.update(PYTHONUTF8="1", PYTHONPATH=str(ROOT), PKG_NATIVE_CACHE_PATH=str(CACHE / "native-cache"))
    subprocess.run([str(python), str(Path(__file__).resolve()), "--self-test"], cwd=ROOT,
                   env=env, check=True, timeout=40)
    subprocess.run(base + ["check"], check=True, timeout=30)


def main(argv=None):
    """支持一键安装调用和独立离线自检，不在语音运行期间自动触发。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mirror", default=MIRROR)
    parser.add_argument("--no-mirror", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        self_test()
    else:
        install(mirror=OFFICIAL if args.no_mirror else args.mirror, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
