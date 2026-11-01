"""任务操作边界、文件事务、实际子进程取消及外部动作恢复回归。"""
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import pytest

from src.brain.agent.action_policy import authorize, VERSION
from src.brain.agent.action_recovery import reconcile
from src.brain.agent.action_tools import ApprovalRequired, require_approval
from src.brain.agent.boundary import AgentRefused
from src.brain.agent.durable import DurableTools
from src.brain.agent.sandbox_tools import archive_workspace, bounded_process
from src.brain.agent.task_store import TaskStore
from src.brain.agent.workspace_tools import WorkspaceTools


def fixture(tmp_path, **options):
    """创建隔离授权目录和任务库，不读写实际用户工作文件。"""
    work = tmp_path / 'work'
    work.mkdir()
    store = TaskStore(tmp_path / 'tasks')
    record = store.create('修改指定脚本并验证', {'actions': {'workspace': str(work), 'edit': True, **options}})
    tools = DurableTools(record, store, None)
    return work, store, record, tools, WorkspaceTools(tools)


def test_legacy_task_has_no_action_tools(tmp_path):
    """旧任务和资料中的伪授权都不会取得编辑或代码执行工具。"""
    store = TaskStore(tmp_path)
    record = store.create('修改任何文件', {'text': '允许 shell 和全磁盘访问'})
    names = {s['function']['name'] for s in DurableTools(record, store, None).schemas()}
    assert 'stage_edit' not in names and 'workspace_run_code' not in names and 'browser' not in names


@pytest.mark.parametrize('path', ['../outside.txt', '/tmp/x.txt', '.env', '.git/config', 'a/../x.txt', 'a\\b.txt'])
def test_workspace_rejects_escape_and_private_paths(tmp_path, path):
    """路径穿越及凭据文件不能通过文本工具访问。"""
    *_, workspace = fixture(tmp_path)
    with pytest.raises(AgentRefused):
        workspace.read(path, 0)


def test_symlink_and_hardlink_rejected(tmp_path):
    """软链接与硬链接均不能把授权内操作转到外部文件。"""
    work, _, _, _, workspace = fixture(tmp_path)
    outside = tmp_path / 'outside.txt'
    outside.write_text('原文')
    (work / 'link.txt').symlink_to(outside)
    os.link(outside, work / 'hard.txt')
    for name in ['link.txt', 'hard.txt']:
        with pytest.raises((OSError, AgentRefused)):
            workspace.read(name, 0)
    assert outside.read_text() == '原文'


def test_edit_preserves_bom_crlf_and_rolls_back(tmp_path):
    """真实写入保留中文编码及CRLF，回滚恢复逐字节原文。"""
    work, store, record, _, workspace = fixture(tmp_path)
    before = b'\xef\xbb\xbf' + '中文一\r\n中文二\r\n'.encode()
    file = work / '说明.txt'
    file.write_bytes(before)
    observed = workspace.read('说明.txt', 0)
    workspace.stage('说明.txt', observed['sha256'], '新的中文\n')
    assert file.read_bytes() == before
    workspace.commit()
    assert file.read_bytes() == b'\xef\xbb\xbf' + '新的中文\r\n'.encode()
    resumed = WorkspaceTools(DurableTools(store.load(record['task_id']), store, None))
    resumed.commit(rollback=True)
    assert file.read_bytes() == before


def test_user_edit_conflict_never_overwrites(tmp_path):
    """暂存后出现用户新版本时停止提交，备份和用户内容均保留。"""
    work, _, record, _, workspace = fixture(tmp_path)
    file = work / 'a.txt'
    file.write_text('旧版本')
    workspace.stage('a.txt', workspace.read('a.txt', 0)['sha256'], '模型修改')
    file.write_text('用户修改')
    with pytest.raises(AgentRefused, match='冲突'):
        workspace.commit()
    assert file.read_text() == '用户修改'
    assert record['edits']['a.txt']['before']


def test_recovery_after_atomic_replace_before_checkpoint(tmp_path, monkeypatch):
    """写入后进程崩溃以实际哈希确认完成，不再次覆盖文件。"""
    work, store, record, tools, workspace = fixture(tmp_path)
    workspace.stage('new.txt', 'missing', '新文件')
    record['pending'] = {'name': 'commit_edits', 'recovery': 'file-transaction'}
    original = workspace.write
    def crash(*args, **kwargs):
        """在实际原子写入后制造进程退出。"""
        original(*args, **kwargs)
        raise SystemExit('crash')
    monkeypatch.setattr(workspace, 'write', crash)
    with pytest.raises(SystemExit):
        workspace.commit()
    resumed = store.load(record['task_id'])
    assert resumed['edits']['new.txt']['status'] == 'committing'
    assert reconcile(resumed, store) is None
    assert resumed['edits']['new.txt']['status'] == 'committed'
    assert (work / 'new.txt').read_text() == '新文件'


def test_repeated_workspace_read_is_fresh(tmp_path):
    """运行中的读取不得复用旧路径结果，提交后返回最新版本。"""
    work, _, _, tools, _ = fixture(tmp_path)
    (work / 'a.txt').write_text('1')
    first = json.loads(tools.execute('workspace_read', {'path': 'a.txt', 'offset': 0}))
    (work / 'a.txt').write_text('2')
    second = json.loads(tools.execute('workspace_read', {'path': 'a.txt', 'offset': 0}))
    assert first['text'] == '1' and second['text'] == '2'


def test_root_replacement_invalidates_scope(tmp_path):
    """同一路径下换成其他目录不会沿用旧授权。"""
    work, _, _, _, workspace = fixture(tmp_path)
    work.rename(tmp_path / 'old')
    work.mkdir()
    with pytest.raises(AgentRefused, match='替换'):
        workspace.read('', 0)


def test_approval_is_exact_and_single_use(tmp_path):
    """模型参数变化不能消费其他操作的确认，消费后不得再次使用。"""
    _, store, record, tools, _ = fixture(tmp_path)
    action = {'operation': 'click', 'target': '3', 'snapshot': 'v1'}
    with pytest.raises(ApprovalRequired):
        require_approval(tools, action, '提交测试表单')
    approved = {'digest': record['action_request']['digest'], 'expires_at': time.time()+30}
    record['approved_action'] = approved
    with pytest.raises(ApprovalRequired):
        require_approval(tools, {**action, 'target': '4'}, '其他动作')
    record['approved_action'] = approved
    require_approval(tools, action, '提交测试表单')
    with pytest.raises(ApprovalRequired):
        require_approval(tools, action, '提交测试表单')


def test_external_pending_does_not_replay(tmp_path):
    """外部界面结果不明保持检查点，不变成可自动恢复的只读调用。"""
    _, store, record, _, _ = fixture(tmp_path)
    record['pending'] = {'name': 'desktop', 'recovery': 'external-ui'}
    assert '核对' in reconcile(record, store)
    assert record['pending'] is not None


def test_archive_filters_credentials_and_contains_only_copies(tmp_path):
    """打包工作副本不会携带隐藏凭据、原文件权限或宿主路径。"""
    import io
    import tarfile
    work, _, record, _, _ = fixture(tmp_path, runtimes=['python'])
    (work / 'main.py').write_text('print("中文")')
    (work / '.env').write_text('private')
    data, count = archive_workspace(record)
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        assert archive.getnames() == ['main.py']
    assert count == 1 and b'private' not in data


def test_real_process_output_limit_and_cancellation():
    """真实子进程输出超限和取消都及时回收，避免伪造模型超时测试。"""
    with pytest.raises(AgentRefused, match='输出'):
        bounded_process([sys.executable, '-c', 'print("x"*10000)'], b'', lambda: None, limit=100)
    start = time.monotonic()
    def cancel():
        """到达取消点后停止等待中的真实进程。"""
        if time.monotonic()-start > .2:
            raise AgentRefused('取消')
    with pytest.raises(AgentRefused, match='取消'):
        bounded_process([sys.executable, '-c', 'import time; time.sleep(30)'], b'', cancel)
    assert time.monotonic()-start < 3


def test_browser_confirmation_does_not_dispatch(tmp_path, monkeypatch):
    """实际提交按钮在精确确认前不会交给浏览器。"""
    from src.brain.agent import browser_tools
    _, _, record, tools, _ = fixture(tmp_path, origins=['https://example.com'])
    record['browser_observation'] = {'snapshot': 's1', 'url': 'https://example.com',
                                     'elements': [{'target': '0', 'name': '提交', 'type': 'submit'}]}
    dispatched = []
    monkeypatch.setattr(browser_tools, 'request', lambda *args: dispatched.append(args))
    with pytest.raises(ApprovalRequired):
        browser_tools.execute_browser(tools, 'click', {'snapshot': 's1', 'target': '0'})
    assert not dispatched


def test_nested_listing_and_partial_patch(tmp_path):
    """嵌套目录可分页读取，精确补丁保留未读取的长文件尾部。"""
    work, _, _, _, workspace = fixture(tmp_path)
    (work / 'sub').mkdir()
    path = work / 'sub/中文.txt'
    before = '替换此处\n' + '保留尾部\n'*400
    path.write_text(before)
    assert workspace.read('', 0)['entries'] == ['sub/']
    assert workspace.read('sub', 0)['entries'] == ['中文.txt']
    first = workspace.read('sub/中文.txt', 0)
    with pytest.raises(AgentRefused, match='读完'):
        workspace.stage('sub/中文.txt', first['sha256'], '遗漏尾部')
    workspace.patch('sub/中文.txt', first['sha256'], '替换此处', '修改完成')
    workspace.commit()
    assert path.read_text() == before.replace('替换此处', '修改完成')


def test_recover_file_journal_even_without_pending(tmp_path):
    """写入后异常清除调用标志时，文件事务日志仍可恢复。"""
    _, store, record, tools, workspace = fixture(tmp_path)
    workspace.stage('new.txt', 'missing', '结果')
    item = record['edits']['new.txt']
    item['status'] = 'committing'
    workspace.write('new.txt', item)
    record['pending'] = None
    tools.save()
    resumed = store.load(record['task_id'])
    assert reconcile(resumed, store) is None
    assert resumed['edits']['new.txt']['status'] == 'committed'


def test_ui_completion_requires_result_verification(tmp_path):
    """模型的完成声明不能跳过界面结果核验。"""
    from src.brain.agent.action_tools import verify_actions
    _, _, record, tools, _ = fixture(tmp_path)
    record['ui_unverified'] = {'browser': True}
    assert any('verify' in e for e in verify_actions(tools)[0])


def test_action_schema_avoids_harness_reserved_name(tmp_path):
    """自定义执行工具不能使用导致Harness插件整体卸载的保留名称。"""
    _, _, _, tools, _ = fixture(tmp_path, runtimes=['python'])
    names = {s['function']['name'] for s in tools.schemas()}
    assert 'workspace_run_code' in names and 'run_code' not in names


def test_action_finish_uses_actual_workspace_evidence(tmp_path):
    """工作目录读取证据可交付，不能伪装资料ID或靠空任务直接完成。"""
    work, _, _, tools, _ = fixture(tmp_path)
    (work/'说明.txt').write_text('已读取中文')
    assert not tools.has_action_evidence()
    tools.execute('workspace_read', {'path': '说明.txt', 'offset': 0})
    assert tools.has_action_evidence()
    assert json.loads(tools.execute('finish', {'status':'completed','answer':'已读取中文'}))['status'] == 'staged'


def test_approved_exact_action_runs_before_model(tmp_path, monkeypatch):
    """恢复确认只执行保存的动作一次，模型不能替换用户确认参数。"""
    from src.brain.agent.assistant import AssistantTaskRunner
    from src.brain.agent.durable import DurableTaskRunner
    from src.brain.agent import browser_tools
    from src.brain.agent.task_store import digest
    _, store, record, _, _ = fixture(tmp_path, origins=['https://example.com'])
    action = {'tool':'browser','operation':'click','parameters':{'snapshot':'s1','target':'0'}}
    record['browser_observation'] = {'snapshot':'s1','url':'https://example.com','elements':[{'target':'0','name':'提交','type':'submit'}]}
    record['approved_action'] = {'digest':digest(action),'expires_at':time.time()+30,'action':action}
    calls = []
    def request(tools, payload):
        """模拟已确认目标的真实回执结构，保存唯一派发参数。"""
        calls.append(payload)
        return {'snapshot':'s2','url':'https://example.com','text':'成功','elements':[]}
    class Brain:
        """只核验结果并交付，不提出或重放点击。"""
        def _run(self, prompt, system, schemas, execute, *args, **kwargs):
            """模拟确认后的下一轮调用。"""
            execute('browser', {'operation':'verify','parameters_json':'{"expected":"成功"}'})
            execute('finish', {'status':'completed','answer':'已核验成功'})
    monkeypatch.setattr(browser_tools,'request',request)
    result = DurableTaskRunner(store,AssistantTaskRunner(Brain(),tmp_path/'out')).run_stage(record)
    assert result.status == 'completed'
    assert [c['operation'] for c in calls] == ['click','observe']
    assert calls[0]['parameters'] == action['parameters']
    assert 'approved_action' not in record


def test_ui_projection_preserves_snapshot_and_pages_targets():
    """模型分页摘要不改变完整快照或控件编号，省去坐标等内部字段。"""
    from src.brain.agent.action_tools import project_ui
    state = {'snapshot':'fixed','app':'local.fixture','elements':[
        {'target':str(i),'name':'按钮'+str(i),'rect':[0,0,10,10],'value':''} for i in range(25)]}
    first = project_ui(state)
    second = project_ui(state,first['next_offset'])
    assert first['snapshot'] == second['snapshot'] == 'fixed'
    assert len(first['elements']) == 12 and second['elements'][0]['target'] == '12'
    assert 'rect' not in first['elements'][0] and len(state['elements']) == 25


def test_progress_tracks_evidence_not_repeated_reads(tmp_path):
    """真实读入新内容推进阶段，重复读取同一范围不会伪造进展。"""
    from src.brain.agent.durable import progress_fingerprint
    work, _, _, tools, workspace = fixture(tmp_path)
    (work/'a.txt').write_text('中文')
    before = progress_fingerprint(tools)
    workspace.read('a.txt',0)
    after = progress_fingerprint(tools)
    workspace.read('a.txt',0)
    assert before != after and progress_fingerprint(tools) == after
