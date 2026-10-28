"""自由任务入口与能力分层；分类本身不授予工具、文件或云端权限。"""
import re


def assistant_instruction(text):
    """识别用户的开放任务，保留原文；转述、否定和普通寒暄不升级。"""
    if not isinstance(text, str) or not 2 <= len(text.strip()) <= 4000:
        return None
    value = text.strip()
    if re.search(r"^(?:不要|不用|别|假如|假设|如果|例如|比如|他说|她说|你说|我说过|有人说)", value):
        return None
    intent = r"分析|比较|对比|总结|汇总|整理|规划|计划|设计|计算|推导|解释|解答|研究|调查|制作|编写|生成|处理|读取|提取|翻译|改写|审阅|检查|查找|搜索|查询|完成"
    prefix = r"(?:(?:请你|请|帮我|麻烦你|麻烦|给我|帮忙|能不能|可以|你能|能否|我想让你)[，,\s]*){0,3}"
    explicit = re.match(r"^(?:请你|帮我|麻烦你|给我|帮忙).{2,}", value)
    if explicit and re.search(r"^(?:请你|帮我|麻烦你|给我|帮忙)(?:不要|不用|别|解释这句话|复述)", value):
        return None
    if explicit or re.match(prefix + "(?:" + intent + ")", value) or re.search(r"哪个好|哪个更好|如何选择|怎么选择", value):
        return ("assistant", value)
    return None


def cloud_reason(task, has_sources=False):
    """仅明确云端或手机交付预先交接；其余由受控本地能力及实际反馈判断。"""
    if re.search(r"OpenClaw|云端|发到.{0,8}手机|发送到.{0,8}手机|手机上", task, re.I):
        return "需要云端执行或手机交付"
    return None
