#!/usr/bin/env python3
"""无音视频设备的长任务验证入口；独立目录可用于恢复、预算与故障验收。"""
import argparse
import json
from pathlib import Path
import time

from src.brain.agent.assistant import AssistantTaskRunner
from src.brain.agent.durable import DurableTaskRunner
from src.brain.agent.production import check_installation
from src.brain.agent.task_manager import TaskManager
from src.brain.agent.task_store import TaskStore, TERMINAL


def main():
    """提交或继续同一持久化任务，Ctrl+C保存并停止，下次可使用原任务编号继续。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task')
    parser.add_argument('--resume')
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--store', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--file', action='append', default=[])
    parser.add_argument('--text', default='')
    parser.add_argument('--note', default='')
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--extend', action='store_true')
    args = parser.parse_args()
    store = TaskStore(args.store)
    if args.list:
        print(json.dumps([{k: r.get(k) for k in ('task_id', 'request', 'status', 'message', 'report', 'budget')}
                          for r in store.list()], ensure_ascii=False, indent=2))
        return
    if not args.task and not args.resume:
        parser.error('须提供--task、--resume或--list')
    check_installation()
    manager = TaskManager(store, DurableTaskRunner(store, AssistantTaskRunner(output_dir=args.output)))
    try:
        manager.start()
        inputs = {'files': [str(Path(p).absolute()) for p in args.file], 'text': args.text}
        if args.resume:
            identifier = args.resume
            current = next(r for r in manager.list() if r['task_id'] == identifier)
            if current['status'] in {'paused', 'needs_input', 'failed'}:
                manager.resume(identifier, inputs=inputs, note=args.note, refresh=args.refresh,
                               extra_seconds=1800 if args.extend else 0, extra_calls=120 if args.extend else 0)
        else:
            identifier = manager.submit(args.task, inputs, {'kind': 'explicit_cli'})
        print(json.dumps({'task_id': identifier, 'accepted': True}, ensure_ascii=False), flush=True)
        while True:
            record = next(r for r in manager.list() if r['task_id'] == identifier)
            if record['status'] in TERMINAL | {'paused', 'needs_input'}:
                print(json.dumps(record, ensure_ascii=False, indent=2))
                break
            time.sleep(.2)
    except KeyboardInterrupt:
        print('任务已请求保存；未取消，可继续。')
    finally:
        manager.close()


if __name__ == '__main__':
    main()
