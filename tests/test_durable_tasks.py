"""持久化任务的故障注入、恢复、授权与取消回归。"""
import json
from pathlib import Path
import threading
import time

import pytest

from src.brain.agent.assistant import AssistantTaskRunner
from src.brain.agent.boundary import AgentRefused
from src.brain.agent.durable import DurableTaskRunner, DurableTools
from src.brain.agent.task_manager import TaskManager
from src.brain.agent.task_store import TaskStore


class SequenceBrain:
    """执行确定工具序列，测试真实检查点和发布而非模型措辞。"""

    def __init__(self, calls):
        """保存调用序列及运行次数。"""
        self.calls, self.count = calls, 0

    def _run(self, prompt, system, tools, execute, should_stop, *args, **kwargs):
        """依次请求工具，允许同一序列在恢复后再次出现。"""
        self.count += 1
        for name, arguments in self.calls:
            execute(name, arguments)


def build(tmp_path, calls):
    """创建与生产资料隔离的任务库和输出目录。"""
    store = TaskStore(tmp_path / 'tasks')
    brain = SequenceBrain(calls)
    runner = DurableTaskRunner(store, AssistantTaskRunner(brain, tmp_path / 'output'))
    return store, runner, brain


def finish(citations='T1'):
    """提供可校验完成提议。"""
    return 'finish', {'status': 'completed', 'answer': 'bo s s，资料已整理。', 'citations': citations}


def wait_state(manager, identifier, statuses, seconds=5):
    """有界等待异步状态，失败时显示当前轻量记录。"""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        values = manager.list()
        record = next(r for r in values if r['task_id'] == identifier)
        if record['status'] in statuses:
            return record
        time.sleep(.01)
    pytest.fail(str(manager.list()))


def test_atomic_store_integrity_and_exclusive_owner(tmp_path):
    """损坏和第二进程所有权不会静默恢复或并发执行。"""
    store = TaskStore(tmp_path)
    record = store.create('整理资料')
    assert store.load(record['task_id'])['request'] == '整理资料'
    with store.ownership(), pytest.raises(AgentRefused, match='另一个'):
        with TaskStore(tmp_path).ownership():
            pass
    path = tmp_path / record['task_id'] / 'state.json'
    value = json.loads(path.read_text())
    value['record']['request'] = '改写的目标'
    path.write_text(json.dumps(value))
    with pytest.raises(AgentRefused, match='损坏'):
        store.load(record['task_id'])


def test_restore_reuses_read_and_append_without_duplicate(tmp_path):
    """恢复后重复工具提议只重用成功结果，已存文本不重复追加。"""
    calls = [('read_source', {'source': 'T1', 'offset': 0}),
             ('write_text', {'name': '结果.txt', 'content': '已确认内容'}), finish()]
    store, runner, brain = build(tmp_path, calls)
    record = store.create('总结资料', {'text': '真实资料'})
    tools = DurableTools(record, store, None)
    for name, args in calls[:-1]:
        tools.execute(name, args)
    # 模拟进程丢失所有 Python 对象，只从磁盘继续。
    resumed = store.load(record['task_id'])
    result = runner.run_stage(resumed)
    assert result.status == 'completed'
    assert (result.path.parent / '结果.txt').read_text() == '已确认内容'
    assert sum(e['name'] == 'write_text' for e in resumed['checkpoint']['trace']) == 1
    assert brain.count == 1


def test_partial_report_explains_unresolved_failure(tmp_path):
    """部分交付包含实际未解决错误，不只显示模型的完成声明。"""
    calls = [('read_source', {'source': 'unknown', 'offset': 0}),
             ('write_text', {'name': '结果.txt', 'content': '已保存草稿'}), finish('')]
    store, runner, _ = build(tmp_path, calls)
    record = store.create('生成文本草稿')
    result = runner.run_stage(record)
    assert result.status == 'partial'
    assert '未解决的工具错误' in result.path.read_text()
    assert 'read_source' in result.answer


def test_resume_publication_after_rename_before_completion_save(tmp_path, monkeypatch):
    """发布目录已出现但完成状态未写回时，恢复只核对哈希并复用。"""
    calls = [('read_source', {'source': 'T1', 'offset': 0}), finish()]
    store, runner, brain = build(tmp_path, calls)
    record = store.create('总结资料', {'text': '内容'})
    original = runner.runner._publish
    durable_state = []
    def crash_after_publish(*args):
        """捕获真正发布前检查点并在目录落盘后模拟退出。"""
        durable_state.append(store.load(record['task_id']))
        original(*args)
        raise SystemExit('crash')
    monkeypatch.setattr(runner.runner, '_publish', crash_after_publish)
    with pytest.raises(SystemExit):
        runner.run_stage(record)
    store.save(durable_state[0])
    result = runner.run_stage(store.load(record['task_id']))
    assert result.path.is_file() and result.status == 'completed'
    assert brain.count == 1


def test_changed_input_pauses_until_explicit_refresh(tmp_path):
    """变化输入不会自动沿旧证据交付；刷新后受影响读取失效并重新执行。"""
    source = tmp_path / '资料.txt'
    source.write_text('旧内容')
    store, runner, _ = build(tmp_path, [('read_source', {'source': 'F1', 'offset': 0}), finish('F1')])
    record = store.create('总结文件', {'files': [str(source)]})
    tools = DurableTools(record, store, None)
    tools.execute('read_source', {'source': 'F1', 'offset': 0})
    import hashlib
    record['fingerprints'] = {'F1': {'path': str(source), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}}
    store.save(record)
    source.write_text('新内容')
    assert runner.run_stage(record) is None
    assert record['status'] == 'needs_input'
    manager = TaskManager(store, runner)
    manager.start()
    try:
        manager.resume(record['task_id'], refresh=True)
        done = wait_state(manager, record['task_id'], {'completed'})
        evidence = json.loads(Path(done['report']).with_name('evidence.json').read_text())
        assert evidence['sources']['F1']['text'] == '新内容'
    finally:
        manager.close()


def test_budget_survives_restart_and_requires_extension(tmp_path):
    """重启不重置调用预算；只有显式延长才继续模型。"""
    store, runner, brain = build(tmp_path, [finish('')])
    record = store.create('解释一句话')
    record['budget']['used_calls'] = 120
    store.save(record)
    manager = TaskManager(store, runner)
    manager.start()
    try:
        wait_state(manager, record['task_id'], {'paused'})
        assert brain.count == 0
        manager.resume(record['task_id'], extra_calls=12)
        wait_state(manager, record['task_id'], {'completed'})
    finally:
        manager.close()


def test_close_preserves_work_and_cancel_prevents_publication(tmp_path):
    """退出保存为可恢复队列，显式取消则不会发布最终文件。"""
    entered, released = threading.Event(), threading.Event()
    store, runner, _ = build(tmp_path, [])
    class WaitingBrain:
        """在可取消等待处停留，模拟正在推理。"""
        def _run(self, prompt, system, schemas, execute, should_stop, *args, **kwargs):
            """先保存中间结果，再等待控制信号。"""
            execute('write_text', {'name': '结果.txt', 'content': '已保存'})
            entered.set()
            while not should_stop() and not released.wait(.01):
                pass
            execute('finish', {'status': 'completed', 'answer': '完成', 'citations': ''})
    runner.runner.brain = WaitingBrain()
    manager = TaskManager(store, runner)
    identifier = manager.submit('写一段文字')
    assert entered.wait(3)
    manager.control(identifier, 'cancel')
    wait_state(manager, identifier, {'cancelled'})
    manager.close()
    assert not (tmp_path / 'output' / identifier).exists()
    assert store.load(identifier)['checkpoint']['artifacts']


def test_real_process_exit_after_tool_checkpoint(tmp_path):
    """真实子进程在工具返回后直接退出，另一个进程对象从磁盘恢复且不重复追加。"""
    import subprocess
    import sys
    store, runner, _ = build(tmp_path, [('read_source', {'source':'T1','offset':0}),
        ('write_text', {'name':'结果.txt','content':'只出现一次'}), finish()])
    record = store.create('总结资料', {'text':'真实输入'})
    program = '''import os,sys
from src.brain.agent.task_store import TaskStore
from src.brain.agent.durable import DurableTools
store=TaskStore(sys.argv[1]); record=store.load(sys.argv[2]); record['status']='running'
tools=DurableTools(record,store,None)
tools.execute('read_source',{'source':'T1','offset':0})
tools.execute('write_text',{'name':'结果.txt','content':'只出现一次'})
os._exit(73)
'''
    child = subprocess.run([sys.executable, '-c', program, str(store.root), record['task_id']], timeout=10)
    assert child.returncode == 73
    manager = TaskManager(store, runner)
    manager.start()
    try:
        done = wait_state(manager, record['task_id'], {'completed'})
        assert Path(done['report']).with_name('结果.txt').read_text() == '只出现一次'
        assert done['budget']['used_seconds'] >= 179
    finally:
        manager.close()


def test_voice_handoff_survives_invalidation_and_uses_new_speech_epoch(tmp_path):
    """已接受任务由独立调度器持有，重连只撤销音频；完成播报绑定新代次。"""
    import numpy as np
    from src.omni.gateway_client import GatewayCallbacks
    from src.omni.task_pipeline import TaskPipeline, Utterance
    events, accepted, spoken = [], [], []
    class Callbacks(GatewayCallbacks):
        """收集不含音视频的任务事件。"""
        def on_task_event(self, state, detail):
            """保存状态以核对归属。"""
            events.append(state)
    pipeline = TaskPipeline(Callbacks(), runner_factory=lambda: None)
    pipeline.begin_session()
    old = pipeline.generation
    pipeline.assistant_submitter = lambda text, **kwargs: accepted.append((text, kwargs))
    pipeline.on_result_speech = lambda identifier, generation, result, fields: spoken.append(generation)
    text = '帮我整理项目资料'
    utterance = Utterance('voice-test', old, time.monotonic(), np.zeros(0), False)
    assert pipeline.submit(utterance, text, ('assistant', text))
    pipeline.invalidate()
    assert len(accepted) == 1 and not pipeline.busy
    detail = {'task_id':'managed-test','answer':'完成','path':None}
    pipeline.deliver_managed('completed', detail)
    assert spoken == []
    pipeline.begin_session()
    pipeline.deliver_managed('completed', detail)
    assert spoken == [pipeline.generation] and spoken[0] != old


def test_missing_materials_can_resume_same_task(tmp_path):
    """待补充任务保持编号和已有草稿，补充文件后继续交付。"""
    store, runner, brain = build(tmp_path, [('finish', {'status':'needs_input','answer':'请提供原始文件','citations':''})])
    manager = TaskManager(store, runner)
    identifier = manager.submit('总结文件')
    try:
        wait_state(manager, identifier, {'needs_input'})
        source = tmp_path / '补充.txt'
        source.write_text('明确资料')
        brain.calls = [('read_source', {'source':'F1','offset':0}), finish('F1')]
        manager.resume(identifier, inputs={'files':[str(source)]})
        done = wait_state(manager, identifier, {'completed'})
        assert done['task_id'] == identifier
    finally:
        manager.close()


def test_unknown_pending_action_never_replays(tmp_path):
    """未来外部动作没有明确回执时，即使重启也不自动重放。"""
    store, runner, brain = build(tmp_path, [finish('')])
    record = store.create('执行任务')
    record['pending'] = {'name':'external_send','recovery':'unknown'}
    store.save(record)
    runner.run_stage(record)
    assert record['status'] == 'needs_input' and brain.count == 0
