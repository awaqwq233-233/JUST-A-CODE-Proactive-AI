"""
J.A.C. 持久记忆子系统 —— 存储层 MemoryStore（Phase 1，v1.0.0）

职责（见 docs/memory/schema.md + 设计文档「存储格式」「可运维性」章节）：
  - 目录解析（显式 base_dir → 环境变量 JAC_MEMORY_DIR → 用户目录）
  - 启动时一次性加载进内存 dict（内存为权威副本）
  - 检索只读内存（亚毫秒），落盘全部交给后台持久化线程（防抖批量写）
  - 原子写（tmp → fsync → os.replace）+ 保留 .bak 用于损坏恢复
  - 版本兼容：version 缺失或 "0.0.0" 当宽松加载；MAJOR 不符抛 MemoryVersionIncompatible
  - 单条缺必填字段 → 容忍跳过并计入 invalid_facts
  - compaction（ttl 过期 / 低权重久未访问归档 / 按 content 哈希去重）
  - 导出 / 两级清空（Tier1 逻辑删除防复活；Tier2 secure 取证级覆写）

顶层信封（v1.0.0）：
    {"version": "1.0.0", "facts": [ {MemoryFact}, ... ]}

线程安全约定：
  - 所有内存读写都持有 ``self._lock``。
  - 唯一的「序列化 → 写盘」代码路径是 ``_do_flush``，由 ``self._flush_lock``
    串行化，保证任意时刻只有一个写者（后台线程为主，``flush()`` 同步强制落盘
    也走同一条路径），不会出现并发写文件。

仅依赖 Python 标准库。
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from .models import (
    MemoryFact,
    MemoryKind,
    MemorySource,
    RetrievalResult,
    _now_iso,
)


# ---------------------------------------------------------------------------
# 常量与异常
# ---------------------------------------------------------------------------

CURRENT_VERSION = "1.0.0"           # semver，MAJOR 不符即拒载
DEFAULT_MAX_FACTS = 2000
DEFAULT_MAX_BYTES = 2_000_000
DEFAULT_FLUSH_INTERVAL = 5.0
FLUSH_BATCH = 10                     # 累计 N 次写信号即提前落盘（防抖上限）
ARCHIVE_IDLE_DAYS = 90              # 久未访问低权重项归档阈值（天）

# 归档留存上限（Rex 定稿，SRE blocker C）
MAX_ARCHIVE_FILES = 12              # 归档文件数上限（按 YYYYMM，约一年）
ARCHIVE_RETENTION_DAYS = 365       # 归档保留天数上限
MAX_ARCHIVE_BYTES = 10_000_000     # 归档总体积上限（约 10MB）

FILE_MODE = 0o600                  # 文件权限（Rex 定稿）：0o700 目录 + 0o600 文件

_STORE_FILENAME = "memory.json"
_BAK_SUFFIX = ".bak"
_TMP_SUFFIX = ".tmp"
_CORRUPT_PREFIX = ".corrupt."

# 简单停用词（中英文），分词后剔除，降低噪声。可按需扩充。
_STOPWORDS = frozenset(
    "的 了 是 我 你 他 她 它 我们 你们 他们 这 那 这个 那个 有 在 和 与 也 都 就 不 没 吗 呢 吧 啊 呀 "
    "把 被 给 对 从 到 让 要 会 能 可以 一个 一些 这种 那种 自己 什么 怎么 怎样 如何 其实 因为 所以 "
    "a an the and or but is are was were be to of in on for with this that you i he she it we they "
    "do does did have has had will would can could should my your his her our their not no yes"
    .split()
)

# 拉丁/数字词 + CJK 单字
_TOKEN_RE = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]")


class MemoryFileCorrupt(Exception):
    """memory.json 结构损坏（JSON 解析失败 / 信封非法）。"""


class MemoryVersionIncompatible(Exception):
    """memory.json 的 version MAJOR 与当前代码不符，拒绝加载。"""


@dataclass
class LoadReport:
    """加载结果（schema.md §9）：有效 facts + 被跳过条目的原因列表。"""

    facts: list[MemoryFact] = field(default_factory=list)
    invalid_facts: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# MemoryStore
# ---------------------------------------------------------------------------

class MemoryStore:
    """单文件 JSON 长期记忆存储（内存权威 + 后台防抖落盘，v1.0.0）。"""

    def __init__(
        self,
        base_dir: Optional[str] = None,
        max_facts: int = DEFAULT_MAX_FACTS,
        max_bytes: int = DEFAULT_MAX_BYTES,
        flush_interval: float = DEFAULT_FLUSH_INTERVAL,
    ) -> None:
        self.base_dir: str = self._resolve_dir(base_dir)
        self._store_path: str = os.path.join(self.base_dir, _STORE_FILENAME)
        self.max_facts = max_facts
        self.max_bytes = max_bytes
        self.flush_interval = flush_interval

        # 内存权威副本 + 独立锁
        self._facts: dict[str, MemoryFact] = {}
        self._lock = threading.Lock()

        # 后台 flush 线程相关
        self._flush_queue: "queue.Queue[Optional[object]]" = queue.Queue()
        self._flush_lock = threading.Lock()      # 串行化唯一写盘路径
        self._dirty = False                       # 内存是否领先于磁盘
        self._shutdown = threading.Event()
        self._pending = 0                         # 仅在 flush 线程内访问
        self._last_flush = 0.0                    # 仅在 flush 线程内访问

        self.last_load_report: Optional[LoadReport] = None

        # 启动时加载（v1 单文件，MAJOR 不符会向上抛，由调用方处理）
        self.load()

        # 启动后台持久化线程（daemon，不阻塞进程退出）
        self._flush_thread = threading.Thread(
            target=self._flush_loop, name="memory-flush", daemon=True
        )
        self._flush_thread.start()

    # ------------------------- 目录解析 -------------------------

    @staticmethod
    def _resolve_dir(base_dir: Optional[str]) -> str:
        """按优先级解析存储目录：显式参数 → 环境变量 → 用户目录。

        用户目录：Windows 用 ``%APPDATA%/jac/memory``，macOS/Linux 用 ``~/.jac/memory``。
        创建目录并尽力设置 0o700 权限。
        """
        if base_dir:
            path = os.path.abspath(base_dir)
        elif os.environ.get("JAC_MEMORY_DIR"):
            path = os.path.abspath(os.environ["JAC_MEMORY_DIR"])
        elif os.name == "nt":
            appdata = os.environ.get("APPDATA") or os.path.expandvars("%APPDATA%")
            path = os.path.join(appdata, "jac", "memory")
        else:
            path = os.path.expanduser("~/.jac/memory")

        os.makedirs(path, exist_ok=True)
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass  # 权限不足时降级而非崩溃
        return path

    # ------------------------- 版本解析 -------------------------

    @staticmethod
    def _parse_version(v: str) -> tuple[int, int, int]:
        """把 semver 字符串解析为 (major, minor, patch) 整数三元组。"""
        parts = (v or "0.0.0").split(".")
        while len(parts) < 3:
            parts.append("0")
        try:
            return (int(parts[0]), int(parts[1]), int(parts[2]))
        except ValueError:
            return (0, 0, 0)

    # ------------------------- 加载 / 序列化 -------------------------

    def load(self) -> LoadReport:
        """从 ``memory.json`` 加载到内存（v1.0.0 契约）。

        损坏恢复策略（schema.md §8）：
          - 文件不存在 → 空库。
          - 主文件解析失败 → 回退 ``memory.json.bak``；仍失败 → 隔离主文件并
            以空库启动（安全重建）。
          - 主文件损坏 + 无 .bak → 抛 ``MemoryFileCorrupt``（向上传播）。
        版本策略（schema.md §7）：
          - version 缺失 / 为 "0.0.0" → 宽松加载（视为旧版），下次写入升级。
          - MAJOR 不符（且非 0）→ 抛 ``MemoryVersionIncompatible``，绝不静默改写。
        """
        if not os.path.exists(self._store_path):
            with self._lock:
                self._facts = {}
                self._dirty = False
            report = LoadReport(facts=[], invalid_facts=[])
            self.last_load_report = report
            return report

        try:
            data = self._read_envelope(self._store_path)
        except MemoryFileCorrupt:
            # 主文件损坏，尝试回退 .bak
            try:
                data = self._read_envelope(self._store_path + _BAK_SUFFIX)
            except MemoryFileCorrupt:
                # 主文件损坏 + 无有效 .bak → 向上抛（schema §8 要求）
                self._quarantine_corrupt(self._store_path)
                with self._lock:
                    self._facts = {}
                    self._dirty = False
                raise

        # 版本兼容（schema.md §7）：缺失或 "0.0.0" 均当宽松旧版处理
        version = data.get("version")  # 缺失时为 None
        cur_major = self._parse_version(CURRENT_VERSION)[0]
        file_major = self._parse_version(version or "0.0.0")[0]
        if file_major not in (0, cur_major):
            raise MemoryVersionIncompatible(
                f"memory.json version {version!r} 与当前 MAJOR {cur_major} 不兼容，拒绝加载"
            )

        report = self._parse_facts(data)
        with self._lock:
            self._facts = {f.id: f for f in report.facts}
            self._dirty = False
        self.last_load_report = report
        return report

    @staticmethod
    def _read_envelope(path: str) -> dict:
        """读取并校验顶层信封。非 dict / 缺 facts / facts 非 list 均视为损坏，抛 MemoryFileCorrupt。"""
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            raise MemoryFileCorrupt(f"无法解析 {path}: {e}") from e
        if not isinstance(data, dict):
            raise MemoryFileCorrupt("store envelope must be a JSON object")
        if "facts" not in data:
            raise MemoryFileCorrupt("store envelope missing 'facts'")
        if not isinstance(data["facts"], list):
            raise MemoryFileCorrupt("'facts' must be a list")
        return data

    def _parse_facts(self, data: dict) -> LoadReport:
        """把 envelope.facts 解析为 facts + invalid_facts（schema.md §9）。

        必填 6 字段缺失 → 计入 invalid_facts 并跳过；可选字段缺失 → 套默认值。
        """
        facts: list[MemoryFact] = []
        invalid: list[dict] = []
        for raw in data.get("facts", []):
            if not isinstance(raw, dict):
                invalid.append({"id": None, "reason": "not_an_object"})
                continue
            missing = [k for k in ("id", "content", "kind", "source", "created_at", "updated_at") if k not in raw]
            if missing:
                invalid.append({"id": raw.get("id"), "reason": f"missing_required:{','.join(missing)}"})
                continue
            try:
                fact = MemoryFact.from_dict(raw)
            except Exception:
                invalid.append({"id": raw.get("id"), "reason": "parse_error"})
                continue
            facts.append(fact)
        return LoadReport(facts=facts, invalid_facts=invalid)

    @staticmethod
    def _quarantine_corrupt(path: str) -> None:
        """把损坏文件隔离为 ``<name>.corrupt.<timestamp>``，避免静默丢弃。"""
        try:
            ts = datetime.now().strftime("%Y%m%d%H%M%S")
            os.replace(path, path + _CORRUPT_PREFIX + ts)
            print(f"[MemoryStore] 损坏文件已隔离: {os.path.basename(path)}{_CORRUPT_PREFIX}{ts}")
        except OSError as e:
            print(f"[MemoryStore] 无法隔离损坏文件: {e}")

    def _serialize(self, facts: list[MemoryFact]) -> dict:
        """构造顶层信封 dict（v1.0.0：仅 version + facts）。"""
        return {
            "version": CURRENT_VERSION,
            "facts": [f.to_dict() for f in facts],
        }

    # ------------------------- 原子写 -------------------------

    def _write_atomic(self, path: str, data: dict) -> None:
        """原子写（主文件用）：写 tmp → fsync → os.replace 覆盖。

        替换前先把当前好文件复制为 ``.bak``（仅当存在且为合法 JSON），
        用于崩溃恢复。归档副本请改用 ``_write_atomic_no_backup`` 以免产生
        ``memory_archive_*.json.bak`` 噪声。
        """
        tmp_path = path + _TMP_SUFFIX
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    json.load(f)  # 仅做合法性校验，不消费内容
                self._copy_file(path, path + _BAK_SUFFIX)
            except (OSError, ValueError):
                pass  # 当前文件已损坏则不覆盖 .bak
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        try:
            os.chmod(path, FILE_MODE)
        except OSError:
            pass

    def _write_atomic_no_backup(self, path: str, data: dict) -> None:
        """原子写但不产生 ``.bak`` 副本（用于 .bak 自身重写、归档重写）。"""
        tmp_path = path + _TMP_SUFFIX
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        try:
            os.chmod(path, FILE_MODE)
        except OSError:
            pass

    @staticmethod
    def _copy_file(src: str, dst: str) -> None:
        """标准库内的文件复制（避免引入 shutil）。"""
        with open(src, "rb") as fin, open(dst, "wb") as fout:
            while True:
                chunk = fin.read(65536)
                if not chunk:
                    break
                fout.write(chunk)

    # ------------------------- 后台 flush 线程 -------------------------

    def _flush_loop(self) -> None:
        """Daemon 线程：从队列取写信号，防抖批量落盘。

        触发条件：脏数据 且（累计信号 >= FLUSH_BATCH 或距上次落盘 >= flush_interval）。
        收到 shutdown 信号后做一次最终落盘再退出。
        """
        self._last_flush = time.monotonic()
        while not self._shutdown.is_set():
            try:
                self._flush_queue.get(timeout=self.flush_interval)
                self._pending += 1
            except queue.Empty:
                pass  # 超时（区间到达）
            now = time.monotonic()
            elapsed = now - self._last_flush
            if self._dirty and (
                self._pending >= FLUSH_BATCH or elapsed >= self.flush_interval
            ):
                self._do_flush()
                self._pending = 0
                self._last_flush = now
        # shutdown：最终落盘
        if self._dirty:
            self._do_flush()

    def _request_flush(self) -> None:
        """入队一个写信号（不阻塞热路径）。"""
        try:
            self._flush_queue.put_nowait(None)
        except Exception:
            pass

    def _do_flush(self) -> None:
        """唯一的「序列化 → 写盘」路径，由 ``_flush_lock`` 串行化。"""
        with self._flush_lock:
            with self._lock:
                if not self._dirty:
                    return
                facts = list(self._facts.values())
                # 快照后即标记已落盘：若本周期内发生并发 upsert，它会把 dirty
                # 重新置 True 并入队信号，从而触发下一次 flush，避免丢写。
                self._dirty = False
            data = self._serialize(facts)
            try:
                self._write_atomic(self._store_path, data)
            except OSError as e:
                # 磁盘满 / 权限不足：恢复 dirty 标记以便下次周期重试，不崩溃
                print(f"[MemoryStore] 落盘失败（将在下次重试）: {e}")
                with self._lock:
                    self._dirty = True
                return

    def flush(self) -> bool:
        """同步强制落盘：清空队列并立即写盘（供 SLEEP / 退出时调用）。"""
        while True:
            try:
                self._flush_queue.get_nowait()
            except queue.Empty:
                break
        self._do_flush()
        return True

    def close(self) -> None:
        """停止后台线程并做最终落盘（资源清理）。"""
        self._shutdown.set()
        self.flush()
        if self._flush_thread.is_alive():
            self._flush_thread.join(timeout=self.flush_interval + 1.0)

    # ------------------------- CRUD -------------------------

    def upsert(self, fact: MemoryFact) -> MemoryFact:
        """插入或更新一条记忆。同 id 时合并（见下方合并语义），并触发后台落盘。"""
        now = _now_iso()
        with self._lock:
            existing = self._facts.get(fact.id)
            if existing is not None:
                # 合并语义：最新字段覆盖；tags 取并集；updated_at 刷新；
                # created_at / id 保留（kind 以新值为准）。
                existing.content = fact.content
                existing.kind = fact.kind
                existing.source = fact.source
                existing.weight = fact.weight
                existing.pii = fact.pii
                existing.ttl = fact.ttl
                existing.embedding = fact.embedding
                existing.tags = sorted(set(existing.tags) | set(fact.tags))
                existing.updated_at = now
            else:
                self._facts[fact.id] = fact
            self._dirty = True
        self._request_flush()
        return fact

    def get(self, id: str) -> Optional[MemoryFact]:
        """按 id 读取（纯读，不 bump heat）。未命中返回 None。"""
        with self._lock:
            return self._facts.get(id)

    def delete(self, id: str) -> bool:
        """按 id 删除（内存 + 磁盘 + 副本一致性清除，防复活）。成功返回 True。"""
        with self._lock:
            if id in self._facts:
                del self._facts[id]
                self._dirty = True
                deleted = True
            else:
                deleted = False
        if deleted:
            self.flush()
            self._purge_replicas(matched_ids={id}, secure=False)
        return deleted

    # ------------------------- 检索 -------------------------

    def query_by_keywords(
        self, text: str, k: int = 5, window_text: Optional[str] = None
    ) -> list[RetrievalResult]:
        """关键词检索（v1 无 embedding）。

        对用户文本（+ 可选最近窗口）与每条 fact 的 ``content + tags`` 做分词重叠
        打分，叠加 weight 与 recency（updated_at）权重，返回按 score 降序的
        top-K。空结果返回 ``[]``。命中会 bump ``updated_at``（heat 折叠）。
        """
        query_tokens = self._tokenize(text)
        if window_text:
            query_tokens |= self._tokenize(window_text)
        if not query_tokens:
            return []

        with self._lock:
            candidates = list(self._facts.values())

        results: list[RetrievalResult] = []
        for fact in candidates:
            fact_tokens = self._tokenize(fact.content + " " + " ".join(fact.tags))
            if not fact_tokens:
                continue
            overlap = query_tokens & fact_tokens
            if not overlap:
                continue
            keyword_score = len(overlap) / max(1.0, len(query_tokens))
            weight_score = fact.weight
            recency_score = self._recency_score(fact)
            score = keyword_score * 1.0 + weight_score * 0.3 + recency_score * 0.3
            results.append(RetrievalResult(fact=fact, score=round(score, 4)))

        results.sort(key=lambda r: r.score, reverse=True)
        top = results[:k]

        if top:
            now_iso = _now_iso()
            with self._lock:
                for r in top:
                    r.fact.updated_at = now_iso  # heat 折叠进 updated_at
                self._dirty = True
            self._request_flush()
        return top

    def query_by_tags(self, tags: list[str], k: int = 5) -> list[RetrievalResult]:
        """按标签检索：统计命中标签数，叠加 weight / recency 权重，返回 top-K。"""
        if not tags:
            return []
        wanted = set(tags)
        with self._lock:
            candidates = list(self._facts.values())

        results: list[RetrievalResult] = []
        for fact in candidates:
            matched = wanted & set(fact.tags)
            if not matched:
                continue
            tag_score = len(matched) / max(1, len(wanted))
            weight_score = fact.weight
            recency_score = self._recency_score(fact)
            score = tag_score * 1.0 + weight_score * 0.3 + recency_score * 0.3
            results.append(RetrievalResult(fact=fact, score=round(score, 4)))

        results.sort(key=lambda r: r.score, reverse=True)
        top = results[:k]

        if top:
            now_iso = _now_iso()
            with self._lock:
                for r in top:
                    r.fact.updated_at = now_iso
                self._dirty = True
            self._request_flush()
        return top

    # ------------------------- 压缩 / 导出 / 清空 -------------------------

    def compact(self) -> bool:
        """压缩：当 ``len(facts) > max_facts`` 或序列化大小 > ``max_bytes`` 触发。

        步骤：
          1. 丢弃 ttl 过期项；
          2. 归档久未访问的低权重项到 ``memory_archive_YYYYMM.json``；
          3. 按 content 哈希合并重复（保留较新 updated_at、合并 tags、取较大 weight）。
        压缩后顺带 flush。返回是否执行了压缩。
        """
        with self._lock:
            facts = list(self._facts.values())
        size = self._estimate_size(facts)
        if len(facts) <= self.max_facts and size <= self.max_bytes:
            return False

        now = datetime.now(timezone.utc)
        alive: list[MemoryFact] = []
        archived: list[MemoryFact] = []
        ttl_dropped = 0

        # 1. ttl 过期丢弃
        for fact in facts:
            if fact.ttl:
                try:
                    if now >= datetime.fromisoformat(fact.ttl.replace("Z", "+00:00")):
                        ttl_dropped += 1
                        continue
                except ValueError:
                    pass
            alive.append(fact)

        # 2. 归档久未访问的低权重项（保留其余进入 kept）
        kept: list[MemoryFact] = []
        for fact in alive:
            idle_days = self._age_days(fact.updated_at or fact.created_at, now)
            if fact.weight <= 0.3 and idle_days > ARCHIVE_IDLE_DAYS:
                archived.append(fact)
            else:
                kept.append(fact)

        # 3. 按 content 哈希合并重复
        merged: dict[str, MemoryFact] = {}
        for fact in kept:
            key = hashlib.sha256(fact.content.strip().encode("utf-8")).hexdigest()
            prev = merged.get(key)
            if prev is not None:
                if fact.updated_at > prev.updated_at:
                    prev.updated_at = fact.updated_at
                prev.tags = sorted(set(prev.tags) | set(fact.tags))
                prev.weight = max(prev.weight, fact.weight)
            else:
                merged[key] = fact

        with self._lock:
            self._facts = {f.id: f for f in merged.values()}
            self._dirty = True

        if archived:
            self._write_archive(archived)

        self._enforce_archive_retention()
        self.flush()
        return True

    def _write_archive(self, facts: list[MemoryFact]) -> None:
        """把归档项写入 ``memory_archive_YYYYMM.json``（与现有归档按 id 合并）。"""
        month = datetime.now().strftime("%Y%m")
        archive_path = os.path.join(self.base_dir, f"memory_archive_{month}.json")
        existing: dict[str, MemoryFact] = {}
        if os.path.exists(archive_path):
            try:
                data = self._read_envelope(archive_path)
                for raw in data.get("facts", []):
                    if isinstance(raw, dict) and "id" in raw:
                        try:
                            existing[raw["id"]] = MemoryFact.from_dict(raw)
                        except Exception:
                            continue
            except (OSError, MemoryFileCorrupt):
                existing = {}
        for fact in facts:
            existing[fact.id] = fact
        envelope = {
            "version": CURRENT_VERSION,
            "archived_at": _now_iso(),
            "facts": [f.to_dict() for f in existing.values()],
        }
        try:
            self._write_atomic_no_backup(archive_path, envelope)
        except OSError as e:
            print(f"[MemoryStore] 归档写入失败: {e}")

    def _enforce_archive_retention(self) -> None:
        """归档轮转（Rex 定稿 C blocker）：按保留天数 / 文件数 / 总体积上限清理旧归档。

        仅删除，不触碰主库；任何 OSError（如沙箱回收站不可用）被吞掉，不崩溃。
        """
        archives: list[tuple[str, str, int]] = []
        for fname in os.listdir(self.base_dir):
            if fname.startswith("memory_archive_") and fname.endswith(".json"):
                full = os.path.join(self.base_dir, fname)
                m = re.match(r"memory_archive_(\d{6})\.json", fname)
                try:
                    size = os.path.getsize(full)
                except OSError:
                    size = 0
                archives.append((full, m.group(1) if m else "", size))

        # 1. 超龄删除（YYYYMM 早于保留阈值）
        cutoff_ym = (datetime.now() - timedelta(days=ARCHIVE_RETENTION_DAYS)).strftime("%Y%m")
        for full, ym, _ in archives:
            if ym and ym < cutoff_ym:
                self._safe_remove(full)

        # 2. 文件数上限（保留最新 MAX_ARCHIVE_FILES 个）
        archives = [a for a in archives if os.path.exists(a[0])]
        archives.sort(key=lambda x: x[1])
        for full, _, _ in archives[: max(0, len(archives) - MAX_ARCHIVE_FILES)]:
            self._safe_remove(full)

        # 3. 总体积上限（从最旧开始删，保留至少 1 个）
        archives = [a for a in archives if os.path.exists(a[0])]
        total = sum(a[2] for a in archives)
        while total > MAX_ARCHIVE_BYTES and len(archives) > 1:
            full, _, size = archives.pop(0)
            self._safe_remove(full)
            total -= size

    def export(self, path: str) -> str:
        """把当前内存库导出为一份 JSON（供隐私命令 / 迁移）。不影响 dirty 与磁盘主库。"""
        with self._lock:
            facts = list(self._facts.values())
        data = self._serialize(facts)
        self._write_atomic(path, data)
        return path

    # ------------------------- 两级清空（Tier1 防复活 + Tier2 取证擦除） -------------------------

    def clear(
        self,
        secure: bool = False,
        *,
        source: Optional[MemorySource] = None,
        pii: Optional[bool] = None,
        kind: Optional[MemoryKind] = None,
        id: Optional[str] = None,
    ) -> int:
        """两级清除（Rex ratify）。

        过滤删除匹配的事实：
          - 不传任何过滤条件（或仅传 ``id``）→ 清空全部 / 指定单条；
          - 否则按 ``source`` / ``pii`` / ``kind`` 选择性清除。
        返回删除的条数。

        Tier1 逻辑删除（secure=False）：删除后清理 ``.bak`` 与归档中的匹配项，
        防止已删事实从备份复活。
        Tier2 安全擦除（secure=True）：在 Tier1 基础上，对涉及副本施加 best-effort
        取证保证（明文 v1 仅尽力；加密才有硬保证）。
        """
        def matches(fact: MemoryFact) -> bool:
            if id is not None and fact.id != id:
                return False
            if source is not None and fact.source != source:
                return False
            if pii is not None and fact.pii != pii:
                return False
            if kind is not None and fact.kind != kind:
                return False
            return True

        full_scope = (source is None and pii is None and kind is None and id is None)

        with self._lock:
            matched_ids = [fid for fid, f in self._facts.items() if matches(f)]
            for fid in matched_ids:
                del self._facts[fid]
            self._dirty = True
            deleted = len(matched_ids)

        if deleted or full_scope:
            self.flush()
            self._purge_replicas(matched_ids=set(matched_ids), secure=secure)
        return deleted

    def _purge_replicas(self, matched_ids: set[str], secure: bool) -> None:
        """Tier1 防复活 / Tier2 擦除：处理 .bak 与所有归档副本，使已删事实不可复活。

        - 归档副本：剔除 matched_ids 后用 no-backup 原子重写（不产生 .bak）。
        - 主文件 .bak：直接删除（下一次正常 flush 会从已清理的内存重建，
          因此 .bak 不会长期残留旧事实）。
        - secure=True：对 .bak / 归档施加 best-effort 取证级覆写后删除。
        """
        # 归档：剔除匹配项并重写（no-backup，避免 nested .bak）
        for arc in self._list_archive_paths():
            if secure:
                self._secure_delete_file(arc)
                continue
            self._rewrite_replica_without(arc, matched_ids)

        # 主文件 .bak
        bak_path = self._store_path + _BAK_SUFFIX
        if secure:
            self._secure_delete_file(bak_path)
        elif os.path.exists(bak_path):
            self._safe_remove(bak_path)

    def _rewrite_replica_without(self, path: str, matched_ids: set[str]) -> None:
        """读取副本（若合法），剔除 matched_ids 后 no-backup 原子重写；损坏/缺失则跳过。"""
        if not os.path.exists(path):
            return
        try:
            data = self._read_envelope(path)
        except MemoryFileCorrupt:
            return
        kept = []
        for raw in data.get("facts", []):
            if not isinstance(raw, dict) or "id" not in raw:
                kept.append(raw)
                continue
            if raw["id"] in matched_ids:
                continue  # 剔除
            kept.append(raw)
        data["facts"] = kept
        try:
            self._write_atomic_no_backup(path, data)
        except OSError:
            pass

    def _list_archive_paths(self) -> list[str]:
        out: list[str] = []
        if not os.path.isdir(self.base_dir):
            return out
        for f in os.listdir(self.base_dir):
            if f.startswith("memory_archive_") and f.endswith(".json"):
                out.append(os.path.join(self.base_dir, f))
        return sorted(out)

    @staticmethod
    def _secure_delete_file(path: str) -> None:
        """best-effort 安全删除：3 遍覆盖写 + 删除（SSD 残留归 best-effort）。"""
        if not os.path.exists(path):
            return
        try:
            size = os.path.getsize(path)
            patterns = (b"\x00" * 65536, b"\xff" * 65536, os.urandom(65536))
            with open(path, "r+b") as f:
                for pat in patterns:
                    f.seek(0)
                    remaining = size
                    while remaining > 0:
                        chunk = pat[: min(len(pat), remaining)]
                        f.write(chunk)
                        remaining -= len(chunk)
                    f.flush()
                    os.fsync(f.fileno())
            os.remove(path)
        except OSError:
            try:
                os.remove(path)
            except OSError:
                pass

    @staticmethod
    def _safe_remove(path: str) -> None:
        """删除文件，吞掉沙箱 / 回收站不可用导致的 OSError（不崩溃）。"""
        try:
            os.remove(path)
        except OSError:
            pass

    # ------------------------- 测试 / 运维对齐 API（oracle 方法名） -------------------------

    def add(self, fact: MemoryFact) -> MemoryFact:
        """oracle 别名：等价于 upsert。"""
        return self.upsert(fact)

    def save(self) -> bool:
        """oracle 别名：等价于 flush（强制落盘）。"""
        return self.flush()

    def get_recent(self, limit: Optional[int] = None, query: Optional[str] = None) -> list[MemoryFact]:
        """oracle 别名：有 query 走关键词检索；否则按 updated_at 倒序返回近期事实。"""
        with self._lock:
            facts = list(self._facts.values())
        if query:
            return [r.fact for r in self.query_by_keywords(query, k=limit or 5)]
        facts.sort(key=lambda f: f.updated_at, reverse=True)
        if limit is not None:
            facts = facts[:limit]
        return facts

    def update(self, id: str, patch: dict) -> bool:
        """对指定 fact 打补丁（仅更新 patch 中给出的字段），成功返回 True。"""
        with self._lock:
            fact = self._facts.get(id)
            if fact is None:
                return False
            for key, value in patch.items():
                if hasattr(fact, key):
                    setattr(fact, key, value)
            fact.updated_at = _now_iso()
            self._dirty = True
        self._request_flush()
        return True

    def stats(self) -> dict:
        """返回存储统计（oracle：测试 / 运维可见性）。"""
        with self._lock:
            facts = list(self._facts.values())
        by_source: dict[str, int] = {}
        by_kind: dict[str, int] = {}
        pii_count = 0
        for f in facts:
            by_source[f.source.value] = by_source.get(f.source.value, 0) + 1
            by_kind[f.kind.value] = by_kind.get(f.kind.value, 0) + 1
            if f.pii:
                pii_count += 1
        return {
            "count": len(facts),
            "by_source": by_source,
            "by_kind": by_kind,
            "pii_count": pii_count,
        }

    def clear_all(self) -> bool:
        """清空全部记忆（Tier1 逻辑删除）。调用方如需保留应先 ``export()``。"""
        self.clear()
        return True

    def clear_by_id(self, id: str) -> bool:
        """按 id 清空单条（内存 + 磁盘 + 副本一致性清除）。"""
        return self.delete(id)

    # ------------------------- 内部工具 -------------------------

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        """简单分词：小写、去标点、拆拉丁词 + CJK 单字，剔除停用词。"""
        if not text:
            return set()
        tokens = set(_TOKEN_RE.findall(text.lower()))
        tokens -= _STOPWORDS
        return tokens

    @staticmethod
    def _recency_score(fact: MemoryFact) -> float:
        """新鲜度 0~1：updated_at 越近分越高；从未设置 / 无法解析返回 0.2 兜底。"""
        if not fact.updated_at:
            return 0.0
        try:
            last = datetime.fromisoformat(fact.updated_at.replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - last).total_seconds() / 86400.0
        except ValueError:
            return 0.2
        return 1.0 / (1.0 + max(0.0, age_days))

    @staticmethod
    def _age_days(iso: str, now: datetime) -> float:
        try:
            return (now - datetime.fromisoformat(iso.replace("Z", "+00:00"))).total_seconds() / 86400.0
        except ValueError:
            return float("inf")

    @staticmethod
    def _estimate_size(facts: list[MemoryFact]) -> int:
        """估算序列化后字节数（UTF-8）。"""
        return len(
            json.dumps(
                [f.to_dict() for f in facts], ensure_ascii=False
            ).encode("utf-8")
        )
