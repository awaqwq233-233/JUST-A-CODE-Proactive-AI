"""J.A.C. 统一 Gateway 入口；旧 MiniCPM 与传统轮询入口已退役。"""
import sys


def main(argv=None):
    """默认运行方案 B，保留 --gateway 参数以兼容既有启动命令。"""
    from src.omni.gateway_cli import main as gateway_main
    args = list(sys.argv[1:] if argv is None else argv)
    return gateway_main([arg for arg in args if arg != "--gateway"])


if __name__ == "__main__":
    raise SystemExit(main())
