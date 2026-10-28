"""有界结构化资料与程序数据运算；不执行公式、表达式或模型生成代码。"""
from __future__ import annotations

import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
import io
import json
import math
import posixpath
import re
import zipfile
from xml.etree import ElementTree as ET

from .boundary import AgentRefused
from .materials import decode_text, read_bytes

NS = {'x': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
      'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
MAX_ROWS = 10000


def checked_rows(rows):
    """校验矩形、唯一表头和数据规模，缺列补空值而不猜字段内容。"""
    if not rows or len(rows) > MAX_ROWS + 1 or not 1 <= len(rows[0]) <= 40:
        raise AgentRefused('表格须含表头，最多10000行数据/40列')
    headers = [str(value).strip() for value in rows[0]]
    if any(not value for value in headers) or len(set(headers)) != len(headers):
        raise AgentRefused('表头存在空值或重名，请先提供明确字段')
    data = []
    for row in rows[1:]:
        if len(row) > len(headers):
            raise AgentRefused('数据列数超过表头，不能自动丢弃列')
        values = list(row) + [''] * (len(headers) - len(row))
        for value in values:
            if not isinstance(value, (str, int, float, bool, type(None))) or len(str(value)) > 5000:
                raise AgentRefused('单元格内容过大或类型不支持')
            if isinstance(value, float) and not math.isfinite(value):
                raise AgentRefused('数字包含非有限值')
        data.append(values)
    return {'headers': headers, 'rows': data}


def office_tables(data, extension):
    """读取DOCX表格或具名XLSX工作表，保留空列，不把公式缓存当重算结果。"""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if len(archive.infolist()) > 500 or sum(i.file_size for i in archive.infolist()) > 16 * 1024 * 1024:
            raise AgentRefused('Office解压大小或条目超限')
        if extension == '.docx':
            root = ET.fromstring(archive.read('word/document.xml'))
            tables = {}
            for index, table in enumerate(root.findall('.//w:tbl', NS), 1):
                rows = [[''.join(t.text or '' for t in cell.findall('.//w:t', NS))
                         for cell in row.findall('w:tc', NS)] for row in table.findall('w:tr', NS)]
                tables['表格' + str(index)] = rows
            return tables
        root = ET.fromstring(archive.read('xl/workbook.xml'))
        rels = {item.get('Id'): item.get('Target') for item in ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))}
        shared = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            shared = [''.join(t.text or '' for t in item.findall('.//x:t', NS))
                      for item in ET.fromstring(archive.read('xl/sharedStrings.xml')).findall('x:si', NS)]
        tables = {}
        for sheet in root.findall('x:sheets/x:sheet', NS):
            rel = sheet.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
            target = rels[rel]
            path = target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/' + target)
            if not path.startswith('xl/worksheets/'):
                raise AgentRefused('工作表引用超出Office工作表范围')
            rows = []
            for row in ET.fromstring(archive.read(path)).findall('x:sheetData/x:row', NS):
                values = []
                for cell in row.findall('x:c', NS):
                    match = re.fullmatch(r'([A-Z]+)[0-9]+', cell.get('r', ''))
                    if not match:
                        raise AgentRefused('单元格坐标缺失')
                    column = 0
                    for char in match[1]:
                        column = column * 26 + ord(char) - 64
                    if column > 40:
                        raise AgentRefused('工作表超过40列')
                    while len(values) < column:
                        values.append('')
                    value = cell.findtext('x:v', '', NS)
                    kind = cell.get('t')
                    if kind == 's':
                        value = shared[int(value)]
                    elif kind == 'inlineStr':
                        value = ''.join(t.text or '' for t in cell.findall('.//x:t', NS))
                    elif kind == 'b':
                        value = value == '1'
                    elif kind in {None, 'n'} and value:
                        number = Decimal(value)
                        value = int(number) if number == int(number) else float(number)
                    if cell.find('x:f', NS) is not None:
                        value = '[公式缓存未重新计算] ' + str(value)
                    values[column-1] = value
                rows.append(values)
                if len(rows) > MAX_ROWS + 1:
                    raise AgentRefused('工作表超过10000行数据')
            tables[sheet.get('name', '')] = rows
        return tables


def load_tables(materials, identifier):
    """只读已经授权的文件编号，数据版本必须与资料快照一致。"""
    materials.read(identifier)
    source = materials.sources[identifier]
    if 'table' in source:
        table = source['table']
        return {source['title']: [table['headers']] + table['rows']}
    if identifier in materials.paths:
        path = materials.paths[identifier]
        data = read_bytes(path)
        import hashlib
        if hashlib.sha256(data).hexdigest() != source['provenance']['file_sha256']:
            raise AgentRefused('资料已改变，请刷新后继续')
        if path.suffix.lower() in {'.docx', '.xlsx'}:
            return office_tables(data, path.suffix.lower())
        extension = path.suffix.lower()
    else:
        extension = '.csv'
    text = source['text']
    if extension == '.json':
        value = json.loads(text)
        if isinstance(value, list) and value and all(isinstance(row, dict) for row in value):
            headers = list(dict.fromkeys(key for row in value for key in row))
            return {'数据': [headers] + [[row.get(key, '') for key in headers] for row in value]}
        if isinstance(value, list):
            return {'数据': value}
        raise AgentRefused('JSON须为对象数组或二维数组')
    if extension not in {'.csv', '.tsv'}:
        raise AgentRefused('该资料不是CSV/TSV/JSON/Office结构表，请先提取结构化字段')
    return {'数据': list(csv.reader(io.StringIO(text), delimiter='\t' if extension == '.tsv' else ','))}


def number(value):
    """严格数值转换，不把空值、货币单位或未知文本静默当零。"""
    if isinstance(value, bool) or value in ('', None):
        raise AgentRefused('统计或转换遇到空值/布尔值，请先筛选或补全')
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise AgentRefused('字段不是有效数值：' + str(value)[:60]) from error
    if not result.is_finite():
        raise AgentRefused('字段含非有限数值')
    return result


def transform(table, operation, other=None):
    """执行枚举数据操作，返回新表和实际计数，不使用eval或模型算术。"""
    headers, rows = list(table['headers']), [list(row) for row in table['rows']]
    kind = operation.get('op')
    keys = {'select': {'op', 'fields'}, 'dedupe': {'op', 'fields'},
            'sort': {'op', 'fields', 'numeric', 'descending'},
            'filter': {'op', 'field', 'operator', 'value'}, 'convert': {'op', 'fields', 'type'},
            'group': {'op', 'fields', 'aggregate', 'value_field', 'result_field'}, 'join': {'op', 'fields'}}
    if kind not in keys or set(operation) - keys[kind]:
        raise AgentRefused('操作字段不支持；group必须用fields分组字段数组、aggregate(sum/count/mean/min/max)、value_field数值列、可选result_field输出列名')
    if kind == 'group' and ('fields' not in operation or 'aggregate' not in operation):
        raise AgentRefused('group须显式提供fields和aggregate，不能猜测分组或聚合方式')
    fields = operation.get('fields', [])
    if not isinstance(fields, list) or any(field not in headers for field in fields):
        raise AgentRefused('字段不存在')
    indexes = [headers.index(field) for field in fields]
    if kind == 'select':
        if not indexes:
            raise AgentRefused('请选择至少一个字段')
        return {'headers': fields, 'rows': [[row[i] for i in indexes] for row in rows]}
    if kind == 'dedupe':
        seen, result = set(), []
        for row in rows:
            key = json.dumps([row[i] for i in indexes] if indexes else row, ensure_ascii=False)
            if key not in seen:
                result.append(row)
                seen.add(key)
        rows = result
    elif kind == 'sort':
        if not indexes:
            raise AgentRefused('排序须指定字段')
        numeric = operation.get('numeric', False)
        rows.sort(key=lambda row: tuple(number(row[i]) if numeric else str(row[i]) for i in indexes), reverse=bool(operation.get('descending', False)))
    elif kind == 'filter':
        field = operation.get('field')
        if field not in headers:
            raise AgentRefused('筛选字段不存在')
        i, mode, value = headers.index(field), operation.get('operator', 'eq'), operation.get('value')
        if mode not in {'eq', 'ne', 'contains', 'gt', 'ge', 'lt', 'le', 'empty', 'not_empty'}:
            raise AgentRefused('筛选运算不支持')
        def matches(row):
            """使用固定比较语义，数值解析失败时保留错误而非自动删行。"""
            left = row[i]
            if mode == 'empty': return left in ('', None)
            if mode == 'not_empty': return left not in ('', None)
            if mode == 'eq': return str(left) == str(value)
            if mode == 'ne': return str(left) != str(value)
            if mode == 'contains': return str(value) in str(left)
            a, b = number(left), number(value)
            return {'gt': a > b, 'ge': a >= b, 'lt': a < b, 'le': a <= b}[mode]
        rows = [row for row in rows if matches(row)]
    elif kind == 'convert':
        target = operation.get('type')
        if not indexes or target not in {'number', 'text', 'date'}:
            raise AgentRefused('转换须指定字段与number/text/date类型')
        for row in rows:
            for i in indexes:
                if target == 'number':
                    value = number(row[i])
                    row[i] = int(value) if value == int(value) else float(value)
                elif target == 'date':
                    row[i] = datetime.fromisoformat(str(row[i])).date().isoformat()
                else:
                    row[i] = str(row[i])
    elif kind == 'group':
        aggregate, field = operation.get('aggregate', 'count'), operation.get('value_field')
        if aggregate not in {'count', 'sum', 'mean', 'min', 'max'} or (aggregate != 'count' and field not in headers):
            raise AgentRefused('聚合字段或运算无效')
        groups = {}
        for row in rows:
            key = tuple(row[i] for i in indexes)
            groups.setdefault(key, []).append(row)
        result = []
        for key, items in groups.items():
            value = len(items)
            if aggregate != 'count':
                values = [number(row[headers.index(field)]) for row in items]
                value = {'sum': lambda: sum(values), 'mean': lambda: sum(values)/len(values),
                         'min': lambda: min(values), 'max': lambda: max(values)}[aggregate]()
                value = int(value) if value == int(value) else float(value)
            result.append(list(key) + [value])
        label = operation.get('result_field', aggregate + ('_' + field if field else ''))
        return checked_rows([fields + [label]] + result)
    elif kind == 'join':
        if not other or not fields or any(field not in other['headers'] for field in fields):
            raise AgentRefused('关联须提供另一数据表及两表共有键字段')
        right_indexes = [other['headers'].index(field) for field in fields]
        mapping = {}
        for row in other['rows']:
            key = tuple(row[i] for i in right_indexes)
            if key in mapping:
                raise AgentRefused('右表关联键不唯一，先去重或聚合以避免多对多膨胀')
            mapping[key] = row
        extra = [i for i in range(len(other['headers'])) if i not in right_indexes]
        headers += ['右表_' + other['headers'][i] for i in extra] + ['匹配状态']
        rows = [row + [mapping.get(tuple(row[i] for i in indexes), [''] * len(other['headers']))[j] for j in extra]
                + ['匹配' if tuple(row[i] for i in indexes) in mapping else '未匹配'] for row in rows]
    else:
        raise AgentRefused('操作须为select/filter/sort/dedupe/convert/group/join')
    return checked_rows([headers] + rows)
