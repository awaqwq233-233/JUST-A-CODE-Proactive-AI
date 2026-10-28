"""扩展资料、程序数据处理、验收纠错与公网能力边界。"""
import io
import json
import zipfile

import pytest

from src.brain.agent.boundary import AgentRefused
from src.brain.agent.durable import DurableTools
from src.brain.agent.materials import decode_text, Materials
from src.brain.agent.office_output import xlsx_bytes, docx_bytes
from src.brain.agent.table_tools import checked_rows, transform, office_tables
from src.brain.agent.task_store import TaskStore
from src.brain.agent.verification import validate_artifacts


def task_tools(tmp_path, task='整理数据生成Excel', inputs=None):
    """建立真实持久化工具域，输入与结果完全隔离。"""
    store = TaskStore(tmp_path / 'tasks')
    record = store.create(task, inputs or {})
    return DurableTools(record, store, None)


def test_typed_xlsx_roundtrip_and_literal_formula():
    """数字与布尔值保持类型，公式形文字不会被执行，中文及空列保留。"""
    rows = [['姓名', '金额', '完成', '备注'], ['中文', 12.5, True, '=1+2'], ['甲', 0, False, '']]
    data = xlsx_bytes(rows)
    assert office_tables(data, '.xlsx')['结果'] == rows
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert b'<f>' not in archive.read('xl/worksheets/sheet1.xml')
        assert b'state="frozen"' in archive.read('xl/worksheets/sheet1.xml')


def test_programmatic_data_pipeline_and_verified_export(tmp_path):
    """真实CSV经历去重、类型转换和分组求和，XLSX逐值与来源核验。"""
    source = tmp_path / '任务.csv'
    source.write_text('负责人,数量\n小李,2\n小王,3\n小李,2\n小李,4\n', encoding='utf-8')
    tools = task_tools(tmp_path, inputs={'files': [str(source)]})
    loaded = json.loads(tools.execute('load_table', {'source': 'F1', 'table': ''}))['id']
    deduped = json.loads(tools.execute('transform_table', {'source': loaded, 'other': '', 'operation_json': '{"op":"dedupe"}'}))['id']
    grouped = json.loads(tools.execute('transform_table', {'source': deduped, 'other': '',
        'operation_json': '{"op":"group","fields":["负责人"],"value_field":"数量","aggregate":"sum"}'}))['id']
    tools.execute('export_table', {'source': grouped, 'name': '汇总.xlsx'})
    assert office_tables(tools.artifacts['汇总.xlsx'], '.xlsx')['结果'] == [['负责人', 'sum_数量'], ['小李', 6], ['小王', 3]]
    assert validate_artifacts(tools)['passed']
    assert tools.materials.sources[grouped]['provenance']['inputs'] == [deduped]


def test_failed_business_check_allows_repair(tmp_path):
    """缺字段的完成提议被拒，模型可修订私有草稿后再次验收。"""
    tools = task_tools(tmp_path)
    tools.execute('plan_task', {'steps_json': '["生成表格","核验字段"]',
        'criteria_json': '{"required_columns":["负责人","任务"],"expected_rows":1}'})
    tools.execute('write_table', {'name': '结果.xlsx', 'rows_json': '[["任务"],["测试"]]'})
    bad = tools.execute('finish', {'status': 'completed', 'answer': '完成', 'citations': ''})
    assert '验收未通过' in bad and tools.finished is None
    tools.execute('write_table', {'name': '结果.xlsx', 'rows_json': '[["负责人","任务"],["小王","测试"]]'})
    assert validate_artifacts(tools)['passed']
    tools.execute('finish', {'status': 'completed', 'answer': '完成', 'citations': ''})
    assert tools.finished is not None
    assert tools.record['artifact_revisions']


def test_join_requires_unique_right_keys_and_retains_unmatched():
    """关联拒绝多对多膨胀，并明确保留未匹配记录。"""
    left = checked_rows([['编号', '任务'], ['1', '甲'], ['2', '乙']])
    right = checked_rows([['编号', '姓名'], ['1', '小王']])
    result = transform(left, {'op': 'join', 'fields': ['编号']}, right)
    assert result['rows'][1][-1] == '未匹配'
    right['rows'].append(['1', '小李'])
    with pytest.raises(AgentRefused, match='不唯一'):
        transform(left, {'op': 'join', 'fields': ['编号']}, right)


def test_data_errors_are_not_silently_zero():
    """空值和文字金额不参与伪统计，数值排序与日期转换使用程序语义。"""
    table = checked_rows([['金额'], ['10'], ['2']])
    assert transform(table, {'op':'sort', 'fields':['金额'], 'numeric':True})['rows'] == [['2'], ['10']]
    for value in ['', '未知', 'NaN']:
        with pytest.raises(AgentRefused):
            transform(checked_rows([['金额'], [value]]), {'op':'group','fields':[],'value_field':'金额','aggregate':'sum'})


def test_word_roundtrip_and_required_output(tmp_path):
    """Word正文可被标准OOXML阅读器还原，未生成要求文件不能完成。"""
    tools = task_tools(tmp_path, '生成Word报告')
    assert not validate_artifacts(tools)['passed']
    tools.execute('write_document', {'name':'报告.docx','title':'项目结果','paragraphs_json':'["中文内容第一段。","第二段可编辑。"]'})
    assert '中文内容第一段' in decode_text(tools.artifacts['报告.docx'], '.docx')
    assert validate_artifacts(tools)['passed']


def test_input_format_does_not_require_same_output(tmp_path):
    """读取Excel再写Word时不强制额外Excel；仅比较Word不强制生成DOCX。"""
    tools = task_tools(tmp_path, '读取Excel并生成Word报告')
    tools.execute('write_document', {'name': '报告.docx', 'title': '汇总', 'paragraphs_json': '["整理完成"]'})
    assert validate_artifacts(tools)['passed']
    tools.task = '比较两份Word资料的差异'
    tools.artifacts.clear()
    assert validate_artifacts(tools)['passed']


def test_pdf_text_and_scan_gap(tmp_path):
    """真实PDF解析子进程保留文字与页码，扫描/空页明确标注未OCR。"""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, NameObject, DictionaryObject
    writer = PdfWriter()
    page = writer.add_blank_page(width=400, height=400)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b'BT /F1 12 Tf 20 300 Td (Task evidence 123) Tj ET')
    page[NameObject('/Contents')] = writer._add_object(stream)
    writer.add_blank_page(width=400, height=400)
    data = io.BytesIO()
    writer.write(data)
    text = decode_text(data.getvalue(), '.pdf')
    assert 'Task evidence 123' in text and '第2页' in text and '尚未OCR' in text


def test_private_tasks_have_no_public_tools(tmp_path):
    """即使原话含联网要求，私有资料任务也不将资料送入公网页面读取。"""
    tools = task_tools(tmp_path, '联网比较两份资料', {'text': '私人资料'})
    assert not {'web_search', 'read_web_page'} & {s['function']['name'] for s in tools.schemas()}
    with pytest.raises(AgentRefused):
        tools.execute('read_web_page', {'source': 'T1', 'result_id': 1})


def test_web_pages_require_observed_search_result(tmp_path):
    """仅跟进实际搜索编号，保留网页来源和抓取时间，未知编号不访问网络。"""
    tools = task_tools(tmp_path, '联网比较公开产品资料')
    class Pages:
        """记录被允许的网页访问，测试无需公网连接。"""
        def __init__(self):
            """初始化访问凭据。"""
            self.urls = []

        def read_page(self, url, should_stop):
            """返回固定公开页面及抓取证据。"""
            self.urls.append(url)
            return {'title': '公开资料', 'url': url, 'text': '真实页面内容',
                    'retrieved_at': '2026-10-28T00:00:00Z', 'truncated': False}
    tools.search_client = Pages()
    source = tools.materials.add('搜索结果', json.dumps({'results': [
        {'id': 1, 'url': 'https://example.com/info', 'title': '资料'}]}), {'kind': 'web'})
    assert '搜索结果编号不存在' in tools.execute('read_web_page', {'source': source, 'result_id': 2})
    assert tools.search_client.urls == []
    result = json.loads(tools.execute('read_web_page', {'source': source, 'result_id': 1}))
    assert '真实页面内容' in result['text']
    assert tools.search_client.urls == ['https://example.com/info']
    assert any(s['provenance'].get('url') == 'https://example.com/info' for s in tools.materials.sources.values())


def test_unverified_wechat_paging_not_exposed(tmp_path, monkeypatch):
    """未实机验收的翻页既不提供给模型，也不能从Python接口绕过。"""
    from src.tools import wechat
    tools = task_tools(tmp_path, '总结微信「测试群」的消息')
    assert 'read_wechat_history' not in {s['function']['name'] for s in tools.schemas()}
    monkeypatch.setattr(wechat, 'check_wechat', lambda *args: {'supported': True, 'accessibility': True, 'running': True})
    with pytest.raises(AgentRefused, match='尚未完成实机验收'):
        wechat.read_wechat('测试群', '总结微信「测试群」的消息', pages=2)
