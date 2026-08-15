"""llama-omni-server 启动器（本地 MiniCPM-o-4_5 推理服务的进程管理）。

职责：
  1. 定位 llama-omni-server 二进制（环境变量 > 配置 > 常见默认路径）。
  2. 按需启动服务子进程（Metal 加速，加载 Q8_0 量化权重）。
  3. 通过 TCP 端口探测判断服务是否就绪（不触碰 WS 握手，避免留下半开会话）。

注意：服务进程在仓库外（~/Desktop/work/coding/jac_omni_backend/），不进 J.A.C. 仓库。
本机代理可能劫持 localhost，启动/连接均显式声明 NO_PROXY 绕过。
"""
import os
import socket
import subprocess
import time

# llama-omni-server 默认搜索路径（按优先级）：环境变量 > 本机工作区常见位置
_DEFAULT_BIN_CANDIDATES = [
    os.environ.get("LLAMA_OMNI_SERVER_BIN", ""),                 # 显式指定优先
    os.path.expanduser(
        "~/Desktop/work/coding/jac_omni_backend/llama.cpp-omni/build/bin/llama-omni-server"
    ),
    # 其它可能摆放位置（留作扩展）
    "/usr/local/bin/llama-omni-server",
    "/opt/homebrew/bin/llama-omni-server",
]

# 默认权重目录（model_dir 未配置时的兜底；与二进制同款工作区路径）
_DEFAULT_MODEL_DIR = os.path.expanduser(
    "~/Desktop/work/coding/jac_omni_backend/models/MiniCPM-o-4_5-gguf"
)


def _ensure_no_proxy():
    """确保 localhost 不走系统代理（本机代理会劫持 127.0.0.1 导致连接失败）。"""
    for key in ("NO_PROXY", "no_proxy"):
        val = os.environ.get(key, "")
        if "127.0.0.1" not in val:
            os.environ[key] = (val + ",127.0.0.1,localhost").strip(",")


class OmniServerLauncher:
    """llama-omni-server 进程管理器。"""

    def __init__(self, host: str = "127.0.0.1", port: int = 9060,
                 model_dir: str = "", quant: str = "Q8_0",
                 server_bin: str = "", ngl: int = 99, ctx_size: int = 8192):
        """初始化启动器。

        Args:
            host: 服务监听地址。
            port: 服务监听端口。
            model_dir: 含 MiniCPM-o-4_5-<quant>.gguf 及其子模型（vision/audio/tts/...）的目录。
            quant: 量化名（默认 Q8_0；Q4_K_M 在 Metal 上劣化，已排除）。
            server_bin: 二进制绝对路径（留空自动探测）。
            ngl: 卸载到 GPU 的层数（-ngl，Metal 下 99 即全卸载）。
            ctx_size: 上下文窗口（-c）。
        """
        _ensure_no_proxy()
        self.host = host
        self.port = port
        # model_dir 未配置则用默认工作区路径兜底，避免 GUI 勾选开关后服务起不来
        self.model_dir = os.path.expanduser(model_dir) if model_dir else _DEFAULT_MODEL_DIR
        self.quant = quant
        self.server_bin = server_bin
        self.ngl = ngl
        self.ctx_size = ctx_size
        self._proc = None  # subprocess.Popen

    # ----------------------------------------------------------------- 定位
    def find_binary(self) -> str:
        """定位 llama-omni-server 二进制，返回绝对路径；找不到返回空串。"""
        for cand in _DEFAULT_BIN_CANDIDATES:
            if not cand:
                continue
            cand = os.path.expanduser(cand)
            if os.path.isfile(cand) and os.access(cand, os.X_OK):
                return cand
        return ""

    # ----------------------------------------------------------------- 就绪探测
    def is_ready(self, timeout: float = 2.0) -> bool:
        """通过 TCP 端口探测判断服务是否已就绪（连接成功即视为已监听）。

        Args:
            timeout: 单次连接超时（秒）。

        Returns:
            bool: 端口可连接返回 True。
        """
        try:
            with socket.create_connection((self.host, self.port), timeout=timeout):
                return True
        except OSError:
            return False

    def wait_ready(self, timeout: float = 180.0, interval: float = 1.0) -> bool:
        """轮询直到服务就绪或超时（模型加载可能耗时 10~60s）。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.is_ready():
                return True
            time.sleep(interval)
        return False

    # ----------------------------------------------------------------- 启动
    def start(self, wait: bool = True, timeout: float = 180.0) -> bool:
        """启动 llama-omni-server 子进程（若端口已就绪则跳过）。

        Args:
            wait: 是否等待服务加载完成（模型权重较大，建议等待）。
            timeout: 等待就绪的最长秒数。

        Returns:
            bool: 服务最终就绪返回 True。
        """
        _ensure_no_proxy()
        # 把 server 的 stdout/stderr 落盘到 temp/omni_server.log，便于排障
        # （之前丢 DEVNULL，导致自动启动时 server 加载进度/报错全不可见，无法判断是真卡死还是加载中）
        _log_dir = os.path.join(os.getcwd(), "temp")
        os.makedirs(_log_dir, exist_ok=True)
        self._log_path = os.path.join(_log_dir, "omni_server.log")
        # 启动前清空旧日志，避免和上一次混淆
        try:
            with open(self._log_path, "w", encoding="utf-8") as _f:
                _f.write("")
        except Exception:  # noqa: BLE001
            pass
        # 已运行则直接复用，不重复拉起
        if self.is_ready():
            print(f"[omni-server] 端口 {self.port} 已就绪，复用已有服务。")
            return True

        bin_path = self.server_bin or self.find_binary()
        if not bin_path:
            print("[omni-server] 找不到 llama-omni-server 二进制，请用 LLAMA_OMNI_SERVER_BIN "
                  "环境变量或 config.omni_server_bin 指定。")
            return False
        if not self.model_dir:
            print("[omni-server] 未配置模型目录（config.omni_model_dir），无法启动。")
            return False

        # 主模型 = model_dir 下的 MiniCPM-o-4_5-<quant>.gguf；
        # 子模型（vision/audio/tts/projector/CoreML）由服务端从 LLM 目录自动派生。
        model_file = os.path.join(self.model_dir, f"MiniCPM-o-4_5-{self.quant}.gguf")
        if not os.path.isfile(model_file):
            print(f"[omni-server] 主模型文件不存在：{model_file}")
            return False

        cmd = [
            bin_path,
            "--host", self.host,
            "--port", str(self.port),
            "-m", model_file,
            "-ngl", str(self.ngl),
            "-c", str(self.ctx_size),
        ]
        print(f"[omni-server] 启动：{' '.join(cmd)}")
        try:
            # 把 server 输出写入日志文件（而非 DEVNULL），排障可见；
            # 句柄存到 self._logf，stop() 时再关闭，避免进程运行时文件被提前回收。
            self._logf = open(self._log_path, "a", encoding="utf-8")
            # 先记录本次启动命令，方便核对参数（量化/层数/上下文是否和手动一致）
            self._logf.write(f"[launcher] 启动命令: {' '.join(cmd)}\n")
            self._logf.flush()
            self._proc = subprocess.Popen(
                cmd,
                stdout=self._logf,
                stderr=self._logf,
                env=os.environ.copy(),
            )
        except Exception as e:  # noqa: BLE001
            print(f"[omni-server] 启动失败: {e}")
            if getattr(self, "_logf", None) is not None:
                try:
                    self._logf.close()
                except Exception:  # noqa: BLE001
                    pass
                self._logf = None
            return False

        if wait:
            ok = self.wait_ready(timeout=timeout)
            if not ok:
                print(f"[omni-server] 等待就绪超时（{timeout}s），请检查二进制/权重/端口。")
                return False
            print(f"[omni-server] 已就绪（{self.host}:{self.port}）。")
            print(f"[omni-server] 运行日志: {self._log_path}（若迟迟不出现🎧，请查此文件尾部）")
        return True

    # ----------------------------------------------------------------- 停止
    def stop(self):
        """停止自拉起的服务子进程（若服务是外部已有的则不终止）。"""
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=10)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None
            print("[omni-server] 已停止自拉起的服务。")
        # 关闭日志文件句柄（若有）
        if getattr(self, "_logf", None) is not None:
            try:
                self._logf.close()
            except Exception:  # noqa: BLE001
                pass
            self._logf = None
