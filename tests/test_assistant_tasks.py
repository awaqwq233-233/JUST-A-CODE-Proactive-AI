"""开放任务的真实边界、产物、云端占位与生产语音接线回归。"""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import threading
import time
import zipfile

import numpy as np
import pytest

from src.brain.agent.assistant import AssistantTaskRunner, AssistantTools
from src.brain.agent.boundary import AgentRefused
from src.brain.agent.materials import Materials, workbook_bytes, decode_text
from src.brain.agent.production import HarnessBrain
from src.brain.agent.profile import ROOT, MODEL
from src.brain.agent.routing import assistant_instruction, cloud_reason
from src.brain.lm_studio import BrainCancelled
from src.omni.task_pipeline import TaskPipeline, Utterance, route_task, route_instruction
from src.omni.task_speech import speech_summary
from src.tools.wechat import read_wechat


class ScriptedBrain:
    """用可核对的工具序列代替模型；不代替工具或发布边界。"""

    def __init__(self, calls):
        """保存本次明确的工具序列。"""
        self.calls, self.runtime = calls, None

    def _run(self, prompt, system, tools, execute, should_stop, *args, **kwargs):
        """逐项调用真实边界，保留完成状态。"""
        for name, arguments in self.calls:
            execute(name, arguments)
        self.runtime = {'finish_reason': 'blocked', 'steps': len(self.calls)}


def finish(answer='已整理资料。', citations='T1', status='completed'):
    """构造结束提议，测试仍由真实边界验证。"""
    return ('finish', {'answer': answer, 'citations': citations, 'status': status})


@pytest.mark.parametrize('text', ['你好', '他说帮我删除文件', '不要总结微信消息', '比如比较甲和乙', '假设帮我读取文件'])
def test_non_task_does_not_escalate(text):
    """否定、举例和转述不会取得开放任务权限。"""
    assert assistant_instruction(text) is None


def test_route_preserves_old_exact_scope_and_general_domain():
    """明确单步仍走原路径，复杂指令独立升级，不借旧权限执行。"""
    assert route_task('查一下电池电量') == ('battery',)
    assert route_instruction('比较两个文件的差异') is None
    assert route_task('比较两个文件的差异')[0] == 'assistant'
    assert cloud_reason('对比甲乙哪个好')
    assert cloud_reason('总结微信某群，再制作Excel表格')
    assert cloud_reason('总结项目群里的消息，再制作Excel表格')
    assert cloud_reason('比较两份文件的差异', True) is None


def test_file_scope_symlink_encoding_and_zip_limits(tmp_path):
    """普通中文可读；符号链接与压缩膨胀被拒绝，不静默损坏编码。"""
    source = tmp_path / '中文.txt'
    source.write_bytes('中文内容'.encode('gb18030'))
    materials = Materials(files=[source])
    assert materials.read('F1')['text'] == '中文内容'
    link = tmp_path / 'link.txt'
    link.symlink_to(source)
    with pytest.raises(OSError):
        Materials(files=[link]).read('F1')
    with pytest.raises(AgentRefused):
        materials.read('../../secret')
    compressed = io.BytesIO()
    with zipfile.ZipFile(compressed, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('word/document.xml', 'x' * 17000000)
    with pytest.raises(AgentRefused, match='解压'):
        decode_text(compressed.getvalue(), '.docx')


def test_xlsx_unicode_is_literal_not_formula():
    """Excel 中文和公式形文本逐字保留，文件中没有可执行公式节点。"""
    data = workbook_bytes([['负责人', '任务'], ['小王', '=HYPERLINK("https://evil.invalid")']])
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        sheet = archive.read('xl/worksheets/sheet1.xml').decode()
        assert '<f>' not in sheet and 'inlineStr' in sheet
    text = decode_text(data, '.xlsx')
    assert '小王' in text and '=HYPERLINK' in text


def test_calculator_cannot_execute_code_or_unbounded_power():
    """纯算术有真实数值反馈，拒绝名称、调用、非有限结果及巨大指数。"""
    from src.tools.calculator import calculate
    assert calculate('(1200-1000)/1000*100')['result'] == 20
    for value in ['__import__("os").system("echo bad")', '10**10000000', '1e999', 'True+1']:
        with pytest.raises((ValueError, OverflowError)):
            calculate(value)


def test_completion_requires_actual_reads_and_artifacts():
    """模型说已完成不能代替文件读取或表格产物。"""
    tools = AssistantTools('总结并制作Excel表格', Materials(text='任务甲'))
    result = json.loads(tools.execute(*finish()))
    assert result['status'] == 'needs_input' and tools.finished is None
    assert not tools.artifacts
    missing = AssistantTools('总结项目群里的消息', Materials())
    result = json.loads(missing.execute(*finish(citations='')))
    assert result['status'] == 'needs_input' and missing.finished is None
    missing.execute(*finish(answer='请明确应用及完整会话名。', citations='', status='needs_input'))
    assert missing.finished['status'] == 'needs_input'


def test_general_multistep_report_table_and_evidence(tmp_path):
    """本地多工具实际生成XLSX、报告和资料哈希，原始范围可追溯。"""
    brain = ScriptedBrain([('read_source', {'source': 'T1', 'offset': 0}),
        ('write_table', {'name': '清单.xlsx', 'rows_json': '[["任务","负责人"],["设计","小王"]]'}), finish('bo s s，表格已生成。')])
    result = AssistantTaskRunner(brain, tmp_path).run('整理资料并生成表格', inputs={'text': '设计由小王完成'})
    assert result.status == 'completed'
    assert (result.path.parent / '清单.xlsx').is_file()
    data = json.loads((result.path.parent / 'evidence.json').read_text())
    assert data['sources']['T1']['sha256'] and data['read_ranges']['T1'] == [[0, 7]]
    assert speech_summary(result, ('assistant',)) == 'bo s s，表格已生成。'


def test_partial_read_is_not_whole_document(tmp_path):
    """只读首段时报告标记partial，长答案不截断朗读成完整答复。"""
    brain = ScriptedBrain([('read_source', {'source': 'T1', 'offset': 0}), finish('分析。' * 100)])
    result = AssistantTaskRunner(brain, tmp_path).run('总结资料', inputs={'text': '甲' * 1700})
    assert result.status == 'partial' and '片段' in result.answer
    assert '请打开最新报告' in speech_summary(result, ('assistant',))


def test_wechat_explicit_identity_and_read_scope(tmp_path):
    """精确会话和实际消息快照留证；图片占位和完整历史不被冒充。"""
    seen = []
    def reader(chat, task, should_stop):
        """提供固定已加载消息用于纯离线边界验证。"""
        seen.append(chat)
        return {'chat': chat, 'messages': [{'text': '测试消息'}, {'text': '图片'}],
                'scope': '已加载消息', 'complete_history': False, 'captured_at': 1}
    brain = ScriptedBrain([('read_wechat', {'chat': '测试群'}), finish('当前可见测试消息和图片占位。', 'S1')])
    result = AssistantTaskRunner(brain, tmp_path, wechat_reader=reader).run('总结微信测试群里的消息')
    assert seen == ['测试群'] and result.status == 'partial'
    with pytest.raises(AgentRefused):
        read_wechat('另一个群', '总结微信测试群里的消息')
    with pytest.raises(AgentRefused, match='截短'):
        read_wechat('甲的项目', '总结微信甲的项目讨论组的已加载消息')
    with pytest.raises(AgentRefused, match='完整会话范围'):
        read_wechat('测试群', '对比微信测试群和另一群里的消息')
    specs = AssistantTools('总结微信甲的项目讨论组的已加载消息', Materials()).schemas()
    chat = next(s for s in specs if s['function']['name'] == 'read_wechat')
    assert chat['function']['parameters']['properties']['chat']['enum'] == ['甲的项目讨论组']


@pytest.mark.parametrize('cancelled', [False, True])
def test_wechat_native_launch_and_cancel_marker(tmp_path, monkeypatch, cancelled):
    """正常读取清理私有请求；启动器超时退出不能提前删除辅助程序的取消标记。"""
    import src.tools.wechat as native
    commands = []
    class Launcher:
        """模拟LaunchServices启动器与辅助程序独立退出。"""
        def __init__(self, command, **kwargs):
            """保存真实命令，正常情况返回有界JSON。"""
            commands.append(command)
            self.output = Path(command[command.index('--output') + 1])
            self.terminated = False
            if not cancelled:
                self.output.write_text('{"accessibility": true}')
        def poll(self):
            """启动器尚未退出。"""
            return None
        def wait(self, timeout):
            """取消时先模拟辅助程序仍未结束。"""
            if cancelled and not self.terminated:
                raise native.subprocess.TimeoutExpired('open', timeout)
            return 0
        def terminate(self):
            """只结束启动器，不能据此推断辅助程序已结束。"""
            self.terminated = True
    monkeypatch.setattr(native, 'ROOT', tmp_path)
    monkeypatch.setattr(native.subprocess, 'Popen', Launcher)
    if cancelled:
        with pytest.raises(BrainCancelled):
            native.native_request(['--read', '测试群'], lambda: True)
        assert list(tmp_path.rglob('response.json.cancel'))
    else:
        assert native.native_request(['--check']) == {'accessibility': True}
        assert not list(tmp_path.rglob('read-*'))
    assert commands[0][:5] == ['/usr/bin/open', '-n', '-g', '-W', '-a']


def test_cloud_task_never_reads_or_uploads_private_sources(tmp_path):
    """明确云端任务只生成未提交交接单，没有伪造执行或手机送达。"""
    result = AssistantTaskRunner(ScriptedBrain([]), tmp_path).run('对比甲乙哪个好并发到手机上', inputs={'text': '不应上传的私有正文'})
    data = json.loads((result.path.parent / 'handoff.json').read_text())
    assert result.status == 'deferred' and data['attachments'] == []
    assert '不应上传' not in json.dumps(data, ensure_ascii=False)
    assert data['delivery']['recipient'] is None
    assert '未发送' in speech_summary(result, ('assistant',))


def test_cancellation_and_changed_source_prevent_publication(tmp_path):
    """取消或输入变动均不得交付成功文件。"""
    stop = threading.Event()
    stop.set()
    with pytest.raises(BrainCancelled):
        AssistantTaskRunner(ScriptedBrain([]), tmp_path / 'out').run('总结资料', should_stop=stop.is_set)
    source = tmp_path / 'input.txt'
    source.write_text('旧版')
    def on_tool(entry):
        """工具实际读取后改变输入，模拟编辑器同时保存。"""
        if entry['name'] == 'read_source':
            source.write_text('新版')
    brain = ScriptedBrain([('read_source', {'source': 'F1', 'offset': 0}), finish(citations='F1')])
    with pytest.raises(AgentRefused, match='改变'):
        AssistantTaskRunner(brain, tmp_path / 'out').run('总结文件', inputs={'files': [source]}, on_tool_result=on_tool)
    assert not (tmp_path / 'out').exists()


def test_tool_scope_cannot_inject_paths_or_exfiltrate():
    """资料存在时不装入公网查询；文件名不能越出产物目录。"""
    tools = AssistantTools('搜索并总结这段资料', Materials(text='私有资料'))
    with pytest.raises(AgentRefused):
        tools.execute('web_search', {'query': '私有资料'})
    result = json.loads(tools.execute('write_text', {'name': '../escape.txt', 'content': '内容'}))
    assert result['status'] == 'needs_input' and not tools.artifacts


def test_public_search_can_chain_without_acquiring_private_scope():
    """第一轮公开搜索的证据不是新增私有资料，允许预算内继续搜索。"""
    queries = []
    class Search:
        """只返回测试公开结果，不访问网络。"""
        def search(self, query, should_stop=None):
            """记录独立关键词以验证多轮工具组合。"""
            queries.append(query)
            return {'results': [{'title': query, 'text': '公开测试结果'}]}
    tools = AssistantTools('联网搜索并整理公开资料', Materials(), search_client=Search())
    for query in ['第一主题', '第二主题', '第三主题']:
        assert 'error' not in json.loads(tools.execute('web_search', {'query': query}))
    assert len(queries) == 3
    assert json.loads(tools.execute('web_search', {'query': '第四主题'}))['status'] == 'needs_input'


@contextmanager
def scripted_model(calls):
    """向真实官方SDK提供可重复的本机SSE模型回合，不请求外网。"""
    requests = []
    class Handler(BaseHTTPRequestHandler):
        """仅提供测试Chat Completions响应。"""
        def log_message(self, *args):
            """不记录测试正文。"""
        def do_POST(self):
            """顺序返回带唯一ID的工具提议。"""
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(body)
            name, arguments = calls[len(requests) - 1]
            delta = {'tool_calls': [{'index': 0, 'id': f'call-{len(requests)}', 'type': 'function',
                                    'function': {'name': name, 'arguments': json.dumps(arguments, ensure_ascii=False)}}]}
            chunks = [{'id': 'test', 'object': 'chat.completion.chunk', 'model': MODEL,
                       'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
                      {'id': 'test', 'object': 'chat.completion.chunk', 'model': MODEL,
                       'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls'}]}]
            raw = (''.join('data: ' + json.dumps(chunk) + '\n\n' for chunk in chunks) + 'data: [DONE]\n\n').encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_real_harness_multistep_stops_on_validated_finish(tmp_path):
    """真实SDK多轮执行并在finish后停止，不依赖模型额外一句成功文字。"""
    if not (ROOT / '.cache/agent/venv/bin/python').exists():
        pytest.skip('独立SDK未安装')
    calls = [('read_source', {'source': 'T1', 'offset': 0}),
             ('write_text', {'name': '结果.txt', 'content': '用户提供测试资料。'}), finish()]
    with scripted_model(calls) as (url, requests):
        result = AssistantTaskRunner(HarnessBrain(url, lambda: 8192), tmp_path).run('总结资料', inputs={'text': '测试资料'})
    assert result.status == 'completed' and len(requests) == 3
    assert (result.path.parent / '结果.txt').read_text() == '用户提供测试资料。'
    assert all(request['reasoning_effort'] == 'none' for request in requests)


def test_real_harness_recovers_read_precondition_and_reuses_success(tmp_path):
    """先比较失败后读两份重试成功；再次相同写入复用结果，不重复追加。"""
    if not (ROOT / '.cache/agent/venv/bin/python').exists():
        pytest.skip('独立SDK未安装')
    before, after = tmp_path / 'before.txt', tmp_path / 'after.txt'
    before.write_text('旧版')
    after.write_text('新版')
    compare = ('compare_sources', {'left': 'F1', 'right': 'F2'})
    write = ('write_text', {'name': 'summary.txt', 'content': '由旧版改为新版。'})
    calls = [compare, ('read_source', {'source': 'F1', 'offset': 0}),
             ('read_source', {'source': 'F2', 'offset': 0}), compare, write, write, finish(citations='F1,F2')]
    with scripted_model(calls) as (url, requests):
        result = AssistantTaskRunner(HarnessBrain(url, lambda: 8192), tmp_path / 'out').run(
            '比较两份文件', inputs={'files': [before, after]})
    assert result.status == 'completed' and len(requests) == 7
    assert (result.path.parent / 'summary.txt').read_text() == '由旧版改为新版。'
    assert len([item for item in result.trace if item['name'] == 'write_text']) == 1


def test_real_harness_rejected_finish_can_recover(tmp_path):
    """完成提议缺少实际读取时不得停止；读取后相同完成提议可重新核验。"""
    if not (ROOT / '.cache/agent/venv/bin/python').exists():
        pytest.skip('独立SDK未安装')
    calls = [finish(), ('read_source', {'source': 'T1', 'offset': 0}), finish()]
    with scripted_model(calls) as (url, requests):
        result = AssistantTaskRunner(HarnessBrain(url, lambda: 8192), tmp_path).run('总结资料', inputs={'text': '测试资料'})
    assert result.status == 'completed' and len(requests) == 3


def test_voice_pipeline_passes_single_use_materials_and_generation():
    """明确复杂语音进入独立任务域，资料消费一次，旧代次不能再次派发。"""
    from src.omni.gateway_client import GatewayCallbacks
    class Decoder:
        """不创建真实Whisper进程。"""
        def __init__(self, path):
            """测试不需要解码资源。"""
    pipeline = TaskPipeline(GatewayCallbacks(), decoder_factory=Decoder)
    pipeline.begin_session()
    pipeline.set_task_inputs({'text': '仅本次资料'})
    utterance = Utterance('voice-test', pipeline.generation, time.monotonic(), np.zeros(0), False)
    task = '帮我比较资料并整理结果'
    assert pipeline.submit(utterance, task, route_task(task))
    queued = pipeline.tasks.get_nowait()
    assert queued[4] == {'text': '仅本次资料'} and pipeline.task_inputs == {}
    pipeline.invalidate()
    assert not pipeline.submit(utterance, task, route_task(task))


def test_gui_task_material_dialog_offscreen():
    """资料对话框不打开设备，中文任务与选择范围完整传出。"""
    import os
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from gui import AssistantDialog
    app = QApplication.instance() or QApplication([])
    dialog = AssistantDialog()
    dialog.task.setPlainText('总结资料')
    dialog.pasted.setPlainText('中文资料')
    assert dialog.inputs() == {'files': [], 'directories': [], 'text': '中文资料'}
    dialog.clear_inputs()
    assert dialog.inputs()['text'] == ''
    dialog.close()
