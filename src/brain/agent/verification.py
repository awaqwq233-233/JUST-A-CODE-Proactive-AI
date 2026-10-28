"""工具交付验收与错误分类：程序证据优先，模型自述不是完成依据。"""
import io
import re
import zipfile
from xml.etree import ElementTree as ET

from .materials import office_text
from .table_tools import office_tables, checked_rows


def classify_failure(error, name=''):
    """以已知异常类别和具体原因区分有限重试、补资料及参数修复。"""
    from src.tools.web_search import SearchError
    if isinstance(error, SearchError):
        return 'transient' if re.search(r'超时|网络|DNS|未取得', str(error)) else 'source_unavailable'
    if isinstance(error, (FileNotFoundError, PermissionError)) or re.search(r'权限|资料编号|未授权|资料已改变|会话|登录', str(error)):
        return 'needs_input'
    return 'repair_parameters'


def validate_criteria(criteria):
    """只接收可由程序验证的有限验收字段，不将模型文字当检查代码。"""
    allowed = {'required_columns', 'expected_rows', 'min_rows', 'required_files'}
    if not isinstance(criteria, dict) or set(criteria) - allowed:
        raise ValueError('验收条件只支持required_columns/expected_rows/min_rows/required_files')
    for key in ('expected_rows', 'min_rows'):
        if key in criteria and (type(criteria[key]) is not int or not 0 <= criteria[key] <= 10000):
            raise ValueError('验收行数须为0–10000整数')
    for key in ('required_columns', 'required_files'):
        if key in criteria and (not isinstance(criteria[key], list) or len(criteria[key]) > 40
                               or any(not isinstance(v, str) or not 1 <= len(v) <= 100 for v in criteria[key])):
            raise ValueError('验收字段须为有界字符串数组')
    return criteria


def validate_artifacts(tools):
    """真实打开每个产物并检查结构、字段、行数与数据来源，不宣称主观内容必然正确。"""
    errors, checks = [], []
    criteria = tools.record.get('acceptance', {})
    for name in criteria.get('required_files', []):
        if name not in tools.artifacts:
            errors.append('缺少文件：' + name)
    outputs = ' '.join(re.findall(r'(?:生成|制作|导出|输出|创建|写一份|做一份|给我一份)([^，。；;\n]{0,80})', tools.task))
    if re.search(r'Word|word|docx', outputs) and not any(name.endswith('.docx') for name in tools.artifacts):
        errors.append('用户要求Word，但尚未生成DOCX')
    if re.search(r'Excel|xlsx|表格', outputs, re.I) and not any(name.endswith('.xlsx') for name in tools.artifacts):
        errors.append('用户要求表格，但尚未生成XLSX')
    for name, data in tools.artifacts.items():
        try:
            if name.endswith('.xlsx'):
                tables = office_tables(data, '.xlsx')
                for title, rows in tables.items():
                    table = checked_rows(rows)
                    count = len(table['rows'])
                    if set(criteria.get('required_columns', [])) - set(table['headers']):
                        errors.append(name + '缺少要求的字段')
                    if 'expected_rows' in criteria and count != criteria['expected_rows']:
                        errors.append(name + f'行数为{count}，与要求不符')
                    if count < criteria.get('min_rows', 0):
                        errors.append(name + '行数不足')
                    source_id = tools.record.get('artifact_sources', {}).get(name)
                    if source_id:
                        source = tools.materials.sources[source_id]['table']
                        if table != source:
                            errors.append(name + '与实际数据表内容不一致')
                    checks.append({'file': name, 'sheet': title, 'rows': count, 'columns': table['headers']})
            elif name.endswith('.docx'):
                text = office_text(data, '.docx')
                if not text.strip():
                    errors.append(name + '正文为空')
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    ET.fromstring(archive.read('word/styles.xml'))
                checks.append({'file': name, 'characters': len(text), 'readable': True})
            else:
                text = data.decode('utf-8', errors='strict')
                checks.append({'file': name, 'characters': len(text), 'readable': True})
        except (ValueError, KeyError, OSError, ET.ParseError, zipfile.BadZipFile) as error:
            errors.append(name + '不可读取：' + str(error)[:160])
    return {'passed': not errors, 'errors': errors, 'checks': checks,
            'scope': '程序核验结构、字段、计数和来源；主观内容仍须对照任务审阅'}
