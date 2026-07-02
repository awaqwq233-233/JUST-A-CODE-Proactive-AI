import threading
import time

class SharedContext:
    """
    共享上下文管理器
    用于在不同线程（视觉、音频、大脑）之间共享信息。
    """
    def __init__(self):
        self._lock = threading.Lock()
        
        # 视觉记忆
        self.current_objects = []     # 当前看到的物体列表
        self.last_seen_time = 0       # 最后更新时间
        
        # 状态标志
        self.is_listening = False     # 是否正在听
        self.is_thinking = False      # 是否正在思考
        self.is_speaking = False      # 是否正在说话

    def update_vision(self, results):
        """
        更新视觉信息 (由 Detector 线程调用)
        
        Args:
            results (list): 检测结果，格式为 [{"label": str, "confidence": float, "bbox": tuple}]
        """
        detected = []
        
        if results and isinstance(results, list):
            for item in results:
                if isinstance(item, dict) and "label" in item and "confidence" in item:
                    label = item["label"]
                    confidence = item["confidence"]
                    detected.append(f"{label} ({confidence:.2f})")
        
        with self._lock:
            self.current_objects = detected
            self.last_seen_time = time.time()

    def get_vision_summary(self):
        """
        获取当前视觉摘要 (由 Brain 调用)
        """
        with self._lock:
            if time.time() - self.last_seen_time > 2.0:
                return "我眼前暂时一片漆黑（无最新视觉数据）。"
            
            if not self.current_objects:
                return "我没有看到特别的物体。"
            
            counts = {}
            for obj in self.current_objects:
                name = obj.split('(')[0].strip()
                counts[name] = counts.get(name, 0) + 1
            
            summary = ", ".join([f"{v}个{k}" for k, v in counts.items()])
            return f"我看到了：{summary}。"