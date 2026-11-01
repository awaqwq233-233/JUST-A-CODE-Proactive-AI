#!/usr/bin/env python3
"""固定电脑操作环境安装入口；浏览器、Swift组件与隔离执行运行时独立于语音依赖。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.cache/actions'


def build_desktop():
    """编译并稳定签名独立桌面组件，系统辅助功能授权仍由用户授予。"""
    source = ROOT / 'src/tools/desktop_controller.swift'
    bundle = CACHE / 'native/JACDesktopController.app'
    binary = bundle / 'Contents/MacOS/desktop-controller'
    binary.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['xcrun', 'swiftc', '-O', '-module-cache-path', str(CACHE / 'native/swift-cache'),
                    str(source), '-o', str(binary)], check=True, timeout=120)
    (bundle / 'Contents/Info.plist').write_bytes(plistlib.dumps({
        'CFBundleIdentifier': 'local.jac.desktop-controller', 'CFBundleName': 'JACDesktopController',
        'CFBundleDisplayName': 'J.A.C. 电脑操作', 'CFBundleExecutable': 'desktop-controller',
        'CFBundlePackageType': 'APPL', 'CFBundleVersion': '1', 'LSUIElement': True}))
    subprocess.run(['codesign', '--force', '--sign', '-', '--identifier', 'local.jac.desktop-controller',
                    '--requirements', '=designated => identifier "local.jac.desktop-controller"', str(bundle)],
                   check=True, timeout=20)
    (bundle.parent / 'desktop-controller.sha256').write_text(hashlib.sha256(source.read_bytes()).hexdigest()+'\n')


def install_python(mirror):
    """哈希锁定安装到独立3.11环境，国内镜像失败时只回退可信官方源。"""
    python = CACHE / 'venv/bin/python'
    if not python.is_file():
        interpreter = shutil.which('python3.11')
        if not interpreter:
            raise RuntimeError('需要 Python 3.11')
        subprocess.run([interpreter, '-m', 'venv', str(python.parents[1])], check=True)
    base = [str(python), '-m', 'pip', '--disable-pip-version-check', '--no-cache-dir', 'install',
            '--only-binary=:all:', '--require-hashes', '-r', str(ROOT / 'new_computer_download/requirements-actions.txt'),
            '--timeout', '20', '--retries', '1', '--index-url']
    try:
        subprocess.run(base+[mirror], check=True, timeout=300)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        subprocess.run(base+['https://pypi.org/simple'], check=True, timeout=300)
    subprocess.run([str(python), '-m', 'pip', 'check'], check=True, timeout=20)
    chrome = Path('/Applications/Google Chrome.app/Contents/Info.plist')
    if chrome.is_file():
        info = plistlib.loads(chrome.read_bytes())
        config = {'channel': 'chrome', 'verified_version': info['CFBundleShortVersionString']}
    else:
        env = dict(os.environ, PLAYWRIGHT_BROWSERS_PATH=str(CACHE / 'browsers'))
        subprocess.run([str(python), '-m', 'playwright', 'install', 'chromium'], env=env, check=True, timeout=300)
        config = {'channel': None}
    (CACHE / 'browser.json').write_text(json.dumps(config), encoding='utf-8')


def start_sandbox():
    """按需启动专用1核1GiB虚拟机，不共享宿主目录、不改变默认Docker上下文。"""
    for name in ('colima', 'docker'):
        if not shutil.which(name):
            raise RuntimeError('缺少 '+name+'；请先安装锁文件登记的容器运行时')
    subprocess.run(['colima', 'start', 'jac', '--cpu', '1', '--memory', '1', '--disk', '8', '--root-disk', '8',
                    '--mount', 'none', '--activate=false', '--ssh-config=false', '--ssh-agent=false',
                    '--port-forwarder', 'none', '--vm-type', 'vz', '--downloader', 'curl'],
                   check=True, timeout=600)
    socket = Path.home() / '.colima/jac/docker.sock'
    if not socket.is_socket():
        raise RuntimeError('未发现专用Docker socket')
    (CACHE / 'runtime.json').write_text(json.dumps({'socket': str(socket)}), encoding='utf-8')
    lock = json.loads((ROOT / 'actions.lock.json').read_text(encoding='utf-8'))
    images = subprocess.run(['docker', '--host', 'unix://'+str(socket), 'image', 'inspect',
                             *lock['images'].values()], capture_output=True, timeout=20)
    if images.returncode != 0:
        subprocess.run([str(ROOT / '.cache/m0/venv/bin/python'), str(Path(__file__).with_name('prepare_action_images.py'))], check=True, timeout=600)


def main(argv=None):
    """安装与运行时启停均显式触发；正常语音任务不会临时联网下载。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mirror', default='https://pypi.tuna.tsinghua.edu.cn/simple')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--start-sandbox', action='store_true')
    parser.add_argument('--stop-sandbox', action='store_true')
    parser.add_argument('--build-only', action='store_true')
    args = parser.parse_args(argv)
    if args.dry_run:
        print('电脑操作：独立Python3.11、锁定Playwright，使用本机Chrome或配套Chromium、签名桌面组件；容器运行时显式启动。')
        return
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise RuntimeError('当前电脑操作安装阶段仅验收 macOS arm64')
    CACHE.mkdir(parents=True, exist_ok=True)
    if args.stop_sandbox:
        subprocess.run(['colima', 'stop', 'jac'], check=True)
    elif args.start_sandbox:
        start_sandbox()
    elif args.build_only:
        build_desktop()
    else:
        install_python(args.mirror)
        build_desktop()


if __name__ == '__main__':
    main()
