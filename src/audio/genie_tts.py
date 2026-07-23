import os
import time
import random
import json
import glob
import platform

PLATFORM = platform.system()
IS_WINDOWS = PLATFORM == 'Windows'
IS_MACOS = PLATFORM == 'Darwin'
IS_LINUX = PLATFORM == 'Linux'

try:
    import genie_tts as genie
except ImportError:
    genie = None

class GenieSpeaker:
    """
    使用 Genie-TTS 进行语音合成的播报器
    依赖 GPT-SoVITS 的 ONNX 推理引擎 genie_tts
    兼容 Windows / macOS / Linux 平台（降级路径随平台自动选择）。
    """
    def __init__(self,
                 character_name=None,
                 onnx_model_dir=None,
                 reference_audio_path=None,
                 reference_audio_text=None,
                 output_dir="temp/voice"):
        """
        初始化播报器
        
        Args:
            character_name (str): 角色名称（用于加载角色）
            onnx_model_dir (str): 角色 ONNX 模型目录
            reference_audio_path (str): 情感/语气参考音频路径
            reference_audio_text (str): 参考音频对应文本
            output_dir (str): 输出语音的目录
        """
        self.character_name = character_name or os.getenv("GENIE_CHARACTER_NAME", "jac")
        self.onnx_model_dir = onnx_model_dir or os.getenv("GENIE_ONNX_DIR", "genie_assets/onnx")
        self.language = os.getenv("GENIE_LANGUAGE", "Chinese")
        
        self.prompt_map = {}
        prompt_json_path = os.path.join(os.path.dirname(self.onnx_model_dir), "prompt_wav.json")
        if os.path.exists(prompt_json_path):
            try:
                with open(prompt_json_path, 'r', encoding='utf-8') as f:
                    self.prompt_map = json.load(f)
            except Exception as e:
                print(f"[警告] 无法读取 prompt_wav.json: {e}")

        self.voice_samples = []
        assets_dir = os.path.dirname(self.onnx_model_dir)
        for ext in ["*.mp3", "*.wav"]:
            for filepath in glob.glob(os.path.join(assets_dir, ext)):
                filename = os.path.basename(filepath)
                if "zh_vo_Main" in filename or filename == "ref.wav":
                    continue
                
                ref_text = ""
                if filename in self.prompt_map:
                    ref_text = self.prompt_map[filename].get("text", "")
                
                self.voice_samples.append({
                    "path": filepath,
                    "text": ref_text or "..."
                })
        
        if self.voice_samples:
            print(f"[系统] 已加载 {len(self.voice_samples)} 个额外语音样本用于随机变换。")

        default_ref_wav = "genie_assets/zh_vo_Main_Linaxita_2_1_10_26.wav"
        default_ref_text = "在此之前，请您务必继续享受旅居拉古那的时光。"
        
        if not os.path.exists(default_ref_wav) and os.path.exists("genie_assets/ref.wav"):
            default_ref_wav = "genie_assets/ref.wav"
            default_ref_text = "你好，这是参考语音样本。"
            
        self.reference_audio_path = reference_audio_path or os.getenv("GENIE_REF_AUDIO", default_ref_wav)
        self.reference_audio_text = reference_audio_text or os.getenv("GENIE_REF_TEXT", default_ref_text)
        self.output_dir = output_dir
        
        self.available = False
        if genie is None:
            print("[警告] 未安装 genie-tts，无法使用 GenieSpeaker。")
            return
        
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir, exist_ok=True)
        
        try:
            if not self.onnx_model_dir or not os.path.isdir(self.onnx_model_dir):
                print(f"[警告] 未提供有效的 ONNX 模型目录: {self.onnx_model_dir}")
                self.available = False
                return

            required_files = [
                "t2s_encoder_fp32.onnx", 
                "t2s_first_stage_decoder_fp32.onnx",
                "vits_fp32.onnx"
            ]
            missing_files = [f for f in required_files if not os.path.exists(os.path.join(self.onnx_model_dir, f))]
            if missing_files:
                print(f"[警告] Genie-TTS 模型目录缺失关键文件: {missing_files}")
                print(f"请确保 '{self.onnx_model_dir}' 包含完整的 GPT-SoVITS V2 ONNX 模型。")
                self.available = False
                return

            genie.load_character(
                character_name=self.character_name,
                onnx_model_dir=self.onnx_model_dir,
                language=self.language
            )
            if self.reference_audio_path and os.path.exists(self.reference_audio_path):
                genie.set_reference_audio(
                    character_name=self.character_name,
                    audio_path=self.reference_audio_path,
                    audio_text=self.reference_audio_text
                )
            else:
                print("[提示] 未设置参考音频，将使用基础合成效果。")
            self.available = True
            print("[系统] Genie-TTS 已就绪。")
        except Exception as e:
            print(f"[错误] Genie-TTS 初始化失败: {e}")
            self.available = False
    
    def speak(self, text, emotion_hint=None):
        """
        合成并播放语音
        
        Args:
            text (str): 要播报的文本
            emotion_hint (str): 情感提示（例如：热情、平静、关怀、鼓励）
        """
        if not self.available:
            print("[警告] Genie-TTS 不可用，跳过播报。")
            self._fallback_speak(text)
            return
        
        prefix_map = {
            "热情": "【语气：热情】",
            "平静": "【语气：平静】",
            "关怀": "【语气：关怀】",
            "鼓励": "【语气：鼓励】",
            "开心": "【语气：开心】",
            "惊讶": "【语气：惊讶】",
            "悲伤": "【语气：悲伤】",
            "生气": "【语气：生气】",
        }
        mapped_emotion = emotion_hint
        for key in prefix_map:
            if key in str(emotion_hint):
                mapped_emotion = key
                break
        
        target_ref_file = None
        target_ref_text = None

        if mapped_emotion and self.reference_audio_path:
            ref_dir = os.path.dirname(self.reference_audio_path)
            emotion_file_suffix = {
                "开心": "happy",
                "热情": "enthusiastic",
                "悲伤": "sad",
                "生气": "angry",
                "惊讶": "surprised",
                "平静": "calm",
                "关怀": "caring",
                "鼓励": "encouraging"
            }
            
            suffix = emotion_file_suffix.get(mapped_emotion, "default")
            possible_file = os.path.join(ref_dir, f"ref_{suffix}.wav")
            if os.path.exists(possible_file):
                target_ref_file = possible_file
                if os.path.basename(possible_file) in self.prompt_map:
                    target_ref_text = self.prompt_map[os.path.basename(possible_file)].get("text", "")
            
        if not target_ref_file and self.voice_samples:
            if random.random() < 0.8:
                sample = random.choice(self.voice_samples)
                target_ref_file = sample["path"]
                target_ref_text = sample["text"]

        if target_ref_file:
            current_ref = getattr(self, "_current_ref_path", self.reference_audio_path)
            
            if target_ref_file != current_ref:
                audio_text = target_ref_text or self.reference_audio_text
                if not target_ref_text:
                     print(f"[警告] 参考音频 {os.path.basename(target_ref_file)} 缺少文本，效果可能不佳。")

                try:
                    print(f"[Genie-TTS] 切换参考音频至: {os.path.basename(target_ref_file)}")
                    genie.set_reference_audio(
                        character_name=self.character_name,
                        audio_path=target_ref_file,
                        audio_text=audio_text
                    )
                    self._current_ref_path = target_ref_file
                except Exception as e:
                    print(f"[警告] 切换参考音频失败: {e}")
                    self._current_ref_path = None

        if mapped_emotion in prefix_map:
            text = f"{prefix_map[mapped_emotion]} {text}"
        
        filename = os.path.join(self.output_dir, f"genie_{int(time.time())}.wav")
        try:
            genie.tts(
                character_name=self.character_name,
                text=text,
                play=True,
                save_path=filename
            )
            return filename
        except ValueError as e:
            if "ge" in str(e) and "missing from input feed" in str(e):
                print(f"[严重错误] Genie-TTS 模型版本不兼容 (缺少 ge/ge_advanced 输入)。")
                print("这通常是因为使用了不兼容的 ONNX 模型。")
                print(">>> 正在自动降级到系统 TTS ...")
                self.available = False
                self._fallback_speak(text)
                return None
            else:
                print(f"[错误] Genie-TTS 推理异常: {e}")
                self._fallback_speak(text)
                return None
        except Exception as e:
            print(f"[错误] Genie-TTS 合成失败: {e}")
            self._fallback_speak(text)
            return None

    def _fallback_speak(self, text):
        """降级到系统 TTS（跨平台兜底：macOS say / Linux espeak / 其它 pyttsx3）"""
        if IS_MACOS:
            try:
                import subprocess
                subprocess.run(['say', '-v', 'Tingting', text],
                             capture_output=True, text=True)
                return
            except Exception as e:
                print(f"[错误] macOS say 命令失败: {e}")
        elif IS_LINUX:
            try:
                import subprocess
                subprocess.run(['espeak', text],
                             capture_output=True, text=True)
                return
            except Exception as e:
                print(f"[错误] Linux espeak 命令失败: {e}")

        try:
            import pyttsx3
            engine = pyttsx3.init()
            engine.say(text)
            engine.runAndWait()
        except Exception as e:
            print(f"[错误] 系统 TTS 也失败了: {e}")