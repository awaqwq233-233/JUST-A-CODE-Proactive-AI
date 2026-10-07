"""只在 CPU 子进程加载 Whisper；PCM 与转写经内存管道传递。"""

import hashlib
import json
import base64
import queue
import subprocess
import sys
import os
from pathlib import Path
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_DIR = Path.home() / ".cache/jac/models/whisper-small"


class StableMelProduct:
    """等价的 NumPy mel 矩阵乘法，避免本机 Accelerate 浮点状态误报。"""

    def __init__(self, matrix):
        """保留官方 mel 滤波矩阵，不更改模型或前处理参数。"""
        self.matrix = matrix

    def __matmul__(self, values):
        """用确定求和核计算，并拒绝任何非有限输入/输出。"""
        import numpy as np
        if not np.isfinite(values).all():
            raise ValueError("Whisper 特征输入非有限")
        result = np.einsum("ij,jk->ik", self.matrix, values, optimize=False)
        if not np.isfinite(result).all():
            raise ValueError("Whisper mel 特征非有限")
        return result


def verify_resources(model_dir):
    """禁止仓库内模型，逐文件验证固定资源，不在运行时下载。"""
    directory = Path(model_dir).expanduser().resolve()
    if directory.is_relative_to(ROOT):
        raise ValueError("Whisper 模型目录必须在仓库外")
    lock = json.loads((ROOT / "transcription.lock.json").read_text(encoding="utf-8"))
    for name, expected in lock["files"].items():
        path = directory / name
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected:
            raise ValueError(f"Whisper 资源哈希不符：{name}")
    return directory


def worker_main(directory):
    """独立解释器只加载 CPU Whisper；JSON 管道不引入 Qt/OpenCV 或共享信号量。"""
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["OMP_NUM_THREADS"] = "2"
    os.environ["VECLIB_MAXIMUM_THREADS"] = "2"
    try:
        import numpy as np
        from faster_whisper import WhisperModel
        model = WhisperModel(str(directory), device="cpu", compute_type="int8",
                             cpu_threads=2, num_workers=1, local_files_only=True)
        model.feature_extractor.mel_filters = StableMelProduct(model.feature_extractor.mel_filters)
        print(json.dumps({"ready": True, "isolated": "cv2" not in sys.modules and "torch" not in sys.modules}), flush=True)
        while True:
            line = sys.stdin.buffer.readline(1100000)
            if not line:
                return
            request = json.loads(line)
            raw = base64.b64decode(request["audio"], validate=True)
            samples = np.frombuffer(raw, dtype="<f4")
            if not 4800 <= len(samples) <= 192000 or not np.isfinite(samples).all():
                raise ValueError("Whisper input")
            segments, _ = model.transcribe(samples, language="zh", beam_size=5,
                temperature=0, condition_on_previous_text=False, vad_filter=False)
            result = [{"text": s.text, "avg_logprob": s.avg_logprob,
                       "no_speech_prob": s.no_speech_prob, "compression_ratio": s.compression_ratio}
                      for s in segments]
            print(json.dumps({"segments": result}, ensure_ascii=False), flush=True)
    except Exception as error:
        print(json.dumps({"error": type(error).__name__}), flush=True)


class WhisperProcess:
    """拥有单一 Whisper 子进程，停止时可回收在途原生 CPU 推理。"""

    def __init__(self, model_dir):
        """记录资源路径；创建对象不加载模型、联网或访问设备。"""
        self.model_dir = model_dir
        self.process = self.reader = None
        self.responses = queue.Queue(maxsize=2)
        self.stopped = threading.Event()
        self._stop_lock = threading.Lock()

    def start(self):
        """在后台完成哈希预检和子进程就绪等待，设备启动前调用。"""
        directory = verify_resources(self.model_dir)
        with self._stop_lock:
            if self.stopped.is_set():
                raise RuntimeError("Whisper 启动已取消")
            self.process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                "--worker", str(directory)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, cwd=ROOT, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            self.reader = threading.Thread(target=self._read_output, name="jac-whisper-pipe", daemon=True)
            self.reader.start()
        try:
            result = self._receive(45)
            if result != {"ready": True, "isolated": True}:
                raise RuntimeError("Whisper CPU 子进程初始化失败")
        except BaseException:
            self.stop()
            raise

    def _read_output(self):
        """专用读线程跨平台读取子进程，始终有界且不输出转写正文。"""
        try:
            while True:
                line = self.process.stdout.readline(16384)
                if not line:
                    return
                result = json.loads(line)
                self.responses.put_nowait(result)
        except (OSError, ValueError, queue.Full):
            self.stopped.set()

    def _receive(self, timeout):
        """限时等待响应，停止/进程异常立即退出。"""
        deadline = time.monotonic() + timeout
        while not self.stopped.is_set():
            try:
                result = self.responses.get(timeout=.1)
                if not isinstance(result, dict) or "error" in result:
                    raise RuntimeError("Whisper CPU 转写失败")
                return result
            except queue.Empty:
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError("Whisper CPU 转写超时")
            if self.process.poll() is not None:
                raise RuntimeError("Whisper CPU 子进程已退出")
        raise RuntimeError("Whisper CPU 转写已停止")

    def transcribe(self, samples):
        """处理一条有界完整语音，供专用转写线程调用。"""
        if self.stopped.is_set():
            raise RuntimeError("Whisper 已停止")
        import numpy as np
        raw = np.asarray(samples, dtype="<f4").tobytes()
        if not 19200 <= len(raw) <= 768000:
            raise ValueError("Whisper 输入须为有界完整句")
        self.process.stdin.write((json.dumps({"audio": base64.b64encode(raw).decode("ascii")}) + "\n").encode("ascii"))
        self.process.stdin.flush()
        return self._receive(30)["segments"]

    def stop(self):
        """只回收自己拥有的子进程，不等待原生推理自然结束。"""
        with self._stop_lock:
            self.stopped.set()
            process = self.process
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(2)

    def request_stop(self):
        """仅设置取消标志，供跨线程启动取消使用，不阻塞音频/Qt。"""
        self.stopped.set()

    def close(self):
        """转写线程结束后释放管道，确认独立读取线程也已退出。"""
        if self.reader is not None:
            self.reader.join(2)
            if self.reader.is_alive():
                raise RuntimeError("Whisper 管道读取线程未退出")
        if self.process is not None:
            self.process.stdin.close()
            self.process.stdout.close()
            self.process = None


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        worker_main(sys.argv[2])
    else:
        raise SystemExit("仅供固定 CPU 子进程使用")
