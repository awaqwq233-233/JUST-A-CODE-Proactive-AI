"""M2 独立只读大脑任务：真实工具证据与中文 Markdown 文件交付。"""

import copy
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import os
import tempfile
import time
from uuid import uuid4

from .llm import LocalBrain
from .lm_studio import BrainError, check_cancelled
from src.tools.executor import execute_tool
from src.tools.registry import get_tool_schemas

ROOT = Path(__file__).resolve().parents[2]
SYSTEM_PROMPT = (
    "你是 J.A.C. 本地大脑，称呼用户为 bo s s，输出简体中文。"
    "本阶段只允许查询系统时间、电池、CPU、内存。涉及实时状态必须先调用 get_system_info。"
    "依据工具结果回答，不编造数值，不声称执行了未提供的操作。"
    "保留工具中的数字、时间、单位及测量限制，不把活跃与有线内存当成全部内存占用。"
    "工具失败或无法读取时如实说明。最终回答将保存为报告文件。"
)


@dataclass
class TaskResult:
    """任务交付信息；工具证据只保存到调用者指定的本机目录。"""

    task_id: str
    path: Path
    answer: str
    trace: list
    elapsed_seconds: float


def atomic_write(path, content, should_stop=None, publication_lock=None):
    """UTF-8 临时文件落盘后检查取消，再原子发布；异常清理临时文件。"""
    path = Path(path)
    check_cancelled(should_stop)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".pending-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        with publication_lock if publication_lock is not None else nullcontext():
            check_cancelled(should_stop)
            os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class BrainTaskRunner:
    """只给模型一项系统查询工具；文件路径由程序生成，不由模型决定。"""

    def __init__(self, brain=None, output_dir=None):
        """绑定大脑与本机输出目录，默认目录已被 Git 忽略。"""
        self.brain = brain if brain is not None else LocalBrain(backend="lm_studio")
        self.output_dir = Path(output_dir or ROOT / "output/m2/qwen").resolve()

    def run(self, task, should_stop=None, info_types=None, publication_lock=None, on_tool_result=None):
        """执行显式文本任务；错误、取消或无真实工具证据均不发布文件。"""
        if not isinstance(task, str) or not task.strip() or len(task) > 4000:
            raise ValueError("任务文字须为 1–4000 字")
        check_cancelled(should_stop)
        started, task_id, trace = time.monotonic(), uuid4().hex, []
        schema = copy.deepcopy(next(t for t in get_tool_schemas() if t["function"]["name"] == "get_system_info"))
        if info_types is not None:
            permitted = set(schema["function"]["parameters"]["properties"]["info_type"]["enum"])
            if not info_types or not set(info_types) <= permitted:
                raise ValueError("系统查询类型无效")
            schema["function"]["parameters"]["properties"]["info_type"]["enum"] = list(info_types)
            schema["function"]["parameters"]["required"] = ["info_type"]

        def execute(name, arguments):
            """记录实际调用结果；每项执行前再次核对取消及只读白名单。"""
            check_cancelled(should_stop)
            if name != "get_system_info":
                raise BrainError("此阶段不允许操作电脑或执行命令")
            output = execute_tool(name, arguments)
            if output.startswith(("错误：", "工具 ")):
                raise BrainError("系统查询工具执行失败")
            check_cancelled(should_stop)
            entry = dict(name=name, arguments=dict(arguments), output=output,
                         queried_at=datetime.now().astimezone().isoformat(timespec="seconds"))
            trace.append(entry)
            if on_tool_result is not None:
                on_tool_result(copy.deepcopy(entry))
            return output

        answer = "".join(self.brain.run_agentic(
            task, [schema], execute, system_prompt=SYSTEM_PROMPT,
            max_tokens=1536, max_iterations=4, should_stop=should_stop, timeout=120,
        )).strip()
        check_cancelled(should_stop)
        if not trace or not answer:
            raise BrainError("未完成真实工具查询，不能发布状态报告")
        if len(answer) > 20000:
            raise BrainError("报告超过长度上限")
        evidence = "\n\n".join(f"### 查询 {i}\n\n工具：{entry['name']}\n\n"
                                f"参数：{entry['arguments']}\n\n查询时间：{entry['queried_at']}\n\n{entry['output']}"
                                for i, entry in enumerate(trace, 1))
        content = (f"# J.A.C. 系统状态报告\n\n"
                   f"任务编号：{task_id}\n\n生成时间：{datetime.now().astimezone().isoformat(timespec='seconds')}\n\n"
                   f"## 请求\n\n{task.strip()}\n\n## 大脑回答\n\n{answer}\n\n"
                   f"## 实际工具返回\n\n{evidence}\n\n"
                   "数据仅代表查询时刻。内存数值为活跃与有线页合计，不代表完整内存占用。\n")
        path = self.output_dir / f"system-status-{task_id}.md"
        atomic_write(path, content, should_stop, publication_lock)
        return TaskResult(task_id, path, answer, trace, time.monotonic() - started)
