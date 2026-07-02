from ultralytics import YOLO
import cv2
import time

class ObjectDetector:
    """
    目标检测类
    使用 YOLOv8 模型识别图像中的物体。
    """
    def __init__(self, model_path='yolov8n.pt'):
        """
        初始化检测器
        
        Args:
            model_path (str): 模型文件路径，首次运行会自动下载 yolov8n.pt
        """
        print(f"[系统] 正在加载 YOLO 模型: {model_path} ...")
        try:
            # 加载预训练模型
            # 'n' 代表 nano，是速度最快、体积最小的版本
            self.model = YOLO(model_path)
            print("[系统] 模型加载成功！")
        except Exception as e:
            print(f"[错误] 模型加载失败: {e}")
            self.model = None

    def detect(self, frame):
        """
        对单帧图像进行检测
        
        Args:
            frame (numpy.ndarray): 输入图像
            
        Returns:
            annotated_frame (numpy.ndarray): 画好框的图像
            results (list): 检测结果详情
        """
        if self.model is None:
            return frame, []

        # conf=0.5 表示只显示置信度大于 50% 的结果
        # verbose=False 让控制台输出更清爽
        results = self.model(frame, conf=0.5, verbose=False)
        
        # results[0].plot() 会自动在图上画出检测框和标签
        annotated_frame = results[0].plot()
        
        return annotated_frame, results

if __name__ == "__main__":
    # 测试代码
    # 注意：这里需要确保有一张名为 test.jpg 的图片，或者你可以直接运行 main.py
    print("请运行 main.py 进行完整测试")
