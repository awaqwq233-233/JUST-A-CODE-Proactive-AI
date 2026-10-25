#!/usr/bin/env python3
"""显式任务验证入口：可选择本地资料或指定微信会话，默认不打开物理音视频设备。"""
import argparse
import json
from pathlib import Path

from src.brain.agent.production import JACBrainTaskRunner, check_installation
from src.tools.wechat import check_wechat


def main():
    """执行用户显式传入的任务并打印真实交付状态；微信只读也须在任务中点名。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task')
    parser.add_argument('--file', action='append', default=[])
    parser.add_argument('--directory', action='append', default=[])
    parser.add_argument('--text', default='')
    parser.add_argument('--check-wechat', action='store_true')
    args = parser.parse_args()
    if args.check_wechat:
        print(json.dumps(check_wechat(), ensure_ascii=False))
        return
    if not args.task:
        parser.error('请提供 --task 或 --check-wechat')
    check_installation()
    result = JACBrainTaskRunner().run(args.task, assistant_task=True,
        inputs={'files': [str(Path(p).absolute()) for p in args.file],
                'directories': [str(Path(p).absolute()) for p in args.directory], 'text': args.text})
    print(json.dumps({'status': result.status, 'task_id': result.task_id, 'report': str(result.path),
                      'answer': result.answer, 'seconds': result.elapsed_seconds}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
