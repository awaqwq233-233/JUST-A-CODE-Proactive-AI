# 记忆格式规范（Memory Schema）

> 文档状态：**v1.0.0 锁定版**。由 architect ratify，testing-expert 据其写字段断言（`docs/memory_test_plan.md` §9）。本文件即 `memory.json` 的机器可解析契约。
>
> 风格：中文标题 + 英文术语，与 `AGENTS.md` 一致。

## 1. 目的与适用范围

本文件定义 J.A.C. 记忆子系统的本地持久化格式 `memory.json`，供以下模块一致使用：

- **recorder / 记录判定模块**：产出 `RecordDecision`（见 §10），落盘为 `MemoryFact`。
- **store / 存储层**：读写 `memory.json`、维护 `.bak`、版本迁移。
- **brain / 检索层**：按 `kind` / `tags` / `weight` / `updated_at` 召回。
- **runbook / 隐私审计**：按 `pii` / `source` 过滤与清除。

记忆是**本地优先、结构化 JSON 摘要**（`AGENTS.md`：记忆从结构化 JSON 摘要起步，再考虑向量数据库）。初版单文件，不加密（加密属逻辑层，见隐私文档）。

## 2. 顶层结构

```json
{
  "version": "1.0.0",
  "facts": [ {MemoryFact}, ... ]
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `version` | string (semver `MAJOR.MINOR.PATCH`) | 是 | 初值 `"1.0.0"`。缺失视为 `"0.0.0"`（旧版/未版本化），宽松加载，下次写回升级为当前版本。 |
| `facts` | array<MemoryFact> | 是 | 记忆数组，可为空 `[]`。 |

> **设计裁定（architect）**：顶层仅保留 `version` 与 `facts`。原草案的 `user_consent` / 顶层 `updated_at` 已移除：
> - `user_consent` 移出 `memory.json`，改由应用/设置层或同级 `consent.json` 记录——**同意生命周期不与事实数据耦合进同一文件**。
> - 顶层 `updated_at` 去掉；压缩/轮转改用各 fact 的 `updated_at` + 计数/体积。

## 3. MemoryFact（v1.0.0 锁定字段）

### 3.1 必填字段（缺失 → 跳过并计入 `invalid_facts`）

| 字段 | 类型 | 约束 |
|---|---|---|
| `id` | string (UUID4) | 全局唯一，生成后不可变 |
| `content` | string | 记忆文本，非空 |
| `kind` | enum | 见 §4 |
| `source` | enum | 见 §5 |
| `created_at` | string (ISO8601) | 带时区，如 `2026-07-01T04:00:00Z` |
| `updated_at` | string (ISO8601) | 创建时 == `created_at`；检索命中时 bump 它，**承载 recency + 热度**，与 `weight` 共同决定召回排序（见 §12 检索打分） |

### 3.2 可选字段（缺失 → 套默认值，不计入 `invalid_facts`）

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `weight` | number [0,1] | `0.5` | 召回优先级 / 重要性打分（v1 **唯一权重字段，无独立整型权重字段**）；`inferred` 来源应默认更低（建议 0.3–0.4）；`recurring` 晋升置 `0.8`（见 §12 / `RECURRING_PROMOTED_WEIGHT`） |
| `tags` | array<string> | `[]` | 检索精度用；v1 可空，由 `content` 分词兜底 |
| `pii` | boolean | `false` | 敏感标记（见 §6）；解析时若缺失 → 按 `false`（非 PII） |
| `ttl` | string\|null (ISO8601) | `null` | 易失类过期时间，供压缩逻辑使用 |
| `embedding` | array<number>\|null | `null` | **保留字段**，未来向量库衔接点（ADR-001）；v1 恒 `null` |

> **折并说明（architect）**：原**整型权重字段**已并入 `weight`(number[0,1])；原 recency / heat **独立字段** → 并入单一 `updated_at`；`occurrences`→**不持久化**，由 recorder 运行时维护 `topic_key→count` 计数器，跨阈值才落 `kind=topic`；`consent_scoped`→`pii`；`type`→`kind`。

## 4. `kind` 枚举（RATIFY，不改名）

`profile` | `preference` | `convention` | `event` | `topic`

与架构 5 类 1:1 映射：`user_profile_fact`→`profile`，`decision_event`→`event`，`project_convention`→`convention`，`recurring_topic`→`topic`，`preference` 保持。

## 5. `source` 枚举（architect 裁定：**五值，非三值**）

`explicit` | `inferred` | `recurring` | `judgment` | `manual`

> **⚠️ 与早期三值提案有意偏离（architect 裁定）**：原 `conversation` / `judgment` / `manual` 三值**不采用**。recorder 有 5 条来源路径：
> - `explicit`：用户主动告知（A 显式意图）
> - `inferred`：系统从对话**推断**出的偏好（B 隐式推断）
> - `recurring`：频次升级产出（C 路径），独立生命周期状态
> - `judgment`：判断引擎介入（D 决策）
> - `manual`：用户/CLI 手动编辑（E）
>
> 关键语义：`explicit`（用户主动告知）与 `inferred`（系统猜出）的区分是**隐私与可清除性**的核心——用户可能想一键清空所有 `inferred` 事实但保留 `explicit`；且 `inferred` 应默认更低信任 / 更低 `weight`。`recurring` 不能并入 `conversation`。测试与文档一律以五值为准。

## 6. `pii` 字段（RATIFY，进 v1.0.0）

boolean，默认 `false`，**进 v1.0.0**（不推迟到 v1.1，避免测试漂移）。它是 Runbook / 隐私说明所需的敏感标记（对应原 `consent_scoped`）。

- **安全默认**：解析时若 `pii` 缺失 → 按 `false` 处理（非 PII）。
- 存储层 v1 **仅标记**；强制门控（同意/加密）属逻辑层，不进 schema。

## 7. 版本号与迁移说明

`version` 为 semver 字符串。兼容性逻辑（测试断言基准，见 §9）：

1. **JSON 解析失败（结构损坏）** → loader 抛 `MemoryFileCorrupt`（不静默吞）。见 §8 损坏恢复。
2. **`version` 缺失** → 按 `"0.0.0"` 宽松加载，不打断；下次写入升级为当前版本。
3. **`version` MAJOR 不符**（如文件 2.x，代码支持 1.x）→ 抛 `MemoryVersionIncompatible`，**拒绝加载**，绝不静默改写。
4. **同 MAJOR 的 MINOR/PATCH 差异** → 向前/向后兼容，正常加载，忽略未知字段、保留已知字段。
5. **单条缺必填字段** → 容忍跳过，计入 `invalid_facts`，其余正常加载。缺可选字段 → 套默认，不计入。

## 8. 损坏恢复（Corruption Recovery）

主文件 `memory.json` 解析抛 `MemoryFileCorrupt` 时，loader **应先尝试 `memory.json.bak`**，再向上抛。两条路径测试均需覆盖：

- **主文件损坏 + `.bak` 有效** → 从 `.bak` 加载（并应在下次写回修复主文件）。
- **主文件损坏 + 无 `.bak`** → `MemoryFileCorrupt` 向上传播。

> Runbook §3.7「损坏」章节据此描述：优先 `.bak` 回退，无则安全重建空文件（保留 `version`）。

## 9. `invalid_facts` 报告形态（测试断言）

loader 返回结果对象含两个字段：

```json
{
  "facts": [ {MemoryFact}, ... ],
  "invalid_facts": [ { "id": "...", "reason": "missing_required:kind" }, ... ]
}
```

- `facts`：成功解析的有效 MemoryFact 列表。
- `invalid_facts`：被跳过条目的「id / 原因」列表（或计数）。测试断言该集合。
- **必填集固定为 6 个**：`id` / `content` / `kind` / `source` / `created_at` / `updated_at`。任一缺失即计入 `invalid_facts`。

## 10. RecordDecision（独立运行时契约，不进 memory.json）

介于 recorder 与 store 之间的 LLM 分类输出：

```json
{
  "should_store": true,
  "reason": "user_stated",
  "kind": "preference",
  "confidence": 0.87
}
```

| 字段 | 类型 | 必填 | 取值 |
|---|---|---|---|
| `should_store` | boolean | 是 | 是否应落盘 |
| `reason` | string | 是 | 非空；建议受控词表：`user_stated` \| `derived_preference` \| `explicit_convention` \| `observed_event` \| `topic_of_interest` \| `low_confidence` \| `duplicate` \| `pii_blocked` \| `not_factual` |
| `kind` | enum \| null | 是* | `should_store=true` 时为 §4 枚举之一；`false` 时为 `null` |
| `confidence` | number [0,1] | 是 | 模型判定应存储的置信度 |

> **注意**：RecordDecision **不持久化进 memory.json**，它是 recorder 运行时产物。其 `kind` 必须与 MemoryFact.kind **同源**（同一枚举），以保证一致性。

## 11. 示例条目（5 类）

```json
[
  {
    "id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
    "content": "用户是嵌入式工程师，常做本地多模态原型",
    "kind": "profile",
    "source": "explicit",
    "created_at": "2026-07-01T04:00:00Z",
    "updated_at": "2026-07-01T04:00:00Z",
    "weight": 0.8,
    "tags": ["职业", "背景"],
    "pii": false,
    "ttl": null,
    "embedding": null
  },
  {
    "id": "7c6b8f9a-12de-4f56-9abc-1234567890ab",
    "content": "用户偏好用中文回复",
    "kind": "preference",
    "source": "inferred",
    "created_at": "2026-07-02T06:30:00Z",
    "updated_at": "2026-07-02T06:30:00Z",
    "weight": 0.4,
    "tags": ["语言"],
    "pii": false,
    "ttl": null,
    "embedding": null
  },
  {
    "id": "3d2e1f0a-9b8c-7d6e-5f4a-0b1c2d3e4f5a",
    "content": "每周一同步一次项目进度",
    "kind": "convention",
    "source": "explicit",
    "created_at": "2026-07-03T09:15:00Z",
    "updated_at": "2026-07-03T09:15:00Z",
    "weight": 0.6,
    "tags": ["节奏"],
    "pii": false,
    "ttl": null,
    "embedding": null
  },
  {
    "id": "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d",
    "content": "2026-07-04 演示了主动感知原型",
    "kind": "event",
    "source": "judgment",
    "created_at": "2026-07-04T12:00:00Z",
    "updated_at": "2026-07-04T12:00:00Z",
    "weight": 0.5,
    "tags": ["里程碑"],
    "pii": false,
    "ttl": null,
    "embedding": null
  },
  {
    "id": "b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d6e",
    "content": "用户对本地优先 AI 助手持续感兴趣",
    "kind": "topic",
    "source": "recurring",
    "created_at": "2026-07-05T03:45:00Z",
    "updated_at": "2026-07-05T03:45:00Z",
    "weight": 0.5,
    "tags": ["兴趣"],
    "pii": false,
    "ttl": null,
    "embedding": null
  }
]

## 12. 检索打分（Retrieval Scoring）

召回排序 **只用两个字段**：`weight` 与 `updated_at`。

- `weight`（number [0,1]）：事实 / 用户重要性，越高越优先。
- `updated_at`（ISO8601）：recency + 热度（检索命中会 bump 此字段）。

> **无 `importance`、无 `access_count`**：v1 检索**不**使用 `importance`（该字段已被 `weight` 完全取代、schema 中不存在）也不使用 `access_count`（已并入 `updated_at`）。任何引用这两个名字的代码 / 测试均属 schema 漂移，应修正为 `weight` + `updated_at`。

**打分公式（参考实现，非契约强制）**：

```
score = 0.7 * weight + 0.3 * recency_norm(updated_at)
```

`recency_norm` 为 `updated_at` 到当前的衰减归一（如指数衰减或线性分桶），值域 [0,1]。

**`recurring` 晋升（C 路径 → 落 `kind=topic`）**：

- 由 recorder 运行时 `topic_key→count` 计数器跨阈值触发；晋升落库时 `weight` 置 **`0.8`**，对应代码侧常量 `RECURRING_PROMOTED_WEIGHT`（避免魔法数字）。
- 落库 fact 的 `source` 仍为 `recurring`，`updated_at` 取晋升时刻，`pii` 默认 `false`。
- 测试断言基准：晋升后 fact 满足 `weight == 0.8` 且 `source == "recurring"`（见 `docs/memory_test_plan.md` §9）。
```
