"""独立容器执行：授权输入副本、禁网、资源限制、进程回收与真实退出状态。"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import tarfile
import tempfile
import time
from uuid import uuid4

from .action_policy import scope, workspace_fd
from .boundary import AgentRefused
from .profile import ROOT
from .workspace_tools import PRIVATE, read_at, relative_path

LOCK = ROOT / 'actions.lock.json'
RUNTIME = ROOT / '.cache/actions'


def docker_command():
    """仅连接本项目明确配置的本地 Docker socket，拒绝环境变量改向远端。"""
    config = json.loads((RUNTIME / 'runtime.json').read_text(encoding='utf-8'))
    socket = config.get('socket', '')
    if not socket.startswith('/') or not Path(socket).is_socket():
        raise AgentRefused('隔离运行环境未启动，请运行电脑操作安装阶段的启动命令')
    executable = shutil.which('docker')
    if not executable:
        raise AgentRefused('Docker命令行未安装')
    return [executable, '--host', 'unix://' + socket]


def archive_workspace(record):
    """通过无链接目录句柄打包授权目录，过滤私有配置并限制文件数量与总量。"""
    stream = io.BytesIO()
    count, total = 0, 0
    def visit(fd, prefix, depth, archive):
        """递归复制普通文件；遇到链接明确拒绝，不跟随越界路径。"""
        nonlocal count, total
        if depth > 12:
            raise AgentRefused('工作目录超过12层，请缩小执行范围')
        for name in sorted(os.listdir(fd)):
            if name.startswith('.') or name in PRIVATE:
                continue
            path = prefix + name
            relative_path(path)
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            import stat
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    visit(child, path + '/', depth+1, archive)
                finally:
                    os.close(child)
            else:
                data, _ = read_at(fd, name)
                if data is None:
                    raise AgentRefused('打包期间输入消失')
                count, total = count+1, total+len(data)
                if count > 200 or total > 8*1024*1024:
                    raise AgentRefused('隔离执行输入超过200文件或8 MiB')
                item = tarfile.TarInfo(path)
                item.size, item.mode = len(data), 0o600
                archive.addfile(item, io.BytesIO(data))
    fd = workspace_fd(record)
    try:
        with tarfile.open(fileobj=stream, mode='w') as archive:
            visit(fd, '', 0, archive)
    finally:
        os.close(fd)
    return stream.getvalue(), count


def bounded_process(command, stdin, check, timeout=60, limit=128000):
    """持续抽取输出而非无界communicate；超时、取消及输出超限终止本次客户端。"""
    with tempfile.TemporaryFile() as source:
        source.write(stdin)
        source.seek(0)
        process = subprocess.Popen(command, stdin=source, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True, env={'PATH': os.environ.get('PATH', ''), 'LANG': 'en_US.UTF-8'})
        selector, outputs, total = selectors.DefaultSelector(), {'stdout': bytearray(), 'stderr': bytearray()}, 0
        try:
            for name in outputs:
                pipe = getattr(process, name)
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ, name)
            deadline = time.monotonic() + timeout
            while selector.get_map():
                check()
                if time.monotonic() > deadline:
                    raise AgentRefused('代码运行超过60秒，已请求回收容器')
                for key, _ in selector.select(.1):
                    chunk = os.read(key.fileobj.fileno(), 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    total += len(chunk)
                    if total > limit:
                        raise AgentRefused('代码输出超过128000字节，已请求回收容器')
                    outputs[key.data].extend(chunk)
            code = process.wait(timeout=2)
            return {'exit_code': code, **{k: bytes(v).decode('utf-8', errors='replace') for k, v in outputs.items()}}
        finally:
            selector.close()
            if process.poll() is None:
                import signal
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3)
            process.stdout.close()
            process.stderr.close()


def execute_code(tools, runtime, script_path, arguments):
    """在无宿主挂载的临时容器运行脚本，执行结果不自动覆盖宿主文件。"""
    if runtime not in scope(tools.record).get('runtimes', []):
        raise AgentRefused('执行环境未授权')
    path = relative_path(script_path)
    if path.suffix not in ({'.py'} if runtime == 'python' else {'.js', '.mjs', '.cjs'}):
        raise AgentRefused('脚本扩展名与执行环境不一致')
    if not isinstance(arguments, list) or len(arguments) > 20 or any(not isinstance(a, str) or len(a) > 500 or '\x00' in a for a in arguments):
        raise AgentRefused('脚本参数须为最多20个有界字符串')
    lock = json.loads(LOCK.read_text(encoding='utf-8'))
    image = lock['images'][runtime]
    if not image.startswith('sha256:') or len(image) != 71:
        raise AgentRefused('执行镜像未固定摘要，请完成安装阶段')
    docker = docker_command()
    payload, count = archive_workspace(tools.record)
    import hashlib
    input_hash = hashlib.sha256(payload).hexdigest()
    name = 'jac-' + uuid4().hex
    tools.record['sandbox_pending'] = {'container': name, 'input_sha256': input_hash, 'runtime': runtime}
    tools.save()
    command = docker + ['run', '--pull=never', '--name', name, '--label', 'local.jac.task=' + tools.task_id,
                        '--network', 'none', '--read-only', '--cap-drop=ALL', '--security-opt', 'no-new-privileges',
                        '--user', '65534:65534', '--pids-limit', '32', '--memory', '512m', '--memory-swap', '512m',
                        '--cpus', '1', '--ulimit', 'fsize=8388608:8388608', '--ulimit', 'nofile=128:128',
                        '--tmpfs', '/work:rw,nosuid,nodev,size=32m,mode=1777', '--tmpfs', '/tmp:rw,nosuid,nodev,size=16m,mode=1777',
                        '--workdir', '/work', '--env', 'PYTHONUTF8=1', '--env', 'PYTHONDONTWRITEBYTECODE=1',
                        '--env', 'HOME=/tmp', '-i', image, '/bin/sh', '-c',
                        'tar --no-same-owner -xf - && exec "$@"', 'jac-run',
                        'python3' if runtime == 'python' else 'node', '--', str(path), *arguments]
    try:
        tools.check()
        result = bounded_process(command, payload, tools.check)
        result.update(runtime=runtime, input_sha256=input_hash, input_files=count,
                      network='disabled', filesystem='ephemeral-copy',
                      output_files='容器内文件不写回原目录；需交付的内容通过文件编辑或报告工具保存')
        tools.record.setdefault('executions', []).append(result)
        tools.save()
        return result
    finally:
        cleanup = subprocess.run(docker + ['rm', '-f', name], capture_output=True, timeout=10)
        if cleanup.returncode == 0:
            tools.record.pop('sandbox_pending', None)
            tools.save()
