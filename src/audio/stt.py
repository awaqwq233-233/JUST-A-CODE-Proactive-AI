import whisper
import os
import torch
import warnings

# 忽略 Whisper 可能产生的一些非关键警告
warnings.filterwarnings("ignore")

class SpeechRecognizer:
    """
    语音识别类 (STT)
    使用 OpenAI Whisper 模型将音频转换为文本。
    """
    def __init__(self, model_size="base"):
        """
        初始化识别器
        
        Args:
            model_size (str): 模型大小，可选 'tiny', 'base', 'small', 'medium', 'large'
                              对于笔记本，推荐 'base' 或 'small'。
        """
        print(f"[系统] 正在加载 Whisper 模型 ({model_size})... 这可能需要几分钟...")
        try:
            # 检查是否有 GPU 加速
            device = "cuda" if torch.cuda.is_available() else "cpu"
            print(f"[系统] 运行设备: {device}")
            
            # 加载模型
            self.model = whisper.load_model(model_size, device=device)
            print("[系统] Whisper 模型加载成功！")
        except Exception as e:
            print(f"[错误] Whisper 模型加载失败: {e}")
            self.model = None

    def transcribe(self, audio_data):
        """
        识别音频数据
        
        Args:
            audio_data: 这里的输入取决于具体的录音实现。
                        Whisper 通常接受文件路径或 numpy 数组。
                        为了兼容性，我们这里假设传入的是一个音频文件路径。
        
        Returns:
            text (str): 识别出的文本
        """
        if self.model is None:
            return ""

        try:
            # transcribe 方法可以直接处理文件路径
            # fp16=False 是为了兼容 CPU (CPU 不支持半精度浮点数)
            result = self.model.transcribe(audio_data, fp16=False)
            text = result['text'].strip()
            return text
        except Exception as e:
            print(f"[错误] 语音识别出错: {e}")
            return ""

if __name__ == "__main__":
    # 测试代码
    # 需要有一个 test.wav 文件才能运行
    print("请配合 recorder.py 进行测试")
