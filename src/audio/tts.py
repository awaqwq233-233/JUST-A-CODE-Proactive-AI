import threading
import platform

PLATFORM = platform.system()
IS_WINDOWS = PLATFORM == 'Windows'
IS_MACOS = PLATFORM == 'Darwin'

class Speaker:
    """
    文本转语音 (TTS) 类
    负责让 J.A.C. 说话。
    兼容 Windows 和 macOS 平台。
    """
    def __init__(self):
        try:
            self.engine = None
            
            if IS_MACOS:
                self._init_macos_tts()
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

if __name__ == "__main__":
    speaker = Speaker()
    speaker.speak("你好，我是你的助手 J.A.C.，系统自检正常。")