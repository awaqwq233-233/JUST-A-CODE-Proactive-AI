"""工具执行器：根据模型给出的名字与参数，安全分发到具体工具函数。"""
from .registry import TOOLS

# 名字 -> 工具定义，便于 O(1) 查找
_TOOL_MAP = {t["name"]: t for t in TOOLS}


def execute_tool(name, arguments):
    """执行单个工具并返回字符串结果。

    任何异常都被捕获并转成错误文本返回，让模型有机会自行纠正，
    而不是让整轮对话崩溃。
    """
    if name not in _TOOL_MAP:
        return f"错误：未知工具 {name}（可用工具：{', '.join(_TOOL_MAP.keys())}）"
    try:
        return _TOOL_MAP[name]["func"](arguments or {})
    except Exception as e:
        return f"工具 {name} 执行出错：{e}"
