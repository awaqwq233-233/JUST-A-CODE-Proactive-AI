"""本阶段独立权限域：两个 UTF-8 输入、内存草稿与程序核验后发布。"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
from pathlib import Path
import stat
import threading
import time


class AgentRefused(RuntimeError):
    """任务取消、预算或授权边界不满足；不得发布成功文件。"""


def open_directory(path, create=False):
    """逐级 NOFOLLOW 打开绝对目录，拒绝目录符号链接和不支持的平台。"""
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
        raise AgentRefused("独立 Agent 文件工具首版仅支持 POSIX；Windows 尚未开放")
    absolute = Path(os.path.abspath(path))
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in absolute.parts[1:]:
            if create:
                try:
                    os.mkdir(component, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def atomic_json(path, data):
    """将程序拥有的私有状态以 UTF-8 原子替换，避免中断留下半份 JSON。"""
    path = Path(path)
    tmp = path.with_suffix(".pending")
    with tmp.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def literal_markdown(text):
    """将模型摘要和输入文件名作为文字展示，避免生成链接或 HTML。"""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\`*_[\]!])", r"\\\1", text)


def source_diff(before, after):
    """逐行比较并显式标记缺少末尾换行，防止相邻增删行拼接。"""
    lines = difflib.unified_diff(before["text"].splitlines(keepends=True),
                                 after["text"].splitlines(keepends=True),
                                 fromfile=before["filename"], tofile=after["filename"])
    return "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
                   for line in lines)


class TaskBoundary:
    """授权、取消和工具证据共用锁；报告草稿永远不由模型选择落盘路径。"""

    def __init__(self, workspace, before, after, task_id, timeout=120, should_stop=None):
        """绑定精确输入名、独立任务身份和总时限，固定读取目录句柄。"""
        if not 0 < timeout <= 300:
            raise ValueError("任务时限须在 0–300 秒内")
        for name in (before, after):
            if (not isinstance(name, str) or not name or name in {".", ".."}
                    or "/" in name or "\\" in name or any(ord(c) < 32 for c in name)):
                raise AgentRefused("输入须为指定工作目录中的单个文件名")
        if before == after:
            raise AgentRefused("请选择两份不同的输入文件")
        self.workspace = str(Path(workspace).absolute())
        self.names = {"before": before, "after": after}
        self.task_id, self.timeout = task_id, timeout
        self.started = time.monotonic()
        self.deadline = self.started + timeout
        self.lock = threading.RLock()
        self.cancel_reason = None
        self.should_stop = should_stop
        self.reads, self.calls, self.trace = {}, {}, []
        self.diff, self.summary = None, None
        self.fd = open_directory(workspace)
        self.closed = False

    def close(self):
        """关闭本任务唯一读取句柄，不删除用户输入。"""
        with self.lock:
            if not self.closed:
                os.close(self.fd)
                self.closed = True

    def cancel(self, reason="cancelled"):
        """同锁撤销后续工具和发布；已经发布的文件不回滚。"""
        with self.lock:
            self.cancel_reason = self.cancel_reason or reason

    def check(self):
        """在工具读取、草稿和最终发布前统一检查任务存活。"""
        if self.should_stop is not None:
            try:
                if self.should_stop():
                    self.cancel_reason = self.cancel_reason or "cancelled"
            except Exception:
                self.cancel_reason = self.cancel_reason or "cancelled"
        if time.monotonic() >= self.deadline:
            self.cancel_reason = self.cancel_reason or "timeout"
        if self.cancel_reason or self.closed:
            raise AgentRefused(self.cancel_reason or "closed")

    def read_source(self, source):
        """只读取授权叶子文件，拒绝链接、非普通文件、超限和非法编码。"""
        fd = os.open(self.names[source], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=self.fd)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > 768:
                raise AgentRefused("输入必须是每份不超过 768 UTF-8 字节的普通文本")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                raw = stream.read(769)
            if len(raw) > 768 or b"\x00" in raw:
                raise AgentRefused("输入超过预算或包含 NUL")
            text = raw.decode("utf-8-sig")
            return {"source": source, "filename": self.names[source], "text": text,
                    "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
        finally:
            os.close(fd)

    def execute(self, request):
        """验证任务/调用身份和精确参数；重复 ID 复用同一结果，不重复动作。"""
        with self.lock:
            self.check()
            if not isinstance(request, dict) or set(request) != {"name", "args", "call_id", "session_id"}:
                raise AgentRefused("非法工具请求结构")
            name, args, call_id = request["name"], request["args"], request["call_id"]
            if request["session_id"] != self.task_id:
                raise AgentRefused("工具请求不属于本任务")
            if not isinstance(call_id, str) or not 1 <= len(call_id) <= 160 or not isinstance(args, dict):
                raise AgentRefused("工具调用 ID 或参数非法")
            encoded = json.dumps([name, args], sort_keys=True, ensure_ascii=False)
            if call_id in self.calls:
                prior, result = self.calls[call_id]
                if prior != encoded:
                    raise AgentRefused("同一调用 ID 的参数发生变化")
                if result.get("status") == "failed":
                    raise AgentRefused(result["error"])
                return result
            if len(self.trace) >= 8:
                raise AgentRefused("超过 8 次工具调用预算")
            entry = {"call_id": call_id, "tool": name, "arguments": args,
                     "at": time.time(), "status": "started"}
            self.trace.append(entry)
            try:
                if name == "jac_read_text" and set(args) == {"source"} and args["source"] in self.names:
                    if self.diff is not None:
                        raise AgentRefused("比较完成后不得改变读取证据")
                    result = self.read_source(args["source"])
                    self.reads[args["source"]] = result
                elif name == "jac_compare_texts" and not args:
                    if self.summary is not None:
                        raise AgentRefused("草稿提交后不得再次比较")
                    if set(self.reads) != set(self.names):
                        raise AgentRefused("必须先读取两份输入")
                    self.diff = source_diff(self.reads["before"], self.reads["after"])
                    result = {"diff": self.diff, "identical": not self.diff,
                              "hashes": {key: value["sha256"] for key, value in self.reads.items()}}
                elif name == "jac_stage_report" and set(args) == {"summary"}:
                    summary = args["summary"]
                    if (self.diff is None or not isinstance(summary, str) or not summary.strip()
                            or len(summary.encode("utf-8")) > 1200 or self.summary is not None):
                        raise AgentRefused("草稿缺少差异依据、为空、超限或已提交")
                    self.summary = summary
                    result = {"status": "staged", "published": False, "task_id": self.task_id}
                else:
                    raise AgentRefused("工具或参数超出本任务授权")
                self.check()
                entry.update(status="completed", result=result)
                self.calls[call_id] = (encoded, result)
                return result
            except Exception as error:
                entry.update(status="failed", error=str(error))
                # 失败调用也消费 ID，避免恢复后重复尝试同一个不确定动作。
                self.calls[call_id] = (encoded, {"error": str(error), "status": "failed"})
                raise

    def evidence(self, status, **extra):
        """记录来源、权限和真实工具证据，不以模型最终文本认定成功。"""
        return {"task_id": self.task_id, "status": status, "workspace": self.workspace,
                "allowed_files": self.names, "tools": self.trace,
                "elapsed_seconds": round(time.monotonic() - self.started, 3), **extra}

    def publish(self, output, worker_result):
        """后置核对输入、终态及证据后原子发布整个目录，取消与发布互斥。"""
        with self.lock:
            self.check()
            if (worker_result.get("finish_reason") != "completed"
                    or worker_result.get("session_id") != self.task_id or self.summary is None
                    or self.diff is None or set(self.reads) != set(self.names)):
                raise AgentRefused("任务没有完整证据，拒绝发布")
            for source in self.names:
                if self.read_source(source)["sha256"] != self.reads[source]["sha256"]:
                    raise AgentRefused("输入在执行期间已改变，拒绝发布过期比较")
            # output 是调用者显式指定的报告目录；模型从未获得这个路径参数。
            output = Path(output).absolute()
            parent = open_directory(output, create=True)
            pending = "." + self.task_id + ".pending"
            child, committed = None, False
            try:
                os.mkdir(pending, mode=0o700, dir_fd=parent)
                child = os.open(pending, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                fence = "`" * max(3, max((len(x) + 1 for x in re.findall(r"`+", self.diff)), default=3))
                safe_summary = literal_markdown(self.summary)
                report = ("# 文本比较报告\n\n## 模型摘要（依据下方工具差异）\n\n" + safe_summary
                          + "\n\n## 程序生成的逐行差异\n\n" + fence + "diff\n" + (self.diff or "两份文本相同。\n")
                          + "\n" + fence + "\n\n## 输入证据\n\n" + "\n".join(
                              f"- {literal_markdown(value['filename'])}：`{value['sha256']}`"
                              for value in self.reads.values()) + "\n")
                files = {"report.md": report, "evidence.json": json.dumps(
                    self.evidence("completed", runtime=worker_result), ensure_ascii=False, indent=2)}
                for name, content in files.items():
                    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=child)
                    with os.fdopen(fd, "w", encoding="utf-8") as stream:
                        stream.write(content)
                        stream.flush()
                        os.fsync(stream.fileno())
                self.check()
                os.fsync(child)
                # task_id 由程序随机创建；已有交付绝不覆盖。
                if self.task_id in os.listdir(parent):
                    raise AgentRefused("任务报告已存在，拒绝重复发布")
                os.rename(pending, self.task_id, src_dir_fd=parent, dst_dir_fd=parent)
                committed = True
                os.fsync(parent)
            except BaseException:
                if child is not None and not committed:
                    for name in ("report.md", "evidence.json"):
                        try:
                            os.unlink(name, dir_fd=child)
                        except FileNotFoundError:
                            pass
                    os.rmdir(pending, dir_fd=parent)
                raise
            finally:
                if child is not None:
                    os.close(child)
                os.close(parent)
            return output / self.task_id / "report.md"
