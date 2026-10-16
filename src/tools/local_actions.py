"""M2c 受限本地执行器：无 shell，单动作，真实结果与可取消的后台等待。"""

from contextlib import nullcontext
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

from src.brain.lm_studio import check_cancelled
from .local_intents import APP_ALIASES, validate_url


MAC_APPS = {
    "Safari": "com.apple.Safari", "Calendar": "com.apple.iCal",
    "Notes": "com.apple.Notes", "Calculator": "com.apple.calculator",
    "TextEdit": "com.apple.TextEdit",
}


def command_result(command, should_stop=None, dispatch_lock=None, timeout=5, payload=None, on_dispatch=None):
    """同代次锁内启动单个子进程；等待不占锁，取消只回收自己启动的辅助进程。"""
    process = None
    try:
        with dispatch_lock if dispatch_lock is not None else nullcontext():
            check_cancelled(should_stop)
            process = subprocess.Popen(command, stdin=subprocess.PIPE if payload is not None else subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if on_dispatch is not None:
                on_dispatch()
        deadline = time.monotonic() + timeout
        while True:
            check_cancelled(should_stop)
            if time.monotonic() >= deadline:
                raise TimeoutError("本地工具等待超时")
            try:
                output, error = process.communicate(input=payload, timeout=.1)
                if len(output) > 262144 or len(error) > 32768:
                    raise ValueError("本地工具输出超限")
                return process.returncode, output.decode("utf-8", errors="replace"), error.decode("utf-8", errors="replace")
            except subprocess.TimeoutExpired:
                payload = None
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate(timeout=2)


def local_answer(name, data):
    """由结构化执行证据生成结果，模型文字不能伪造已打开或已找到。"""
    status = data["status"]
    if status == "failed":
        return "bo s s，本地任务未完成：" + data["reason"] + "。"
    if name == "open_url":
        return "bo s s，打开网址的请求已交给默认浏览器，尚未验证页面是否加载成功。"
    if name == "open_app":
        if status == "completed":
            return "bo s s，已检测到 " + data["app_name"] + " 正在运行；未核验窗口是否在前台。"
        return "bo s s，应用启动请求已提交，尚未确认应用正在运行。"
    count = len(data["matches"])
    suffix = "本次只完成了部分扫描。" if status == "partial" else "指定范围扫描已完成。"
    if count == 0:
        return f"bo s s，本次扫描未找到匹配文件。{suffix}搜索范围和记录已保存。"
    return f"bo s s，找到 {count} 个匹配文件。{suffix}真实路径已保存到报告。"


class LocalActionExecutor:
    """仅接收已经逐字锁定的本句参数；旧通用工具注册表不构成执行权限。"""

    def execute(self, name, arguments, should_stop=None, dispatch_lock=None, on_dispatch=None):
        """执行一次请求，区分实际完成、请求提交、部分搜索和失败。"""
        check_cancelled(should_stop)
        try:
            if name == "search_files":
                code, output, _ = command_result(
                    [sys.executable, "-I", str(Path(__file__).with_name("local_file_worker.py"))],
                    should_stop, dispatch_lock, timeout=7,
                    payload=json.dumps(arguments, ensure_ascii=False).encode("utf-8"))
                if code:
                    raise OSError("文件搜索子进程失败")
                return json.loads(output)
            if name not in {"open_app", "open_url"}:
                raise ValueError("本地动作未授权")
            system = platform.system()
            if name == "open_url":
                if set(arguments) != {"url"}:
                    raise ValueError("网址参数无效")
                target = validate_url(arguments["url"])
                if system == "Darwin":
                    command = ["/usr/bin/open", target]
                elif system == "Linux":
                    command = ["/usr/bin/xdg-open", target]
                elif system == "Windows":
                    return self._windows_start(target, should_stop, dispatch_lock, on_dispatch)
                else:
                    raise ValueError("当前平台不支持打开网址")
            else:
                app = arguments.get("app_name")
                if set(arguments) != {"app_name"} or app not in set(APP_ALIASES.values()):
                    raise ValueError("应用不在白名单")
                if system != "Darwin":
                    raise ValueError("首版应用白名单仅支持 macOS，当前平台未执行")
                command = ["/usr/bin/open", "-b", MAC_APPS[app]]
            code, _, _ = command_result(command, should_stop, dispatch_lock, on_dispatch=on_dispatch)
            if code:
                raise OSError("系统启动器拒绝请求，请检查应用安装或默认浏览器")
            data = dict(status="submitted", **arguments)
            if name == "open_app":
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    check_cancelled(should_stop)
                    try:
                        code, processes, _ = command_result(["/bin/ps", "-axo", "comm="], should_stop, timeout=2)
                    except (OSError, ValueError, TimeoutError):
                        data["verification"] = "启动器已接收，进程核验未完成"
                        return data
                    suffix = f"/{app}.app/Contents/MacOS/{app}"
                    if code == 0 and any(line.strip().endswith(suffix) for line in processes.splitlines()):
                        data["status"] = "completed"
                        break
                    time.sleep(.1)
            return data
        except (OSError, ValueError, TimeoutError) as error:
            return dict(status="failed", reason=str(error))

    @staticmethod
    def _windows_start(target, should_stop, dispatch_lock, on_dispatch):
        """Windows 使用系统文件关联打开已校验网址，不经过 cmd 或命令字符串。"""
        with dispatch_lock if dispatch_lock is not None else nullcontext():
            check_cancelled(should_stop)
            os.startfile(target)
            if on_dispatch is not None:
                on_dispatch()
        return dict(status="submitted", url=target)
