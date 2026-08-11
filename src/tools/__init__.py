"""J.A.C. 工具层（Function Calling / 给 J.A.C. 装手）。

对外只暴露两个入口，主程序 process_response 调用它们即可：
- get_tool_schemas()：返回 OpenAI 风格的 tools 定义，发给大脑模型决定何时调用。
- execute_tool(name, arguments)：执行某个具体工具，返回字符串结果回喂模型。

所有工具均收敛在白名单内：只做打开应用/网页、只读本地文件搜索、查询系统状态、
以及受限 shell（仅白名单命令）。绝不做删除/提权/任意写等危险动作。
"""
from .registry import get_tool_schemas, get_tool_names
from .executor import execute_tool

__all__ = ["get_tool_schemas", "get_tool_names", "execute_tool"]
