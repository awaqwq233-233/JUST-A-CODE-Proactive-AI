"""标准库生成新 Office 产物，保留中文、数值类型和基本可读版式。"""
import io
import math
import zipfile
from xml.sax.saxutils import escape

from .boundary import AgentRefused
from .table_tools import checked_rows


def archive_bytes(files):
    """封装UTF-8 OOXML，各部件不含外链、宏或可执行对象。"""
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + content)
    return output.getvalue()


def column_name(index):
    """将零起始列号转换为 Excel 字母坐标，支持超过26列。"""
    name, index = '', index + 1
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def clean_text(value):
    """拒绝OOXML不能表示的控制字符，并转义用户文字。"""
    value = str(value)
    if any(ord(c) < 32 and c not in '\n\r\t' for c in value):
        raise AgentRefused('产物包含不可写入Office的控制字符')
    return escape(value)


def xlsx_bytes(rows):
    """输出带表头冻结、筛选、列宽和真实数字/布尔类型的单工作表。"""
    table = checked_rows(rows)
    rows = [table['headers']] + table['rows']
    xml_rows = []
    for index, row in enumerate(rows, 1):
        cells = []
        for column, value in enumerate(row):
            reference = column_name(column) + str(index)
            style = '1' if index == 1 else '0'
            if index != 1 and isinstance(value, bool):
                cells.append(f'<c r="{reference}" s="{style}" t="b"><v>{int(value)}</v></c>')
            elif index != 1 and isinstance(value, (int, float)):
                if not math.isfinite(value):
                    raise AgentRefused('不能输出非有限数字')
                cells.append(f'<c r="{reference}" s="{style}" t="n"><v>{value}</v></c>')
            else:
                cells.append(f'<c r="{reference}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{clean_text(value if value is not None else "")}</t></is></c>')
        xml_rows.append(f'<row r="{index}"' + (' ht="28" customHeight="1"' if index == 1 else '') + '>' + ''.join(cells) + '</row>')
    columns = ''.join(f'<col min="{i+1}" max="{i+1}" width="{min(48, max(14, max(len(str(r[i])) if i < len(r) else 0 for r in rows[:100])*1.6)):.1f}" customWidth="1"/>' for i in range(len(rows[0])))
    files = {
        '[Content_Types].xml': '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        '_rels/.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        'xl/workbook.xml': '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="结果" sheetId="1" r:id="rId1"/></sheets></workbook>',
        'xl/_rels/workbook.xml.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>',
        'xl/styles.xml': '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Microsoft YaHei"/><charset val="134"/></font><font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Microsoft YaHei"/><charset val="134"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF173B5E"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>',
        'xl/worksheets/sheet1.xml': '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols>' + columns + '</cols><sheetData>' + ''.join(xml_rows) + f'</sheetData><autoFilter ref="A1:{column_name(len(rows[0])-1)}{len(rows)}"/></worksheet>',
    }
    return archive_bytes(files)


def docx_bytes(title, paragraphs):
    """生成带标题与段落的中文 Word 报告，保留可编辑正文及分页保护。"""
    if not isinstance(title, str) or not 1 <= len(title) <= 120 or not isinstance(paragraphs, list) or not 1 <= len(paragraphs) <= 200:
        raise AgentRefused('Word须有标题和1–200个正文段落')
    if any(not isinstance(p, str) or len(p) > 5000 for p in paragraphs) or sum(map(len, paragraphs)) > 100000:
        raise AgentRefused('Word段落过长')
    body = '<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr><w:r><w:t>' + clean_text(title) + '</w:t></w:r></w:p>'
    for paragraph in paragraphs:
        body += '<w:p><w:pPr><w:widowControl/><w:spacing w:after="160" w:line="300" w:lineRule="auto"/></w:pPr><w:r><w:t xml:space="preserve">' + clean_text(paragraph) + '</w:t></w:r></w:p>'
    return archive_bytes({
        '[Content_Types].xml': '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>',
        '_rels/.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        'word/_rels/document.xml.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>',
        'word/document.xml': '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + body + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr></w:body></w:document>',
        'word/styles.xml': '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Microsoft YaHei"/><w:sz w:val="22"/><w:lang w:val="zh-CN" w:eastAsia="zh-CN"/></w:rPr></w:rPrDefault></w:docDefaults><w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style><w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:after="320"/></w:pPr><w:rPr><w:b/><w:sz w:val="36"/></w:rPr></w:style></w:styles>',
    })
