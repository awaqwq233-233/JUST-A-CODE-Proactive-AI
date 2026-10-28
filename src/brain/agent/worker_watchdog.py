"""父进程退出时回收本次独占的 SDK 进程组，防止崩溃恢复留下旧推理。"""
import os
import signal
import threading


def watch_owner(owner_pid, interval=.2):
    """只在自身为进程组首领时监控原父进程；绝不按模糊进程名终止程序。"""
    if not owner_pid or os.name != 'posix' or os.getpgrp() != os.getpid():
        return
    def monitor():
        """原父进程消失后终止自己及同组运行时，旧工具服务不会被继续使用。"""
        wake = threading.Event()
        while os.getppid() == owner_pid:
            wake.wait(interval)
        os.killpg(os.getpid(), signal.SIGTERM)
    threading.Thread(target=monitor, name='jac-owner-watch', daemon=True).start()
