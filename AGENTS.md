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

## 新架构（2026-10-01 定案，2026-10-06 方案 B 修订，2026-10-18 Agent 方案增订）

> bo s s 已确认方案 B：保留三层模型架构，第一层使用固定版本官方 MiniCPM-o-Demo Gateway / Worker 与 `tc-mb/llama.cpp-omni` Metal。权威 DOCX 为 `brainstorming_projectPLAN/10月1日新架构.docx`，精确版本、模型 SHA256 和协议参数记录在 `backend.lock.json`。**M0 经用户确认通过并免做 30 分钟长测；M1 生产听看说、GUI 与启停已获用户验收，M2a 独立 Qwen 只读调用/文件输出已验证；M2b 已接入语音转写/只读路由，2026-10-08 真机文字结果已获用户确认；原生结果播报已接入，联网搜索已接入本地 Qwen；受限本地电脑任务于 2026-10-16 接入；通用桌面控制、记忆与云端仍待迁移。**

### 一、三层模型架构

J.A.C. 由三层模型协同，按「本地低延迟 → 本地重推理 → 云端长任务」分级：

| 层 | 模型 | 运行位置 | 职责 | 输出 |
|---|---|---|---|---|
| **感知/主动判断层** | `MiniCPM-o-4_5` | 本地 Metal，经 Gateway `ws://127.0.0.1:8006/v1/realtime?mode=video` | 全双工持续听/说、主动判断；能力不足时升级 | **语音**（原生 TTS） |
| **大脑层** | `qwen/qwen3.6-35b-a3b` | LM Studio `127.0.0.1:12345` | 复杂决策：Function Calling / Tool Use / Agentic Coding（控制电脑） | **文件** |
| **云端层** | 云端 OpenClaw | 服务器（接入 DeepSeek API，有公网地址） | 更复杂、长时间任务 | **文件** |

升级链：`MiniCPM-o-4_5` 解决不了 → 发起调用 `qwen/qwen3.6-35b-a3b` → 仍不够 → 交给云端 OpenClaw。

2026-10-18 bo s s 已确认在大脑层引入 DeepSeek Harness，采用官方 Python SDK、独立运行时及 J.A.C. 专用工具插件。它为现有 Qwen 提供 Agent 执行框架，不增加模型层级；具体契约见第十节。当前仅完成方案确认和文档同步，生产入口尚未接入 Harness。

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

- **2026-10-09 真机反馈与提示修订**：bo s s 已确认 battery 实际查询、文件与原生结果播报正确返回 80%，该指令闭环验收通过；音色相似度和长时稳定性分别确认。VAD 累计有声不足 300ms 的短候选只统计 `short_noise_ignored`，不触发用户话轮、不取消旧排队回复、不显示失败；有效句首 PCM 仍完整保留，达到原 300ms 门槛后才激活取消。完整句转写质量失败用 `transcription_rejected` 独立显示“语音 · 未确认”，连续失败十秒最多提示一次，成功转写或新会话重置间隔；每句质量拒绝与禁猜权限仍保留。语音通知、工具进度不改变在途任务忙碌状态；只有任务生命周期状态更新它。
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

### 九、待办清单与主动提醒（2026-10-11 确认，末期计划，尚未实现）

bo s s 已确认：在现有核心迁移完成后，最后落实本模块；智能眼镜等可选外设不作为前置条件。三层模型和记忆选型不变。本节是未来目标，不表示当前 GUI、任务路由或固定后端已经支持。

- **意图与记录**：由 MiniCPM-o-4_5 根据用户对话和场景判断正在做什么、计划做什么、要求谁做以及何时完成。明确任务经本地校验后自动记录并轻量提示；改期、取消、完成更新同一条目。指代、负责人或时间不明确时标为待确认，必要时追问；假设、否定、引用和模型自行声称完成不得成为任务或完成证据。复杂拆解/歧义可升级给 Qwen。
- **任务字段**：唯一任务标识、名称、必要用户原话/来源、负责人、实际执行者、状态、截止/提醒时间及其时区、前置条件/里程碑、进度依据、更新时间、提醒记录和下次检查时间。负责人为用户/其他人/J.A.C.；实际执行者可为用户、指定人员、MiniCPM、Qwen 或 OpenClaw。计划分配、实际提交、接收与完成分别记录，不把“记下交给谁”当作对方接受或工具已执行。
- **状态与证据**：待确认、待执行、进行中、等待反馈、已完成、已取消。由有效用户反馈、真实工具结果或执行端回执更新进度；没有新证据只能标注暂无新进度，不推定停滞、不编造百分比。逾期由截止时间与未完成状态计算，不冒充执行失败；已完成/取消须撤销尚未交付的旧提醒。
- **当前活动**：独立保存用户正在做的事、关联任务、确认/推测来源及最近确认时间。仅场景推测标为推测并随时间失效，不把每次活动变化都新增为待办，也不将安静等同于适合打扰。
- **本地真源与显示**：确认变更及时可靠写入本地 JSON，采用原子保存、幂等更新和可恢复的变更记录，重启后恢复任务和提醒状态；GUI 随有效事件及时刷新。ChromaDB 仅作可重建语义索引，摘要仍按 5 条/会话结束批量写入，待办落盘不得等凑满 5 条。支持更正和删除关联数据；必要原话最小化保存，不为本功能新增原始音视频留存，实际待办/活动/提醒数据不进 Git、不整份自动上传云端。
- **三种触发**：对话/任务回执驱动实时更新；普通静默巡检默认每 45 分钟，可设 30–60 分钟或更长，没有临近事项可延长；明确提醒时间、截止条件和关键任务事件单独触发，不等下次巡检。巡检先读本地结构化时间/状态，仅有理解需求时调用模型，不新增周期性向量检索例外，不阻塞音频回调或 Qt/asyncio 主循环。
- **提醒时机**：普通巡检和语音提醒优先在用户不说话、助手不播报且系统空闲时，结合当前活动和免打扰设置延后。明确到点提醒优先显示，到可播报时再说；语音目标复用 MiniCPM 原生输出，接线需另行验证。同一事项/条件去重并冷却，多条普通提醒可合并；用户可延后、今天不提醒、确认完成或取消。45 分钟是巡检间隔，不是催促间隔；未实际交付不得记为已提醒。
- **实施前置验证**：当前 MiniCPM 普通回复不能直接作为可靠的待办写入指令。需先验证独立、可校验且不被朗读的结构化事件通道，以及静默检查接入固定后端的方式；绑定用户语句/任务/会话代次和证据，拒绝迟到或重复事件。不预设上游已支持任意工具调用/动态 prompt 更新，不恢复旧 CALL_QWEN 文本令牌执行路径；若需协议/补丁变化，须在届时登记并验证，不在本轮修改锁文件或后端。
- **实施顺序与验收**：现有核心迁移完成 → 事件通道验证与待办记录/本地恢复 → 执行分配和进度同步 → 静默巡检/主动提醒 → 真机验收。重点验证误记、重复、改期/取消、活动过期、恢复、重复提醒、忙碌/免打扰、到点和证据归属。程序退出、电脑休眠及恢复后的补提醒策略单独定义，首版不承诺离线准时提醒；当前只更新文档，不增加代码、依赖、安装步骤或定时任务。

---

### 十、JACBrain Agent（2026-10-18 确认，待实现）

bo s s 已确认采用“官方 Python SDK + J.A.C. 专用插件，先独立多步骤验证，再接生产语音/GUI”的路线。本轮仅更新架构文档，不安装 Harness、不新增运行依赖、不改变现有工具权限或后端协议。

- **定位与连接**：Qwen 继续由 LM Studio `127.0.0.1:12345` 提供推理。J.A.C. Python 适配层通过官方 SDK，以 stdio JSON-RPC 驱动独立 Harness 运行时；配置专用模型 provider，使用本机 `/v1` Chat Completions 接口与精确模型标识。SDK 的 `base_url` 快捷参数不能未经验证就当作通用 provider 配置；须按所选适配器验证工具调用、角色、token 上限和关闭思考参数。MiniCPM 继续承担听看说与原生播报，OpenClaw 仍为后续云端层。
- **Agent 职责**：在本次已授权任务范围内拆解步骤、选择工具、接收真实反馈、有限重试或调整计划，交付文件与状态。现有 `run_agentic()` 是有限工具循环；Harness 的新增目标是统一多步骤编排、会话管理及恢复，不把旧循环误写为完全没有 Agent 基础。
- **语音任务归属**：目标统一为“VAD/Whisper 完整句 → 来源/质量/时效校验 → JACBrain 任务接口 → Agent 或单步快速路径 → 受控工具 → 真实证据/文件 → Qt 与原生短播报”。现有打开应用、网址、文件名搜索、站名解析与系统/联网查询可交给 Agent 编排，原工具作为插件复用；明确简单动作可保留同一任务接口下的确定性快速路径。每项任务只能由一个执行路径拥有，不同时派发旧 runner 与 Harness。
- **权限与工具**：Agent 提出的计划不构成新授权。现有单步指令继续保持精确参数、单动作及应用/目录/网站边界；多步骤任务使用独立权限域，显式限定目标、工作目录、可读写范围、允许工具与动作次数，不通过放宽现有 `local` 规则获得通用执行能力。缺少必要范围则追问。路径/符号链接、网页不可信输入、站名候选令牌/编号/代次/期限等现有校验继续由工具边界实施，模型不能绕过或自行确认。自定义 profile 仅装入阶段所需工具，禁用未批准的默认 shell、文件修改、云端路由或其他旁路；后续代码执行和桌面操作逐项验证后开放，高风险动作按原规则确认。
- **结果与播报**：工具实际结果及必要后置检查决定 completed/submitted/partial/failed/needs_input；模型文字或 Harness 回合结束不直接证明业务任务完成。保留浏览器交接不等于页面加载、进程存在不等于窗口前台等区别。报告原子发布，GUI 显示真实阶段，MiniCPM 只接收有界证据摘要；不朗读控制事件或思考正文，也不另加 TTS 服务。
- **生命周期**：任务 ID、来源语句、授权范围、会话代次、工具调用 ID 和执行结果建立可核验关联。推理、工具与 SDK 等待均在后台，有总时限、步数/token/输出预算及取消；初期单任务串行。接入首版继承停止/重连失效与忙碌不积压规则，派发和发布前复核。Harness 会话历史不等于未完成动作可重放；执行结果不明的有副作用动作须先核对，不能因重试或恢复重复启动、写入。已提交给系统的动作无法撤回。跨 Gateway 轮换继续长任务需另做任务所有权、授权续期、恢复与播报归属验证，通过前不开放。
- **模型与性能**：2026-10-17 本机查询到该 Qwen 已加载实例上下文为 8192 tokens，仅为当时快照；启动时读取实际配置，不把模型最大容量或 MiniCPM 的 `-c 4096` 当作 Qwen 当前容量。显式适配现有 `reasoning_effort="none"` 语义，禁止静默换模型/开思考。按实际容量裁剪工具 schema、提示与历史，摘要保留授权和证据引用。扩大上下文、增加并发或启用思考前实测与 MiniCPM 同跑的内存、延迟及音视频节拍。
- **本地状态与记忆**：专用 Harness home 拟放忽略的 `.cache/agent/`，报告拟放 `output/agent/`，工作目录按任务显式指定；配置模板/插件源码可入 Git，实际会话、凭据、工作文件、缓存和媒体不入 Git。运行时仅接本机 Qwen，按需工具联网不携带整段历史，未批准的遥测/云端回退关闭。Harness 任务历史不替代 JSON 事实真源、ChromaDB 索引和 BGE embedding；原记忆批量写入/检索时机及末期主动提醒顺序保持。
- **实施与验收**：先锁定 SDK/同版本 runtime、插件和所需构建工具，在独立环境验证精确 Qwen 的多轮工具闭环、错误/取消/超时与恢复；用专用测试目录完成“读取指定文本 → 整理差异 → 生成报告”，不越出授权读写范围。随后迁移现有语音单步任务并做功能等价、重复/迟到/撤销、候选选择和原生播报回归，再逐项开放多步骤工具组合、文件编辑、代码执行及桌面能力。SDK 兼容、多步成功率、真实语音、音色听感和持续同跑性能分别验收。
- **安装与版本**：实现时新增独立依赖/版本清单及一键安装阶段，检查 Python 3.11、macOS arm64 wheel、SDK/runtime 版本一致性、插件构建和本机模型能力；国内镜像失败回退可信官方源并校验制品，不在语音会话中下载。上游预览版本须固定，必要的小补丁单独登记；不直接跟随 master、不改写现有 `backend.lock.json` 的 MiniCPM 契约。本次不新增安装命令或锁定未经项目联调的 Harness 版本。

依据：[官方 Python SDK](https://github.com/deepseek-ai/deepseek-harness/blob/master/python/sdk/README.md)、[模型 provider 配置](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/user/guide/providers.md)。这些上游能力不代表 J.A.C. 已接通；具体版本与兼容性以实施时固定版本和真实联调为准。

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
| 大脑 Agent 框架 | 官方 DeepSeek Harness Python SDK + J.A.C. 专用插件，仍使用本机 Qwen | 2026-10-18 方案已确认；现有有限循环与受限任务仍为生产实现 | 独立多步验证 → 现有语音任务迁移 → 扩展工具，尚未安装或接入 |
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

## 当前开发状态（2026-10-18 更新）

- **JACBrain Agent 方案（2026-10-18）**：已确认第十节的官方 SDK/独立运行时/专用插件方案，并同步本地权威 DOCX。现有语音任务可逐步纳入 Agent 编排，执行权限与真实证据仍由程序工具层控制。当前仅文档更新，未安装 Harness、未新增生产多步骤能力；三层模型、记忆与末期主动提醒顺序不变。

- **通用站名解析（2026-10-17）**：`website_resolver.py` 继承当前有界公网访问层，独立站名查询不套用天气规则；国内必应 RSS 请求失败或完整站名失配再试全球，最多 5 个主机候选/3 个首页，提取站名、标题、正文和 canonical/og:url/JSON-LD 名称及主机冲突。`brain/website_tasks.py` 使用一次 Qwen 候选编号选择，不接受模型生成 URL；唯一 HTTPS 证据候选且模型同意才自动派发一次，主站与论坛/下载分站区分；证据不是所有权认证。HTTP、验证页、同名或证据不足走 `needs_input`；无匹配/网络失败如实报告。现有 `local` 单工具规则未全局放宽，新增 `website`/`website_choice`/`website_forget`/`website_cancel` 独立权限。GUI 最多三个按钮及“打开第一个网站”后续语音，候选令牌/编号/代次/生成时间/60 秒有效期绑定、消费一次，候选生成前说完的迟到转写不能选择新候选；新任务、停止/重连、过期失效，GUI 不直接打开候选 URL。任务全程限 60 秒，搜索 25 秒，100ms 等待取消与派发同锁复核；实际动作不可撤回。待选/无结果/打开/缓存清理均生成本地证据和程序固定原生摘要，报表仍在 output/，统计不存站名正文。动态缓存 `.cache/sites/resolutions.json` 原子 JSON、128 条上限，自动证据 7 天/用户选择 30 天；命中重新读首页并核对主机，失败或变域重新检索。“重新查找X网站”“忘记X的网站地址”“清除网站缓存”或 GUI 清理按钮管理动态缓存，源码内常用快捷别名仍保留。解析/缓存/网络均在后台，不新增 Chroma 检索或持久化原始媒体。`verify_website.py --name` 默认只解析，`--open` 明确实际打开；探针缓存与生产缓存隔离。调研 SearXNG/DDGS 后继续复用 httpx，无新依赖/服务/后端协议/锁文件变化；三层架构、记忆与云端顺序不变。

- **M2c 受限本地任务（2026-10-17 更新）**：`src/tools/local_intents.py` 完整句解析打开白名单应用、明确 HTTP(S) 网址和指定目录文件名搜索；生产 `TaskPipeline` 接入，未知本地动作也禁止普通模型抢答，缺搜索目录用固定原生追问。`BrainTaskRunner` 向精确 Qwen 只提供一个工具与本句参数枚举，整批必须恰好一次且逐字一致才执行，结果由实际证据生成，不再请求模型自由生成成功结论。新 `local_actions.py` 使用无 shell 的系统启动器与可取消等待，启动前在会话代次锁内复核；已交给系统的动作不可因取消撤回。macOS 应用白名单为 Safari/Calendar/Notes/Calculator/TextEdit，使用 bundle id 启动并核对进程；启动提交、检测到运行与窗口前台分开。网址支持完整标准 HTTP(S)，以及百度/GitHub/Python/B站（哔哩哔哩/bilibili）固定名称，可带网页/网站/官网/官方网站及繁体修饰；“打开B站”“打开B站网页”均指向同一明确地址。未预设站名现进入上述动态解析分支，明确应用请求不得降级成网站，浏览器交接不等于页面加载；该入口使用用户默认浏览器，不继承 `search_web` 的无 Cookie/固定公网 IP 保证，浏览器自身网络/登录设置生效。指令词兼容繁简，但网址/路径/文件名关键词原样保留，不默默转换到另一个真实路径。`local_file_worker.py` 独立进程只读文件名，明确主目录子目录、跳过隐藏/链接，POSIX 逐级 NOFOLLOW，4 秒扫描/7 秒等待、60000 条目/20 层/20 结果，部分扫描明确标注。GUI/原生播报区分 completed/submitted/partial/failed，实际证据和报告保持本地 `output/m2/qwen/`，不入 Git。`verify_local_tasks.py --task` 会真实执行指定动作；Gateway 文件探针识别出的本地动作也会实际执行，仅不打开采集/播放设备。无新增依赖/后端补丁，安装器补离线导入和解析自检；Windows/Linux 仅保留搜索/网址交接代码，应用白名单首版仅 macOS，其他平台未实测。仍非任意命令或通用鼠标键盘控制；记忆、云端与末期主动提醒顺序不变。验收结果以 CHANGELOG 对应日期记录为准。

> **M0 已通过且 30 分钟长测免测；M1 基础听看说、GUI 和手动启停已由 bo s s 真机验收。** M2a Qwen 独立只读工具/文件闭环已验证；M2b 已接入并行转写与 Gateway 只读任务路由，2026-10-08 真机文字结果已获用户确认；原生任务播报已接入，音色听感待确认。旧 MiniCPM 及传统运行入口已删除，云端和记忆尚未迁移。

- **M2b 并行转写/任务**：`src/omni/task_pipeline.py` 与 `transcription_worker.py` 在同一份重采样 PCM 上旁路 VAD → Whisper → Qwen，只在后台工作；麦克风回调仍只复制至主队列。WebRTC 30ms、300ms 预留、600ms 句末，至少 300ms 有声段，12 秒长句拒绝。Whisper small 在独立解释器 CPU INT8/2 线程运行，主进程不导入 faster-whisper/CTranslate2/PyAV；UTF-8 管道有界，无 PCM 文件、torch/MPS 或运行期下载。资源与独立 SHA256 见 `transcription.lock.json`，默认仓库外 `~/.cache/jac/models/whisper-small`。仅通过置信度校验的完整、无播放重叠的用户指令进入显式白名单，转写原话显示与下一会话上下文同步；不依赖助手输出或旧 CALL_QWEN 令牌。开放系统状态查询/报告与明确联网搜索；另有 M2c 受限本地动作，尚无通用桌面控制/云端工具。句子代次/ID 去重，15 秒过期、忙碌不排队；重连/停止取消旧 HTTP/任务/文件发布，原子发布与代次共锁。旁路故障明确暂停任务且保留感知上行。GUI/真实 CLI 默认开启，SDK opt-in、文件验证显式 `--transcription`，可用 `--no-transcription` 保留基础交互。GUI 显示用户转写、任务进度和真实报告入口，任务文本/证据仅写到本机报告，统计不含正文。原生结果播报使用实际工具证据短句（综合报告播报完成提示），与真实 PCM 同块上行，经既有 LLM 隐藏状态 → TTS → Token2Wav 生成 24k PCM；不让感知模型重新推理状态数值。真实后端 60 秒合成文件闭环通过：唯一转写/任务/报告，关闭确认与清理成功；该合成文件验证没有打开真实设备。2026-10-08 bo s s 反馈“测试成功，能正常输出文字结果”，真机语音任务文字结果按用户反馈验收通过；未新增具体指令、次数、时长、延迟或报告内容核对记录，音色听感未由该反馈确认。VAD/置信度不等于说话人识别或绝对准确，长时稳定性不从短测或合成文件推定。
- **联网查询（2026-10-09）**：`src/tools/web_search.py` 使用现有 httpx 与标准库，默认国内必应 RSS，失败再试固定全球入口；免密钥、不绕验证码、不保证未公开搜索接口永久有效。每次至多 5 条结果；固定规则分隔天气词/文档词，记录原话与实际搜索词；前三个公网来源并发读静态正文，整次网络 25 秒、每页 6 秒、512KiB 解压后字节上限、3500 字正文及最多 30 个有界段落。URL/逐跳 IPv4 DNS 公网核验后连接已核验 IP，并保留 Host/TLS SNI 及证书验证；不访问本机/内网或继承代理/Cookie。Qwen 仅可对本句关键词调用一次 `search_web`，只选择已读来源的段落编号；目标天气日期与英文具体词先筛选候选，程序核对原文再交付，不接纳自由生成的联网事实、链接或发布时间。结果采用来源摘录，未提供自由改写/翻译；原文一致不证明网站内容真实、时效或相关性。语音支持“上网搜索…”及明确城市天气问句，保留置信度/代次/忙碌/播放重叠约束，联网意图阻止普通模型抢答。GUI 显示实际工具/来源/正文读取状态，UTF-8 报告保存链接、抓取时间及原始证据；原生播报使用已核对的有界摘录。仅查询词出网，不上传对话历史、参考音或音视频。实际语音入口听感须另做用户验收；独立验收使用 `verify_web_search.py`，不打开设备。
- **联网纠偏（2026-10-09）**：完整解析“查寻”“让我上网查一下”；地区/日期无关顺序，天气搜索固定以地区开头（明天海口的天气 → 海口天气），日期仍按用户原话筛选。缺少城市使用程序固定原生追问，标为 needs_input，工具/网络/报告均为零；不纠正“插”等同音误识别。天气来源标题/摘要先核对地区和天气主题，失配尝试固定全球入口，不读取无关小说/字典。空摘录为 no_answer，保留查询记录，不记作任务成功或播报查询完成。GUI 显示实际搜索词和追问播报状态。
- **视觉问句与转写隔离（2026-10-09）**：提示明确支持“你看到了什么”；当前用户语句编号同时约束转写原话、拒绝提示与任务提交，迟到旧句不能污染新句。拒绝事件携带语句编号/原因，统计只保留原因计数，原质量门槛不变。质量拒绝的 ≤4 秒视觉候选可保留完整 PCM、分别补 300/600ms 前导静音复核；两次原阈值合格且同一中性视觉问句才恢复，系统/联网主题、长句、重叠、过期/新句不适用，无示例提示/阈值放宽。真实带噪短句初次失败后恢复；有内容的合成画面 65 秒文件验证返回正确蓝色界面描述与 63360 样本/2.64 秒原生音频。此前灰帧长/短对照均沉默；用户原 PCM 未保存，合成验证不证明当次真机声学原因或代替用户验收。
- **M2a 独立 Qwen**：`verify_toolcall.py` 使用同一 Python 3.11 环境与已有 httpx，只开放 `get_system_info`；`--task` 是独立终端显式任务，不是生产语音入口。`src/brain/lm_studio.py` 核对 `/api/v1/models` 的精确已加载实例及能力，要求 LM Studio 0.4.8+，请求使用 `reasoning_effort="none"`，不移除参数后默默重试、不恢复思考尾段、不自动加载或回退其他模型。`BrainTaskRunner` 将真实证据和最终回答原子发布到忽略的 `output/m2/qwen/`；错误/取消不发布未完成文件。单次 HTTP 90 秒、整个任务 120 秒、最多 4 轮；等待中每 100ms 检查取消，各工具执行前再检查。已开始的只读系统查询有自身超时，不能声称任意工具可抢占终止。普通聊天 SSE 保留，agent 最终回答不再额外生成一次。三项独立真实只读验证通过；M2b 合成文件验证已与 MiniCPM/CPU Whisper 同跑并交付系统报告，仅代表这段测量，不推定真机或长时性能。M2b 已在语音旁路接入来源校验与 GUI 只读路由，并接入原生结果播报；完整报告仍保留文字和工具证据。
- **M1 Gateway 入口**：`main.py --gateway --consent-devices`（终端）、`main.py` 或 `main.py --gateway --gui`（GUI），使用 Python 3.11 独立环境；GUI 可异步调用固定启动器启动/回收自己拥有的后端；外部已运行服务只探测与复用，不接管。收到启动器就绪事件后才开放语音，停止后端/关闭窗口先等待音视频会话清理，再回收所属后端。GUI 仅使用 Gateway，不依赖旧 torch/PyAudio/Voicebox/YOLO。`omni_backend` 只接受 `gateway`；`legacy` 会明确报错。`main.py --gui` 与 `python -m src.omni --gui` 同样进入 Gateway，`--gateway` 保留兼容。
- **启动修复（2026-10-14）**：无参数直接运行 `main.py` 默认打开 GUI，脚本启动时在第三方导入前优先切换到已安装的 `.cache/m0/venv` Python 3.11；显式终端/文件参数保持不变，F5 应仍指定项目解释器以保留调试连接。后端源码、编译产物与音色前端不得长期放在系统临时目录。本机原 `/private/tmp/jac-m0.QL0OXs` 已缺失普通文件；恢复采用仓库旁的 `jac_gateway_backend/` 长期目录，原模型保留并通过绝对链接复用，仍须精确 commit、完整补丁及 SHA256 校验。启动器将缺失/损坏 Git 仓库转为路径与恢复提示，通过错误事件保留在 GUI，避免将本地安装损坏误称 M0 验收未通过。目标架构和锁文件不变。
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

- `main.py`：统一 Gateway CLI/GUI 入口；无参数默认 GUI，脚本优先复用项目 3.11 环境；`--gateway` 为兼容参数，不再分流至旧实现。
- `src/capture/camera.py`：未迁移的旧通用摄像头组件，无生产入口；Gateway 使用 `src/omni/media.py` 独立线程 640×480。
- `src/analysis/detector.py`：YOLOv8 检测器封装。
- `src/audio/recorder.py`：VAD 麦克风录音（旧：PyAudio + WebRTC VAD；新架构改 SoundDevice）。
- `src/audio/stt.py`：Whisper 语音识别（旧架构非流式转写；新架构音频直通 o 版全双工，STT 仅保留作可观测/校验）。
- `src/audio/`（`voicebox_tts.py` / `qwen_tts.py` / `speaker_factory.py` / `tts.py` / `playback.py`）：旧架构 TTS 链（Voicebox → Qwen3-TTS → 系统 TTS）——**新架构语音输出改由 MiniCPM-o 自带音色克隆承担，此链待移除/降级**。
- `src/brain/llm.py`：`LocalBrain`，多后端大脑层（lm_studio / ollama / llama_cpp / auto）——大脑层 `qwen/qwen3.6-35b-a3b` 在新架构中保留为第二层。
- `src/tools/`：Function Calling 工具层（装手）——新架构中归属大脑层 `qwen/qwen3.6-35b-a3b` 的 Tool Use / Agentic Coding。
- `src/judgment/` 与 `src/runtime.py`：已删除，旧 MiniCPM 判断轮询与传统运行编排不再启动。
- `src/memory/`：旧架构记忆子系统（fastembed + 自研 MemoryStore）——**新架构改 ChromaDB + BGE-Small-ZH-v1.5，此目录待重写**。
- `src/omni/`：Gateway 客户端/协议/设备/桌面运行时和 GUI 后端控制器；旧 `client.py` / `server_launcher.py` / Voicebox 桥接 / 回灌 / 令牌过滤已删除。旧 router.py 仅保留草案；M2b 的生产路由由 TaskPipeline + BrainTaskRunner 承担，已扩展受限本地动作，不宣称通用 agent 可用。
- `voices/silverwalf_voice.wav`：方案 B 原生音色参考 WAV，启动会话时编码发送给本机 Gateway。
- `backend.lock.json`：后端与模型精确版本、SHA256、端口及协议配置。
- `verify_gateway_tasks.py`：M2b 固定 Gateway 文件探针，真实 VAD/Whisper/Qwen 与原生结果音频核对，仅保存统计与报告文件名，不打开设备。
- `src/omni/task_speech.py`：单个待播报队列、实际证据短句、任务代次与字面文本/音频归属校验；不保存 PCM。
- `new_computer_download/update_native_backend.py`：仅升级登记的旧补丁或干净固定源码，可重新编译；陌生源码拒绝覆盖，应用失败回滚。
- `transcription.lock.json` / `new_computer_download/prepare_transcription.py`：仓库外 Whisper small 资源锁与 HTTPS 下载/CPU 自检。
- `src/tools/web_search.py` / `verify_web_search.py`：免密钥必应搜索、公网来源读取与独立 Qwen 文件验收；联网报告和测试产物仍保存在忽略的 output/。
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

- Gateway 已接入听看说与新 GUI；旧 `:9060`、轮询判断模型和传统运行入口已删除。M2b 已接入并行转写和 Qwen 明确只读任务；公网搜索和受限本地动作已接入；通用桌面控制、ChromaDB 和 OpenClaw 尚未接入；原生结果播报已接入。未迁移通用音频/记忆/检测组件保留，无生产入口。
- 运行依赖外部软件（LM Studio / 固定版本 Gateway、Worker、llama-omni-server / 可选云端 OpenClaw）。
- M0 基于已有真机验证与 bo s s 明确验收通过；30 分钟稳定性免测，不作为阻塞项。M1 文件回放不等于新增生产 GUI/设备的真机听感验收。
- M2b 已展示确认的 Whisper 用户转写，并注入后续会话的有界上下文；被拒绝或未经转写的语音不恢复，重连仍有采集间隙。
- 无 WebRTC AEC（新架构以戴耳机规避回声，AEC 需求待重新评估）。
- 无云端 OpenClaw 集成（新架构新增项，代码未落地）。
- 原生音色克隆接线已修复，但不保证与参考 WAV 完全一致；技术加载确认与用户听感验收分别记录。CPU 前端冷启动/条件加载会增加会话初始化时间，不阻塞已建立会话的音视频回调。
