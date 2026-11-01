"""有界文本编辑事务：版本检查、原子单文件写入、恢复副本与条件回滚。"""
from __future__ import annotations

import ast
import base64
from contextlib import contextmanager
import difflib
import hashlib
import json
import os
from pathlib import PurePosixPath
import stat
from uuid import uuid4

from .action_policy import scope, workspace_fd
from .boundary import AgentRefused

MAX_FILE = 256 * 1024
MAX_TRANSACTION = 2 * 1024 * 1024
EXTENSIONS = {'.txt', '.md', '.json', '.csv', '.tsv', '.py', '.js', '.mjs', '.cjs', '.ts', '.tsx',
              '.jsx', '.html', '.css', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.xml', '.log'}
PRIVATE = {'.git', '.aws', '.ssh', '.codex', '.agents', '.cache', '.venv', 'node_modules',
           'secrets', 'credentials.json', 'key', 'key.pub'}


def relative_path(value):
    """只接受工作目录内的普通相对路径，拒绝隐私目录及路径解释歧义。"""
    if (not isinstance(value, str) or not value or len(value) > 500 or '\\' in value
            or any(ord(c) < 32 for c in value)):
        raise AgentRefused('文件路径无效')
    path = PurePosixPath(value)
    if path.is_absolute() or any(p in {'', '.', '..'} for p in value.split('/')):
        raise AgentRefused('仅允许工作目录内的相对路径')
    if any(p in PRIVATE or p.startswith('.') or p.lower().endswith(('.pem', '.key', '.p12')) for p in path.parts):
        raise AgentRefused('不允许访问隐藏配置、凭据或依赖目录')
    return path


@contextmanager
def parent_fd(record, value):
    """逐层打开无符号链接目录，文件操作始终相对于已检查的目录句柄。"""
    path = relative_path(value)
    fd = workspace_fd(record)
    try:
        for part in path.parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd, path.name
    finally:
        os.close(fd)


def read_at(fd, name):
    """读取有限普通文件；硬链接也拒绝，避免通过别名改到授权目录外。"""
    try:
        file = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    except FileNotFoundError:
        return None, None
    try:
        info = os.fstat(file)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_FILE:
            raise AgentRefused('只支持不超过256 KiB、无硬链接的普通文件')
        with os.fdopen(file, 'rb', closefd=False) as stream:
            data = stream.read(MAX_FILE + 1)
        if len(data) > MAX_FILE:
            raise AgentRefused('文件超过256 KiB')
        return data, stat.S_IMODE(info.st_mode)
    finally:
        os.close(file)


def fingerprint(data):
    """明确区分不存在文件与空文件的版本。"""
    return hashlib.sha256(data).hexdigest() if data is not None else 'missing'


def directory_page(fd, offset):
    """只列普通目录和单链接文件，忽略凭据、链接及特殊设备。"""
    names = []
    for name in sorted(os.listdir(fd)):
        try:
            relative_path(name)
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                names.append(name + ('/' if stat.S_ISDIR(info.st_mode) else ''))
        except (OSError, AgentRefused):
            continue
    return {'entries': names[offset:offset+30], 'count': len(names), 'offset': offset,
            'next_offset': min(len(names), offset+30)}


def text_bytes(path, content, previous):
    """严格检查文本和常见语法，并保留已有 UTF-8 BOM 与 CRLF 换行。"""
    if PurePosixPath(path).suffix.lower() not in EXTENSIONS:
        raise AgentRefused('该文件类型尚未开放文本编辑')
    if not isinstance(content, str) or '\x00' in content or len(content.encode('utf-8')) > MAX_FILE:
        raise AgentRefused('新内容为空值、含NUL或超过文件预算')
    if previous is not None:
        previous.decode('utf-8-sig', errors='strict')
    normalized = content.replace('\r\n', '\n')
    if path.endswith('.json'):
        json.loads(normalized)
    if path.endswith('.py'):
        ast.parse(normalized, filename=path)
    if previous and b'\r\n' in previous and b'\n' not in previous.replace(b'\r\n', b''):
        normalized = normalized.replace('\n', '\r\n')
    raw = normalized.encode('utf-8')
    return (b'\xef\xbb\xbf' + raw) if previous and previous.startswith(b'\xef\xbb\xbf') else raw


class WorkspaceTools:
    """修改意图与备份存在同一个任务检查点，文件提交结果由重新读取确认。"""

    def __init__(self, tools):
        """复用任务的取消检查、持久化锁和权限真源。"""
        self.tools, self.record = tools, tools.record

    def read(self, path, offset):
        """列出指定目录或分页读取文本；目录列表不递归泄漏其他资料。"""
        if type(offset) is not int or offset < 0:
            raise AgentRefused('偏移必须是非负整数')
        if isinstance(path, str) and path.endswith('/') and path != '/':
            path = path[:-1]
        if path == '':
            fd = workspace_fd(self.record)
            try:
                return directory_page(fd, offset)
            finally:
                os.close(fd)
        with parent_fd(self.record, path) as (fd, name):
            try:
                directory = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except (FileNotFoundError, NotADirectoryError):
                directory = None
            if directory is not None:
                try:
                    return directory_page(directory, offset)
                finally:
                    os.close(directory)
            data, _ = read_at(fd, name)
        if data is None:
            return {'path': path, 'sha256': 'missing', 'exists': False}
        text = data.decode('utf-8-sig', errors='strict')
        if '\x00' in text:
            raise AgentRefused('不支持二进制文件')
        observed = self.record.setdefault('workspace_reads', {}).setdefault(path, {'sha256': fingerprint(data), 'ranges': []})
        if observed['sha256'] != fingerprint(data):
            observed.update(sha256=fingerprint(data), ranges=[])
        if offset > len(text):
            raise AgentRefused('偏移超过文件字符数')
        merged = []
        for start, end in sorted(observed['ranges'] + [[offset, min(len(text), offset+1200)]]):
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        observed['ranges'] = merged
        self.tools.save()
        return {'path': path, 'sha256': fingerprint(data), 'text': text[offset:offset+1200],
                'offset': offset, 'length': len(text), 'next_offset': min(len(text), offset+1200)}

    def stage(self, path, expected_sha256, content, *, precise_patch=False):
        """保存可检查的修改及原始字节；模型必须使用实际读取的前版本。"""
        if not scope(self.record).get('edit'):
            raise AgentRefused('未授权编辑工作目录')
        with parent_fd(self.record, path) as (fd, name):
            before, mode = read_at(fd, name)
        if fingerprint(before) != expected_sha256:
            raise AgentRefused('文件版本冲突，请重新读取并整理修改')
        if before is not None and not precise_patch:
            observed = self.record.get('workspace_reads', {}).get(path, {})
            end = 0
            for start, stop in sorted(observed.get('ranges', [])):
                if start > end:
                    break
                end = max(end, stop)
            if observed.get('sha256') != expected_sha256 or end < len(before.decode('utf-8-sig')):
                raise AgentRefused('完整替换前须读完文件；局部修改请使用patch_edit')
        after = text_bytes(path, content, before)
        edits = self.record.setdefault('edits', {})
        if path not in edits and len(edits) >= 20:
            raise AgentRefused('单次事务最多20份文件')
        item = {'before': base64.b64encode(before).decode() if before is not None else None,
                'after': base64.b64encode(after).decode(), 'before_hash': fingerprint(before),
                'after_hash': fingerprint(after), 'mode': mode or 0o600, 'status': 'staged'}
        if sum(len(v['after']) + len(v.get('before') or '') for k, v in edits.items() if k != path) + len(item['after']) + len(item['before'] or '') > MAX_TRANSACTION:
            raise AgentRefused('编辑事务及备份超过2 MiB')
        prior = edits.get(path)
        if prior and prior['status'] in {'committing', 'rolling_back'}:
            raise AgentRefused('上次文件事务尚未核对，不能覆盖恢复记录')
        if prior and prior['status'] in {'committed', 'rolled_back', 'conflict'}:
            history = self.record.setdefault('edit_history', [])
            if len(history) >= 20 or sum(len(v['edit']['after']) + len(v['edit'].get('before') or '') for v in history) > MAX_TRANSACTION:
                raise AgentRefused('修改历史超过预算，请结束本任务后继续')
            history.append({'path': path, 'edit': prior})
        edits[path] = item
        self.tools.save()
        diff = ''.join(difflib.unified_diff((before or b'').decode('utf-8-sig').splitlines(True),
                                          after.decode('utf-8-sig').splitlines(True), fromfile=path, tofile=path))
        return {'path': path, 'before_sha256': item['before_hash'], 'after_sha256': item['after_hash'],
                'diff': diff[:6000], 'diff_truncated': len(diff) > 6000, 'status': 'staged', 'written': False}

    def patch(self, path, expected_sha256, old, new):
        """精确替换唯一文本片段，保留未发送给模型的其余内容。"""
        if not isinstance(old, str) or not old or not isinstance(new, str):
            raise AgentRefused('局部修改须提供非空原文及替换文字')
        with parent_fd(self.record, path) as (fd, name):
            before, _ = read_at(fd, name)
        if before is None or fingerprint(before) != expected_sha256:
            raise AgentRefused('局部修改的文件版本不一致')
        text = before.decode('utf-8-sig').replace('\r\n', '\n')
        if text.count(old) != 1:
            raise AgentRefused('原文必须精确匹配且只出现一次，请增加上下文')
        return self.stage(path, expected_sha256, text.replace(old, new, 1), precise_patch=True)

    def write(self, path, item, rollback=False):
        """单文件校验后原子提交；回滚也检查当前版本，避免覆盖用户后续改动。"""
        expected = item['after_hash'] if rollback else item['before_hash']
        desired = item['before_hash'] if rollback else item['after_hash']
        raw = item['before'] if rollback else item['after']
        data = base64.b64decode(raw, validate=True) if raw is not None else None
        with parent_fd(self.record, path) as (fd, name):
            current, _ = read_at(fd, name)
            if fingerprint(current) == desired:
                return
            if fingerprint(current) != expected:
                raise AgentRefused('文件版本冲突，未覆盖：' + path)
            self.tools.check()
            temporary = '.jac-' + uuid4().hex
            try:
                if data is not None:
                    out = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, item['mode'] & 0o777, dir_fd=fd)
                    with os.fdopen(out, 'wb') as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                # 再检查路径身份和字节；非协作外部编辑器仍不提供跨进程CAS承诺。
                with parent_fd(self.record, path) as (fresh, _):
                    current_parent, original_parent = os.fstat(fresh), os.fstat(fd)
                    if (current_parent.st_dev, current_parent.st_ino) != (original_parent.st_dev, original_parent.st_ino) or fingerprint(read_at(fd, name)[0]) != expected:
                        raise AgentRefused('提交前文件或父目录发生变化')
                self.tools.check()
                if data is None:
                    os.unlink(name, dir_fd=fd)
                else:
                    os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
                os.fsync(fd)
                if fingerprint(read_at(fd, name)[0]) != desired:
                    raise AgentRefused('写入后核验失败：' + path)
            finally:
                try:
                    os.unlink(temporary, dir_fd=fd)
                except FileNotFoundError:
                    pass

    def commit(self, rollback=False):
        """逐文件持久化事务进度；部分失败保留真实状态，不冒充多文件原子性。"""
        if not scope(self.record).get('edit'):
            raise AgentRefused('未授权修改原文件')
        edits = self.record.get('edits', {})
        if not edits:
            raise AgentRefused('尚无暂存修改')
        with self.tools.store.lock:
            for path, item in edits.items():
                if rollback and item['status'] == 'staged':
                    item['status'] = 'rolled_back'
                    self.tools.save()
                    continue
                self.tools.check()
                item['status'] = 'rolling_back' if rollback else 'committing'
                self.tools.save()
                try:
                    self.write(path, item, rollback)
                except (OSError, AgentRefused):
                    # 即使写入后核验失败也保留可核对状态；确认第三方版本时允许重新读取后修订。
                    try:
                        with parent_fd(self.record, path) as (fd, name):
                            actual = fingerprint(read_at(fd, name)[0])
                        if actual not in {item['before_hash'], item['after_hash']}:
                            item['status'] = 'conflict'
                            self.tools.save()
                    except (OSError, AgentRefused):
                        pass
                    raise
                item['status'] = 'rolled_back' if rollback else 'committed'
                self.tools.save()
        return {'status': 'verified', 'files': {p: i['status'] for p, i in edits.items()},
                'scope': '逐文件原子写入；备份保存在本任务状态中'}
