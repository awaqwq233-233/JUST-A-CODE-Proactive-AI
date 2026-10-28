"""开放任务能力注册表：schema、权限、恢复语义与产物检查共用定义。"""
from dataclasses import dataclass
import json
import re

from .assistant import schema, text_spec
from .boundary import AgentRefused
from .office_output import xlsx_bytes, docx_bytes
from .table_tools import checked_rows, load_tables, transform


@dataclass(frozen=True)
class Capability:
    """程序定义工具契约；模型不能登记新执行代码或变更权限。"""
    name: str
    description: str
    parameters: dict
    scope: str
    recovery: str
    postcondition: str

    def tool_schema(self):
        """生成与实际注册执行器一致的模型工具参数。"""
        return schema(self.name, self.description, self.parameters)


CAPABILITIES = (
    Capability('read_wechat_history', '在精确会话消息区域有限向上翻页；pages为1–6，不保证完整历史或时间范围。默认复用快照，refresh为true才重新读取。',
               {'chat': text_spec(), 'pages': {'type': 'integer'}, 'refresh': {'type': 'boolean'}},
               'explicit_wechat_chat', 'read_ui', 'exact_chat_and_overlap'),
    Capability('find_source', '在资料中查找字面关键词，返回段落及可继续读取的偏移。',
               {'source': text_spec(), 'term': text_spec(), 'offset': {'type': 'integer'}}, 'authorized_sources', 'read', 'observed_offsets'),
    Capability('load_table', '读取CSV/TSV/JSON或Office表格，table为空时返回表名；仅一张表则直接载入。',
               {'source': text_spec(), 'table': text_spec()}, 'authorized_sources', 'read', 'rectangular_rows'),
    Capability('transform_table', '程序数据运算，operation_json严格JSON。分组示例：{"op":"group","fields":["负责人"],"aggregate":"sum","value_field":"工时","result_field":"总工时"}。其他op：select/dedupe用fields；sort用fields/numeric/descending；convert用fields/type(number/text/date)；filter用field/operator(eq/ne/contains/gt/ge/lt/le/empty/not_empty)/value；join用fields和other表ID。其余other为空。',
               {'source': text_spec(), 'operation_json': text_spec(), 'other': text_spec()}, 'task_data', 'pure', 'computed_rows'),
    Capability('export_table', '将数据表ID完整导出XLSX，程序传递全部行，不需模型逐行重写。',
               {'source': text_spec(), 'name': text_spec()}, 'new_artifacts', 'private_draft', 'xlsx_rows_match'),
    Capability('write_document', '生成可编辑中文Word报告，paragraphs_json为正文字符串数组。',
               {'name': text_spec(), 'title': text_spec(), 'paragraphs_json': text_spec()}, 'new_artifacts', 'private_draft', 'docx_readable'),
    Capability('validate_result', '按任务验收要求检查当前产物与引用；返回需修正项，不结束任务。',
               {}, 'task_artifacts', 'read', 'acceptance_checks'),
    Capability('read_web_page', '读取实际搜索结果中的公开网址；不能读取内网或私有资料中的地址。',
               {'source': text_spec(), 'result_id': {'type': 'integer'}}, 'public_web', 'read', 'public_text_with_url'),
)
REGISTRY = {capability.name: capability for capability in CAPABILITIES}


def extension_schemas(tools):
    """按资料和任务域开放能力，私有任务无公网入口，表格和文档能力按需加载。"""
    selected = ['find_source', 'validate_result']
    task = tools.task
    if (re.search(r'表|数据|统计|去重|筛选|排序|汇总|Excel|xlsx|csv|CSV', task)
            or any(path.suffix.lower() in {'.csv', '.tsv', '.json', '.xlsx', '.docx'} for path in tools.materials.paths.values())):
        selected += ['load_table', 'transform_table', 'export_table']
    if re.search(r'Word|word|docx|文档|报告', task):
        selected += ['write_document']
    if tools.allow_web:
        selected += ['read_web_page']
    return [REGISTRY[name].tool_schema() for name in selected]


def add_table(tools, title, table, inputs, operation=None):
    """保存程序结构化结果及来源，模型只获得编号、字段、行数和有界预览。"""
    table = checked_rows([table['headers']] + table['rows'])
    text = json.dumps(table, ensure_ascii=False)
    identifier = tools.materials.add(title, text, {'kind': 'table', 'inputs': inputs, 'operation': operation,
                                                 'row_count': len(table['rows'])})
    tools.materials.sources[identifier]['table'] = table
    return {'id': identifier, 'headers': table['headers'], 'row_count': len(table['rows']),
            'preview': table['rows'][:3], 'all_rows_saved': True}


def execute_extension(tools, name, args):
    """工具实现只引用已授权编号和固定操作；网页内容不可成为执行参数代码。"""
    if name == 'read_wechat_history':
        if re.search(r'从.{1,25}到|[0-9]{1,2}月[0-9]{1,2}日|昨天|今天|上周|本周|最近[0-9一二三四五六七八九十]+天', tools.task):
            raise AgentRefused('当前微信时间标签未可靠关联每条消息，无法验收指定时间范围；请提供带时间的导出或改为有限页快照')
        if not args['refresh']:
            prior = next((key for key, value in tools.materials.sources.items()
                          if value['provenance']['kind'] == 'wechat' and value['title'] == args['chat']), None)
            if prior:
                return tools._read(prior)
        from src.tools.wechat import read_wechat
        value = read_wechat(args['chat'], tools.task, tools.should_stop, pages=args['pages'])
        identifier = tools.materials.add(value['chat'], json.dumps(value['messages'], ensure_ascii=False),
            {'kind': 'wechat', 'scope': value['scope'], 'captured_at': value['captured_at'], 'complete_history': False})
        return tools._read(identifier)
    if name == 'find_source':
        source = args['source']
        tools.materials.read(source)
        text, term, start = tools.materials.sources[source]['text'], args['term'], args['offset']
        if not 1 <= len(term) <= 200 or type(start) is not int or start < 0:
            raise AgentRefused('查找词或偏移无效')
        matches = []
        while len(matches) < 5:
            index = text.find(term, start)
            if index < 0:
                break
            matches.append(tools._record_read(tools.materials.read(source, max(0, index - 80), 300)))
            start = index + len(term)
        return {'matches': matches, 'next_offset': start, 'has_more': text.find(term, start) >= 0}
    if name == 'load_table':
        tables = load_tables(tools.materials, args['source'])
        selected = args['table']
        if not selected:
            if len(tables) != 1:
                return {'tables': list(tables), 'instruction': '请选择精确表名再次调用'}
            selected = next(iter(tables))
        if selected not in tables:
            raise AgentRefused('表名不存在，可用表：' + '、'.join(tables))
        return add_table(tools, selected, checked_rows(tables[selected]), [args['source']], {'op': 'load', 'table': selected})
    if name == 'transform_table':
        source = tools.materials.sources[args['source']]
        if 'table' not in source:
            raise AgentRefused('须先用load_table获得数据表ID')
        other = tools.materials.sources.get(args['other'], {}).get('table') if args['other'] else None
        operation = json.loads(args['operation_json'])
        if not isinstance(operation, dict):
            raise AgentRefused('数据操作须为JSON对象')
        table = transform(source['table'], operation, other)
        return add_table(tools, '程序数据处理', table, [args['source']] + ([args['other']] if args['other'] else []), operation)
    if name == 'export_table':
        source = tools.materials.sources[args['source']]
        if 'table' not in source:
            raise AgentRefused('导出对象须为数据表ID')
        table = source['table']
        result = tools._stage(args['name'], xlsx_bytes([table['headers']] + table['rows']), 'xlsx')
        tools.record.setdefault('artifact_sources', {})[args['name']] = args['source']
        return {**result, 'row_count': len(table['rows']), 'columns': table['headers']}
    if name == 'write_document':
        return tools._stage(args['name'], docx_bytes(args['title'], json.loads(args['paragraphs_json'])), 'docx')
    if name == 'validate_result':
        from .verification import validate_artifacts
        return validate_artifacts(tools)
    if name == 'read_web_page':
        if not tools.allow_web:
            raise AgentRefused('私有资料任务不开放公网工具')
        source = tools.materials.sources[args['source']]
        if source['provenance']['kind'] != 'web':
            raise AgentRefused('只能继续读取实际搜索返回的网页')
        data = json.loads(source['text'])
        item = next((r for r in data.get('results', []) if r['id'] == args['result_id']), None)
        if item is None:
            raise AgentRefused('搜索结果编号不存在')
        from src.tools.web_search import WebSearchClient
        client = tools.search_client or WebSearchClient()
        page = client.read_page(item['url'], tools.should_stop)
        identifier = tools.materials.add(page.get('title') or item['title'], page['text'],
                                        {'kind': 'web_page', 'url': page['url'], 'retrieved_at': page['retrieved_at'],
                                         'date_metadata': page.get('date_metadata', {}), 'inputs': [args['source']],
                                         'truncated': page['truncated']})
        return tools._read(identifier)
    raise AgentRefused('工具未注册')
