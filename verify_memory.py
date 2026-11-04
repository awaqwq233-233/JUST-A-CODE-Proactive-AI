#!/usr/bin/env python3
"""隔离长期记忆验收；可选真实 Qwen 与无设备 Gateway 合成音视频回放。"""
import argparse
import asyncio
from collections import Counter
import json
from pathlib import Path
import tempfile
import time

from src.memory.longterm.service import MemoryService


def wait_pending(service, timeout=45):
    """等待真实提炼完成，逾时不把候选当作已保存事实。"""
    deadline = time.monotonic() + timeout
    while service.view().get('pending'):
        if time.monotonic() > deadline:
            raise RuntimeError('语义提炼未在预算内完成')
        time.sleep(.1)


def self_test(directory, use_model=False):
    """验证自然记忆、重启、更正、删除、索引重建和真实检索延迟。"""
    service = MemoryService(directory)
    assert service.ready.wait(5) and not service.error
    timings = []
    try:
        service.request('turn', text='咖啡我一直喝不加糖的', source={'kind': 'synthetic'}).result(5)
        if use_model:
            service.request('turn', text='我现在在杭州做嵌入式开发，平时主要用Python。', source={'kind': 'synthetic'}).result(5)
            wait_pending(service)
        service.request('flush_index').result(5)
        if service.index:
            service.index.ready.wait(45)
        time.sleep(2)
        assert service.view()['vector'] == 'ready', service.view().get('error')
        for _ in range(20):
            started = time.monotonic()
            answer = service.request('turn', text='我的咖啡怎么做？', source={'kind': 'synthetic'}).result(5)
            timings.append((time.monotonic() - started) * 1000)
            assert '不加糖' in answer['answer'], ('coffee', answer)
        if use_model:
            answer = service.request('turn', text='我平时写程序用什么语言？', source={'kind': 'synthetic'}).result(20)
            assert 'Python' in answer['answer'], ('programming', answer, service.view()['facts'])
    finally:
        service.close()
    service = MemoryService(directory)
    assert service.ready.wait(5)
    try:
        answer = service.request('turn', text='我的咖啡怎么做？', source={'kind': 'synthetic'}).result(5)
        assert '不加糖' in answer['answer']
        service.request('turn', text='以后咖啡改成半糖', source={'kind': 'synthetic'}).result(5)
        answer = service.request('turn', text='我的咖啡怎么做？', source={'kind': 'synthetic'}).result(5)
        assert '半糖' in answer['answer'] and '不加糖' not in answer['answer']
        service.request('delete').result(5)
        service.request('rebuild_index').result(10)
        assert service.index.ready.wait(45) and not service.index.error
        assert not service.request('search', text='咖啡').result(5)['facts']
    finally:
        service.close()
    service = MemoryService(directory)
    assert service.ready.wait(5)
    try:
        assert service.index.ready.wait(45) and not service.index.error
        assert not service.request('search', text='咖啡').result(5)['facts']
    finally:
        service.close()
    return {'passed': True, 'qwen_tested': use_model, 'retrieval_p95_ms': sorted(timings)[int(len(timings) * .95) - 1],
            'retrieval_max_ms': max(timings), 'devices_opened': False}


async def native_test(directory, audio_dir, seconds, session_seconds=120, expected_correction='半糖'):
    """真实 Gateway/Whisper/原生音频链路，静态合成画面与语音，永不打开采集或播放设备。"""
    import cv2
    import numpy as np
    from src.omni.gateway_client import GatewayClient, GatewayCallbacks
    from src.omni.gateway_cli import ReplayDevices
    from src.omni.realtime_protocol import load_audio
    counts, answers = Counter(), []

    class Callbacks(GatewayCallbacks):
        """只记录合成验收结果与计数，不写实际用户媒体。"""

        def on_task_event(self, state, detail):
            """统计记忆与原生播报状态，不把收到音频等同真人听感。"""
            counts[state] += 1
            if state == 'memory_answer':
                answers.append(detail.get('answer', ''))
            if state.startswith('memory_') or state.startswith('speech_'):
                print(state, detail.get('answer') or detail.get('code', ''), flush=True)

        def on_state(self, state, info=None):
            """记录会话轮换进度。"""
            nonlocal number
            counts['gateway_' + state] += 1
            if state == 'ready':
                number += 1
            print('Gateway', state, flush=True)

    images = cv2.imencode('.jpg', np.full((480, 640, 3), 80, dtype=np.uint8))[1].tobytes()
    clips = {name: load_audio(audio_dir / (name + '.aiff')) for name in ('remember', 'recall', 'correct')}
    number = 0

    def devices(**kwargs):
        """依次回放记住、跨会话提问、更正和后续提问。"""
        name = ('remember', 'recall', 'correct')[number] if number < 3 else 'recall'
        samples = np.concatenate((np.zeros(5 * 16000, dtype='<f4'), clips[name]))
        return ReplayDevices(samples, images, **kwargs)

    def refuse_task(*args, **kwargs):
        """本探针仅验收记忆，禁止恢复或创建生产任务。"""
        raise RuntimeError('记忆探针不执行电脑任务')

    memory = MemoryService(directory)
    client = GatewayClient(consent_devices=True, callbacks=Callbacks(), device_factory=devices,
        session_seconds=session_seconds, transcription_enabled=True, memory_provider=lambda: memory,
        context_provider=memory.bootstrap, task_submitter=refuse_task)
    memory.on_change = client.memory_changed
    started = time.monotonic()
    error = None
    try:
        await asyncio.wait_for(client.run(), timeout=seconds)
    except TimeoutError:
        pass
    except Exception as failure:
        error = str(failure)
    finally:
        memory.close()
    return {'seconds': time.monotonic() - started, 'stats': client.stats(), 'counts': dict(counts), 'error': error,
            'answers': answers, 'devices_opened': False, 'actual_playback_tested': False,
            'passed': not error and time.monotonic() - started >= seconds and counts['memory_answer'] >= 3 and counts['speech_completed'] >= 2
                      and bool(answers) and expected_correction in answers[-1]
                      and not client.stats().get('cleanup_failures')}


def main(argv=None):
    """所有验收使用新隔离目录，报告明确区分模型、协议和实际播放。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', action='store_true')
    parser.add_argument('--native', action='store_true')
    parser.add_argument('--seconds', type=int, default=360)
    parser.add_argument('--session-seconds', type=int, default=120)
    parser.add_argument('--expected-correction', default='半糖')
    parser.add_argument('--audio-dir', type=Path, default=Path('output/memory-validation'))
    parser.add_argument('--report', type=Path, default=Path('output/memory-validation/report.json'))
    args = parser.parse_args(argv)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    report = {'passed': False}
    try:
        with tempfile.TemporaryDirectory(prefix='jac-memory-verify-') as directory:
            directory = Path(directory).resolve()
            if args.native:
                report = asyncio.run(native_test(directory, args.audio_dir, args.seconds, args.session_seconds, args.expected_correction))
            else:
                report = self_test(directory, args.model)
    except Exception as error:
        report['error'] = str(error)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['passed'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
