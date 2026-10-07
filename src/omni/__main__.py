"""python -m src.omni 与主入口统一使用固定 Gateway。"""
from .gateway_cli import main

if __name__ == "__main__":
    raise SystemExit(main())
