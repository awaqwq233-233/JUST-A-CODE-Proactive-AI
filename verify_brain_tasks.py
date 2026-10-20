#!/usr/bin/env python3
"""通过生产 JACBrain 接口验证显式文字任务；本地动作会真实执行，不开启音视频设备。"""
import argparse
import json
from pathlib import Path
import signal
import threading

from src.brain.agent.production import JACBrainTaskRunner, check_installation
from src.omni.task_pipeline import route_instruction


def main(argv=None):
    """只接受已支持完整指令，生成真实报告；Ctrl+C 撤销后续动作与发布。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True)
    parser.add_argument("--url", default="http://127.0.0.1:12345")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    fields = route_instruction(args.task)
    if not fields or fields[0] in {"weather_city", "local_input", "website_choice", "website_cancel"}:
        parser.error("请提供支持的完整任务；候选选择须在原语音/GUI 会话内完成")
    stopped = threading.Event()
    def cancel(signum, frame):
        """把终端中断转成协作取消，不终止外部模型或已有应用。"""
        stopped.set()
    previous = signal.signal(signal.SIGINT, cancel)
    try:
        check_installation()
        job = JACBrainTaskRunner(args.url, output_dir=args.output_dir)
        if fields[0] == "local":
            scope = {"local_action": fields}
        elif fields[0] in {"website", "website_forget"}:
            scope = {"website_request": fields}
        else:
            scope = {"web_query": fields[1]} if fields[0] == "web" else {"info_types": fields}
        result = job.run(args.task, should_stop=stopped.is_set, **scope)
        print(json.dumps({"task_id": result.task_id, "status": result.status, "report": str(result.path),
                          "seconds": result.elapsed_seconds, "tool_results": len(result.trace)}, ensure_ascii=False, indent=2))
        return 0 if result.status in {"completed", "submitted", "needs_input", "no_answer", "partial"} else 2
    except Exception as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
        return 2
    finally:
        signal.signal(signal.SIGINT, previous)


if __name__ == "__main__":
    raise SystemExit(main())
