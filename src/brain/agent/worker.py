"""独立 SDK 子进程；父进程负责总时限、工具授权与最终发布。"""
from importlib.metadata import version
import hashlib
import json
from pathlib import Path
import sys

from .profile import ROOT, VERSION
from pydantic import BaseModel


class ShutdownAck(BaseModel):
    """官方 shutdown 空对象确认，用于核验持久化完成。"""



def main():
    """从私有配置启动固定 SDK；恢复模式只读取历史，不发送任务或重放工具。"""
    from deepseek_harness import DeepSeekHarness
    lock = json.loads((ROOT / "agent.lock.json").read_text(encoding="utf-8"))
    plugin = lock["plugin"]
    if hashlib.sha256((ROOT / plugin["path"]).read_bytes()).hexdigest() != plugin["sha256"]:
        raise RuntimeError("Agent 插件哈希不匹配")
    config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    if any(version(name) != VERSION for name in
           ("deepseek-harness-sdk", "deepseek-harness-runtime-bin")):
        raise RuntimeError("Harness SDK/runtime 版本不匹配，请重新执行独立安装阶段")
    session_id = config.pop("session_id")
    restore_only = config.pop("restore_only", False)
    with DeepSeekHarness(**config) as harness:
        if restore_only:
            session = harness.resume_session(session_id)
            result = {"status": "restored", "session_id": session.id,
                      "cwd": session.get_working_directory(), "actions_replayed": False}
        else:
            run = harness.run("比较授权的 before 和 after 文本：先分别读取，再比较实际差异，"
                              "最后调用 jac_stage_report 提交简洁中文摘要。", session_id=session_id)
            result = {"finish_reason": run.finish_reason, "session_id": run.session_id,
                      "event_count": len(run.events),
                      "steps": sum(event.get("type") == "step/start" for event in run.events),
                      "terminal": [event.get("data") for event in run.events if event.get("type") == "turn/end"]}
        harness.client.request("shutdown", None, response_model=ShutdownAck, timeout_seconds=8)
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
