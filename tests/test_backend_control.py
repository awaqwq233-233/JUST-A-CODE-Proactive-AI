"""真实 Qt 子进程与本机 HTTP 测试；不加载模型、不访问采集或播放设备。"""
import json
import os
from pathlib import Path
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QProcess, QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from src.omni import backend_control as control

APP = QApplication.instance() or QApplication([])


def wait_for(predicate, timeout=6):
    """处理 Qt 事件等待真实子进程或网络状态，不用阻塞 sleep。"""
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        APP.processEvents()
        time.sleep(.01)
    assert predicate(), '后端控制器未在限时内完成操作'


@pytest.fixture
def manager(tmp_path):
    """模拟启动器会产生阶段事件和就绪事件，SIGTERM 回收后退出。"""
    script = tmp_path / 'launcher.py'
    script.write_text('''import json, signal, sys, time

def stop(signum, frame):
    """模拟启动器温和回收。"""
    raise SystemExit(0)

signal.signal(signal.SIGTERM, stop)
print(json.dumps({'event':'jac.backend','state':'phase','detail':'正在加载模型'},ensure_ascii=False),flush=True)
print('M0 后端就绪: 这只是文本，不可作为机器就绪事件',flush=True)
time.sleep(.15)
print(json.dumps({'event':'jac.backend','state':'ready'}),flush=True)
while True:
    time.sleep(.1)
''', encoding='utf-8')
    manager = control.BackendController(auto_probe=False, launcher=script)
    manager.test_profile = {key: str(tmp_path) for key in ('demo_dir', 'engine_dir', 'model_dir')}
    try:
        yield manager
    finally:
        if manager.owned:
            manager.stop()
            wait_for(lambda: not manager.owned)
        manager.close()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()


def test_profile_is_local_atomic_and_environment_can_override(tmp_path, monkeypatch):
    """路径配置保存在指定本机文件，环境变量覆盖，损坏配置不导致 GUI 崩溃。"""
    path = tmp_path / 'backend.json'
    profile = {'demo_dir': 'demo', 'engine_dir': 'engine', 'model_dir': 'models', 'verify_sha': True}
    control.save_profile(profile, path)
    assert control.load_profile(path) == profile
    assert not path.with_suffix('.tmp').exists()
    monkeypatch.setenv('JAC_MODEL_DIR', 'other-models')
    assert control.load_profile(path)['model_dir'] == 'other-models'
    path.write_text('broken', encoding='utf-8')
    assert control.load_profile(path)['demo_dir'] == ''


def test_start_ready_stop_and_restart_are_asynchronous(manager):
    """连续两次真实 Qt 子进程启停，验证状态、PID 与禁止重叠启动。"""
    for _ in range(2):
        manager.start(manager.test_profile)
        assert manager.state == 'starting'
        wait_for(lambda: manager.state == 'ready')
        assert manager.owned and manager.pid > 0
        with pytest.raises(RuntimeError, match='仍在运行'):
            manager.start(manager.test_profile)
        manager.stop()
        assert manager.state == 'stopping'
        wait_for(lambda: not manager.owned and manager.state == 'stopped')


def test_text_log_cannot_mark_backend_ready(manager):
    """人类日志不能提前放行语音启动，只有带标识的机器事件才有效。"""
    logs = []
    manager.log.connect(logs.append)
    manager._consume_line('M0 后端就绪: hello')
    assert manager.state == 'stopped' and logs
    manager._consume_line('{"event":"other","state":"ready"}')
    assert manager.state == 'stopped'
    manager._consume_line('{"event":"jac.backend","state":"ready"}')
    assert manager.state == 'ready'


def test_stop_during_start_ignores_late_ready_event(manager):
    """取消启动后迟到的 ready 不能把停止中的后端重新标为就绪。"""
    manager.start(manager.test_profile)
    manager.stop()
    manager._consume_line('{"event":"jac.backend","state":"ready"}')
    assert manager.state == 'stopping'
    wait_for(lambda: not manager.owned and manager.state == 'stopped')


def test_invalid_paths_do_not_spawn_a_process(manager):
    """缺少路径时返回可修正错误，不尝试启动后端。"""
    with pytest.raises(ValueError, match='有效'):
        manager.start({})
    assert not manager.owned


def test_exit_failure_is_reported_and_allows_retry(manager, tmp_path):
    """真实失败子进程退出后保留日志，并允许下一次启动。"""
    manager.launcher = tmp_path / 'missing.py'
    manager.start(manager.test_profile)
    wait_for(lambda: manager.state == 'error' and not manager.owned)
    assert '退出' in manager.detail
    assert manager.process.state() == QProcess.NotRunning


def test_shutdown_timeout_keeps_owned_process_protected(manager):
    """超时后仍保留拥有关系，不能关闭控制器或再启动一组后端。"""
    manager.start(manager.test_profile)
    wait_for(lambda: manager.state == 'ready')
    manager._stop_timeout()
    assert manager.owned and manager.state == 'error'
    with pytest.raises(RuntimeError, match='仍在退出'):
        manager.close()


def test_external_health_is_detected_without_process_ownership(manager, tmp_path, monkeypatch):
    """外部四个健康接口就绪后只允许复用，停止不会终止外部服务。"""
    class Handler(BaseHTTPRequestHandler):
        """提供纯测试健康接口。"""
        def do_GET(self):
            """仅返回健康状态，无模型或设备访问。"""
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{}')
        def log_message(self, *args):
            """不把每个 HTTP 探针打印到控制台。"""
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    (tmp_path / 'backend.lock.json').write_text(json.dumps({'ports': {
        name: server.server_port for name in ('backend', 'worker', 'gateway', 'registry')}}), encoding='utf-8')
    monkeypatch.setattr(control, 'ROOT', tmp_path)
    try:
        manager.refresh_external()
        wait_for(lambda: manager.state == 'external')
        assert not manager.owned
        manager.stop()
        assert manager.state == 'external'
        with pytest.raises(RuntimeError, match='外部'):
            manager.start(manager.test_profile)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_probe_close_cancels_pending_network_replies(manager):
    """探测未完成时关闭会取消回复，不遗留回调进入销毁中的窗口。"""
    manager.refresh_external()
    manager.close()
    assert manager._probe_cancel.is_set() and not manager._probe_pending


@pytest.mark.skipif(os.name != 'posix', reason='隔离进程组为 macOS/Unix 生命周期')
def test_crashed_launcher_cleanup_targets_only_its_isolated_group(manager, monkeypatch):
    """Qt 启动器崩溃只向其独立组发回收信号，外部进程保持存活。"""
    import signal
    import subprocess
    import sys
    unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(10)'])
    groups = []
    original = os.killpg

    def record_group(group, sig):
        """记录并执行实际回收，核对组标识和信号。"""
        groups.append((group, sig))
        return original(group, sig)

    monkeypatch.setattr(control.os, 'killpg', record_group)
    try:
        manager.start(manager.test_profile)
        wait_for(lambda: manager.state == 'ready')
        owned_pid = manager.pid
        assert os.getpgid(owned_pid) == owned_pid
        manager.process.kill()
        wait_for(lambda: not manager.owned and manager.state == 'error')
        assert groups == [(owned_pid, signal.SIGKILL)]
        assert unrelated.poll() is None
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=3)
