import cv2
import time
import re
import platform

PLATFORM = platform.system()
IS_WINDOWS = PLATFORM == 'Windows'
IS_MACOS = PLATFORM == 'Darwin'

class VisionDetector:
    """
    视觉检测器类
    支持 YOLOv8 和 LocateAnything-3B 双模型，提供灵活的视觉感知能力。
    
    架构说明：
    - YOLOv8: 快速自动检测，用于实时场景概览
    - LocateAnything-3B: 基于自然语言查询的精准定位，用于特定对象搜索
    
    混合模式：
    - 默认模式: 使用 YOLOv8 进行实时检测
    - 查询模式: 使用 LocateAnything-3B 根据用户查询定位特定对象
    
    平台优化：
    - Apple Silicon: 使用 MLX 框架加速 LocateAnything-3B
    - CUDA: 使用 PyTorch 加速
    - CPU: 降级使用 PyTorch float32
    """
    
    DEFAULT_QUERIES = [
        "Locate all people.",
        "Locate all animals.",
        "Locate all vehicles.",
        "Locate all objects.",
    ]
    
    def __init__(self, mode='hybrid', yolo_model_path='yolov8n.pt', la_model_path=None):
        """
        初始化检测器
        
        Args:
            mode (str): 检测模式，可选 'yolo', 'locate_anything', 'hybrid'
            yolo_model_path (str): YOLOv8 模型路径
            la_model_path (str): LocateAnything-3B 模型路径，默认为 None（自动下载）
        """
        self.mode = mode
        self.yolo_model = None
        self.la_model = None
        self.la_processor = None
        self.la_tokenizer = None
        self.la_available = False
        self.yolo_available = False
        self.la_device = "cpu"
        self.la_framework = None
        self.la_model_path = la_model_path
        
        print(f"[系统] 视觉检测器初始化，模式: {mode}")
        
        if mode in ['yolo', 'hybrid']:
            self._init_yolo(yolo_model_path)
        
        if mode in ['locate_anything', 'hybrid']:
            self._init_locate_anything_lazy()
    
    def _init_yolo(self, model_path):
        """初始化 YOLOv8 模型"""
        try:
            from ultralytics import YOLO
            print(f"[系统] 正在加载 YOLOv8 模型: {model_path} ...")
            self.yolo_model = YOLO(model_path)
            self.yolo_available = True
            print("[系统] YOLOv8 模型加载成功！")
        except Exception as e:
            print(f"[警告] YOLOv8 模型加载失败: {e}")
            self.yolo_available = False
    
    def _init_locate_anything_lazy(self):
        """懒加载初始化 - 只检查环境，不加载模型"""
        print("[系统] LocateAnything-3B 已注册，将在首次使用时加载...")
        self.la_available = True
    
    def _load_locate_anything(self):
        """实际加载 LocateAnything-3B 模型"""
        if self.la_model is not None:
            return True
        
        try:
            import subprocess
            result = subprocess.run(['sysctl', '-n', 'machdep.cpu.brand_string'], 
                                   capture_output=True, text=True)
            if 'Apple' in result.stdout:
                return self._init_locate_anything_mlx()
            else:
                return self._init_locate_anything_pytorch()
        except:
            return self._init_locate_anything_pytorch()
    
    def _init_locate_anything_mlx(self):
        """使用 MLX 框架初始化 LocateAnything-3B (Apple Silicon 优化)"""
        try:
            from mlx_vlm import load, generate
            from PIL import Image
            
            model_name = self.la_model_path or "eadx/LocateAnything-3B-MLX"
            print(f"[系统] 正在加载 LocateAnything-3B-MLX (Apple Silicon 优化): {model_name} ...")
            
            self.la_model, self.la_processor = load(model_name, trust_remote_code=True)
            self.la_framework = "mlx"
            self.la_available = True
            print("[系统] LocateAnything-3B-MLX 模型加载成功！(MLX 框架)")
            return True
        except Exception as e:
            print(f"[警告] MLX 加载失败，尝试使用 PyTorch: {e}")
            return self._init_locate_anything_pytorch()
    
    def _init_locate_anything_pytorch(self):
        """使用 PyTorch 初始化 LocateAnything-3B"""
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer, AutoProcessor
            
            model_name = self.la_model_path or "nvidia/LocateAnything-3B"
            print(f"[系统] 正在加载 LocateAnything-3B: {model_name} ...")
            
            self.la_tokenizer = AutoTokenizer.from_pretrained(
                model_name,
                trust_remote_code=True
            )
            self.la_processor = AutoProcessor.from_pretrained(
                model_name,
                trust_remote_code=True
            )
            
            device = "cuda" if torch.cuda.is_available() else "cpu"
            self.la_model = AutoModel.from_pretrained(
                model_name,
                torch_dtype=torch.bfloat16 if device == "cuda" else torch.float32,
                trust_remote_code=True,
                low_cpu_mem_usage=True
            ).to(device).eval()
            
            self.la_device = device
            self.la_framework = "pytorch"
            self.la_available = True
            print(f"[系统] LocateAnything-3B 模型加载成功！设备: {device}")
            return True
        except Exception as e:
            print(f"[警告] LocateAnything-3B 模型加载失败: {e}")
            print("[提示] 如果网络问题无法下载，请手动下载模型到本地")
            self.la_available = False
            return False
    
    def detect(self, frame, query=None):
        """
        对单帧图像进行检测
        
        Args:
            frame (numpy.ndarray): 输入图像
            query (str): 可选的自然语言查询（仅 LocateAnything 模式有效）
            
        Returns:
            annotated_frame (numpy.ndarray): 画好框的图像
            results (list): 检测结果详情，格式为 [{"label": str, "confidence": float, "bbox": tuple}]
        """
        if self.mode == 'yolo' or (self.mode == 'hybrid' and not query):
            return self._detect_yolo(frame)
        elif self.mode == 'locate_anything' or (self.mode == 'hybrid' and query):
            if self.la_model is None:
                print(f"[系统] 首次使用 LocateAnything，正在加载模型...")
                self._load_locate_anything()
            return self._detect_locate_anything(frame, query)
        else:
            return frame, []
    
    def _detect_yolo(self, frame):
        """使用 YOLOv8 进行检测"""
        if not self.yolo_available:
            return frame, []
        
        results = self.yolo_model(frame, conf=0.5, verbose=False)
        annotated_frame = results[0].plot()
        
        detected = []
        r = results[0]
        for box in r.boxes:
            class_id = int(box.cls[0])
            class_name = r.names[class_id]
            conf = float(box.conf[0])
            bbox = box.xyxy[0].tolist()
            detected.append({
                "label": class_name,
                "confidence": conf,
                "bbox": tuple(bbox)
            })
        
        return annotated_frame, detected
    
    def _detect_locate_anything(self, frame, query):
        """使用 LocateAnything-3B 进行定位"""
        if not self.la_available or self.la_model is None:
            print("[警告] LocateAnything-3B 不可用，降级到 YOLOv8")
            return self._detect_yolo(frame)
        
        if not query:
            query = "Locate all objects."
        
        try:
            from PIL import Image
            
            img_pil = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            
            if self.la_framework == "mlx":
                return self._detect_locate_anything_mlx(img_pil, frame, query)
            else:
                return self._detect_locate_anything_pytorch(img_pil, frame, query)
            
        except Exception as e:
            print(f"[错误] LocateAnything-3B 检测失败: {e}")
            return self._detect_yolo(frame)
    
    def _detect_locate_anything_mlx(self, img_pil, frame, query):
        """使用 MLX 框架进行 LocateAnything 推理"""
        try:
            from mlx_vlm import generate
            
            messages = [{
                "role": "user",
                "content": [
                    {"type": "image"},
                    {"type": "text", "text": query}
                ]
            }]
            
            text_prompt = self.la_processor.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
            
            response = generate(
                self.la_model, 
                self.la_processor, 
                text_prompt, 
                image=img_pil, 
                max_tokens=1000
            )
            
            detected = self._parse_la_output(response.text, frame.shape)
            annotated_frame = self._draw_bboxes(frame, detected)
            
            return annotated_frame, detected
            
        except Exception as e:
            print(f"[错误] MLX 推理失败: {e}")
            return self._detect_yolo(frame)
    
    def _detect_locate_anything_pytorch(self, img_pil, frame, query):
        """使用 PyTorch 框架进行 LocateAnything 推理"""
        try:
            import torch
            
            messages = [{
                "role": "user",
                "content": [
                    {"type": "image", "image": img_pil},
                    {"type": "text", "text": query}
                ]
            }]
            
            text = self.la_processor.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
            
            images, _ = self.la_processor.process_vision_info(messages)
            inputs = self.la_processor(
                text=[text],
                images=images,
                return_tensors="pt"
            ).to(self.la_device)
            
            with torch.no_grad():
                outputs = self.la_model.generate(
                    **inputs,
                    max_new_tokens=2048,
                    temperature=0.7,
                    top_p=0.9
                )
            
            response_text = self.la_tokenizer.decode(
                outputs[0], skip_special_tokens=True
            )
            
            detected = self._parse_la_output(response_text, frame.shape)
            annotated_frame = self._draw_bboxes(frame, detected)
            
            return annotated_frame, detected
            
        except Exception as e:
            print(f"[错误] PyTorch 推理失败: {e}")
            return self._detect_yolo(frame)
    
    def _parse_la_output(self, output_text, image_shape):
        """
        解析 LocateAnything-3B 的输出
        
        输出格式示例:
        <ref>Locate the person</ref><box><247><220><757><1000></box>
        
        坐标范围: 0-1000 (归一化)
        """
        pattern = r"<ref>(.*?)</ref><box><(\d+)><(\d+)><(\d+)><(\d+)></box>"
        matches = re.findall(pattern, output_text)
        
        detected = []
        height, width = image_shape[:2]
        
        for match in matches:
            label = match[0].strip()
            ymin, xmin, ymax, xmax = [int(m) for m in match[1:]]
            
            x1 = int(xmin * width / 1000)
            y1 = int(ymin * height / 1000)
            x2 = int(xmax * width / 1000)
            y2 = int(ymax * height / 1000)
            
            detected.append({
                "label": label,
                "confidence": 0.95,
                "bbox": (x1, y1, x2, y2)
            })
        
        return detected
    
    def _draw_bboxes(self, frame, detections):
        """在图像上绘制边界框"""
        annotated_frame = frame.copy()
        
        for det in detections:
            label = det["label"]
            confidence = det["confidence"]
            x1, y1, x2, y2 = det["bbox"]
            
            cv2.rectangle(annotated_frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(annotated_frame, f"{label} ({confidence:.2f})",
                       (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
        
        return annotated_frame
    
    def set_mode(self, mode):
        """切换检测模式"""
        if mode in ['yolo', 'locate_anything', 'hybrid']:
            self.mode = mode
            print(f"[系统] 检测模式已切换为: {mode}")
            return True
        else:
            print(f"[错误] 无效模式: {mode}")
            return False
    
    def is_la_available(self):
        """检查 LocateAnything 是否可用"""
        return self.la_available and self.la_model is not None

if __name__ == "__main__":
    print("请运行 main.py 进行完整测试")