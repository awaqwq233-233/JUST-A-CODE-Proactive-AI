import cv2
import time
import platform

PLATFORM = platform.system()
IS_WINDOWS = PLATFORM == 'Windows'
IS_MACOS = PLATFORM == 'Darwin'

class Camera:
    """
    摄像头管理类
    负责视频流的采集、帧获取和资源释放。
    兼容 Windows 和 macOS 平台。
    """
    def __init__(self, camera_id=None, width=1280, height=720):
        """
        初始化摄像头
        
        Args:
            camera_id (int, optional): 摄像头设备ID，None 表示自动检测系统默认摄像头
            width (int): 期望的视频宽度
            height (int): 期望的视频高度
        """
        self.camera_id = camera_id
        self.width = width
        self.height = height
        self.cap = None
        self.is_running = False
    
    @staticmethod
    def find_default_camera():
        """
        自动检测系统默认摄像头
        
        Returns:
            int: 找到的可用摄像头 ID，如果未找到返回 0
        """
        # 尝试从 ID 0 开始检测，最多检测 5 个摄像头
        for test_id in range(5):
            cap = cv2.VideoCapture(test_id)
            if cap.isOpened():
                cap.release()
                return test_id
        return 0

    def start(self):
        """
        启动摄像头
        """
        # 自动检测默认摄像头（如果未指定）
        if self.camera_id is None:
            print("[系统] 正在自动检测默认摄像头...")
            self.camera_id = self.find_default_camera()
            print(f"[系统] 使用摄像头 ID: {self.camera_id}")
        
        print(f"[系统] 正在尝试打开摄像头 (ID: {self.camera_id})...")
        
        if IS_MACOS:
            self.cap = cv2.VideoCapture(self.camera_id)
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            self.cap.set(cv2.CAP_PROP_FPS, 30)
        else:
            self.cap = cv2.VideoCapture(self.camera_id)
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        
        if not self.cap.isOpened():
            print("[错误] 无法打开摄像头！请检查设备连接。")
            if IS_MACOS:
                print("[提示] macOS 用户请确保已授权摄像头访问权限")
            self.is_running = False
            return False
        
        print(f"[系统] 摄像头启动成功。分辨率: {self.width}x{self.height}")
        self.is_running = True
        return True

    def get_frame(self):
        """
        读取一帧图像
        
        Returns:
            ret (bool): 读取是否成功
            frame (numpy.ndarray): 图像数据
        """
        if not self.is_running or self.cap is None:
            return False, None
        
        ret, frame = self.cap.read()
        
        if not ret:
            print("[警告] 无法读取视频帧。")
            return False, None
            
        return True, frame

    def stop(self):
        """
        释放摄像头资源
        """
        if self.cap is not None:
            self.cap.release()
            self.is_running = False
            print("[系统] 摄像头已关闭。")

if __name__ == "__main__":
    cam = Camera()
    if cam.start():
        try:
            while True:
                ret, frame = cam.get_frame()
                if ret:
                    cv2.imshow("Test Camera", frame)
                
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break
        finally:
            cam.stop()
            cv2.destroyAllWindows()