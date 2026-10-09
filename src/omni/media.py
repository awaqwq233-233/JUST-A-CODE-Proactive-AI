"""SoundDevice 与后台 OpenCV 采集/原生播放；不保存原始媒体。"""

from __future__ import annotations

import queue
import threading
import time

import cv2
import numpy as np
import sounddevice as sd
import soxr


def request_camera_permission(camera: int = 0) -> None:
    """macOS 首次权限在启动主线程申请，此处不读取或编码视频。"""
    import sys
    if sys.platform != "darwin":
        return
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("macOS 摄像头首次权限须由启动主线程申请")
    probe = cv2.VideoCapture(camera)
    try:
        if not probe.isOpened():
            raise RuntimeError("macOS 摄像头权限未授予")
    finally:
        probe.release()


class LiveDevices:
    """隔离音视频采集与播放，所有缓冲有界且不写原始媒体文件。"""

    def __init__(self, input_device: int | None, output_device: int | None, camera: int,
                 *, fps: int = 5, mic_gain: float = 1.0, video_enabled: bool = True):
        """创建缓冲与统计项，不在构造时打开设备。"""
        self.input_device, self.output_device, self.camera = input_device, output_device, camera
        if not 5 <= fps <= 10 or not np.isfinite(mic_gain) or mic_gain <= 0:
            raise ValueError("采集帧率须为 5–10fps，麦克风增益须为正数")
        self.fps, self.mic_gain, self.video_enabled = fps, mic_gain, video_enabled
        self.frames = queue.Queue(maxsize=1)
        self.raw_audio = queue.Queue(maxsize=30)
        self.audio = queue.Queue(maxsize=5)
        self.video = queue.Queue(maxsize=1)
        self.playback = queue.Queue(maxsize=32)
        self.stop_event = threading.Event()
        self.camera_ready = threading.Event()
        self.error = None
        self.mic = self.speaker = None
        self.threads = []
        self.pending_output = np.zeros(0, dtype="<f4")
        self.played_samples = self.captured_frames = 0
        self.audio_queue_max = self.playback_queue_max = 0
        self.input_status_events = self.output_status_events = 0
        self.mic_rms_max = 0.0
        self.mic_rms_current = 0.0
        self.audio_tap = None
        self.playback_active_until = 0.0
        self.pending_output_cancel = None

    def microphone_callback(self, data, frames, timing, status) -> None:
        """实时回调只复制入队；溢出显式失败，绝不悄悄丢弃用户语音。"""
        if status:
            self.input_status_events += 1
            self.error = f"麦克风流状态异常: {status}"
        try:
            self.raw_audio.put_nowait(data[:, 0].copy())
        except queue.Full:
            self.error = "麦克风原始音频队列溢出"

    def speaker_callback(self, output, frames, timing, status) -> None:
        """顺序播放原生 24k 音频；没有待播数据时填零，不阻塞设备回调。"""
        if status:
            self.output_status_events += 1
        output.fill(0)
        offset = 0
        while offset < frames:
            if self.pending_output_cancel is not None and self.pending_output_cancel.is_set():
                self.pending_output = np.zeros(0, dtype="float32")
                self.pending_output_cancel = None
            if not len(self.pending_output):
                try:
                    self.pending_output = self.playback.get_nowait()
                    self.pending_output_cancel = None
                    if isinstance(self.pending_output, tuple):
                        self.pending_output, self.pending_output_cancel = self.pending_output
                        if self.pending_output_cancel.is_set():
                            continue
                except queue.Empty:
                    return
            count = min(frames - offset, len(self.pending_output))
            output[offset:offset + count, 0] = self.pending_output[:count]
            self.pending_output = self.pending_output[count:]
            offset += count
            self.played_samples += count
            self.playback_active_until = time.monotonic() + .3

    def audio_worker(self) -> None:
        """后台流式重采样 48k→16k，按完整一秒入队并保留波形顺序。"""
        resampler = soxr.ResampleStream(48000, 16000, 1, dtype="float32")
        pending = np.zeros(0, dtype="<f4")
        while not self.stop_event.is_set():
            try:
                raw = self.raw_audio.get(timeout=0.2)
            except queue.Empty:
                continue
            self.mic_rms_current = float(np.sqrt(np.mean(raw * raw)))
            self.mic_rms_max = max(self.mic_rms_max, self.mic_rms_current)
            converted = np.clip(resampler.resample_chunk(raw) * self.mic_gain, -1, 1)
            if self.audio_tap is not None:
                self.audio_tap(converted, time.monotonic() < self.playback_active_until
                               or bool(len(self.pending_output)) or not self.playback.empty())
            pending = np.concatenate((pending, converted))
            while len(pending) >= 16000:
                try:
                    self.audio.put_nowait(pending[:16000].copy())
                except queue.Full:
                    self.error = "一秒音频队列溢出，终止探针而不丢弃语音"
                    return
                self.audio_queue_max = max(self.audio_queue_max, self.audio.qsize())
                pending = pending[16000:]

    def camera_worker(self) -> None:
        """独立线程读取与编码 640×480 JPEG，5–10fps 采集且只保留最新帧。"""
        camera = cv2.VideoCapture(self.camera)
        try:
            camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            camera.set(cv2.CAP_PROP_FPS, self.fps)
            if not camera.isOpened():
                self.error = "摄像头无法打开或权限未授予"
                return
            while not self.stop_event.is_set():
                started = time.monotonic()
                success, frame = camera.read()
                if not success:
                    self.error = "摄像头读取失败"
                    return
                if frame.shape[:2] != (480, 640):
                    frame = cv2.resize(frame, (640, 480))
                success, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                if not success:
                    self.error = "JPEG 编码失败"
                    return
                if self.video.full():
                    self.video.get_nowait()
                self.video.put_nowait(encoded.tobytes())
                if self.frames.full():
                    self.frames.get_nowait()
                self.frames.put_nowait(frame)
                self.captured_frames += 1
                self.camera_ready.set()
                self.stop_event.wait(max(0, 1 / self.fps - (time.monotonic() - started)))
        finally:
            camera.release()
            self.camera_ready.set()

    def start(self) -> None:
        """启动设备流与两个后台线程；失败也可由 stop 完整回收。"""
        self.mic = sd.InputStream(device=self.input_device, samplerate=48000, channels=1,
                                  dtype="float32", blocksize=4800, callback=self.microphone_callback)
        self.speaker = sd.OutputStream(device=self.output_device, samplerate=24000, channels=1,
                                      dtype="float32", blocksize=1200, callback=self.speaker_callback)
        self.threads = [threading.Thread(target=self.audio_worker, name="m0-audio"),
                        threading.Thread(target=self.camera_worker, name="m0-camera")]
        if not self.video_enabled:
            self.threads = self.threads[:1]
            self.camera_ready.set()
        self.speaker.start()
        self.mic.start()
        for worker in self.threads:
            worker.start()

    def enqueue_output(self, samples: np.ndarray) -> None:
        """把原生输出交给播放回调，播放积压超界时显式失败。"""
        try:
            self.playback.put_nowait(samples)
        except queue.Full as error:
            raise RuntimeError("原生音频播放队列溢出") from error
        self.playback_queue_max = max(self.playback_queue_max, self.playback.qsize())

    def enqueue_task_output(self, samples, cancellation) -> None:
        """沿用同一播放流，取消标志使停止或重连后的排队任务块也不会发声。"""
        self.enqueue_output((samples, cancellation))

    def latest_video(self) -> bytes | None:
        """在 Queue 自身互斥锁内读取最新帧，不暴露非线程安全的队列访问。"""
        with self.video.mutex:
            return self.video.queue[-1] if self.video.queue else None

    def latest_frame(self):
        """为 GUI 返回最新原始帧副本，读取不消耗采集队列。"""
        with self.frames.mutex:
            return self.frames.queue[-1].copy() if self.frames.queue else None

    def finish_input(self) -> None:
        """上行结束即停止采集，避免等待尾部 TTS 时音频队列被继续填满。"""
        self.stop_event.set()
        if self.mic is not None:
            self.mic.abort()
            self.mic.close()
            self.mic = None

    def stop(self) -> bool:
        """停止流、释放摄像头并等待线程退出，不影响其他应用的设备。"""
        self.stop_event.set()
        for stream in (self.mic, self.speaker):
            if stream is not None:
                stream.abort()
                stream.close()
        for worker in self.threads:
            if worker.ident is not None:
                worker.join(timeout=5)
        return all(not worker.is_alive() for worker in self.threads)
