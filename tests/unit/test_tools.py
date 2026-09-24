"""J.A.C. Function Calling 工具层单元测试（离线、不依赖 LM Studio）。

覆盖：工具 schema 格式、受限 shell 放行/拦截、本地文件搜索与越权拦截、
系统状态查询、未知工具处理，以及大脑侧 tool_calls 解析（LM Studio / Ollama 两种格式）。
"""
import os
import shutil
import tempfile

import pytest

from tools import get_tool_schemas, execute_tool
from brain.llm import parse_tool_calls, ThinkResult, LocalBrain


# ---------------------------------------------------------------------------
# 注册表 / schema
# ---------------------------------------------------------------------------
def test_get_tool_schemas_format():
    """tools 定义必须是合法的 OpenAI 风格结构，且包含 5 个白名单工具。"""
    schemas = get_tool_schemas()
    assert len(schemas) == 5
    names = set()
    for s in schemas:
        assert s["type"] == "function"
        fn = s["function"]
        assert fn["name"] and fn["description"]
        assert "properties" in fn["parameters"]
        names.add(fn["name"])
    assert names == {"open_url", "open_app", "search_files", "get_system_info", "run_command"}


# ---------------------------------------------------------------------------
# 受限 shell
# ---------------------------------------------------------------------------
def test_run_command_allows_whitelisted():
    """白名单内的只读命令应当正常执行并返回输出。"""
    out = execute_tool("run_command", {"command": "echo fc_ok_123"})
    assert "fc_ok_123" in out


def test_run_command_blocks_dangerous():
    """rm / sudo 等危险命令必须被拦截。"""
    out = execute_tool("run_command", {"command": "rm -rf /"})
    assert ("拒绝" in out) or ("危险" in out)


def test_run_command_blocks_unauthorized():
    """不在白名单的基命令（如 git）必须被拒绝。"""
    out = execute_tool("run_command", {"command": "git status"})
    assert ("未授权" in out) or ("拒绝" in out)


# ---------------------------------------------------------------------------
# 本地文件搜索
# ---------------------------------------------------------------------------
def test_search_files_finds_and_blocks_scope():
    """能按关键字搜到文件；非用户目录路径必须被安全拦截。"""
    # 用「每次唯一的子目录」而不是固定的 ~/fc_test_tmp（2026-09-24 修复）：
    #   ① 固定目录名会在多次运行之间累积文件——一旦清理被外部保护机制拦下就会残留，
    #      而本用例断言的是「搜索结果里含目标文件」（max_results=5），目录里文件一多，
    #      目标就可能被挤出前 5 名，表现为「代码正确、测试假失败」（实测残留 58 个时触发）；
    #   ② 唯一子目录保证每次都是干净起点，且清理量只有 1 个文件，不会触发批量删除保护。
    # 注意：目录必须仍位于用户主目录下——`search_files` 工具限定只扫用户目录。
    home_tmp = tempfile.mkdtemp(prefix="fc_test_tmp_", dir=os.path.expanduser("~"))
    try:
        with open(os.path.join(home_tmp, "fc_report_2026.pdf"), "w") as f:
            f.write("x")
        res = execute_tool("search_files", {"query": "report", "path": home_tmp, "max_results": 5})
        assert "fc_report_2026.pdf" in res

        # 越权：/tmp 不在用户目录下，必须被拦
        blocked = execute_tool("search_files", {"query": "x", "path": "/tmp"})
        assert "安全限制" in blocked
    finally:
        try:
            shutil.rmtree(home_tmp, ignore_errors=True)
        except BaseException as e:  # noqa: BLE001
            # 清理属于 best-effort：部分受保护环境（沙箱 / 批量删除配额）会让 rmtree
            # 直接抛 SystemExit（它是 BaseException 而非 Exception，`ignore_errors`
            # 只兜 OSError，兜不住）。清理失败不该让一个已经断言通过的用例变红，
            # 但也不静默——打印一行便于排查残留。
            print(f"[test_tools] 清理 {home_tmp} 失败（不影响断言）：{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# 系统状态查询
# ---------------------------------------------------------------------------
def test_get_system_info_time():
    """time 类型查询必须返回当前时间文本。"""
    out = execute_tool("get_system_info", {"info_type": "time"})
    assert "当前时间" in out


# ---------------------------------------------------------------------------
# 未知工具
# ---------------------------------------------------------------------------
def test_execute_unknown_tool():
    """调用不存在的工具应返回友好错误，而非抛异常。"""
    out = execute_tool("nonexistent_tool", {})
    assert "未知工具" in out


# ---------------------------------------------------------------------------
# 大脑侧 tool_calls 解析
# ---------------------------------------------------------------------------
def test_parse_tool_calls_lm_studio_format():
    """LM Studio 返回的 tool_calls（arguments 为 JSON 字符串、带 id）应正确解析。"""
    msg = {
        "tool_calls": [
            {"id": "abc123", "type": "function",
             "function": {"name": "open_url", "arguments": '{"url":"https://www.baidu.com"}'}}
        ]
    }
    parsed, clean = parse_tool_calls(msg)
    assert parsed[0]["name"] == "open_url"
    assert parsed[0]["arguments"] == {"url": "https://www.baidu.com"}
    # clean 需保留原样 arguments 字符串与 id，供下一轮回传
    assert clean[0]["id"] == "abc123"
    assert clean[0]["function"]["arguments"] == '{"url":"https://www.baidu.com"}'


def test_parse_tool_calls_ollama_format():
    """Ollama 返回的 tool_calls（arguments 为 dict、可能缺 id）应正确解析并补 id。"""
    msg = {
        "tool_calls": [
            {"type": "function", "function": {"name": "get_system_info", "arguments": {"info_type": "battery"}}}
        ]
    }
    parsed, clean = parse_tool_calls(msg)
    assert parsed[0]["arguments"] == {"info_type": "battery"}
    assert clean[0]["id"].startswith("call_")


# ---------------------------------------------------------------------------
# 工具调用循环（mock 后端降级）
# ---------------------------------------------------------------------------
def test_run_agentic_mock_fallback():
    """mock 后端下 run_agentic 不应抛异常，并产出文本（无工具分支）。"""
    brain = LocalBrain(backend="mock")
    out = "".join(brain.run_agentic(
        "帮我打开百度首页",
        tools=get_tool_schemas(),
        tool_executor=execute_tool,
        max_iterations=2,
    ))
    assert isinstance(out, str) and out
