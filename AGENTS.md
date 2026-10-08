# J.A.C. 项目说明（AGENTS.md）

## 项目概述

J.A.C. = "Just A Code"。这是一个**本地优先的多模态 AI 管家原型**，灵感来自 JARVIS：通过摄像头/麦克风感知用户的环境，基于当前场景做推理，用语音 TTS 回应，目标是无需用户显式触发即可主动行动。

长期产品愿景是**打造一个强人工智能管家（Proactive AI Butler）**：

- 它能**主动感知**环境、提前**规划与预警**、在危险发生前发出**警示**、并能**应急接管**。
- 智能眼镜 / MR 等可穿戴终端只是**可选的随身交互外设**，而非项目的主目标或核心愿景——J.A.C. 的本质是 **AI 架构、系统与整套主动服务框架**，可穿戴终端只是承载它的一种形态。
- 主机（当前开发期）：MacBook Pro 级别的本地机器做低延迟的感知与推理。
- 云或局域网服务器承担更重的推理、长期记忆、路由到更大的模型，以及本地算力不足时的外部 API。
- 助手最终要支持主动感知、agent 式任务执行、外部 API 调用、语音/HUD 输出，以及闭环任务循环。

当前代码库是一个 **macOS 优先的 Python 桌面原型**（同时保留 Windows/Linux 的跨平台兼容代码，但不保证在 Windows 开发机上跑通）。

---

## 新架构（2026-10-01 定案，2026-10-06 方案 B 修订）

> bo s s 已确认方案 B：保留三层模型架构，第一层使用固定版本官方 MiniCPM-o-Demo Gateway / Worker 与 `tc-mb/llama.cpp-omni` Metal。权威 DOCX 为 `brainstorming_projectPLAN/10月1日新架构.docx`，精确版本、模型 SHA256 和协议参数记录在 `backend.lock.json`。**M0 经用户确认通过并免做 30 分钟长测；M1 生产听看说、GUI 与启停已获用户验收，M2a 独立 Qwen 只读调用/文件输出已验证；M2b 已接入语音转写/只读路由，2026-10-08 真机文字结果已获用户确认；原生结果播报已接入，通用工具、记忆与云端仍待迁移。**

### 一、三层模型架构

J.A.C. 由三层模型协同，按「本地低延迟 → 本地重推理 → 云端长任务」分级：

| 层 | 模型 | 运行位置 | 职责 | 输出 |
|---|---|---|---|---|
| **感知/主动判断层** | `MiniCPM-o-4_5` | 本地 Metal，经 Gateway `ws://127.0.0.1:8006/v1/realtime?mode=video` | 全双工持续听/说、主动判断；能力不足时升级 | **语音**（原生 TTS） |
| **大脑层** | `qwen/qwen3.6-35b-a3b` | LM Studio `127.0.0.1:12345` | 复杂决策：Function Calling / Tool Use / Agentic Coding（控制电脑） | **文件** |
| **云端层** | 云端 OpenClaw | 服务器（接入 DeepSeek API，有公网地址） | 更复杂、长时间任务 | **文件** |

升级链：`MiniCPM-o-4_5` 解决不了 → 发起调用 `qwen/qwen3.6-35b-a3b` → 仍不够 → 交给云端 OpenClaw。

### 二、输入层

- **音频**：SoundDevice 采集，回调只复制至有界 Queue；后台重采样为 **16kHz / float32 little-endian / Mono PCM**，每 **1000ms** 发送 16000 个样本的 Base64 块。静音也持续发送，不通过丢弃用户语音追赶水位。
- **视频**：用 **OpenCV** 采集。**不要在主事件循环（asyncio loop / Qt Main Thread）里同步调用 `cv2.read()` 和 JPEG 编码**——独立后台线程采集，经线程安全 Queue 提供给 GUI 和上行采样；默认上行每秒最新 1 帧，不把 5–10fps 的采集帧率误当上行帧率。
  - **强制将摄像头分辨率降为 640×480，帧率限制在 5–10 fps**。
- **上行通道**：客户端只连接 Gateway 的 `/v1/realtime?mode=video`。默认每个 1 秒 `input.append` 附最新 1 帧 JPEG；采集/GUI 预览帧率与模型上行帧率分离，多帧上行必须先实测性能。
- 至此音视频信号传入后端 `MiniCPM-o-4_5`，进行全双工主动判断。

### 三、后端（方案 B 固定版本）

- **环境**：客户端与 Gateway / Worker 使用 **Python 3.11**；推理使用 C++ Metal，不使用 PyTorch MPS 加载全双工模型。
- **Demo**：`OpenBMB/MiniCPM-o-Demo` main，commit `47709a9210dfd71afa76c058e017fc8c4db5c8d2`。
- **引擎**：`tc-mb/llama.cpp-omni` master，commit `873056743b74e1a4ce5dcf7290e2298428e214db`。
- **模型**：`openbmb/MiniCPM-o-4_5-gguf`，revision `db25077c33951fe163b42986fba0132e279872a2`。除 `MiniCPM-o-4_5-Q4_K_M.gguf` 外，必须包含 audio、vision、tts、token2wav-gguf；逐文件 SHA256 见 `backend.lock.json`。
- **链路**：J.A.C. → Gateway `127.0.0.1:8006` → Worker `127.0.0.1:22400` → `llama-omni-server` `127.0.0.1:22500`。Worker 注册接口为本机 `:8007`。
- **构建**：CMake Release、`GGML_METAL=ON`、`LLAMA_OPENSSL=OFF`，构建目标 `llama-omni-server` / `llama-omni-cli`。固定 commit 加上 `new_computer_download/patches/engine-loopback-native-voice.patch`：包含遵守 `--host` 的本机监听修复及 bo s s 2026-10-07 明确批准的原生音色条件接线；2026-10-08 按 bo s s 原生结果播报授权扩展同一组合补丁，加入有界字面文本 token 评估与原生 TTS/Token2Wav 音频归属确认；启动器只接受登记的完整组合补丁，不接受其他源码漂移。
- **起始参数**：`-ngl 99 -c 4096 -t 8`。其他参数依据该固定版本的 `--help` 与 M0 结果确定，不照搬旧 `llama-server` 命令。
- **生命周期**：等待 `session.queue_done` → 发送 `session.init {payload: ...}` → 收到 `session.created` → 固定节拍 `input.append {input: ...}` → 接收 `response.output.delta` → `session.close / session.closed`。
- **输出**：`kind=listen/text/audio`；音频是 **24kHz mono float32 PCM Base64**，独立于文本流。视频双工不用 `response.done` 作为每轮结束信号。
- **会话时限**：该 Gateway 视频会话 300 秒、音频会话 600 秒。常开模式必须在迁移时实现受控重连和上下文恢复；不得声称当前上游可无限会话。
- **协议依据**：固定 commit 中的 `docs/en/realtime-protocol-overview.md` 与 `docs-app/content/docs/en/realtime-api/video.md`；旧 `/ws/duplex/{session_id}` 页面不是本次客户端依据。

### 四、音色克隆（原生参考音频）

- 参考音为 `voices/silverwalf_voice.wav`，客户端读取后转成 **16kHz mono float32 PCM Base64**。
- 在 `session.init.payload.voice.ref_audio_base64` 与 `tts_ref_audio_base64` 发送，不再离线提取 `.pt`，也不使用 `--tts-speaker-emb` 或 `voice_id`。
- 下行原生 24kHz PCM 直接通过 SoundDevice 输出流播放；运行时不将参考音发送给云端推理服务。bo s s 已明确允许将模板 `voices/silverwalf_voice.wav` 推送到本项目的公开 GitHub 仓库；此授权不包含实际测试录音。
- **2026-10-07 原生接线修复**：bo s s 已批准扩展锁定补丁，现以 CPU ONNX 前端生成 192 维 CAMPPlus 声纹、S3 参考 token 与 24k mel 条件，并通过现有 native PromptBundle 接口加载到 C++ Token2Wav。`backend.lock.json.voice_frontend` 独立锁定两个官方 ONNX，存放在仓库外 GGUF 目录的 `voice-frontend/`；官方 10 个 GGUF 与 SHA256 不改、不覆盖默认缓存。派生条件按规范化 16k PCM 内容哈希隔离至忽略的 `.cache/voices/`，重连复用、换参考重建，损坏/静音/缺失前端明确失败。
- **会话确认**：`session.created.voice_conditioning` 返回 applied 与参考 PCM SHA256；GUI/CLI 和文件/设备探针核对确认后才开启采集，旧后端没有确认时拒绝启动，不默默使用默认音色。默认音色仅供没有请求参考音的协议探针，复用会话时也明确重载。`realtime_protocol.build_init()` 仍针对固定 C++ 版本将 `system_prompt` 编为 `<|audio_end|>…<|im_end|>` 后缀；不能直接用于其他后端。生产客户端双会话、参考切换和静音拒绝已验证，音色听感仍须 bo s s 确认，详见 CHANGELOG 附 A4。

### 原生任务结果播报（2026-10-08）

- **普通抢答隔离**：启用转写时，`response_gate.py` 在用户 VAD/转写判定前有界暂存普通文字/音频（8 秒 PCM、8192 文本字节、256 个增量，等待 15 秒）；系统主题只放行带任务标识的实际证据播报。未匹配系统说法不放宽为模糊工具执行，明确提示未查询；重叠、质量/过期/长句拒绝均显示原因。新句取消排队普通播放，普通聊天确认后恢复；原音视频上行不等待转写。转写故障暂停未核验播报直至重启，感知上行保留。普通对话会增加 CPU 转写等待，不宣称原有回复延迟不变。
- **可核验证据**：只有 `get_system_info` 真实返回后才显示 `tool_completed`，包含参数、查询时间与原始结果，并保存到报告。普通 J.A.C. 回复和“正在查询”不能作为已调用工具的证明。2026-10-08 bo s s 电池测试的 100% 没有对应已完成报告，当前直接与独立 Qwen 查询均为 80%；具体该次未路由原因没有运行转写日志，不能据合成样本推定。
- 同一个 Gateway 视频会话的 `session.created.capabilities.task_speech=1` 确认本项目补丁扩展。已完成任务经 `input.append.input.task_speech {id, text}` 附在下一秒真实音视频块；固定上游原协议没有此字段，本项目仅支持已登记组合补丁。
- 字面文本使用 `parse_special=false` token 化并在唯一 duplex LLM 线程评估隐藏状态，再走现有原生 TTS/Token2Wav；不插入用户消息、不重新采样任务数值、不调用第二个 TTS 服务。后端限制 768 UTF-8 字节/192 文本 token，拒绝控制字符、重复标识和忙碌请求。
- 待发仅一个播报，等待助手聆听、用户 VAD 静音与播放队列空闲；等待 20 秒过期、已发 30 秒超时，轮换剩余不足 15 秒跳过。文字/报告交付不受播报跳过或失败影响。
- `response.output.delta.task_speech_id` 贯穿原生文本和 PCM，`task.speech` 完成事件从最后 Token2Wav 窗口发出。客户端核对精确文本、非静音 PCM 与样本数；GUI“结果音频已接收”表示接收确认，实际播放与音色听感分别验证。
- 停止/重连/旁路故障使任务播报代次失效，已排队或部分播放的任务块携带取消标志，播放回调跳过剩余部分；已经交给设备的当前音频块不能倒退。底层在途推理按原关闭握手收尾，不声称所有 native 计算可抢占终止。
- 新机器应用完整组合补丁构建；旧安装使用 `update_native_backend.py --build`，默认读取 GUI 保存的引擎路径。重新编译前释放固定端口；已有外部服务不接管。生产任务启用时，旧二进制缺少能力确认会在采集前明确拒绝启动。

### 五、输出层

- **语音输出**：由 `MiniCPM-o-4_5` 完成（自带音色克隆 TTS）。
- **文件输出**：由 `qwen/qwen3.6-35b-a3b` 或云端 OpenClaw 完成。

### 六、记忆层

| 组件 | 选型 |
|---|---|
| 向量数据库 | **ChromaDB** |
| Embedding 模型 | **BGE-Small-ZH-v1.5（ONNX INT8 版）** |
| 结构化存储 | **JSON** |

两条**性能铁律**（ChromaDB 在 Apple Silicon 上的坑）：

1. **批量写库**：ChromaDB 频繁 `add` 后内存不会立即释放给 OS。**不要每次对话结束都立刻写库**——在客户端内存缓存最近 5 条摘要，满 5 条或会话结束时批量执行一次 `collection.add()`，再清理缓存，内存曲线才平滑。
2. **限制检索时机**：严禁放进音频回调。只在初始化会话和本地 VAD 判定完整句子结束后检索。初始化没有开场白时注入用户档案与近期记忆；本地 Whisper 并行转写完整句子以提供查询文本，不阻塞全双工上行。固定协议未确认支持动态 prompt 更新，检索结果优先提供给 Qwen 并注入下一会话。

JSON 是结构化事实真源，ChromaDB 是可从 JSON 重建的索引；批量写入应支持幂等 `upsert`、崩溃恢复及隐私删除。

### 七、GUI

配置 **Gateway 专用语音 GUI**：Qt 绘制的深蓝渐变科技风格（青色高光与细网格），原比例无黑边摄像头、连续回复文本、折叠连接日志、顶部单状态提示。无文字输入/发送、显示分辨率/缩放或旧模型开关。右侧仅显示已支持的 Gateway 设备/增益/采集帧率/会话/连接/音色参数；启动前调整、运行中锁定。「调节参数」按钮按面板开/关保持高亮/暗色，并提供按压反馈。按 bo s s 2026-10-07 本轮要求，GUI 设备同意默认开启且不显示勾选项，仅点击「启动语音」才开启设备；终端/探针仍保留显式 --consent-devices。顶部提供「启动后端」与「启动语音」，后端路径通过图形目录选择器保存到忽略的 `.cache/gui/backend.json`；仅回收本窗口拥有的后端，不接管外部服务。

### 八、DLC（未来可选）

- **小米 miloco 系列**：全屋摄像头监看，将有人的摄像头接入 `MiniCPM-o-4_5`。
- **智能眼镜**：将画面和音频传给 MacBook Pro（随身携带），实现第一视角监看。
- **光波导（AR）/ 空间计算（Vision Pro）**：实现 3D GUI 覆盖，例如 3D 立体导航、主动提醒后方来车等。

---

## 代码迁移状态（旧架构 → 新架构，M1）

> M1 已接入独立 Gateway 生产路径；旧 MiniCPM 客户端、轮询 judge 和传统运行入口已退役，其余改造点按下表推进。详细差距见 `CHANGELOG.md` 附 A。

| 维度 | 新架构（目标） | 当前实现 | 迁移动作 |
|---|---|---|---|
| omni 后端 | 固定版本 Gateway / Worker / C++ Metal，Q4_K_M，`:8006/v1/realtime?mode=video` | 新 `GatewayClient` 已接入 `main.py --gateway` 和轻量 GUI；旧入口已移除 | 继续验证生产设备与升级接线 |
| 上下文/参数 | 起始 `-c 4096 -t 8 -ngl 99` | 固定方案 B 参数 | 后续性能优化需实测 |
| 语音输出 | 参考 WAV Base64 + 原生 24k float32 PCM | 原生音色条件已接入 C++ Metal；会话核对已应用的参考音哈希 | 技术接线/重连/切换通过；听感待用户确认 |
| 记忆 | **ChromaDB** + BGE-Small-ZH-v1.5（ONNX INT8） + JSON | fastembed + paraphrase-multilingual-MiniLM + 自研 MemoryStore | 重写 `src/memory/` |
| 模型层数 | 三层（o-4_5 + qwen + 云端 OpenClaw） | 第一层 Gateway 已运行；M2a Qwen 独立只读/文件任务通过，M2b 语音转写/只读路由已接线且真机文字结果获用户确认，OpenClaw 未接入 | 扩展工具、记忆与云端通道 |
| 音频输入 | SoundDevice + 16k float32 mono + 固定 1 秒块 | Gateway 已接入；旧运行入口已移除 | M2b CPU Whisper small / WebRTC VAD 已旁路接入 |
| 视频输入 | 独立线程 640×480、采集 5~10fps、默认每秒上行最新 1 帧 | Gateway 使用线程采集与独立 GUI 帧缓冲；无旧采集入口 | 新界面真机使用确认与后续性能验证 |
| 运行环境 | Python 3.11 | Gateway CLI/GUI 已使用 `.cache/m0/venv` 3.11；旧 `.venv` 3.13 保留 | 已有旧环境保留，不再用于主程序 |
| 判断引擎 | 由 MiniCPM-o-4_5 全双工承担（不再单独轮询） | 轮询 judge 与传统运行时已删除 | 已退役 |

## 文档同步硬性规定（Agent 必读）

本仓库有四类**必须随代码改动同步**的文档，任一代码/配置/依赖变更后都必须检查并更新对应条目：

1. `README.md` — GitHub 首页文档（双语：英文在前、中文在后）。每次改动后确保其描述与项目真实状态一致。
2. `AGENTS.md` — 本文件，开发者契约。架构/运行方式/依赖/文件路径变化必须同步。
3. `CHANGELOG.md` — 变更日志。**同时是「文档归口中心」**：原 `codingLOG.md`（差距笔记）与原 `docs/memory/` 四份记忆子文档已于 2026-10-01 全量并入本文件（附 A 差距笔记 / 附 B 记忆契约 / 附 C 记忆测试计划与实现真值 / 附 D 用户指南 / 附 E 隐私说明 / 附 F 运维 Runbook），源文件已删除。每次变更追加一条用户可读的改动说明；涉及差距或记忆子系统的修订，**直接改 `CHANGELOG.md` 对应附录**，不再新建 `docs/` 专项。
4. `brainstorming_projectPLAN/10月1日新架构.docx` — **新架构权威基准（2026-10-01 定案）**。自 2026-10-06 起，Agent 获准编辑和同步该本地目录，但整个 `brainstorming_projectPLAN/` 必须加入 `.gitignore`、停止 Git 跟踪，今后不得提交或推送。涉及目标架构的实质性变更时，必须先取得 bo s s 明确确认，并在修改后同步 `AGENTS.md`、`README.md`、`CHANGELOG.md` 与安装文档。

**查看改动时的强制动作**：每当 Agent 需要了解「最近改了什么 / 当前实现状态」，必须优先读取 `CHANGELOG.md` 与 `AGENTS.md`；需要「当前代码与新架构的差距」时读 `CHANGELOG.md` 附 A（已按新架构口径改写）。

配套检查项（每次提交/对话后）：

- 检查 `.gitignore` 是否需要新增忽略（如新增大体积/二进制产物）。
- 推送包含用户明确允许的模板 `voices/silverwalf_voice.wav`，不含模型、`brainstorming_projectPLAN/` 或测试录音录像。忽略规则不影响已跟踪文件，需停止跟踪但保留本地文件；尚未推送的提交也必须检查，避免排除文件随历史上传。已发布历史的清理须另行授权，不自动强推。
- 每次代码/配置修改后检查依赖与运行方式变化，同步检查 `requirements.txt`、独立依赖清单、`new_computer_download/` 一键安装脚本和 `READMEfirst.md`，确保其他机器可按文档安装。新增或改变依赖时必须更新对应安装阶段与安装后自检，不只修改当前机器环境；没有相关变化时在日志说明已检查、无需调整。
- **阶段自动发布**：项目达到经验证可交付的里程碑或完成当前阶段目标时，自动提交并推送当前开发分支到 GitHub，仅纳入本轮相关代码、测试和文档，继续遵守上述模型/规划目录/实际媒体排除规则；不自动强推或合并 main。
- **操作命令交付**：每次需要 bo s s 操作时，提供当前环境可直接执行的完整命令，逐条单独给出，说明和注释放在代码块外；不以「用原命令」或未填路径代替所需命令。
- `codinglog_by_awaqwq233/` 仍只由 bo s s 手动维护，Agent 不得自动编辑；`brainstorming_projectPLAN/` 已获准由 Agent 按 bo s s 确认的架构决策编辑和同步。
- **开发平台**：当前以 macOS（Apple Silicon）为主开发机，保持跨平台兼容代码；Windows 开发机已不再使用。

## 当前开发状态（2026-10-08 更新）

> **M0 已通过且 30 分钟长测免测；M1 基础听看说、GUI 和手动启停已由 bo s s 真机验收。** M2a Qwen 独立只读工具/文件闭环已验证；M2b 已接入并行转写与 Gateway 只读任务路由，2026-10-08 真机文字结果已获用户确认；原生任务播报已接入，音色听感待确认。旧 MiniCPM 及传统运行入口已删除，云端和记忆尚未迁移。

- **M2b 并行转写/任务**：`src/omni/task_pipeline.py` 与 `transcription_worker.py` 在同一份重采样 PCM 上旁路 VAD → Whisper → Qwen，只在后台工作；麦克风回调仍只复制至主队列。WebRTC 30ms、300ms 预留、600ms 句末，至少 300ms 有声段，12 秒长句拒绝。Whisper small 在独立解释器 CPU INT8/2 线程运行，主进程不导入 faster-whisper/CTranslate2/PyAV；UTF-8 管道有界，无 PCM 文件、torch/MPS 或运行期下载。资源与独立 SHA256 见 `transcription.lock.json`，默认仓库外 `~/.cache/jac/models/whisper-small`。仅通过置信度校验的完整、无播放重叠的用户指令进入显式白名单，转写原话显示与下一会话上下文同步；不依赖助手输出或旧 CALL_QWEN 令牌。只开放系统状态查询/报告，尚无通用电脑控制/云端工具。句子代次/ID 去重，15 秒过期、忙碌不排队；重连/停止取消旧 HTTP/任务/文件发布，原子发布与代次共锁。旁路故障明确暂停任务且保留感知上行。GUI/真实 CLI 默认开启，SDK opt-in、文件验证显式 `--transcription`，可用 `--no-transcription` 保留基础交互。GUI 显示用户转写、任务进度和真实报告入口，任务文本/证据仅写到本机报告，统计不含正文。原生结果播报使用实际工具证据短句（综合报告播报完成提示），与真实 PCM 同块上行，经既有 LLM 隐藏状态 → TTS → Token2Wav 生成 24k PCM；不让感知模型重新推理状态数值。真实后端 60 秒合成文件闭环通过：唯一转写/任务/报告，关闭确认与清理成功；该合成文件验证没有打开真实设备。2026-10-08 bo s s 反馈“测试成功，能正常输出文字结果”，真机语音任务文字结果按用户反馈验收通过；未新增具体指令、次数、时长、延迟或报告内容核对记录，音色听感未由该反馈确认。VAD/置信度不等于说话人识别或绝对准确，长时稳定性不从短测或合成文件推定。
- **M2a 独立 Qwen**：`verify_toolcall.py` 使用同一 Python 3.11 环境与已有 httpx，只开放 `get_system_info`；`--task` 是独立终端显式任务，不是生产语音入口。`src/brain/lm_studio.py` 核对 `/api/v1/models` 的精确已加载实例及能力，要求 LM Studio 0.4.8+，请求使用 `reasoning_effort="none"`，不移除参数后默默重试、不恢复思考尾段、不自动加载或回退其他模型。`BrainTaskRunner` 将真实证据和最终回答原子发布到忽略的 `output/m2/qwen/`；错误/取消不发布未完成文件。单次 HTTP 90 秒、整个任务 120 秒、最多 4 轮；等待中每 100ms 检查取消，各工具执行前再检查。已开始的只读系统查询有自身超时，不能声称任意工具可抢占终止。普通聊天 SSE 保留，agent 最终回答不再额外生成一次。三项独立真实只读验证通过；M2b 合成文件验证已与 MiniCPM/CPU Whisper 同跑并交付系统报告，仅代表这段测量，不推定真机或长时性能。M2b 已在语音旁路接入来源校验与 GUI 只读路由，并接入原生结果播报；完整报告仍保留文字和工具证据。
- **M1 Gateway 入口**：`main.py --gateway`（终端）、`main.py --gateway --gui`（GUI），使用 Python 3.11 独立环境；GUI 可异步调用固定启动器启动/回收自己拥有的后端；外部已运行服务只探测与复用，不接管。收到启动器就绪事件后才开放语音，停止后端/关闭窗口先等待音视频会话清理，再回收所属后端。GUI 仅使用 Gateway，不依赖旧 torch/PyAudio/Voicebox/YOLO。`omni_backend` 只接受 `gateway`；`legacy` 会明确报错。`main.py --gui` 与 `python -m src.omni --gui` 同样进入 Gateway，`--gateway` 保留兼容。
- **GUI 后端控制**：`src/omni/backend_control.py` 使用 QProcess 与启动器 `--events-json` 事件管理固定三进程，健康检查在后台且只访问本机；启动/停止/取消/异常均反馈至 GUI，窗口退出等待清理。设置不改变固定版本、端口、模型清单和构建参数。
- **M0 文件**：`backend.lock.json`、`verify_duplex.py`、`new_computer_download/start_m0_backend.py`、`new_computer_download/requirements-m0.txt`。后端源码、模型和测试运行产物不提交到 Git。
- **M0 已通过（用户验收）**：文件协议、40 秒真机、10 次启停和后续两段各 225 秒通过；累计上行 450 秒、P95 679/891ms、原生音频收播合计 176.2 秒，异常为零。bo s s 明确认可短测效果并免做 30 分钟长测，不再以该项阻塞迁移；原始报告保留 `soak_30min_verified=false`，不伪造未执行的测量。
- **M1 会话重建**：默认每 240 秒上行后受控关闭、释放设备和重连；重新注入参考音、已确认上下文、有界助手历史。段间采集暂停并显示状态；未转写的用户语音不能完整恢复，不声称无缝或无限单会话。启动保护通过 `force_listen` 保留真实音频，不把用户输入换成静音。
- **M1 验证与验收**：Python 3.11 专项 57 项通过，覆盖画面清空、异步停止、关闭确认、在途推理尾窗、重复停止与退出窗口。此前两次真实后端文件会话共 40 块、原生音频 5.64 秒、P95 977ms，随后合成帧/静音三次手动启停通过。2026-10-07 bo s s 在修复后确认「这次完全正常」，基础听看说、GUI 预览与启停现按用户反馈验收通过；未新增或推测真机循环次数、时长及长期稳定性测量。
- **M1 手动停止**：先在 Qt 主线程停绘制、清空画面，再后台停止采集并保留默认 8 秒接收尾窗，等待 `session.closed` 和 Gateway 断链，额外冷却 1 秒，清理成功才恢复启动按钮；常规约 9 秒，超时显示「重试停止」，不允许重叠客户端。窗口退出也等待同一清理。固定上游会先报告关闭再完成 C++ 清理，尾窗/冷却是客户端兼容处理，不声称其底层竞争已被源码修复；锁定后端与补丁未改变。
- **设备探针**：`verify_live_duplex.py`（短测）和 `verify_soak_duplex.py`（长测）必须显式 `--consent-devices` 且戴耳机。macOS 首次摄像头权限在启动主线程申请；帧读取/编码仍在独立线程。探针不保存原始媒体，使用后释放设备。长测保持同一组后端常驻，8 段累计 1800 秒上行，每段 225 秒，段间重开设备；不代表单会话无限运行或上下文恢复已完成。
- **旧 MiniCPM 实现的历史问题**（仅作为退役原因保留，不用于判断已验收的新 Gateway）：
  1. **视觉分辨率仅 grid 1x1 / 64 视觉 token**（服务端日志 `image encoded ... grid: 1x1`），细节视觉问答不可行（真机曾把 boss 本人误判成"电脑桌面"）。
  2. **音频理解（ASR）质量差**：内建麦离嘴远、人声 RMS 仅 0.03~0.065，模型听不清寒暄，会从 prompt 示例里"捡"输出。
  3. **"说/听切换"不稳**：`listen_prob_scale` 1.0 偏沉默、0.8 偏抢话，无稳定工作点；它是开口意愿旋钮（`listen_bias=(scale-1.0)*2.0`），不是 VAD、不影响听清。
- **旧架构已定位但未修的 bug**（待新架构落地时一并处理或作废）：①旧 qwen 思考参数无效（M2a 独立 HTTP 已改用 `reasoning_effort="none"` 并实测通过，M2b 语音只读升级已接线）；②「真提问被自己拦截」——护栏判据失准（P2）；③无 WebRTC AEC（旧方案见已删除的 `docs/webrtc_aec_plan.md`；新架构戴耳机规避回声，AEC 需求重新评估）。
- ⚠️ **口径提醒**：`CHANGELOG.md` 附 A 中「视觉 token 吃爆 KV → 每 30s 清空 → 复读示例」的旧假设，已被 2026-09-28 的 A1 实验（全程 `--no-video` 零视频帧，幻觉依旧）**证伪**，勿再据此修 bug。

## 重要文件与目录

- `main.py`：统一 Gateway CLI/GUI 入口；`--gateway` 为兼容参数，不再分流至旧实现。
- `src/capture/camera.py`：未迁移的旧通用摄像头组件，无生产入口；Gateway 使用 `src/omni/media.py` 独立线程 640×480。
- `src/analysis/detector.py`：YOLOv8 检测器封装。
- `src/audio/recorder.py`：VAD 麦克风录音（旧：PyAudio + WebRTC VAD；新架构改 SoundDevice）。
- `src/audio/stt.py`：Whisper 语音识别（旧架构非流式转写；新架构音频直通 o 版全双工，STT 仅保留作可观测/校验）。
- `src/audio/`（`voicebox_tts.py` / `qwen_tts.py` / `speaker_factory.py` / `tts.py` / `playback.py`）：旧架构 TTS 链（Voicebox → Qwen3-TTS → 系统 TTS）——**新架构语音输出改由 MiniCPM-o 自带音色克隆承担，此链待移除/降级**。
- `src/brain/llm.py`：`LocalBrain`，多后端大脑层（lm_studio / ollama / llama_cpp / auto）——大脑层 `qwen/qwen3.6-35b-a3b` 在新架构中保留为第二层。
- `src/tools/`：Function Calling 工具层（装手）——新架构中归属大脑层 `qwen/qwen3.6-35b-a3b` 的 Tool Use / Agentic Coding。
- `src/judgment/` 与 `src/runtime.py`：已删除，旧 MiniCPM 判断轮询与传统运行编排不再启动。
- `src/memory/`：旧架构记忆子系统（fastembed + 自研 MemoryStore）——**新架构改 ChromaDB + BGE-Small-ZH-v1.5，此目录待重写**。
- `src/omni/`：Gateway 客户端/协议/设备/桌面运行时和 GUI 后端控制器；旧 `client.py` / `server_launcher.py` / Voicebox 桥接 / 回灌 / 令牌过滤已删除。旧 router.py 仅保留草案；M2b 的生产路由由 TaskPipeline + BrainTaskRunner 承担，仅只读任务，不宣称通用 agent 可用。
- `voices/silverwalf_voice.wav`：方案 B 原生音色参考 WAV，启动会话时编码发送给本机 Gateway。
- `backend.lock.json`：后端与模型精确版本、SHA256、端口及协议配置。
- `verify_gateway_tasks.py`：M2b 固定 Gateway 文件探针，真实 VAD/Whisper/Qwen 与原生结果音频核对，仅保存统计与报告文件名，不打开设备。
- `src/omni/task_speech.py`：单个待播报队列、实际证据短句、任务代次与字面文本/音频归属校验；不保存 PCM。
- `new_computer_download/update_native_backend.py`：仅升级登记的旧补丁或干净固定源码，可重新编译；陌生源码拒绝覆盖，应用失败回滚。
- `transcription.lock.json` / `new_computer_download/prepare_transcription.py`：仓库外 Whisper small 资源锁与 HTTPS 下载/CPU 自检。
- `verify_toolcall.py`：M2a 独立精确 Qwen 只读工具/文件验收；不启动设备或 Gateway。
- `src/brain/lm_studio.py` / `task_runner.py`：本机严格大脑契约与独立报告文件交付。
- `verify_duplex.py`：独立 M0 协议/媒体文件探针，不调用生产运行时，也不打开麦克风或摄像头。
- `verify_live_duplex.py`：明确授权后使用本机设备的短时 M0 探针，不改生产入口、不保存原始媒体。
- `verify_soak_duplex.py`：明确授权后的 M0 分会话长测，记录健康/RSS 与逐段统计，失败也保存报告。
- `new_computer_download/start_m0_backend.py`：固定版本校验、模型预检、启动本机三进程与回收。
- `temp/`：运行时临时音频文件。
- `requirements.txt`：引用固定 Gateway 依赖；`requirements_fixed.txt` 仅为未迁移组件的历史快照。
- `Modelfile`：Ollama 构建定义。
- `new_computer_download/READMEfirst.md`：新机器一键环境搭建与安装指南（已按新架构改写）。
- `setup_ffmpeg.py`：从 imageio-ffmpeg 复制二进制为项目根 `ffmpeg`。
- `verify_model.py` / `verify_toolcall.py`：模型/工具调用校验脚本。
- `codinglog_by_awaqwq233/`：bo s s 的个人记录，仍禁止 Agent 编辑。
- `brainstorming_projectPLAN/`：仅本地的架构规划目录；允许 Agent 按已确认决策编辑和同步，但不提交、不推送。

通常不参与编辑的大体积/二进制产物：

- `.venv/` / `.cache/` / `__pycache__/`
- 模型二进制（`*.gguf`、`*.pt`、`*.bin`、`*.onnx`）——由外部 AI 软件或模型仓库管理，不进本项目。
- `temp/` 下的运行时音频

## 模型与资产

所有**推理模型均不存放在项目内**，由外部 AI 软件 / 外部仓库管理：

- **感知/主动判断层**：`MiniCPM-o-4_5` Q4_K_M 主模型（约 5.03GB）与 audio/vision/tts/token2wav GGUF 由固定版本 C++ Metal 引擎加载；模型目录必须在仓库外，完整清单与哈希见 `backend.lock.json`。
- **大脑层** `qwen/qwen3.6-35b-a3b`：在 **LM Studio** 中加载（`127.0.0.1:12345`），原生多模态、`enable_thinking=False`。代码按模型标识符精确匹配。
- **云端层**：云端 OpenClaw，接入 **DeepSeek API**，有公网地址，处理更复杂/长时间任务。
- **音色克隆**：直接使用 `voices/silverwalf_voice.wav`，转为 16k mono float32 Base64，经 `session.init.payload.voice` 传给本机 Gateway。
- **记忆**：ChromaDB 向量库 + `BGE-Small-ZH-v1.5`（ONNX INT8）embedding，权重由 ONNX runtime 加载（仓库外缓存）。
- **物体检测** `yolov8n.pt`：首次运行由 `ultralytics` 自动下载到缓存，不进仓库。

## 设置与运行

完整步骤见 **`new_computer_download/READMEfirst.md`**。安装器默认 `gateway`（兼容 `--only gateway` / `--only m0`）建立 `.cache/m0/venv` Python 3.11，安装 `requirements-m0.txt`（已含 Qt GUI、httpx 与 CPU 转写依赖），执行导入自检并默认准备仓库外 Whisper 资源/CPU 自检（可用 --skip-transcription-model 跳过资源），保留现有 `.venv`。运行 `main.py --gateway --gui` 后可点「启动后端」，再启动语音；独立后端与经设备同意的终端入口仍支持。不带 `--gateway` 同样进入 Gateway；所有旧 MiniCPM 入口已移除。

VS Code 本机调试配置为忽略的 `.vscode/launch.json`「J.A.C. · Gateway GUI」，显式指定 `.cache/m0/venv/bin/python`、`--gateway --gui` 和项目工作目录。新工作区须选择项目解释器，已缓存系统解释器时手动切换；编辑器直接运行文件不会应用 F5 配置参数。`.vscode/` 不提交，安装步骤见 READMEfirst 第 10 节。

### 运行前置条件

- **MiniCPM-o-4_5 后端**：固定版本 Gateway / Worker / C++ Metal；M0 所有服务绑定 loopback，禁用上游会话录制。
- **LM Studio**：加载 `qwen/qwen3.6-35b-a3b`（大脑层），`127.0.0.1:12345`。
- **云端 OpenClaw**：处理更长任务时需服务器在跑（可选）。
- 可用的摄像头、麦克风；项目根或 PATH 中的 FFmpeg。
- **运行环境 python3.11**（后端 MiniCPM-o 部分同）。

## 工程指导（未来工作）

- 坚持本地优先设计。唤醒词检测、VAD、基础感知、紧急交互留在本地（MiniCPM-o-4_5 全双工层）。
- 分层升级：本地 MiniCPM-o-4_5 → 大脑 qwen3.6-35b → 云端 OpenClaw，按「简单规则 → 小模型 → 大模型」逐级上抛，避免让大模型决定每个底层路由。
- **采集与推理解耦**：SoundDevice 回调只入队；后台转为 16k float32 mono、固定 1 秒块。OpenCV 独立线程 640×480、5~10fps 采集；每秒附最新 1 帧，不在主线程读摄像头或编码。
- **记忆性能铁律**：ChromaDB 写库走「缓存 5 条摘要 → 批量 `collection.add()`」；检索只在「WS 建立发 init 前」与「VAD 判定说完一句后」两处触发，严禁放音频回调里。
- **语音输出由 MiniCPM-o 承担**：参考音频克隆 + 原生 24k float32 流；文件输出走大脑层/云端层。
- 谨慎对待隐私与安全：主动常开感知必须遵循用户设备授权和明确的启动/停止状态、本地过滤、日志控制，以及在录音/识别人物/向云 API 发送数据前的清晰边界。
- 任何新的 agent/工具执行功能，对高风险操作必须显式白名单与确认。
- 延迟优化优先做流式与流水线：流式 ASR、增量推理、流式/提前 TTS。
- 记忆从 JSON 结构化摘要起步，向量检索用 ChromaDB。

## 已知限制（当前代码，待新架构落地后重新评估）

- Gateway 已接入听看说与新 GUI；旧 `:9060`、轮询判断模型和传统运行入口已删除。M2b 已接入并行转写和 Qwen 明确只读任务；通用工具、ChromaDB 和 OpenClaw 尚未接入；原生结果播报已接入。未迁移通用音频/记忆/检测组件保留，无生产入口。
- 运行依赖外部软件（LM Studio / 固定版本 Gateway、Worker、llama-omni-server / 可选云端 OpenClaw）。
- M0 基于已有真机验证与 bo s s 明确验收通过；30 分钟稳定性免测，不作为阻塞项。M1 文件回放不等于新增生产 GUI/设备的真机听感验收。
- M2b 已展示确认的 Whisper 用户转写，并注入后续会话的有界上下文；被拒绝或未经转写的语音不恢复，重连仍有采集间隙。
- 无 WebRTC AEC（新架构以戴耳机规避回声，AEC 需求待重新评估）。
- 无云端 OpenClaw 集成（新架构新增项，代码未落地）。
- 原生音色克隆接线已修复，但不保证与参考 WAV 完全一致；技术加载确认与用户听感验收分别记录。CPU 前端冷启动/条件加载会增加会话初始化时间，不阻塞已建立会话的音视频回调。
