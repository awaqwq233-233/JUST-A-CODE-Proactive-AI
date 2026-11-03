"""异步向量索引适配器；可用性不影响 JSON 提交与主媒体线程。"""
from concurrent.futures import Future
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time

from .vector_worker import DEFAULT_MODEL, ROOT


class VectorIndex:
    """串行访问独立进程；快照合并，查询有预算，索引可从真源重建。"""

    def __init__(self, directory, model_dir=None):
        """仅建立有界请求队列；模型加载在后台发生。"""
        self.directory, self.model_dir = Path(directory), Path(model_dir or DEFAULT_MODEL)
        self.requests = queue.Queue(32)
        self.responses = queue.Queue(2)
        self.process = None
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.error = None
        self.thread = threading.Thread(target=self._loop, daemon=True, name='jac-memory-index')
        self.thread.start()

    def _rpc(self, data, timeout=20):
        """单一 IPC 所有者读取有界回执；超时停止进程避免回执串线。"""
        if data is not None:
            self.process.stdin.write(json.dumps(data, ensure_ascii=False) + '\n')
            self.process.stdin.flush()
        deadline = time.monotonic() + timeout
        while not self.stop.is_set() and time.monotonic() < deadline:
            try:
                response = self.responses.get(timeout=.05)
            except queue.Empty:
                continue
            if isinstance(response, Exception):
                raise response
            if 'error' in response:
                raise RuntimeError('向量进程处理失败：' + response['error'])
            return response.get('result', response)
        raise TimeoutError('向量服务等待超时')

    def _read_responses(self):
        """专用线程读取管道，兼容 Windows 不支持 select 匿名管道的限制。"""
        try:
            while not self.stop.is_set():
                line = self.process.stdout.readline(262144)
                if not line or len(line) >= 262144:
                    raise RuntimeError('向量进程已退出或回执超限')
                self.responses.put(json.loads(line), timeout=1)
        except Exception as error:
            try:
                self.responses.put(error, timeout=1)
            except queue.Full:
                pass

    def _loop(self):
        """后台启动后串行处理索引命令；故障保留 JSON 并向调用方显式降级。"""
        try:
            interpreter = ROOT / '.cache/memory/venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            if not interpreter.is_file():
                raise RuntimeError('记忆向量环境未安装')
            env = dict(os.environ, PYTHONUTF8='1', ANONYMIZED_TELEMETRY='False', HF_HUB_OFFLINE='1', TOKENIZERS_PARALLELISM='false')
            self.process = subprocess.Popen([str(interpreter), '-m', 'src.memory.longterm.vector_worker', str(self.directory), str(self.model_dir)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding='utf-8', bufsize=1, cwd=ROOT, env=env)
            threading.Thread(target=self._read_responses, daemon=True, name='jac-memory-ipc').start()
            self._rpc(None, 45)
            self.ready.set()
            while not self.stop.is_set():
                try:
                    request, future = self.requests.get(timeout=.1)
                except queue.Empty:
                    continue
                if not future.set_running_or_notify_cancel():
                    continue
                try:
                    future.set_result(self._rpc(request))
                except Exception as error:
                    future.set_exception(error)
                    raise
        except Exception as error:
            self.error = str(error)
            self.ready.set()
        finally:
            if self.process:
                self.process.terminate()
                try:
                    self.process.wait(3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(3)
            while not self.requests.empty():
                _, future = self.requests.get_nowait()
                if not future.done():
                    future.set_exception(RuntimeError(self.error or '向量服务已关闭'))

    def request(self, op, **values):
        """立即返回 Future，调用方自行决定等待预算。"""
        future = Future()
        if self.error or self.stop.is_set():
            future.set_exception(RuntimeError(self.error or '向量服务已关闭'))
        else:
            try:
                self.requests.put_nowait(({'op': op, **values}, future))
            except queue.Full:
                future.set_exception(RuntimeError('向量请求积压'))
        return future

    def close(self):
        """回收自有进程，不影响其他后端或任务。"""
        self.stop.set()
        self.thread.join(5)
