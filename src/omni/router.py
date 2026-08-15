"""M2 升级路由：把 omni 发出的 <<CALL_QWEN>> 令牌转交 qwen3.6-35b + 工具执行。

职责边界（详见项目根 AGENTS.md / 升级计划）：
  - omni（耳朵 + 眼睛 + 嘴巴）遇到「需要联网 / 操作电脑 / 复杂推理 / 调工具」的任务时，
    输出 `<<CALL_QWEN>>{任务}` 交给这里。
  - 本路由用独立的 LocalBrain（lm_studio 上的 qwen3.6-35b-a3b，即「大脑 + 手」）
    跑 agentic 工具循环，把最终结果回传调用方；调用方再经 omni 的 turn_based
    回灌通道（backfeed.py）自然播报给 boss。
  - 本模块不直接发声、不操作 GUI，只产出「给 boss 的结果文本」。
"""
import logging

from src.brain.llm import LocalBrain
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
    """升级路由器：把任务交给 qwen + tools 执行，流式产出最终回答。

    与 omni 解耦：持有自己的 LocalBrain 实例（lm_studio / qwen3.6-35b），
    不依赖 omni 的全双工会话；因此可在任意线程独立运行（调用方负责放到后台线程）。
    """

    def __init__(self, backend: str = "lm_studio",
                 lm_studio_model: str = "qwen/qwen3.6-35b-a3b"):
        """初始化路由器（仅设置大脑后端，不加载模型，首次请求才真正联机）。"""
        self.brain = LocalBrain(backend=backend, lm_studio_model=lm_studio_model)

    def escalate(self, task_text: str, on_progress=None) -> str:
        """执行升级任务，返回最终自然语言结果文本。

        Args:
            task_text: omni 给出的任务描述（一句话）。
            on_progress: 可选回调(text_chunk)，把流式打字机文本推到 GUI / 控制台。

        Returns:
            str: qwen + tools 最终回答（可能为空串，表示执行失败 / 无结果）。
        """
        if not task_text or not task_text.strip():
            return ""
        # 把「升级任务」包装成给大脑的一句话指令：明确可用工具 + 用简体中文回答 boss
        prompt = (
            f"[升级任务] {task_text.strip()}\n"
            "如需操作电脑 / 联网 / 查状态，请调用可用工具；"
            "最终用简体中文、口语化一句话告诉 boss 结果。"
        )
        try:
            result_parts = []
            # run_agentic 是生成器，流式 yield 最终回答文本（打字机效果）
            for chunk in self.brain.run_agentic(
                prompt=prompt,
                tools=get_tool_schemas(),
                tool_executor=execute_tool,
                system_prompt=TOOL_SYSTEM_PROMPT,
                temperature=0.3,
                max_tokens=512,
                max_iterations=4,
            ):
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
