#!/usr/bin/env python3
"""M2 独立实机验收：Qwen 结构化调用 → 真实只读工具 → 中文文件。"""

import argparse
import json
from pathlib import Path
import re
import signal
import sys
import threading

from src.brain.llm import LocalBrain
from src.brain.lm_studio import BrainError, MODEL
from src.brain.task_runner import BrainTaskRunner, atomic_write, ROOT


def check_grounding(name, result):
    """验收答案保留真实时间、电量及完整报告的 CPU/内存数值。"""
    output = "\n".join(entry["output"] for entry in result.trace)
    values = []
    if name in {"time", "all"}:
        stamp = re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", output)
        if not stamp:
            return False
        values.append(stamp.group())
    if name in {"battery", "all"}:
        battery = re.search(r"\d+%", output)
        if battery:
            values.append(battery.group())
        elif "无法" not in result.answer:
            return False
    if name == "all":
        cpu = re.search(r"CPU 核心数：(\d+)", output)
        load = re.search(r"负载：([\d./]+)", output)
        memory = re.search(r"合计约 ([\d.]+) GiB / 共 ([\d.]+) GiB", output)
        if cpu:
            values.append(cpu.group(1))
        if load:
            values.extend(load.group(1).split("/"))
        if memory:
            values.extend(memory.groups())
            values.append("GiB")
        elif "无法" not in result.answer:
            return False
    return all(re.search(r"(?<![\d.])" + re.escape(value) + r"(?![\d.])", result.answer)
               for value in values)


def main(argv=None):
    """默认验收时间/电池/状态报告；可传显式文本任务，只开放系统查询。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:12345', help='LM Studio 本机根地址')
    parser.add_argument('--model', default=MODEL, help='精确已加载模型实例 ID')
    parser.add_argument('--task', help='可选显式任务文字；仍仅允许查询系统状态')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'output/m2/qwen')
    args = parser.parse_args(argv)
    if sys.version_info[:2] != (3, 11):
        parser.error('请使用项目 .cache/m0/venv 的 Python 3.11')
    stopped = threading.Event()
    previous = signal.getsignal(signal.SIGINT)

    def cancel(signum, frame):
        """Ctrl+C 协作取消在途请求，不发布未完成任务文件。"""
        stopped.set()

    signal.signal(signal.SIGINT, cancel)
    report = {'model': args.model, 'url': args.url, 'passed': False, 'cases': []}
    try:
        brain = LocalBrain(backend='lm_studio', lm_studio_model=args.model, lm_studio_url=args.url)
        runner = BrainTaskRunner(brain, args.output_dir)
        cases = [('task', args.task, None)] if args.task else [
            ('time', '查询本机当前时间，回答必须原样保留工具返回的完整日期和时间。', ['time']),
            ('battery', '查询本机实际电池状态，回答保留真实百分比与充放电状态；无法读取时如实说明。', ['battery']),
            ('all', '查询本机时间、电池、CPU、内存，并生成中文系统状态报告，保留真实数值与测量限制。', ['all']),
        ]
        for name, task, info_types in cases:
            result = runner.run(task, stopped.is_set, info_types)
            grounded = None if name == 'task' else check_grounding(name, result)
            report['cases'].append(dict(name=name, seconds=round(result.elapsed_seconds, 3),
                                        tool_count=len(result.trace), grounded=grounded,
                                        reasoning_tokens=(brain.lm_client.last_usage.get('completion_tokens_details') or {}).get('reasoning_tokens', 0),
                                        file=result.path.name))
            print(f'[{name}] {len(result.trace)} 次真实工具调用，{result.elapsed_seconds:.2f} 秒；{result.path}')
            if grounded is False:
                raise BrainError('最终回答未保留要求核对的实际工具数值，验收失败')
        if stopped.is_set():
            raise BrainError('任务已取消')
        report['passed'] = True
        return 0
    except (BrainError, ValueError) as exc:
        report['error'] = str(exc)
        print(f'[未通过] {exc}', file=sys.stderr)
        return 2
    finally:
        signal.signal(signal.SIGINT, previous)
        atomic_write(args.output_dir / 'verification.json', json.dumps(report, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
