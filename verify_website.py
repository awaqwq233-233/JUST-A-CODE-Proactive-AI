#!/usr/bin/env python3
"""通用站名探针：默认只解析；--open 显式打开一次，不开启采集或播放设备。"""

import argparse
import json
from pathlib import Path
import signal
import threading
import time

from src.brain.llm import LocalBrain
from src.brain.task_runner import BrainTaskRunner, atomic_write, ROOT
from src.brain.website_tasks import select_candidate
from src.tools.local_intents import local_instruction
from src.tools.website_resolver import WebsiteSearch, WebsiteCache


def main(argv=None):
    """保存实际候选、选择、耗时和结果，未打开时不会宣称执行成功。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name', required=True, help='要查找的网站名称')
    parser.add_argument('--open', action='store_true', help='实际打开解析结果；歧义只展示候选')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'output/m2/websites')
    args = parser.parse_args(argv)
    task, stopped, started = '打开' + args.name + '网站', threading.Event(), time.monotonic()
    fields = local_instruction(task)
    if not fields or fields[0] not in {'website', 'local'}:
        parser.error('请指定单个网站名称')
    previous = signal.getsignal(signal.SIGINT)

    def cancel(signum, frame):
        """取消后续网络、模型和打开操作，保留设备不曾打开的事实。"""
        stopped.set()

    signal.signal(signal.SIGINT, cancel)
    report = dict(name=args.name, devices_opened=False, open_requested=args.open, passed=False)
    try:
        brain, search = LocalBrain(backend='lm_studio'), WebsiteSearch()
        if args.open:
            job = BrainTaskRunner(brain, args.output_dir, website_search=search,
                                  website_cache=WebsiteCache(args.output_dir / 'cache.json'))
            kwargs = {'website_request': fields} if fields[0] == 'website' else {'local_action': fields}
            result = job.run(task, stopped.is_set, **kwargs)
            report.update(status=result.status, file=result.path.name, trace=result.trace,
                          passed=result.status == 'submitted', choices=result.website)
            print(result.answer, flush=True)
        else:
            data = search.search(args.name, stopped.is_set)
            decision = select_candidate(brain, args.name, data['candidates'], stopped.is_set) if data['candidates'] else {'decision': 'no_match', 'candidate_id': 0}
            report.update(resolution=data, decision=decision, passed=decision['decision'] == 'open', status='resolved_only')
            print(json.dumps({'decision': decision, 'candidates': [{k: item[k] for k in ('id', 'title', 'url', 'evidence')} for item in data['candidates']]}, ensure_ascii=False, indent=2), flush=True)
        return 0 if report['passed'] else 2
    except Exception as error:
        report.update(error_type=type(error).__name__, error=str(error))
        print('[未通过] ' + str(error), flush=True)
        return 2
    finally:
        signal.signal(signal.SIGINT, previous)
        report['seconds'] = round(time.monotonic() - started, 3)
        atomic_write(args.output_dir / 'verification.json', json.dumps(report, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
