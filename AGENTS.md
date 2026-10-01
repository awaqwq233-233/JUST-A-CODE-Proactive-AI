# J.A.C. 项目说明（AGENTS.md）

## 项目概述

J.A.C. = "Just A Code"。这是一个**本地优先的多模态 AI 管家原型**，灵感来自 JARVIS：通过摄像头/麦克风感知用户的环境，基于当前场景做推理，用语音 TTS 回应，目标是无需用户显式触发即可主动行动。

长期产品愿景是**打造一个强人工智能管家（Proactive AI Butler）**：

- 它能**主动感知**环境、提前**规划与预警**、在危险发生前发出**警示**、并能**应急接管**。
- 智能眼镜 / MR 等可穿戴终端只是**可选的随身交互外设**，而非项目的主目标或核心愿景——J.A.C. 的本质是 **AI 架构、系统与整套主动服务框架**，可穿戴终端只是承载它的一种形态。
- 主机（当前开发期）：MacBook Pro 级别的本地机器做低延迟的感知与推理。
- 云或局域网服务器承担更重的推理、长期记忆、路由到更大的模型，以及本地算力不足时的外部 API。
- 助手最终要支持主动感知、agent 式任务执行、外部 API 调用、语音/HUD 输出，以及闭环任务循环。

当前代码库是一个 **macOS 优先的 Python 桌面原型**（同时保留 Windows/Linux 的跨平台兼容代码，但不保证在 Windows 开发机上跑通），还不是最终的眼镜/云端架构。

## 文档同步硬性规定（Agent 必读）

本仓库有四类**必须随代码改动同步**的文档，任一代码/配置/依赖变更后都必须检查并更新对应条目：

1. `README.md` — GitHub 首页文档（双语：英文在前、中文在后）。每次改动后确保其描述与项目真实状态一致。
2. `AGENTS.md` — 本文件，开发者契约。架构/运行方式/依赖/文件路径变化必须同步。
3. `CHANGELOG.md` — 变更日志。**同时是「文档归口中心」**：原 `codingLOG.md`（差距笔记）与原 `docs/memory/` 四份记忆子文档已于 2026-10-01 全量并入本文件（附 A 差距笔记 / 附 B 记忆契约 / 附 C 记忆测试计划与实现真值 / 附 D 用户指南 / 附 E 隐私说明 / 附 F 运维 Runbook），源文件已删除。每次变更追加一条用户可读的改动说明；涉及差距或记忆子系统的修订，**直接改 `CHANGELOG.md` 对应附录**，不再新建 `docs/` 专项。

**查看改动时的强制动作**：每当 Agent 需要了解「最近改了什么 / 当前实现状态」，必须优先读取 `CHANGELOG.md` 与 `AGENTS.md` 这两个文件，而不是依赖记忆或过时摘要；需要「与最终目标的差距」时读 `CHANGELOG.md` 附 A。

配套检查项（每次提交/对话后）：

- 检查 `.gitignore` 是否需要新增忽略（如新增大体积/二进制产物）。
- 检查 `requirements.txt` 是否新增依赖；若有，同步更新 `new_computer_download/` 下的一键安装脚本与 `new_computer_download/READMEfirst.md`。
- `codinglog_by_awaqwq233/` 文件夹**只由 bo s s 手动维护**，Agent 不得自动编辑或同步其内容。
- **开发平台**：当前以 macOS 为主开发机，保持跨平台兼容代码；Windows 开发机已不再使用（旧 `build.py` / `fix_install.py` 等 Windows 专用脚本已删除）。**macOS 27 适配良好**——此前 GUI 渲染崩溃根因为渲染代码 bug（已在 gui.py 修复）、TTS 异常为本机代理导致 Voicebox 连不上 HuggingFace（已通过改用本地 Voicebox 解决），后续文档不再归咎系统版本。

## 当前开发状态（2026-10-01 补正，此前未记录）

> 结论先行：**项目自 2026-09-28 起暂停开发，boss 正在重新审视技术实现路径**，本文档同步冻结在此状态。

- **代码基线**：全量回归 `134 passed`；最近一次代码改动为 2026-09-28 的 omni `prompts.py` 治理（删 5 条具体示例改抽象规则，`py_compile` 过、omni 回归 62 passed）。
- **已定性的 MiniCPM-o 能力天花板**（非代码 bug，属模型 / llama.cpp-omni 固有限制）：
  1. **视觉分辨率仅 grid 1x1 / 64 视觉 token**（服务端日志铁证 `image encoded ... grid: 1x1`），细节视觉问答不可行（真机曾把 boss 本人误判成"电脑桌面"）。
  2. **音频理解（ASR）质量差**：内建麦离嘴远、人声 RMS 仅 0.03~0.065，模型听不清寒暄，会从 prompt 示例里"捡"输出（"查电池"即被复读的示例）。
  3. **"说/听切换"不稳**：`listen_prob_scale` 1.0 偏沉默、0.8 偏抢话，无稳定工作点；它是**开口意愿**旋钮（`listen_bias=(scale-1.0)*2.0`），**不是 VAD、不影响听清**。
- **待 boss 拍板的三条路线**（挂起未动）：①继续 MiniCPM-o 全双工（接受天花板 + 视觉问题升级给 qwen）；②改 turn_based / half-duplex；③重构为「qwen 主 + MiniCPM-o 仅做 VAD」。
- **已定位但未修的 bug**：①qwen `enable_thinking=false` 未生效 → `tool_calls` 空、**升级通道实际空转**（2026-09-28 LM Studio developer logs）；②「真提问被自己拦截」——护栏判据（峰值阈值 0.06 偏低、门控与护栏排除项耦合）失准（P2）；③无 WebRTC AEC（方案见 `docs/webrtc_aec_plan.md`，关键障碍是发声全走 `afplay` 子进程、Python 侧拿不到 far-end 参考 PCM，须先改播放链路）。
- ⚠️ **文档口径提醒**：`CHANGELOG.md` 附 A 中 2026-09-17 那条「视觉 token 吃爆 KV → 每 30s 清空上下文 → 复读示例」的假设，**已被 2026-09-28 的 A1 实验（全程 `--no-video` 零视频帧，幻觉依旧）证伪**，勿再据此修 bug。

## 当前实现

可运行入口是 `main.py`。它把以下模块串联起来：

- OpenCV 摄像头采集：`src/capture/camera.py`（自动探测摄像头 ID，默认 1280×720）。
- YOLOv8 物体检测：`src/analysis/detector.py`（仅 YOLOv8，`conf=0.5`；权重首次运行由 ultralytics 自动下载，不进仓库）。
- 线程安全共享上下文：`src/utils/context.py`（转录环形缓冲、最新帧缓存、介入标志）。
- VAD 麦克风录音：`src/audio/recorder.py`（PyAudio + WebRTC VAD，阈值/预热/最短时长已调优）。
- Whisper 语音识别：`src/audio/stt.py`（默认 `model_size="tiny"`，**非流式**；强制 `language="zh"` 简体中文，并内置繁→简兜底归一化，根治自动检测漂移导致的繁体/乱码）。
- 本地大脑推理：`src/brain/llm.py`（`LocalBrain`，多后端：lm_studio / ollama / llama_cpp / auto）。
- 语音合成：统一走 `build_speaker` 工厂——**Voicebox（开源克隆引擎，macOS 主力）→ Qwen3-TTS（仅 NVIDIA）→ 系统 TTS 兜底**。克隆参考音固定为 `voices/silverwalf_voice.wav`（唯一音色）。
- **主动判断引擎**：`src/judgment/judge.py`（`JudgmentEngine`，连 LM Studio 的 `/v1/chat/completions`，每 4s 拿「截图 + 转录文本」问一次要不要介入，**默认开启** `JUDGMENT_ENGINE_ENABLED=True`；模型未加载则自动进入被动模式，不报错也不主动）。
  - ⚠️ **模型名口径（2026-10-01 补正）**：代码默认值是 **`minicpm-v-4_5`（V 版 9B VLM，不是 MiniCPM-o）**，标识符来自 `config.judgment_model_name` / `JUDGMENT_MODEL_NAME`（规范化匹配 LM Studio 实际加载 ID，匹配到别的名字会回填并打日志）。**走标准 chat API 时 o 版的「听原始音频 + 1Hz 主动决策」用不上**，换上 mini-cpm-o 也只会退化成与 V 版重叠的 9B VLM、收益≈0；要真正用上 o 版必须按「持续喂音视频流 + 订阅主动发言事件」重构（见 `codingLOG.md` §1）。

### 运行流程（`main.py`）

1. 初始化摄像头、YOLO 检测器、扬声器（统一走 `build_speaker` 工厂：Voicebox → Qwen3-TTS(仅 NVIDIA) → 系统 TTS 兜底；macOS 上 Qwen 禁用、由 Voicebox 接管）、Whisper、`AudioRecorder`、`LocalBrain`（**默认 `backend="lm_studio"`，大脑模型为 LM Studio 中的标识符 `qwen/qwen3.6-35b-a3b`（原生支持视觉、禁用思考模式）**）。
2. 启动三条线程：音频主循环（监听→识别→唤醒判断→响应）、**控制台输入线程**、判断引擎线程（daemon）。
3. 主循环每帧：取帧 → YOLO 检测 → 更新 `SharedContext`（视觉摘要 + 缓存最新帧）→ 绘制 FPS / 状态灯（Listening/Thinking/Speaking）→ `cv2.imshow`。
4. 唤醒词集合：`jac` / `j.a.c` / `杰克` / `接客` / `你好` / `hello jac` / `hi jac` / `你好 jac` / `hey jac`。
5. 唤醒后进入 `AWAKE` 状态；`SYSTEM_STATE` 在 **20 秒（AWAKE_TIMEOUT）** 无交互后自动回到 `SLEEP`；用户说「再见/休息」立即休眠。
6. 用户输入进入 `handle_user_text` → `process_response`：取视觉摘要 → 若判定为视觉相关问题（看到/看见/有什么/画面/是谁…）且后端支持图像，则把真实摄像头帧发给 `brain.think_with_image()`；否则用文本分支处理（见下「Function Calling（装手）」）。
7. 模型输出**纯文本回复**（不再带 `[情绪]` 标签），经 `_strip_boilerplate` 清洗与残留括号清除后，直接交给扬声器**中性朗读**（不再传 `emotion_hint`）。
8. 若主动判断引擎已激活，主循环每帧检查介入请求，确认后新开 daemon 线程主动回应（绕过唤醒词）。

### Function Calling（装手）

非视觉文本分支在 `process_response` 中接入了 agent 式工具调用（2026-08-11 落地），让 J.A.C. 真正"有手"：

- **开关与前提**：默认开启（`TOOLS_ENABLED` 环境变量可关）；仅当后端支持结构化 function calling（`brain.supports_tools()`，当前 `lm_studio` / `ollama`）且 `src/tools` 有工具时启用，否则降级为普通流式对话。**GUI 右侧可折叠选项面板提供「工具功能（Function Calling）」勾选框**（启动前配置，与主动模型/TTS 开关一致），由 `JACRuntime.start` 桥接到 `main.TOOLS_ENABLED` 生效。
- **大脑侧**：`src/brain/llm.py` 新增 `think_with_tools(messages, tools)`（发送 `tools` + `tool_choice=auto`，解析 `tool_calls`）与 `run_agentic(prompt, tools, tool_executor)`（工具调用循环生成器，流式吐出最终回答，保留打字机效果）。
- **工具层**：`src/tools/` 提供四个白名单工具——`open_url` / `open_app`（打开网页/应用）、`search_files`（只读本地文件搜索，限定用户目录）、`get_system_info`（时间/电池/CPU/内存）、`run_command`（**受限 shell**：仅白名单命令，拦截 `rm`/`sudo` 等危险操作）。所有工具经 `get_tool_schemas()` 生成 OpenAI 风格 schema 发给模型，`execute_tool(name, arguments)` 执行并回喂结果。
- **循环**：模型要调工具 → `execute_tool` 执行 → 结果作为 `tool` 消息回喂模型 → 重复直到模型给出最终自然语言回答 → TTS 朗读。工具执行任何异常都被转成错误文本回喂，不让整轮对话崩溃。
- **安全边界**：工具只做打开应用/网页、只读搜索、状态查询、受限命令；绝不做删除/提权/任意写；`search_files` 仅扫用户目录，`run_command` 白名单 + `shell=False` 双重防注入。

### 多模态图像问答

`LocalBrain.think_with_image(prompt, frame)` 把当前帧编码为 JPEG base64，按 OpenAI 多模态消息格式发送：`lm_studio` / `ollama` 原生支持；`llama_cpp` 通过 `_find_mmproj()` 自动挂载 `mmproj-*.gguf` 投影。图像请求失败时降级为基于 YOLO 检测摘要的文本回答（`build_text_only_vision_reply`）。

### 键盘与输入控制

- `q`：退出。
- `SPACE`（空格）：手动唤醒（「我在，请讲。」）。
- **控制台 stdin 文本输入**：任意时刻回车输入文字，以 `source="控制台"`、`bypass_wake=True` 直接进入思考，绕过唤醒词。

### 全双工 Omni 模式（src/omni，M5 验收 + M7b/M7a）

`src/omni/` 提供**全双工本地多模态接管**模式，与 `main.py` 传统被动链路**互斥**（OMNI 模式下不启动 judge 判断引擎与 main 的唤醒词循环）。运行入口 `python -m src.omni --mic <id>`（或 GUI 的 OMNI 模式开关）：

- **全双工闭环（M5 验收）**：MiniCPM-o-4_5 经本地 llama.cpp-omni server（9060，Metal，GGUF Q8_0）跑**全双工**——持续听/说、主动打招呼、按 `<<CALL_QWEN>>` 令牌升级到 `qwen/qwen3.6-35b-a3b` 调 `src/tools/` 工具、回灌播报；整条闭环真机验收通过（2026-08-15）。
- **主对话 LLM 流式 + M7b 句子级 TTS 桥接**：omni 下行 `response.output.delta` 逐字吐文本；`src/omni/voicebox_bridge.py` 按标点/句子边界把 text delta 攒成句，攒够一句即送本地 **Voicebox（JAC 克隆声纹）** 合成并播放（独立 daemon 播放线程串行保序），实现「说一句听一句」近似实时感；omni 自带 TTS 音频在桥接启用时丢弃。
- **回灌（M7a）**：`speak_result` → `src/omni/backfeed.py` 的 `speak_text_via_voicebox` 用 JAC 克隆声纹播报（替代原 omni 第二 turn_based 会话——server 单会话限制会拒第二个会话致 `ConnectionClosedOK` 无声音，已根除）。
- **升级线程的停止语义（2026-09-24 修复）**：升级 worker 跑在 daemon 线程里，`EscalationRouter.escalate()` 内部是**同步阻塞**的 LLM + 工具循环，**无法抢占式中断**，因此采用「协作取消」——`escalate(..., should_stop=callable)` 每收到一个流式分片检查一次，命中即返回空串并停止 `on_progress`。`runtime` 侧有三个检查点：①线程入口（已停止时连 `EscalationRouter` 都不创建）；②**结果播报之前**（早于任何 TTS / GUI 写入）；③异常分支。`stop()` 会 `join(timeout=1.5s)` 升级线程，**且必须早于关闭 `omni_client`**——历史 bug 正是顺序反了：worker 读到 `omni_client is None` 后走了 `speak_text_via_voicebox` 降级分支，把"保证答案一定出声"变成了"停止后一定出声"。回归见 `tests/test_runtime_escalation_stop.py`（含防修复过度的正例）。
- **CLI 开关**：`--mic <id>` 指定麦克风、`--no-voicebox` 关克隆声纹桥接（改系统 TTS/仅文本）、`--no-play` 关 omni 自带音频播放（排查用）、`--list-mics` / `--mic-gain` 设备排查、`--no-auto-launch` 不自动拉起 server、`--video-interval <秒>` 图像上行间隔（默认 1.0，0=每段都带图）、**`--no-video` 完全关闭图像上行（纯音频全双工，P0 变量分离实验）**、`--debug` 逐块上行诊断日志（等价 `OMNI_DEBUG=1`）、**`--no-echo-gate` 强制关闭回声门控（默认按输入+输出设备自动判定：耳机+独立麦→关可打断，输入本身是耳机→开防自激）**。
- **令牌拦截与多轮升级（2026-08-15 修复）**：`client._on_text` 做**检测前置**——含 `<<CALL_QWEN>>` 的 delta 只把令牌之前的文本送 Voicebox 桥接朗读、令牌及任务描述丢弃并立即触发升级，**绝不把"问题本身"当答案朗读**；`voicebox_bridge.feed` 同步加令牌截断兜底。`_call_qwen_fired` 触发后会在每轮 `listen` 事件经 `_reset_escalation_state()` 复位，支持**反复升级**（修复"第二次升级被吞"）。回灌与桥接共用同一加锁 `VoiceboxSpeaker`（uuid 文件名防并发互覆盖），升级答案在 `speak_result`/`backfeed` 中**一定出声**（Voicebox 优先→系统 TTS 兜底，去掉原 `is_running()` 静默跳过）。
- **回声门控 Echo Gate（2026-09-06 新增，默认 auto）**：外放场景下 TTS 声音会被本机麦克风重新采集，omni 把**自己的语音**当成用户发言 → 自问自答 → 幻觉出 `<<CALL_QWEN>>` 任务并真触发升级（真机已复现：`[TTS] 正在播放` 与 `🎙 检测到人声 RMS=0.022` 同帧出现）。根治需 WebRTC AEC；工程等价做法是 `src/audio/playback.py` 维护全局「正在出声」状态，`_push_loop` 在回声窗口内用**等长零字节**替换真实采集推送（保持实时节奏，避免只听不说），该帧不计为人声。护栏 `_has_recent_speech()` 在回声期一律判为幻觉（不升级、不静音）。**开关（bo s s 选耳机，故默认 auto）**：`resolve_echo_gate()` 按 PyAudio 默认输出设备自动判定（耳机/蓝牙→关可打断，扬声器外放→开防自激）；CLI `--no-echo-gate` 强制关、环境变量 `OMNI_ECHO_GATE=auto|0|1`、GUI OMNI 面板「回声门控」下拉可设；拖尾 `OMNI_ECHO_TAIL`（默认 0.8s）。**代价：门控期间听不到用户插话**，故戴耳机时自动关闭以保留打断能力。
- **幻觉任务不得升级 / 不得显示（2026-09-06）**：`_hallucinated` 守卫杜绝「拦截后又被后续句号偷偷 fire」；令牌之后的任务描述属**内部指令**，经 `_broadcast` + `_shown_len` 裁剪后不再广播到控制台 / GUI（此前用户看到的「给您推荐一部电」实为 omni 模型幻觉输出，不是 ASR 识别结果——full_duplex 协议不回传用户 ASR 原文）。
- **GUI 实时整合（2026-08-15）**：OMNI 模式右侧选项面板新增「麦克风音量条」+「OMNI 实时回复」文字区（轮询 `get_latest_mic_level`/`get_reply_text` 刷新）+「麦克风增益」框（接 `config.omni_mic_gain`→`OmniClient.mic_gain`，缓解内建麦离嘴远能量不足）；勾选 OMNI 时 judge/TTS/tools 开关灰掉并提示"OMNI 模式下不生效"（架构互斥）。视频画面此前已接入。**右侧面板已改为可滚动侧栏（2026-09-17）**：内容装进 `QScrollArea`（只竖滚、`NoFrame`、去底去边防双层卡片），「« 收起选项」按钮固定在滚动区外贴顶；面板用固定宽度带 `min 340 / max 420` 且 `stretch=0`（**退出与视频区/控制台的横向争抢**，此前三列按 3:2:1 抢空间，窗口稍窄就把长标签截成「Listen 概率系数（ON」）；`_labeled_slider` 的容器与滑条、`mic_bar`、`omni_reply` 都设了 `setMinimumHeight` 下限——**空间不够时出滚动条，而不是把控件压扁**（此前底部两个滑块被压成几像素的方块）。
- **Listen 概率配置协议（2026-09-13 修复）**：`listen_prob_scale` 必须嵌套在 WebSocket `session.init.payload.config`，因为 llama.cpp-omni 的 `parse_session_init()` 只从该位置读取；放在消息顶层会被静默忽略，后端回落默认 `1.0`，表现为连续 `listen=1` / `is_end_of_turn=0`、麦克风有声却永不回复。`OmniClient._build_session_init()` 统一构造正确报文，启动日志会回显实际发送的系数；默认 0.5，GUI / `OMNI_LISTEN_PROB_SCALE` 仍可调。**注意它是「开口意愿」旋钮而非灵敏度**：服务端 `omni.cpp` 实现为 `listen_bias=(scale-1.0)*2.0` 加到 `<|listen|>` logit 上，`<1` 更爱抢话（也更易胡说）、`>1` 更爱听；它**不影响听清与否**，别拿它当 VAD 调。
- **P0-a 令牌前缀 holdback + 畸形令牌容忍（2026-09-13，二轮+三轮）**：omni 服务端 `make_text_delta(frag)` 是**按 token 逐片**下发文本，`<<CALL_QWEN>>` 必然被切成 `<<CALL_Q` 这类碎片。**三轮追加两条硬事实**：①模型**不保证**原样吐出令牌——真机确认它会吐 `<<CALL_ QWEN>>`（中间夹空格/换行，空格来自模型本身），精确匹配会漏、碎片会被当普通对话朗读（同一句被反复念），且 `<<CALL_` 虽是合法前缀但后接空格就不再是前缀、holdback 也拦不住；②`listen` 在 full_duplex 下**每段都会来**，不能用它清文本缓冲（会把正在下发的令牌拦腰截断，剩下的裸片段 `QWEN>>` 要么丢失用户请求、要么被当台词念）。
  - **令牌识别统一到 `src/omni/tokens.py`**（唯一事实来源）：`CALL_TOKEN_RE` 容忍空白/换行/大小写；`token_prefix_suffix_len()` 同样容忍空白的 holdback 判定；`sanitize_for_speech()` 硬安全网——含 `<`/`>` 或裸标记词（`QWEN`/`CALL_`）的片段一律不外发/不朗读。`client.py` 与 `voicebox_bridge.py` 都接（纵深防御）。
  - **释放点只能是真轮末**：`listen` / `session.closed` 立即释放 + 文本静默 `OMNI_HOLD_IDLE`（默认 1.5s）兜底（`_hold_flush_loop()` 协程）。**`response.done` 一律不释放**——full_duplex 下它是**每段一次**而非每轮一次，段边界会落在令牌中间（真机复现 `<<CALL_QW`）。命中令牌后**消费掉令牌及其之前的内容**，`_reset_escalation_state()` 只丢已外发前缀、**保留扣留中的尾巴**。
  - `OMNI_DEBUG=1` 会打印原始 text delta 的 `repr`，用于坐实畸形标记形态。回归见 `tests/test_omni_p0_flow_and_token.py`（含「段边界不得释放」「4 种畸形变体零泄漏」「裸标记碎片被拦住」）。**旧单测把整段令牌一次喂入，故最早抓不到这些 bug。**
- **P0-b 推流背压 + 音频水位 + 段事件去重（2026-09-13，二轮+三轮）**：服务端 full_duplex 是**串行**循环「读一条 `input.append` → prefill+decode 一步」，单段实测 0.69s（VPM 图像编码 190ms + decode p50 330ms/p90 759ms）。**三轮关键发现**：服务端**一段会发两个终局事件且共用同一 `response_id`**——「模型说话后切回聆听」的这段先发 `listen delta`（`ws_handler.cpp:1173`）再发 `response.done`（`:1232`）。若两个都算「本段完成」，一段被算两次 → 推流翻倍、出现 0.02s 级极小块 → 每轮仍要重做图像编码（**成本与块大小无关**）→ 消费速率腰斩 → 积压丢帧 5.1s（丢在句子中间 → 模型只听得到残句）。
  - `_signal_chunk_done(response_id)` **按 response_id 去重**；`_wait_chunk_slot()` 追加**最小推流间隔**（≥`push_interval`）；`_wait_min_chunk()` 确保**最小块时长** `OMNI_MIN_CHUNK_SECS`（默认 0.4s，小于此不值得单独占一轮服务端算力）；`_take_audio()` 设水位 `max_buf_secs`（默认 1.2s，超水位丢最旧留最新）。
  - **每帧强制带音频**（先等 ≤0.16s，仍空则补 0.1s 静音）——服务端 full_duplex 收到空音频会 `fail_fast("missing_audio")` 直接 `ws.close(1000)` 打死整个会话。
  - 开关：`OMNI_FLOW_CONTROL=0`、`OMNI_MAX_BUF_SECS`、`OMNI_CHUNK_WAIT`、`OMNI_MIN_CHUNK_SECS`、`OMNI_MIN_PUSH_INTERVAL`；WS 级集成回归见 `tests/test_omni_p0_backpressure_ws.py`（假服务端**照真服务端发两个同 id 终局事件**，实测节拍 0.60s、单段最小 0.40s、零丢弃）。
- **人声判据与回声门控判定（2026-09-13 三轮）**：护栏窗口 3.0s **小于端到端延迟**（音频积压 + 服务端一轮 + TTS 排队），且 RMS 阈值 0.02 **正压在人声段下沿**（实测底噪 RMS 0.002~0.013 / 人声 RMS 0.020~0.056 / 人声峰值 0.088~0.277，日志同秒内「检测到人声 0.023」→「进入静音 0.004」）→ **真实提问被误判成静音期幻觉**而拦截。现判据改「**峰值 ≥ `OMNI_SPEECH_PEAK_TH`(0.06) 或 RMS ≥ `OMNI_SPEECH_RMS_TH`(0.02)**」双条件、窗口 `OMNI_SPEECH_WINDOW`(6.0s)，拦截日志会打出「距上次人声 x.xs > 窗口 y.ys」。门控 auto 判定改为**同时看输入与输出**（输出是耳机且输入不是 → 关；**输入本身是耳机/蓝牙 → 开**，否则耳机麦会采到耳机自己的输出；其余保守开），启动日志打印两端口设备名；「自身播报窗口」的排除从门控开关**解耦**（默认 `OMNI_ECHO_GUARD=always` 无论门控都排除，`gate` 可退回旧行为）。
- **语音输入方案定性（2026-09-13）**：**不是「录完再发」，是 0.4s 粒度分块流式**——麦克风每 64ms 读入内存缓冲，推送协程每 0.4s 打包「音频 + 1 帧 JPEG」发 `input.append`。**离 GPT-Live 级尚差**：①无真 barge-in（回声门控播放期推等长静音＝放弃打断，需 WebRTC AEC 替代）；②omni 原生 token 级音频被 M7b 桥接丢弃；③分块 0.4s；④**full_duplex 下行无用户转写**，客户端无法校验模型是否听懂（补法：对同一 `_mic_buf` 并行跑本地 Whisper，仅用于显示 + 令牌任务相关性校验）。- **图像降频已落地（P1，2026-09-13）：默认 1 帧/秒**（`OMNI_VIDEO_INTERVAL`，<=0 退回每段带图；GUI 有「图像上行间隔s (OMNI)」框、CLI 有 `--video-interval`）。**图像上行与音频上行解耦**：音频照常按块上行，图像按间隔抽帧（首段必带图；计时用累加，长期均值准确落在 1/间隔）。**实测收益**（真机日志：VPM p50=196ms/均值 232ms、图像 64 视觉 token/帧、块节奏约 1.5 段/秒）：KV 增长 109→79 token/秒（降约三成）、VPM 负载 0.34→0.23 秒/秒（降约三分之一）、上下文寿命 69→95 秒（+约四成）；**并且它是 P2 的前置条件**——分块若降到 0.16s，不降频的话 VPM 会占掉每秒 1.14s 的算力，实时性直接崩。
- **P0 变量分离实验开关（2026-09-17 新增）**：在 `video_interval`（**多久**发一帧）之上补了「**发不发**」的总开关 `video_enabled`——GUI OMNI 面板「图像上行（OMNI 视觉）」复选框（默认勾选）/ CLI `--no-video` / 环境变量 `OMNI_VIDEO_ENABLED=0`。关闭后 `_should_attach_frame()` **恒返回 False**（优先级高于 `video_interval <= 0` 的「每段都带图」）、`_cam_loop` 也停发 jpeg（省掉每帧 JPEG 编码），**纯音频全双工**：服务端不再做 VPM 编码、不再往 KV 写视觉 token；BGR 帧照常刷新，**GUI 本地预览与「看着你」的观感不受影响**。
  - **为什么需要它**：2026-09-17 四组真机日志（listen 系数 1.0/0.8/0.6/0.4）诊断出「我说话要么没反应，要么被『识别』成查电脑状态」是**三个独立故障叠加**，其中「乱识别成查电池」**不是 ASR 结果**（full_duplex 协议不回传用户 ASR 原文），而是上下文被视觉 token 冲垮后模型照 `prompts.py` 示例复读——真机 `temp/omni_server.log` 实测 `n_past=6792`、`Duplex decode: n_keep=675`、每段 +73 token（视觉约 64）。**关掉图像若幻觉消失即可坐实该机制**，这是成本最低的变量分离实验。⚠️ **2026-09-28 结果：关图后（143 段零视频帧）幻觉依旧，该假设被证伪**——根因重定位为「MiniCPM-o 听不清（内建麦 RMS 仅 0.03~0.065）+ prompt 具体示例是复读诱饵」，`prompts.py` 已删示例改纯规则（任务描述用 boss 原话 / 听不清请重复 / 寒暄不吐令牌）。详见 codingLOG。
  - **配套改进**：把散在 5 处的 `os.environ.get("OMNI_DEBUG") == "1"` 统一收敛为 `self._debug`，并新增 GUI「上行调试日志（OMNI_DEBUG）」复选框（此前 GUI 启动的进程改不了环境变量，逐块 RMS/块长日志只能从命令行拿，而量化「块长抖动」正需要它）。`OmniClient` 参数为 `None` 时读环境变量，显式传参优先。
  - 回归见 `tests/test_omni_video_switch.py`（配置层 / 单元层 / **WS 级抓包断言「关掉后 9 段上行零 `video_frames` 且每帧仍带音频」**）。

> 注意：OMNI 模式默认只跑 omni 全双工 + 升级路由（qwen+tools 回灌），**不包含** main.py 的摄像头 YOLO 检测 / 唤醒词 / judge 主动判断；两者架构互斥，分别用于「全双工实时对话」与「传统被动多模态桌面原型」。所谓"识别成查电池"**不是 ASR 结果**（full_duplex 下行不回传用户转写），是 MiniCPM-o 音频理解质量限制 + prompt 具体示例造成的复读/幻觉（2026-09-28 A1 实验已排除「视觉 token 吃爆 KV」这一旧假设），属模型侧限制、代码无法根治，仅做可观测性缓解（增益框 + 实时回复区 + `prompts.py` 已删具体示例改纯规则）。

## 重要文件与目录

- `main.py`：多模态运行主入口。
- `src/capture/camera.py`：摄像头封装，Windows/macOS 感知。
- `src/analysis/detector.py`：YOLOv8 检测器封装。
- `src/audio/recorder.py`：PyAudio + WebRTC VAD 录音器。
- `src/audio/stt.py`：OpenAI Whisper 封装；`SpeechRecognizer` 强制 `language="zh"`（环境变量 `STT_LANGUAGE` 可覆盖），`_to_simplified()` 兜底把繁体残字统一为简体（优先 `opencc`，否则内置常用字映射）。
- `src/audio/tts.py`：跨平台系统 TTS 兜底封装。
- `src/audio/playback.py`：共享 WAV 播放工具（`afplay` / PowerShell / `aplay`），Voicebox 与系统 TTS 共用；**同时维护全局「正在出声」状态**（`is_playback_active()` / `seconds_since_playback_end()` / `mark_external_playback()`），供 OMNI 回声门控查询。
- `src/audio/voicebox_tts.py`：Voicebox 克隆 TTS（开源，REST API `http://127.0.0.1:17493`，macOS 友好主力 TTS），自动克隆 JAC 声纹 + 8 种情绪映射 + 系统 TTS 兜底。
- `src/audio/speaker_factory.py`：统一扬声器选择工厂 `build_speaker(config)`（Voicebox → Qwen3-TTS → 系统 TTS）。
- `src/audio/qwen_tts.py`：Qwen3-TTS 语音合成（开源本地 TTS，支持情绪/语气控制与声音克隆，仅 NVIDIA 平台启用），带系统 TTS 兜底降级。
- `src/brain/llm.py`：`LocalBrain`，llama.cpp / LM Studio / Ollama / auto 多后端，含 `think_with_image` 与 Function Calling 的 `think_with_tools` / `run_agentic`。
- `src/tools/`：**Function Calling 工具层（装手）**——`registry.py`（工具注册表 + OpenAI schema）、`executor.py`（安全分发执行）、`open_actions.py` / `search_files.py` / `system_info.py` / `shell.py`（四个白名单工具）。
- `src/judgment/judge.py`：主动判断引擎（`minicpm-v-4_5` via LM Studio 标准 chat API；与 omni 链路常驻的 MiniCPM-o-4_5 是两份不同实例，详见「当前开发状态」）。
- `src/utils/context.py`：线程安全的共享上下文（视觉摘要、状态标志、转录缓冲、帧缓存、介入标志）。
- `src/omni/`：**全双工 omni 接管模块**——`client.py`（OmniClient WebSocket 收发 + 令牌路由 + 推流背压）、`tokens.py`（升级令牌识别的唯一事实来源：容忍空白的变体匹配 / holdback 判定 / 硬安全网）、`voicebox_bridge.py`（M7b 句子级 Voicebox 桥接）、`backfeed.py`（M7a 回灌）、`router.py`（升级路由 EscalationRouter）、`server_launcher.py`（拉起 llama.cpp-omni）、`prompts.py`、`__main__.py`（CLI 入口 `python -m src.omni`）。
- `voices/`：TTS 声音克隆参考音。`silverwalf_voice.wav` 为唯一克隆音色参考（体积小、有意保留进版本库，见 `.gitignore` 注释）。
- `temp/`：运行时临时音频文件。
- `requirements.txt` / `requirements_fixed.txt`：依赖快照（`requirements.txt` 较新，`requirements_fixed.txt` 为旧稳定版）。
- `Modelfile`：Ollama 构建定义（jac-qwen3.5）。
- `docs/`：设计与方案文档——`minicpmo_master_plan.md`（MiniCPM-o 接入总规划）、**`webrtc_aec_plan.md`（2026-09-24 新增）**（原 `memory_test_plan.md` 已于 2026-10-01 并入 `CHANGELOG.md` 附 C，测试/契约真值一律以附 C 与 `src/memory/` 代码为准）：WebRTC AEC 接入方案——现状障碍（**所有发声都走 `afplay` 子进程，拿不到 far-end 参考 PCM**）/ 三条候选路线（`pywebrtc-audio` 自建播放｜macOS `AVAudioEngine` VoiceProcessingIO｜afplay+预解码对照）/ 四阶段实施路径 / 验收判据 / 「已核实 vs 待实测」界限表。**状态：方案待评审，未实施。**
- ~~`codingLOG.md`~~：**已于 2026-10-01 全量并入 `CHANGELOG.md` 附 A 并删除**（文件已不存在，不要再引向它）。
- `codinglog_by_awaqwq233/`：项目背景、预期架构、进度与研究文档——**只由 bo s s 手动维护，Agent 不得自动编辑**。
- `setup_ffmpeg.py`：从 imageio-ffmpeg 复制二进制为项目根 `ffmpeg`（macOS/Linux）或 `ffmpeg.exe`（Windows）。
- `verify_model.py`：校验 `llama_cpp` 兜底后端所需的本地 GGUF 模型（仅在使用 `llama_cpp` / `auto` backend 且本地有 GGUF 时需要）。
- **记忆子系统文档（已归口 `CHANGELOG.md`，原 `docs/memory/` 目录已删除）**：契约 `附 B`、测试计划与实现真值 `附 C`（含锁定的判定正则 / `kind` 枚举 / `reason` 受控词表 / 归档常量）、用户指南 `附 D`、隐私说明 `附 E`、运维 Runbook `附 F`。记忆数据文件默认在用户目录 `~/.jac/memory/`，不进仓库；**⚠️ 已知代码缺口（附 C §C9）：范围级清除 `clear(source=...)` / `clear(pii=...)` / `secure` 擦除未落地，`consent.json` 同意机制未实现**。
- `new_computer_download/`：到新机器的一键环境搭建脚本与详细安装指南（`READMEfirst.md` 为双语安装首页）。

通常不参与编辑的大体积/二进制产物：

- `.venv/` / `.cache/` / `__pycache__/`
- 模型二进制（`*.gguf`、`*.pt`、`*.bin`）——项目不再内置，由外部 AI 软件管理。
- `temp/` 下的运行时音频

## 模型与资产

所有**推理模型均不存放在项目内**，由外部 AI 软件管理：

- **大脑模型** `qwen/qwen3.6-35b-a3b`：在 **LM Studio** 中加载（默认 `backend="lm_studio"`，`127.0.0.1:12345`），原生多模态、`enable_thinking=False` 禁用思考。代码按模型标识符精确匹配，不依赖任何本地 GGUF 文件。
- **主动判断引擎模型** `minicpm-v-4_5`（代码默认 `config.judgment_model_name`，`src/judgment/judge.py` 用标准 chat API 调 LM Studio 的 `/v1/chat/completions`，**不是 MiniCPM-o**；OMNI 全双工链路里常驻的那份是 `MiniCPM-o-4_5` GGUF Q8_0，跑在 llama.cpp-omni `:9060`，两者是不同实例、不同用法）。判断引擎未加载该模型时自动进入被动模式（不报错也不主动）。
- **TTS 声纹**：由 **Voicebox** App 托管，克隆 `voices/silverwalf_voice.wav` 得到名为 **JAC** 的声纹。项目内不再存放 TTS 权重。
- **物体检测** `yolov8n.pt`：首次运行由 `ultralytics` 自动下载到缓存，不进仓库。

> 旧版 `models/` 目录（GGUF / Qwen3-TTS 权重）已移除：项目不再内置任何大模型权重，所有模型走 LM Studio / Voicebox 等外部软件。`build.py` / `fix_install.py` / `download_models.py` / `DEPLOY_GUIDE.txt` 等旧 Windows/模型下载脚本已删除。

当前 STT：Whisper，`model_size="tiny"`，非流式；强制简体中文输出并兜底繁→简归一化（避免识别成繁体/乱码）。

当前 TTS：默认走 `build_speaker` 工厂，选择链 **Voicebox（开源克隆引擎，REST API）→ Qwen3-TTS（仅 NVIDIA）→ 系统 TTS 兜底**。

- **Voicebox（macOS 主力）**：`src/audio/voicebox_tts.py`，调开源 Voicebox App 的 `http://127.0.0.1:17493` REST API；自动建/复用名为 **JAC** 的克隆声纹（用 `voices/silverwalf_voice.wav`），支持中文 + 声音克隆；8 种情绪映射成 Chatterbox Turbo 副语言标签（`[laugh]/[sigh]/[gasp]/[excited]/[whisper]`）+ instruct；服务未启动自动回退系统 `say -v Tingting`。
- **Qwen3-TTS（仅 NVIDIA）**：`src/audio/qwen_tts.py`，开源本地 TTS，支持情绪/语气自然语言控制与 3 秒声音克隆。macOS 无 NVIDIA 卡，已默认禁用（见 CHANGELOG 2026-08-05）；可用 `QWEN_TTS_FORCE=1` 强开。默认克隆模式（`clone`）使用 `voices/silverwalf_voice.wav`，参考文本见 `qwen_tts.py` 的 `DEFAULT_REF_TEXT`；可用环境变量 `QWEN_TTS_REF` / `QWEN_TTS_REF_TEXT` 临时覆盖。
- 配置项（`src/utils/config.py`，均可用环境变量覆盖）：`use_voicebox_tts` / `voicebox_url` / `voicebox_engine`(默认留空，由 JAC 声纹绑定的模型决定；设 `VOICEBOX_ENGINE` 可覆盖) / `voicebox_profile_name`(JAC) / `voicebox_ref_wav` / `voicebox_ref_text` / `voicebox_language`(zh) / `voicebox_fallback_voice`(Tingting)。

## 设置与运行

完整安装与配置见 **`new_computer_download/READMEfirst.md`**（双语：英文官方方法 + 中文含国内镜像方法）。推荐 Python 3.10 / 3.11。

快速开始：

```bash
# 1. 创建并激活虚拟环境（推荐）
python3 -m venv .venv && source .venv/bin/activate        # macOS/Linux
# Windows:  .venv\Scripts\activate

# 2. 安装依赖
pip install -r requirements.txt
# 国内网络可加清华镜像： -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn

# 3. 确保 FFmpeg 可用（项目根或系统 PATH）
python setup_ffmpeg.py     # 缺失时从 imageio-ffmpeg 复制

# 4. 启动外部 AI 软件并加载模型
#    - LM Studio：加载大脑模型 qwen/qwen3.6-35b-a3b（标识符必须精确匹配），开启本地服务器 127.0.0.1:12345
#    - Voicebox App：克隆 voices/silverwalf_voice.wav 为 JAC 声纹（macOS 主力 TTS）

# 5. 运行原型
python main.py
```

### 运行前置条件

- **默认 `backend="lm_studio"`**，因此运行前需启动 **LM Studio** 并在 `127.0.0.1:12345` 加载 `qwen/qwen3.6-35b-a3b`（如需主动判断，另加载 **`minicpm-v-4_5`**）。否则所有思考请求会连接失败。
- 若想纯本地 GGUF 推理，需把 `main.py` 中 `LocalBrain(..., backend="lm_studio")` 改为 `"llama_cpp"` 或 `"auto"`（`auto` 会探测可用后端），并确保对应 GGUF 由外部放置（项目不再内置）。
- Ollama 用法：用附带的 `Modelfile` 构建 `jac-qwen3.5`，再把 backend 改为 `"ollama"`。

所需本地硬件/运行时条件：

- 可用的摄像头、可用的麦克风。
- 项目根或系统 PATH 中的 FFmpeg。
- 运行中的 **LM Studio**（默认）或本地 GGUF 模型（改 backend 后）。
- **Voicebox App**（macOS 主力 TTS）：从官方渠道安装，克隆 J.A.C. 声纹即可，项目不再内置 TTS 权重、也无需 `download_models.py`。

## 当前进度（来自日志）

项目于 2026-06-23 高考后重启。方向（来自 `codinglog_by_awaqwq233/当前进度.docx`）：

- 继续用 vibe coding 推进。
- 把纸质架构图数字化。
- 用更新的工具/模型重新审视架构：OpenClaw、小米 MiLoco 2.0、能在 48GB MacBook 级机器运行的新 Qwen 系列。
- 重做语音模型路径：TTS 后端已从 Genie-TTS（GPT-SoVITS/ONNX）全面切换为 **Voicebox（开源本地克隆引擎，macOS 主力；Qwen3-TTS 仅 NVIDIA 兜底）**，克隆参考音固定为 `voices/silverwalf_voice.wav`，旧 Genie 代码与资产已删除。
- 研究小判断模型能否在流式输入时持续思考。
- 搭建 GitHub 提交/开源工具链。
- 在稳定服务器可用后探索服务器连接模块。

**代码中已落地的进展（相对旧文档）：**

- 大脑从 Qwen1.5-1.8B 升级为 `qwen/qwen3.6-35b-a3b`（经 LM Studio 加载），并抽象出多后端 `LocalBrain`。
- 新增 `src/judgment` 主动判断引擎雏形（`minicpm-v-4_5` via LM Studio 标准 chat API，每 4s 判断是否主动介入）——对应愿景里的「核心判断 / 持续感知」。
- 新增多模态图像问答 `think_with_image()`（视觉问题时发送真实摄像头帧）。
- 新增 `SLEEP`/`AWAKE` 状态机 + 20s 超时自动休眠。
- 新增控制台文本输入实时对话（绕过唤醒词）。
- TTS 后端从 Genie-TTS 全面切换为 **Voicebox（克隆 J.A.C. 声纹）+ Qwen3-TTS 仅 NVIDIA 兜底**。

`CHANGELOG.md` 附 A 列出的与最终目标的差距中，**以下仍为未实现 / 待做项**：MCP / OpenClaw 集成、实时联网工具（天气/日程）、token 级流式 TTS（omni 全双工已落地 LLM 流式输出 + M7b 句子级 Voicebox 桥接近似实时，但非 token 级）、OCR / 人脸识别 / 深度等视觉理解。注意附 A 部分内容早于 `main.py`，应作为架构差距笔记而非精确实现状态。

> **已落地（曾列于未实现项，现已实现并集成）**：持久记忆（JSON 长期记忆 + 轻量本地向量检索，见 `src/memory/` 与 `CHANGELOG.md` 附 B/附 C）。附 A 中「记忆功能待验证」指端到端未在真机跑过，并非代码空缺。
> **记忆子系统已知代码缺口（2026-10-01 核对，详见 `CHANGELOG.md` 附 C §C9）**：①范围级清除 API（`clear(source=...)` / `clear(pii=...)` / `secure` 擦除）未落地 → 「一键清空 inferred 保留 explicit」与清除权暂不可兑现；②`consent.json` 可见同意机制未实现；③归档常量与契约不一致（代码 `MAX_ARCHIVE_FILES=12` / `ARCHIVE_RETENTION_DAYS=365`）。

## 预期未来架构

规划文档描述了一个由「J.A.C. Brain」驱动的系统：

- 输入层：设备信号、实时音频、视觉帧。
- 感知/预处理：语音转写、CNN/视觉分析，把解析结果缓冲进记忆。
- 核心判断：一个「多模态小判断模型」或判断模型集群，持续决定 J.A.C. 是否应介入。
- 调节/安全模块：校验判断是否正确，拦截不应静默执行的操作，误报时回到判断循环。
- J.A.C. Brain：更大的推理模型（可能 Qwen 系或改进版小米 MiLoco 2.0），负责复杂分析与任务规划。
- Agent 执行：内部技能与外部 API，可能通过 OpenClaw/MCP 类集成。
- 外部模型 API：Gemini、ChatGPT、Grok、Claude、Qwen、DeepSeek 等。
- 输出层：App/HUD 结果展示 + 语音 TTS（纯文本中性朗读）。
- 闭环：输出反馈到下一轮判断，形成持续主动服务。

硬件预期（来自文档，可穿戴终端仅为外设）：

- 主机：未来的 MacBook Pro 14" M5 Pro 级，48GB+ 统一内存，1TB SSD。
- 可能外设：小米 AI 眼镜、Apple Vision Pro，或便携相机/MR 设备。
- 便携供电：背包内高功率充电宝。
- 服务器：LAN/公网服务器承担更重模型，概念目标约双 22GB RTX 2080 Ti + 128GB RAM。

## 工程指导（未来工作）

- 坚持本地优先设计。尽量把唤醒词检测、VAD、基础感知、紧急交互留在本地。
- 优先简单规则，其次小模型，最后大模型——尤其用于介入判断与延迟敏感路径。
- 避免让大模型决定每个底层路由选择；用任务路由表与显式策略，除非确实需要模型判断。
- 保持摄像头/音频采集与模型推理通过清晰的 context/state 对象松耦合。`SharedContext` 是该模式的种子。
- 注意 `main.py` 的线程状态：`context.is_speaking`、`context.is_listening`、`context.is_thinking`、`conversation_running` 用于避免反馈循环与重叠交互。
- **新增后端（云端/外部 API）应在 `LocalBrain` 内扩展**，而非绕过它直接发请求，以保持统一的多模态接口与 mock 兜底。
- 把 `temp/` 音频当作可丢弃的运行时产物。
- 不要提交大体积模型/音频/打包产物，除非项目明确要跟踪二进制资产。`voices/` 下的参考音（J.A.C. 音色，体积小）有意保留、不忽略，迁移或克隆后按需 `git add voices/` 提交；模型权重一律走外部 AI 软件、不进仓库。
- 谨慎对待隐私与安全。愿景明确要求「主动常开感知」，未来实现必须包含可见的同意、本地过滤、日志控制，以及在录音/识别人物/向云 API 发送数据前的清晰边界。
- 任何新的 agent/工具执行功能，对高风险操作必须显式白名单与确认。当前助手能说、能看；执行系统动作是重大信任边界。
- 延迟优化优先做流式与流水线：流式 ASR、增量推理、流式/提前 TTS。
- 记忆从结构化 JSON 摘要起步，再考虑向量数据库。
- 更换 TTS 时保持 `speak(text, emotion_hint)` 统一接口与系统 TTS 兜底不变；当前已实现为 **Voicebox（克隆 `voices/silverwalf_voice.wav` 声纹）+ 系统 TTS 兜底**。

## 已知限制

- **运行强依赖 LM Studio**：`main.py` 默认 `backend="lm_studio"`，必须本地 12345 端口加载 `qwen/qwen3.6-35b-a3b`；否则思考全部失败。纯本地 GGUF 需改 backend。
- **双模型资源**：开启主动判断需 LM Studio 同时加载 `qwen/qwen3.6-35b-a3b` + `minicpm-v-4_5`；当前 M5 Pro 48G 统一内存已验证可同时承载（2026-08-11）。默认 `JUDGMENT_ENGINE_ENABLED=True`，未检测到该模型时自动进入被动模式（不报错也不主动）。**OMNI 全双工模式则另需 llama.cpp-omni 的 MiniCPM-o-4_5（Q8_0）起在 `:9060`**，与 LM Studio 那份判断模型无关（OMNI 模式下判断引擎根本不启动）。
- VAD 录音仍可能阻塞在「等待说话」，影响关闭响应（旧限制仍在）。
- **延迟**：STT 仍为非流式的 Whisper tiny（整段说完才识别）；但 LLM 经 omni 全双工已**流式输出**文本、TTS 经 M7b **句子级 Voicebox 桥接**近似实时 + JAC 克隆声纹（omni 自带 TTS 音频在桥接启用时丢弃），端到端感知延迟已显著下降（全双工边听边说）。token 级流式 TTS 仍待做。
- 无 MCP/OpenClaw 集成（目标未实现）；Function Calling（装手）+ agent 执行框架、持久记忆（JSON 长期记忆 + 轻量向量检索）已实现，见 `src/tools/`、`src/brain/llm.py`、`src/memory/`。
- `Qwen3.6-35B` 大模型（`qwen/qwen3.6-35b-a3b`）**现已接入为默认大脑**（经 LM Studio 按标识符加载）；本地不再内置 GGUF 备份，运行完全依赖 LM Studio 加载的模型。
- `requirements.txt` 已装 `fastapi`/`uvicorn`/`websockets` 等 web 栈，但 `src/` 下无对应 server 代码——属依赖传递或预留骨架，勿误读为「已有 API 服务」。
- 当前项目树**已有自动化测试**：`tests/unit/test_tools.py`（Function Calling 工具层单测）、`tests/test_omni_m2.py`（M2 升级路由单测）、`tests/test_omni_p0_flow_and_token.py`（P0-a 令牌分片零泄漏 + P0-b 背压/水位单测）、`tests/test_omni_p0_backpressure_ws.py`（P0-b WS 级集成：假服务端压测真实客户端节拍）、`tests/test_*.py` 系列（记忆 / 语音 / GUI 运行期等），可用 `pytest` 运行（见 `pytest.ini`）。
