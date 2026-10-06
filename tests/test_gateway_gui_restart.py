"""离屏验证 GUI 停止/重启后的真实定时器刷新，不打开采集或播放设备。"""

import logging
import os
import sys
import threading
import time

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtTest import QTest

import gui
from src.utils.config import Config


class FakeClient:
    """为每次启动生成不同颜色的帧，检查预览确实来自新会话。"""

    def __init__(self, blue):
        """创建有区别的合成视频帧和拉取计数。"""
        self.blue = blue
        self.frame_reads = 0

    def get_latest_frame(self):
        """生成纯内存 BGR 帧，不访问摄像头。"""
        self.frame_reads += 1
        frame = np.zeros((48, 64, 3), dtype="uint8")
        frame[:, :, 0] = self.blue
        return frame

    def get_latest_mic_level(self):
        """提供固定模拟音量。"""
        return 0.05

    def get_reply_text(self):
        """提供固定模拟回复。"""
        return "模拟回复"


class FakeRuntime:
    """同步发布启停通知，独立验证 Qt 定时器生命周期。"""

    def __init__(self, context, on_state_change):
        """绑定回调，不构造生产媒体客户端。"""
        self.callback = on_state_change
        self.running = self.omni_mode = False
        self.omni_client = None
        self.starts = 0

    def start(self, config=None):
        """模拟新会话创建并通知 GUI。"""
        self.starts += 1
        self.omni_client = FakeClient(self.starts * 50)
        self.running = self.omni_mode = True
        self.callback(True)

    def stop(self):
        """模拟关闭会话并通知 GUI。"""
        self.running = self.omni_mode = False
        self.omni_client = None
        self.callback(False)


@pytest.fixture
def window(monkeypatch):
    """创建离屏窗口，结束时恢复日志重定向与 pytest 的输出捕获。"""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    streams = sys.stdout, sys.stderr
    handlers = list(logging.getLogger().handlers)
    monkeypatch.setattr(gui, "DesktopRuntime", FakeRuntime)
    instance = gui.MainWindow(Config())
    try:
        yield instance
    finally:
        instance.close()
        app.processEvents()
        sys.stdout, sys.stderr = streams
        for handler in list(logging.getLogger().handlers):
            if handler not in handlers:
                logging.getLogger().removeHandler(handler)


def test_preview_returns_after_stop_and_restart(window):
    """连续三次启停：停止必须停绘制，重新 ready 必须恢复定时拉帧和新图像。"""
    for index in range(3):
        window._stop_requested = False
        window.runtime.start()
        QTest.qWait(100)
        assert window.frame_timer.isActive(), "重启后帧刷新定时器没有恢复"
        client = window.runtime.omni_client
        assert client.frame_reads > 0
        pixmap = window.video_label.pixmap()
        assert pixmap is not None and not pixmap.isNull()
        assert pixmap.toImage().pixelColor(0, 0).blue() == (index + 1) * 50
        previous_reads = client.frame_reads
        QTest.qWait(80)
        assert client.frame_reads > previous_reads, "画面只刷新一次，没有持续更新"
        window._safe_stop_runtime()
        QTest.qWait(30)
        assert not window.frame_timer.isActive()
        assert window.start_btn.text() == "启动"


def test_worker_ready_notification_updates_preview_on_qt_thread(window):
    """工作线程只排队通知，真正恢复 Qt 定时器和新画面必须发生在主线程。"""
    window.frame_timer.stop()
    window.runtime.running = window.runtime.omni_mode = True
    window.runtime.omni_client = FakeClient(80)
    worker = threading.Thread(target=window._on_state_change, args=(True,))
    worker.start()
    worker.join(timeout=1)
    assert not worker.is_alive()
    assert not window.frame_timer.isActive(), "工作线程直接操作了 Qt 定时器"
    QTest.qWait(100)
    assert window.frame_timer.isActive()
    assert window.runtime.omni_client.frame_reads > 0
    assert window.start_btn.text() == "停止"


def test_stale_stop_notification_does_not_hide_new_session(window):
    """迟到的上一轮停止通知不能停止当前会话预览或解锁启动按钮。"""
    window.runtime.start()
    QTest.qWait(80)
    window._on_state_change(False)
    QTest.qWait(30)
    assert window.runtime.running and window.frame_timer.isActive()
    assert window.start_btn.text() == "停止"


def test_worker_startup_failure_restores_controls_on_qt_thread(window):
    """没有 Qt 事件循环的启动线程也能通过信号恢复失败后的 GUI 控件。"""
    window.start_btn.setEnabled(False)
    window.start_btn.setText("启动中…")
    worker = threading.Thread(target=window._startup_failed.emit, args=("模拟启动失败",))
    worker.start()
    worker.join(timeout=1)
    assert not worker.is_alive()
    assert not window.start_btn.isEnabled()
    QTest.qWait(80)
    assert window.start_btn.isEnabled() and window.start_btn.text() == "启动"
    assert not window.frame_timer.isActive()
    assert "模拟启动失败" in window.console.toPlainText()


def test_startup_exception_cleans_partially_started_runtime(window, monkeypatch):
    """工作线程初始化抛错时，主线程回收已启动部分并恢复可再次启动的控件。"""
    def fail_start(config):
        """模拟运行标志已设置、设备尚未完成初始化时的异常。"""
        window.runtime.running = True
        raise RuntimeError("模拟初始化异常")

    monkeypatch.setattr(window.runtime, "start", fail_start)
    window.gateway_consent_chk.setChecked(True)
    window.video_enabled_chk.setChecked(False)
    window._toggle_run()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        QTest.qWait(20)
        if (not window.runtime.running and window.start_btn.isEnabled()
                and "模拟初始化异常" in window.console.toPlainText()):
            break
    assert not window.runtime.running
    assert not window.frame_timer.isActive()
    assert window.start_btn.isEnabled() and window.start_btn.text() == "启动"
    assert "模拟初始化异常" in window.console.toPlainText()
