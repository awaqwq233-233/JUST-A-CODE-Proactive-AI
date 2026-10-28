"""OpenClaw 适配契约：未接入时仅生成本地交接单，绝不伪造提交或手机送达。"""
from dataclasses import asdict, dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class CloudTask:
    """未来云端提交的最小载荷；附件须逐项获准，不携带整段历史或隐式凭据。"""
    task_id: str
    instruction: str
    reason: str
    source: dict
    attachments: tuple = ()
    delivery: dict = field(default_factory=lambda: {"channel": "local", "recipient": None})
    schema_version: int = 1

    def payload(self):
        """返回独立序列化副本，服务端以 task_id 做幂等键。"""
        return asdict(self)


@dataclass(frozen=True)
class CloudReceipt:
    """远端接收、执行和交付是三个状态；无回执不得认定完成。"""
    task_id: str
    status: str
    remote_id: str | None = None
    artifacts: tuple = ()
    delivery_status: str = "not_sent"
    detail: str = ""


class OpenClawAdapter(Protocol):
    """J.A.C. 自有适配层接口，非假定上游 HTTP 端点；未来绑定已部署版本。"""

    def submit(self, task: CloudTask, should_stop=None) -> CloudReceipt:
        """使用幂等任务号提交已授权载荷，超时不盲目重试有副作用任务。"""
        ...

    def status(self, task_id: str, remote_id: str, should_stop=None) -> CloudReceipt:
        """查询真实任务和交付回执，不用模型文字推定完成。"""
        ...

    def cancel(self, task_id: str, remote_id: str) -> CloudReceipt:
        """请求撤销尚未执行的工作；不保证撤回已经交付的动作。"""
        ...


class UnavailableOpenClaw:
    """默认关闭的云端边界，不发网络请求、不后台自动补交。"""

    def submit(self, task, should_stop=None):
        """只返回未接入状态，私有材料不离开本机。"""
        from src.brain.lm_studio import check_cancelled
        check_cancelled(should_stop)
        return CloudReceipt(task.task_id, "deferred", detail="OpenClaw 尚未接入，任务未提交，手机未发送")

    def status(self, task_id, remote_id, should_stop=None):
        """未接入时没有可查询的远端任务。"""
        return CloudReceipt(task_id, "deferred", detail="没有远端接收回执")

    def cancel(self, task_id, remote_id):
        """未提交的任务无需远端撤销，也不假造取消回执。"""
        return CloudReceipt(task_id, "deferred", detail="任务未提交到远端")
