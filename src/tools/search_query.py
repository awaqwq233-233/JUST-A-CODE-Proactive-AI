"""共享的联网指令与天气关键词解析；只做明确语法归一，不猜位置或同音字。"""

from dataclasses import dataclass
import re
import unicodedata

PREFIX = r"(?:(?:请你|请|帮我|让我|麻烦你|麻烦|给我|jac|贾维斯)[，,\s]*){0,3}"
ACTION = r"(?>搜索一下|搜索|搜一下|搜寻|查寻|查询一下|查询|查一下|查下|查|搜)"
PERIOD = r"今天|明天|后天|本周|周末|未来\d+天"


@dataclass(frozen=True)
class WeatherQuery:
    """保留用户地区与相对日期；空地区只用于触发本地追问。"""

    location: str
    offset: int | None


def normalized_query(text):
    """统一全角与明确繁体指令字，保留普通查询的英文大小写和内部空格。"""
    text = unicodedata.normalize("NFKC", text).strip().strip("，。！？,.!? ")
    return text.translate(str.maketrans("網絡搜尋幫請氣預報麼樣嗎後開刪執讓詢", "网络搜寻帮请气预报么样吗后开删执让询"))


def weather_query(query):
    """完整预报语法提取地区；拒绝指令残片作地名，科普问题保留为普通搜索。"""
    query = normalized_query(query)
    match = re.fullmatch(r"(.*?)\s*(?:的)?(?:天气(?:预报)?|(?:会不会|会)?下雨|有没有雨|气温多少(?:度)?|温度多少(?:度)?)(?:怎么样|如何|怎样|是什么|会下雨吗|吗)?", query)
    if not match:
        return None
    before = match.group(1).strip()
    periods = re.findall(PERIOD, before)
    if len(periods) > 1:
        return None
    location = re.sub(PERIOD, "", before).strip().rstrip("的").strip()
    if location and (not re.fullmatch(r"[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z .-]{1,19}", location)
            or re.search(r"上网|网上|联网|搜索|查|寻|插|一下|让我|帮我|给我|喜欢|觉得|他说|我说|不要|看看|现在|刚才|未来|原理|科普|为什么|什么", location)):
        return None
    offset = {"今天": 0, "明天": 1, "后天": 2}.get(periods[0]) if periods else None
    return WeatherQuery(location, offset)


def web_instruction(text):
    """按完整指令匹配联网查询；缺少天气地区时返回追问，畸形命令不兜底出网。"""
    text = normalized_query(text)
    match = re.fullmatch(PREFIX + r"(?:(?:在)?(?:网上|网络|网页|互联网)|上网|联网)\s*" + ACTION + r"\s*(.{2,200})", text, re.I)
    if match is None:
        match = re.fullmatch(PREFIX + ACTION + r"\s*(?:一下)?(?:在)?(?:网上|网络|互联网|网页)(?:的)?\s*(.{2,200})", text, re.I)
    if match:
        query = match.group(1).strip()
    else:
        # 明确联网说法解析失败时，不能把整句当成城市天气关键词。
        if re.search(r"上网|联网|网上|网络|互联网|网页", text):
            return None
        query = re.sub(r"^" + PREFIX + r"(?:" + ACTION + r")?\s*", "", text, flags=re.I)
        if weather_query(query) is None:
            return None
        query = re.sub(r"(?:怎么样|如何|怎样|是什么)$", "", query)
    if (not 2 <= len(query) <= 200 or any(ord(c) < 32 for c in query)
            or re.search(r"[<>]|(?:然后|并且|同时|再)(?:帮我|请)?(?:打开|删除|运行|执行|发送|上传|下载)", query)):
        return None
    weather = weather_query(query)
    if weather is not None and not weather.location:
        return ("weather_city",)
    return ("web", query)
