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
from PySide6.QtCore import QCoreApplication, QEvent

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
    controller_class = gui.BackendController

    def external_backend(parent):
        """已有后端模拟仅服务于客户端启停测试，不做网络探测。"""
        controller = controller_class(parent, auto_probe=False)
        controller.state = 'external'
        return controller

    monkeypatch.setattr(gui, 'BackendController', external_backend)
    instance = gui.MainWindow(Config())
    instance.setStyleSheet(gui.TECH_QSS)
    try:
        yield instance
    finally:
        instance.close()
        wait_gui(lambda: not instance._stopping)
        app.processEvents()
        instance.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()
        sys.stdout, sys.stderr = streams
        for handler in list(logging.getLogger().handlers):
            if handler not in handlers:
                logging.getLogger().removeHandler(handler)


def wait_gui(predicate, timeout=3):
    """持续处理 Qt 事件直到后台启停结束，避免用固定短等待掩盖线程调度。"""
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(20)
    assert predicate(), "后台 GUI 操作未在预期时间内完成"


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
        wait_gui(lambda: not window._stopping)
        assert not window.frame_timer.isActive()
        assert window.start_btn.text() == "启动语音"


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
    assert window.start_btn.text() == "停止语音"


def test_stale_stop_notification_does_not_hide_new_session(window):
    """迟到的上一轮停止通知不能停止当前会话预览或解锁启动按钮。"""
    window.runtime.start()
    QTest.qWait(80)
    window._on_state_change(False)
    QTest.qWait(30)
    assert window.runtime.running and window.frame_timer.isActive()
    assert window.start_btn.text() == "停止语音"


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
    assert window.start_btn.isEnabled() and window.start_btn.text() == "启动语音"
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
        if (not window._stopping and not window.runtime.running and window.start_btn.isEnabled()
                and "模拟初始化异常" in window.console.toPlainText()):
            break
    assert not window.runtime.running
    assert not window.frame_timer.isActive()
    assert window.start_btn.isEnabled() and window.start_btn.text() == "启动语音"
    assert "模拟初始化异常" in window.console.toPlainText()


def test_preview_clear_erases_entire_painted_widget(window):
    """检查控件实际像素而非空 pixmap，停止后两侧不得残留上一帧。"""
    window.show()
    window.runtime.start()
    QTest.qWait(100)
    window._safe_stop_runtime()
    wait_gui(lambda: not window._stopping)
    image = window.video_label.grab().toImage()
    for x in (8, image.width() // 2, image.width() - 9):
        color = image.pixelColor(x, image.height() // 2)
        assert color.name() == "#0a1e34", "停止后仍有旧合成帧残留"


def test_slow_stop_keeps_qt_responsive_and_blocks_restart(window, monkeypatch):
    """延迟设备清理时立即清屏，Qt 不阻塞且完成前不能再次启动。"""
    from PySide6.QtCore import QTimer
    release = threading.Event()
    original_stop = window.runtime.stop

    def slow_stop():
        """模拟 Worker 关闭耗时，不接触真实设备。"""
        assert release.wait(3)
        original_stop()

    monkeypatch.setattr(window.runtime, "stop", slow_stop)
    window.runtime.start()
    QTest.qWait(60)
    processed = []
    try:
        window._safe_stop_runtime()
        assert window._stopping and not window.start_btn.isEnabled()
        assert window.start_btn.text() == "停止中…"
        assert window.video_label.pixmap().isNull()
        window._toggle_run()
        assert window.runtime.starts == 1
        QTimer.singleShot(0, lambda: processed.append(True))
        QTest.qWait(40)
        assert processed, "停止等待阻塞了 Qt 主线程"
    finally:
        release.set()
        wait_gui(lambda: not window._stopping)
    assert window.start_btn.isEnabled() and window.start_btn.text() == "启动语音"
    window.gateway_consent_chk.setChecked(True)
    window.video_enabled_chk.setChecked(False)
    window._toggle_run()
    wait_gui(lambda: window.runtime.starts == 2 and window.start_btn.text() == "停止语音")


def test_stop_cleans_client_even_if_running_flag_is_false(window):
    """异常提前关闭 running 标志后，停止仍须清理残留客户端和画面。"""
    window.runtime.start()
    QTest.qWait(60)
    window.runtime.running = False
    assert window.runtime.omni_client is not None
    window._safe_stop_runtime()
    wait_gui(lambda: not window._stopping)
    assert window.runtime.omni_client is None
    assert window.video_label.pixmap().isNull()


def test_close_window_waits_for_shutdown(window, monkeypatch):
    """退出窗口必须等待关闭握手完成，不能直接终止 daemon 会话线程。"""
    release = threading.Event()
    original_stop = window.runtime.stop

    def slow_stop():
        """模拟仍在关闭的会话。"""
        assert release.wait(3)
        original_stop()

    monkeypatch.setattr(window.runtime, "stop", slow_stop)
    window.show()
    window.runtime.start()
    QTest.qWait(60)
    try:
        window.close()
        QTest.qWait(30)
        assert window.isVisible() and window._stopping
    finally:
        release.set()
        wait_gui(lambda: not window._stopping)
    assert not window.isVisible()


def test_stop_failure_requires_retry_before_start(window, monkeypatch):
    """停止超时不得伪装可启动；重试成功后才解锁下一次启动。"""
    original_stop = window.runtime.stop

    def failed_stop():
        """模拟尚未完成设备释放。"""
        raise RuntimeError("模拟停止超时")

    window.runtime.start()
    QTest.qWait(60)
    monkeypatch.setattr(window.runtime, "stop", failed_stop)
    window._safe_stop_runtime()
    wait_gui(lambda: not window._stopping)
    assert window.start_btn.text() == "重试停止"
    assert "模拟停止超时" in window.console.toPlainText()
    assert window._stop_error
    monkeypatch.setattr(window.runtime, "stop", original_stop)
    window._toggle_run()
    wait_gui(lambda: not window._stopping)
    assert not window._stop_error and window.runtime.starts == 1
    assert window.start_btn.text() == "启动语音"


def test_camera_geometry_preserves_full_frame_without_black_bars(window):
    """用整控件像素验证 4:3 和宽画面不裁切、不拉伸、不留黑边。"""
    from PySide6.QtGui import QPixmap, QColor
    window.show()
    for width, height in ((640, 480), (960, 540)):
        window.video_container.set_ratio(width, height)
        pix = QPixmap(width, height)
        pix.fill(QColor("#f08060"))
        window.video_label.setPixmap(pix)
        QTest.qWait(30)
        label = window.video_label
        assert abs(label.width()/label.height() - width/height) < .01
        image = label.grab().toImage()
        for x, y in ((20, image.height()//2), (image.width()-21, image.height()//2),
                     (image.width()//2, 20), (image.width()//2, image.height()-21)):
            assert image.pixelColor(x, y).name() == "#f08060"


def test_all_gateway_settings_are_collected_and_locked_while_running(window):
    """验证新参数均被收集，运行中不可修改，收起面板仍保留值。"""
    window.mic_gain_spin.setValue(2.5)
    window.fps_spin.setValue(8)
    window.session_spin.setValue(60)
    window.retry_spin.setValue(2)
    window.camera_spin.setValue(1)
    window.video_enabled_chk.setChecked(False)
    window.input_device_combo.addItem("测试麦克风", 4)
    window.input_device_combo.setCurrentIndex(1)
    window.output_device_combo.addItem("测试耳机", 6)
    window.output_device_combo.setCurrentIndex(1)
    config = window._collect_config()
    assert (config.omni_mic_gain, config.omni_fps, config.gateway_session_seconds, config.gateway_retry_limit) == (2.5, 8, 60, 2)
    assert (config.gateway_camera, config.gateway_input_device, config.gateway_output_device) == (1, 4, 6)
    assert not config.omni_video_enabled
    window.runtime.start()
    QTest.qWait(50)
    assert all(not control.isEnabled() for control in window._controls)
    window._safe_stop_runtime()
    wait_gui(lambda: not window._stopping)
    assert all(control.isEnabled() for control in window._controls)
    assert window._collect_config() == config


def test_reply_fragments_remain_continuous_and_logs_are_separate(window):
    """回复分片不能插入多余换行；连接日志独立且不丢失。"""
    window._append_reply("你好，")
    window._append_reply("bo s s。")
    assert "你好，bo s s。" in window.console.toPlainText()
    window._end_reply()
    window._append_reply("下一句。")
    assert window.console.toPlainText().count("J.A.C. ·") == 2
    window.log_q.put("[Gateway] connecting\n")
    window._pull_logs()
    assert "connecting" not in window.console.toPlainText()
    assert "connecting" in window.diagnostics.toPlainText()


def test_refresh_devices_preserves_selection_without_starting_streams(window, monkeypatch):
    """设备刷新只查询名称，输入输出分别过滤并保留选择。"""
    import sounddevice as sd
    monkeypatch.setattr(sd, "query_devices", lambda: [
        {"name": "麦克风", "max_input_channels": 1, "max_output_channels": 0},
        {"name": "耳机", "max_input_channels": 0, "max_output_channels": 2},
    ])
    window.output_device_combo.addItem("已选择耳机", 1)
    window.output_device_combo.setCurrentIndex(1)
    window._refresh_devices()
    assert window.input_device_combo.count() == 2
    assert window.output_device_combo.currentData() == 1
    assert "耳机" in window.output_device_combo.currentText()
    assert not window.runtime.running


def test_compact_window_and_collapsed_settings_keep_readable_layout(window):
    """最小窗口下保持可读对话区，收起设置后内容获得额外空间。"""
    window.resize(1100, 700)
    window.show()
    QTest.qWait(30)
    assert window.console.width() >= 240
    assert window.video_label.width() >= 280
    previous = window.content_splitter.width()
    window.settings_btn.setChecked(False)
    QTest.qWait(30)
    assert window.content_splitter.width() > previous
    assert not window.option_panel.isVisible()


def test_log_streams_are_restored_after_close(window):
    """结束 GUI 不遗留标准输出重定向或后台日志处理器。"""
    original = window._original_streams
    window.close()
    assert (sys.stdout, sys.stderr) == original
    assert window._log_handler not in logging.getLogger().handlers


def test_failed_connection_cleans_residual_client_before_restart(window):
    """连接异常提前清除 running 标志后，GUI 先回收残留客户端再恢复启动。"""
    window.runtime.start()
    QTest.qWait(50)
    window.runtime.running = False
    window.runtime.state = "error"
    window._on_state_change(False)
    wait_gui(lambda: window.runtime.omni_client is None and not window._stopping)
    assert window.start_btn.text() == "启动语音" and window.start_btn.isEnabled()
    assert not window.frame_timer.isActive()
    assert "清理会话" in window.console.toPlainText()


def attach_test_backend(window, tmp_path):
    """为 GUI 安装真实 Qt 子进程，替代模型后端而保留控制生命周期。"""
    from src.omni.backend_control import BackendController
    script = tmp_path / 'gui_launcher.py'
    script.write_text("import json,signal,sys,time\n"
        "def stop(signum,frame):\n    time.sleep(.15)\n    raise SystemExit(0)\n"
        "signal.signal(signal.SIGTERM,stop)\n"
        "print(json.dumps({'event':'jac.backend','state':'ready'}),flush=True)\n"
        "while True:\n    time.sleep(.1)\n", encoding='utf-8')
    window.backend.close()
    window.backend = BackendController(window, auto_probe=False, launcher=script)
    window.backend.changed.connect(window._on_backend_change)
    window.backend.log.connect(window._backend_log)
    window.backend_profile = {key: str(tmp_path) for key in ('demo_dir', 'engine_dir', 'model_dir')}
    return window.backend


def test_gui_backend_button_starts_and_stops_real_subprocess(window, tmp_path):
    """后端按钮真正启动和回收进程，文字随机器状态改变。"""
    backend = attach_test_backend(window, tmp_path)
    window._toggle_backend()
    wait_gui(lambda: backend.state == 'ready')
    assert window.backend_btn.text() == '停止后端' and backend.owned
    window._toggle_backend()
    wait_gui(lambda: backend.state == 'stopped' and not backend.owned)
    assert window.backend_btn.text() == '启动后端'


def test_stopping_backend_waits_for_media_session_cleanup(window, tmp_path, monkeypatch):
    """媒体尚未退出时保持后端存活，清理成功后才终止三进程启动器。"""
    backend = attach_test_backend(window, tmp_path)
    window._toggle_backend()
    wait_gui(lambda: backend.state == 'ready')
    release = threading.Event()
    original = window.runtime.stop

    def slow_media_stop():
        """模拟真实 Gateway 关闭握手尚在等待。"""
        assert release.wait(3)
        original()

    monkeypatch.setattr(window.runtime, 'stop', slow_media_stop)
    window.runtime.start()
    QTest.qWait(40)
    try:
        window._toggle_backend()
        QTest.qWait(40)
        assert window._stopping and backend.state == 'ready' and backend.owned
    finally:
        release.set()
        wait_gui(lambda: not window._stopping and not backend.owned)
    assert backend.state == 'stopped' and not window.runtime.running


def test_close_window_waits_for_owned_backend_process(window, tmp_path):
    """关闭 GUI 后等启动器完成温和回收，不能直接退出遗留模型进程。"""
    backend = attach_test_backend(window, tmp_path)
    window.show()
    window._toggle_backend()
    wait_gui(lambda: backend.state == 'ready')
    window.close()
    assert window.isVisible() and backend.owned
    wait_gui(lambda: not backend.owned and not window.isVisible())


def test_voice_start_requires_ready_backend_without_opening_devices(window, monkeypatch):
    """后端未就绪时不发起客户端启动或首次摄像头权限请求。"""
    window.backend.state = 'stopped'
    window.gateway_consent_chk.setChecked(True)
    def forbidden(*args):
        """没有后端时任何设备权限调用都是错误。"""
        pytest.fail('后端未就绪时访问了设备')
    import src.omni.media as media
    monkeypatch.setattr(media, 'request_camera_permission', forbidden)
    window._toggle_run()
    assert not window.runtime.running and window.runtime.starts == 0
    assert '先启动后端' in window.console.toPlainText()
