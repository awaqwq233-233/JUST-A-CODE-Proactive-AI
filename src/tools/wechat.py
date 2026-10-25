"""微信只读边界；Swift 辅助程序在安装阶段构建，不在语音任务中下载或编译。"""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import shutil
import sys
import tempfile
import time

from src.brain.agent.boundary import AgentRefused
from src.brain.agent.profile import ROOT
from src.brain.lm_studio import check_cancelled

SOURCE = ROOT / "src/tools/wechat_reader.swift"
BINARY = ROOT / ".cache/agent/native/JACWeChatReader.app/Contents/MacOS/wechat-reader"


def explicit_chat_names(instruction):
    """提取明确引号或消息后缀限定的完整名称；歧义句不猜测或截短名称。"""
    if '微信' not in instruction:
        return []
    tail = instruction.split('微信', 1)[1]
    quoted = re.findall(r'[「“"]([^」”"\n]{2,120})[」”"]', tail)
    if quoted:
        return list(dict.fromkeys(quoted))
    match = re.match(r'(?:里面|里|中|内|的)?[：:\s]*([^，。；\n]{2,120}?)(?=(?:里面的|里的|中的|里面|里|中|的)(?:已加载消息|聊天记录|聊天信息|消息|内容|文件)|$)', tail)
    if match and not re.search(r'和|与|、|以及', match[1]):
        return [match[1]]
    return []


def native_request(arguments, should_stop=None):
    """经LaunchServices独立启动已获授权的应用，避免继承终端的辅助功能身份。"""
    cache = ROOT / '.cache/agent/native/requests'
    cache.mkdir(parents=True, exist_ok=True)
    directory = tempfile.mkdtemp(prefix='read-', dir=cache)
    process, cleanup_safe = None, True
    try:
        output = Path(directory) / 'response.json'
        process = subprocess.Popen(['/usr/bin/open', '-n', '-g', '-W', '-a', str(BINARY.parents[2]),
                                    '--args', '--output', str(output), *arguments],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cleanup_safe = False
        deadline = time.monotonic() + 15
        try:
            while not output.is_file():
                check_cancelled(should_stop)
                if time.monotonic() > deadline or process.poll() not in {None, 0}:
                    raise AgentRefused('微信组件未返回，请检查系统辅助功能权限')
                time.sleep(.05)
            check_cancelled(should_stop)
            data = output.read_bytes()
            if len(data) > 500000:
                raise AgentRefused('微信读取结果超过预算')
            result = json.loads(data)
            if not isinstance(result, dict):
                raise AgentRefused('微信组件返回格式不正确')
            return result
        finally:
            Path(str(output) + '.cancel').touch()
            try:
                process.wait(timeout=2)
                cleanup_safe = True
            except subprocess.TimeoutExpired:
                # open进程退出不证明辅助程序退出；保留取消标记，直到辅助程序自行停止。
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
    finally:
        if cleanup_safe:
            shutil.rmtree(directory)


def check_wechat(should_stop=None):
    """只核对安装及权限，不打开会话、不申请权限、不输出聊天内容。"""
    if sys.platform != "darwin":
        return {"supported": False, "reason": "微信直读当前只支持 macOS"}
    if not BINARY.is_file():
        return {"supported": False, "reason": "请运行安装器 --only agent 构建微信适配器"}
    marker = ROOT / ".cache/agent/native/wechat-reader.sha256"
    if not marker.is_file() or marker.read_text().strip() != hashlib.sha256(SOURCE.read_bytes()).hexdigest():
        return {"supported": False, "reason": "微信适配器需重新构建"}
    return {"supported": True, **native_request(['--check'], should_stop)}


def read_wechat(chat, instruction, should_stop=None):
    """仅接受用户本句明确说出的完整会话名；取消/超时丢弃结果，不读取数据库。"""
    if (not isinstance(chat, str) or not 2 <= len(chat) <= 120 or chat not in instruction
            or "微信" not in instruction or any(ord(c) < 32 for c in chat)):
        raise AgentRefused("请在指令中明确说出微信的完整会话名")
    names = explicit_chat_names(instruction)
    if not names:
        raise AgentRefused('无法确定完整会话范围，请使用“总结微信「完整会话名」的消息”，多个名称分别加引号')
    if chat not in names:
        raise AgentRefused("会话名被截短或不符，请使用本句完整名称：" + '、'.join(names))
    check_cancelled(should_stop)
    status = check_wechat(should_stop)
    if not status.get("supported") or not status.get("accessibility") or not status.get("running"):
        raise AgentRefused(status.get("reason") or "请登录微信，并给 JACWeChatReader.app 授予辅助功能权限")
    data = native_request(['--read', chat], should_stop)
    if data.get("chat") != chat or data.get("complete_history") is not False:
        raise AgentRefused(data.get("error", "微信会话核验失败"))
    data["captured_at"] = time.time()
    return data
