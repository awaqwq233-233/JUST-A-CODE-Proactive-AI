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
        """
        detected = []
        if results and len(results) > 0:
            # 解析 YOLO 结果
            # result.boxes.cls 包含类别ID
            # result.names 包含类别名称映射
            r = results[0]
            for box in r.boxes:
                class_id = int(box.cls[0])
                class_name = r.names[class_id]
                conf = float(box.conf[0])
                detected.append(f"{class_name} ({conf:.2f})")
        
        with self._lock:
            self.current_objects = detected
            self.last_seen_time = time.time()

    def get_vision_summary(self):
        """
        获取当前视觉摘要 (由 Brain 调用)
        """
        with self._lock:
            # 如果数据太旧（比如超过 2 秒没更新），可能摄像头卡了或者没东西
            if time.time() - self.last_seen_time > 2.0:
                return "我眼前暂时一片漆黑（无最新视觉数据）。"
            
            if not self.current_objects:
                return "我没有看到特别的物体。"
            
            # 统计物体数量，例如: 2 person, 1 cell phone
            counts = {}
            for obj in self.current_objects:
                name = obj.split('(')[0].strip()
                counts[name] = counts.get(name, 0) + 1
            
            summary = ", ".join([f"{v}个{k}" for k, v in counts.items()])
            return f"我看到了：{summary}。"
