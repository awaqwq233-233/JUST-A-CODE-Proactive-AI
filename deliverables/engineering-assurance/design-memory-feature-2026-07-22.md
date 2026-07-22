# 记忆功能子系统 · 架构设计方案（待确认）

**日期**：2026-07-22
**工作流**：2 系统设计
**参与成员**：Archi（架构）、Rex（SRE）、Tessa（测试）、Docu（文档）

---

## 📌 TL;DR（执行摘要）

- **目标**：让 J.A.C. 持久化「关键信息」，忽略「日常提问」；本地优先、隐私优先、JSON 起步、绝不阻塞实时交互。
- **方案**：新建独立包 `src/memory/`（store / recorder / retriever / manager）；判定逻辑「规则优先 + LLM 辅助」，触发条件覆盖显式指令/偏好事实/反复主题/决策约定，并明确排除闲聊与一次性问答；关键词检索后注入 `system_prompt`；默认存用户目录 `~/.jac/memory/`。
- **严重度分布**：🔴严重 0 项 / 🟠高 1 项（记录判定误判，最高风险）/ 🟡中 2 项（存储格式分歧 JSON vs JSONL、现有 `main.py` 明文 `print` 转录的隐私缺口）/ 🟢低 若干（上下文膨胀、判定依赖 LocalBrain 可用性等）。
- **阻塞 / 非阻塞**：设计层面无硬阻塞；需用户拍板 **2 项设计选择**（存储格式、首轮实施范围）后即可开工。

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| 整体评级 | 🟡 方案已成型，待确认 2 项设计选择 |
| 阻塞项数量 | 0（设计层面）；2 项待用户决策 |
| 关键行动项 | 6 个阶段（见行动清单） |
| 建议下一步 | 用户确认设计选择 → 进入 Phase 0 测试基建 + Phase 1 存储骨架 |

---

## 需求与目标

1. **记住关键信息**：用户画像事实、偏好/习惯、项目约定、重要决策/事件、反复出现的主题。
2. **忽略日常提问**：一次性问答、闲聊、纯任务执行结果、未授权的敏感内容不落地。
3. **本地优先 / 隐私优先**：记忆只存本机，绝不主动上传云；可见同意、可查看/导出/清除、本地过滤、日志控制（`AGENTS.md` 要求）。
4. **JSON 起步，向量库后置**：`AGENTS.md` 原话「记忆从结构化 JSON 摘要起步，再考虑向量数据库」，初版零新增重依赖。
5. **不阻塞实时交互**：当前 STT/LLM/TTS 均非流式、延迟高；记忆的判定与落盘必须全异步、门控、限流，用户毫无感知。
6. **统一模型入口**：所有 LLM 调用（含记忆分类）必须经 `LocalBrain`，不得绕过（`AGENTS.md` 工程指导）。

---

## 高层设计

### 模块位置与文件结构
**决策：新建独立包 `src/memory/`，不并入 `context.py`**。`SharedContext` 是进程内运行时状态（视觉/转录/状态灯），进程退出即失；长期记忆是跨进程持久化、需独立读写/轮转/隐私控制，生命周期与关注点完全不同。

| 文件 | 职责 |
|------|------|
| `src/memory/__init__.py` | 包导出；`MemoryManager` 工厂（按配置构造 store+recorder+retriever）。 |
| `src/memory/models.py` | 数据类：`MemoryRecord`、`MemoryType`(枚举)、`Importance`、`MemorySource`、`RetrievalResult`。纯结构，便于单测。 |
| `src/memory/store.py` | `MemoryStore`：JSON 加载 / 原子落盘 / 轮转 / 压缩；CRUD（`upsert`/`get`/`delete`/`query_by_keywords`/`query_by_tags`）；预留 `embedding` 字段与向量索引钩子；自带独立锁。 |
| `src/memory/recorder.py` | `MemoryRecorder`：**记录判定逻辑核心**。规则阶段（正则/关键词，零成本）+ LLM 辅助阶段（经 `LocalBrain.think()` 分类，异步门控限流）。产出候选 `MemoryRecord` 交给 store。 |
| `src/memory/retriever.py` | `MemoryRetriever`：对话前检索相关记忆（v1 关键词+标签+权重打分；未来加 embedding 余弦）。返回注入 prompt 的精简列表。 |
| `src/memory/manager.py` | `MemoryManager`：门面，对 `main.py` 暴露 `retrieve_for_prompt(user_text, vision_info)` 与 `record_turn(user_text, response, window)`。 |
| `src/memory/prompts.py` | LLM 分类 prompt 模板与记忆注入模板（集中管理，便于文档维护）。 |

### 数据模型
**分类（`MemoryType`）**：`user_profile_fact`（用户画像事实）/ `preference`（偏好习惯）/ `project_convention`（项目约定）/ `decision_event`（决策事件）/ `recurring_topic`（反复主题，频次自动升级）。

**`MemoryRecord` 字段**：`id`(uuid 短哈希)、`type`(枚举)、`content`(归一化文本)、`importance`(1-3)、`source`(explicit/implicit_profile/recurring/judgment/manual)、`tags[]`、`embedding`(预留, 初版 null)、`created_at`/`updated_at`/`last_accessed`(ISO8601)、`access_count`、`occurrences`(频次阈值判断)、`consent_scoped`(敏感边界标记, 默认 false)、`ttl`(可选过期)。

**顶层信封**：`{ schema_version, user_consent{granted, granted_at, notice_shown}, updated_at, records[] }`（初值 `schema_version: 1`）。

### 存储格式（初版，见「风险与权衡」中的格式分歧）
- **路径**：默认用户目录 `~/.jac/memory/memory.json`（Windows `%APPDATA%/jac/...`，macOS/Linux `~/.jac/...`），`JAC_MEMORY_DIR` 可覆盖。天然不进 git，随用户走。
- **结构**：初版单文件 `memory.json`（一个 `records` 数组），原型量级最简单。
- **读写**：启动时一次性加载进内存 dict；检索只读内存（亚毫秒）；落盘在**后台持久化线程**防抖批量刷（每 ~5s 或累计 N 次）。
- **原子写**：先写 `memory.json.tmp` → `fsync` → `os.replace` 覆盖；保留上一版 `memory.json.bak` 用于损坏恢复。
- **轮转/压缩**：`records > 2000` 或文件 `> 2MB` 触发 compaction（丢弃 `ttl` 过期、按 `content` 哈希合并重复、久未访问低 importance 项归档到 `memory_archive_YYYYMM.json`）。
- **强制 flush**：进入 `SLEEP` 或捕获 `KeyboardInterrupt` 时立即落盘，保证强杀也不丢已提交记忆。
- **向量衔接**：每条记录预留 `embedding`；未来用本地嵌入模型填充，并维护本地向量索引（sqlite-vec / chroma / numpy），`store.query` 接口不变。

### 记录判定逻辑（核心）
**策略：规则优先 + LLM 辅助**（非纯规则——抓不到隐式偏好；非纯 LLM——每轮调 LLM 成本/延迟不可接受）。

**触发条件（满足任一即记为「重要」候选）**：
- **A. 显式保存意图（规则高置信，直接记）**：命中 `记住|记一下|别忘了|请记住|帮我记|以后|提醒我|remember|don't forget` 等 → 抽取命题，`source=explicit`。
- **B. 用户画像 / 偏好 / 习惯事实**：命中 `我喜欢|我不喜欢|我习惯|叫我|我是|我家|我不吃|我爱|我讨厌` 等 → 规则识别意图，LLM 抽取归一化 `content`+`tags`，`source=implicit_profile`。
- **C. 反复出现的主题（自动升级）**：对每轮文本轻量归一化，维护 `topic_key → occurrences`；同一 key 累计 ≥ `RECURRENCE_THRESHOLD`(默认 3) → 自动建/升级 `recurring_topic`，`importance` 提到 2-3，`source=recurring`，同 key `upsert` 防刷屏。
- **D. 重要决策 / 约定 / 承诺**：命中 `我们决定|约定|答应|计划|下次|承诺` 等 → LLM 确认后记录。
- **E. 判断引擎信号（未来）**：`JUDGMENT_ACTIVATED` 且介入理由指向承诺/事件时作为候选。**v1 不接判断引擎**，避免环境观察污染长期记忆。

**排除项（不记录）**：一次性问答（`？/?` 结尾且无上述模式）、闲聊（问候/笑话/情绪宣泄无事实）、纯任务执行结果、未授权敏感内容（摄像头识别的具体人物身份需 `consent_scoped=true`+显式同意，默认不记）、原始连续转录（只存抽取事实）。

**判定流程（时机/频率/成本）**：
1. **时机**：每轮对话**结束后**（助手说完，`process_response` 末尾）取 `(user_text, response, window)` 交给 `MemoryRecorder`，**后台线程**执行，绝不阻塞用户。
2. **规则阶段**（即时、零成本）：跑正则/关键词；高置信命中 A/B/D → 直接 `upsert`。
3. **LLM 辅助阶段**（仅规则模糊/疑似偏好或重复时）：调用 `LocalBrain.think()`（`max_tokens=128`，要求输出 `{decision, type, content, tags}` JSON），解析后 `upsert` 或丢弃。
4. **门控与限流（防阻塞/防竞争）**：仅当 `not context.is_thinking`（大脑空闲）才发起分类；距上次分类 ≥ `MIN_CLASSIFY_INTERVAL`(默认 3s) 才允许；单轮最多 1 次；brain 繁忙则跳过本轮（宁可不记，也不拖慢交互）。

### 与现有代码集成点
- `main()` 实例化 `memory = MemoryManager(...)`，透传进 `handle_user_text(...)` 与 `process_response(...)`（各加 `memory=None` 形参即可；判断引擎 daemon 的 `process_response` 调用 lambda 一并传入）。
- **读**：`process_response` 构造 prompt 前调 `retrieve_for_prompt`，把相关记忆拼进 `system_prompt`（文本模式与 `think_with_image` 同法注入）。
- **写**：`process_response` 末尾后台触发 `record_turn`。
- **视觉主循环**：每帧不碰记忆，避免 30fps 开销；记忆只在对话轮读写。
- **SLEEP/AWAKE 与退出**：进入 `SLEEP` 或 `finally` 调 `memory.flush()` 强制落盘；唤醒无特殊逻辑。
- **控制台输入**：走同一 `handle_user_text(bypass_wake=True)` → `process_response`，记忆自动覆盖，无需额外分支。
- **判断引擎**：v1 不向其写记忆（易失状态，默认不记）。

### 检索 API 与注入
- `MemoryManager.retrieve_for_prompt(user_text, vision_info="") -> str`；底层 `MemoryStore.query(keywords, tags, k=5)` + `MemoryRetriever.score(...)`。
- **v1 策略（无 embedding）**：对用户文本+最近转录窗口分词，与 `content`+`tags` 做词重叠打分（TF 加权），叠加 `importance` 与 `recency` 权重，取 top-K（默认 5）；`content` 过长截断。
- **注入形式**：仅当检索到相关记忆时，向 `system_prompt` 追加块（总字符 ≤ ~300）：
  ```
  【长期记忆（仅供参考，不要复述给用户）】
  - [偏好] 用户不喝咖啡，偏好茶饮。
  - [用户画像] 用户叫小明，是学生。
  - [约定] 明早 10 点开会。
  ```
  命中后更新该记录 `last_accessed`/`access_count`（热度）。

### 隐私与同意
- **本地存储、不上云**：默认全本机 JSON，无外发；未来云同步需显式同意+可见边界。
- **用户可查看/导出/清除**：复用控制台 stdin，暴露文本命令 `记忆 列表` / `记忆 导出 <path>` / `记忆 清除 <id>` / `记忆 清除 全部`（二次确认）。
- **日志控制**：记忆内容默认不进 INFO 级日志；仅 `JAC_MEMORY_LOG=debug` 记摘要。
- **敏感边界**：人物身份默认不记（`consent_scoped`+显式同意才记）；只存抽取事实不存原始转录；任何未来云端发送记忆需显式同意。
- **可见同意**：首次运行一次性提示「J.A.C. 会把重要信息保存在你本机，不会上传；可随时查看/导出/清除」，写 `user_consent.notice_shown=true`。
- **开关**：`MEMORY_ENABLED`(总开关)、`MEMORY_CAPTURE_PERSON_ID`(默认 false)。

---

## 关键决策记录（ADR）

**ADR-001: J.A.C. 持久记忆子系统架构** — 状态：Proposed ｜ 日期：2026-07-22

| 决策点 | 选项 | 结论 |
|--------|------|------|
| 代码位置 | 并入 `context.py` vs 新建 `src/memory/` 包 | **新建包**（生命周期/关注点分离） |
| 存储技术 | JSON 文件 vs 向量库 | **JSON 起步**（零依赖） |
| 存储路径 | 项目目录 vs 用户目录 | **用户目录 `~/.jac/`**（隐私/不提交） |
| 记录判定 | 纯规则 vs 规则+LLM vs 纯 LLM | **规则优先 + LLM 辅助** |
| 检索方式 | 关键词 vs 语义 | **关键词起步，预留 embedding** |

**影响**：补足 `codingLOG.md` 差距，个性化陪伴可落地，JSON 易导出/清除/审计；需新增后台持久化线程与原子写/压缩逻辑，新增经 `LocalBrain` 的 LLM 分类路径；需重新审视判断引擎与记忆的关系（v1 隔离）。

---

## 可运维性（Rex 评审摘要）

- **存储位置**：默认用户目录 `~/.jac/memory/`（`0700`），`JAC_MEMORY_DIR` 可覆盖，天然不进 git、随用户迁移。
- **崩溃安全**：原子写（tmp→fsync→os.replace）+ 保留 `.bak`；损坏文件隔离为 `.corrupt.<ts>` 而非静默丢弃；启动自检回退 `.bak`、不崩溃、不静默清空。
- **并发**：单写者 + 异步队列——所有 `record()` 仅「加锁追加内存 + `queue.put`」，由单一后台 flush 线程落盘，热路径（音频/主循环）不碰磁盘。
- **资源异常**：磁盘满（写阶段 & rename 阶段分别捕获，主文件不被破坏，有告警、暂停转录写入而非崩溃）；权限不足优雅降级；超大文件有加载上限。
- **性能**：`record()` 微秒级；磁盘 I/O 全在后台；视觉每帧绝不持久化。
- **备份/迁移**：记忆目录整体可复制即备份；复用 `DEPLOY_GUIDE.txt` 离线迁移新增「复制 `~/.jac/memory/`」一步；加密后密钥不随机器迁移（需带口令导出/导入）。
- **隐私缺口（已发现）**：当前 `main.py` 直接用 `print()` 明文打出转录与回复，是既有隐私暴露点；记忆子系统应引入**受控 logging + 脱敏**，并将 `SharedContext` 转录环形缓冲的留存/开关纳入日志控制。
- **上线前检查清单雏形**：崩溃安全 / 并发 / 资源异常 / 隐私合规 / 生命周期与运维 五类 Go/No-Go 项（详见成员原始产出）。

> ⚠️ **与架构方案的分歧**：Rex 推荐「JSONL 事件日志 + 周期压缩快照」（`memory_events.jsonl` + `knowledge.json`），理由是追加写天然崩溃可恢复、未来易接向量库重放；Archi 推荐「单文件 JSON + compaction」。两者取舍见「风险与权衡」，需用户拍板。

---

## 测试策略（Tessa 计划摘要）

- **现状**：项目**测试基建为 0**——无 `tests/`、无 pytest 配置、`requirements.txt` 无测试依赖。需 Phase 0 全新建。
- **利好**：`LocalBrain(backend="mock")` 可离线跑，集成测试无需 LM Studio；`JudgmentEngine` 的「LLM 判定+优雅降级」模式可直接复用给记录判定。
- **接口草案**：`src/memory/store.py` MemoryStore、`src/memory/recorder.py` should_record、`SharedContext.memory` 扩展、注入点在 `process_response` 拼【已知信息】入 system_prompt（⚠️ 待 Archi 确认）。
- **单元测试**：MemoryStore 增删改查 / JSON 损坏恢复（截断、非法、版本不符、非 dict、无 bak）/ 原子写 / RecordJudge 黄金数据集与误判率 / 12 个示例用例。
- **集成测试**：mock LocalBrain 验证注入、主循环读写时机、状态机 SLEEP/AWAKE、控制台输入、多轮累积、判定异常不阻断对话。
- **边界异常**：空 / 超大（10k 条、单条>100KB、prompt 长度上限）/ 并发（audio+手动+judgment 三线程用 `threading.Barrier` 制造竞争）/ 隐私清除（字节不可搜到明文）/ 冲突合并。
- **覆盖率与 CI**：建议 `src/memory` ≥90%、context 记忆扩展 ≥85%、main 注入分支 ≥70%，全局 `--cov-fail-under=85`；新增 `requirements-test.txt`（pytest/cov/mock/timeout/xdist/hypothesis）；CI 用 monkeypatch 拦 requests。
- **分阶段执行**：0 基建 → 1 存储单测 → 2 判定单测（优先建黄金集）→ 3 集成 → 4 边界 → 5 E2E → 6 CI 固化。
- **记录判定验收硬指标（建议进 DoD）**：显式保存指令 100% 判 IMPORTANT；闲聊/一次性问答误存率 ≤5%；LM Studio 不可用时纯规则仍确定可运行；每次判定 `reason` 非空可解释；重复主题 N 次（默认 3）自动升级 IMPORTANT。
- **关键风险**：① 记录判定误判（最高）② JSON 损坏/原子写 ③ 隐私清除不彻底 ④ 并发写竞争 ⑤ mock brain 只验注入、验不了模型真用上 ⑥ 超大记忆撑爆上下文。

---

## 文档结构（Docu 大纲摘要）

新建 `docs/memory/` 目录，下设：
- `docs/memory/README.md` — 用户向「记忆功能」章（记忆是什么/记住了什么/如何知道被记住/查看导出清除/隐私/FAQ）。
- `docs/memory/schema.md` — JSON schema 规范（`schema_version`、顶层结构、单条字段、5 类边界、示例、版本迁移、写入读取契约）。
- `docs/memory/runbook.md` — 运维 Runbook（查看/清除某条或全部/导出/迁移/三类故障排查：不写入·误写入·损坏/隐私审计/回滚）。
- `docs/memory/privacy.md` — 隐私说明（本地存储位置、是否加密、同意机制、敏感数据边界、清除不可逆性、日志控制）。
- 主 `README.md` 与 `AGENTS.md` 各加**一行指针链接**（不复制内容）；`AGENTS.md` 把「持久记忆」从「未实现」更新为「已实现」。

---

## 风险与权衡

1. **🟠 记录判定误判（最高风险）**：纯规则漏掉隐式偏好，纯 LLM 成本/延迟不可接受 → 采用「规则优先 + LLM 辅助 + 门控限流」，并以 Tessa 的黄金数据集量化误判率（闲聊误存 ≤5%）兜底。
2. **🟡 存储格式分歧（JSON 单文件 vs JSONL+快照）**：
   - *单文件 JSON*（Archi）：实现简单、导出/清除/排查最直观，原型量级（数百~低千条）足够；靠原子写+`.bak` 解决损坏。
   - *JSONL 事件日志 + 快照*（Rex）：追加写天然崩溃可恢复、未来易接向量库重放、磁盘增长与文件大小解耦；代价是读时需重放/维护快照、实现稍重。
   - **建议**：v1 先用单文件 JSON + 原子写 + `.bak`（满足原型需求且实现轻）；把 JSONL+快照作为「规模化/向量化」阶段的 hardening 选项。**最终以用户选择为准。**
3. **🟡 现有隐私暴露点**：`main.py` 直接 `print()` 明文转录/回复，违反 `AGENTS.md` 日志控制 → 记忆子系统实施时一并引入受控 logging + 脱敏（替换明文 print），并纳入 Runbook。
4. **🟢 上下文膨胀**：超大记忆撑爆 prompt → top-K(5) + 总字符 ≤300 截断。
5. **🟢 判定依赖 LocalBrain 可用**：LM Studio 未加载时分类不可用 → 纯规则模式仍确定运行，brain 繁忙则跳过本轮。
6. **🟢 测试基建为 0**：需先建 Phase 0，否则无法验证验收指标。

---

## ✅ 行动清单（分阶段，待确认后执行）

| # | 阶段 | 负责角色 | 紧急度 | 预期完成 |
|---|------|---------|--------|---------|
| 1 | Phase 0：测试基建（pytest + `requirements-test.txt` + `tests/` 骨架 + mock brain 夹具） | Tessa / Rex | P0 | 确认后第 1 批 |
| 2 | Phase 1：`src/memory/models.py` + `MemoryStore`（加载/原子写/.bak/compaction）+ 单测 | Archi / Cody(审查) | P0 | 紧随 Phase 0 |
| 3 | Phase 2：`MemoryRecorder` 规则判定 + 黄金数据集单测（优先，验证验收指标） | Archi / Tessa | P0 | 紧随 Phase 1 |
| 4 | Phase 3：检索 + 注入 `system_prompt` + `main.py` 集成 + 集成测试 | Archi / Tessa | P1 | Phase 2 后 |
| 5 | Phase 4：隐私与同意（开关、控制台命令、受控 logging 替换明文 print） | Archi / Rex | P1 | 与 Phase 3 并行 |
| 6 | Phase 5：`docs/memory/*`（schema/README/runbook/privacy）+ 更新 AGENTS/README 指针 | Docu | P2 | 集成完成后 |
| 7 | Phase 6：CI 固化 + 全量回归 + Go/No-Go 检查清单核对 | Tessa / Rex | P2 | 末批 |

---

## ⚠️ 待完善 / 已知局限

- 存储格式（JSON 单文件 vs JSONL+快照）尚待用户拍板。
- 首轮实施范围（完整 6 阶段 vs 先做 MVP）尚待用户拍板。
- 判断引擎（v1）与记忆明确隔离，未来接入需单独 ADR。
- 静态加密（Fernet + 钥匙串/口令）作为路线图项，v1 默认明文 JSON。
- 项目无任何测试基建，Phase 0 是前置硬依赖。
- `main.py` 明文 `print` 转录属既有问题，随记忆子系统一并治理。

---

## 📚 数据来源 & 成员产出索引

- Archi（架构师）原始产出：完整架构设计方案（设计原则、模块结构、数据模型、存储格式、记录判定逻辑、集成点、检索 API、隐私、ADR-001）；同步草稿 `docs/memory_design_draft.md`。
- Rex（SRE）原始产出：可运维性与可靠性设计评审（存储位置、崩溃安全、并发单写者队列、资源异常、性能、备份迁移、隐私缺口、Go/No-Go 清单）。
- Tessa（测试专家）原始产出：《记忆功能子系统 · 测试计划》（现状核对、9 节测试策略、判定验收硬指标、分阶段执行、6 大风险）。
- Docu（技术文档师）原始产出：文档结构大纲（schema/README/runbook/privacy 四份 + 主文档指针 + 跨团队依赖对齐）。

---

> 本报告由工程保障团队 AI 协作生成，关键决策请由人类工程负责人复核。
