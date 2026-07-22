# J.A.C. 记忆功能子系统 · 测试计划

> 作者：泰莎 (Tessa) · 测试专家
> 阶段：**设计阶段（仅规划，不含实现）**
> 范围：记忆存储 与 「记录判定逻辑」（重要信息 vs 日常提问），JSON 持久化初版

---

## 0. 现状核对（已读源码）

| 检查项 | 现状 | 对测试的影响 |
|---|---|---|
| `tests/` 目录 | **不存在** | 需新建测试目录与结构 |
| pytest / pytest-cov / pytest-mock / hypothesis | **`requirements.txt` 中均未包含** | 测试基建为 0，需补充依赖 |
| pytest 配置（pytest.ini / pyproject.toml） | **无** | 需新建，集中管理 mock / 覆盖配置 |
| `LocalBrain` 测试可用性 | ✅ 支持 `backend="mock"`，`think()` 走 `_mock_response`，**不连 LM Studio** | 集成测试可完全离线跑 |
| `SharedContext` | ✅ 已用 `threading.Lock()` 做线程安全 | 记忆并发需在其之上再加一层锁 |
| `JudgmentEngine` 模式 | ✅ 现成 LLM 判定范式（`check_available()` 优雅降级 → 被动模式） | 「记录判定」应复用同一降级思路 |
| 已有测试覆盖 | **0%**（无任何测试） | 缺口 = 记忆子系统全部 + 测试基建本身 |

**结论**：本计划内容全部为**新增**，且必须先补齐最小测试基建（依赖 + 目录 + 配置）。当前「现有覆盖缺口」= 100% 的记忆功能未被测试，且连测试运行器都没有。

---

## 1. 测试策略总览（金字塔映射）

```
        /   E2E 小范围    \     少量：真实 main.py 跑一段带 mock 外设的对话
       /     集成测试      \    中量：MemoryStore ↔ LocalBrain ↔ 主循环（mock brain）
      /       单元测试      \   大量：MemoryStore 存储/恢复、RecordJudge 分类
```

按组件类型制定策略：

- **记忆存储（MemoryStore）**：纯类/函数的单元测试为主（快、可离线），原子写与损坏恢复为关键路径。
- **记录判定（RecordJudge）**：规则单测 + 黄金数据集评估 LLM 判定的边界与误判率。
- **集成**：memory 在 `LocalBrain.think()` 注入、主循环读写时机、状态机/控制台输入 —— 全程 mock `LocalBrain`，不依赖 LM Studio。

---

## 2. 提议的模块接口（⚠️ 待架构师 Archi 确认）

为让测试目标明确，先定下约定接口（若 Archi 调整，测试签名需同步）：

```
src/memory/__init__.py
src/memory/store.py            # MemoryStore（持久化 + 去重/合并 + 损坏恢复 + 按 source 清除）
src/memory/judge_record.py     # MemoryClassifier：规则优先 + LLM 辅助的「记录判定」
src/memory/schema.py           # 记忆 JSON schema / 版本常量（已 ratify，供测试断言）

常量与异常（来自 ratify 版 schema，作为测试 oracle）：
  SCHEMA_VERSION = "1.0.0"          # semver 字符串（顶层 version，非 int）
  RECURRENCE_THRESHOLD = 3          # 同 topic_key 累计出现次数达到即升级 source=recurring
  MIN_CLASSIFY_INTERVAL = 3.0       # 距上次分类的最小间隔（秒），限流用
  WEIGHT_DEFAULT = 0.5              # weight 默认（取代原 importance int）；recurring 取更高值
  判定正则（Archi 锁定量，均 re.IGNORECASE）：
    EXPLICIT_SAVE_RE  = r"(记住|记一下|记着|别忘了|请记住|帮我记|记到|存一下|提醒我|remember\s+(this|that)|don'?t\s+forget|save\s+(this|that)|note\s+(this|that))"
    WEAK_INTENT_RE    = r"(以后|之后|将来|下次|下回|next\s+time|from\s+now\s+on)"   # 弱信号→LLM，不直接记
    PREFERENCE_RE     = r"(我喜欢|我不喜欢|我讨厌|我恨|我爱|我习惯|我一般|叫我|我是|我家|我不吃|我想要|我打算|i\s+(like|love|hate|prefer|usually|always))"
    DECISION_RE       = r"(我们决定|决定|约定|答应|计划|承诺|说好|说定了|约好|we\s+(decided|agreed)|i\s+promise|let'?s\s+(meet|do))"
    QUESTION_RE       = r"[?？]\s*$"                                  # 一次性问答排除辅助
    SMALLTALK_RE      = r"(你好|您好|hi|hello|hey|讲个笑话|讲个故事|再见|拜拜)"
  规则阶段判定顺序（硬 oracle）：SMALLTALK_RE→排除；EXPLICIT_SAVE_RE→直接记(source=explicit)；
    QUESTION_RE 且无他触发→排除；PREFERENCE_RE/DECISION_RE/WEAK_INTENT_RE→路由 classify_round；否则→排除(日常)
  kind 枚举（与 RecordDecision.kind 同源；should_store=false 时 null）：
    profile / preference / convention / event / topic
    （映射：user_profile_fact→profile、preference→preference、project_convention→convention、
      decision_event→event、recurring_topic→topic）
  注：recurring 晋升设置 weight 高值（[0,1]，如 0.8）；文中 "importance=2~3" 为旧术语，
      已对齐 ratify schema 的 weight 字段，不再使用 importance int（待 Docu 在 schema.md 确认无残留）。
  异常：MemoryFileCorrupt / MemoryVersionIncompatible
  存储路径（Rex 定稿，非项目内 data/）：
    MEMORY_DIR  = ~/.jac/memory/            # Windows: %APPDATA%/jac/memory/
    MEMORY_PATH = MEMORY_DIR/memory.json
    DIR_MODE=0o700 / FILE_MODE=0o600
    同目录副本：memory.json.bak / memory_archive_YYYYMM.json / *.tmp / .corrupt.*
    MEMORY_CAPTURE_PERSON_ID = False   # 写入期 PII 把关：默认不持久化人物/人脸类 PII
    JAC_MEMORY_DIR = None              # 环境变量覆盖 base 目录（monkeypatch 验证生效）
    MEMORY_ENABLED = True              # base 不可写时降级为 False（优雅降级，不崩溃）
    ACTIVE_MAX_BYTES = 2*1024*1024      # 活动文件 2MB 上限，触发压缩/归档（独立于归档聚合上限）
    MAX_ARCHIVE_FILES = 6              # 归档文件数上限，超出淘汰最旧
    MAX_ARCHIVE_BYTES = 10*1024*1024   # 归档总字节上限，超出淘汰最旧直至回落
    ARCHIVE_RETENTION_DAYS = 180       # 归档留存天数，超期自动删除

顶层结构：{ "version": "1.0.0", "facts": [...] }
  （user_consent 已移出 memory.json → consent.json/设置层，不在此断言）

MemoryFact 字段（必填 6 / 可选 5）：
  必填（缺失 → 跳过并计入 invalid_facts）：
    id(UUID4) / content / kind(enum) / source(五值) / created_at / updated_at  （ISO8601 字符串）
  可选（缺失 → 套默认，不计入 invalid_facts）：
    weight: number[0,1]，默认 0.5
    tags: array<string>，默认 []
    pii: boolean，默认 false（缺失按 false，安全默认）
    ttl: string|null(ISO8601)，默认 null
    embedding: array<number>|null，默认 null（保留字段，v1 恒 null，ADR-001）

  source 五值（⚠️ 已 ratify，非旧三值）：
    explicit（用户主动告知）| inferred（系统推断偏好，B 路径）| recurring（频次升级，C 路径）|
    judgment（判断引擎介入）| manual（用户/CLI 手动编辑）
    → 隐私含义：可一键清空全部 inferred 而保留 explicit；inferred 默认更低 weight。

类与方法（Archi 5 点已定稿，作为测试 oracle）：
  class MemoryStore:
      __init__(self, path=None, context=None)
          # 默认 path = ~/.jac/memory/memory.json（Windows %APPDATA%/jac/memory/memory.json）
          # 目录 0o700 / 文件 0o600；同目录有 .bak / memory_archive_YYYYMM.json / *.tmp / .corrupt.*
      add(fact: MemoryFact) -> MemoryFact        # 写入；recurring 同 topic_key 合并 upsert
      get_recent(limit=None, query=None) -> list  # 注入用检索
      update(id, patch) -> bool
      delete(id) -> bool
      # 清除分两级（Rex 定稿）；`secure` 是修饰语，可作用于全清或范围清：
      #  Tier1 逻辑删除：clear(source="inferred") / clear(pii=True)
      #        → 从 memory.json + .bak + 各 archive 一并剔除，防逻辑复活（不要求取证级）
      #  Tier2 安全擦除：clear(secure=True) / clear(source=..., secure=True) / clear(pii=True, secure=True)
      #        → 销毁全部副本（活动+.bak+archive+tmp+.corrupt）或范围剔除后施加取证保证
      #       加密态=销毁密钥；明文态=3 遍覆盖写(0x00→0xFF→随机)+os.remove+best-effort TRIM
      clear(secure=False, source=None, pii=False) -> None
      load() -> {"facts":[...], "invalid_facts":[{"id","reason"}]}
      save()                                     # 原子写（temp + os.replace）+ 写 .bak
      stats() -> dict

  class MemoryRecorder:                          # src/memory/judge_record.py
      counts: dict[topic_key, int]                # 会话级内存计数器，**不持久化**到 memory.json
      normalize_topic(text) -> str                # 小写/去标点空白/去停用词/确定性（同输入→同 key）
      classify_round(turn) -> RecordDecision | None
          # 单元：入参 (user_text, window) → LocalBrain.think() 分类 prompt(max_tokens=128)
          #       → 解析 RecordDecision → should_store 则 upsert（受 pii 门控）
          # 门控（worker 内、调用前）：not context.is_thinking 且 now-last >= MIN_CLASSIFY_INTERVAL
          #       单轮 ≤1 次分类；brain 忙 → 跳过本轮（宁可漏记）
      # 晋升：count >= RECURRENCE_THRESHOLD(3) 且无对应 topic 事实
      #       → upsert(kind=topic, weight 高值[0,1], source=recurring)；晋升后保留计数防重复晋升
      # 进程重启 counts 清零（会话级）；跨会话靠已晋升事实持久化覆盖

  class MemoryManager:                           # 编排 + 后台 worker
      record_turn(user_text, response, window)
          # 在 process_response 末尾（助手说完、context.is_speaking=False 之后）调用一次
          # 入队到内部单后台 worker（daemon 线程 + 队列 / 1 worker ThreadPoolExecutor）
          # 绝不在音频/控制台/主线程，绝不阻塞响应（立即返回）
      # pii 双层门控：分类侧标 pii=True；写时 MEMORY_CAPTURE_PERSON_ID=False → 拒绝任何 pii=True 事实
      #       仅 flag=True 且 source=explicit 才落库并标 pii=True

  RecordDecision（独立契约，不进 memory.json）：
      should_store(bool) / reason(string，受控词表) /
      kind(enum|null，与 MemoryFact.kind 同源；should_store=False 时必为 null) /
      confidence(number[0,1])
```

- **注入点**：`process_response()` 在调 `brain.think()` 前用 `store.get_recent()` 拼 `[已知信息]` 入 `system_prompt`；**记录**在 `process_response` 末尾（助手说完、`context.is_speaking=False` 之后）由 `MemoryManager.record_turn()` 触发，入队后台 worker，立即返回、**绝不阻塞响应**。
- **SharedContext 扩展**：建议加 `context.memory` 句柄或 `context.get_memory_context()`，使 judgment 线程 / 主循环共享同一 `MemoryStore` 实例（保证单例 + 单锁）。

---

## 3. 单元测试

### 3.1 MemoryStore（存储 / 检索 / 去重 / 更新 / 删除）

测试类型：纯单测（`tmp_path` 做 path，无网络）。

| 用例 | 输入 / 场景 | 断言 |
|---|---|---|
| 存储后可读 | add 一条 fact，用**新实例**读同 path | 取出内容一致、id 稳定、created_at 合理 |
| 去重 | 连续 add 两条 90% 相似内容 | 实际仅 1 条；返回 merged fact、weight 增加 |
| 更新 | update(id, {content}) | 该条 content 变、updated_at 晚于 created_at、其它字段保留 |
| 删除 | delete(id) 后再 get_recent | 列表不含该 id、文件已反映删除 |
| 空检索 | 库空时 get_recent | 返回 `[]`，不抛错 |
| 按 query 检索 | 库中 3 条，query 命中 1 条关键词 | 仅返回命中条，顺序按权重 / 时间 |
| 重复 / 冲突条目合并 | 先存「我在上海」，后存「我搬去北京」 | 标记冲突、保留两条或合并为最新（按既定策略断言） |

### 3.2 JSON 读写与损坏恢复（依据 ratify 版 schema 的 7 条行为）

| # | 用例 | 场景 | 断言 |
|---|---|---|---|
| 1 | 解析失败 | 文件内容非合法 JSON（截断 / 乱码） | 抛 `MemoryFileCorrupt`（不静默吞） |
| 2 | version 缺失 | 合法 JSON 但无 `version` 字段 | 按 `"0.0.0"` 宽松加载，下次 save 升级为 `1.0.0` |
| 3 | MAJOR 不符 | `version: "2.0.0"` 而代码期望 `1.x` | 抛 `MemoryVersionIncompatible`，拒绝加载 |
| 4 | 同 MAJOR 差异 | `version: "1.2.0"` 读入 `1.0.0` 代码 | 兼容加载，忽略未知字段，不报错 |
| 5 | 单条缺必填 | facts 中某条缺 `id/content/kind/source/created_at/updated_at` 之一 | 该条容忍跳过，计入 `invalid_facts`；其余正常载入 |
| 6a | 损坏恢复·有备份 | 主文件 `MemoryFileCorrupt` 且 `memory.json.bak` 有效 | loader 先尝试 `.bak` → 从 `.bak` 成功加载 |
| 6b | 损坏恢复·无备份 | 主文件损坏且**无** `.bak` | `MemoryFileCorrupt` 向上传播（不静默空库） |
| 7 | invalid_facts 报告 | 触发 #5 场景 | `load()` 返回 `{"facts":[...], "invalid_facts":[{"id","reason"}, ...]}`；断言计数与原因列表 |
| — | 可选字段缺省 | 单条缺 `weight/tags/pii/ttl/embedding` | 套默认（0.5/[]/false/null/null），不计入 invalid_facts |
| — | 顶层非 facts | 文件顶层非 `{version,facts}` 结构 | 视为损坏或按 #4/#5 容错；不把裸数组当单条 |

### 3.3 原子写入验证

| 用例 | 场景 | 断言 |
|---|---|---|
| 写过程崩溃不丢数据 | 在 `save()` 写 tmp 阶段注入异常（monkeypatch `os.replace` 抛错） | 原 `memory.json` 不变、`.bak` 仍存在、进程不损坏 |
| 原子替换 | 正常 save | 最终 `memory.json` 完整且为新内容；`.tmp` 不残留 |
| 中途断电模拟 | save 后校验 `os.replace` 调用一次 | 用 mock 验证仅一次原子替换，无部分写入 |

### 3.4 RecordJudge「记录判定」（重点 · 规则优先 + LLM 辅助）

> 判定策略（来自 Archi 设计）：**规则优先 + LLM 辅助**，非纯规则也非纯 LLM；判定在**每轮对话结束后于后台线程执行**，**绝不阻塞用户**。

**触发条件（满足任一 → 记为重要）**：

| 标记 | 条件 | 正则 / 信号 | 落库方式 |
|---|---|---|---|
| A 显式保存意图 | 用户明确要求记住 | `EXPLICIT_SAVE_RE`（高置信） | 规则高置信，**直接落库**，source=`explicit` |
| B 用户画像/偏好/习惯 | 透露偏好/身份/习惯 | `PREFERENCE_RE` | 规则识别意图，**LLM 抽取 content+tags**，source=`inferred`，kind=`preference`/`profile` |
| C 反复主题自动升级 | 同 topic_key 累计（内存计数） | `count >= RECURRENCE_THRESHOLD(3)` 且无对应 topic 事实 | 晋升 `upsert(kind=topic, weight 高值, source=recurring)`；晋升后保留计数防重复晋升 |
| D 重要决策/约定/承诺 | 约定/承诺类 | `DECISION_RE` | **LLM 确认后记录**，kind=`event` |
| W 弱意图 | 「以后/下次/将来…」 | `WEAK_INTENT_RE` | **不直接记**，路由 `classify_round` 由 LLM 判定 |
| E 判断引擎信号 | 未来能力 | （v1 不接） | — |

规则阶段判定顺序（硬 oracle）：`SMALLTALK_RE`→排除；`EXPLICIT_SAVE_RE`→直接记；`QUESTION_RE` 且无他触发→排除；`PREFERENCE_RE`/`DECISION_RE`/`WEAK_INTENT_RE`→路由 `classify_round`；否则→排除（日常）。

**排除项（必须断言「不记录」）**：
- 一次性问答（`？/?` 结尾且无任何 A~D 模式）
- 闲聊（如「你好」「讲个笑话」）
- 纯任务结果（如「已为你设置闹钟」的回显）
- 敏感未授权人物身份（默认不记）：分类侧标 `pii=True` 的事实，写时 `MEMORY_CAPTURE_PERSON_ID=False`（默认）**拒绝持久化**（无论 should_store）；仅 flag=True 且 source=explicit 才落库
- 原始转录流（不应整段落库）

**黄金数据集 + 误判率（断言阈值）**：
- 存放 `tests/fixtures/record_samples.jsonl`，建议 ≥ 40 条，正负各半，覆盖 A/B/C/D 与全部排除项。
- 硬指标：**A 类 100% 落库**；**所有排除项 100% 不记录**。
- 规则层**精确率 ≥ 0.95**；LLM 增强后**召回率 ≥ 0.85**；**误存率（SKIP→IMPORTANT）≤ 5%**。
- `hypothesis` 模糊输入（超长 / 特殊字符 / 中英混排）确保判定不崩。

**测试维度（Archi 指定 oracle）**：
1. 规则正则命中率与路由：EXPLICIT_SAVE_RE / PREFERENCE_RE / DECISION_RE / WEAK_INTENT_RE / QUESTION_RE / SMALLTALK_RE 各组造正例+负例，断言命中与路由（直接记 vs 路由 LLM vs 排除）。
2. 阈值边界：同 topic_key 出现 **2 次 vs 3 次** → 2 次不升级、3 次升级 `recurring_topic`。
3. 门控条件：`context.is_thinking == True` 时分类调用次数应为 **0**（见 §3.6）。
4. 限流：3s 内多次模糊输入，LLM 分类**至多 1 次**（见 §3.6）。
5. 排除项断言：一次性问答 / 闲聊 / 纯任务结果 / 敏感人物 / 原始转录 均 `should_store=False`。
6. 后台线程非阻塞：`process_response` 返回耗时不受分类影响（见 §3.7）。

**样例集（节选）**：

| 类别 | 正例 | 期望 |
|---|---|---|
| A 显式 | 「记住我喜欢喝美式咖啡」「remind me to…」 | should_store=True（高置信，source=explicit） |
| B 偏好 | 「我是素食者」「我家猫叫 Mochi」 | True，LLM 抽 content+tags，source=inferred |
| C 反复 | 3×「项目 X 的 deadline 快到了」 | 第 3 次晋升 recurring_topic（counts 会话级） |
| D 约定 | 「我们约定每周三碰头」「我答应明天交」 | LLM 确认后 True，kind=event |
| W 弱意图 | 「以后每天提醒我吃药」 | 不直接记，路由 classify_round（LLM 判定） |
| 排除-问答 | 「今天周几？」 | False |
| 排除-闲聊 | 「你好」「讲个笑话」 | False |
| 排除-敏感 | 「那个人叫张三」（未授权） | pii=True+flag=False → 硬丢弃不落库 |

**判定逻辑验收标准（写入 DoD）**：
1. A 类显式保存（EXPLICIT_SAVE_RE）**100%** 落库（硬指标）。
2. 排除项 **100%** 不记录（硬指标）。
3. 闲聊 / 一次性问答误存率 **≤ 5%**。
4. LM Studio 不可用时纯规则模式仍确定可运行（相同输入 → 相同输出）。
5. 每次判定 `reason` 非空、可解释；`weight` / `tags` 由 LLM 正确填充；`source` 取五值之一；`kind` 与 MemoryFact.kind 同源（should_store=False 时 `kind` 必为 null）。
6. 同 topic_key 累计达 `RECURRENCE_THRESHOLD(3)` 自动晋升，且同 key 合并 upsert；晋升后保留计数防重复晋升；memory.json **无** `occurrences` 字段。
7. pii 双层门控：pii=True 且 flag=False → **硬丢弃不落库**；pii=True 且 flag=True 且 source=explicit → 落库且 pii=True；pii=False 不受影响。
8. `record_turn` 在 `process_response` 末尾（助手说完、is_speaking=False 后）调用一次，入队后台 worker，立即返回不阻塞；`classify_round` 在 worker 线程执行。
9. `normalize_topic` 确定性：同输入 → 同 topic_key（断言幂等）。

### 3.5 示例测试用例（描述式，非实现）

```
test_store_add_then_reload_persists_content
test_store_dedup_merges_similar_facts
test_store_update_preserves_other_fields
test_store_delete_removes_from_file
test_load_recovers_from_bak_on_truncated_json
test_load_starts_empty_when_no_bak
test_save_atomic_no_tmp_leftover
test_save_keeps_old_file_when_replace_fails
test_recordjudge_explicit_save_is_always_important
test_recordjudge_chitchat_false_positive_under_5pct
test_recordjudge_repeated_topic_promoted_after_n
test_recordjudge_falls_back_to_rule_when_llm_unavailable
```

---

### 3.6 门控与限流（关键可测不变量）

| 不变量 | 场景 | 断言 |
|---|---|---|
| 分类仅在大脑空闲时发起 | `context.is_thinking == True` 期间触发 `classify_round` | 分类调用计数 = 0，不阻塞 |
| 最小间隔限流 | 距上次分类 < `MIN_CLASSIFY_INTERVAL(3s)` 再次触发 | 本轮跳过（调用数不增） |
| 单轮至多 1 次 LLM 分类 | 一轮内多次模糊 / 边界输入 | LLM（`requests.post`）调用 ≤ 1 |
| brain 繁忙宁可漏记 | `is_thinking` 为真时 | 返回 `None`，不拖延交互 |

### 3.7 后台线程非阻塞

| 用例 | 场景 | 断言 |
|---|---|---|
| 分类不拖慢回复 | `process_response` 调用 `classify_round`（mock 其内部耗时 200ms） | `process_response` 返回耗时不受分类影响（差异在阈值内） |
| 分类在独立线程 | 触发一轮对话 | 分类发生在非主线程（断言 `threading.current_thread()` 非主线程），或主流程已先返回 |

---

### 3.8 路径解析与优雅降级（A，SRE blocker）

| 用例 | 场景 | 断言 |
|---|---|---|
| 单 base 派生 | 所有副本路径（memory.json/.bak/archive/*.tmp/.corrupt） | 均派生自单一 base，无硬编码散落路径 |
| 环境变量覆盖 | 设 `JAC_MEMORY_DIR` 后初始化 | 实际 base 取该值（monkeypatch os.environ 验证生效） |
| 跨平台默认 base | mac/linux / Windows 分别初始化 | 默认 `~/.jac/memory` / `%APPDATA%/jac/memory`，正确解析 |
| base 不可写降级 | base 目录无写权限 / 不存在且不可建 | `MEMORY_ENABLED=False`，不崩溃、不抛未捕获异常，记忆功能安全禁用 |

---

## 4. 集成测试

全程用 `LocalBrain(backend="mock")` + `monkeypatch` 拦截 `requests.post`，**不依赖 LM Studio**。

| 用例 | 场景 | 断言 |
|---|---|---|
| 记忆注入 think | 预置 1 条记忆，`process_response` 调 `brain.think()` | mock brain 收到的 `system_prompt` 含 `[已知信息]` 与该记忆内容 |
| 无记忆时不注入 | 空库 | `system_prompt` 不含记忆块（或为空块） |
| 主循环写入时机 | 经 `handle_user_text("记住我怕黑", ...)` | 该 fact 出现在 store；且发生在 think 之前 |
| 状态机集成 | SLEEP 态下未唤醒的闲聊不触发记录；AWAKE 下记录 | 依 `SYSTEM_STATE` 断言是否写库 |
| 控制台输入集成 | `source="控制台", bypass_wake=True` | 与语音路径一致地走 注入 + 判定 |
| 多轮记忆累积 | 连续 3 轮都涉及同一主题 | 第 3 轮 `get_recent()` 返回权重更高的合并条目 |
| 判定失败不影响对话 | `should_record` 抛异常 | `process_response` 仍正常回复，记录被跳过并记日志 |

---

## 5. 边界与异常

| 维度 | 用例 | 断言 |
|---|---|---|
| 空记忆 | 首启无文件 | 空库启动、注入空块、不崩 |
| 超大记忆 | 注入 10k 条 / 单条 > 100KB | `stats()` 反映体积；`save` 仍能完成；注入时截断 / 摘要，避免超出 LLM 上下文（断言 prompt 长度上限） |
| 并发读写 | 同时起 audio 线程 + 手动线程 + judgment 线程各调 add/read | 最终数据一致、无部分写、无死锁（用 `threading.Barrier` 制造竞争 + `pytest-timeout` 防挂死） |
| Tier1 范围清除·按来源 | `clear(source="inferred")` 后 | `memory.json` 中 `source=="inferred"` 计数 ==0；`source=="explicit"` 完整保留 |
| Tier1 一致性·备份 | 同上，且存在 `memory.json.bak` | `.bak` 中 inferred==0（已重建非陈旧），非仅删活动文件 |
| Tier1 一致性·归档 | 同上，且存在 `memory_archive_YYYYMM.json` | 各 archive 中 inferred==0（已剔除） |
| Tier1 不复活 | 清除后从 `.bak` 重载 / 模拟重启 | 重载后 inferred 仍 ==0，不发生逻辑复活 |
| Tier2 安全擦除·副本消失 | `clear(secure=True)` 后 | 目录下 `memory.json`/`.bak`/`memory_archive_*.json`/`*.tmp`/`.corrupt.*` 均不存在；内存 store 空 |
| Tier2 加密态·密钥销毁 | 启用加密时 `clear(secure=True)` | 断言密钥材料已销毁（重开无法解密 / 密钥句柄为空） |
| Tier2 明文态·无已知串 | v1 明文默认 `clear(secure=True)` | 目录内不含已知已删 fact 的字符串（best-effort 可验证层） |
| Tier1 范围清除·PII | `clear(pii=True)` 后 | `memory.json`/`.bak`/各 `memory_archive_YYYYMM.json` 中 `pii=true` 均 ==0（逻辑 absence，非取证级）；重载/重启不复活 |
| Tier2 范围安全擦除·PII | `clear(pii=True, secure=True)` 后 | 范围级 Tier2：重写 memory.json 剔除 pii 事实 + 对存储文件施取证保证（加密→毁钥；明文→覆写+TRIM best-effort）；pii 相关副本均消失 |
| 同意移出 | `user_consent` 不在 memory.json | 加载不依赖 memory.json 内 consent 字段（已在 consent.json/设置层） |
| 重复 / 冲突合并 | 同主题矛盾陈述 | 按既定策略合并或保留多版本，断言结果稳定 |

**归档留存（C，SRE blocker）**：

| 用例 | 场景 | 断言 |
|---|---|---|
| 压缩幂等 | 已归档记录再次触发归档 | 按 id 比较，不被重复移入归档（无重复 fact） |
| 归档数上限 | 归档文件数 > `MAX_ARCHIVE_FILES(6)` | 最旧归档被淘汰，数量回落 ≤ 6 |
| 归档字节上限 | 归档总字节 > `MAX_ARCHIVE_BYTES(10MB)` | 淘汰最旧直至总字节回落上限内 |
| 归档留存期 | 归档超 `ARCHIVE_RETENTION_DAYS(180)` | 到期归档自动删除 |
| 不绕过活动上限 | 活动文件达 2MB | 触发活动压缩 / 归档（独立机制）；归档另有聚合上限，互不绕过 |
| 归档·PII 一致剥离 | `clear(pii=True)` 且存在归档 | 含 pii 的归档事实被一致剥离（归档中 pii==0） |
| 归档·安全擦除 | `clear(secure=True)` 且存在归档 | `memory_archive_*.json` 文件被删除 |

**测试性边界（关键，Rex 定稿）**：Tier1（范围逻辑删除）完全可机器验证，用「存在性 / 一致性」断言。Tier2 的「取证级不可恢复」在跨平台 / SSD 上**无法完全自动化验证**——可断言部分 = 文件消失 +（加密）密钥销毁 +（明文）剩余文件无已知串；SSD 磨损残留归为文档声明的 best-effort，**不要写成硬断言**（否则测试虚假通过或不可移植）。明文 v1 的 secure 擦除须在文档写明 best-effort、SSD 不保证取证级不可恢复；启用加密才有硬保证。

---

## 6. 覆盖率目标与 CI 建议

**补充测试依赖（新增，不污染运行依赖）：**
```
pytest>=8
pytest-cov
pytest-mock
pytest-timeout
pytest-xdist      # 并行
hypothesis       # 模糊测试
```
建议放 `requirements-test.txt` 或 `pyproject.toml` 的 `[test]` extra。

**覆盖率阈值（建议）：**
- `src/memory/**` 行覆盖 ≥ **90%**（核心逻辑）
- `src/utils/context.py` 记忆相关扩展 ≥ **85%**
- `main.py` 注入 / 判定分支 ≥ **70%**（主循环难全测，重点覆盖 `process_response` / `handle_user_text`）
- 全局门槛：`--cov-fail-under=85`，未达标 CI 失败

**CI（项目本地优先，但建议加最小 GitHub Actions / 本地 `make test`）：**
```yaml
# .github/workflows/test.yml（或本地脚本）
pytest tests/ -q --cov=src/memory --cov=src/utils \
  --cov-report=term-missing --cov-fail-under=85 \
  --timeout=60 -n auto
```
- 全程 mock `LocalBrain` / `requests`，无需 LM Studio、无需摄像头 / 麦克风（`Camera` / `Speaker` 在测试中 stub）。
- 黄金数据集评估（误判率）作为**非阻断**报告项先跑，逐步收紧阈值。

**Mock 外部依赖策略：**
- `LocalBrain(backend="mock")` 直接离线；LLM 判定用 `monkeypatch` 替 `requests.post` 返回固定 `STORE / SKIP`。
- 摄像头 / 麦克风 / TTS：`pytest` fixture 用 fake 对象替换，避免硬件。
- 磁盘：`tmp_path` fixture 隔离，不碰真实 `data/`。

---

## 7. 分阶段测试执行顺序

```
阶段 0  基建       新增 tests/ + pytest 配置 + 依赖；建 fixtures 目录
   ↓
阶段 1  单测-存储  MemoryStore 增删改查 → 原子写 → 损坏恢复（最快、零依赖）
   ↓
阶段 2  单测-判定  RecordJudge 规则层 → 黄金集评估 → LLM 层 mock（核心风险）
   ↓
阶段 3  集成       mock LocalBrain 注入 / 主循环 / 状态机 / 控制台
   ↓
阶段 4  边界异常   空 / 超大 / 并发 / 隐私清除 / 冲突合并
   ↓
阶段 5  E2E 小样   真实 main 流程（全 mock 外设）跑一段对话
   ↓
阶段 6  CI 固化    覆盖率门槛 + 误判率报告接入，门禁生效
```
原则：**先单测后集成**；每个阶段红 → 绿后再进下一阶段；判定逻辑（阶段 2）因误判风险最高，优先建黄金集。

---

## 8. 关键风险点

1. **记录判定误判（最高风险）**：LLM 非确定性，纯 LLM 判定不可靠 → 必须规则兜底 + 黄金集量化误判率，否则「记住重要 / 忽略日常」的核心价值无法保证。
2. **JSON 损坏 / 原子写缺失**：断电或崩溃可能丢全部记忆 → 测试必须证明 `.bak` + 原子 `os.replace` 生效。
3. **隐私清除不彻底**：仅 `os.remove` 在 SSD / `.bak` / `memory_archive_*.json` 中可恢复 → 测试按 Rex 两级语义验证（Tier1 一致性清除防复活；Tier2 加密态毁钥 / 明文态 3 遍覆盖写 + 删除 + best-effort TRIM），且不为 Tier2 写不可移植的「取证不可恢复」硬断言。
4. **并发写竞争**：audio + 手动 + judgment 三线程同写 → 需单锁 + 竞争测试，否则偶发损坏难复现。
5. **mock brain 不验证真实记忆驱动行为**：`_mock_response` 是关键词回显，只能验证「注入发生」，不能验证「模型真的用上了记忆」→ LLM 行为层需单独人工 / 采样评估，测试只保证注入正确。
6. **超大记忆撑爆上下文**：注入未截断会把历史塞满 prompt → 测试要断言注入长度上限与摘要逻辑。

---

## 9. 测试基建缺口 & 待确认项

- **缺口**：无 `tests/`、无 pytest 配置、无测试依赖 → 全部需新建（阶段 0）。
- **记录判定 oracle 已确认（Archi）**：规则 A/B/C/D + 排除项、`RECURRENCE_THRESHOLD=3`、`MIN_CLASSIFY_INTERVAL=3s`、门控（is_thinking 门、单轮 ≤1 次 LLM 分类、brain 繁忙跳过）、后台线程非阻塞——已作为本计划测试 oracle。
- **schema 已 ratify（Docu + Archi），见 `docs/memory/schema.md`，测试断言据此、无漂移风险**：
  - 顶层 `{ "version": "1.0.0", "facts": [...] }`；`version` 为 semver 字符串（非 int）。
  - MemoryFact 必填 6：`id`(UUID4)/`content`/`kind`/`source`/`created_at`/`updated_at`(ISO8601)；缺 → 跳过并计入 `invalid_facts`。
  - 可选 5：`weight`(默认 0.5)/`tags`(默认 [])/`pii`(默认 false，缺失按 false)/`ttl`(默认 null)/`embedding`(默认 null，v1 恒 null)。
  - **`source` 五值（已修正，非旧三值）**：`explicit`/`inferred`/`recurring`/`judgment`/`manual`；`inferred` 默认可一键清空且 weight 更低。
  - 异常：`MemoryFileCorrupt`（解析失败 / 主文件损坏）、`MemoryVersionIncompatible`（MAJOR 不符拒绝加载）。
  - 损坏恢复：主文件 corrupt 先试 `memory.json.bak` 再上抛；`invalid_facts` 报告形态 `{"facts":[...], "invalid_facts":[{"id","reason"}]}`。
  - `RecordDecision` 独立契约：`should_store`/`reason`(受控词表)/`kind`(enum|null，与 MemoryFact.kind 同源；should_store=false 时必为 null)/`confidence`[0,1]。
  - `user_consent` 已移出 memory.json（consent.json/设置层），记忆加载不依赖它。
- **SRE blockers A/C 补断言（Archi 收口，已并入 §3.8 / §5）**：
  - **A 路径解析器**：所有副本路径派生自单一 base；`JAC_MEMORY_DIR` 覆盖生效；默认 base 跨平台正确（mac/linux `~/.jac/memory`、win `%APPDATA%/jac/memory`）；base 不可写 → `MEMORY_ENABLED=False` 优雅降级、不崩溃、不抛未捕获异常（§3.8）。
  - **C 归档留存**：压缩幂等（id 比较，不重复归档）；归档数 > `MAX_ARCHIVE_FILES(6)` → 淘汰最旧；归档字节 > `MAX_ARCHIVE_BYTES(10MB)` → 淘汰最旧直至回落；超 `ARCHIVE_RETENTION_DAYS(180)` → 自动删除；归档不绕过 2MB 活动上限（活动压缩独立触发）；`clear(pii=True)` → 含 pii 归档事实一致剥离；`clear(secure=True)` → 归档文件删除（§5 归档留存块）。
  - **pii 清除入口**：`clear(pii=True)` 仅删 pii=true（范围断言）；`clear(pii=True, secure=True)` 走 Tier2 全副本擦除；写时门控 `MEMORY_CAPTURE_PERSON_ID=False` 默认不落 pii 事实（默认不写 pii 断言）。
  - **B-J 可选断言（与既有用例合并）**：原子写无半截主文件、磁盘满主文件完好、文件权限 0600、崩溃 ≤5s 丢失窗口、日志不泄 pii 内容、`MemoryFileCorrupt` 回退 .bak —— 并入 §3.2/§3.3/§5 既有损坏与权限用例。
- **判定/录制/清除集成已定稿（Archi 5 点，三块签名全锁）**：
  - ① 频次状态：`MemoryRecorder.counts` 为**会话级内存 dict，不持久化**；`count>=RECURRENCE_THRESHOLD(3)` 且无对应 topic 事实 → `upsert(kind=topic, weight 高值, source=recurring)`，晋升后保留计数；进程重启清零；memory.json **无** `occurrences` 字段（断言）。
  - ② 正则字面量已锁定（§2 六条：EXPLICIT_SAVE_RE / WEAK_INTENT_RE / PREFERENCE_RE / DECISION_RE / QUESTION_RE / SMALLTALK_RE）+ 判定顺序 oracle；「以后」降级为 WEAK_INTENT（不直接记）。
  - ③ 集成点：`MemoryManager.record_turn()` 在 `process_response` 末尾（助手说完、is_speaking=False 后）调用一次，入队内部单后台 worker（daemon+队列 / 1-worker ThreadPoolExecutor），绝不阻塞；门控在 worker 内（not is_thinking 且 ≥3s、单轮 ≤1 次、brain 忙跳过）。
  - ④ 敏感人物 pii 双层门控：分类侧标 pii=True；写时 `MEMORY_CAPTURE_PERSON_ID=False`（默认）拒绝任何 pii=True 事实；仅 flag=True 且 source=explicit 才落库并标 pii=True。
  - ⑤ `kind` 枚举：`profile`/`preference`/`convention`/`event`/`topic`（与 RecordDecision.kind 同源，should_store=false→null）。
  - ⚠️ 术语对账：Archi 文中 recurring「importance=2~3」与 ratify schema 的 `weight`[0,1] 不一致——本计划按 schema 用 `weight` 高值（如 0.8），已请 Docu 在 schema.md 确认无 `importance` 字段残留。
- **存储与清除语义已 ratify（Rex）**：① 存储路径为 `~/.jac/memory/memory.json`（Windows `%APPDATA%/jac/memory/memory.json`），目录 0o700 / 文件 0o600，同目录含 `.bak`/`memory_archive_YYYYMM.json`/`*.tmp`/`.corrupt.*`——§2/§5 断言据此。② `clear()` 为**两级语义**：Tier1 `clear(source="inferred")` = 逻辑删除（一致性清除所有副本防复活，不需取证级）；Tier2 `clear(secure=True)` = 安全擦除（加密态销毁密钥 / 明文态 3 遍覆盖写+删除+best-effort TRIM）。③ Tier2 的「取证不可恢复」在 SSD/跨平台无法完全自动化，测试只断言可验证部分（文件消失+密钥销毁/无已知串），明文 best-effort 须在文档声明。④ **PII 双控**：写入期 `MEMORY_CAPTURE_PERSON_ID=False` 默认不持久化人物/人脸类 PII；`clear(pii=True)` 是事后补救 / 擦除权路径，二者互补都要有。⑤ 自然扩展 `clear(pii=True, secure=True)` = 范围级 Tier2（重写剔除 pii 事实 + 对文件施取证保证），`secure` 可作修饰语作用于全清或范围清。⑥ pii Tier1 断言与 inferred 平行：memory.json/`.bak`/归档中 `pii=true` 均 ==0、重载不复活；「字节无其内容」=逻辑层 store 不含该事实（与 `inferred==0` 同义），非取证级磁盘恢复，明文 v1 的 SSD 残留仍归 best-effort（§5 边界照样适用）。

---

#### ⚠️ 9.1 实现漂移核对（2026-07-22，比对 `src/memory` 实现 vs 已 ratify 契约）

> **结论：实现与已定稿（schema.md v1.0.0 + 本计划三块签名）存在系统性漂移。在 Reconcile 之前，不得据本计划写测试逻辑——按本计划写的用例会对当前实现 100% 失败。**
> 比对对象：`src/memory/models.py`、`src/memory/store.py`、`src/memory/__init__.py`（Phase 1，已合入）。
> 依据契约：`docs/memory/schema.md`（v1.0.0 锁定）、本计划 §2/§3/§5 锁定的常量与签名。
> **Phase 0 基建（目录 + pytest 配置 + 依赖 + 中性 fixture）不受影响，已先行搭建（见仓库 `tests/`）。**

**漂移矩阵（oracle → 实现 → 影响 → 严重度）：**

| # | 已定稿契约（oracle） | 当前实现（`src/memory`） | 受影响的计划章节 | 严重度 |
|---|---|---|---|---|
| 1 | 顶层信封 `{ "version": "1.0.0"(str), "facts": [...] }`；`user_consent` **移出** | `_serialize` 输出 `{ "schema_version": 1(int), "user_consent": {...}, "updated_at":..., "records": [...] }` | §3.2 全部 version/MAJOR 矩阵；§9 schema 断言；`user_consent` 被重新写回（与裁定矛盾） | **高** |
| 2 | MemoryFact 必填6：`id/content/kind/source/created_at/updated_at` | `MemoryRecord` 字段：`id/type/content/importance/source/tags/embedding/created_at/updated_at/last_accessed/access_count/occurrences/consent_scoped/ttl` | §2/§3.1 字段名 `kind→type`；无 `weight` | **高** |
| 3 | 可选5：`weight`[0,1]默认0.5 / `tags` / `pii`(bool) / `ttl` / `embedding` | 无 `weight`（代以 `importance` IntEnum 1-3）；无 `pii`（代以 `consent_scoped` bool）；`tags/ttl/embedding` 在 | §3.4 recurring 高权重(0.8)、§5 PII 清除断言全部无对应字段 | **高** |
| 4 | `source` 五值：`explicit/inferred/recurring/judgment/manual` | `MemorySource`：`explicit/implicit_profile/recurring/judgment/manual` | §2/§5 全部 `clear(source="inferred")` 与 `inferred==0` 断言落空；「一键清空 inferred」语义失效 | **高** |
| 5 | `kind` 枚举：`profile/preference/convention/event/topic`（与 RecordDecision 同源） | `MemoryType`：`user_profile_fact/preference/project_convention/decision_event/recurring_topic`（长名） | §3.4 映射、§4 注入标签、RecordDecision.kind 断言不匹配 | **中** |
| 6 | **无 `occurrences` 字段**（频次在 recorder 会话级内存计数，不持久化） | `MemoryRecord.occurrences` 持久化；`upsert`/`compact` 累加并写盘 | 直接矛盾：§2/§9①「memory.json 无 occurrences」断言将被违；频次语义由「内存计数器」变「持久化字段」 | **高** |
| 7 | 异常 `MemoryFileCorrupt` / `MemoryVersionIncompatible`（解析失败/MAJOR 不符上抛） | 无此异常类；`load()` 用 `ValueError`，损坏文件 `_quarantine_corrupt` 后**空库启动**，无版本校验 | §3.2 #1/#3/#6b「抛异常」断言、#2/#4 版本矩阵全失效 | **高** |
| 8 | `load()` 返回 `{facts, invalid_facts:[{id,reason}]}`（缺字段计入报告） | `_parse_records` 单条坏数据 `continue` 静默丢弃，无 `invalid_facts` | §3.2 #5/#7 报告断言落空 | **中** |
| 9 | 方法：`add` / `get_recent(limit,query)` / `update(id,patch)` / `save()` / `stats()` / `clear(secure,source,pii)` | `upsert` / `get(id)` / `query_by_keywords` / `query_by_tags` / `delete` / `compact` / `export` / `clear_all` / `clear_by_id` / `flush` / `close`；**无 `clear(secure/source/pii)`** | §3.1 全部 CRUD 用例、§5 整段 Tier1/Tier2 隐私清除无对应 API | **高** |
| 10 | 两级清除：`clear(source="inferred")` / `clear(pii=True)` / `clear(secure=True)` | 仅 `clear_all()` / `clear_by_id(id)`；无 source 范围清、无 pii 清、无 secure 擦除、无归档删除 | §5 整段 Tier1/Tier2/归档清除/PII 断言无支撑 | **高** |
| 11 | 归档留存(C)：`MAX_ARCHIVE_FILES=6` / `MAX_ARCHIVE_BYTES=10MB` / `ARCHIVE_RETENTION_DAYS=180` | `compact()` 仅按 `ARCHIVE_IDLE_DAYS=90` 归档低重要久未访问项；无文件数/字节上限、无留存期删除 | §5 归档留存块(C blocker)断言无支撑 | **中** |
| 12 | `MemoryManager.record_turn()` 在 `process_response` 末尾注入后台 worker；`context.memory` 句柄 | **未实现**（Phase 2）：`main.py` 的 `process_response(text,brain,speaker)` **无** MemoryStore/record_turn 集成；`context.py` 无 memory 句柄 | §4 集成测试、§3.4 维度#6/DoD#8 无法执行 | **高**（已知待 Phase 2） |
| 13 | 频次阈值 `RECURRENCE_THRESHOLD=3`、限流 `MIN_CLASSIFY_INTERVAL=3.0`（recorder/manager） | `store.py` 无此常量；recorder 未实现 | §3.4 阈值/限流用例无支撑（待 Phase 2） | **中**（已知待 Phase 2） |
| 14 | 文件权限 `DIR_MODE=0o700` / `FILE_MODE=0o600` 均由存储层设置 | `_resolve_dir` 仅对**目录** `chmod 0o700`（try/except 静默）；**文件** 0o600 未设置 | §3.8/§5 文件权限 0600 断言部分落空 | **低** |

**Reconcile 路径（二选一，需 team-lead 裁定）：**
- **(A) 改实现以贴合已 ratify 契约（推荐，尤其 #1/#3/#4/#6/#7/#9/#10 涉安全与正确性的 oracle）**：将 `schema_version`→`version`(str)、`records`→`facts`；`importance`→`weight`[0,1]；`consent_scoped`→`pii`；`implicit_profile`→`inferred`；`type`→`kind`（短名）；移除 `occurrences` 持久化（改 recorder 运行时计数）；新增 `MemoryFileCorrupt`/`MemoryVersionIncompatible` 异常与版本校验；新增两级 `clear(secure,source,pii)`；移除信封内 `user_consent`；补齐归档文件数/字节/留存期上限。
- **(B) 若实现取舍为有意，则反向重新 ratify**：更新 `schema.md` + 本计划 §2/§3/§5/§9，使 oracle 与实现一致（含 PII 用 `consent_scoped`、权重用 `importance` 1-3、`source=implicit_profile`、`occurrences` 持久化、损坏走 quarantine 而非抛异常、两级清除暂缺）。**注意**：(B) 会改变隐私可清除性语义（无 inferred 一键清、无 pii 字段、无 secure 擦除），须由 Rex/Docu 重新评估。

> **当前动作**：Phase 0 基建已立（目录/配置/依赖/中性 fixture）；**测试逻辑（Phase 1-5）暂缓至 Reconcile 裁定后**。#12/#13 为已知 Phase 2 范围，不计入实现缺陷。
