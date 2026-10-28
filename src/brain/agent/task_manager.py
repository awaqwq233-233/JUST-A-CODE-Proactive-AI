"""独立于音视频会话的单任务调度器，支持排队、暂停、补资料及重启恢复。"""
from __future__ import annotations

import copy
import hashlib
import threading
import time

from .boundary import AgentRefused
from .durable import DurableTaskRunner, changed_inputs
from .materials import Materials, read_bytes
from .task_store import TaskStore, TERMINAL


class TaskManager:
    """一个工作线程拥有模型；UI 操作只改变任务控制状态，不操作设备。"""

    def __init__(self, store=None, runner=None, on_event=None):
        """可注入独立状态目录和模型替身，生产默认持久化到 data/tasks。"""
        self.store = store or TaskStore()
        self.runner = runner or DurableTaskRunner(self.store)
        self.on_event = on_event
        self.lock = self.store.lock
        self.records = {}
        self.wake, self.closing, self.ready = threading.Event(), threading.Event(), threading.Event()
        self.thread, self.active, self.error = None, None, None

    def start(self):
        """后台取得独占锁并恢复任务索引，重复启动不会创建第二个线程。"""
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            self.closing.clear()
            self.ready.clear()
            self.error = None
            self.thread = threading.Thread(target=self._loop, daemon=True, name='jac-task-manager')
            self.thread.start()
        if not self.ready.wait(5):
            raise AgentRefused('任务存储启动超时')
        if self.error:
            raise AgentRefused(self.error)

    def emit(self, state, record, **extra):
        """只发送轻量状态和实际结果位置；界面回调异常不能破坏已保存的任务。"""
        if self.on_event:
            detail = {'id': record['task_id'], 'task_id': record['task_id'], 'managed': True,
                      'answer': record.get('answer') or record.get('message', ''),
                      'path': record.get('report'), 'request': record.get('request', ''), **extra}
            try:
                self.on_event(state, detail)
            except Exception:
                pass

    def submit(self, task, inputs=None, source=None):
        """接受任务后即返回稳定编号，最多五项等待，不向模型自动扩大权限。"""
        self.start()
        with self.lock:
            if self.closing.is_set():
                raise AgentRefused('程序正在关闭，未接受新任务')
            if sum(r['status'] == 'queued' for r in self.records.values()) >= 5:
                raise AgentRefused('已有五项排队任务，请等待或取消一项')
            record = self.store.create(task, copy.deepcopy(inputs), copy.deepcopy(source))
            self.records[record['task_id']] = record
        self.emit('queued', record)
        self.wake.set()
        return record['task_id']

    def list(self):
        """返回内存中的轻量任务列表，Qt 刷新不重复读取资料正文。"""
        with self.lock:
            keys = ('task_id', 'request', 'status', 'message', 'report', 'stage', 'plan', 'budget',
                    'created_at', 'updated_at', 'changed_inputs', 'notification', 'order')
            return [copy.deepcopy({k: r[k] for k in keys if k in r})
                    for r in sorted(self.records.values(), key=lambda value: value.get('order', 0))]

    def control(self, task_id, action):
        """暂停和取消先持久化控制标记，与发布共锁，已交付文件不伪装撤回。"""
        if action not in {'pause', 'cancel'}:
            raise ValueError('不支持的任务控制')
        with self.lock:
            record = self.records[task_id]
            if record['status'] in TERMINAL | {'corrupt'}:
                return False
            record['control'] = action
            if self.active != task_id:
                record['status'] = 'paused' if action == 'pause' else 'cancelled'
            else:
                record['status'] = 'pausing' if action == 'pause' else 'cancelling'
            self.store.save(record)
        self.emit(record['status'], record)
        self.wake.set()
        return True

    def resume(self, task_id, *, extra_seconds=0, extra_calls=0, inputs=None, note='', refresh=False):
        """用户补充同一任务；预算仅明确延长，刷新只使依赖已变资料的结果失效。"""
        if type(extra_seconds) is not int or not 0 <= extra_seconds <= 1800 or type(extra_calls) is not int or not 0 <= extra_calls <= 120:
            raise AgentRefused('每次最多增加30分钟/120次调用')
        if not isinstance(note, str) or len(note) > 4000:
            raise AgentRefused('补充要求须不超过4000字')
        with self.lock:
            record = self.records[task_id]
            if self.active == task_id or record['status'] not in {'paused', 'needs_input', 'failed'}:
                raise AgentRefused('只能继续已暂停、待补充或失败任务')
            if sum(r['status'] == 'queued' for r in self.records.values()) >= 5:
                raise AgentRefused('等待队列已满')
            if record.get('publication'):
                raise AgentRefused('该任务已有交付清单，不能修改恢复目标；请新建后续任务')
            record.setdefault('supplements', []).append({'inputs': copy.deepcopy(inputs or {}), 'note': note, 'refresh': refresh})
            record['budget']['seconds'] += extra_seconds
            record['budget']['calls'] += extra_calls
            record.update(status='queued', control=None, stalls=0, failures=0, message='')
            self.store.save(record)
        self.emit('queued', record)
        self.wake.set()

    def prioritize(self, task_id):
        """将等待任务移到队首，不抢占当前执行的工具调用。"""
        with self.lock:
            record = self.records[task_id]
            if record['status'] != 'queued':
                raise AgentRefused('只有排队任务可以调整顺序')
            record['order'] = min(r.get('order', 0) for r in self.records.values()) - 1
            self.store.save(record)
        self.wake.set()

    def delete(self, task_id):
        """删除已停止的私有检查点；报告仍可在结果目录另行管理。"""
        with self.lock:
            if self.active == task_id:
                raise AgentRefused('任务仍在运行，请先暂停')
            self.store.delete(task_id)
            del self.records[task_id]

    def acknowledge(self, task_id):
        """记录界面已展示交付通知，不把展示等同于语音播放完成。"""
        with self.lock:
            record = self.records[task_id]
            record['notification'] = 'shown'
            self.store.save(record)

    def close(self, timeout=12):
        """退出时要求所有在途任务保存，不安装守护进程；超时保留恢复记录。"""
        self.closing.set()
        self.wake.set()
        thread = self.thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout)
            if thread.is_alive():
                raise AgentRefused('任务正在保存或等待工具退出，请稍后重试关闭')

    def _apply_supplements(self, record):
        """在工作线程读取补充资料，保持既有编号并传播失效依赖。"""
        for supplement in record.get('supplements', []):
            saved = record.get('checkpoint')
            if not saved:
                existing = record['inputs']
                extra = supplement['inputs']
                for key in ('files', 'directories'):
                    existing[key] = list(dict.fromkeys(existing.get(key, []) + extra.get(key, [])))
                existing['text'] = '\n'.join(filter(None, [existing.get('text', ''), extra.get('text', ''), supplement['note']]))
            else:
                if supplement['refresh']:
                    changed = set(changed_inputs(record))
                    affected = set(changed)
                    while True:
                        expanded = affected | {key for key, source in saved['sources'].items()
                                               if set(source['provenance'].get('inputs', [])) & affected}
                        if expanded == affected:
                            break
                        affected = expanded
                    for key in affected:
                        saved['sources'].pop(key, None)
                        saved['read_ranges'].pop(key, None)
                    for name, deps in list(saved.get('dependencies', {}).items()):
                        if affected.intersection(deps):
                            saved['artifacts'].pop(name, None)
                            saved['dependencies'].pop(name, None)
                    saved['reuse'] = {key: value for key, value in saved.get('reuse', {}).items()
                                      if not affected.intersection(value['dependencies'])}
                    for name in list(record.get('artifact_sources', {})):
                        if name not in saved['artifacts']:
                            record['artifact_sources'].pop(name, None)
                    for key in changed:
                        info = record['fingerprints'][key]
                        info['sha256'] = hashlib.sha256(read_bytes(info['path'])).hexdigest()
                    record.pop('changed_inputs', None)
                    record.pop('publication', None)
                    saved['trace'] = [dict(entry, stale_input=True, status='invalidated') if
                                      any(key in str(entry.get('arguments', {})) for key in affected)
                                      else entry for entry in saved['trace']]
                extra = Materials(**supplement['inputs'])
                for path in extra.paths.values():
                    if str(path) not in saved['paths'].values():
                        key = 'F' + str(max([int(k[1:]) for k in saved['paths']] or [0]) + 1)
                        saved['paths'][key] = str(path)
                        record.setdefault('fingerprints', {})[key] = {'path': str(path), 'sha256': hashlib.sha256(read_bytes(path)).hexdigest()}
                for value in extra.sources.values():
                    key = 'U' + str(len(saved['sources']) + 1)
                    saved['sources'][key] = {**value, 'id': key}
                # 新私有资料进入后永久禁用公网工具，避免后续查询携带资料。
                if extra.inventory():
                    saved['allow_web'] = False
                saved['finished'] = None
            if supplement['note']:
                record['notes'].append(supplement['note'])
                if saved:
                    saved['allow_web'] = False
        record['supplements'] = []
        self.store.save(record)

    def _loop(self):
        """独占持久化调度，崩溃恢复先核销租约并检查输入，再开启下一阶段。"""
        try:
            with self.store.ownership():
                with self.lock:
                    for record in self.store.list():
                        if record['status'] in {'running', 'publishing', 'pausing', 'cancelling'}:
                            record['budget']['used_seconds'] += record.get('lease_seconds', 0)
                            record['lease_seconds'] = 0
                            control = record.get('control')
                            record['status'] = 'cancelled' if control == 'cancel' else 'paused' if control == 'pause' else 'queued'
                            self.store.save(record)
                        self.records[record['task_id']] = record
                self.ready.set()
                for record in self.records.values():
                    self.emit('task_restored', record, restored_status=record['status'])
                while not self.closing.is_set():
                    with self.lock:
                        queued = sorted((r for r in self.records.values() if r['status'] == 'queued'), key=lambda r: r['order'])
                        record = queued[0] if queued else None
                        self.active = record['task_id'] if record else None
                    if record is None:
                        self.wake.wait(.5)
                        self.wake.clear()
                        continue
                    self._execute(record)
        except Exception as error:
            self.error = str(error)
            self.ready.set()
            self.emit('manager_error', {'task_id': '', 'message': self.error})
        finally:
            self.active = None

    def _execute(self, record):
        """阶段异常保留检查点，连续无进展或预算到达后停下，不无限重试。"""
        try:
            if record.get('supplements'):
                self._apply_supplements(record)
            budget = record['budget']
            if budget['used_seconds'] >= budget['seconds'] or budget['used_calls'] >= budget['calls']:
                record.update(status='paused', message='任务预算用尽，已保存；可选择延长预算后继续')
                return
            self.emit('running', record)
            self.runner.run_stage(record,
                should_stop=lambda: self.closing.is_set() or bool(record.get('control')),
                on_tool_result=lambda entry: self.emit('tool_failed' if entry.get('status') == 'failed' else 'tool_completed', record, **entry),
                publication_lock=self.lock)
        except Exception as error:
            if '正忙' in str(error):
                record.update(status='queued', message='等待当前单步任务释放模型')
                self.wake.wait(.5)
                self.wake.clear()
            else:
                record.update(status='needs_input', message=str(error)[:500])
        finally:
            with self.lock:
                control = record.get('control')
                if record['status'] not in TERMINAL:
                    if control:
                        record['status'] = 'cancelled' if control == 'cancel' else 'paused'
                    elif self.closing.is_set():
                        record['status'] = 'queued'
                record['lease_seconds'] = 0
                self.store.save(record)
                self.active = None
            self.emit(record['status'], record)
