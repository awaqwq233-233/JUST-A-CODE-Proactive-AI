import cv2
import time
import platform

class VisionDetector:
    """
    视觉检测器类 (YOLOv8-only)
    
    使用 YOLOv8 进行实时对象检测。
    视觉理解请求已移除 NVIDIA LocateAnything-3B，
    现在由 JACbrain (Qwen3.5-9B) 通过文本方式处理。
    """
    
    def __init__(self, yolo_model_path='yolov8n.pt'):
        """
        初始化检测器
        
        Args:
            yolo_model_path (str): YOLOv8 模型路径
        """
        self.yolo_model = None
        self.yolo_available = False
        
        print(f"[系统] 视觉检测器初始化 (YOLOv8)")
        self._init_yolo(yolo_model_path)
    
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
    
    def detect(self, frame):
        """
        对单帧图像进行检测
        
        Args:
            frame (numpy.ndarray): 输入图像
            
        Returns:
            annotated_frame (numpy.ndarray): 画好框的图像
            results (list): 检测结果详情，格式为[{"label": str, "confidence": float, "bbox": tuple}]
        """
        return self._detect_yolo(frame)
    
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

if __name__ == "__main__":
    print("请运行 main.py 进行完整测试")
