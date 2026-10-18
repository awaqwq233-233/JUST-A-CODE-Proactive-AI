#!/usr/bin/env python3
"""M2c 独立验收：真实 Qwen → 单个明确本地任务 → 实际证据文件，不打开采集设备。"""

import argparse
import json
from pathlib import Path
import signal
import threading

from src.brain.llm import LocalBrain
from src.brain.task_runner import BrainTaskRunner, ROOT, atomic_write
from src.tools.local_intents import local_instruction


def main(argv=None):
    """仅执行命令行明确提供的动作；打开类任务会实际启动应用或浏览器。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True, help="完整单句，例如：打开计算器；会实际执行")
    parser.add_argument("--url", default="http://127.0.0.1:12345")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output/m2/local")
    args = parser.parse_args(argv)
    action = local_instruction(args.task)
    if not action or action[0] != "local":
        parser.error("请指定支持的完整本地任务；搜索须明确目录和文件名关键词")
    stopped = threading.Event()
    previous = signal.getsignal(signal.SIGINT)

    def cancel(signum, frame):
        """Ctrl+C 取消后续动作和文件发布，不撤回已提交给系统的启动请求。"""
        stopped.set()

    signal.signal(signal.SIGINT, cancel)
    report = dict(passed=False, devices_opened=False, tool=action[1])
    try:
        runner = BrainTaskRunner(LocalBrain(backend="lm_studio", lm_studio_url=args.url), args.output_dir)
        result = runner.run(args.task, stopped.is_set, local_action=action)
        report.update(status=result.status, tool_count=len(result.trace), file=result.path.name,
                      seconds=round(result.elapsed_seconds, 3))
        # URL 首版只确认启动器接收请求，不能将其升级为网页加载验收。
        report["passed"] = len(result.trace) == 1 and result.status in {"completed", "submitted"}
        print(result.answer, flush=True)
        print("[记录] " + str(result.path), flush=True)
        return 0 if report["passed"] else 2
    except Exception as error:
        report.update(error_type=type(error).__name__)
        print("[未通过] " + str(error), flush=True)
        return 2
    finally:
        signal.signal(signal.SIGINT, previous)
        atomic_write(args.output_dir / "verification.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
