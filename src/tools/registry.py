"""工具注册表：把各文件里的具体工具收敛成统一列表，并生成 OpenAI 风格 schema。"""
from .open_actions import open_url, open_app
from .search_files import search_files
from .system_info import get_system_info
from .shell import run_command

# 每个工具含：name / description / parameters(JSON Schema) / func(接收参数字典返回字符串)
TOOLS = [
    {
        "name": "open_url",
        "description": "在系统默认浏览器中打开一个网页网址（仅支持 http/https）。",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要打开的网址，必须以 http:// 或 https:// 开头"}
            },
            "required": ["url"],
        },
        "func": open_url,
    },
    {
        "name": "open_app",
        "description": "按名称打开一个已安装的桌面应用程序（如 备忘录、Safari、终端）。",
        "parameters": {
            "type": "object",
            "properties": {
                "app_name": {"type": "string", "description": "应用名称，例如 备忘录、Safari、终端、日历"}
            },
            "required": ["app_name"],
        },
        "func": open_app,
    },
    {
        "name": "search_files",
        "description": "在用户目录（桌面/文档/下载）中按文件名关键字搜索文件，只读、不修改任何文件。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "文件名包含的子串，例如 报告、photo、.pdf"},
                "path": {"type": "string", "description": "可选，指定用户目录下的起始路径（如 ~/Desktop）"},
                "max_results": {"type": "integer", "description": "最多返回条数，默认 20，最大 100"}
            },
            "required": ["query"],
        },
        "func": search_files,
    },
    {
        "name": "get_system_info",
        "description": "查询本机系统状态：时间、电池、CPU 负载、内存占用。",
        "parameters": {
            "type": "object",
            "properties": {
                "info_type": {"type": "string", "description": "取值 time/battery/cpu/memory/all，默认 all"}
            },
            "required": [],
        },
        "func": get_system_info,
    },
    {
        "name": "run_command",
        "description": "执行一条受限 shell 命令（仅白名单内的只读/安全命令，如 ls/date/df），拦截 rm/sudo 等危险操作。",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的命令，例如 'ls -la ~/Desktop'"}
            },
            "required": ["command"],
        },
        "func": run_command,
    },
]


def get_tool_names():
    """返回所有已注册工具名（用于白名单校验）。"""
    return [t["name"] for t in TOOLS]


def get_tool_schemas():
    """把注册表转换成 OpenAI 风格的 tools 定义，直接喂给大脑模型的 tools 参数。"""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["parameters"],
            },
        }
        for t in TOOLS
    ]
