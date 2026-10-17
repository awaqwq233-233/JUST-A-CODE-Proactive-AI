"""本地任务的完整句授权；模型不得扩展动作、目录或参数。"""

import ipaddress
import json
from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit


APP_ALIASES = {
    "safari": "Safari", "日历": "Calendar", "calendar": "Calendar",
    "备忘录": "Notes", "notes": "Notes", "计算器": "Calculator",
    "calculator": "Calculator", "文本编辑": "TextEdit", "textedit": "TextEdit",
    "日曆": "Calendar", "備忘錄": "Notes", "計算器": "Calculator", "文本編輯": "TextEdit",
}
SITE_ALIASES = {
    "百度": "https://www.baidu.com/", "github": "https://github.com/",
    "python": "https://www.python.org/", "b站": "https://www.bilibili.com/",
    "哔哩哔哩": "https://www.bilibili.com/", "嗶哩嗶哩": "https://www.bilibili.com/",
    "bilibili": "https://www.bilibili.com/",
}
DIRECTORIES = {"桌面": "Desktop", "下载": "Downloads", "下載": "Downloads", "文档": "Documents", "文檔": "Documents"}
SCOPE_PROMPT = "bo s s，请说明搜索目录和文件名关键词，例如：在下载目录查找文件名包含报告的文件。"


def normalize_local_text(text):
    """仅去句边空白和结束标点；不转写网址、路径或文件名中的字符。"""
    suffix = "。！？" if re.search(r"https?://", text, re.I) else "。！!？?"
    return text.strip().rstrip(suffix).strip()


def validate_url(value):
    """只接受明确的 HTTP(S) 网址，拒绝凭据、控制字符和本地协议。"""
    if (not isinstance(value, str) or not 8 <= len(value) <= 2048
            or re.search(r'[\s\\<>"\x00-\x1f\x7f]', value)
            or re.search(r"%(?:0[0-9a-f]|1[0-9a-f]|7f)", value, re.I)):
        raise ValueError("网址格式不受支持")
    parsed = urlsplit(value)
    if (parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None or parsed.fragment
            or parsed.port not in (None, 80, 443)):
        raise ValueError("只支持无凭据、无片段的标准 HTTP(S) 网址")
    host = parsed.hostname.lower()
    if host == "localhost" or host.endswith((".local", ".localhost", ".internal")):
        raise ValueError("首版不打开本机或内部网址")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if (not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host)
                or "." not in host or ".." in host or host.rsplit(".", 1)[-1].isdigit()):
            raise ValueError("请提供明确的完整域名")
    else:
        if not address.is_global:
            raise ValueError("首版不打开本机或内部网址")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path or "/", parsed.query, ""))


def search_root(value):
    """将明确目录转为用户主目录内路径；实际文件访问时再次核对真实路径。"""
    if value in DIRECTORIES:
        return str(Path.home() / DIRECTORIES[value])
    path = Path(value).expanduser()
    if not path.is_absolute() or ".." in path.parts or any(part.startswith(".") for part in path.parts):
        raise ValueError("请指定主目录内的非隐藏目录")
    if not path.is_relative_to(Path.home()) or path == Path.home():
        raise ValueError("搜索目录须为主目录内的具体子目录")
    return str(path)


def website_url(target):
    """解析已知网站的完整名称和网页修饰词；明确网址保留原始路径与参数。"""
    target = re.sub(r"^(?:[网網]址|[网網][页頁]|[网網]站)\s*", "", target)
    if re.match(r"https?://", target, re.I):
        return validate_url(target)
    match = re.fullmatch(r"(.+?)(?:\s*(?:的\s*)?(?:官[网網]|官方[网網]站|[网網][页頁]|[网網]站))?", target)
    name = match[1].strip().lower() if match else ""
    if name not in SITE_ALIASES:
        raise ValueError("未识别网站名称，请提供完整 HTTP(S) 网址")
    return validate_url(SITE_ALIASES[name])


def website_name(target):
    """提取开放站名；拒绝协议、路径、应用请求、复合动作和未支持的页面操作。"""
    target = re.sub(r"^(?:[网網]址|[网網][页頁]|[网網]站)\s*", "", target)
    target = re.sub(r"\s*(?:的\s*)?(?:官[网網]|官方[网網]站|[网網][页頁]|[网網]站)$", "", target).strip()
    if (not 1 <= len(target) <= 80 or not any(c.isalnum() for c in target)
            or not re.fullmatch(r"[\w\u3400-\u9fff .+&()\-]+", target)
            or target in {"网站", "網頁", "网页", "官网", "網址", "网址", "網站", "官網", "终端", "終端"}
            or re.search(r"(?:应用|應用|程序)$|文件|目录|目錄|登录|登錄|登[陆陸]|然后|然後|[并並和及再](?:打开|打開|删除|刪除)|、|第[一二三123]个", target)
            or re.fullmatch(r"(?:" + '|'.join(map(re.escape, SITE_ALIASES)) + r")(?:和|与|與|及).+", target, re.I)):
        raise ValueError("请提供单个网站名称")
    return target


def local_instruction(text):
    """完整匹配单个本地动作；否定、转述、引用和复合指令不能获得执行权限。"""
    if not isinstance(text, str) or len(text) > 2400 or re.search(r"[\x00-\x1f\x7f]", text):
        return None
    text = normalize_local_text(text)
    if re.search(r'["“”‘’`<>]|不要|别打开|别查|不用|不必|他说|她说|假如|如果|然后|并且|顺便|再打开|再搜索|他說|她說|然後|並且|順便', text):
        return None
    if re.search(r"(?:并|並|和|及|再)(?:打[开開]|[删刪]除|修改|[运運]行|[执執]行|查找|搜索|[启啟][动動]|移[动動]|[复複]制|[发發]送|上[传傳])", text):
        return None
    text = re.sub(r"^(?:(?:[请請]你|[请請]|[帮幫]我|麻[烦煩]你|麻[烦煩]|[现現]在)[,， ]*){0,3}", "", text)
    choice = re.fullmatch(r"(?:打[开開]|[选選][择擇]?)(?:第)?([一二三123])(?:[个個])(?:[网網]站|[网網][页頁])?", text)
    if choice:
        return ("website_choice", str("一二三".index(choice[1]) + 1) if choice[1] in "一二三" else choice[1])
    if text in {"取消网站选择", "取消網站選擇"}:
        return ("website_cancel",)
    if text in {"清除网站缓存", "清除網站緩存"}:
        return ("website_forget", "*")
    refresh = re.fullmatch(r"(?:重新(?:查找|解析)|忘[记記])(.+?)(?:的)?[网網]站(?:地址)?", text)
    if refresh:
        try:
            name = website_name(refresh[1])
            return ("website_forget", name) if text.startswith(("忘记", "忘記")) else ("website", name, "refresh")
        except ValueError:
            return None
    match = re.fullmatch(r"(?:打[开開]|[启啟][动動])\s*(.+)", text)
    if match:
        target = match[1].strip()
        application = re.fullmatch(r'(?:[应應]用|程序)\s*(.+)', target)
        if application and not re.search(r'(?:[网網]站|[网網][页頁]|官[网網])$', target):
            app = APP_ALIASES.get(application[1].lower())
            return pack_action('open_app', {'app_name': app}) if app else None
        app = APP_ALIASES.get(target.lower())
        if app:
            return pack_action("open_app", {"app_name": app})
        try:
            return pack_action("open_url", {"url": website_url(target)})
        except ValueError:
            try:
                return ("website", website_name(target))
            except ValueError:
                return None
    match = re.fullmatch(r"在(.+?)(?:目[录錄]|文件[夹夾])?(?:[里裡]|中)?(?:查找|搜索|找一下|找)(?:文件名包含|名[称稱]包含|名[为為])?(.+?)(?:的文件|文件)", text)
    if match:
        root, query = match[1].strip(), match[2].strip()
        if not re.fullmatch(r"[\w\u3400-\u9fff .()\-]{1,80}", query) or not query.strip(" ."):
            return None
        try:
            root = search_root(root)
        except ValueError:
            return None
        return pack_action("search_files", {"path": root, "query": query, "max_results": 20})
    if re.fullmatch(r"(?:查找|搜索|找一下|找)(?:文件名包含|名[称稱]包含|名[为為])?[\w\u3400-\u9fff .()\-]{1,80}(?:的文件|文件)", text):
        return ("local_input",)
    return None


def pack_action(name, arguments):
    """不可变路由字段保存规范参数，供入队和执行前逐字复核。"""
    return ("local", name, json.dumps(arguments, ensure_ascii=False, sort_keys=True))


def is_local_request(text):
    """宽松识别电脑动作主题仅用于禁猜，不授予任何执行权限。"""
    text = normalize_local_text(text)
    return bool(re.search(r"打[开開]|[启啟][动動]|[开開][启啟]|(?:[选選]|取消|清除|忘[记記]|重新).*(?:[网網]站|[网網][页頁])|(?:查找|搜索|找一下|找).*(?:文件|目[录錄]|文件[夹夾])", text))
