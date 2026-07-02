import pyaudio
import wave
import os
import time
import collections
import webrtcvad
import numpy as np
import platform

# 平台检测
PLATFORM = platform.system()
IS_WINDOWS = PLATFORM == 'Windows'
IS_MACOS = PLATFORM == 'Darwin'

class AudioRecorder:
    """
    录音机类
    负责从麦克风录制音频并保存为临时文件。
    支持 VAD (语音活动检测) 以实现自动停止录音。
    兼容 Windows 和 macOS 平台。
    """
    def __init__(self, chunk=480, format=pyaudio.paInt16, channels=1, rate=16000, vad_aggressiveness=3):
        """
        初始化录音参数
        Whisper 模型通常使用 16kHz 采样率。
        VAD 要求 chunk 必须是 10/20/30ms。
        16000Hz * 0.03s = 480 frames.
        
        vad_aggressiveness: 0-3，3 最激进（过滤噪音最强）
        """
        self.chunk = chunk
        self.format = format
        self.channels = channels
        self.rate = rate
        self.p = pyaudio.PyAudio()
        self.frames = []
        self.is_recording = False
        self.stream = None
        self.vad = webrtcvad.Vad(vad_aggressiveness)
        
        self.energy_threshold = 500

    def start_recording(self):
        """开始录音"""
        self.frames = []
        
        try:
            if IS_MACOS:
                self.stream = self.p.open(
                    format=self.format,
                    channels=self.channels,
                    rate=self.rate,
                    input=True,
                    input_device_index=None,
                    frames_per_buffer=self.chunk
                )
            else:
                self.stream = self.p.open(
                    format=self.format,
                    channels=self.channels,
                    rate=self.rate,
                    input=True,
                    frames_per_buffer=self.chunk
                )
            self.is_recording = True
        except Exception as e:
            print(f"[错误] 启动录音失败: {e}")
            if IS_MACOS:
                print("[提示] macOS 用户请确保已授权麦克风访问权限")
            self.is_recording = False

    def stop_recording(self, output_filename="temp_audio.wav"):
        """
        停止录音并保存文件
        """
        self.is_recording = False
        if self.stream:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except Exception as e:
                print(f"[警告] 关闭流异常: {e}")
            self.stream = None
        
        try:
            wf = wave.open(output_filename, 'wb')
            wf.setnchannels(self.channels)
            wf.setsampwidth(self.p.get_sample_size(self.format))
            wf.setframerate(self.rate)
            wf.writeframes(b''.join(self.frames))
            wf.close()
        except Exception as e:
            print(f"[错误] 保存录音文件失败: {e}")
            return None
        
        return output_filename

    def listen_and_record(self, output_filename="temp_audio.wav", silence_timeout=1.2, max_duration=15.0, min_duration=0.5):
        """
        监听并自动录制说话片段 (VAD + Energy)
        
        Args:
            output_filename: 保存文件名
            silence_timeout: 说话停止后等待的静音时长（秒）
            max_duration: 最大录音时长（秒）
            min_duration: 最小录音时长（秒），低于此值视为噪音
        """
        self.start_recording()
        
        if not self.is_recording:
            print("[错误] 录音未启动，无法监听")
            return None
        
        trigger_window = collections.deque(maxlen=10)
        trigger_threshold = 8
        
        speech_detected = False
        silence_start_time = None
        start_time = time.time()
        
        print("[耳朵] 正在聆听...", end="\r")
        
        while True:
            try:
                data = self.stream.read(self.chunk, exception_on_overflow=False)
            except Exception as e:
                print(f"\n[错误] 录音异常: {e}")
                break
            
            audio_data = np.frombuffer(data, dtype=np.int16)
            if len(audio_data) == 0:
                rms = 0
            else:
                rms = int(np.sqrt(np.mean(audio_data.astype(np.float32)**2)))
            
            is_speech = False
            if rms > self.energy_threshold:
                try:
                    is_speech = self.vad.is_speech(data, self.rate)
                except:
                    is_speech = False
            
            if not speech_detected:
                trigger_window.append((data, is_speech))
                
                if len(trigger_window) == trigger_window.maxlen:
                    speech_count = sum(1 for _, s in trigger_window if s)
                    
                    if speech_count >= trigger_threshold:
                        print(f"\n[耳朵] 检测到有效语音 (RMS:{rms})，开始录制...")
                        speech_detected = True
                        for d, _ in trigger_window:
                            self.frames.append(d)
            else:
                self.frames.append(data)
                
                if is_speech:
                    silence_start_time = None
                else:
                    if silence_start_time is None:
                        silence_start_time = time.time()
                    elif time.time() - silence_start_time > silence_timeout:
                        current_duration = len(self.frames) * (self.chunk / self.rate)
                        if current_duration < min_duration:
                            print(f"[耳朵] 录音太短 ({current_duration:.2f}s < {min_duration}s)，视为噪音，继续监听...")
                            speech_detected = False
                            self.frames = []
                            trigger_window.clear()
                            silence_start_time = None
                            print("[耳朵] 正在聆听...", end="\r")
                            continue
                        else:
                            print(f"[耳朵] 说话结束 (时长: {current_duration:.2f}s)。")
                            break
                        
                if time.time() - start_time > max_duration:
                    print("[耳朵] 达到最大录音时长。")
                    break
        
        return self.stop_recording(output_filename)

    def record_chunk(self):
        """
        录制一个小片段（需要在循环中调用）
        """
        if self.is_recording and self.stream:
            data = self.stream.read(self.chunk)
            self.frames.append(data)

    def record_seconds(self, seconds=5, output_filename="temp_audio.wav"):
        """
        录制指定时长的音频（阻塞式）
        方便快速测试。
        """
        self.start_recording()
        for _ in range(0, int(self.rate / self.chunk * seconds)):
            self.record_chunk()
        return self.stop_recording(output_filename)

    def __del__(self):
        """析构函数，释放资源"""
        try:
            self.p.terminate()
        except Exception as e:
            print(f"[警告] 释放音频资源异常: {e}")

if __name__ == "__main__":
    recorder = AudioRecorder()
    print("录音测试：请说话5秒钟...")
    filename = recorder.record_seconds(5)
    print(f"录音已保存: {filename}")