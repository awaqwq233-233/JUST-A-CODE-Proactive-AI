"""开放任务新增能力的唯一分派、恢复分类和一次性具体动作确认。"""
import copy
import json
import time

from src.brain.lm_studio import BrainCancelled
from .action_policy import scope
from .assistant import schema, text_spec
from .boundary import AgentRefused
from .task_store import digest

NAMES = {'workspace_read', 'stage_edit', 'patch_edit', 'commit_edits', 'rollback_edits', 'workspace_run_code', 'browser', 'desktop'}
VOLATILE = NAMES | {'validate_result'}
RECOVERY = {'workspace_read': 'read-or-private-draft', 'stage_edit': 'read-or-private-draft', 'patch_edit': 'read-or-private-draft',
            'commit_edits': 'file-transaction', 'rollback_edits': 'file-transaction',
            'workspace_run_code': 'isolated-execution', 'browser': 'external-ui', 'desktop': 'external-ui'}


class ApprovalRequired(BrainCancelled):
    """具体动作尚未执行，等待界面确认；模型不能自行批准。"""


class ActionUncertain(BrainCancelled):
    """已派发的外部动作没有完整回执，先核对而非重放。"""


def project_ui(state, offset=0):
    """模型只接收分页控件摘要，完整观察保留在任务中供派发前复核。"""
    if type(offset) is not int or offset < 0:
        raise AgentRefused('控件分页偏移须为非负整数')
    result = {k: state[k] for k in ('snapshot', 'app', 'url', 'title') if k in state}
    fields = {'target', 'role', 'name', 'description', 'value', 'tag', 'type', 'href', 'password', 'secure', 'disabled'}
    elements = state.get('elements', [])
    result['elements'] = [{k: v for k, v in item.items() if k in fields and v not in ('', False)}
                          for item in elements[offset:offset+12]]
    if 'text' in state:
        result['text'] = state['text'][:1200]
    result['total_elements'] = len(elements)
    if offset+12 < len(elements):
        result['next_offset'] = offset+12
    if state.get('blocked_requests'):
        result['blocked_requests'] = state['blocked_requests'][:3]
    return result


def require_approval(tools, action, detail, preview=None):
    """确认绑定任务、精确参数和观察版本，五分钟后过期且只消费一次。"""
    identity = digest(action)
    approved = tools.record.get('approved_action')
    if approved and approved['digest'] == identity and time.time() < approved['expires_at']:
        tools.record.pop('approved_action')
        tools.record.pop('action_request', None)
        tools.save()
        return
    tools.record['action_request'] = {'digest': identity, 'action': copy.deepcopy(action),
                                       'detail': detail, 'created_at': time.time()}
    if preview:
        tools.record['action_request']['preview'] = preview
    tools.record.update(status='needs_input', message='需要确认具体操作：' + detail)
    tools.record['pending'] = None
    tools.save()
    raise ApprovalRequired(detail)


def action_schemas(record):
    """按用户明确选择装入工具，控制 schema 大小以适配实际8192上下文。"""
    selected = scope(record)
    specs = []
    if selected.get('workspace'):
        specs.append(schema('workspace_read', '读取工作目录内UTF-8文本，path为空列根目录；offset为字符偏移。返回文件版本。',
                            {'path': text_spec(), 'offset': {'type': 'integer'}}))
    if selected.get('edit'):
        specs += [schema('stage_edit', '暂存完整文件新内容及差异；先读取获得expected_sha256，新文件为missing。保留UTF-8 BOM和CRLF。',
                         {'path': text_spec(), 'expected_sha256': text_spec(), 'content': text_spec()}),
                  schema('patch_edit', '暂存唯一原文片段替换，保留其他内容；expected_sha256为实际读取版本。',
                         {'path': text_spec(), 'expected_sha256': text_spec(), 'old': text_spec(), 'new': text_spec()}),
                  schema('commit_edits', '提交暂存文件并核验，保留恢复副本；多文件为逐个提交。', {}),
                  schema('rollback_edits', '按写入后版本回滚本次修改，用户后续编辑会阻止回滚。', {})]
    if selected.get('runtimes'):
        specs.append(schema('workspace_run_code', '运行已存在的脚本文件，示例：runtime="python",script_path="check.py",arguments=[]。script_path是相对文件名，不能填写代码。禁网临时容器60秒；返回退出码/stdout/stderr，临时文件不写回原目录。',
                            {'runtime': {'type': 'string', 'enum': selected['runtimes']},
                             'script_path': text_spec(), 'arguments': {'type': 'array', 'items': {'type': 'string'}}}))
    if selected.get('origins'):
        specs.append(schema('browser', '操作专用浏览器。operation为open/observe/click/fill/select/press/scroll/verify；parameters_json严格JSON。open:{url};observe:{offset:0}按next_offset翻控件页；其余须snapshot和target，fill加text，select加value，press加key，scroll加dy；verify:{expected}核验页面包含结果文字。操作后须verify才可完成。只使用观察返回的编号。',
                            {'operation': text_spec(), 'parameters_json': text_spec()}))
    if selected.get('apps'):
        specs.append(schema('desktop', '操作授权应用。operation为observe/click/double_click/fill/press/scroll/drag/verify/locate/click_candidate；parameters_json含app。observe含app及可选offset，按next_offset翻控件页；其余含snapshot,target；fill加text，press加key，scroll加dy，drag加destination编号；verify:{app,expected}核验结果文字。locate:{app,description}截图定位；click_candidate:{app,candidate}点击候选须用户确认。操作后须verify。',
                            {'operation': text_spec(), 'parameters_json': text_spec()}))
    return specs


def execute_action(tools, name, args):
    """分派程序注册实现，任何操作都不能新增工具或调用宿主shell。"""
    if name in {'workspace_read', 'stage_edit', 'patch_edit', 'commit_edits', 'rollback_edits'}:
        from .workspace_tools import WorkspaceTools
        workspace = WorkspaceTools(tools)
        if name == 'workspace_read':
            return workspace.read(**args)
        if name == 'stage_edit':
            return workspace.stage(**args)
        if name == 'patch_edit':
            return workspace.patch(**args)
        return workspace.commit(rollback=name == 'rollback_edits')
    if name == 'workspace_run_code':
        from .sandbox_tools import execute_code
        return execute_code(tools, **args)
    if name == 'browser':
        from .browser_tools import execute_browser
        return execute_browser(tools, args['operation'], json.loads(args['parameters_json']))
    if name == 'desktop':
        from .desktop_tools import execute_desktop
        return execute_desktop(tools, args['operation'], json.loads(args['parameters_json']))
    raise AgentRefused('操作工具未注册')


def verify_actions(tools):
    """交付前核验实际文件版本和未解决动作，不把模型终态当执行证明。"""
    from .workspace_tools import parent_fd, read_at, fingerprint
    errors, checks = [], []
    for path, item in tools.record.get('edits', {}).items():
        if item['status'] not in {'committed', 'rolled_back'}:
            errors.append('文件修改未完成：' + path)
            continue
        with parent_fd(tools.record, path) as (fd, name):
            actual = fingerprint(read_at(fd, name)[0])
        expected = item['after_hash'] if item['status'] == 'committed' else item['before_hash']
        if actual != expected:
            errors.append('已修改文件后来发生变化：' + path)
        checks.append({'file': path, 'sha256': actual, 'state': item['status']})
    if tools.record.get('action_request'):
        errors.append('存在尚未确认的操作')
    if tools.record.get('sandbox_pending'):
        errors.append('代码容器尚未确认回收')
    if any(tools.record.get('ui_unverified', {}).values()):
        errors.append('界面操作尚未通过verify核验实际结果')
    executions = tools.record.get('executions', [])
    if executions and executions[-1]['exit_code'] != 0:
        errors.append('最后一次代码执行未成功，请修复或说明部分结果')
    if executions and executions[-1]['exit_code'] == 0:
        import hashlib
        from .sandbox_tools import archive_workspace
        if hashlib.sha256(archive_workspace(tools.record)[0]).hexdigest() != executions[-1]['input_sha256']:
            errors.append('工作目录已在代码验证后改变，需要重新运行验证')
    return errors, checks
