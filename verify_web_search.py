#!/usr/bin/env python3
"""独立联网检索验收：实际搜索/来源正文 → 精确 Qwen → 中文报告，无设备。"""

import argparse
import json
import re
from pathlib import Path
import signal
import sys
import threading

from src.brain.llm import LocalBrain
from src.brain.lm_studio import BrainError
from src.brain.task_runner import BrainTaskRunner, ROOT, atomic_write
from src.tools.web_search import SearchError, WebSearchClient


def main(argv=None):
    """明确查询关键词；可单独检验公网，也可验收真实 Qwen 文件闭环。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", required=True, help="用户授权的检索关键词")
    parser.add_argument("--url", default="http://127.0.0.1:12345")
    parser.add_argument("--search-only", action="store_true", help="只验证公网搜索/来源，不调用 Qwen")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output/m2/search")
    args = parser.parse_args(argv)
    if sys.version_info[:2] != (3, 11):
        parser.error("请使用项目 .cache/m0/venv 的 Python 3.11")
    stopped = threading.Event()
    previous = signal.getsignal(signal.SIGINT)

    def cancel(signum, frame):
        """Ctrl+C 取消在途公网/模型请求及报告发布。"""
        stopped.set()

    signal.signal(signal.SIGINT, cancel)
    report = dict(passed=False, devices_opened=False, search_only=args.search_only)
    try:
        if args.search_only:
            data = WebSearchClient().search(args.query, stopped.is_set)
            atomic_write(args.output_dir / "search-evidence.json", json.dumps(data, ensure_ascii=False, indent=2), stopped.is_set)
        else:
            brain = LocalBrain(backend="lm_studio", lm_studio_url=args.url)
            result = BrainTaskRunner(brain, args.output_dir).run(
                "上网搜索" + args.query, stopped.is_set, web_query=args.query)
            data = json.loads(result.trace[0]["output"])
            report.update(file=result.path.name, seconds=round(result.elapsed_seconds, 3), tool_count=len(result.trace))
            report["answer_available"] = bool(re.search(r"\[\d+\]", result.answer))
            print("[报告] " + str(result.path), flush=True)
        report.update(provider=data["provider"], results=len(data["results"]),
                      pages_read=sum(item["page"]["status"] == "read" for item in data["results"]))
        if not report["pages_read"]:
            raise SearchError("只获得搜索摘要，未验证任何来源正文")
        if not args.search_only and not report["answer_available"]:
            raise SearchError("搜索已完成，但未取得相关来源摘录；不作为问题回答验收通过")
        report["passed"] = True
        print(f"[通过] {report['results']} 条实际来源，{report['pages_read']} 页正文已读取", flush=True)
        return 0
    except (BrainError, SearchError, ValueError) as error:
        report["error"] = str(error)
        print("[未通过] " + str(error), file=sys.stderr, flush=True)
        return 2
    finally:
        signal.signal(signal.SIGINT, previous)
        atomic_write(args.output_dir / "verification.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
