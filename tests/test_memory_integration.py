"""生产记忆边界：故障关闭、任务优先级、迟到语音与后台让出。"""
import copy
from concurrent.futures import Future
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from src.memory.longterm.store import FactStore, atomic_json
from src.memory.longterm.service import MemoryService
from src.memory.longterm.extraction import rule_fact
from src.omni.task_speech import TaskSpeechQueue
from src.omni.gateway_client import GatewayCallbacks
from src.omni.task_pipeline import TaskPipeline, Utterance


def save(store, text):
    """通过完整事务保存合成事实。"""
    event = store.accept(text, {'kind': 'synthetic'})
    store.finish(event['id'], rule_fact(text, store.snapshot()))


class PausedModel:
    """可取消模型替身用于注入稳定的等待状态。"""

    def propose(self, text, facts, should_stop=None):
        """退出时返回空提议，验证取消不会误消费持久候选。"""
        while not should_stop():
            time.sleep(.01)
        return []


def test_old_journal_cannot_undo_newer_deletion(tmp_path):
    """旧恢复记录不能覆盖更新主文件中的删除屏障。"""
    store = FactStore(tmp_path)
    save(store, '我喝咖啡不加糖')
    before = copy.deepcopy(store.state)
    store.forget()
    store.close()
    atomic_json(tmp_path / 'commit.json', before)
    restored = FactStore(tmp_path)
    assert restored.snapshot() == []
    restored.close()


def test_recreated_fact_never_reuses_deleted_revision(tmp_path):
    """删除后重新记住同一属性，旧任务引用仍然失效。"""
    store = FactStore(tmp_path)
    save(store, '我喝咖啡不加糖')
    old = store.snapshot()[0]
    store.forget()
    save(store, '我喝咖啡半糖')
    current = store.snapshot()[0]
    assert current['id'] == old['id'] and current['revision'] > old['revision']
    assert not store.search('无关', [{'id': old['id'], 'revision': old['revision'], 'distance': 0}])
    store.close()


@pytest.mark.parametrize('text', ['帮我查询电池电量是多少？', '我的电脑还有多少电量？', '帮我解释一下什么是递归？', '帮我查询北京天气怎么样？'])
def test_memory_does_not_steal_computer_or_general_tasks(tmp_path, text):
    """含有我字的任务仍由现有授权和工具路由负责。"""
    service = MemoryService(tmp_path, model=PausedModel(), vector_factory=False)
    assert service.ready.wait(3)
    try:
        assert not service.request('turn', text=text, source={'kind': 'voice', 'task': True}).result(2)['handled']
    finally:
        service.close()


def test_unrelated_pending_does_not_block_known_fact(tmp_path):
    """后台整理别的话题时，已保存的明确事实可以立即回答。"""
    service = MemoryService(tmp_path, model=PausedModel(), vector_factory=False)
    service.ready.wait(3)
    try:
        service.request('turn', text='我喝咖啡不加糖', source={}).result(2)
        service.request('turn', text='我最近一直在学习陶艺', source={}).result(2)
        answer = service.request('turn', text='我的咖啡怎么做？', source={}).result(2)
        assert '不加糖' in answer['answer']
    finally:
        service.close()
    store = FactStore(tmp_path)
    assert len(store.state['pending']) == 1
    store.close()


def test_failed_commit_invalidates_service_and_current_context(tmp_path, monkeypatch):
    """磁盘提交失败时停用热快照，不能继续以旧事实回答或确认删除成功。"""
    import src.memory.longterm.store as module
    changes = []
    service = MemoryService(tmp_path, model=PausedModel(), vector_factory=False, on_change=lambda *args: changes.append(args))
    service.ready.wait(3)
    service.request('turn', text='我喝咖啡不加糖', source={}).result(2)
    original = module.atomic_json

    def fail_state(path, data):
        """在提交日志成功后模拟磁盘失败。"""
        if path.name == 'state.json':
            raise OSError('disk full')
        original(path, data)

    monkeypatch.setattr(module, 'atomic_json', fail_state)
    with pytest.raises(OSError):
        service.request('delete').result(2)
    service.thread.join(3)
    assert service.error and not service.view()['facts'] and changes[-1][1]
    service.close()
    monkeypatch.setattr(module, 'atomic_json', original)
    restored = FactStore(tmp_path)
    assert not restored.snapshot()
    restored.close()


def test_stale_memory_cancels_pending_and_received_native_audio():
    """版本更正后未发与已发音频都失效，即使 Gateway 会话尚未轮换。"""
    revision = [1]
    pipeline = SimpleNamespace(lock=threading.RLock(), current=lambda generation: True, user_quiet=lambda: True,
                               memory=SimpleNamespace(valid=lambda value: value == revision[0]))
    queue = TaskSpeechQueue(pipeline, GatewayCallbacks())
    result = SimpleNamespace(answer='咖啡不加糖', memory_revision=1)
    queue.offer('memory:1', 1, result, ('memory',))
    revision[0] = 2
    assert queue.next_input(True, 100) is None
    result.memory_revision = 2
    queue.offer('memory:2', 1, result, ('memory',))
    queue.next_input(True, 100)
    guard = queue.audio('memory:2', np.ones(24))
    revision[0] = 3
    assert queue.audio('memory:2', np.ones(24)) is None and guard.is_set()


def test_late_memory_answer_never_releases_new_utterance():
    """旧句的异步检索完成不能给新句播报或放行旧普通回答。"""
    pipeline = TaskPipeline(GatewayCallbacks(), decoder_factory=lambda _: SimpleNamespace())
    pipeline.begin_session()
    first = Utterance('old', pipeline.generation, time.monotonic(), np.zeros(16), False, pipeline.response_sequence)
    pipeline._response_started()
    spoken = []
    pipeline.on_result_speech = lambda *args: spoken.append(args)
    pipeline._route_confirmed(first, '我喝什么咖啡？', {'handled': True, 'answer': '旧答案'})
    assert not spoken


def test_foreground_task_can_yield_background_memory(tmp_path):
    """前台只请求后台记忆让出模型，不突破已有前台任务的互斥锁。"""
    from src.brain.agent.runner import serial_task
    entered, released = threading.Event(), threading.Event()

    def background():
        """模拟会观察取消标记的低优先级提炼。"""
        with serial_task(tmp_path, background=True):
            entered.set()
            deadline = time.monotonic() + 4
            while not (tmp_path / 'memory-yield').exists() and time.monotonic() < deadline:
                time.sleep(.01)
        released.set()

    thread = threading.Thread(target=background)
    thread.start()
    assert entered.wait(2)
    with serial_task(tmp_path):
        assert released.wait(1)
    thread.join(2)


def test_migration_candidates_are_reviewable_but_not_recalled(tmp_path):
    """旧推测可在界面核对保存，确认前不进入问答。"""
    import json
    (tmp_path / 'memory.json').write_text(json.dumps({'version': '1', 'facts': [
        {'id': 'candidate', 'source': 'inferred', 'content': '用户可能喜欢茶'}]}), encoding='utf-8')
    service = MemoryService(tmp_path / 'memory-v2', model=PausedModel(), vector_factory=False)
    service.ready.wait(3)
    try:
        assert not service.view()['facts']
        candidate = service.view()['candidate_facts'][0]
        service.request('edit', id=candidate['id'], revision=candidate['revision'], content='我喜欢喝红茶').result(2)
        assert service.view()['facts'][0]['content'] == '我喜欢喝红茶'
    finally:
        service.close()


def test_ambiguous_correction_does_not_replace_verified_value():
    """ASR同音词没有实际甜度证据时拒绝提议，不猜改转写。"""
    from src.memory.longterm.extraction import validate_proposals
    prior = {'id': 'coffee', 'subject': '用户', 'attribute': '咖啡甜度', 'revision': 1}
    item = {'subject': '用户', 'attribute': '咖啡甜度', 'content': '以后咖啡改成半堂',
            'evidence': '以后咖啡改成半堂', 'target_id': 'coffee'}
    with pytest.raises(ValueError, match='甜度'):
        validate_proposals([item], item['evidence'], [prior])


@pytest.mark.parametrize('error_text,retry', [('服务端错误: session_failed', True), ('音色条件未确认', False)])
def test_backend_transport_reconnect_is_bounded(monkeypatch, error_text, retry):
    """只重试可恢复连接失败，不吞掉音色和协议校验错误。"""
    import asyncio
    from src.omni.gateway_client import GatewayClient
    from src.omni import realtime_protocol
    monkeypatch.setattr(realtime_protocol, 'load_audio', lambda path: np.zeros(160))
    attempts = []
    client = GatewayClient(consent_devices=True, retry_limit=2)
    client._stats['sessions_started'] = 1

    async def session(voice):
        """模拟已清理旧代次后连接失败，第三次仍失败便应退出。"""
        attempts.append(True)
        raise RuntimeError(error_text)

    async def immediate_sleep(seconds):
        """跳过测试退避时间，不跳过重试数量检查。"""
        return None

    monkeypatch.setattr(client, '_session', session)
    monkeypatch.setattr(asyncio, 'sleep', immediate_sleep)
    with pytest.raises(RuntimeError, match=error_text):
        asyncio.run(client._run_sessions())
    assert len(attempts) == (3 if retry else 1)


def test_yielding_foreground_does_not_exhaust_extraction_attempts(tmp_path):
    """前台长任务占用模型不是提炼失败，空闲后仍自动尝试。"""
    store = FactStore(tmp_path)
    event = store.accept('我平时常读科幻小说', {'kind': 'synthetic'})
    for _ in range(5):
        store.defer(event['id'], yielded=True)
    assert store.state['pending'][event['id']]['attempts'] == 0
    assert not store.state['pending'][event['id']]['needs_input']
    store.close()


def test_migration_keeps_expiry_and_normalizes_known_slots(tmp_path):
    """过期旧记录不迁移成永久事实，已知偏好可被新更正覆盖。"""
    import json
    (tmp_path / 'memory.json').write_text(json.dumps({'version': '1', 'facts': [
        {'id': 'coffee', 'source': 'explicit', 'content': '我喝咖啡不加糖'},
        {'id': 'expired', 'source': 'explicit', 'content': '临时任务', 'ttl': '2000-01-01T00:00:00Z'}]}), encoding='utf-8')
    store = FactStore(tmp_path / 'memory-v2')
    assert len(store.snapshot()) == 1
    original_id = store.snapshot()[0]['id']
    save(store, '以后咖啡改成半糖')
    assert len(store.snapshot()) == 1 and store.snapshot()[0]['id'] == original_id
    store.close()


def test_expiry_invalidates_context_without_clearing_other_pending(tmp_path):
    """到期处理更新版本并保留无关候选，不允许旧索引恢复过期值。"""
    store = FactStore(tmp_path)
    event = store.accept('我在杭州工作', {'kind': 'synthetic'})
    store.finish(event['id'], [{'subject': '用户', 'attribute': '工作地点', 'content': event['text'],
                               'evidence': event['text'], 'expires_at': time.time() - 1}])
    revision = store.state['revision']
    pending = store.accept('我平时读科幻小说', {'kind': 'synthetic'})
    assert store.expire()
    assert store.state['revision'] > revision and not store.state['facts']
    assert pending['id'] in store.state['pending']
    store.close()


def test_native_probe_init_retry_does_not_skip_input_clips(tmp_path, monkeypatch):
    """测试会话初始化失败不能消费尚未回放的更正语句。"""
    import asyncio
    import verify_memory
    from src.omni import gateway_client, realtime_protocol
    sampled = []

    class FakeMemory:
        """不打开生产存储或真实模型的探针依赖。"""

        def __init__(self, directory):
            """保存回调占位供探针接线。"""
            self.on_change = None

        def bootstrap(self):
            """返回空初始化资料。"""
            return ''

        def close(self):
            """无后台资源需要回收。"""

    class FakeClient:
        """按固定顺序模拟初始化失败与成功。"""

        def __init__(self, **values):
            """只保存探针传入的回调与文件源工厂。"""
            self.factory, self.callbacks = values['device_factory'], values['callbacks']

        def memory_changed(self, *args):
            """本测试没有记忆变更事件。"""

        async def run(self):
            """失败创建文件源后不发送ready，下一次必须仍为同一素材。"""
            for ready in (True, False, True, False, True, True):
                device = self.factory()
                sampled.append(float(device.samples[-1]))
                if ready:
                    self.callbacks.on_state('ready')

        def stats(self):
            """提供空统计，测试不声明原生播报通过。"""
            return {}

    monkeypatch.setattr(verify_memory, 'MemoryService', FakeMemory)
    monkeypatch.setattr(gateway_client, 'GatewayClient', FakeClient)
    monkeypatch.setattr(realtime_protocol, 'load_audio', lambda path: np.array([{'remember': 1, 'recall': 2, 'correct': 3}[path.stem]], dtype='float32'))
    asyncio.run(verify_memory.native_test(tmp_path, tmp_path, 1))
    assert sampled == [1, 2, 2, 3, 3, 2]


def test_file_edit_wording_does_not_become_memory_correction(tmp_path):
    """明确编辑文件的改成措辞应留给任务执行器，不进入个人事实提炼。"""
    service = MemoryService(tmp_path, model=PausedModel(), vector_factory=False)
    service.ready.wait(3)
    try:
        answer = service.request('turn', text='帮我把文档标题改成十一月总结', source={'kind': 'gui', 'task': True}).result(2)
        assert not answer['handled'] and service.view()['pending'] == 0
        with pytest.raises(ValueError):
            service.request('edit', id='missing', revision=1, content='').result(2)
    finally:
        service.close()


def test_memory_change_during_publication_prevents_atomic_delivery(tmp_path):
    """文件整理完成后事实失效，最后发布检查仍阻止把旧报告交付出去。"""
    from src.brain.agent.assistant import AssistantTaskRunner
    from src.brain.agent.durable import DurableTaskRunner, MemoryChanged
    from src.brain.lm_studio import BrainCancelled
    calls = []

    def check():
        """第三次检查模拟删除发生于临时文件写完之后。"""
        calls.append(True)
        if len(calls) >= 3:
            raise MemoryChanged('事实已删除')

    assistant = AssistantTaskRunner(SimpleNamespace(), tmp_path / 'output')
    runner = DurableTaskRunner(SimpleNamespace(), assistant)
    tools = SimpleNamespace(check=check, save=lambda: None, artifacts={})
    record = {'task_id': 'test-memory-publication', 'status': 'running'}
    with pytest.raises((MemoryChanged, BrainCancelled)):
        runner.commit_files(record, {'report.txt': b'old memory'}, 'completed', 'old answer', tools, None, None)
    assert not (tmp_path / 'output' / record['task_id']).exists()
    assert record['status'] != 'completed'


def test_projection_error_after_delete_still_invalidates_hot_context(tmp_path, monkeypatch):
    """删除真源后可读投影写失败，不能继续让旧热缓存或语音有效。"""
    changes = []
    service = MemoryService(tmp_path, model=PausedModel(), vector_factory=False, on_change=lambda *args: changes.append(args))
    service.ready.wait(3)
    try:
        service.request('turn', text='我喝咖啡不加糖', source={}).result(2)
        revision = service.view()['revision']

        def fail_view():
            """模拟私有可读文件暂时不可写。"""
            raise OSError('projection unavailable')

        monkeypatch.setattr(service.store, 'write_view', fail_view)
        with pytest.raises(OSError):
            service.request('delete').result(2)
        assert not service.view()['facts'] and not service.valid(revision) and changes[-1][1]
    finally:
        service.close()


def test_automatic_new_fact_does_not_interrupt_unrelated_speech():
    """自动新增事实不取消正在播报的工具结果，更正删除才触发会话刷新。"""
    from src.omni.gateway_client import GatewayClient
    calls = []
    client = GatewayClient()
    client._speech = SimpleNamespace(cancel=lambda: calls.append(True))
    client.memory_changed(1, False)
    assert not calls and not client._memory_refresh.is_set()
    client.memory_changed(2, True)
    assert len(calls) == 1 and client._memory_refresh.is_set()
