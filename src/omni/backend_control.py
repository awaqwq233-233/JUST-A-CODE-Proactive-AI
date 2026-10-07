"""Qt 后端控制器：复用固定启动器，所有进程和健康检查均异步。"""
from __future__ import annotations

import json
import codecs
import os
from pathlib import Path
import signal
import sys
import threading

import httpx

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal, Qt

ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / '.cache/gui/backend.json'
LAUNCHER = ROOT / 'new_computer_download/start_m0_backend.py'


def load_profile(path=PROFILE):
    """读取本机路径配置，损坏时返回空配置，环境变量优先。"""
    try:
        profile = json.loads(Path(path).read_text(encoding='utf-8'))
        if not isinstance(profile, dict):
            profile = {}
    except (OSError, ValueError):
        profile = {}
    for key, env in (('demo_dir', 'JAC_DEMO_DIR'), ('engine_dir', 'JAC_ENGINE_DIR'),
                     ('model_dir', 'JAC_MODEL_DIR')):
        profile[key] = os.environ.get(env, profile.get(key, ''))
    profile['verify_sha'] = bool(profile.get('verify_sha', True))
    return profile


def save_profile(profile, path=PROFILE):
    """原子保存本机后端路径，不写 Git 配置或模型内容。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


class BackendController(QObject):
    """只控制本窗口启动的启动器；外部服务仅探测，不接管。"""

    changed = Signal(str, str)
    log = Signal(str)
    _probe_result = Signal(bool)

    def __init__(self, parent=None, *, auto_probe=True, launcher=LAUNCHER):
        """配置 QProcess 和网络探针，不启动模型或访问用户设备。"""
        super().__init__(parent)
        self.state, self.detail = 'stopped', '后端未启动'
        self.launcher, self.pid = Path(launcher), 0
        self._buffer, self._requested_stop = '', False
        self._decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        self._probe_cancel = threading.Event()
        self._probe_pending = False
        self._closed = False
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        if os.name == 'posix':
            parameters = QProcess.UnixProcessParameters()
            parameters.flags = QProcess.UnixProcessFlag.CreateNewSession
            self.process.setUnixProcessParameters(parameters)
        self.process.started.connect(self._started)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._process_error)
        self.stop_timer = QTimer(self)
        self.stop_timer.setSingleShot(True)
        self.stop_timer.timeout.connect(self._stop_timeout)
        self._probe_result.connect(self._health_result, Qt.QueuedConnection)
        self.probe_timer = QTimer(self)
        self.probe_timer.timeout.connect(self.refresh_external)
        if auto_probe:
            self.probe_timer.start(3000)
            QTimer.singleShot(0, self.refresh_external)

    @property
    def owned(self):
        """进程仍存在时才允许本窗口执行停止。"""
        return not self._closed and self.process.state() != QProcess.NotRunning

    def _set_state(self, state, detail):
        """发布给 GUI 的状态，不使用启动进程存在作为就绪判据。"""
        self.state, self.detail = state, detail
        self.changed.emit(state, detail)

    def start(self, profile):
        """启动校验过的固定脚本，参数独立传入，禁止 shell 拼接或重复启动。"""
        if self.owned:
            raise RuntimeError('本窗口的后端仍在运行，请先停止')
        if self.state == 'external':
            raise RuntimeError('外部后端已运行，可直接启动语音会话')
        paths = {}
        for key in ('demo_dir', 'engine_dir', 'model_dir'):
            value = str(profile.get(key, '')).strip()
            if not value or not Path(value).expanduser().is_dir():
                raise ValueError('请在后端设置中选择有效的 Demo、引擎和模型目录')
            paths[key] = str(Path(value).expanduser().resolve())
        if sys.version_info[:2] != (3, 11):
            raise RuntimeError('后端必须使用项目的 Python 3.11 环境')
        self.pid, self._buffer, self._requested_stop = 0, '', False
        self._decoder.reset()
        arguments = ['-u', str(self.launcher), '--demo-dir', paths['demo_dir'],
                     '--engine-dir', paths['engine_dir'], '--model-dir', paths['model_dir'],
                     '--events-json']
        if profile.get('verify_sha', True):
            arguments.append('--verify-sha')
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert('PYTHONUNBUFFERED', '1')
        environment.insert('PYTHONIOENCODING', 'utf-8')
        self.process.setProcessEnvironment(environment)
        self.process.setWorkingDirectory(str(ROOT))
        self._set_state('starting', '正在校验固定后端与模型…')
        self.process.start(sys.executable, arguments)

    def _started(self):
        """记录本窗口创建的隔离进程组；启动期间取消也会温和终止。"""
        self.pid = int(self.process.processId())
        if self._requested_stop:
            self.process.terminate()

    def stop(self):
        """发送终止请求，由启动器逆序回收自己的三进程，不阻塞 Qt。"""
        if not self.owned:
            return
        self._requested_stop = True
        self._set_state('stopping', '正在回收后端进程…')
        self.process.terminate()
        self.stop_timer.start(45000)

    def _read_output(self):
        """按完整行解析机器事件；其他启动信息写入连接日志。"""
        self._buffer += self._decoder.decode(bytes(self.process.readAllStandardOutput()))
        lines = self._buffer.split('\n')
        self._buffer = lines.pop()[-16384:]
        for line in lines:
            self._consume_line(line)

    def _consume_line(self, line):
        """只有明确的 ready 事件才开放语音启动，文本日志不触发就绪。"""
        try:
            event = json.loads(line)
        except ValueError:
            event = None
        if isinstance(event, dict) and event.get('event') == 'jac.backend':
            if self._requested_stop:
                return
            state, detail = event.get('state'), event.get('detail', '')
            if state == 'ready':
                self._set_state('ready', '后端已就绪 · 本窗口管理')
            elif state == 'phase':
                self._set_state('starting', str(detail))
        elif line.strip():
            self.log.emit('[后端] ' + line + '\n')

    def _finished(self, code, status):
        """进程退出后解除占用；异常崩溃时清理其隔离组中的遗留子进程。"""
        self.stop_timer.stop()
        self._read_output()
        if self._buffer:
            self._consume_line(self._buffer)
            self._buffer = ''
        if status == QProcess.CrashExit and os.name == 'posix' and self.pid:
            try:
                os.killpg(self.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError as error:
                self.log.emit(f'[后端] 回收隔离进程组失败：{error}\n')
        self.pid = 0
        if self._requested_stop:
            self._set_state('stopped', '后端已停止')
        else:
            self._set_state('error', f'后端退出（{code}），请查看连接日志')

    def _process_error(self, error):
        """启动文件不可执行等失败明确反馈，仍存在的进程保持受控。"""
        if error == QProcess.FailedToStart:
            self._set_state('error', '后端启动失败：' + self.process.errorString())
            self.log.emit('[后端] ' + self.process.errorString() + '\n')

    def _stop_timeout(self):
        """超时保留停止入口，不开放重叠启动或强制退出窗口。"""
        if self.owned:
            self._set_state('error', '停止尚未完成，请重试停止后端')

    def refresh_external(self):
        """后台探测固定本机端口，不阻塞 Qt 或干预已存在的服务。"""
        if self.owned or self._probe_pending or self._closed:
            return
        ports = json.loads((ROOT / 'backend.lock.json').read_text(encoding='utf-8'))['ports']
        self._probe_pending = True

        def probe():
            """只读取固定健康端点，窗口关闭后不再向 Qt 投递状态。"""
            ok = True
            try:
                with httpx.Client(trust_env=False, timeout=1) as client:
                    for port in ports.values():
                        if self._probe_cancel.is_set():
                            return
                        if client.get(f'http://127.0.0.1:{port}/health').status_code != 200:
                            ok = False
            except httpx.HTTPError:
                ok = False
            if not self._closed:
                try:
                    self._probe_result.emit(ok)
                except RuntimeError:
                    pass  # 父窗口已销毁时丢弃探测结果。

        threading.Thread(target=probe, daemon=True, name='jac-backend-health').start()

    def _health_result(self, ok):
        """四个固定健康接口都通过才标记外部就绪，拥有进程忽略旧探测。"""
        self._probe_pending = False
        if self._closed or self.owned:
            return
        if ok:
            self._set_state('external', '外部后端已就绪 · 不由本窗口停止')
        elif self.state == 'external':
            self._set_state('stopped', '外部后端已断开')

    def close(self):
        """窗口完成回收后停止健康探测，存活的拥有进程不允许丢弃。"""
        if self._closed:
            return
        if self.owned:
            raise RuntimeError('后端仍在退出，请等待完成')
        self._closed = True
        self.probe_timer.stop()
        self._probe_cancel.set()
        self._probe_pending = False
        self.stop_timer.stop()
        for connection in (self.process.started, self.process.readyReadStandardOutput,
                           self.process.finished, self.process.errorOccurred):
            connection.disconnect()
        self._probe_result.disconnect()
        self.process.deleteLater()
        self.deleteLater()
