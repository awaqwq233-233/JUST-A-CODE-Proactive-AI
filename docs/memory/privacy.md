# 记忆功能隐私说明（Privacy）

> 文档状态：v1.0.0 配套隐私细则，与 `schema.md`、`runbook.md` 配套。用户向概览见 `docs/memory/README.md`。
> 风格：中文标题 + 英文术语，与 `AGENTS.md` 一致。

## 1. 核心隐私承诺

本地优先、可见同意、用户可控制（查看 / 导出 / 清除）、日志受控。记忆内容**默认不出网**，仅本地持久化（呼应 `AGENTS.md`「工程指导—谨慎对待隐私」）。

## 2. 本地存储位置（§4.1）

- 记忆文件：`<root>/memory/memory.json`
  - macOS / Linux：`~/.jac/memory/memory.json`
  - Windows：`%APPDATA%/jac/memory/memory.json`
- 同目录：`memory.json.bak`、`memory_archive_YYYYMM.json`、`consent.json`。
- 环境变量 `JAC_MEMORY_DIR` 可改整个目录位置（测试/便携版用）。
- **不进 git**：默认在用户主目录，物理上不在仓库内。仅当 `JAC_MEMORY_DIR` 指向项目内（如 `data/`）时，需在 `.gitignore` 加 `data/` 并排除出 PyInstaller onedir 构建。
- 权限：目录 **700**、文件 **600**（Windows 靠 `%APPDATA%` 用户私有）。

## 3. 是否加密（§4.2）

- **v1：明文 JSON**。理由：本地优先、单用户本地原型、记忆内容不出网，作为可接受基线。
- **路线图项（默认关闭）**：静态加密 `cryptography.Fernet` + 密钥来自系统钥匙串或用户口令（PBKDF2），**默认关、可按敏感类别开启**（如仅对 `pii=true` 的事实加密）。
- 启用加密后，**必须提供带口令的导出/导入**以便迁移（钥匙串密钥不随机器转移）。
- 小结：「v1 明文、本地优先、无出网；加密为后续项，默认关、可按敏感类别开」。

## 4. 同意机制（§4.3）

- 记忆的"是否开启/记录"由用户同意控制，记录于同目录 `consent.json`（与记忆同权限、同备份、同迁移）。
- `consent.json` 内容/语义归属 architect/产品；运维侧（SRE）保证不进 git、权限正确、随记忆一并备份与迁移、删除记忆时按策略处理。
- 可见同意：应用应在首次记录前弹出说明，用户可撤回（关闭记录即停止新写入，已记内容仍可由用户清除）。
- 呼应 `AGENTS.md`「工程指导—谨慎对待隐私」：主动常开感知必须包含可见同意、本地过滤、日志控制、录音/识别人物/向云 API 发送前的清晰边界。

## 5. 敏感数据边界（§4.4）

- 人物身份（identity）/ 语音转写（transcript）默认本地、不外发。
- fact 的 `pii` 字段（boolean，默认 false）标记敏感事实；`pii=true` 的事实在审计中单独计数（runbook §8），未来可按敏感类别加密（§3）。
- 用户可一键清除所有 `inferred`（系统推断）事实而保留 `explicit`（用户主动告知）——`explicit` vs `inferred` 的来源区分即为此设计（schema §5）。
- 误写入敏感数据时：立即清除并标记 `redacted`（runbook §7.2），并做隐私审计。
- **写时门控（默认不记敏感人物）**：store 在写入前按 `MEMORY_CAPTURE_PERSON_ID`（默认 `False`）+ `pii` + `source` 双层判定，拒存任何 `pii=True` 事实，除非显式开启且来源为 `explicit`（runbook §7.4）。即敏感人物身份默认**不落库**，属"默认不记"的隐私姿态——用户无需主动操作即可避免人物身份被持久化。

## 6. 清除的不可逆性（§4.5）

- 删除即永久：无回收站、无云端副本。
- 全部清除前建议先导出（runbook §5）。
- 损坏恢复**绝不静默清空**：坏文件隔离为 `memory.json.corrupt.<时间戳>`（runbook §7.3）。

## 7. 日志控制（§4.6，现状缺口 + 目标设计）

**现状（务必写实现状缺口）：**
- `SharedContext._transcriptions = deque(maxlen=20)`：纯内存环形缓冲，**不持久化、无开关**，退出即清空，不是记忆、不进记忆文件。
- `main.py` 全程 `print()`，无 logging 框架/级别/文件/脱敏；转录明文经 `print(f"[听写] {text}")` 与 `print(f"[J.A.C 原始回复] ...")` 打到 stdout —— **已存在的隐私暴露点**。
- `log_queue` 声明但未消费（死代码）。
- `codingLOG.md`：非运行时日志，是手写架构差距笔记，无留存策略/开关/自动生成。

**结论**：今天**无任何日志控制开关**，`AGENTS.md`「日志控制」当前未满足。

**目标设计：**
- 记忆子系统引入受控 logging：环境变量 `JAC_MEMORY_LOG` 默认**不记内容**，仅 debug 记摘要/计数/时间戳/类型，**绝不记原始转录或人物 ID**。
- 把现有 `print([听写]...)` / `print([J.A.C 原始回复]...)` 改为受控日志。
- 与 §4.4 一致：日志层面也不泄露 PII / 转录原文。

## 8. 与 AGENTS.md 的呼应

本说明是 `AGENTS.md`「工程指导—谨慎对待隐私」的可执行细则：本地优先、可见同意、本地过滤、日志控制、人物与云边界，均在此落地为具体文件/字段/开关。
