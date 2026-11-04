"""长期记忆生产契约：来源、恢复、更正删除、并发提炼与非阻塞接线。"""
import json
import threading
import time

import pytest

from src.memory.longterm.store import FactStore, slot_key, atomic_json
from src.memory.longterm.extraction import rule_fact, intent, validate_proposals
from src.memory.longterm.service import MemoryService


def save(store, text, source='voice'):
    """按真实接受/提交协议保存合成事实。"""
    event = store.accept(text, {'kind': source})
    store.finish(event['id'], rule_fact(text, store.snapshot()))
    return event


def test_restart_correction_and_index_cannot_restore_deleted(tmp_path):
    """更正保持身份，旧版本索引失效；删除重启后依然为空。"""
    store = FactStore(tmp_path / 'memory-v2')
    save(store, '我喝咖啡不加糖')
    first = store.snapshot()[0]
    store.close()
    store = FactStore(tmp_path / 'memory-v2')
    assert store.search('我的咖啡怎么做')[0]['content'] == '我喝咖啡不加糖'
    save(store, '以后咖啡改成半糖')
    current = store.snapshot()[0]
    assert current['id'] == first['id'] and current['revision'] == 2
    assert not store.search('完全不同', [{'id': first['id'], 'revision': 1, 'distance': .01}])
    store.forget([first['id']])
    store.close()
    store = FactStore(tmp_path / 'memory-v2')
    assert not store.search('咖啡', [{'id': first['id'], 'revision': 2, 'distance': .01}])
    assert '咖啡' not in (tmp_path / 'memory-v2/MEMORY.md').read_text()
    store.close()


def test_late_extraction_cannot_override_new_statement_or_delete(tmp_path):
    """接受顺序而非模型返回顺序决定当前值，删除屏障撤销迟到候选。"""
    store = FactStore(tmp_path)
    old = store.accept('我喝咖啡不加糖', {'kind': 'voice'})
    save(store, '以后咖啡改成半糖')
    store.finish(old['id'], rule_fact(old['text']))
    assert store.snapshot()[0]['content'] == '以后咖啡改成半糖'
    pending = store.accept('我喝咖啡不加糖', {'kind': 'voice'})
    store.forget()
    assert store.finish(pending['id'], rule_fact(pending['text'])) == []
    assert store.snapshot() == []
    store.close()


def test_committed_journal_recovers_and_corrupt_source_fails_closed(tmp_path, monkeypatch):
    """真源写入失败后重启恢复已提交事务；无事务时损坏不回退旧索引。"""
    import src.memory.longterm.store as module
    store = FactStore(tmp_path)
    original = module.atomic_json

    def fail_state(path, data):
        """模拟提交日志已落盘而主文件替换失败。"""
        if path.name == 'state.json':
            raise OSError('simulated disk failure')
        return original(path, data)

    monkeypatch.setattr(module, 'atomic_json', fail_state)
    with pytest.raises(OSError):
        store.accept('我喜欢安静', {'kind': 'voice'})
    store.close()
    monkeypatch.setattr(module, 'atomic_json', original)
    restored = FactStore(tmp_path)
    assert len(restored.state['pending']) == 1
    restored.close()
    (tmp_path / 'state.json').write_text('{broken', encoding='utf-8')
    with pytest.raises(ValueError):
        FactStore(tmp_path)


def test_duplicate_event_and_writer_exclusion(tmp_path):
    """重复来源不会产生第二条事实，同时启动的写入者明确失败。"""
    store = FactStore(tmp_path)
    event = store.accept('我喝咖啡不加糖', {'kind': 'voice', 'utterance_id': 'same'})
    store.finish(event['id'], rule_fact(event['text']))
    assert store.accept(event['text'], {'utterance_id': 'same'}) is None
    with pytest.raises(OSError):
        FactStore(tmp_path)
    store.close()


@pytest.mark.parametrize('text', ['如果我以后咖啡不加糖', '他说我喝咖啡不加糖', '今天咖啡改成半糖',
                                  '我的咖啡是不是不加糖？', '不要记住我的咖啡不加糖', '记住密码是123456'])
def test_nonfacts_and_secrets_do_not_become_rule_facts(text):
    """假设、转述、临时例外、问题、否定保存及秘密不写入稳定偏好。"""
    assert rule_fact(text) == []


def test_model_cannot_invent_evidence_or_target():
    """语义提议只能保存本句连续原文，不能指定不存在的更正对象。"""
    item = dict(subject='用户', attribute='职业', content='我是一名教师', evidence='我是一名教师', target_id='', temporary=False)
    with pytest.raises(ValueError):
        validate_proposals([item], '我是一名程序员', [])
    item.update(content='我是一名程序员', evidence='我是一名程序员', target_id='invented')
    with pytest.raises(ValueError):
        validate_proposals([item], '我是一名程序员', [])


def test_migration_preserves_provenance_and_does_not_seed(tmp_path):
    """旧的推测留作候选，固定种子不迁移，删除后不会重新导入旧文件。"""
    (tmp_path / 'memory.json').write_text(json.dumps({'version': '1.0.0', 'facts': [
        {'id': 'one', 'content': '我在杭州工作', 'source': 'explicit'},
        {'id': 'two', 'content': '用户可能喜欢茶', 'source': 'inferred'},
        {'id': 'base_user_address', 'content': '叫老板', 'source': 'manual'}]}, ensure_ascii=False), encoding='utf-8')
    store = FactStore(tmp_path / 'memory-v2')
    assert len(store.snapshot()) == 1
    assert len(store.state['facts']) == 2
    store.forget()
    store.close()
    restored = FactStore(tmp_path / 'memory-v2')
    assert restored.snapshot() == []
    restored.close()


class SlowModel:
    """模拟等待推理期间存储/检索仍能独立完成。"""

    def __init__(self):
        """持有测试同步事件。"""
        self.started, self.release = threading.Event(), threading.Event()

    def propose(self, text, facts, should_stop=None):
        """可取消的慢调用，不连接真实模型。"""
        self.started.set()
        while not self.release.wait(.01):
            if should_stop and should_stop():
                return []
        return []


def test_service_nonblocking_pending_survives_restart_and_clear(tmp_path):
    """模型繁忙不阻塞明确事实、更正与删除；待处理文本持久保存。"""
    model = SlowModel()
    service = MemoryService(tmp_path, model=model, vector_factory=False)
    assert service.ready.wait(3) and not service.error
    service.request('turn', text='我最近一直在学习陶艺', source={'kind': 'voice'}).result(2)
    assert model.started.wait(2)
    start = time.monotonic()
    result = service.request('turn', text='记住我喝咖啡不加糖', source={'kind': 'voice'}).result(2)
    assert result['handled'] and result['status'] == 'completed'
    assert time.monotonic() - start < .5
    assert service.view()['pending'] == 1
    service.request('delete').result(2)
    model.release.set()
    service.close()
    restored = FactStore(tmp_path)
    assert restored.snapshot() == [] and not restored.state['pending']
    restored.close()


def test_service_natural_fact_immediate_recall_edit_delete(tmp_path):
    """真实服务的自然表述、当轮查询、GUI更正和删除闭环。"""
    service = MemoryService(tmp_path, model=SlowModel(), vector_factory=False)
    service.ready.wait(3)
    service.request('turn', text='咖啡我一直喝不加糖的', source={'kind': 'voice'}).result(2)
    answer = service.request('turn', text='我的咖啡怎么做？', source={'kind': 'voice'}).result(2)
    assert answer['handled'] and '不加糖' in answer['answer']
    fact = service.view()['facts'][0]
    service.request('edit', id=fact['id'], revision=fact['revision'], content='我的咖啡改成半糖').result(2)
    with pytest.raises(ValueError):
        service.request('edit', id=fact['id'], revision=fact['revision'], content='旧的更正').result(2)
    result = service.request('turn', text='忘掉我的咖啡偏好', source={'kind': 'voice'}).result(2)
    assert result['status'] == 'completed' and not service.view()['facts']
    service.close()
