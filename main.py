"""J.A.C. 统一 Gateway 入口；旧 MiniCPM 与传统轮询入口已退役。"""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def ensure_runtime():
    """直接运行时优先复用已安装的项目 3.11 环境，在导入第三方包前切换。"""
    environment = ROOT / ".cache/m0/venv"
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if Path(sys.prefix).resolve() == environment.resolve() and sys.version_info[:2] == (3, 11):
        return
    if python.is_file():
        try:
            probe = subprocess.run(
                [str(python), "-c", "import sys; print(sys.version_info[:2]); print(sys.prefix)"],
                check=True, capture_output=True, text=True, encoding="utf-8", timeout=10,
                env=dict(os.environ, PYTHONIOENCODING="utf-8"),
            )
            lines = probe.stdout.strip().splitlines()
            if len(lines) != 2 or lines[0] != "(3, 11)" or Path(lines[1]).resolve() != environment.resolve():
                raise ValueError("项目虚拟环境不是有效的 Python 3.11 环境")
            print("[启动] 切换到项目 Python 3.11 环境", flush=True)
            os.execv(str(python), [str(python), str(ROOT / "main.py"), *sys.argv[1:]])
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            raise RuntimeError(f"项目 Python 环境不可用：{error}；请按 READMEfirst.md 重新检查 Gateway 安装") from error
    elif sys.version_info[:2] != (3, 11):
        raise RuntimeError("未找到项目 Python 3.11 环境；请按 new_computer_download/READMEfirst.md 安装 Gateway")


def main(argv=None):
    """无参数直接打开 GUI；显式终端/文件参数与旧 --gateway 参数保持兼容。"""
    from src.omni.gateway_cli import main as gateway_main
    args = list(sys.argv[1:] if argv is None else argv)
    args = [arg for arg in args if arg != "--gateway"]
    return gateway_main(args or ["--gui"])


if __name__ == "__main__":
    try:
        ensure_runtime()
    except RuntimeError as error:
        print(f"[启动失败] {error}", file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(main())
