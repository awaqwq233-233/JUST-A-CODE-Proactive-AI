# 记忆功能 Runbook（用户 / 运维向）

> 文档状态：v1.0.0 配套运维手册，与 `docs/memory/schema.md`（数据契约）配套。
> 用户向的"记忆是什么 / 如何查看"见 `docs/memory/README.md`；隐私细则见 `docs/memory/privacy.md`。
> 风格：中文标题 + 英文术语，与 `AGENTS.md` 一致。

## 1. 何时使用本 Runbook

适用场景：
- 查看当前记忆内容
- 清除某一条 / 按来源 / 全部清除
- 导出备份 / 迁移到新机器
- 排查记忆故障（不写入 / 误写入 / 损坏）
- 隐私审计（含 PII 与 `inferred` 来源统计）

## 2. 前置条件与所需权限

- 记忆文件位于 `<root>/memory/`（见 §3.3），需该目录读/写权限。
- 目录权限 **700**、文件权限 **600**（Windows 靠 `%APPDATA%` 用户私有）。
- 清空/导出建议在**应用关闭时**进行，避免与应用内持久化线程争用 `.bak`；应用内「查看/导出/清除」功能则无需手动停。
- 环境变量 `JAC_MEMORY_DIR` 可整体改目录位置（测试/便携版用）。

> **数据模型速查（详见 schema.md）**：`kind` 枚举见 §4（`profile` / `preference` / `convention` / `event` / `topic`）；`source` 枚举见 §5（`explicit` / `inferred` / `recurring` / `judgment` / `manual`）；完整字段表见 §3。

## 3. 查看当前记忆（§3.3）

v1 为明文 JSON，两种查看方式：

**方式 A：直接打开文件**
- macOS / Linux：`~/.jac/memory/memory.json`
- Windows：`%APPDATA%/jac/memory/memory.json`
- 可用任意编辑器打开。**含个人数据，不要在共享/同步盘打开。**

**方式 B：应用内「查看/导出」**
- 走应用内功能，自动按 `kind` / `source` / `pii` 过滤展示。

按来源过滤示例（运维/排查用，直接读 JSON）：
```bash
# 查看所有 inferred（系统推断）事实
jq '.facts[] | select(.source=="inferred")' "$HOME/.jac/memory/memory.json"
```

> 同目录文件：`memory.json` / `memory.json.bak`（上一次成功写回前的好版本）/ `memory_archive_YYYYMM.json`（按月归档）/ `consent.json`（同意记录）。

## 4. 清除某一条 / 按来源 / 全部（§3.4）

> ⚠️ **不可逆警告**：以下清除均为永久删除，无回收站、无云端副本（见隐私 §4.5）。全部清除前建议先导出（§5）。

- **清除某一条**：按 `id` 删除对应 fact。
- **按来源清除（隐私常用）**：一键清除所有 `inferred`（系统推断）事实，保留 `explicit`（用户主动告知）：
  ```bash
  jq '.facts |= map(select(.source != "inferred"))' \
     "$HOME/.jac/memory/memory.json" > /tmp/m.json \
     && mv /tmp/m.json "$HOME/.jac/memory/memory.json"
  ```
- **全部清除**：清空 `facts` 数组（保留 `version`）。应用内提供对应按钮；手动则置 `{"version":"1.0.0","facts":[]}`。

> 应用内清除走持久化线程，会自动刷新 `.bak`（见 §7）。手动改文件后建议重启应用以重新加载。

## 5. 导出备份（§3.5）

- 应用内「导出」生成 JSON（v1 明文；**若启用按敏感类别加密，则导出必须带口令**——见隐私 §4.2）。
- 手动备份：复制整个 `<root>/memory/` 目录即可（含 `.bak` / 归档 / `consent.json`）。
- 迁移/回滚均依赖此备份。

## 6. 迁移到新机器（§3.6）

复用 `DEPLOY_GUIDE.txt` 思路，记忆相关步骤：
1. 在旧机器导出/复制 `<root>/memory/` 整个目录（务必包含 `consent.json` 与 `.bak`）。
2. 新机器放置到对应 `<root>/memory/`（路径用 `JAC_MEMORY_DIR` 可改）。
3. 校验 `version` 为 `1.0.0`、文件权限 600 / 目录 700。
4. 若启用了加密，导出/导入必须走**带口令**流程（钥匙串密钥不随机器转移）。
5. 启动应用，确认 load 成功；失败则见 §7 恢复。

> 记忆数据默认在用户主目录，**不进 git**；仅当 `JAC_MEMORY_DIR` 指向项目内（如 `data/`）时，需在 `.gitignore` 加 `data/` 并排除出 PyInstaller onedir 构建。

## 7. 故障排查（§3.7）

三类故障（与测试策略对齐：不写入 / 误写入 / 损坏）。

### 7.1 不写入（判定未触发 / 路径错误 / 权限 / 容量）
- 现象：对话后记忆无新增。
- 排查：`RecordDecision.should_store` 是否 false（看 `reason`：`low_confidence` / `duplicate` / `not_factual` / `pii_blocked`）；`JAC_MEMORY_DIR` 是否指向可写目录；目录/文件权限是否 700/600；是否达容量上限（压缩逻辑看 `updated_at` + `ttl`）。

### 7.2 误写入（错误归类 / 敏感数据）
- 现象：记了不该记的，或 `kind` / `source` 错。
- 处置：按 `id` 清除该条并标记 `redacted`（见隐私 §4.4）；若是 `pii` 误标或敏感转录，立即清除并做隐私审计（§8）；若是 `inferred` 误记，可批量清 `inferred`（§4）。

### 7.3 损坏（JSON 解析 / schema 不符）
- 现象：应用启动报 `MemoryFileCorrupt` / `MemoryVersionIncompatible`。
- **自动恢复（写实）**：启动 load `memory.json` 失败（解析/schema）→ 自动尝试 `memory.json.bak` → 成功则用其重写 `memory.json`；`.bak` 也失败 → **空启动**并把坏文件隔离为 `memory.json.corrupt.<时间戳>`，**绝不静默清空**。
- **手动恢复**：把 `memory.json.bak` 复制为 `memory.json` 后重启：
  ```bash
  cp "$HOME/.jac/memory/memory.json.bak" "$HOME/.jac/memory/memory.json"
  ```
- **`.bak` 约定（运维侧）**：由**后台持久化线程**在每次 flush「写回替换」**之前**，若当前 `memory.json` 存在且有效，先复制为 `.bak`（覆盖）。首写无 `.bak`；之后每次刷新。写后读回校验失败则本轮回滚、不更新 `.bak`。可选保留两代 `.bak` + `.bak2`。
- 与 schema §8 一致：`version` 缺失 → `0.0.0` 宽松加载下次升级；MAJOR 不符 → `MemoryVersionIncompatible` 拒绝加载。

> 单条 fact 缺必填字段不会拖垮全部：loader 容忍跳过并计入 `invalid_facts`（schema §9），其余正常加载。

### 7.4 写入门控（敏感人物，默认不记）

**双层门控，防止敏感人物身份被落库**（与隐私 §5 呼应；architect 裁定 ④）：

1. **分类层**：recorder 对「摄像头具体人物 + 姓名 / 身份」类事实标 `pii=True`（如"这是张三""张三是家人"）。
2. **写时层（store 拒存）**：由环境变量 `MEMORY_CAPTURE_PERSON_ID` 控制落库判定——

| `MEMORY_CAPTURE_PERSON_ID` | fact 的 `pii` | fact 的 `source` | 是否落库 |
|---|---|---|---|
| `False`（**默认**） | 任意 | 任意 | **拒存**：任何 `pii=True` 事实均不写入，`RecordDecision.reason="pii_blocked"` |
| `True` | `True` | `explicit`（用户主动告知） | 落库 |
| `True` | `True` | 非 `explicit`（inferred / recurring / judgment / manual） | **拒存**（仅显式告知的敏感人物才允许记） |
| `True` | `False` | 任意 | 正常落库 |

**默认行为**：`MEMORY_CAPTURE_PERSON_ID` 缺省为 `False` → store 拒存任何 `pii=True` 事实，即"敏感人物默认不记"。

> **排查提示（见 §7.1）**：若预期该记的敏感人物没记上，先确认 `MEMORY_CAPTURE_PERSON_ID=True` 且来源为 `explicit`；若不应记的记上了，按 `id` 清除并做隐私审计（§8）。

## 8. 隐私审计（§3.8）

列出存储位置、加密状态、含 `pii` 的条目与 `inferred` 来源条目计数：
```bash
jq '{ total: (.facts|length),
      pii: ([.facts[]|select(.pii)]|length),
      inferred: ([.facts[]|select(.source=="inferred")]|length),
      kinds: (.facts|group_by(.kind)|map({(.[0].kind): length})) }' \
   "$HOME/.jac/memory/memory.json"
```
供用户核验"记了什么、哪些敏感、哪些是我没主动说的"。

## 9. 回滚 / 升级路径（§3.9）

- **回滚**：从 §5 备份恢复整个 `<root>/memory/`。
- **升级**：`version` 同 MAJOR 的 MINOR/PATCH 差异向前/向后兼容（schema §7）；跨 MAJOR 由迁移脚本处理（当前无，未来 ADR 定义）。
- `consent.json` 与记忆同目录、同权限、同备份、同迁移（见隐私 §4.1 / §4.6）。
