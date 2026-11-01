"""以真实工具检查点恢复 Qwen 任务；阶段历史可丢弃，授权与证据不可丢弃。"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
from pathlib import Path
import time

from src.brain.lm_studio import BrainCancelled
from src.brain.task_runner import TaskResult
from .assistant import AssistantTaskRunner, AssistantTools, PERSONA, schema, text_spec
from .boundary import AgentRefused
from .materials import Materials, read_bytes
from .profile import MODEL
from .routing import cloud_reason
from .runner import CACHE, serial_task
from .task_store import digest
from .action_tools import NAMES, VOLATILE, RECOVERY, ApprovalRequired, ActionUncertain, action_schemas, execute_action
from .action_policy import VERSION, LEGACY, scope
from src.tools.web_search import SearchError


class TaskPause(BrainCancelled):
    """当前执行已保存，可等待用户或下一阶段继续。"""


def snapshot(tools):
    """将资料、实际读取范围、调用与内存产物作为同一事务保存。"""
    return {'paths': {key: str(path) for key, path in tools.materials.paths.items()},
            'sources': tools.materials.sources, 'trace': tools.trace, 'read_ranges': tools.read_ranges,
            'artifacts': {name: base64.b64encode(data).decode('ascii') for name, data in tools.artifacts.items()},
            'finished': tools.finished, 'web_calls': tools.web_calls, 'allow_web': tools.allow_web,
            'reuse': tools.reuse, 'dependencies': tools.dependencies}


def restore(record):
    """恢复冻结的资料编号；已保存微信和网页快照不会因重启自动刷新。"""
    saved = record.get('checkpoint')
    if saved:
        materials = Materials()
        materials.paths = {key: Path(path) for key, path in saved['paths'].items()}
        materials.sources = copy.deepcopy(saved['sources'])
    else:
        materials = Materials(**record['inputs'])
    return materials


def changed_inputs(record):
    """校验所有已授权文件的版本，缺失和变化均作为待补资料而非模型错误。"""
    changed = []
    for key, info in record.get('fingerprints', {}).items():
        try:
            if hashlib.sha256(read_bytes(info['path'])).hexdigest() != info['sha256']:
                changed.append(key)
        except (OSError, AgentRefused):
            changed.append(key)
    return changed


def progress_fingerprint(tools):
    """只计算实际读取、内容和操作状态，重复同一工具回执不虚增进度。"""
    record = tools.record
    return digest({'reads': tools.read_ranges, 'workspace_reads': record.get('workspace_reads', {}),
                   'artifacts': {p: hashlib.sha256(v).hexdigest() for p, v in tools.artifacts.items()},
                   'sources': list(tools.materials.sources),
                   'edits': {p: {k: v[k] for k in ('status', 'after_hash')} for p, v in record.get('edits', {}).items()},
                   'execution': record.get('executions', [None])[-1],
                   'ui': {kind: {k: v for k, v in record.get(kind, {}).items() if k in {'app', 'title', 'url', 'text', 'elements'}}
                          for kind in ('desktop_observation', 'browser_observation')}})


class DurableTools(AssistantTools):
    """每次工具调用前后提交检查点，恢复时只重用确认过的成功结果。"""

    def __init__(self, record, store, should_stop, on_tool_result=None, **options):
        """绑定不可由模型修改的任务授权、预算与持久化设施。"""
        super().__init__(record['request'], restore(record), should_stop, on_tool_result, **options)
        self.record, self.store = record, store
        self.task_id = record['task_id']
        self.reuse, self.dependencies = {}, {}
        self.base_seconds = record['budget']['used_seconds']
        self.stage_calls = 0
        saved = record.get('checkpoint')
        if saved:
            for name in ('trace', 'read_ranges', 'finished', 'web_calls', 'allow_web', 'reuse', 'dependencies'):
                setattr(self, name, copy.deepcopy(saved.get(name, getattr(self, name))))
            self.artifacts = {name: base64.b64decode(data, validate=True) for name, data in saved['artifacts'].items()}
        self.stage_trace_start = len(self.trace)
        self.max_artifact_bytes = 4 * 1024 * 1024

    def _record_read(self, result):
        """合并实际读取的重叠区间，保留精确覆盖而不重复堆积上下文。"""
        super()._record_read(result)
        merged = []
        for start, end in sorted(self.read_ranges[result['id']]):
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        self.read_ranges[result['id']] = merged
        return result

    def schemas(self):
        """加入计划与草稿读取，阶段之间只携带有界摘要而不重灌全部历史。"""
        from .capabilities import extension_schemas
        specs = super().schemas() + extension_schemas(self)
        if scope(self.record) and not self.materials.inventory():
            specs = [s for s in specs if s['function']['name'] in {'finish', 'write_text', 'validate_result'}]
            for spec in specs:
                if spec['function']['name'] == 'finish':
                    spec['function']['description'] = '交付操作任务的结果；真实操作证据由程序自动关联。'
                    spec['function']['parameters']['properties'].pop('citations')
                    spec['function']['parameters']['required'].remove('citations')
        specs += action_schemas(self.record)
        specs += [schema('plan_task', '保存计划。steps_json示例：["读取资料","汇总","交付"]。criteria_json只支持required_columns字段数组、required_files文件名数组、expected_rows精确数据行数、min_rows最小行数；不确定项省略，用{}也可。',
                         {'steps_json': text_spec(), 'criteria_json': text_spec()}),
                  schema('read_artifact', '分页查看已保存文本草稿；只接受产物名称。',
                         {'name': text_spec(), 'offset': {'type': 'integer'}})]
        return specs

    def has_action_evidence(self):
        """只有授权工具的成功回执才可作为操作证据，最终文件与界面另作后置核验。"""
        return bool(scope(self.record)) and any(
            entry['name'] in NAMES and entry['status'] == 'completed' for entry in self.trace)

    def check(self):
        """取消、暂停及全任务预算在每个工具边界生效；不随语音代次失效。"""
        if self.should_stop and self.should_stop():
            raise TaskPause('任务已在安全点保存')
        elapsed = time.monotonic() - self.started
        if elapsed >= 180 or self.base_seconds + elapsed >= self.record['budget']['seconds']:
            raise TaskPause('执行时间预算到达，已保存进度')

    def save(self):
        """提交原子检查点并计入已用时间；崩溃时未核销阶段租约保守计入预算。"""
        elapsed = max(0, time.monotonic() - self.started)
        self.record['budget']['used_seconds'] = self.base_seconds + elapsed
        self.record['lease_seconds'] = max(0, min(180, self.record['budget']['seconds'] - self.base_seconds) - elapsed)
        self.record['checkpoint'] = snapshot(self)
        self.store.save(self.record)

    def execute(self, name, args):
        """先保存意图，再运行受控工具，最后连同草稿与结果一次落盘。"""
        self.check()
        if self.finished is not None or self.stage_calls >= 12:
            raise TaskPause('本阶段工具预算已用完')
        if self.record['budget']['used_calls'] >= self.record['budget']['calls']:
            raise TaskPause('全任务工具预算已用完')
        from src.tools.validation import validate_arguments
        allowed = {item['function']['name']: item['function']['parameters'] for item in self.schemas()}
        if name not in allowed:
            raise AgentRefused('工具不在本次授权范围')
        validate_arguments(allowed[name], args)
        self.stage_calls += 1
        self.record['budget']['used_calls'] += 1
        signature = digest({'name': name, 'arguments': args})
        cached = self.reuse.get(signature)
        if cached and name not in VOLATILE and name not in {'finish', 'plan_task', 'validate_result', 'read_wechat_history'} and all(
                key in self.artifacts and hashlib.sha256(self.artifacts[key]).hexdigest() == value
                for key, value in cached.get('artifacts', {}).items()):
            self.save()
            return self.reuse[signature]['output']
        entry = {'name': name, 'arguments': copy.deepcopy(args), 'task_id': self.task_id,
                 'tool_call_id': self.call_identity(), 'stage': self.record['stage'],
                 'queried_at': time.time(), 'status': 'started'}
        prior_artifacts = {name: hashlib.sha256(data).hexdigest() for name, data in self.artifacts.items()}
        self.trace.append(entry)
        recovery = RECOVERY.get(name, 'read-or-private-draft')
        if name in {'browser', 'desktop'} and args.get('operation') == 'observe':
            recovery = 'read-or-private-draft'
        self.record['pending'] = {'name': name, 'arguments': args, 'recovery': recovery}
        self.save()
        try:
            from .capabilities import REGISTRY, execute_extension
            if name in NAMES:
                result = execute_action(self, name, args)
            elif name in REGISTRY:
                result = execute_extension(self, name, args)
            elif name == 'plan_task':
                result = self.plan(args)
            elif name == 'read_artifact':
                offset = args['offset']
                if type(offset) is not int or offset < 0 or not args['name'].endswith('.txt'):
                    raise AgentRefused('只支持按非负偏移读取文本草稿')
                text = self.artifacts[args['name']].decode('utf-8')
                result = {'text': text[offset:offset+1200], 'length': len(text), 'offset': offset}
            else:
                result = self._execute(name, args)
            entry['status'] = 'completed'
        except ApprovalRequired:
            entry['status'] = 'awaiting_approval'
            self.save()
            raise
        except BrainCancelled:
            self.save()
            raise
        except (AgentRefused, SearchError, OSError, ValueError, KeyError, ArithmeticError, SyntaxError) as error:
            from .verification import classify_failure
            if self.record.get('effect_started', False):
                entry.update(status='uncertain', output=json.dumps({'error': str(error)}, ensure_ascii=False))
                self.save()
                raise ActionUncertain('操作已派发但回执不完整，请核对实际状态') from error
            result = {'error': str(error), 'status': 'needs_input', 'retry': classify_failure(error, name)}
            entry['status'] = 'failed'
        entry['output'] = json.dumps(result, ensure_ascii=False)
        if entry['status'] == 'completed':
            for prior in self.trace[:-1]:
                if prior['name'] == name and prior.get('status') == 'failed':
                    failure = json.loads(prior.get('output', '{}'))
                    if failure.get('retry') in {'repair_parameters', 'transient'}:
                        prior.update(status='recovered', recovered_by={'stage': entry['stage'], 'tool_call_id': entry['tool_call_id']})
        dependencies = sorted(self.materials.sources)
        if entry['status'] == 'completed':
            self.reuse[signature] = {'output': entry['output'], 'name': name, 'dependencies': dependencies}
            for artifact, data in self.artifacts.items():
                if prior_artifacts.get(artifact) != hashlib.sha256(data).hexdigest():
                    self.dependencies[artifact] = dependencies
                    self.reuse[signature].setdefault('artifacts', {})[artifact] = hashlib.sha256(data).hexdigest()
        self.record.pop('effect_started', None)
        self.record['pending'] = None
        self.save()
        if self.on_tool_result:
            self.on_tool_result(copy.deepcopy(entry))
        self.check()
        return entry['output']

    def plan(self, args):
        """保存有界步骤与明确验收字段；模型计划不成为新的文件或联网授权。"""
        steps, criteria = json.loads(args['steps_json']), json.loads(args['criteria_json'])
        if (not isinstance(steps, list) or not 1 <= len(steps) <= 20
                or any(not isinstance(s, str) or not 1 <= len(s) <= 200 for s in steps)):
            raise AgentRefused('计划须为1–20个有界步骤说明')
        if not isinstance(criteria, dict) or len(json.dumps(criteria)) > 3000:
            raise AgentRefused('验收条件须为有界JSON对象')
        from .verification import validate_criteria
        validated = validate_criteria(criteria)
        self.record['plan'] = steps
        self.record['acceptance'] = validated
        return {'saved': True, 'authorization_changed': False}

    def _stage(self, name, data, extension, append=False):
        """允许修订本任务私有草稿，先验证新内容，失败时完整保留旧版本。"""
        previous = self.artifacts.pop(name, None) if not append else None
        try:
            result = super()._stage(name, data, extension, append)
        except BaseException:
            if previous is not None:
                self.artifacts[name] = previous
            raise
        if previous is not None:
            self.record.setdefault('artifact_revisions', []).append({
                'name': name, 'prior_sha256': hashlib.sha256(previous).hexdigest(),
                'next_sha256': hashlib.sha256(data).hexdigest()})
        return result

    def _execute(self, name, args):
        """结束前核验业务产物；新表格保留数值类型，错误反馈后允许有限修订。"""
        if name == 'finish' and scope(self.record) and not self.materials.inventory():
            args = {**args, 'citations': ''}
        if name == 'write_table':
            from .office_output import xlsx_bytes
            self.record.setdefault('artifact_sources', {}).pop(args['name'], None)
            return self._stage(args['name'], xlsx_bytes(json.loads(args['rows_json'])), 'xlsx')
        if name == 'finish' and args['status'] == 'completed':
            from .verification import validate_artifacts
            verification = validate_artifacts(self)
            self.record['verification'] = verification
            if not verification['passed']:
                raise AgentRefused('验收未通过：' + '；'.join(verification['errors']))
        return super()._execute(name, args)

    def context(self):
        """重建有界工作上下文，正文仍只能按授权资料编号读取。"""
        recent = [{'name': entry['name'], 'status': entry['status'],
                   'output': entry.get('output', '')[:600]} for entry in self.trace[-3:]]
        return json.dumps({'task': self.task, 'supplement': self.record.get('notes', [])[-3:],
                           'action_scope': scope(self.record),
                           'edits': {p: {k: v[k] for k in ('status', 'before_hash', 'after_hash')} for p, v in self.record.get('edits', {}).items()},
                           'sources': self.materials.inventory(), 'plan': self.record['plan'],
                           'criteria': self.record['acceptance'],
                           'read_ranges': self.read_ranges, 'artifacts': list(self.artifacts),
                           'recent_results': recent, 'stage': self.record['stage'],
                           'recent_errors': self.record.get('stage_errors', [])[-2:],
                           'remaining_calls': self.record['budget']['calls'] - self.record['budget']['used_calls']},
                          ensure_ascii=False)


class DurableTaskRunner:
    """一次运行一个有界阶段；由 TaskManager 决定继续、暂停或等待补充。"""

    def __init__(self, store, runner=None):
        """复用现有本机模型、受控工具与原子产物发布，不安装新运行时。"""
        self.store, self.runner = store, runner or AssistantTaskRunner()

    def run_stage(self, record, should_stop=None, on_tool_result=None, publication_lock=None):
        """从已保存检查点续跑；异常与已完成工具证据分开处理。"""
        if record.get('authorization') not in {LEGACY, VERSION}:
            record.update(status='needs_input', message='授权版本不兼容，请重新发布任务')
            self.store.save(record)
            return None
        from .action_recovery import reconcile
        unresolved = reconcile(record, self.store)
        if unresolved:
            record.update(status='needs_input', message=unresolved)
            self.store.save(record)
            return None
        changed = changed_inputs(record)
        if changed:
            record.update(status='needs_input', changed_inputs=changed, message='输入已变化或缺失，请在补充资料中确认刷新')
            self.store.save(record)
            return None
        tools = DurableTools(record, self.store, should_stop, on_tool_result,
                             wechat_reader=self.runner.wechat_reader, search_client=self.runner.search_client)
        if 'fingerprints' not in record:
            try:
                record['fingerprints'] = {key: {'path': str(path), 'sha256': hashlib.sha256(read_bytes(path)).hexdigest()}
                                          for key, path in tools.materials.paths.items()}
            except (OSError, AgentRefused) as error:
                record.update(status='needs_input', message=str(error))
                tools.save()
                return None
        old_progress = progress_fingerprint(tools)
        record.update(status='running', stage=record['stage'] + 1)
        tools.save()
        reason = cloud_reason(record['request'], bool(tools.materials.inventory()))
        try:
            with serial_task():
                if reason:
                    return self.handoff(record, tools, reason, should_stop, publication_lock)
                approved = record.get('approved_action')
                if approved and approved.get('action'):
                    action = approved['action']
                    tools.execute(action['tool'], {'operation': action['operation'], 'parameters_json': json.dumps(action['parameters'], ensure_ascii=False)})
                if tools.finished is None:
                    brain = self.runner.brain
                    from .production import HarnessBrain, check_installation
                    if isinstance(brain, HarnessBrain):
                        check_installation()
                    brain.task_id = record['task_id']
                    brain.run_dir = CACHE / 'runs' / record['task_id'] / f"stage-{record['stage']:04d}"
                    brain.run_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
                    brain.current_call, brain.runtime = None, None
                    tools.call_identity = lambda: getattr(brain, 'current_call', None)
                    try:
                        brain._run(tools.context(), PERSONA + '长任务可跨阶段。先用plan_task保存步骤和验收要求，复用已保存结果。'
                                   '遇到失败修正参数或换一种已授权方法；不重复无进展调用。',
                                   tools.schemas(), tools.execute, should_stop,
                                   min(180, max(1, record['budget']['seconds'] - tools.base_seconds)), 12,
                                   domain='assistant', stop_tool='finish')
                    except Exception as error:
                        if isinstance(error, ActionUncertain) or record.get('effect_started'):
                            raise ActionUncertain(str(error)) from error
                        if record.get('action_request'):
                            raise ApprovalRequired(record['action_request']['detail']) from error
                        if should_stop and should_stop():
                            raise TaskPause('已保存进度') from error
                        if tools.finished is None:
                            record['message'] = str(error)[:500]
                            record.setdefault('stage_errors', []).append({'stage': record['stage'], 'error': str(error)[:500]})
                            record['stage_errors'] = record['stage_errors'][-10:]
                            record['failures'] += 1
                tools.check()
                if tools.finished is not None:
                    return self.publish(record, tools, should_stop, publication_lock)
                progress = progress_fingerprint(tools)
                record['stalls'] = record['stalls'] + 1 if progress == old_progress else 0
                record['status'] = 'paused' if record['stalls'] >= 2 else 'queued'
                if record['status'] == 'paused':
                    record['message'] = '连续两阶段没有新证据或产物，已暂停；可补充要求后继续' + (
                        '。最近错误：' + record['stage_errors'][-1]['error'] if record.get('stage_errors') else '')
        except ActionUncertain as error:
            record.update(status='needs_input', message=str(error))
        except ApprovalRequired as error:
            record.update(status='needs_input', message='需要确认具体操作：' + str(error))
        except TaskPause as error:
            record.update(status='paused' if (should_stop and should_stop()) else 'queued', message=str(error))
        except BrainCancelled:
            record.update(status='paused', message='任务已保存，可继续')
        finally:
            tools.save()
            record['lease_seconds'] = 0
            self.store.save(record)
        return None

    def publish(self, record, tools, should_stop, publication_lock):
        """先保存发布清单，再核对原子目录；崩溃恢复不重复追加文件。"""
        from contextlib import nullcontext
        with publication_lock if publication_lock is not None else nullcontext():
            tools.check()
            changed = changed_inputs(record)
            if changed:
                record.update(status='needs_input', changed_inputs=changed, message='交付前输入发生变化，请确认刷新')
                tools.finished = None
                return None
            tools.materials.verify()
            status, answer = tools.finished['status'], tools.finished['answer']
            if status == 'needs_input':
                record.update(status='needs_input', message=answer)
                tools.finished = None
                return None
            if status == 'deferred':
                return self.handoff(record, tools, answer, should_stop, publication_lock)
            from .verification import validate_artifacts
            verification = validate_artifacts(tools)
            if not verification['passed']:
                record.update(status='needs_input', message='验收未通过：' + '；'.join(verification['errors']))
                tools.finished = None
                return None
            record['verification'] = verification
            if status == 'completed' and tools.coverage_partial():
                status = 'partial'
                answer += '\n范围说明：仅依据已读取内容或微信可见快照，不代表完整历史。'
            unresolved = [entry for entry in tools.trace if entry.get('status') == 'failed']
            if unresolved:
                status = 'partial'
                details = [entry['name'] + '：' + str(json.loads(entry.get('output', '{}')).get('error', '执行失败'))[:200]
                           for entry in unresolved[-5:]]
                answer += '\n未解决的工具错误（保留已生成结果）：\n' + '\n'.join(details)
            files = dict(tools.artifacts)
            report = 'J.A.C. 任务报告\n状态：' + status + '\n任务：' + record['request'] + '\n\n' + answer
            evidence = {'task_id': record['task_id'], 'model': MODEL, 'tools': tools.trace,
                        'sources': tools.materials.sources, 'read_ranges': tools.read_ranges,
                        'plan': record['plan'], 'acceptance': record['acceptance'], 'verification': verification, 'budget': record['budget']}
            files.update({'report.txt': report.encode('utf-8'),
                          'evidence.json': json.dumps(evidence, ensure_ascii=False, indent=2).encode('utf-8')})
            return self.commit_files(record, files, status, answer, tools, should_stop, publication_lock)

    def commit_files(self, record, files, status, answer, tools, should_stop, publication_lock):
        """用内容哈希确认既有交付；无法确认的目录保持原样并明确拒绝。"""
        target = self.runner.output_dir / record['task_id']
        # 已发布证据含之前的计时，恢复时按当时保存清单核验，而非重建后覆盖。
        manifest = record.get('publication')
        if target.exists():
            if not manifest or set(p.name for p in target.iterdir()) != set(manifest['files']) or any(hashlib.sha256(read_bytes(target / name, limit=128 * 1024 * 1024)).hexdigest() != sha
                                   for name, sha in manifest['files'].items()):
                raise AgentRefused('已存在结果与发布清单不符，未覆盖或重复发布')
            status, answer = manifest['status'], manifest['answer']
            path = target / 'report.txt'
        else:
            record['publication'] = {'files': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
                                     'status': status, 'answer': answer}
            record['status'] = 'publishing'
            tools.save()
            path = self.runner._publish(record['task_id'], files, should_stop, publication_lock)
        record.update(status=status, answer=answer, report=str(path), message='',
                      artifacts=list(tools.artifacts), notification='pending')
        tools.save()
        return TaskResult(record['task_id'], path, answer, tools.trace, record['budget']['used_seconds'], status)

    def handoff(self, record, tools, reason, should_stop, publication_lock):
        """只生成未提交的本地云端交接单，不发送附件或手机消息。"""
        from .cloud import CloudTask
        import re
        handoff = CloudTask(record['task_id'], record['request'], reason, record['source'],
                            delivery={'channel': 'phone' if re.search('手机', record['request']) else 'local', 'recipient': None})
        receipt = self.runner.cloud.submit(handoff, should_stop)
        if receipt.status != 'deferred' or receipt.task_id != record['task_id']:
            raise AgentRefused('真实云端适配器仍待接入验证')
        answer = 'bo s s，OpenClaw 尚未接入，任务未提交、手机未发送；本地交接单已保存。'
        files = {'report.txt': answer.encode('utf-8'),
                 'handoff.json': json.dumps(handoff.payload(), ensure_ascii=False, indent=2).encode('utf-8')}
        return self.commit_files(record, files, 'deferred', answer, tools, should_stop, publication_lock)
