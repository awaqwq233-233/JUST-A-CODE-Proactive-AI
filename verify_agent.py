#!/usr/bin/env python3
"""独立 JACBrain 验证：指定两份小文本，真实 Qwen/Harness 多轮工具及证据报告。"""
import argparse
import json
from pathlib import Path
import signal
import threading

from src.brain.agent.profile import ROOT
from src.brain.agent.runner import inspect_session, run_comparison


def main(argv=None):
    """解析显式工作目录和文件名；Ctrl+C 撤销任务，恢复模式只读历史。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--before")
    parser.add_argument("--after")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output/agent")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--inspect-session", help="只读恢复指定任务历史，不执行未完成动作")
    args = parser.parse_args(argv)
    if args.inspect_session:
        try:
            print(json.dumps(inspect_session(args.inspect_session), ensure_ascii=False, indent=2))
            return 0
        except Exception as error:
            print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
            return 2
    if not args.workspace or not args.before or not args.after:
        parser.error("必须指定 --workspace、--before 和 --after")
    stopped = threading.Event()

    def cancel(signum, frame):
        """将终端中断转成协作取消，由控制器清理其拥有的子进程。"""
        stopped.set()

    previous = signal.signal(signal.SIGINT, cancel)
    try:
        result = run_comparison(args.workspace, args.before, args.after, output=args.output_dir,
                                timeout=args.timeout, should_stop=stopped.is_set)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "completed" else 2
    except Exception as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
        return 2
    finally:
        signal.signal(signal.SIGINT, previous)


if __name__ == "__main__":
    raise SystemExit(main())
