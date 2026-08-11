"""系统状态查询工具（时间 / 电池 / CPU / 内存，只读）。"""
import os
import re
import platform
import subprocess
import datetime


def _get_time():
    """返回当前本地时间。"""
    now = datetime.datetime.now()
    return f"当前时间：{now.strftime('%Y-%m-%d %H:%M:%S')}（本地时区）"


def _get_battery():
    """返回电池电量；仅 macOS 走 pmset 精确读取，其它平台尽力推断。"""
    if platform.system() == "Darwin":
        try:
            out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=5).stdout
            # 示例：' -InternalBattery-0 (id=...) 82%; discharging; 3:21 remaining'
            m = re.search(r"(\d+)%", out)
            if "discharging" in out:
                state = "放电中"
            elif "charging" in out:
                state = "充电中"
            else:
                state = "已接入电源"
            if m:
                return f"电池电量：{m.group(1)}%（{state}）"
        except Exception:
            pass
    return "电池信息：当前平台/环境无法读取（仅 macOS 走 pmset 精确读取）。"


def _get_cpu():
    """返回 CPU 核心数与最近负载。"""
    cores = os.cpu_count() or "未知"
    try:
        load = os.getloadavg()  # Unix 才有，返回 (1m,5m,15m)
        return (f"CPU 核心数：{cores}；"
                f"最近 1/5/15 分钟负载：{load[0]:.2f}/{load[1]:.2f}/{load[2]:.2f}")
    except Exception:
        return f"CPU 核心数：{cores}（当前平台不支持读取负载）"


def _get_memory():
    """返回内存占用；macOS 通过 sysctl + vm_stat 计算，其它平台尽力推断。"""
    if platform.system() == "Darwin":
        try:
            total = int(subprocess.run(["sysctl", "-n", "hw.memsize"],
                                       capture_output=True, text=True, timeout=5).stdout.strip())
            vm = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5).stdout
            pages = 4096  # macOS vm_stat 默认页大小

            def _pages(key):
                for line in vm.splitlines():
                    if line.startswith(key):
                        return int("".join(filter(str.isdigit, line.split(":")[1])))
                return 0

            used_pages = _pages("Pages active") + _pages("Pages wired down")
            used_gib = used_pages * pages / (1024 ** 3)
            total_gib = total / (1024 ** 3)
            return f"内存：已用约 {used_gib:.1f} GB / 共 {total_gib:.1f} GB（macOS）"
        except Exception:
            pass
    return "内存：当前平台/环境无法精确读取。"


def get_system_info(arguments):
    """查询系统状态。

    参数：info_type —— 取值 time/battery/cpu/memory/all，默认 all。
    """
    args = arguments or {}
    info_type = (args.get("info_type") or "all").lower()

    parts = {
        "time": _get_time,
        "battery": _get_battery,
        "cpu": _get_cpu,
        "memory": _get_memory,
    }
    if info_type == "all":
        return "\n".join(fn() for fn in parts.values())
    if info_type in parts:
        return parts[info_type]()
    return f"错误：未知的 info_type「{info_type}」，可选 time/battery/cpu/memory/all。"
