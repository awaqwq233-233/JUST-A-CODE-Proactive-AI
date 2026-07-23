import threading
import platform

PLATFORM = platform.system()
IS_WINDOWS = PLATFORM == 'Windows'
IS_MACOS = PLATFORM == 'Darwin'
IS_LINUX = PLATFORM == 'Linux'

class Speaker:
    """
    文本转语音 (TTS) 类
    负责让 J.A.C. 说话。
    兼容 Windows / macOS / Linux 三平台：
      - macOS:   pyttsx3（中文嗓色）+ 系统 say 命令兜底
      - Windows: pyttsx3
      - Linux:   pyttsx3（需 espeak/speech-dispatcher）+ espeak 命令兜底
    """
    def __init__(self):
        try:
            self.engine = None

            if IS_MACOS:
                self._init_macos_tts()
            elif IS_LINUX:
                self._init_linux_tts()
            else:
                self._init_windows_tts()
                
        except Exception as e:
            print(f"[错误] TTS 初始化失败: {e}")
            self.engine = None

    def _init_windows_tts(self):
        """Windows 平台 TTS 初始化"""
        import pyttsx3
        self.engine = pyttsx3.init()
        self.engine.setProperty('rate', 150)
        
        voices = self.engine.getProperty('voices')
        for voice in voices:
            if 'Chinese' in voice.name or 'CN' in voice.id:
                self.engine.setProperty('voice', voice.id)
                break

    def _init_macos_tts(self):
        """macOS 平台 TTS 初始化"""
        try:
            import pyttsx3
            self.engine = pyttsx3.init()
            self.engine.setProperty('rate', 150)
            
            voices = self.engine.getProperty('voices')
            for voice in voices:
                if 'Chinese' in voice.name or '普通话' in voice.name or 'zh' in voice.languages:
                    self.engine.setProperty('voice', voice.id)
                    break
        except Exception as e:
            print(f"[警告] pyttsx3 在 macOS 上初始化失败: {e}")
            print("[提示] 将使用系统自带的 say 命令")
            self.engine = 'say_command'

    def _init_linux_tts(self):
        """Linux 平台 TTS 初始化（优先 pyttsx3 + espeak/speech-dispatcher 兜底）"""
        try:
            import pyttsx3
            self.engine = pyttsx3.init()
            self.engine.setProperty('rate', 150)
            voices = self.engine.getProperty('voices')
            for voice in voices:
                # Linux 下 espeak 的中文嗓色通常含 'chinese' / 'zh' / 'yue'
                if 'chinese' in voice.name.lower() or 'zh' in voice.id.lower() \
                        or 'yue' in voice.id.lower():
                    self.engine.setProperty('voice', voice.id)
                    break
        except Exception as e:
            print(f"[警告] Linux 上 pyttsx3 初始化失败（通常需要 espeak 或 speech-dispatcher）: {e}")
            print("[提示] 将使用系统 espeak 命令")
            self.engine = 'espeak_command'

    def speak(self, text, emotion_hint=None):
        """
        朗读文字 (非阻塞模式)
        
        Args:
            text (str): 要朗读的内容
            emotion_hint (str): 情感提示（可选）
        """
        if self.engine is None:
            print(f"[TTS (模拟)] {text}")
            return

        print(f"[J.A.C. 说] {text}")

        if self.engine == 'say_command':
            threading.Thread(target=self._macos_say_command, args=(text,)).start()
        elif self.engine == 'espeak_command':
            threading.Thread(target=self._linux_espeak_command, args=(text,)).start()
        else:
            threading.Thread(target=self._speak_thread, args=(text,)).start()

    def _speak_thread(self, text):
        """
        内部使用的线程函数，避免阻塞主程序
        """
        try:
            if IS_MACOS:
                import pyttsx3
                local_engine = pyttsx3.init()
                local_engine.setProperty('rate', 150)
                
                voices = local_engine.getProperty('voices')
                for voice in voices:
                    if 'Chinese' in voice.name or '普通话' in voice.name or 'zh' in voice.languages:
                        local_engine.setProperty('voice', voice.id)
                        break
                
                local_engine.say(text)
                local_engine.runAndWait()
            else:
                import pyttsx3
                local_engine = pyttsx3.init()
                local_engine.setProperty('rate', 150)
                local_engine.say(text)
                local_engine.runAndWait()
        except Exception as e:
            print(f"[错误] TTS 播放失败: {e}")
            if IS_MACOS:
                self._macos_say_command(text)

    def _macos_say_command(self, text):
        """
        使用 macOS 系统自带的 say 命令进行语音合成
        """
        try:
            import subprocess
            subprocess.run(['say', '-v', 'Tingting', text], 
                         capture_output=True, text=True)
        except Exception as e:
            print(f"[错误] macOS say 命令失败: {e}")

    def _linux_espeak_command(self, text):
        """
        使用 Linux 系统自带的 espeak 命令进行语音合成
        """
        try:
            import subprocess
            subprocess.run(['espeak', text], capture_output=True, text=True)
        except Exception as e:
            print(f"[错误] Linux espeak 命令失败: {e}")

if __name__ == "__main__":
    speaker = Speaker()
    speaker.speak("你好，我是你的助手 J.A.C.，系统自检正常。")