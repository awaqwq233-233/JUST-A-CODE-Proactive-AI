"""任务级操作授权：仅由 GUI/CLI 建立，资料正文和模型工具不能扩大范围。"""
from __future__ import annotations

import copy
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from .boundary import AgentRefused, open_directory

VERSION = 'scoped-actions-v1'
LEGACY = 'local-read-and-new-artifacts-v1'
BLOCKED_APPS = {'com.apple.Terminal', 'com.googlecode.iterm2', 'com.apple.systempreferences',
                'com.apple.ScriptEditor2', 'com.microsoft.VSCode', 'com.apple.finder',
                'com.apple.Safari', 'com.google.Chrome', 'com.microsoft.edgemac'}


def origin(value):
    """规范化显式网站来源，不接受凭据、非 HTTP 协议或通配符。"""
    parsed = urlsplit(value)
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username
            or parsed.password or '*' in value or any(ord(c) < 32 for c in value)):
        raise AgentRefused('网站须为明确的 http/https 地址，不含凭据或通配符')
    host = parsed.hostname.encode('idna').decode('ascii')
    if ':' in host:
        host = '[' + host + ']'
    port = parsed.port
    return parsed.scheme + '://' + host + (':' + str(port) if port and port != {'http': 80, 'https': 443}[parsed.scheme] else '')


def authorize(value):
    """校验用户选择并冻结根目录身份；未提供权限时保持原只读任务。"""
    if not value:
        return None
    if not isinstance(value, dict) or set(value) - {'workspace', 'edit', 'runtimes', 'origins', 'apps'}:
        raise AgentRefused('操作授权字段无效')
    selected = copy.deepcopy(value)
    if type(selected.get('edit', False)) is not bool:
        raise AgentRefused('编辑权限必须明确选择')
    for key in ('runtimes', 'origins', 'apps'):
        items = selected.get(key, [])
        if not isinstance(items, list) or len(items) > 12 or any(not isinstance(x, str) for x in items):
            raise AgentRefused('授权列表无效：' + key)
        selected[key] = list(dict.fromkeys(items))
    if set(selected['runtimes']) - {'python', 'node'}:
        raise AgentRefused('首版执行环境仅支持 python/node')
    selected['origins'] = [origin(url) for url in selected['origins']]
    for app in selected['apps']:
        if not re.fullmatch(r'[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+', app) or app in BLOCKED_APPS:
            raise AgentRefused('该应用须使用专用文件、网页或隔离执行工具：' + app)
    workspace = selected.get('workspace', '')
    if workspace:
        path = Path(workspace).absolute()
        if path == Path('/') or path == Path.home():
            raise AgentRefused('请选择具体工作目录，不能授权整个磁盘或用户主目录')
        fd = open_directory(path)
        try:
            info = os.fstat(fd)
            selected.update(workspace=str(path), root_identity=[info.st_dev, info.st_ino])
        finally:
            os.close(fd)
    elif selected.get('edit') or selected['runtimes']:
        raise AgentRefused('编辑和代码执行须先选择工作目录')
    selected['edit'] = selected.get('edit', False)
    selected['version'] = VERSION
    return selected


def scope(record):
    """返回程序保存的权限；旧任务永远不因更新软件取得新工具。"""
    value = record.get('action_scope')
    if record.get('authorization') == VERSION and isinstance(value, dict) and value.get('version') == VERSION:
        return value
    return {}


def workspace_fd(record):
    """每次访问重新检查授权根目录身份，路径被替换后停止。"""
    selected = scope(record)
    if not selected.get('workspace'):
        raise AgentRefused('本任务未授权工作目录')
    fd = open_directory(selected['workspace'])
    info = os.fstat(fd)
    if [info.st_dev, info.st_ino] != selected['root_identity']:
        os.close(fd)
        raise AgentRefused('授权工作目录已被替换，请重新建立任务')
    return fd
