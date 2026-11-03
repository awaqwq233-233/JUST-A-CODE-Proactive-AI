#!/usr/bin/env python3
"""准备独立 Chroma/BGE 环境和锁定模型；运行时无下载，国内源失败回退。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def download(directory):
    """固定 revision 的资源只在哈希正确后原子安装，不关闭 TLS 验证。"""
    lock = json.loads((ROOT / 'memory.lock.json').read_text(encoding='utf-8'))
    for name, expected in lock['files'].items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == expected:
            continue
        errors = []
        for host in ('https://hf-mirror.com', 'https://huggingface.co'):
            temporary = path.with_name(path.name + '.part')
            try:
                url = f"{host}/{lock['onnx_repository']}/resolve/{lock['revision']}/{name}"
                digest, received = hashlib.sha256(), 0
                with urllib.request.urlopen(url, timeout=30) as response, temporary.open('wb') as handle:
                    while chunk := response.read(1024 * 1024):
                        received += len(chunk)
                        if received > 128 * 1024 * 1024:
                            raise ValueError('模型资源超限')
                        digest.update(chunk)
                        handle.write(chunk)
                if digest.hexdigest() != expected:
                    raise ValueError('模型资源哈希不符')
                os.replace(temporary, path)
                print('[记忆资源] ' + name, flush=True)
                break
            except Exception as error:
                errors.append(type(error).__name__)
                temporary.unlink(missing_ok=True)
        else:
            raise RuntimeError('记忆资源下载失败：' + name + ' ' + ','.join(errors))


def main(argv=None):
    """安装独立 Python 3.11 环境并运行真实离线向量写入/查询/删除自检。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mirror', default='https://pypi.tuna.tsinghua.edu.cn/simple')
    parser.add_argument('--model-dir', type=Path, default=ROOT / '.cache/memory/models/bge-small-zh-v1.5')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--self-test-only', action='store_true')
    args = parser.parse_args(argv)
    python = ROOT / '.cache/memory/venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if args.dry_run:
        print('记忆：Python 3.11 独立环境、requirements-memory.txt、锁定BGE资源、离线索引自检')
        return
    if not args.self_test_only:
        if not python.exists():
            fallback = ROOT / '.cache/m0/venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            interpreter = sys.executable if sys.version_info[:2] == (3, 11) else (shutil.which('python3.11') or str(fallback))
            subprocess.run([interpreter, '-m', 'venv', str(python.parents[1])], check=True)
        cmd = [str(python), '-m', 'pip', 'install', '-r', str(ROOT / 'new_computer_download/requirements-memory.txt'), '--index-url']
        result = subprocess.run(cmd + [args.mirror])
        if result.returncode:
            subprocess.run(cmd + ['https://pypi.org/simple'], check=True)
        download(args.model_dir)
    with tempfile.TemporaryDirectory(prefix='jac-memory-test-') as temporary:
        code = ('from src.memory.longterm.vector_worker import VectorEngine; import sys; '
                'v=VectorEngine(sys.argv[1],sys.argv[2]); '
                'v.execute({"op":"upsert","facts":[{"id":"test","revision":1,"attribute":"咖啡甜度","evidence":"咖啡不加糖"}]}); '
                'assert v.execute({"op":"query","query":"咖啡甜度"})[0]["id"]=="test"; '
                'v.execute({"op":"delete","ids":["test"]}); assert v.execute({"op":"inventory"})=={}; '
                'print("记忆离线向量自检通过")')
        subprocess.run([str(python), '-c', code, temporary, str(args.model_dir)], cwd=ROOT, check=True,
                       env=dict(os.environ, ANONYMIZED_TELEMETRY='False', HF_HUB_OFFLINE='1', TOKENIZERS_PARALLELISM='false'))


if __name__ == '__main__':
    main()
