"""只解析标准输入中的PDF字节；有时限、页数和文本预算，不执行文档动作。"""
import io
import json
import sys


def extract(data):
    """提取文字PDF并保留页码；扫描页明确留下缺口，不把空白误报为已读。"""
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data), strict=True)
    if reader.is_encrypted:
        raise ValueError('不支持加密PDF，请提供已解密副本')
    if len(reader.pages) > 150:
        raise ValueError('PDF超过150页，请按章节拆分')
    texts, total = [], 0
    for number, page in enumerate(reader.pages, 1):
        contents = page.get_contents()
        if contents is not None and len(contents.get_data()) > 8 * 1024 * 1024:
            raise ValueError('PDF单页解压内容过大')
        text = page.extract_text() or ''
        total += len(text)
        if total > 1000000:
            raise ValueError('PDF文字超过100万字，请按章节拆分')
        texts.append(f'[PDF第{number}页]\n' + (text.strip() or '[本页没有可提取文字；可能是扫描页，尚未OCR]'))
    return '\n\n'.join(texts)


def main():
    """在专用子进程设资源限额并输出有界JSON；异常不回传原始文档内容。"""
    try:
        if sys.platform != 'win32':
            import resource
            resource.setrlimit(resource.RLIMIT_CPU, (15, 15))
            if sys.platform != "darwin":
                resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
        data = sys.stdin.buffer.read(8 * 1024 * 1024 + 1)
        if len(data) > 8 * 1024 * 1024:
            raise ValueError('PDF超过8 MiB')
        result = {'text': extract(data)}
    except Exception as error:
        result = {'error': str(error)[:300]}
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
