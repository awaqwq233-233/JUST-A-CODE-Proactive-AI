# J.A.C. × MiniCPM-o-4_5 全双工集成 — 总计划（Master Plan）

> 版本：v1.0（2026-08-11，基于多次调研与决策收敛）
> 状态：待 bo s s 评审 → 同意后进入实现阶段
> 范围：把现有 `Voicebox TTS` + `minicpm-v-4_5` 判断模型，整体替换为 **MiniCPM-o-4_5**（llama.cpp-omni / Mac Metal）作为常驻全双工前端大脑，并接入原 PySide6 GUI。

---

## 0. 一句话目标

电脑麦克风 + 摄像头**实时常开、全双工**喂给 **MiniCPM-o-4_5**（独立运行的 llama.cpp-omni 服务），它端到端**同时**处理连续的视频流与音频流，**并发**产出文本流与语音流（互不阻塞），负责与用户自然对话（同时"看 / 听 / 说"）；当它判断用户需要工具或复杂推理时，**自动委托** LM Studio 里的 `qwen/qwen3.6-35b-a3b` 用 tools 尝试解决，结果回传软件 UI 供用户查看。原 `gui.py` 改造为强交互主界面。Voicebox 退居兜底 TTS。

---

## 1. 已锁定决策（D1–D7）

| 编号 | 决策 | 说明 |
|------|------|------|
| **D1** | MiniCPM-o-4_5 作独立程序 | 用 **llama.cpp-omni**（`tc-mb/llama.cpp-omni` 的 Mac Metal 分支）编译出 `llama-server`，J.A.C. 经 **9060 HTTP API** 接入。Python 本体不加载大模型，无重型 ML 依赖、无版本冲突。 |
| **D2** | 手动启动所有服务 | 每次：① 手动起 llama-server(9060) → ② 手动起 LM Studio(12345, 35B) → ③ 启动 J.A.C. GUI(`python main.py`) → 界面点「启动」连服务。 |
| **D3** | 全双工常驻 | 麦克风 + 摄像头实时常开，可随时打断插话（barge-in）。不降级为半双工。 |
| **D4** | 音视频由 J.A.C. 采集并流式喂 API（Fork A） | 无头 `llama-server` **不能自己抓 Mac 摄像头/麦**；J.A.C. 用 OpenCV+PyAudio 采集，建立到 server 的**持久直播流**。J.A.C. 必须在环内，才能自动委托 35B。 |
| **D5** | TTS：MiniCPM-o 原生 TTS 替换 Voicebox | MiniCPM-o 用 CosyVoice2 克隆 JAC 声纹出声（参考 `voices/silverwalf_voice.wav`）；Voicebox/系统 TTS 退居兜底。 |
| **D6** | GUI 保留 PySide6 并改造 | `gui.py` 继续作为主界面，改造为强交互表面（对话气泡、状态灯、静音键、委托/结果面板）。 |
| **D7** | 委托策略：J.A.C. 路由 | MiniCPM-o 文本意图判定 → 转 35B `run_agentic`（带 tools）；`openclaw` 未来作为新增工具接入。 |

> 关于模型名：端侧全双工 Omni 模型是 **MiniCPM-o-4_5**（9B，Qwen3-8B + SigLip2 + Whisper-medium + CosyVoice2）。旧的 `minicpm-v-4_5` 是纯视觉版，仅作历史判断引擎，本次被整体取代。

---

## 2. 总体架构（三层 + 数据流）

```
┌──────────────────────────────────────────────────────────────────────┐
│  用户环境（物理）                                                        │
│   🎤 麦克风(常开)      📷 摄像头(常开, OpenCV)                          │
└───────────┬───────────────────────────┬──────────────────────────────┘
            │  PyAudio 音频流            │  OpenCV 帧流
            ▼                            ▼
┌──────────────────────────────────────────────────────────────────────┐
│  J.A.C. 本体 (Python, main.py + gui.py)  ← 你在环内、强交互主界面       │
│                                                                         │
│  ┌────────────┐   ┌──────────────┐   ┌────────────┐  ┌─────────────┐  │
│  │ mic_capture│   │cam_capture   │   │ orchestrator│  │  delegate    │  │
│  │  (thread)  │   │  (thread)    │   │ (全双工编排)│  │ (委托 35B)   │  │
│  └─────┬──────┘   └──────┬───────┘   └─────┬──────┘  └──────┬──────┘  │
│        │  audio queue    │  frame queue    │                │         │
│        └────────┬────────┴─────────────────┘                │         │
│                 ▼                                            │         │
│        ┌──────────────────┐  HTTP 9060 (流式)   ┌───────────▼──────┐  │
│        │  server_client   │ ───────────────────▶│  llama.cpp-omni  │  │
│        │ (9060 API 适配)  │ ◀───────────────────│  llama-server    │  │
│        └──────────────────┘   TTS WAV + text     │  MiniCPM-o-4_5   │  │
│                 │                                 │  (全双工看听说) │  │
│                 │ text/audio queue               └───────────────────┘  │
│                 ▼                                                          │
│        ┌──────────────┐  工具结果回传       ┌──────────────────────────┐ │
│        │  Speaker(播) │ ◀──────┐            │ LM Studio :12345          │ │
│        │  + GUI 气泡  │        └───────────▶│ qwen/qwen3.6-35b-a3b     │ │
│        └──────────────┘   run_agentic+tools │ (复杂推理 / 工具调用)    │ │
│                 │                            └──────────────────────────┘ │
│                 ▼                                                          │
│        ┌──────────────┐                                                   │
│        │  PySide6 GUI │ 对话气泡 / 状态灯 / 静音 / 委托&结果面板          │
│        └──────────────┘                                                   │
└──────────────────────────────────────────────────────────────────────┘
```

**关键事实**：`llama-server` 是**无头推理引擎**，不会自己去开 Mac 摄像头/麦克风；"模型直接接入电脑音视频"只存在于浏览器 WebRTC 模式（那会让 J.A.C. 退出对话环、无法自动委托 35B）。因此采用 **Fork A**：J.A.C. 采集并建持久直播流喂 API，模型做全部理解/发声处理——这正是"把音视频给 MiniCPM-o 处理"的实现方式，搬运字节这一步 unavoidable。

---

## 3. 并发与非阻塞流模型（核心：各流互不阻塞）

全双工体验的本质是**多流并行、互不阻塞**。采用「**独立线程 + 队列**」模型，每条流一个生产者/消费者，互不等待：

| 流 | 生产者线程 | 消费者线程 | 载体 |
|----|-----------|-----------|------|
| 麦克风采集 | `MicCaptureThread` | 推入 `audio_q` | PyAudio 16k mono int16 |
| 摄像头采集 | `CamCaptureThread` | 推入 `frame_q`（抽帧，如 5–10fps） | OpenCV BGR |
| 上行喂流 | `UploadThread` | 从 `audio_q`/`frame_q` 取 → POST 9060 chunk | HTTP 流式 |
| 下行接收 | `RecvThread` | 解析 SSE → 拆分 `text_q` + `audio_q_out` | TTS WAV(24k) + text |
| 语音播放 | `PlaybackThread` | 从 `audio_q_out` 取 → PyAudio 输出流 | 24k float |
| 文本/状态 | `GuiUpdateThread` | 从 `text_q`/`state_q` 取 → 更新 UI | 信号槽 |
| 委托执行 | `DelegateThread` | 触发时调 35B `run_agentic` | 工具调用 |

**阻塞隔离原则**：
- 采集线程**只采集**，不等待网络；
- 上行/下行走独立连接，下行回包（TTS 音频）到达立即进播放队列，**不等文本**；
- 播放线程常驻，与"听/看"完全解耦 → 实现"边说边听边看"；
- 任一线程异常被 `try/except` 包裹并转状态事件，不拖垮主循环；
- 全双工打断（barge-in）：检测到用户新语音（VAD）→ 置 `stop_response` → 上行线程暂停喂旧段、通知 server 中断当前生成 → `PlaybackThread` 停止当前播放。

---

## 4. 文件清单

### 新建（`src/minicpmo/` 包）
1. `server_client.py` — 9060 HTTP API 适配层（init / 喂音频块 / 喂视频块 / 收 TTS+text / 中断）。**端点名与报文在 Phase 0 实测固化**。
2. `orchestrator.py` — 全双工编排器：起停各线程、管理队列、`stop_response` 打断、连接生命周期。
3. `delegate.py` — 委托 35B：意图判定 → `LocalBrain.run_agentic` → 结果回传 MiniCPM-o + UI；预留 `openclaw` 接入点。
4. `speaker.py` — `MiniCPMSpeaker`：实现 `speak()` 走 MiniCPM-o TTS（用于会话外系统播报）；Voicebox 兜底。
5. `streams.py` — 各类队列与线程原语（可选，或并入 orchestrator）。
6. `prompts.py` — MiniCPM-o 系统提示词（含"何时请求委托"的协议标记）。
7. `config_ext.py` — 本模块配置默认值常量（也可并入 `config.py`）。
8. `gui_panel.py` — GUI 全双工面板控件（气泡区/状态灯/静音键/委托结果面板）。
9. `diagnostics.py` — 启动自检（server 可达？麦克风权限？摄像头可达？RTF 粗测）。

### 修改
10. `src/utils/config.py` — 新增 MiniCPM-o 相关字段（见 §6）。
11. `src/utils/context.py` — `SharedContext` 新增 omni 状态/气泡/静音/委托状态字段。
12. `src/audio/speaker_factory.py` — 新增 `use_minicpm_tts` 分支 / `MiniCPMSpeaker`。
13. `gui.py` — 接入 `gui_panel`，选项面板新增「MiniCPM-o 全双工」勾选框与加载状态；停止顺序保护。
14. `src/runtime.py` — `JACRuntime` 桥接新开关到 `main` 全局。
15. `main.py` — 新增 MiniCPM-o 全双工启动路径（与旧 headless/cv2 路径互斥）；关闭旧 `judge.py` 主动判断 + 取代 Whisper 唤醒词路径。
16. `src/judgment/judge.py` — 全双工开启时停用（MiniCPM-o 即常驻感知+主动）。
17. `requirements.txt` + `new_computer_download/`（仅新增**编译 llama.cpp-omni 的一键脚本/指南**，非 Python 依赖）。
18. 文档同步：`README.md` / `AGENTS.md` / `CHANGELOG.md` / `codingLOG.md`。

---

## 5. 模块设计

### 5.1 server_client（9060 API 适配层）
- `OmniClient(base_url="http://127.0.0.1:9060")`：
  - `init_session(duplex=True, media_type=2(视+音), use_tts=True, voice_ref_wav, vision_backend="coreml")` → 开 duplex 会话。
  - `send_audio_chunk(np_chunk)` / `send_video_frame(pil_or_bgr)` → 持续上行。
  - `stream_responses()` → 生成器，逐包 yield `{"text":..., "audio_wav":..., "sampling_rate":24000}`。
  - `interrupt()` → 置 `stop_response`，打断当前生成。
- **端点与报文格式在 Phase 0 用官方集成指南 + 实测固化**（已知有 `/v1/stream/omni_init`；具体 chunk 端点名待确认）。该层把"协议细节"封死，上层只调语义方法。

### 5.2 orchestrator（全双工编排器）
- `OmniOrchestrator(config, shared_ctx, delegate)`：
  - `start()`：自检 → 连 server → 起 Mic/Cam/Upload/Recv/Playback/GuiUpdate 线程 → 置状态 `listening`。
  - `stop()`：先停音频播放与上行 → 中断 server → Join 所有线程 → 释放资源（复用现有 `_safe_stop_runtime` 模式）。
  - 维护 `stop_response` 与 `mic_muted` 标志；VAD 检测新语音触发打断。
  - 把下行 text/audio 分别推 `text_q` / `audio_q_out`，把状态推 `state_q`。

### 5.3 delegate（委托 35B）
- `DelegateRouter(brain: LocalBrain, tools, shared_ctx)`：
  - **触发判定**（可配置，默认 hybrid）：
    - (a) **协议标记**：MiniCPM-o 被 `prompts.py` 引导，在需要工具/复杂帮助时输出 `<DELEGATE>用户原意</DELEGATE>`；J.A.C. 解析。
    - (b) **关键词兜底**：如「打开/搜索/运行命令/查文件/现在几点/电池/系统信息」等直接路由（复用现有工具意图词表）。
  - `run(user_request)`：
    1. 置 GUI 状态「委托 35B 中」；
    2. `brain.run_agentic(prompt=user_request, tools=get_tool_schemas(), tool_executor=execute_tool)` → 流式拿 35B 最终回答（35B 可多轮调 tools：`open_url`/`open_app`/`search_files`/`get_system_info`/`run_command`）；
    3. 结果 → 作为一条 user 消息回喂 MiniCPM-o（让它自然发声总结）+ 推 `text_q` 到 UI 结果面板；
    4. **openclaw 未来**：在 `src/tools/` 新增 `openclaw.py` 工具，注册进 `get_tool_schemas()`，delegate 无需改动即可生效。
- 复用现有 `src/tools/registry.py` + `executor.py`（与唤醒词完全解耦，天然可复用）。

### 5.4 speaker（MiniCPM-o TTS 适配器）
- `MiniCPMSpeaker(server_client)` 实现 `speak(text)`：把文本作为一次性 TTS 请求发给 MiniCPM-o，接收 WAV 播放。用于**会话外系统播报**（如启动提示）。
- `build_speaker` 新增分支：`use_minicpm_tts=True` → `MiniCPMSpeaker`；server 不可达时降级 Voicebox/system TTS。

### 5.5 SharedContext 扩展
新增字段/方法：`omni_state`(idle/listening/thinking/speaking/delegating)、`mic_active`、`mic_muted`、`assistant_text`、`user_text`、`delegate_status`、`model_loading_progress`、`last_error`。GUI 通过信号槽订阅。

### 5.6 Config 扩展（§6）
### 5.7 GUI 改造（§7）
### 5.8 main.py 启动编排
- 全双工模式（`minicpmo_enabled=True`）下：不启动旧 `audio_thread_func`(Whisper 唤醒) 与 `judge.py` 主动判断；改由 `OmniOrchestrator` 接管音视频与对话；视觉主循环/YOLO 保留（YOLO 仅用于 GUI 标注/状态，理解交给 MiniCPM-o）；35B 大脑仅在被委托时调用。

---

## 6. Config 新增字段（`src/utils/config.py`）

| 字段 | 默认值 | 环境变量 | 说明 |
|------|--------|----------|------|
| `minicpmo_enabled` | `False` | `MINICPMO_ENABLED` | 是否启用 MiniCPM-o 全双工主交互 |
| `minicpmo_base_url` | `http://127.0.0.1:9060` | `MINICPMO_BASE_URL` | llama-server 地址 |
| `minicpmo_autolaunch` | `False` | `MINICPMO_AUTOLAUNCH` | **手动启动**（D2），默认不自动拉起 |
| `minicpmo_vision_backend` | `coreml` | `MINICPMO_VISION_BACKEND` | 视觉后端丢 ANE，释放 GPU |
| `minicpmo_use_tts` | `True` | `MINICPMO_USE_TTS` | 用 MiniCPM-o 原生 TTS |
| `minicpmo_voice_ref` | `voices/silverwalf_voice.wav` | `MINICPMO_VOICE_REF` | 声纹克隆参考音 |
| `minicpmo_full_duplex` | `True` | `MINICPMO_FULL_DUPLEX` | 常驻全双工（D3） |
| `minicpmo_delegate_enabled` | `True` | `MINICPMO_DELEGATE_ENABLED` | 启用委托 35B |
| `minicpmo_delegate_mode` | `hybrid` | `MINICPMO_DELEGATE_MODE` | `hybrid`/`keyword`/`marker` |
| `minicpmo_sample_rate` | `16000` | — | 上行音频采样率 |
| `minicpmo_tts_rate` | `24000` | — | TTS 输出采样率 |
| `minicpmo_cam_fps` | `8` | — | 抽帧上行的摄像头 fps |
| `lm_studio_url` | `http://127.0.0.1:12345` | 已有 | 委托目标（35B） |

`load()` 同步映射环境变量；`main.py` 模块级常量与 `runtime.py` bridge 同步新增。

---

## 7. GUI 改造（`gui.py` + `gui_panel.py`）

保留 PySide6 形态，新增/改造：
- **全双工会话面板**：实时对话气泡区（用户/助手分色）、`omni_state` 状态灯（听/想/说/委托中）、麦克风激活灯、说话指示。
- **静音键**：置 `mic_muted`（暂停上行音频，保留看）。
- **委托 & 结果面板**：显示「正在委托 35B」「调用的工具」「最终结果」（如打开了某网页、查到某文件）。
- **选项面板**：新增「MiniCPM-o 全双工」勾选框 + 模型加载/连接状态；与现有「工具功能 / 主动模型 / TTS」并列。
- **启动/停止顺序**：点「启动」→ `OmniOrchestrator.start()`；点「停止」→ 先停播放/上行 → 中断 server → Join 线程 → 释放 Qt 资源（复用 `_safe_stop_runtime`）。
- GUI **不参与音频播放**（由 orchestrator 的 PyAudio 输出流负责），只订阅 `SharedContext` 状态/文本。

---

## 8. 委托机制详解（D7）

```
用户说话 ──▶ MiniCPM-o(听+看+理解)
                │
                ├─ 能直接答 ──▶ 直接 TTS 发声 + UI 气泡
                │
                └─ 需要工具/复杂 ──▶ 输出 <DELEGATE>...</DELEGATE>（或被关键词命中）
                                          │
                                          ▼
                                   DelegateRouter.run()
                                          │
                                          ▼
                          35B run_agentic(prompt, tools, execute_tool)
                          （可多轮调 open_url/open_app/search_files/
                           get_system_info/run_command [+openclaw 未来]）
                                          │
                                          ▼
                                   最终自然语言答案
                                          │
                              ┌───────────┴───────────┐
                              ▼                       ▼
                     回喂 MiniCPM-o(让它发声总结)   UI 结果面板展示
```

- MiniCPM-o **不要求自己会 function calling**（规避 4_5/llama.cpp-omni 可靠性风险）；"是否委托"由 J.A.C. 判定。
- `openclaw`：未来在 `src/tools/openclaw.py` 实现并注册，delegate 自动可用。

---

## 9. 启动顺序（手动运维，D2）

1. 启动 **llama.cpp-omni** `llama-server`（Metal，9060，加载 MiniCPM-o-4_5 GGUF 全家桶 + 声纹）。
2. 启动 **LM Studio**（12345，加载 `qwen/qwen3.6-35b-a3b`）。
3. 启动 **J.A.C. GUI**：`python main.py` → 界面勾选「MiniCPM-o 全双工」→ 点「启动」→ J.A.C. 连两服务、开麦开摄、进入全双工。
4. 验证：UI 状态灯 `listening`；说一句话 → MiniCPM-o 回应并出声；说「帮我打开 XX 网页」→ 委托 35B → UI 结果面板显示。

> 编译与权重下载写进 `new_computer_download/`（一键脚本 + 指南）：`cmake` 编译 `tc-mb/llama.cpp-omni`（Metal）、`modelscope` 镜像拉 GGUF 全家桶（~8.3GB）、声纹参考音配置。

---

## 10. 阶段化实施（Phase 0–6）

| 阶段 | 内容 | 验收标准 |
|------|------|----------|
| **0 Spike** | 编译 llama.cpp-omni；固化 9060 喂流/收 TTS 端点与报文；粗测 M5 Pro 48GB 全双工 RTF | server 起得来；能单向喂一段音+一帧图拿到 TTS WAV；RTF 记录 |
| **1 接入** | `server_client` + `OmniClient`；单轮（非双工）音视频→文本+语音跑通 | 一句输入→MiniCPM-o 出声+文本 |
| **2 全双工** | `orchestrator` 起各线程；常驻上行 + 下行播放；`stop_response` 打断 | 可随时插话打断；听/说不互锁 |
| **3 视频** | 摄像头抽帧持续喂；CoreML 视觉后端 | MiniCPM-o 能描述画面内容 |
| **4 委托** | `delegate` + 35B `run_agentic` + tools；marker/keyword 路由；结果回 UI | 说"打开网页/搜文件"→ 35B 调工具→ UI 显示结果 |
| **5 GUI** | `gui_panel` + 选项框 + 状态灯 + 静音 + 结果面板；启动/停止顺序 | GUI 强交互可用，音频不自管 |
| **6 文档** | README/AGENTS/CHANGELOG/codingLOG 同步；`new_computer_download` 脚本 | 文档与代码一致 |

> 每个阶段结束做内存/RTF 快照；Phase 2 起监控统一内存占用（MiniCPM-o ~10GB + 35B 视 LM Studio 量化）。

---

## 11. 风险与回退（R1–R8）

| 风险 | 影响 | 回退/对策 |
|------|------|-----------|
| **R1** M5 Pro 全双工 RTF>1（未官方验证） | 说话延迟/卡顿 | 默认仍全双工（D3 不降级）；若实测不可接受，临时降 `cam_fps`/关 CoreML 以外的加速；最终可限为"说时暂停听"半双工（不影响计划结构） |
| **R2** 9060 端点/报文与文档不符 | Phase 0 阻塞 | `server_client` 适配层隔离，Phase 0 实测固化 |
| **R3** 统一内存吃紧（48GB 扛 MiniCPM-o+35B） | OOM | 关 YOLO/GUI 标注；35B 用更低量化；监控告警 |
| **R4** 麦克风权限 / 摄像头占用 | 采集失败 | `diagnostics.py` 启动自检 + UI 明确报错 |
| **R5** MiniCPM-o 偶发不触发委托 | 该委托没委托 | marker + keyword 双保险；可手动"问 35B"按钮 |
| **R6** server 崩溃/断连 | 对话中断 | orchestrator 重连 + 状态灯 `idle` + 降级 Voicebox 系统播报 |
| **R7** 回声/双讲 | 自己听到自己 | 播放时做 AEC 或上行静音检测；mic 与 speaker 路由隔离 |
| **R8** 编译 llama.cpp-omni 失败（Xcode/SDK） | 装不上 | 提供 oneclick WebRTC 兜底说明（仍走 9060 API 可用） |

---

## 12. 实现前待确认（Phase 0 必做）

1. **9060 确切端点名 + 报文格式**（喂音频块 / 喂视频块 / 收 TTS WAV / 中断）——官方集成指南有，需实测固化进 `server_client`。
2. **M5 Pro 48GB 实测全双工 RTF** —— 决定 R1 是否需临时降级参数。
3. **声纹克隆参考音路径** 在 9060 `omni_init` 的传参方式（文件路径 vs base64）。
4. **CoreML 视觉后端**在 `tc-mb/llama.cpp-omni` 的编译开关与运行时参数。

---

## 13. 与现有代码的互斥关系

- `minicpmo_enabled=True` ⇒ **关闭** `judge.py` 主动判断（MiniCPM-o 即常驻感知+主动）；**取代** Whisper 唤醒词路径（MiniCPM-o 直接听）。
- 旧 `VoiceboxSpeaker` 仍保留为兜底；`build_speaker` 新增 MiniCPM-o 分支。
- YOLO/视觉主循环保留，但仅作 GUI 标注/状态，理解职责移交 MiniCPM-o。
- 35B 大脑（`LocalBrain`）保留，仅在被委托时调用；`src/tools/` 完全复用。

---

*本计划为总纲。评审通过后，按 Phase 0→6 逐步实现，每阶段结束同步文档与记忆。*
