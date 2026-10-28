"""长任务 JSON 真源：原子检查点、独占执行与只含本地资料的可恢复状态。"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
import threading
from uuid import uuid4

from .boundary import AgentRefused, open_directory
from .profile import ROOT

TERMINAL = {'completed', 'partial', 'cancelled', 'deferred', 'failed'}
MAX_STATE = 128 * 1024 * 1024


def digest(value):
    """用稳定 UTF-8 序列化计算检查点完整性，不把摘要当身份认证。"""
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


class TaskStore:
    """每个任务一个原子状态文件；不沿链接读写，不从损坏状态猜测执行进度。"""

    def __init__(self, root=None):
        """确定私有目录，实际文件操作仍逐级校验目录句柄。"""
        self.root = Path(root or ROOT / 'data/tasks').absolute()
        self.lock = threading.RLock()

    def directory(self, task_id, create=False):
        """只接受程序生成的任务标识，避免状态名成为任意路径。"""
        if not re.fullmatch(r'jac-[0-9a-f]{32}', task_id):
            raise AgentRefused('任务标识无效')
        return open_directory(self.root / task_id, create=create)

    def save(self, record):
        """串行化检查点写入，与用户取消和最终发布使用同一把锁。"""
        with self.lock:
            self._save(record)

    def _save(self, record):
        """同目录临时文件 fsync 后原子替换并同步父目录，断电不留下半份检查点。"""
        record['updated_at'] = time.time()
        record['revision'] = record.get('revision', 0) + 1
        data = json.dumps({'checksum': digest(record), 'record': record}, ensure_ascii=False,
                          allow_nan=False, separators=(',', ':')).encode('utf-8')
        if len(data) > MAX_STATE:
            raise AgentRefused('任务检查点超过128 MiB，请缩小资料范围')
        parent = self.directory(record['task_id'], create=True)
        temporary = '.state-' + uuid4().hex
        try:
            fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, 'state.json', src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
        finally:
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass
            os.close(parent)

    def load(self, task_id):
        """校验版本、任务归属与内容摘要，损坏时停止而不是回退旧动作记录。"""
        parent = self.directory(task_id)
        try:
            fd = os.open('state.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        finally:
            os.close(parent)
        with os.fdopen(fd, 'rb') as stream:
            data = stream.read(MAX_STATE + 1)
        if len(data) > MAX_STATE:
            raise AgentRefused('任务状态过大')
        value = json.loads(data)
        record = value['record']
        if (record.get('version') != 1 or record.get('task_id') != task_id
                or digest(record) != value.get('checksum')):
            raise AgentRefused('任务状态损坏或版本不兼容，未恢复执行')
        return record

    def create(self, task, inputs=None, source=None):
        """接受完整用户目标和显式授权资料，尚不访问资料或启动模型。"""
        if not isinstance(task, str) or not 2 <= len(task.strip()) <= 4000:
            raise AgentRefused('任务文字须为2–4000字')
        record = {'version': 1, 'task_id': 'jac-' + uuid4().hex, 'request': task.strip(),
                  'source': source or {'kind': 'gui'}, 'inputs': inputs or {}, 'status': 'queued',
                  'created_at': time.time(), 'order': time.time_ns(), 'control': None,
                  'budget': {'seconds': 1800, 'calls': 120, 'used_seconds': 0.0, 'used_calls': 0},
                  'stage': 0, 'failures': 0, 'stalls': 0, 'checkpoint': None, 'pending': None,
                  'plan': [], 'acceptance': {}, 'notes': [], 'authorization': 'local-read-and-new-artifacts-v1'}
        self.save(record)
        return record

    def list(self):
        """列出可校验任务；单个损坏记录显示独立故障，不影响其他任务。"""
        parent = open_directory(self.root, create=True)
        try:
            names = os.listdir(parent)
        finally:
            os.close(parent)
        records = []
        for name in names:
            if not re.fullmatch(r'jac-[0-9a-f]{32}', name):
                continue
            try:
                records.append(self.load(name))
            except (OSError, ValueError, KeyError, AgentRefused):
                records.append({'task_id': name, 'status': 'corrupt', 'request': '状态损坏，未自动恢复',
                                'order': 0, 'updated_at': 0})
        return sorted(records, key=lambda item: item.get('order', 0))

    @contextmanager
    def ownership(self):
        """跨进程独占整个调度器，两个 GUI 不能同时恢复同一批任务。"""
        import fcntl
        parent = open_directory(self.root, create=True)
        fd = os.open('manager.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        os.close(parent)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise AgentRefused('另一个 J.A.C. 实例正在管理任务') from error
            yield
        finally:
            os.close(fd)

    def delete(self, task_id):
        """删除已结束任务的私有状态；输出报告由用户在结果目录独立管理。"""
        record = self.load(task_id)
        if record['status'] not in TERMINAL | {'paused', 'needs_input'}:
            raise AgentRefused('请先暂停或取消任务再删除')
        fd = self.directory(task_id)
        os.close(fd)
        shutil.rmtree(self.root / task_id)
