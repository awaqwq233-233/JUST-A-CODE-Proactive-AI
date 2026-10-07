"""待迁移的 Qwen 工具路由；Gateway 尚未接线，不启动旧 MiniCPM 或播报链。"""
import logging

from src.brain.llm import LocalBrain
from src.brain.lm_studio import check_cancelled
from src.tools.registry import get_tool_schemas
from src.tools.executor import execute_tool
from .prompts import TOOL_SYSTEM_PROMPT

logger = logging.getLogger("omni.router")

# 升级令牌：omni 文本流中出现即触发路由
CALL_QWEN_TOKEN = "<<CALL_QWEN>>"


def parse_call_qwen(text: str):
    """从一段文本里解析 <<CALL_QWEN>>{task} 令牌，返回任务字符串或 None。

    约定：令牌后紧跟「一句话任务描述」，通常到首个换行结束。
    若令牌后无内容（任务还在后续流式分片到达），返回空串 "" 表示「已命中但任务未齐」，
    调用方应继续累积；返回 None 表示整段文本里根本没有令牌。

    Args:
        text: 待解析的文本（可能是累积的多片文本）。

    Returns:
        str | None: 任务描述；"" 表示令牌已出现但任务描述尚未完整；None 表示未命中。
    """
    if not text:
        return None
    idx = text.find(CALL_QWEN_TOKEN)
    if idx < 0:
        return None
    after = text[idx + len(CALL_QWEN_TOKEN):]
    # 取首个换行前的内容作为任务（任务通常单行紧凑描述）
    task = after.split("\n", 1)[0].strip()
    return task  # 可能为空串


class EscalationRouter:
    """保留的独立只读路由草案，Gateway 尚未接线。

    与 omni 解耦：持有自己的 LocalBrain 实例（lm_studio / qwen3.6-35b），
    不依赖 omni 的全双工会话；因此可在任意线程独立运行（调用方负责放到后台线程）。
    """

    def __init__(self, backend: str = "lm_studio",
                 lm_studio_model: str = "qwen/qwen3.6-35b-a3b"):
        """核对已加载的大脑实例，不自行加载模型；须由后台线程构造。"""
        self.brain = LocalBrain(backend=backend, lm_studio_model=lm_studio_model)

    def escalate(self, task_text: str, on_progress=None, should_stop=None) -> str:
        """独立只读路由草案；取消传入 HTTP/工具循环，Gateway 尚未调用它。"""
        if not task_text or not task_text.strip():
            return ""
        # 把「升级任务」包装成给大脑的一句话指令：明确可用工具 + 用简体中文回答 boss
        prompt = (
            f"[升级任务] {task_text.strip()}\n"
            "本阶段仅提供系统状态查询，其他操作明确说明暂未开放；"
            "最终用简体中文、口语化一句话告诉 boss 结果。"
        )
        try:
            result_parts = []
            # 最终回答只生成一次；不会再请求第二次答案。
            for chunk in self.brain.run_agentic(
                prompt=prompt,
                tools=[t for t in get_tool_schemas() if t["function"]["name"] == "get_system_info"],
                tool_executor=execute_tool,
                system_prompt=TOOL_SYSTEM_PROMPT,
                temperature=0.3,
                max_tokens=512,
                max_iterations=4,
                should_stop=should_stop,
            ):
                check_cancelled(should_stop)
                if chunk:
                    result_parts.append(chunk)
                    if on_progress is not None:
                        try:
                            on_progress(chunk)
                        except Exception:  # noqa: BLE001
                            pass
            return "".join(result_parts).strip()
        except Exception as e:  # noqa: BLE001
            logger.error("升级路由执行失败: %s", e)
            return ""
