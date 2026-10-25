"""任务资料与产物：授权句柄读取、标准库 Office 文本、无公式 Excel 输出。"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import stat
import zipfile
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from .boundary import AgentRefused, open_directory

MAX_FILE = 2 * 1024 * 1024
EXTENSIONS = {".txt", ".md", ".csv", ".tsv", ".json", ".log", ".py", ".docx", ".xlsx"}


def read_bytes(path):
    """逐级拒绝符号链接，只读取有界普通文件，不打开管道、设备或快捷链接。"""
    path = Path(path).absolute()
    parent = open_directory(path.parent)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    finally:
        os.close(parent)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE:
            raise AgentRefused("资料必须为不超过 2 MiB 的普通文件")
        data = handle.read(MAX_FILE + 1)
    if len(data) > MAX_FILE:
        raise AgentRefused("资料在读取时超过大小限制")
    return data


def office_text(data, extension):
    """只解压有界 OOXML 文本，不运行宏、外链、公式或嵌入对象。"""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = archive.infolist()
        if len(infos) > 500 or sum(i.file_size for i in infos) > 8 * MAX_FILE:
            raise AgentRefused("Office 解压大小或条目超限")
        if any(i.flag_bits & 1 for i in infos):
            raise AgentRefused("不支持加密 Office 文件")
        if extension == ".docx":
            root = ET.fromstring(archive.read("word/document.xml"))
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            return "\n".join("".join(t.text or "" for t in p.findall(".//w:t", ns)) for p in root.findall(".//w:p", ns))
        ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared = ["".join(t.text or "" for t in item.findall(".//x:t", ns))
                      for item in ET.fromstring(archive.read("xl/sharedStrings.xml")).findall("x:si", ns)]
        lines = []
        for name in sorted(archive.namelist()):
            if not (name.startswith("xl/worksheets/sheet") and name.endswith(".xml")):
                continue
            lines.append("[" + name + "]")
            for row in ET.fromstring(archive.read(name)).findall(".//x:row", ns):
                cells = []
                for cell in row.findall("x:c", ns):
                    value = cell.findtext("x:v", "", ns)
                    if cell.get("t") == "s":
                        value = shared[int(value)]
                    elif cell.get("t") == "inlineStr":
                        value = "".join(t.text or "" for t in cell.findall(".//x:t", ns))
                    if cell.find("x:f", ns) is not None:
                        value = "[公式缓存值，未重新计算] " + value
                    cells.append(cell.get("r", "") + "=" + value)
                lines.append("\t".join(cells))
        return "\n".join(lines)


def decode_text(data, extension):
    """按 BOM、UTF-8 或中文 GB18030 严格解码；不吞掉错误字节。"""
    if extension in {".docx", ".xlsx"}:
        return office_text(data, extension)
    if extension not in EXTENSIONS:
        raise AgentRefused("当前资料支持 TXT/Markdown/CSV/TSV/JSON/日志/Python/DOCX/XLSX")
    encodings = ["utf-16"] if data.startswith((b"\xff\xfe", b"\xfe\xff")) else ["utf-8-sig", "gb18030"]
    for encoding in encodings:
        try:
            text = data.decode(encoding)
            if "\x00" in text:
                raise AgentRefused("资料包含二进制内容")
            return text
        except UnicodeDecodeError:
            continue
    raise AgentRefused("资料编码无法识别，请另存为 UTF-8")


class Materials:
    """模型只取得本次选定资料编号；不能提供任意路径扩大读取范围。"""

    def __init__(self, files=(), directories=(), text=""):
        """保存显式授权的精确路径，目录只收录首层支持文件且最多 40 份。"""
        if len(files) > 40 or len(directories) > 4 or len(text) > 30000:
            raise AgentRefused("任务资料超过数量或文字上限")
        paths = [Path(p).absolute() for p in files]
        for directory in directories:
            fd = open_directory(directory)
            try:
                for name in sorted(os.listdir(fd)):
                    if name.startswith(".") or Path(name).suffix.lower() not in EXTENSIONS:
                        continue
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    if stat.S_ISREG(info.st_mode):
                        paths.append(Path(directory).absolute() / name)
                    if len(paths) > 40:
                        raise AgentRefused("目录包含超过 40 份资料，请选更小范围")
            finally:
                os.close(fd)
        self.paths, self.sources = {}, {}
        for path in dict.fromkeys(paths):
            if path.suffix.lower() not in EXTENSIONS:
                raise AgentRefused("不支持的资料格式：" + path.suffix)
            self.paths["F" + str(len(self.paths) + 1)] = path
        if text.strip():
            self.add("粘贴文本", text, {"kind": "user_text"}, "T1")

    def add(self, title, text, provenance, identifier=None):
        """加入真实来源快照；资料原文仅留本地，摘要明确标注读取范围。"""
        identifier = identifier or "S" + str(len(self.sources) + 1)
        self.sources[identifier] = {"id": identifier, "title": title, "text": text,
                                    "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                                    "provenance": provenance, "length": len(text)}
        return identifier

    def inventory(self):
        """返回资料索引，不提前把全部正文送入模型。"""
        return [{"id": key, "title": path.name} for key, path in self.paths.items()] + [
            {"id": key, "title": value["title"], "length": value["length"]}
            for key, value in self.sources.items() if key not in self.paths]

    def read(self, identifier, offset=0, limit=700):
        """分页读取已授权编号，保留完整快照与原始文件哈希用于发布前复核。"""
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 1200:
            raise AgentRefused("读取页范围无效")
        if identifier in self.paths and identifier not in self.sources:
            path = self.paths[identifier]
            data = read_bytes(path)
            self.add(path.name, decode_text(data, path.suffix.lower()),
                     {"kind": "file", "path": str(path), "file_sha256": hashlib.sha256(data).hexdigest()}, identifier)
        if identifier not in self.sources:
            raise AgentRefused("资料编号未授权或尚未读取")
        value = self.sources[identifier]
        return {"id": identifier, "title": value["title"], "sha256": value["sha256"],
                "offset": offset, "text": value["text"][offset:offset + limit],
                "length": value["length"], "has_more": offset + limit < value["length"],
                "scope": value["provenance"].get("scope", "本次资料快照")}

    def verify(self):
        """交付前核对实际读过的文件未变动，不将旧摘要伪装成最新内容。"""
        for value in self.sources.values():
            source = value["provenance"]
            if source["kind"] == "file" and hashlib.sha256(read_bytes(source["path"])).hexdigest() != source["file_sha256"]:
                raise AgentRefused("输入文件已改变，请重新执行任务")


def workbook_bytes(rows):
    """生成 Excel 可直接打开的 XLSX；所有单元格为文字，阻止公式注入。"""
    if (not isinstance(rows, list) or not 1 <= len(rows) <= 200
            or any(not isinstance(row, list) or not 1 <= len(row) <= 20 for row in rows)):
        raise AgentRefused("表格须为 1–200 行、每行 1–20 列")
    xml_rows = []
    for index, row in enumerate(rows, 1):
        cells = []
        for column, cell in enumerate(row):
            if not isinstance(cell, (str, int, float)) or isinstance(cell, bool) or len(str(cell)) > 2000:
                raise AgentRefused("单元格必须是有界文字或数字")
            value = str(cell)
            if any(ord(c) < 32 and c not in "\n\r\t" for c in value):
                raise AgentRefused("单元格含控制字符")
            cells.append(f'<c r="{chr(65 + column)}{index}" t="inlineStr"><is><t xml:space="preserve">{escape(value)}</t></is></c>')
        xml_rows.append(f'<row r="{index}">' + "".join(cells) + '</row>')
    files = {
        "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        "_rels/.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="结果" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + "".join(xml_rows) + '</sheetData></worksheet>',
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, '<?xml version="1.0" encoding="UTF-8"?>' + content)
    return output.getvalue()
