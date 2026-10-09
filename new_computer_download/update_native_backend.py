#!/usr/bin/env python3
"""将已登记的旧音色补丁升级为含原生任务播报的组合补丁，可选重新编译。"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from new_computer_download.start_m0_backend import check_checkout, ensure_free_ports


def git(directory, *arguments):
    """使用参数数组调用 Git，保留 UTF-8 输出并让失败明确中止。"""
    return subprocess.run(['git', '-C', str(directory), *arguments], check=True,
                          capture_output=True, text=True, encoding='utf-8').stdout


def update_engine(directory, lock):
    """只接受锁定 commit 的干净源码、当前补丁或已登记旧补丁；失败恢复旧修改。"""
    directory = Path(directory).expanduser().resolve()
    engine = lock['repositories']['engine']
    if git(directory, 'rev-parse', 'HEAD').strip() != engine['commit']:
        raise ValueError('引擎 commit 与 backend.lock.json 不符，未修改源码')
    current = git(directory, 'diff', 'HEAD', '--no-ext-diff')
    patch = ROOT / engine['patch']
    expected = patch.read_text(encoding='utf-8')
    if current.strip() == expected.strip():
        return False
    digest = hashlib.sha256(current.strip().encode('utf-8')).hexdigest()
    if current.strip() and digest not in engine.get('previous_patch_sha256', []):
        raise ValueError('源码包含未登记修改，未覆盖或重置')
    with tempfile.TemporaryDirectory(prefix='jac-engine-upgrade-') as temporary:
        previous = Path(temporary) / 'previous.patch'
        previous.write_text(current, encoding='utf-8')
        reversed_previous = applied_new = False
        try:
            if current.strip():
                git(directory, 'apply', '--reverse', '--check', str(previous))
                git(directory, 'apply', '--reverse', str(previous))
                reversed_previous = True
            git(directory, 'apply', '--check', str(patch))
            git(directory, 'apply', str(patch))
            applied_new = True
            check_checkout(directory, engine['commit'], patch)
        except Exception:
            if applied_new:
                git(directory, 'apply', '--reverse', str(patch))
            if reversed_previous:
                git(directory, 'apply', str(previous))
            raise
    return True


def build_engine(directory):
    """按方案 B 原构建参数生成 server/cli，不下载依赖或修改模型。"""
    cmake = shutil.which('cmake')
    if not cmake:
        raise RuntimeError('未安装 CMake，请先按 READMEfirst.md 完成构建环境安装')
    subprocess.run([cmake, '-S', str(directory), '-B', str(directory / 'build'),
                    '-DCMAKE_BUILD_TYPE=Release', '-DGGML_METAL=ON', '-DLLAMA_OPENSSL=OFF',
                    '-DLLAMA_CURL=OFF', '-DLLAMA_BUILD_TESTS=OFF'], check=True)
    subprocess.run([cmake, '--build', str(directory / 'build'), '--target', 'llama-omni-server',
                    '--target', 'llama-omni-cli', '-j8'], check=True)


def main(argv=None):
    """默认读取已保存的 GUI 引擎路径；重新编译前要求固定端口全部释放。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine-dir', type=Path)
    parser.add_argument('--build', action='store_true')
    args = parser.parse_args(argv)
    try:
        directory = args.engine_dir
        if directory is None:
            settings = json.loads((ROOT / '.cache/gui/backend.json').read_text(encoding='utf-8'))
            value = settings.get('engine_dir')
            if not isinstance(value, str) or not value:
                raise ValueError('GUI 尚未保存引擎目录，请先通过后端路径设置选择目录')
            directory = Path(value)
        directory = directory.expanduser().resolve()
        lock = json.loads((ROOT / 'backend.lock.json').read_text(encoding='utf-8'))
        if args.build:
            ensure_free_ports(lock['ports'])
        changed = update_engine(directory, lock)
        print('原生任务播报补丁已更新' if changed else '原生任务播报补丁已是当前版本')
        if args.build:
            build_engine(directory)
            print('固定原生后端已重新编译；请重新启动后端后再启动语音')
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f'后端更新未完成：{error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
