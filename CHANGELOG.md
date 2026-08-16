# 修改日志 (Changelog)

记录 J.A.C. 项目对代码 / 脚本 / 配置的实际改动。最新改动在最上方。

---

## 2026-08-16（夜）— OMNI 全双工五项体验修复（Voicebox 代理超时 / 升级结果前缀 / 推流刷屏 / 任务提取 / 普通对话）

- **背景**：bo s s 真机对话暴露 5 类问题：①普通寒暄（"你好你在吗"）无回复；②"查一下时间"被 ASR 拆字 + 换行截断导致答非所问（实际回的是电池）；③回灌文本把"（升级结果）"前缀也念了出来；④`[omni-client] 推流#…` 每 0.4s 刷屏；⑤Voicebox 偶发"轮询音频超时"降级系统音。
- **修复（按根因）**：
  1. **Voicebox 代理劫持根因（`src/audio/voicebox_tts.py`）**：`VoiceboxSpeaker` 建 `requests.Session()` 时未绕过本机代理，localhost 的 `/generate`、`/audio` 被代理劫持成 502/504（日志 `：None` = 代理无响应），轮询 60s 全失败超时降级。修复：建 session 即 `trust_env=False` + `proxies={"http":None,"https":None}`（与 omni `_ensure_no_proxy()` 同源思路、模块自包含）；`_poll_audio` 总超时改读 `VOICEBOX_POLL_TIMEOUT`（默认 120s），单次 GET 超时 30→10s 更快暴露 hang，错误文案带「最近 HTTP 状态」区分代理(502/504) vs 服务端慢(500)；移除 `>44` 字节硬判，下游 RIFF 魔数校验兜底。
  2. **「（升级结果）」被朗读（`src/omni/__main__.py` / `src/runtime.py`）**：升级结果用 `f"（升级结果）{result}"` 直接喂 `speak_result` 进 TTS，被 Voicebox 念出。修复：分离「干净 spoken 文本」与「带前缀 GUI 文字」——`speak_result(clean)` 只收干净文本，`append_reply` 才带前缀进 GUI 实时文字区（控制台/文字区仍可见前缀，但不再出声）。
  3. **推流刷屏（`src/omni/client.py`）**：`_push_loop` 每 ~0.4s 打印推流/静音日志。修复：仅「人声↔静音」状态翻转时打印一行；新增 `OMNI_DEBUG=1` 才逐块打印 RMS 诊断（默认安静，排障时不刷屏）。
  4. **任务提取错乱（`src/omni/client.py`）**：ASR 把"查一下这台电脑的本地时间"拆成"查 一 下这台电"+"脑的本地时间"，令牌检测按首个换行截断 → 任务被截短、大脑理解偏差答非所问。修复：新增 `_clean_task`（删汉字之间空格 + 折叠跨换行空白还原连贯指令）+ `_try_finalize_pending`（跨换行累积、遇句末标点即时触发、长度 ≥64 或 1.5s 定时器兜底），不再按首个换行硬截断。
  5. **普通对话无回复（`src/omni/prompts.py` + `src/omni/client.py`）**：强化系统提示——纯寒暄（"你好/在吗"）必须先用口语回应、绝不吐令牌；仅当核心需求命中工具/外部信息才升级；把"无法回答"收窄为"确实无法仅凭常识回答"。另：`OmniClient.listen_prob_scale` 默认值 1.0→0.5（CLI 演示路径此前漏传、等于没修复"只听不说"；GUI 路径经 config 本就 0.5）。诊断结论：日志 RMS=0.110 远超人声阈值，**非噪音误判**，是模型此前未生成寒暄回复。
- **验证**：`py_compile` 全过；`tests/test_omni_m2.py` 全过（扩展 3 用例：ASR 空格折叠跨换行提取为"查一下这台电脑的本地时间"、幻觉护栏、普通对话不升级）；真机验收待 bo s s（按 `jac-omni-m5-acceptance` SOP + 新增四项：①普通寒暄有口语回应 ②回灌无"升级结果"前缀 ③控制台无刷屏 ④`:None` 超时消失）。

---

## 2026-08-16（续）— OMNI 升级令牌静音期幻觉护栏 + 推流日志降噪

- **背景**：bo s s 真机复现——纯静音段（RMS 0.003~0.005，用户尚未开口）omni 自行幻觉出 `<<CALL_QWEN>>查一下这台台电脑的电池电量百分比` 并**自动执行**升级；因 `_call_qwen_fired=True` 把后续主对话音频全静音，用户随后真实说的"你好能听到我说话吗"也无语音回复。另：B2 推流诊断日志每 0.4s 一行（静音段也打印）严重刷屏。
- **修复（纯客户端，`src/omni/client.py`）**：
  - **升级令牌护栏 `_has_recent_speech()`**：令牌命中时先查「令牌前 `_speech_window=3.0s` 内是否检测到真实人声（RMS≥`_speech_rms_th=0.02`，mic_check 实测说话段 0.08+）」。静音期（含从未检测到人声的开局）判定为幻觉 → 仅丢弃该任务、停止朗读幻觉内容、**不触发升级、不静音**，用户真实发言仍可正常回复。真实人声后窗口内的令牌仍正常升级。
  - **`_push_loop` 记录 `_last_speech_ts`**：每帧 RMS≥阈值即刷新；同时**推流日志降噪**——仅当检测到人声时打印一行（🎙 + RMS/峰值/块大小），静音段只首块 + 每 10 块（≈4s）汇总一行，消除刷屏。
- **取值说明**：`_speech_window`/`_speech_rms_th` 为类内常量，若真机出现「人声停顿 >3s 后说的指令被误拦」可调大 window；若仍幻觉可调高 rms 阈值。
- **附带修复 `_on_text` pending 跨 delta 延迟**：令牌命中但任务描述跨多个 delta 到达（无即时换行）时，原逻辑只在 1.5s 兜底定时器触发，导致任务结算延迟；改为在 pending 累积期间本段一旦出现换行即立即结算触发，降低升级延迟（同步测试 `test_client_streaming_token` 因此通过）。
- **验证**：`py_compile` 通过；`tests/test_omni_m2.py` + `tests/test_omni_m3_token.py` 全过（含新增 `test_silence_hallucination_guard`）；真机复测待 bo s s 执行（静音期不应再出现 `[OMNI升级]` 自动任务，对麦说话仍正常出回话；控制台不再刷推流行）。

---

## 2026-08-16 — OMNI 全双工「只听不说」根因修复（listen_prob_scale 传参）

- **背景**：GUI 启动 OMNI 全双工后，对着麦说话模型全程 `listen=1` / `is_end_of_turn=0` / `llm_text.len=0`，完全不回复。完整调查链（mic_check.py 排除设备/权限/人声质量、增益 8/10 复测排除能量不足）证明根因在**采样参数**：服务端 MiniCPM-o 在 full_duplex 下天然偏好采样 `<|listen|>`，客户端未传 `listen_prob_scale` → 服务端用默认 `1.0`（偏置 0）→ `is_end_of_turn` 永不翻转 → 只听不说。与音频能量/增益/设备/客户端采集无关。
- **修复（纯客户端，不改 C++、不重编译）**：在 `session.init` 顶层补 `config.listen_prob_scale`（默认 0.5，偏置 -1.0，压低 listen 逼模型回复）。
  - `src/utils/config.py`：Config dataclass 新增 `omni_listen_prob_scale: float = 0.5`，`load()` 支持环境变量 `OMNI_LISTEN_PROB_SCALE`。
  - `src/omni/client.py`：`OmniClient.__init__` 新增 `listen_prob_scale` 参数并透传到 `self`；`session.init` 的 `init_msg` 顶层新增 `"config": {"listen_prob_scale": ...}`；`_push_loop` 新增 B2 逐块推流诊断日志（块序号/间隔/RMS/峰值，仅日志）。
  - `src/runtime.py`：`_start_omni` 的 `OmniClient(...)` 透传 `listen_prob_scale=config.omni_listen_prob_scale`。
  - `gui.py`：右侧选项面板新增「Listen 概率系数 (OMNI)」`QDoubleSpinBox`（0.1~1.0，步长 0.05，默认 0.5）并接入 `_collect_config`；**默认勾选 OMNI 全双工、取消勾选「前置判断模型」**（bo s s 偏好，仅改 GUI 呈现，不改 `config.py` 全局默认，不影响 CLI/传统模式）。
- **取值指引**：默认 0.5 基准；仍只听不说降到 0.3/0.2；抢答升到 0.6~0.8；**增益务必调回 1.0**（×10 削波有害）。
- **验证**：`py_compile` 通过；真机复测待 bo s s 执行（`tail -f temp/omni_server.log` 看 `listen`→`is_end_of_turn` 翻转 + 对麦说话出 Voicebox 回话）。

---

## 2026-08-15（hotfix）— GUI 启动失败 `name 'os' is not defined`

- **根因**：`src/runtime.py` 的 OMNI 启动分支 `_start_omni` 用了 `os.path`（解析 `omni_ref_audio` 绝对路径），但文件顶部**缺少 `import os`**。历史遗留、此前未勾选 OMNI 走不到该分支故未暴露；本次 OMNI GUI 开关可用后一勾即炸。
- **修复**：①`src/runtime.py` 顶部补 `import os`；②排查确认 omni 链路其余用到 `os` 的模块（`client.py` / `server_launcher.py` / `__main__.py`）均已导入，无同类隐患；③`gui.py` 启动失败捕获改为打印**完整 traceback**（原仅打印异常消息），便于真机验收时直接定位根因。
- **验证**：`py_compile src/runtime.py gui.py` 通过。

---

## 2026-08-15 — omni 全双工 bug 修复：令牌被朗读 + 答案未读 + 多轮失效 + GUI 实时

- **背景**：bo s s 真机跑 `python -m src.omni --mic 2` 暴露 4 类问题：①问"电量"被 ASR 识别成"天气"（MiniCPM-o 模型识别质量限制，非代码 bug，仅缓解）；②升级结果没读出来（时间答案静默丢失）；③把 `<<CALL_QWEN>>查一下电池电量` 这种**问题本身**当答案朗读；④GUI 视频已接但麦克风音量条/实时回复文字/升级结果显示未接。
- **核心 bug（令牌被朗读）修复 `src/omni/client.py`**：`_on_text` 原为「先 `feed` 喂 Voicebox 桥接、后做令牌检测」，承载令牌的 delta 在检测前已入朗读队列。改为**检测前置**——含 `<<CALL_QWEN>>` 的 delta 只把令牌之前的文本 `feed` 给桥接，令牌及任务描述丢弃并立即触发升级，绝不朗读问题本身。`src/omni/voicebox_bridge.py` 的 `feed` 同步加令牌截断兜底（双保险）。
- **答案未读修复（静默失败点）**：①`src/audio/voicebox_tts.py` 的 `VoiceboxSpeaker.speak` 加 `RLock` 串行锁 + 临时文件名改 `uuid`，根治回灌线程与桥接线程并发导致的重叠/同毫秒文件互覆盖；②`src/omni/backfeed.py` 的 `speak_text_via_voicebox` 在 `speaker is None` 时降级系统 TTS（不再静默丢弃）；③`client.speak_result` 与 `src/omni/__main__.py`、`src/runtime.py` 的升级 worker 去掉 `is_running()` 静默跳过分支，答案**一定出声**（Voicebox 优先，否则系统 TTS）。
- **多轮升级失效修复 `src/omni/client.py`**：`_call_qwen_fired` 触发后永不复位导致第二次升级被吞。新增 `_reset_escalation_state()`，在每轮 `listen` 事件（新用户轮）复位升级标志，支持反复触发 `<<CALL_QWEN>>`；`response.done`/`session.closed` 的 `flush_remaining` 守卫加 `_token_seen` 判断，避免令牌残句冲入朗读队列。
- **GUI 实时整合 `gui.py` + `src/runtime.py` + `src/utils/config.py`**：①OMNI 模式右键面板新增「麦克风音量条」+「OMNI 实时回复」文字区，由现有帧/状态定时器轮询 `OmniClient.get_latest_mic_level()` / `get_reply_text()` 刷新；②新增「麦克风增益」数字框（接 `config.omni_mic_gain` → `OmniClient.mic_gain`，缓解内建麦离嘴远能量不足）；③升级结果经 `append_reply` 写入实时文字区；④OMNI 与传统 judge/TTS/tools **互斥 UI 提示**：勾选 OMNI 时灰掉三者并标注"OMNI 模式下不生效"。
- **ASR 误识别（电量→天气）处理**：属 MiniCPM-o 模型识别质量限制，代码无法根治；本次仅做可观测性（GUI 实时回复区 + 麦克风增益调参入口）+ 确认缺用户原话显示（omni 协议只回传模型回复文本，用户原话需并行本地 Whisper 旁路 ASR，列为后续可选增强）。
- **验证**：`py_compile` 全部通过；新增 `tests/test_omni_m3_token.py` 验证「令牌文本不进朗读队列 + 多轮升级可重复触发」均通过；GUI 实跑待 bo s s 验收（视频/音量条/回复文字/升级结果显示 + 互斥提示）。

---

## 2026-08-15 — M6 文档同步：全双工/流式/Function Calling 状态校正 + 补 omni 模块

- **背景**：M5 全双工验收 + M7b/M7a 句子级 Voicebox 桥接已落地，但 `codingLOG.md` §4 仍标「全双工=未解决」、`AGENTS.md`/`README.md` 仍把"流式"和"function calling/agent 框架"列未实现、`src/omni/` 在 AGENTS.md 完全无记录、"没有自动化测试"已过时。
- **`codingLOG.md` §4**：由「未解决」改为「已落地（全双工 M5 验收 + M7b 句子级桥接；token 级 TTS 待做）」，重写现状（含 M7b/M7a 说明）与仍待做项。
- **`AGENTS.md`**：①「当前实现」新增「全双工 Omni 模式（src/omni，M5 验收 + M7b/M7a）」小节；②「重要文件与目录」新增 `src/omni/` 条目；③「当前进度」未实现项清单移除已落地的 function calling/工具执行层/agent 执行框架、把"流式 STT/LLM/TTS"细化为"token 级流式 TTS（全双工+M7b 已近似实现）"；④「已知限制」修正"STT/LLM/TTS 均非流式"为准确描述（LLM 全双工已流式、TTS 句子级桥接近似实时），移除"无 function calling"，"没有自动化测试"改为"已有自动化测试"。
- **`README.md`**：「尚未实现」清单中"流式 STT/LLM/TTS"改为"token 级流式 TTS（omni 全双工已落地 LLM 流式 + M7b 句子级桥接近似实时）"。
- **说明**：M7b 真机逐句听感验收待 bo s s 收尾；本同步基于"全双工主链路 M5 真机已验收 + M7b 代码已落地单测通过"，不预设未确认的真机听感结论。

---

## 2026-08-15 — M7b/M7a：主对话 + 回灌改用本地 Voicebox 克隆声纹

- **背景**：omni 全双工自带 TTS 无 JAC 克隆声纹、音质差（"响一下就结束"）；且回灌原走 omni 第二个 turn_based 会话，但 llama.cpp-omni server **单会话**——主 full_duplex 占槽后第二个会话被拒（server 日志 `session.init rejected — active session exists` → client 收到 `ConnectionClosedOK` 无声音）。bo s s 拍板 M7b（主对话句子级流式桥接 Voicebox）+ 一并做 M7a（回灌改用 Voicebox，因原路径必死）。
- **M7b 主对话**：新建 `src/omni/voicebox_bridge.py`——按标点/句子边界把 omni 的 text delta 攒成句，攒够一句（遇 。？！；\n 或超 40 字/2s 超时）就送本地 Voicebox 合成该句并播放，下一句继续攒；保留「说一句听一句」近似实时感 + JAC 克隆声纹。独立 daemon 播放线程串行保序，绝不阻塞 omni 接收协程。`src/omni/client.py`：`__init__` 加 `voicebox_speaker` 参数并创建 `VoiceboxBridge`（仅当启用播放且传入 speaker）；`_receiver_loop` 的 `kind=audio` 在桥接启用时**丢弃 omni 自带 audio**；`_on_text` 在令牌触发前把文本喂给桥接攒句；`_fire_call_qwen` 时 `flush_and_stop`（flush 残留 + 清空未播队列，防与回灌重叠）；`response.done`/`session.closed` 正常结束 `flush_remaining` 尾句；`stop` 释放桥接。
- **M7a 回灌**：`speak_result` 改用本地 Voicebox 合成播报（替代 omni 第二会话）；`src/omni/backfeed.py` 重写为薄封装 `speak_text_via_voicebox(speaker, text)`（不再开 omni WS）；Voicebox 不可用自动降级系统 TTS / 仅文本，不崩。
- **接线**：`src/omni/__main__.py` 加 `--no-voicebox`（默认开）并构造 `VoiceboxSpeaker` 传入 client；`src/runtime.py` 的 `_start_omni` 单独创建 `VoiceboxSpeaker`（OMNI 模式 line 134 直接 return 跳过传统 `build_speaker`，故 self.speaker 原为 None，现统一赋 Voicebox 实例）传给 OmniClient。`speak_result_via_turnbased` 全部改名为 `speak_result`（__main__/runtime 共 6 处调用）。
- **验证**：`py_compile` 通过；`tests/test_omni_m2.py` 3 passed 无回归；`VoiceboxBridge` 攒句切句内联验证通过（三句按标点切分 + 尾句 flush）。待 bo s s 真机验收：开 Voicebox App（17493），跑 `python -m src.omni --mic 2`（去 --no-play 戴耳机），闲聊应听到 JAC 克隆声纹逐句播；问"查电池"→升级→回灌也应为 JAC 克隆声纹（ConnectionClosedOK 消失）。

---

## 2026-08-15 — M5 真机验收收尾（主链路通过 + 戴耳机切麦根因 + 待办步骤）

- **M5 主链路真机验收通过（中午）**：修复 CLI 回调属性名 bug（`client.callbacks`→`client.cb`，`src/omni/__main__.py`）后，bo s s 手动重跑确认全链路通：状态 connecting→ready→`🎧 聆听中…`→文本输出→omni 主动打招呼「你好，有什么需要帮忙的吗」；纯闲聊（"你好"）**不吐 `<<CALL_QWEN>>` 令牌**（系统提示词硬化生效）。已移除调试探针（`[omni-dbg]` 与 `_dbg_rx`），控制台恢复干净。
- **三问解答已给 bo s s**：①"语音没读出"=`--no-play` 就是关 omni 语音播放（听 omni 开口需去掉 `--no-play` 并戴耳机防回授）；②"🎧重复"=探针刷屏已移除，`🎧聆听中…`每回合结束重现一次是正常聆听态回显；③GUI「主动判断模型开关」=旧 `judge.py`（MiniCPM-o via LM Studio），`runtime._start_omni` 注释明确 OMNI 与传统**互斥、OMNI 下不启动判断引擎**，故 OMNI 模式该开关不生效，仅传统被动模式有用。
- **戴耳机「听不到」根因定位（午后）**：bo s s 戴耳机说「你好」也听不到，疑误听。对照 server 日志铁证——成功会话有 `listen=0`+`speek_done=1`（开口说了），失败会话整段（93 round 直到 sliding window 触发）全是 `listen=1`+`speek_done=0`+`llm_text.len=0`（**从头到尾只 listen 从未 speak**）→ omni 一直没检测到有效用户语音。根因 = `src/omni/client.py:_start_capture` 开麦克风用 `pyaudio.open(input=True)` **未指定 `input_device_index`**，取 macOS 默认输入；戴耳机（带麦/蓝牙）时 mac 自动把默认输入切到耳机麦，若耳机麦未授权/静音/增益低则采静音 → VAD 永判无人说话。代码无回归（录音逻辑未改），是设备切换问题。
- **已加排查辅助**：`_start_capture` 在 open 前打印 `[omni] 麦克风输入设备: <name> (index=.., 采样率≈..)`，戴/不戴耳机重跑即可一眼看到采的是哪个设备。py_compile 通过。
- **当前待办步骤（见下方 memory「断点续做清单」）**：① 摘耳机对照（server 还开着，重跑 `python -m src.omni --no-auto-launch`，看设备行是否为"内建麦克风"、说"你好"是否回应）；② 若坐实切麦→macOS 系统设置→声音→输入固定选"内建麦克风"；③ 固定麦后带 `--no-play` 验令牌触发（问"查电池电量"应吐 `[升级→大脑]`+`⚡`）；④ 开 LM Studio 加载 35B 测升级回灌出声（争 GPU，建议用完即退）；⑤ M6 文档同步（codingLOG.md §4「全双工=未解决」stale）。

---

## 2026-08-15 — M5 真机验收修复（三）：CLI 路径接通升级回灌

- **背景**：bo s s 真机戴耳机跑 `python -m src.omni --no-auto-launch --no-play`，full_duplex 令牌触发成功（"查电池"→`<<CALL_QWEN>>`+`⚡升级令牌触发`），但**结果没读出来、没反馈**。根因 = CLI 演示路径 `src/omni/__main__.py` 的 `on_call_qwen` 只打印令牌、**没接升级路由**（GUI 路径 `src/runtime.py._handle_escalation` 接了，CLI 漏了），故令牌触发后无人调 qwen+tools、也无人回灌播报。
- **修复 `src/omni/__main__.py`**：把 `runtime.py` 已验证的升级接线移植到 CLI 的 `on_call_qwen`：
  - `_ConsoleCallbacks.__init__` 新增 `client` 引用 + `_router`（懒创建）；`main()` 调整为先建 `OmniClient` 再注入回调（`cb = _ConsoleCallbacks(client)`）。
  - `on_call_qwen` 触发后调 `_start_escalation(task)`：开后台线程 `_worker` → 懒创建 `EscalationRouter` → `escalate(task)`（qwen+tools 同步阻塞，绝不在 omni 接收循环里跑）→ 结果非空则 `client.speak_result_via_turnbased(f"（升级结果）{result}"）` 经 omni turn_based 回灌 → `finally` 调 `mark_escalation_done()` 解除主会话静音；空/异常有兜底播报文案。
- **集成验证**（omni 9060 + LM Studio 12345 在线）：新增 `/tmp/jac_m5_cli_wire_probe.py` 复刻修复后 worker 链路 → escalate(`查一下这台电脑的电池电量百分比`) 返回真实数据「电池电量还剩 80%，充电中」（42.1s，见下注），backfeed turn_based 通道调用成功。**证明 CLI 路径「令牌→升级→回灌」端到端在真环境跑通**。`py_compile` 通过。
- **注（性能）**：本次 escalate 耗时 42.1s（此前沙箱单独测 7.4s）。差异主因 = omni(9060, Metal) 与 LM Studio(12345, 35B, Metal) **同跑争抢 M5 Pro GPU**，且升级期间 omni 仍占 GPU。功能闭环已通；若真机体感延迟过长，可考虑升级期间降 omni 负载或错峰，属后续优化。
- **待 bo s s 真机复验**：重跑 `python -m src.omni --no-auto-launch --no-play`，说「查一下电池电量」应听到 JAC 用克隆声纹读出「电池电量还剩 80%，充电中」（后台线程跑、不阻塞对话）；天气/时间同理。

---

## 2026-08-15 — M5 真机验收修复（二）：omni 令牌触发硬化 + 控制台噪声清理

- **背景**：上一轮修 listen 刷屏 + 麦克风诊断后，bo s s 重跑发现新问题——说「查一下电池电量」时 omni 不吐 `<<CALL_QWEN>>` 令牌、自己瞎答（跑去查地铁站），升级路由（qwen+tools）从未被触发。根因 = MiniCPM-o 全双工指令遵循弱，旧 SYSTEM_PROMPT 的令牌约定没被遵循。
- **修复 `src/omni/prompts.py`**：重写 `SYSTEM_PROMPT`——「强制 + 示例 + 禁止编造」三件套：把令牌触发单列「【最重要规则】」明确四类触发项（查设备状态 / 联网查询 / 打开应用网页 / 需工具或外部信息）；新增「寒暄不影响判定」；示例从 2 条扩到 5 条（电池、开浏览器、天气、时间、带寒暄任务），每条都给「输出 `<<CALL_QWEN>>{任务}` 然后闭嘴」。
- **修复 `src/omni/__main__.py`**（控制台噪声）：`on_text_final` 原每轮 `response.done` 都打印「[omni] （本轮结束）」→ full_duplex 下频繁刷屏；改为去重（仅当 final 文本比已打印 delta 更长时才补印，否则静默）；`on_text_delta` 把裸 `<<CALL_QWEN>>` 字面替换成友好前缀 `[升级→大脑] `。
- **主动验证**（omni 9060 与 LM Studio 12345 均在线）：新增 `/tmp/jac_m5_prompt_probe.py`（turn_based 文字探针），把硬化后 SYSTEM_PROMPT 发给 omni，覆盖 7 条 query。结果 **7/7 符合预期**：需升级的全吐令牌（电池/开网页/天气/带寒暄天气/时间），闲聊全不吐（你是谁/讲笑话）。证明 prompt 硬化在文本路径生效（必要条件）；真机 full_duplex 语音触发仍需 bo s s 戴耳机重跑确认（双工指令遵循更弱）。
- **结论**：bo s s 痛点（查电池/开应用）在文本路径已稳定触发令牌。真机端到端闭环（令牌→qwen+tools→回灌播报）待 bo s s 重跑 `python -m src.omni --no-auto-launch --no-play` 验收：应看到 `[升级→大脑] 查一下这台电脑的电池电量百分比` + `⚡ 升级令牌触发`，随后 qwen 调 `get_system_info` 并回灌播报。`py_compile` 改动文件全部通过。

---

## 2026-08-15 — M5 真机验收修复（listen 刷屏 / 麦克风诊断 / 令牌可见）

- **背景**：bo s s 戴耳机跑 `python -m src.omni` 真机验收，全双工闭环跑通（connecting→ready、摄像头开、omni 主动开口），但暴露两问题：(1) `[omni] 🎧 聆听中…` 每帧刷屏；(2) 说「查一下电池电量」omni 无反应、无文本、无 `<<CALL_QWEN>>` 令牌。后者疑似麦克风未采到（omni 未听到用户）。
- **修复**：
  - `src/omni/__main__.py`：`_ConsoleCallbacks` 加 `_was_listening` 去重（仅进入聆听时打印一次）；重写 `on_call_qwen` 打印 `⚡ 升级令牌触发`；`on_text_delta` 在文本出现时收尾聆听行并重置标志。
  - `src/omni/client.py`：`OmniCallbacks` 新增 `on_mic_level(rms)` 默认空回调；`_push_loop` 计算麦克风 RMS，持续静音（RMS≈0）超 5s 周期打印权限/输入设备警告（不干扰正常文本流）。
- **诊断目标（bo s s 重跑）**：若报「持续未检测到麦克风音频」→ macOS 麦克风权限/默认输入设备问题（去系统设置授权终端/IDE）；若无警告但仍无响应 → omni 全双工行为问题（需调 prompt 或确认 ASR）。py_compile 通过。

---

## 2026-08-15 — MiniCPM-o-4_5 全双工（M5）：实机验收（沙箱可自动化部分）

- **背景**：M0/M1/M2 代码已落地，bo s s 进入 M5 做实机联调验收。沙箱无麦克风/声卡，无法验「真实语音触发令牌」端到端，本次聚焦本机可自动化的三项。
- **环境**：本机 Apple Silicon M5 Pro / 48GB。**omni server（9060，Q8_0）与 LM Studio（12345，qwen3.6-35b）同跑**，合计逼近 48G 上限但未 OOM（bo s s 选「冒险同拉」）；后台 server 常驻，真机验收可复用（`python -m src.omni --no-auto-launch`）。
- **验收项（全过）**：
  1. **升级路由大脑+手闭环复测**：`EscalationRouter.escalate("查电池+时间")` → 模型自主调 `get_system_info` → 真实数据（电量80%充电中/时间/18核/内存8.1/48GB）→ 流式合成口语短句，耗时 7.4s。M2 链路无回归。
  2. **全双工无头握手**：`OmniClient.start()`（关 mic/camera/playback）连 9060 → `session.init` → `session.created`（session_id=6fcae7…），声纹克隆加载成功，10.4s。
  3. **回灌 turn_based TTS**：独立 turn_based 会话 `input.append(messages + tts:{enabled:true})` → omni 用克隆声纹流式合成音频 **656640 字节**（got_created=True / got_audio=True）。M2 回灌输出端可用。
- **未验（需 bo s s 真机戴耳机）**：真实语音 → omni ASR 出 `<<CALL_QWEN>>` 令牌 → 触发升级 → 回灌播报的端到端；全双工 RTF 流畅度；麦克风啸叫（建议戴耳机或加 WebRTC AEC）。已单列任务跟踪。
- **产物**：`/tmp/jac_m5_escalate_probe.py`、`/tmp/jac_m5_handshake.py`、`/tmp/jac_m5_backfeed.py`（临时验收脚本，留存 /tmp 未入仓）。

---

## 2026-08-15 — MiniCPM-o-4_5 全双工（M2）：升级路由 + qwen+tools 回灌

- **背景**：M1 已交付全双工语音闭环（omni = 耳朵+眼睛+嘴巴）。M2 打通「omni 遇到需联网/操作电脑/复杂推理的任务 → 令牌 `<<CALL_QWEN>>` → qwen3.6-35b+tools（大脑+手）处理 → 结果回灌 omni 自然播报」的升级链路。
- **关键架构决策（源码坐实 `llama.cpp-omni/tools/server/ws_handler.cpp`）**：
  - full_duplex 的 `input.append` **严禁带 `messages`**（:1075 `fail_fast`），文字注入不可行；音频注入会被当作用户语音二次 ASR+应答（双份播报）。
  - 故回灌定为**独立 turn_based 会话**（`session.init mode="turn_based"` + `tts:{enabled:true}`），用同一克隆声纹把文本流式 TTS 出来（即此前 `omni_turnbased_test.py` 验证过的路径）；主 full_duplex 会话升级期间 `_suppress_audio` 静音，避免重叠/回声。
- **新增文件**：
  - `src/omni/router.py`：`EscalationRouter`（持有独立 `LocalBrain(backend="lm_studio", lm_studio_model="qwen/qwen3.6-35b-a3b")`，跑 `run_agentic(prompt, get_tool_schemas(), execute_tool)` 流式聚合最终回答）+ 纯函数 `parse_call_qwen`（从文本解析令牌，区分未命中/命中未齐/命中）。
  - `src/omni/backfeed.py`：`speak_text_via_omni(url, ref_audio_b64, text)` —— 独立线程 + 独立事件循环开临时 turn_based 会话，复用 `client._PyAudioPlayer` 播放 omni 返回的合成语音（播完 join 返回，便于调用方解除主会话静音）。
- **改动 `src/omni/client.py`**：`OmniCallbacks.on_call_qwen` 回调；`_on_text` 流式令牌检测（令牌可跨多个 delta 分片、`<<CALL_QWEN>>` 命中后幂等、令牌后无换行时 1.5s 兜底定时器触发）；`_suppress_audio` 升级静音（解除发生在回灌完成后的下一次 `listen` 事件，防爆音）；`mark_escalation_done` / `speak_result_via_turnbased` / `get_ref_audio_b64`；session.init 后缓存 `_ref_audio_b64` 供回灌复用；`stop()` 取消兜底定时器。
- **改动 `src/omni/prompts.py`**：细化 `SYSTEM_PROMPT`（令牌后停下等大脑，不长篇回答）；新增 `TOOL_SYSTEM_PROMPT`（大脑侧：口语短句把结果告诉 boss，不直接发声）。`__init__.py` 导出 `EscalationRouter` / `parse_call_qwen` / `TOOL_SYSTEM_PROMPT`。
- **改动 `src/runtime.py`**：`_OmniRuntimeCallbacks.on_call_qwen` 接管（状态灯升级期间显示 Thinking）→ `JACRuntime._handle_escalation` 在后台线程跑 router → 经 omni turn_based 回灌播报（结果非空正常播报；空/异常有兜底文案）→ `mark_escalation_done`；`omni_router` 懒创建。
- **测试 `tests/test_omni_m2.py`**：离线单测——`parse_call_qwen` 各分支、client 跨分片流式检测/幂等/静音、`EscalationRouter` 接线（mock 大脑验证 prompt/tools/流式聚合）。
- **验证**：`py_compile` 全部改动文件通过；离线单测全过；**真实 escalate 实跑通过**——本机 LM Studio(12345) 在线，`EscalationRouter.escalate` 触发模型自主调用 `get_system_info` 工具 → 执行拿电池/CPU/内存 → 流式合成最终回答返回（M2「大脑+手」闭环已验证）。omni 语音流令牌触发 + turn_based 回灌播报的端到端真机验收属 M5 联调。

---


- **背景**：M0 已在本机验证 llama.cpp-omni（master 分支）+ MiniCPM-o-4_5-GGUF(Q8_0) 的本地全双工 WebSocket 契约可用。M1 把该契约封装成 J.A.C. 的 `src/omni/` SDK，并接入 GUI 启动开关与运行时分支，使 bo s s 可在 UI 里一键进入「OMNI 全双工模式」。
- **新增 `src/omni/`**：
  - `client.py`：`OmniClient` 全双工客户端——实时推流（麦克风 16k float32 + 摄像头 jpeg）、接收 omni 文本/语音增量、声纹克隆（复用 `voices/silverwalf_voice.wav`，自动重采样 16k）、内置低延迟 PyAudio 播放器、事件回调（状态/文本/语音/聆听）；严格按 M0 实测的「真实实时节奏」喂音频（防全双工只读不说）。
  - `server_launcher.py`：`OmniServerLauncher` 定位/按需启动 `llama-omni-server`（Metal，`-ngl 99 -c 8192`，Q8_0），TCP 端口就绪探测（`NO_PROXY` 绕过本机代理劫持）。
  - `prompts.py`：omni 系统提示（角色 J.A.C.、称呼 boss、三条准则、含 `<<CALL_QWEN>>` 升级令牌约定）。
  - `__main__.py`：CLI 演示 `python -m src.omni`（不依赖 GUI，真机验收用）。
- **配置 `src/utils/config.py`**：新增 `OmniConfig` 字段（`omni_enabled` 默认关、`omni_server_url/bin/model_dir/host/port/quant(Q8_0)/ref_audio/fps/duplex/auto_launch`），全部支持环境变量覆盖。
- **运行时 `src/runtime.py`**：`JACRuntime.start()` 按 `config.omni_enabled` 分支进入 OMNI 模式——跳过传统 Voicebox TTS / Whisper STT / MiniCPM-v 判断引擎，直接起 omni 服务 + `OmniClient` 全双工闭环；`manual_input` 在 OMNI 模式给出明确提示（文字指令/回灌留 M2）；`stop()` 优雅关闭会话（不自杀服务进程，便于复用）。
- **GUI `gui.py`**：右侧选项面板新增「MiniCPM-o-4_5 全双工（接管 TTS + 判断）」开关，纳入 `_collect_config`/`_set_options_enabled`；状态栏新增 OMNI 指示；OMNI 模式下视频预览取自 omni 客户端摄像头帧。
- **依赖**：`.venv` 补装 `websockets==15.0.1`（已在 `requirements.txt` L106 声明，旧 venv 未装）；`src/omni/client.py` 复用既有 `opencv-python`/`PyAudio`/`soundfile`/`soxr`/`numpy`。
- **验证**：`py_compile` 八个新增/改动文件全部通过；轻量冒烟（二进制定位 / TCP 探测 / 声纹加载 44.1k→16k 约 12.4s / 令牌齐全）通过；**无头全连接冒烟**自动起服务 + 连 WS + `session.init` 收到 `session.created` 通过（证明 SDK 握手/初始化在实时服务上可用）。
- **说明**：M1 聚焦「全双工语音闭环」，文字指令注入与 `<<CALL_QWEN>>` 回灌（qwen+tools）属 M2；AGENTS/README/codingLOG 文档同步统一在 M6 收口。

---

## 2026-08-11 — 控制台日志与交互优化（7 项问题中的 5 项落地）

- **背景**：bo s s 跑完整启动日志后反馈 7 个优化点，其中 5 项本期实现、2 项（问题 5/6）仅分析不改动。
- **改动**：
  1. **主动介入过滤（问题 1）**：`src/judgment/judge.py` 系统提示词把"单纯的等待或困惑（无人提问、无危险/异常）"从【需要介入】移到【不需要介入】；`main.py` 新增 `_should_skip_intervention()` 兜底过滤——reason 命中危险/异常关键词或用户有提问则放行，纯等待/困惑且无提问/无危险则拦截、不唤醒大脑（仅打印一行提示，不刷屏）。
  2. **Embedder 日志精简（问题 2）**：`src/memory/embedder.py` 删除每次启动必打的两行配置提示（HF 镜像、缓存目录），只保留"已缓存→跳过下载"与"已加载向量模型（维度 N）"两行；失败文案补充手动安装引导。
  3. **STT 繁→简状态透明化（问题 3）**：`src/audio/stt.py` 启动时打印一行明确状态（OpenCC 已启用 / 未安装用内置字表兜底），取代原"仅转繁体残字才提示一次"逻辑；`requirements.txt` / `requirements_fixed.txt` 新增 `opencc-python-reimplemented==0.2.1`（识别结果本就在 transcribe 返回前 `_to_simplified`，发给大脑已是简体）。
  4. **兜底语音复用现成音频（问题 4）**：`main.py` 播放处判断——若回复文本为兜底串"（刚才走神了，能再问一次吗？）"且 `voices/voice_resources/error.wav` 存在，直接 `play_wav()`（复用 src/audio/playback.py），不再每次重新合成 TTS。该 wav 不忽略、需 `git add` 上传。
  5. **工具结果/回答前台排版（问题 7）**：`main.py` 的 FC system prompt 追加"输出格式铁律"——日期/数字/时间不要逐字或逐行拆分，根治模型把"2026年8月11日13点01分25秒"拆成每字一行铺满窗口。
- **仅分析不改动**：问题 5（"流式返回为空"根因为 LM Studio 同时跑 35B 大脑 + MiniCPM-o 并发争抢 GPU）；问题 6（judge 请求 15s 超时返回不介入、非直接交 brain，下一轮轮询成功才介入）。
- **验证**：`py_compile` 四个改动文件全部通过。

---

## 2026-08-11 — GUI 右侧面板新增「工具功能」开关

- **背景**：此前 Function Calling 总开关是 `main.py` 模块级常量 `TOOLS_ENABLED`（由环境变量决定），GUI 选项面板无对应控件，导致 GUI 模式下无法开关工具功能（UI 改不了、始终按环境变量默认生效）。
- **改动**：
  - `gui.py`：右侧可折叠选项面板新增 `tools_chk`（"工具功能（Function Calling）"）勾选框，初始读 `config.tools_enabled`；纳入 `_set_options_enabled`（运行时与其他开关一同禁用）与 `_collect_config`（启动前写回 `tools_enabled`，与主动模型/TTS 开关一致的"启动前配置"模式）。
  - `src/runtime.py`：`JACRuntime.start()` 把 `config.tools_enabled` 桥接到 `main.TOOLS_ENABLED`，使 `process_response` 在 GUI 模式下真正按 UI 配置启用/禁用 Function Calling（修复此前开关形同虚设的问题）。
- **验证**：`py_compile` 通过；静态校验确认"右侧开关添加 + `_collect_config` 收集 + `runtime.start` 桥接"三者齐备。
- **说明**：与主动模型/TTS 开关一致，为启动前配置——修改后需点「启动」重新加载生效。FC 本身是 `process_response` 每次请求实时读取 `main.TOOLS_ENABLED`，具备运行时热切换的技术条件；如需"运行中点开关即时生效"可再加一行 `toggled` 回调，暂未做以保持与现有开关行为一致。

---

## 2026-08-11 — STT 语音识别修复：强制简体中文 + 繁→简兜底归一化

- **背景**：实测运行时 Whisper（`model_size="tiny"`）自动语言检测漂移，把中文识别成繁体（`現在天氣怎麼樣`）或乱码（`politikand`），导致唤醒词/视觉判断/LLM 拿到脏文本。
- **根因**：`SpeechRecognizer.transcribe()` 未传 `language`，Whisper 走自动检测；`tiny` 模型中文分辨力弱，检测一旦误判即吐繁体/乱码。
- **改动（业务代码）**：
  - `src/audio/stt.py`：`SpeechRecognizer.__init__` 新增 `language` 参数（默认读环境变量 `STT_LANGUAGE`，缺省 `"zh"`）；`transcribe()` 调用 `self.model.transcribe(..., language=self.language)` **强制简体中文**；新增 `_to_simplified()` 兜底归一化——优先用 `opencc`（完整转换，需 `pip install opencc-python-reimplemented`），未装则走内置常用繁→简映射表（覆盖口语高频字 + 实测残字），并将结果统一为简体。
  - `src/utils/config.py`：新增 `stt_language: str = "zh"` 配置项（环境变量 `STT_LANGUAGE` 覆盖），供 GUI 绑定。
  - `src/runtime.py`：构造识别器时传入 `language=config.stt_language`。
  - `main.py`：构造点 `SpeechRecognizer(model_size="tiny")` 默认继承 `STT_LANGUAGE` 环境变量（无需改动即生效）。
- **验证**：离线单测确认 `現在天氣怎麼樣 → 现在天气怎么样`、`這是我們的會議記錄 → 这是我们的会议记录` 等繁体残字正确归一；`py_compile` 全部改动文件通过；既有 `tests/unit/test_tools.py` 10/10 无回归。
- **未含（后续可选）**：`politikand` 这类纯小模型误听属 `tiny` 模型分辨力问题，非语言检测问题；如仍频繁出现可把 `model_size` 升到 `base`/`small`（更准但更慢/更占资源）。`opencc` 为可选依赖，未写入 `requirements.txt` 以免国内网络安装失败拖垮整包。

---

## 2026-08-11 — Function Calling 工具层实现（给 J.A.C. 装手）

- **背景**：大脑 `qwen/qwen3.6-35b-a3b` 经 `verify_toolcall.py` 验证支持 OpenAI 风格 function calling（M5 Pro 48G 机器，LM Studio `127.0.0.1:12345`）。
- **改动（业务代码）**：
  - `src/brain/llm.py`：新增 `ThinkResult` / `parse_tool_calls`（兼容 LM Studio 的 `arguments` 字符串格式与 Ollama 的 `arguments` dict 格式）；`_query_lm_studio` / `_query_ollama` 支持 `tools` 参数并在工具模式返回 `ThinkResult`；新增 `think_with_tools`（带工具推理）、`supports_tools`（后端能力判断）、`run_agentic`（工具调用循环生成器，流式吐出最终回答、保留打字机效果）、`_stream_final`。
  - `src/tools/`（**新建**）：`registry.py`（白名单工具注册 + OpenAI schema）、`executor.py`（安全分发执行）、`open_actions.py`（`open_url` / `open_app`）、`search_files.py`（只读本地文件搜索，限定用户目录）、`system_info.py`（时间/电池/CPU/内存）、`shell.py`（**受限 shell**：白名单命令 + `shell=False` 防注入，拦截 `rm`/`sudo` 等）。
  - `main.py`：`process_response` 非视觉分支接入 Function Calling（`TOOLS_ENABLED` 开关，默认开；后端不支持时降级普通流式对话）；新增模块级 `TOOLS_ENABLED` 常量。
  - `src/utils/config.py`：新增 `tools_enabled` 配置项（默认 `True`，环境变量 `TOOLS_ENABLED` 覆盖）。
  - `tests/unit/test_tools.py`（**新建**）：10 个单测覆盖 schema 格式、受限 shell 放行/拦截、本地搜索与越权拦截、系统状态、未知工具、`parse_tool_calls` 两种格式、`run_agentic` 在 mock 后端降级，全部通过。
- **安全边界**：工具只做打开应用/网页、只读搜索、状态查询、受限命令；`search_files` 仅扫用户目录、`run_command` 白名单 + `shell=False` 双重防注入；**不联网、不写文件、不删除、不提权**。
- **文档**：`codingLOG.md`（§2 未解决→部分解决；§5 agent 框架缺位→已落地；"无测试"→已有 `tests/unit/test_tools.py`）、`AGENTS.md`（新增「Function Calling（装手）」小节 + 文件条目）、`README.md`（已实现列表加入工具层）。

---

## 2026-08-11 — 文档同步：修正「显存不足 / 待验证 / 默认 False / 35B 未接入」过时描述

- **背景**：开发机已升级 M5 Pro 48G 统一内存，MiniCPM-o 主动判断引擎与 `qwen/qwen3.6-35b-a3b` 大脑均已实跑验证通过；原文档中「显存不足 / 待验证 / 默认 `JUDGMENT_ENGINE_ENABLED=False` / 35B 未接入代码」描述已过时。
- **改动**：
  - `codingLOG.md`：§1 主动引擎「显存不足暂未验证」→「已实跑验证通过」；§3 记忆「待验证（显存不足）」→「已落地、具备端到端验证条件」；§4 流式「理论上可以实现」→「已实跑验证」；§5「无 agent 执行框架 / 35B 未接入 / 显存不足」→「35B 已完整接入并验证；agent 执行框架缺位，Function Calling 工具层正在补齐」。
  - `AGENTS.md`：主动判断引擎「默认 `JUDGMENT_ENGINE_ENABLED=False`」→「默认开启 `True`；未加载 MiniCPM-o 自动被动」；「双模型显存压力…默认 False」→「M5 Pro 48G 已验证可同时承载，默认 `True`」。
  - `README.md`：主动判断引擎「off by default / 默认关闭」→「on by default / 默认开启」。
  - 安装文档 `new_computer_download/READMEfirst.md`（EN/L66、中/L164）、`models_config.json`、`new_computer_download/setup_new_computer.py`：同步「默认 `JUDGMENT_ENGINE_ENABLED=False`」→「默认 `True`，未加载 MiniCPM-o 自动被动」。
- **说明**：本次仅同步文档反映已验证的真实状态，未改动业务代码；Function Calling 工具层实现待 LM Studio tool calling 验证通过后开工（见 `verify_toolcall.py`）。

---

## 2026-08-09 — 语音输出去情绪标签：模型纯文本输出、TTS 中性朗读

- **目标**：移除 brain 回复中的 `[情绪] 内容` 标签，语音只输出纯文本。
- **Prompt 调整**（`main.py`）：删除 `process_response` 主对话、`img_system_prompt`、`build_text_only_vision_reply` 三处要求模型按 `[情绪] 回复内容` 格式输出的指令；同步清理视觉降级兜底的 `[平静]` 硬编码前缀。
- **解析精简**（`main.py` `process_response`）：移除情绪正则抽取逻辑，回复经 `_strip_boilerplate` 清洗 + 残留括号清除 + 超长截断后，直接 `speaker.speak(response_text)` 中性朗读（不再传 `emotion_hint`）；终端打印不再显示 `情绪:` 字段。
- **固定话术中性化**（`main.py` / `src/runtime.py`）：唤醒词「我在。」「我在，请讲。」与休眠词「好的，有需要随时叫我。」去掉 `emotion_hint` 语音风格。
- **兜底清理**（`src/brain/llm.py`）：`_query_lm_studio` 在 content 为空时改为直接取 thinking 链最后一段非空内容（去掉基于情绪标记的恢复分支）；`_mock_response` 去掉 `[happy]`/`[calm]` 前缀。
- **未改动**：TTS 各实现的 `speak(text, emotion_hint=None)` 接口保留（`emotion_hint` 仍可选，传 `None` 即中性）。
- **测试修正（顺带）**：`tests/test_voicebox_speaker.py` 的 `_make_session` 桩原本让 `/generate` 直接返回音频字节，与 2026-08-05 起生效的异步契约（`/generate` 返回 JSON `id` → 再 `GET /audio/{id}` 取音频）不符，导致 `test_speak_injects_emotion_tags_and_plays` 预存失败。已把桩对齐为真实契约（并补最小合法 WAV 头通过魔数校验），全部 7 个用例通过。
- **文档**：`AGENTS.md` 同步更新回复格式与输出层描述；本日志追加本条。

---

> **更正声明（2026-08-06）**：此前部分文档曾将 GUI 渲染崩溃、TTS 异常归咎于「macOS 27 不稳定 / Metal 不兼容」。经核实，macOS 27 适配良好——GUI 崩溃根因为渲染代码 bug（已在 gui.py 修复），TTS 异常为本机代理导致 Voicebox 连不上 HuggingFace（已通过改用本地 Voicebox 解决）。特此更正，后续文档不再归咎系统。

## 2026-08-06 — 治理清理：去除项目内模型下载、统一文档与安装指南

### 1. 删除的过时文件（git rm，未提交）
- `AGENTS.en.md`：陈旧英文孤儿文档。
- `DEPLOY_GUIDE.txt` / `new_computer_download/DEPLOY_GUIDE_NEW.md`：旧模型/GGUF 下载指南，已无用途。
- `build.py`：PyInstaller Windows 打包辅助（开发期暂不需要）。
- `fix_install.py`：Windows-only PyAudio/llama-cpp-python 修复（Windows 开发机已弃用）。
- `download_models.py`：仅下载 Qwen3-TTS 权重到 `models/qwen_tts/`，默认 Voicebox 路径不需要。
- `voices/zh_vo_Main_Linaxita_2_1_10_26.wav`：旧 TTS 克隆音色（仅保留 `silverwalf_voice.wav`）。

### 2. 代码 / 脚本
- `src/audio/qwen_tts.py`：移除对 `download_models.py` 的子进程调用；权重缺失时改由运行时在线拉取或系统 TTS 兜底。清理残留注释。
- `new_computer_download/models_config.json`：重写为说明型（模型由 LM Studio/Voicebox 管理，不再含 GGUF/TTS 条目）。
- `new_computer_download/setup_new_computer.py`：删除全部模型下载代码（步骤 4 改为「外部 AI 软件加载指引」）；仅保留 embedding 模型预下载为项目内合法下载；镜像/回退逻辑保留。

### 3. 文档（一次性重写 / 新建，满足文档同步硬规定）
- `AGENTS.md`：愿景改为「强人工智能管家」（智能眼镜/MR 仅为外设）；删除 `models/` GGUF 段与已删文件引用；新增「文档同步硬性规定」段——四类文档 {README, AGENTS, CHANGELOG, codingLOG} 随改动同步，且 Agent 查看改动时必读 `CHANGELOG.md` + `codingLOG.md`；补 macOS 27 更正说明。
- `README.md`：整篇重写为双语（英文在前、中文在后）；愿景/平台/TTS/模型/macOS 27 均按治理口径。
- `new_computer_download/READMEfirst.md`（**新建**）：双语安装首页——英文走官方方法；中文提供「海外源」与「国内镜像」两种方法；明确项目内不下载本地模型权重；含代理/Voicebox/HF、torch 版本、PySide6 403 回退、fastembed 钉死 0.5.1、麦克风/摄像头权限、模型标识符必须为 `qwen/qwen3.6-35b-a3b` 等排错。

### 4. 配置
- `.gitignore`：新增 `codinglog_by_awaqwq233/`（只由用户手动维护，不进仓库）；`models/*` / `models/qwen_tts/` 标注为历史遗留、权重现由外部软件管理。

### 5. 残留引用清理
- 复检全仓：`download_models.py` / `DEPLOY_GUIDE.txt` / `fix_install.py` / `build.py` 仅以「已删除/已移除」说明性文字出现，无功能性引用；`zh_vo_Main_Linaxita` 全仓无残留。

### 6. 对外官网同步（`/Users/awaqwq233/Downloads/index.html`，不在本仓库）
- 技术描述对齐治理后口径：TTS 由「GPT TTS」改为 **Voicebox 本地克隆引擎（默认 macOS）+ 系统 TTS 兜底**；视觉由「CNN 图像分析」改为 **YOLOv8 检测 + J.A.C. Brain 原生多模态理解**；眼镜 / MR 明确为**可选外设**（摄像头为默认感知源）；大脑精确为 **qwen/qwen3.6-35b-a3b（LM Studio 加载，权重不进仓库）**，移除「30–33GB 本地显存 GGUF」旧描述；新增「本地优先 AI 管家 + 主动服务」定位。
- 该文件位于用户 Downloads 目录，需手动上传/部署到官网，不纳入本仓库 git。

---

## 2026-08-06 — 大脑模型切换为 qwen/qwen3.6-35b-a3b（LM Studio，原生视觉，禁用思考）

### 1. 改动
- 大脑模型标识符从 `qwen/qwen3.5-9b` 切换为 `qwen/qwen3.6-35b-a3b`，仍走 LM Studio（`127.0.0.1:12345`）。
- 三处硬编码同步更新：
  - `src/brain/llm.py:30` 的 `self.brain_model_name`（模糊匹配首选名）。
  - `main.py:572` 与 `src/runtime.py:89` 的 `LocalBrain(..., lm_studio_model=...)`（精确匹配优先）。
- 新增防御兜底（对齐 `src/judgment/judge.py`）：`src/brain/llm.py` 的 `_query_lm_studio` 与 `_query_lm_studio_stream` 在收到 `400` 且报错含 `enable_thinking` 时，自动移除 `chat_template_kwargs` 重试一次，避免个别 LM Studio 模板不支持该参数导致大脑失声。

### 2. 保持不变（已满足需求）
- **思考模式已禁用**：两处 LM Studio 请求体里本就写死 `"chat_template_kwargs": {"enable_thinking": False}`，换模型后继续生效，直接输出内容。
- **视觉输入已支持**：`think_with_image()` 对 LM Studio 走 OpenAI 原生多模态消息（`image_url` + base64），`_init_lm_studio` 无条件 `multimodal=True`；新模型原生多模态，无需 `mmproj`。
- `model_path`（仅 `llama_cpp` 兜底用）未改动——用户模型实际运行在 LM Studio 内，本地 GGUF 不参与。

### 3. 文档
- `AGENTS.md` / `AGENTS.en.md`：默认大脑描述改为 `qwen/qwen3.6-35b-a3b`，更新"已下载未引用"状态为"已接入为默认大脑"，注明模型在 LM Studio 内运行。

### 4. 前置（用户侧）
- 在 LM Studio 加载目标模型并把**模型标识符设为 `qwen/qwen3.6-35b-a3b`**（代码精确匹配此 id）。

### 5. 验证
- 待用户侧在 LM Studio 加载后运行 `python main.py`，确认控制台打印 `[System] Current LM Studio model: qwen/qwen3.6-35b-a3b`；唤醒后问视觉问题确认多模态正常、回复无大段 thinking。

---

## 2026-08-06 — 记忆向量模型「每次启动都下载」澄清（日志误导，非真重下）

### 1. 结论
- 记忆子系统的 fastembed 向量模型（`qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q`，约 240MB）
  **已缓存在 `~/.cache/fastembed`，每次启动都在复用，并未真正重复下载**。
- 用户感知到的"每次都下载一遍"是 `src/memory/embedder.py` 启动日志文案误导：无论是否命中缓存都打印了
  "下载向量模型"字样。**无需、也无法用仓库 `.gitignore` 控制**（缓存与 `~/.jac/memory` 记忆数据均在仓库外）。

### 2. 改动（`src/memory/embedder.py`）
- 新增模块级辅助函数 `_is_model_cached(cache_path)`：扫描 `FASTEMBED_CACHE_PATH` 下是否存在 `*.onnx`，
  粗略判断模型已缓存（避免依赖具体 HF 仓库名映射）。
- 在 `_ensure_loaded()` 中、`TextEmbedding(...)` 之前插入显式二态打印：
  - 命中缓存：`向量模型已缓存于 <path>，跳过下载，直接从磁盘加载。`
  - 未命中：`未检测到本地缓存，开始从镜像下载向量模型（首次较慢，约 240MB）...`
- 软化 `_apply_hf_mirror()` 中的误导文案（去掉无条件的"下载"二字，改为"获取向量模型（已缓存则直接复用）"）。

### 3. 预下载方式（新机器 / 清过缓存后）
- 一行命令：`HF_ENDPOINT=https://hf-mirror.com python -c "from fastembed import TextEmbedding; TextEmbedding('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')"`
- 或用项目已有脚本 `new_computer_download/setup_new_computer.py`（其预下载步骤已封装同上逻辑）。

### 4. 验证
- `py_compile` 通过；`_is_model_cached` 对真实 `~/.cache/fastembed` 返回 `True`，对空/不存在路径返回 `False`。
- `.gitignore` 未改动；`FASTEMBED_CACHE_PATH` 默认值（`~/.cache/fastembed`）保持不变。

---

## 2026-08-05 — 修复 Voicebox「合成成功但 J.A.C. 播不到声音」（typ?）+ GUI 停止/复制/闪退

### 1. Voicebox 发声 bug（调用契约错误，根因坐实）
- **现象**：Voicebox 服务端合成成功，但 J.A.C. 写出的 `temp/voice/voicebox_*.wav` 被 afplay 报
  `AudioFileOpen failed ('typ?')`，且播放失败被静默吞掉 → 全程无声音、无系统兜底。
- **根因（OpenAPI /openapi.json + curl 实测 v0.5.0）**：`POST /generate` 是**异步**的，200 响应是
  `application/json`（`GenerationResponse`，含 `id`），**不是音频字节**。旧 `speak()` 把整段 JSON 当 WAV
  写入 → 文件是 JSON 文本 → afplay 打不开。次要 bug：`playback.play_wav` 把播放失败（afplay 非零退出码/
  异常）悄悄 print 掉、不报错 → `speak()` 的 `except` 兜底链永远触发不了。
- **修复**：
  - `src/audio/playback.py`：`play_wav` 改返回 `bool`（成功 True / 失败 False），保留 defensive print、不 raise。
  - `src/audio/voicebox_tts.py`：`speak()` 重写 → `/generate` 取 `id` → 新增 `_poll_audio(gen_id)` 轮询
    `GET /audio/{id}`（超时 60s，生成中 HTTP 500 重试）→ 校验 WAV 魔数（`RIFF`/`WAVE`）→ 写 `.wav` →
    `play_wav` 返回 False 时 raise 触发 `_fallback_speak`（系统 `say -v Tingting` 兜底）。
    顶部接口约定注释按实测 v0.5.0 重写。
- **验证**：managed venv 装 requests 跑临时脚本（替换 play_wav 为记录型），确认生成→轮询→写出**合法 WAV**
  （魔数通过）且 play_wav 被调用。结论：发声链路修复成功。

### 2. GUI 修复（用户需求：停止 ≠ 关窗）
- **需求**：点「停止」= 停掉 J.A.C. 运行时但 **GUI 保持打开**（控制台日志保留可复制，用于 debug）；
  只有点窗口 X 才真正退出程序。
- **控制台可复制**：`gui.py` 控制台 `QPlainTextEdit` 显式
  `setTextInteractionFlags(TextSelectableByMouse | TextSelectableByKeyboard)`；
  `_pull_logs` 在用户有选区（`textCursor().hasSelection()`）时不滚动/移动光标，避免打断复制。
- **防闪退（macOS Metal）**：之前停运行时主线程释放摄像头、但 `frame_timer`(33ms) 仍在 `_pull_frame`
  向已销毁/释放的窗口提交帧 → Metal 断言崩溃。修复：`_pull_frame` 在 `not runtime.running` 时早退；
  新增 `_safe_stop_runtime()`（先 `frame_timer.stop()` + `video_label.clear()` 释放 Metal 资源，再
  `runtime.stop()`），被「停止」按钮与 `closeEvent` 共用；`closeEvent` 用 `try/finally` 保证无论如何都
  `super().closeEvent(event)` 关窗、不崩溃。
- **边界**：新增 `self._stop_requested` 标志，处理「启动过程中点停止」——`_do_start` 完成后若其为 True
  则 `_safe_stop_runtime()`。
- **验证**：`py_compile` 三个文件通过；GUI 运行时行为（停止不关窗、控制台 Cmd+C、点 X 退出不闪退）
  需在用户 GUI 环境实测。

---

## 2026-08-05 — Voicebox 不再硬编码引擎，由 JAC 声纹绑定的模型发声

- **改动**：`VoiceboxSpeaker` 合成时**不再默认指定 `engine` 字段**。原默认 `chatterbox` 改为
  留空（`DEFAULT_ENGINE=""`、`config.voicebox_engine=""`），`POST /generate` 只传 `JAC` 声纹的
  `profile_id`，由 Voicebox 用该声纹在 App 内绑定的模型发声（即「声纹什么模型就用什么模型」）。
  仅当显式设置环境变量 `VOICEBOX_ENGINE` 时才覆盖此行为。
- **报错回退**：合成失败 / 服务未启动 / 声纹克隆失败 → 一律回退系统 TTS（macOS `say -v Tingting`），
  不阻断主程序。**顺手修复** `_fallback_speak` 缺失 `import subprocess` 的 bug（否则回退路径会 NameError）。
- **涉及文件**：`src/audio/voicebox_tts.py`、`src/utils/config.py`、`tests/test_voicebox_speaker.py`
  （新增「默认不传 engine」与「显式设置才传 engine」两个用例，共 7 passed）、`AGENTS.md`、`README.md`。

---

## 2026-08-05 — 新增 Voicebox 开源克隆 TTS，替代 macOS 上出 bug 的 Qwen3-TTS

- **动机**：Qwen3-TTS 在 macOS（无 NVIDIA GPU）推理数值错乱（见下条），合成「外星人噪音」；
  开源项目 voicebox.sh 是一个本地优先的 TTS 聚合 App（Tauri/Rust），以 REST API
  （默认 `http://127.0.0.1:17493`）对外服务，内部 Chatterbox 引擎在 macOS(MLX) 上支持
  中文 + 声音克隆，正好替代。
- **新增文件**：
  - `src/audio/voicebox_tts.py`：`VoiceboxSpeaker`，走 Voicebox REST API：
    `GET /health` 探活 → 自动建/复用名为 **JAC** 的克隆声纹（用 `voices/silverwalf_voice.wav`）
    → `POST /generate` 拿 WAV 用 `afplay` 播；8 种情绪映射成 Chatterbox Turbo 副语言标签
    （`[laugh]/[sigh]/[gasp]/[excited]/[whisper]`）+ instruct 自然语言指令；失败回退系统 TTS。
  - `src/audio/playback.py`：抽出共享 `play_wav`（原在 `qwen_tts.py`），Qwen3-TTS 与 Voicebox 共用。
  - `src/audio/speaker_factory.py`：`build_speaker(config)` 统一 TTS 选择（消除 main.py /
    runtime.py 重复逻辑），选择链 **Voicebox → Qwen3-TTS(仅 NVIDIA) → 系统 TTS 兜底**；
    另含 `preload_if_needed` 预热 Qwen 模型。
  - `tests/test_voicebox_speaker.py`：mock REST API 的单元测试（探活/克隆/情绪/降级），6 passed。
- **配置**（`src/utils/config.py`，均可用环境变量覆盖）：`use_voicebox_tts`(默认 True) /
  `voicebox_url` / `voicebox_engine`(默认 chatterbox) / `voicebox_profile_name`(JAC) /
  `voicebox_ref_wav` / `voicebox_ref_text` / `voicebox_language`(zh) / `voicebox_fallback_voice`(Tingting)。
- **接入**：`main.py` 与 `runtime.py` 的 speaker 选择统一改为 `build_speaker(config)`；
  macOS 上 Qwen 仍禁用，由 Voicebox 接管；Voicebox 未启动则自动回退系统 `say -v Tingting`。
- **已知风险**：Chatterbox 偏英文，macOS 上中文克隆音质可能不理想；已做成 `VOICEBOX_ENGINE`
  可切换 + 系统 TTS 兜底，真跑起来若中文不行可换引擎或回退。
- **App 内设置**：打开 Voicebox App（或 `docker compose up` 起无 GUI 后端），默认监听 17493；
  在 App 内确保已下载/启用一个支持中文+克隆的引擎（如 Chatterbox / Chatterbox Turbo），
  J.A.C. 启动时会自动建 JAC 声纹并上传 `voices/silverwalf_voice.wav` 做克隆。详见 README.md。

## 2026-08-05 — 结论：Qwen3-TTS 在 macOS（无 NVIDIA GPU）上不可用，改为平台分流

- **诊断铁证**：在声码器 `F.embedding` 处抓取 talker 生成的原始 audio codes，统计分布：
  中/英文 codes 的 norm_entropy≈0.9（越接近 1.0 越均匀随机）、unique≈2048/2048、top-10 占比仅 12%，
  证明 talker 输出的是**无语义的随机序列**，声码器忠实合成即"外星人噪音"。
- **根因**：官方 qwen-tts 0.1.1 **仅验证 CUDA + bfloat16（NVIDIA GPU）**；Apple Silicon 无 NVIDIA 卡，
  CPU(fp32 不崩但噪声) / CPU(bf16 采样 NaN 崩) / MPS(极慢且不稳) 均跑不对。属**环境不匹配**，
  非模型文件损坏、非中文前端、非越界。
- **推翻 08-05 早些时候的"越界 clamp 修复"判断**：之前的 `_patch_multinomial` / `_patch_embedding_clamp` /
  `_force_eager` 都只"防崩溃"，codes 本身仍随机 → 声音永远噪，属治标不治本（补丁保留，无害）。
- **决策（按平台分流，符合项目架构：重推理上服务器）**：
  - macOS：默认禁用 Qwen3-TTS（`QwenTTSSpeaker.available=False`），回退系统 TTS（say / pyttsx3）。
  - Windows / Linux（未来带 NVIDIA GPU 的服务器）：仍启用 Qwen3-TTS。
  - 强制开关：`QWEN_TTS_FORCE=1` 可在 Mac 上强制尝试 Qwen3-TTS。
  - 改动文件：`src/audio/qwen_tts.py`（`__init__` 新增 IS_MACOS 分流分支）。
- **用户偏好重申**：尽量不动 torch 版本（之前改 torch 引出过 MPS 崩溃等 bug）。

## 2026-08-05 — 早前（已推翻）根治 Qwen3-TTS「外星人语音/噪音」：audio codes 越界 clamp（不动 torch）

- **问题**：Qwen3-TTS 合成不崩溃但输出无语义的"外星人说话"噪音，听不清内容。
- **根因（推翻了 08-04 的判断）**：
  1. 上一轮加的 forward hook（NaN 归零）+ pooling patch **过度破坏内容**——实测在 torch 2.9.x + CPU(float32) + eager 下，模型前向 **NaN 比例仅 0.00%**，并非 NaN 问题。
  2. 真正元凶：talker 自回归生成的多层音频 code 中**偶发越界索引**（如某层 codebook=2048 却收到 3063），导致声码器 `F.embedding` 解码时 `IndexError: index out of range`；越界被 forward hook 归零后，codes 勉强落回范围却内容错乱 → 噪音。越界比例极低（全程仅 2 次 / 数千次）。
- **修复（全部在 src/audio/qwen_tts.py，运行时 monkey-patch，不动 torch、不改 venv 包）**：
  - 删除 `_install_nan_guard`（forward hook 归零）与 `_patch_attentive_pooling_softmax`——二者是噪音元凶。
  - 新增 `_patch_embedding_clamp()`：模块加载时替换 `torch.nn.functional.embedding`，对越界索引夹回 `[0, num_embeddings-1]`，修复声码器解码越界且不破坏正常语音。
  - 保留 `_patch_multinomial()`（防 SDPA 路径偶发 NaN 崩溃）与 CPU 强制 eager attention。
- **验证**：正式链路生成 `temp/voice/final_cn.wav`（中文 clone，23.9s）频谱质心 1809Hz、峰值 0.93（正常语音特征，对比噪音段 1466Hz/0.46）；英文 `diag_clip_en.wav` 亦成功。

## 2026-08-04 — 根治 Qwen3-TTS 合成 NaN（不动 torch 版本）

- **问题**：上一轮改 float32 后 Qwen3-TTS 仍报 `probability tensor contains inf, nan or element < 0`，程序回退系统 TTS。
- **根因（两个 NaN 源，均在外部包内）**：
  1. 主生成路径 `Qwen3TTSTalkerAttention`/`Qwen3TTSAttention` 默认走 **SDPA**，在 MPS/CPU 数值不稳产生 NaN；eager 路径（float32 softmax）才稳。
  2. 说话人编码 `AttentiveStatisticsPooling` 用裸 `F.softmax(attention, dim=2)`，masked 全 `-inf` 行产 NaN 污染 x-vector。
  - 之前改 float32 只动权重精度，未切 attention 后端 → 无效；`from_pretrained` 实际能转发 `attn_implementation="eager"`（先前测试为假阴性）。
- **修复（全部在 `src/audio/qwen_tts.py` 内 monkey-patch，不碰 venv 包与 torch）**：
  1. 模块加载即 `_patch_multinomial()`：采样前把 NaN/Inf/负数归零并重新归一化，整行崩则退化均匀分布。
  2. 模型加载后 `_install_nan_guard(model.model)`：对所有子模块注册 forward hook，NaN/Inf 归零阻断传播。
  3. `_patch_attentive_pooling_softmax`：包装 `AttentiveStatisticsPooling.forward` 兜底 NaN。
  4. **仅 CPU 强制 eager**（`_force_eager` 只在 device=cpu 调用）；MPS/CUDA 走默认 SDPA + 护栏兜底。
  5. `_pick_device` 默认改 **CPU**（实测 MPS 对该 fp32 模型生成比 CPU 慢 ~6 倍）；MPS 仅 `QWEN_TTS_DEVICE=mps` 显式启用。
  6. `speak` 加 `max_new_tokens`（默认 512，可用 `QWEN_TTS_MAX_TOKENS` 覆盖），避免默认 2048 在慢设备生成十几分钟像卡死。
- **验证**：CPU 端到端合成成功（`temp/voice/qwen_*.wav`，24000Hz，约 40s 有效音频），无 NaN 报错。MPS 虽能跑但极慢，不推荐。
- **未改动**：torch / torchaudio / torchvision 版本（遵循用户要求，规避改版本回归 bug）。

## 2026-08-04 — 修复三类运行问题：TTS NaN / 大脑回吐提示词 / 停止按钮点不动

- 1. **Qwen3-TTS 合成失败（probability tensor contains inf/nan or element < 0）**
  - 根因：`src/audio/qwen_tts.py` 的 `_pick_dtype` 给 MPS 选了 `float16`；Qwen3-TTS 在 fp16 下采样语音 token 时 logits 算出 NaN/Inf → `torch.multinomial` 报错。
  - 修复：`_pick_dtype` 改为 **MPS/CPU 一律 `float32`**（CUDA 仍 `bfloat16`）。
- 2. **大脑把系统提示词当思考链吐出（视觉问答 `content` 为空）**
  - 根因：qwen3.5 在 LM Studio 上 `content` 为空、答句落在 `reasoning_content` 末尾；旧恢复逻辑取「最后一个任意括号」命中开头 `【铁律】`，把提示词回吐，真正描述在末尾被 400 字截断截掉。
  - 修复（双保险）：`src/brain/llm.py._query_lm_studio` 恢复时锁定【最后一个情绪标记】/「情绪词，」之后；`main.py.process_response` 抽取情绪与朗读文本同样取最后一个情绪标记之后，并新增 `_strip_boilerplate` 过滤提示词/自检废话行（铁律/可选：/口语化描述/再次检查 等）。已用真实坏输出样例验证：正确提取「画面正中央坐着一位戴黑框眼镜的年轻男性…」（125 字）。
- 3. **GUI 左下角「停止」按钮点不动**
  - 根因：`gui.py._toggle_run` 启动时 `start_btn.setEnabled(False)`，运行成功后 `_on_state_change` 只改文字、未重新启用 → 按钮停在禁用灰态。
  - 修复：`_on_state_change` 内补 `start_btn.setEnabled(True)`（运行/停止两态都可点）。
- 验证：四文件 `py_compile` 通过；`_pick_dtype` 实测 mps/cpu→float32、cuda→bfloat16；抽取逻辑单元验证通过。

---

## 2026-08-04 — 修复 Qwen3-TTS 不可用：torchaudio 版本漂移（2.11.0 比 torch 2.9.1 新）

- 现象：启动后日志 `[TTS] Qwen3-TTS 不可用（Could not load this library: .../torchaudio/lib/_torchaudio.abi3.so）`，
  回退系统 TTS（macOS `say`）。`import qwen_tts` 失败，因为 `qwen_tts` 顶层会 `import torchaudio`。
- 根因：`torch` 已对齐为 2.9.1，但 `torchaudio` 是 **2.11.0**（装 `qwen-tts` 时因其 `requires: torchaudio`
  **无版本锁**，pip 拉到最新版）。torchaudio 的 C 扩展按新版 torch 编译，引用 `_torch_library_impl` 符号，
  而 torch 2.9.1 的 `libtorch_cpu.dylib` 没有该符号 → `dlopen` 失败。
- 修复（本日执行）：
  1. `pip install torch==2.9.1 torchaudio==2.9.1 torchvision==0.24.1`（清华镜像；
     torch / torchvision 已满足被跳过，torchaudio 2.11.0 → 2.9.1）。
  2. `requirements.txt` 补 `torchaudio==2.9.1` 锁定（原本只锁了 torch / torchvision，漏了 torchaudio，
     这正是重装会复发的原因）。
- 验证：`import torchaudio` → 2.9.1 正常；`import qwen_tts` 成功；
  `QwenTTSSpeaker().available == True`，本地权重 `models/qwen_tts/Qwen3-TTS-12Hz-1.7B-Base` 齐全。
- 结论：torch / torchaudio / torchvision 三件套大版本必须严格一致（2.9.1 ↔ 2.9.1 ↔ 0.24.1）。

---

## 2026-08-04 — 最终更正：崩溃真凶是 torch 版本漂移（非 macOS 27 / 非 Qt），降级 2.9.1 恢复 MPS 显卡

（前两条「修正根因 MPS 禁用」「macOS 27 GUI 仍崩」为误诊记录：当时误判为系统/Qt 的 Metal 不兼容，
并加了禁用 MPS 走 CPU、`QT_RHI_BACKEND=software` 等误诊产物，**均已撤销**。真正根因见下。）

- 真凶：`.venv` 装的是 **torch 2.13.0**，与 `requirements.txt` 锁定的 **torch==2.9.1** 不一致。
  torch 被意外升级到 2.13.0 后，其在 macOS 27 上的 MPS 后端出现 regression，加载 Qwen3-TTS
  （MPS+fp16）时提交 Metal command buffer 触发 `failed assertion _status < MTLCommandBufferStatusCommitted` → abort。
  git 历史 `89dd915 优化了macOS下的模型调用逻辑，优先使用metal加速` 印证之前 MPS 在 macOS 正常过。
- 修复（用户选「降级到 2.9.1」，本日执行）：
  1. `pip install torch==2.9.1 torchvision==0.24.1`（清华镜像，cp313 wheel 装回，旧 2.13.0 卸载）。
  2. 撤销 main.py 顶部 MPS 禁用 patch，恢复 MPS 自动选择。
  3. 撤销 gui.py `run_gui` 的 `QT_RHI_BACKEND=software` / `QT_MAC_WANTS_LAYER=0`，恢复 Qt 默认 Metal。
- 验证：`py_compile gui.py main.py` 通过；`torch.__version__==2.9.1`，MPS matmul+fp16 沙箱正常。
- 现状：代码已恢复「用显卡」设计；`--console` 纯终端模式保留为可选入口。待真机（macOS 27 + 2.9.1）实跑确认。

---

## 2026-08-04 — 修正根因：`--console` 崩溃是 PyTorch MPS（非 Qt）+ 全局禁用 MPS 兜底

- 现象：用户跑 `python main.py --console`（纯终端，不加载 Qt）依然 `abort`，崩溃发生在
  `加载 Qwen3-TTS 模型 (device=mps, dtype=torch.float16)` 与 `Whisper ... 运行设备: mps` 之后，
  报 `failed assertion _status < MTLCommandBufferStatusCommitted`。
- 根因（关键修正）：前几轮误判为 Qt/OpenCV 窗口的 Metal。但 `--console` 根本不碰 Qt/OpenCV 窗口，
  仍崩在模型加载阶段 → 真凶是 **PyTorch 的 MPS（Metal Performance Shaders）后端**：macOS 27 beta 上
  把模型张量提交到 MPS 设备即触发同一 Metal command buffer 断言。机器上有两个独立 Metal 崩溃源：
  ① Qt 窗口 CAMetalLayer 呈现层（`--console` 已规避）② PyTorch MPS 模型加载/推理（本次修复）。
- 修复：`main.py` 顶部、任何 `import torch`/子模块之前注入全局 MPS 禁用：
  ```python
  import os, torch
  if os.environ.get("JAC_ENABLE_MPS", "0") != "1":
      torch.backends.mps.is_available = lambda: False
      if hasattr(torch.backends.mps, "is_built"):
          torch.backends.mps.is_built = lambda: False
  ```
  覆盖全部设备自动选择（stt.py / qwen_tts.py / ultralytics-YOLO 均用 `torch.backends.mps.is_available()`）。
  默认禁用 MPS → 强制本地模型走 **CPU**；dtype 随之降为 float32（qwen_tts._pick_dtype 的 cpu 分支返回
  float32），避免 CPU 不支持 fp16 而二次崩溃。正常 macOS 设 `JAC_ENABLE_MPS=1` 可重开 MPS 加速。
- 验证：`py_compile main.py` 通过；patch 形式验证 `is_available()` 返回 False。
- 代价：CPU 推理明显慢于 MPS（尤其 Qwen3-TTS 1.7B 与 YOLO 实时检测），但稳定不崩。
  GUI 窗口的 Metal 崩溃（源①）仍待 PySide6 出 macOS-27 兼容版或改 Web(MJPEG)方案。

---

## 2026-08-04 — macOS 27 GUI 仍崩：确认 Qt 无法规避 Metal + 新增纯终端模式（可靠兜底）

- 现象：上一轮加 `QT_RHI_BACKEND=software`（保留 `QT_MAC_WANTS_LAYER=1`）后，用户（macOS 27 beta 4）
  点启动仍 `abort`；Apple 崩溃报告明确 `Triggered by Thread: 65, Dispatch Queue: metal gpu stream`
  → **Metal RHI 仍在运行**，说明 software 后端没真正生效 / 不够。
- 根因（最终确认）：
  1. 之前用的是 `os.environ.setdefault(...)`，**只在变量未设置时才写**；若 shell 已导出
     `QT_RHI_BACKEND` 则被覆盖，software 后端从未真正启用。
  2. 即便 RHI=software，macOS 上 Qt 6 默认把窗口设为 **CAMetalLayer(Metal)** 呈现层，
     画面提交到屏幕时仍走 Metal → 断言 `abort`。`QT_MAC_WANTS_LAYER=1` 反而**强制开启**了
     layer-backing，等于把 Metal 路铺好。
  3. 结论：**Qt 6.11.1 在 macOS 27 beta 上无法稳定规避 Metal**，GUI 窗口在该系统短期无解
     （pip 上 PySide6 最新仅 6.11.1，官方 wheel 未跟进 macOS 27）。
- 修复一（`gui.py` `run_gui`，最后再试一次 GUI）：改为**直接赋值** `os.environ["QT_RHI_BACKEND"]="software"`
  （不被 shell 变量覆盖），并把 `QT_MAC_WANTS_LAYER` 翻成 `"0"`（关闭 layer-backed，走旧版
  CPU/NSGraphicsContext 路径，从根避开 CAMetalLayer 呈现崩溃）。保留为「尽力一试」，不保证在 beta 上成功。
- 修复二（`main.py`，**保证可用**的可靠路径）：新增纯终端模式 `--console` / `--headless`，
  完全不创建任何窗口（不加载 Qt、不调用 `cv2.imshow`），零渲染、零 Metal。功能通过
  **语音唤醒 + 控制台 stdin 输入文字回车** 完成；退出用 `Ctrl+C`。
  - 新增模块级 `DISPLAY_ENABLED` 开关；`main()` 主循环里 `cv2.imshow`/`cv2.waitKey` 整块按
    `DISPLAY_ENABLED` 跳过（纯终端模式改 `time.sleep(0.005)` 维持循环）；`finally` 里
    `cv2.destroyAllWindows()` 同样按开关守卫。`__main__` 解析 `--console`/`--headless` 后置
    `DISPLAY_ENABLED=False` 再调 `main()`。
- 验证：`main.py`/`gui.py` 均 `py_compile` 通过；`python main.py --console` 冒烟测试
  （沙箱无摄像头）正常打印横幅、未加载 Qt、未触发 Metal 崩溃，确认纯终端模式可用。
- 最终建议：macOS 27 beta 上直接 `python main.py --console` 使用 J.A.C.；
  想用 GUI 窗口就等 PySide6 出 macOS-27 兼容版，或后续把界面换成「本地 Web 服务 + 浏览器」
  （完全不经过 Qt/OpenCV Metal）。

---

## 2026-08-04 — 修复 macOS 27 beta 系统级 Metal 崩溃（强制 CPU 软件渲染）

- 现象：窗口刚弹出（尚未点「启动」）即 `zsh: abort`，终端仍是
  `failed assertion _status < MTLCommandBufferStatusCommitted ... [IOGPUMetalCommandBuffer setCurrentCommandEncoder:]`。
  此时 `QT_MAC_WANTS_LAYER=1` 已设但仍崩，说明崩在 Qt 自己用 Metal 合成窗口
  （背景/圆角/阴影）阶段，与业务 `paintEvent` 无关。用户系统为 **macOS 27 beta 4**。
- 根因：Qt 6 在 macOS 上默认把 2D 绘制后端设为 **Metal**；macOS 27 beta 改变了 Metal
  行为，使 Qt 的 Metal 命令缓冲断言 `abort`。pip 上 PySide6 最新即 **6.11.1**（2025 年版本），
  官方 wheel 尚未跟进 macOS 27，靠升级 Qt 短期无解。
- 修复（`gui.py` `run_gui`）：在 `QApplication` 实例化前设置
  `os.environ["QT_RHI_BACKEND"] = "software"`，强制 Qt RHI 走**纯 CPU 软件光栅化**，
  完全不经过 Metal，从根上规避该崩溃；保留 `QT_MAC_WANTS_LAYER=1`（图层合成与 RHI
  绘制后端相互独立）。可用 `export QT_RHI_BACKEND=metal|opengl` 覆盖回默认以排查。
- 验证：`py_compile` 通过；沙箱 offscreen 平台下 `QT_RHI_BACKEND=software` 被 Qt 6.11.1
  正常接受（QApplication+QLabel+setPixmap 无报错）。真实 Metal 崩溃只能在本机（有显示 +
  macOS 27）确认，但 software 路径理论 100% 避开 Metal。

## 2026-08-04 — 修复 macOS GUI 点击「启动」后未响应 + 闪退（Metal 二次崩溃）

- 现象：首轮修复后窗口能弹出，但点「启动」后界面「未响应」，随后闪退，终端仍打印
  `failed assertion _status < MTLCommandBufferStatusCommitted ... [IOGPUMetalCommandBuffer setCurrentCommandEncoder:]`，
  这次**没有了** `QPixmap::scaled: Pixmap is a null pixmap`（说明空 pixmap 已拦住），崩在真实帧绘制阶段。
- 根因（三重）：
  1. `RoundedVideoLabel.paintEvent` 仍调用 `pix.scaled(self.size(), ...)` 做二次缩放；
     macOS Metal 后端对 `QPixmap.scaled()` 有已知断言崩溃，真实帧到来即触发 `abort`。
  2. `_pull_frame` 里 `pix.scaled(target, ...)` 又缩放一次 → 双重 `scaled`，放大崩溃概率。
  3. `JACRuntime.start` 是**同步**在 GUI 主线程执行（摄像头 + YOLO + Whisper + Qwen3-TTS +
     记忆加载一大串重活），主线程被长时间锁住 → macOS 判「未响应」。
- 修复（`gui.py`）：
  1. `run_gui` 在 `QApplication` 实例化前设置 `os.environ["QT_MAC_WANTS_LAYER"] = "1"`，
     强制 CALayer 合成后端，规避 Qt 6 在 macOS 的 Metal 命令缓冲断言崩溃（标准 workaround）。
  2. `paintEvent` 去掉 `scaled()`，改为手动计算等比矩形 + `painter.drawPixmap(x, y, dw, dh, pix)`，
     不再对 QPixmap 做缩放。
  3. `_pull_frame` 不再 `scaled`，直接 `setPixmap(pix)`，缩放完全交给 `paintEvent`，消除双重缩放。
  4. `_toggle_run` 把 `runtime.start` 放进后台 daemon 线程；按钮先置「启动中…」并禁用，
     启动失败时跨线程 `QTimer.singleShot(0, ...)` 回主线程恢复按钮，避免主线程阻塞导致「未响应」。
- 验证：`py_compile` 通过；逻辑上已消除全部 `QPixmap.scaled()` 调用（Metal 崩溃触发点），
  且启动不再阻塞主线程。

## 2026-08-04 — 修复 macOS GUI 启动即崩溃（Metal 断言 abort）

- 现象：`python main.py` 在 GUI 模式启动即闪退，终端末尾打印
  `QPixmap::scaled: Pixmap is a null pixmap` 后紧跟
  `failed assertion _status < MTLCommandBufferStatusCommitted ... [IOGPUMetalCommandBuffer setCurrentCommandEncoder:]`，
  进程 `zsh: abort`。崩溃发生在窗口首次绘制阶段，尚未点击「启动」。
- 根因：PySide6 6.11.1 的 `QLabel.pixmap()` 在未设置 pixmap 时返回的是**空 QPixmap**
  （而非 `None`）。`RoundedVideoLabel.paintEvent` 原只判 `if pix is None`，于是空
  pixmap 被 `scaled()` 送进 macOS Metal 后端渲染，触发断言 `abort`。
- 修复（`gui.py`）：
  1. `RoundedVideoLabel.paintEvent`：判断改为 `if pix is None or pix.isNull():`，
     空 pixmap 时退回默认 `super().paintEvent(event)`，不再缩放空图。
  2. `_pull_frame`：对空帧/非法尺寸/非连续内存/空 QImage/空 pixmap 逐层防御，
     任何一环为空都跳过本次绘制，绝不把空 pixmap 交给 Metal。
  3. 顶部新增 `import numpy as np`，用于检测帧内存连续性（`np.ascontiguousarray`）。
- 验证：`py_compile` 通过。逻辑上消除了唯一一处对可能为空 pixmap 的 `scaled()` 调用，
  正是触发 Metal 断言的那一行；其余 QLabel 均不涉及 pixmap 绘制。

## 2026-08-04 — 运行时四类问题修复（控制台实跑反馈）

用户 bo s s 在 macOS 实跑 `main.py` 控制台后反馈 4 类问题，本次修复 3 类（回声问题留 TODO）。

### 修复 1：Qwen3-TTS 仍不可用（回归）
- 根因：从 Windows 开发机拷回项目后，`src/audio/qwen_tts.py` 的 `ensure_qwen_tts()` 的
  `def` 行再次丢失，整个函数体（docstring + 自动安装/权重下载逻辑）被吞进 `play_wav`
  函数体内（缩进恰好落在 play_wav 内，故 `py_compile` 不报错，但模块无该属性）。
  `main._load_qwen_tts` 调 `qt.ensure_qwen_tts()` 抛 `AttributeError`，TTS 永远回退系统。
- 改动：在 `play_wav` 之后补回 `def ensure_qwen_tts(autoinstall=True, autodownload=False):`，
  原函数体缩进不变即正确成为模块级函数。AST 已确认其为顶层函数。
- 验证：`.venv` 已装 `qwen-tts` 0.1.1，本地权重 `models/qwen_tts/Qwen3-TTS-12Hz-1.7B-Base`
  已从 Windows 拷来且完整（model.safetensors 3.8GB + speech_tokenizer 齐全）。修复后
  首次启动应自动探测并启用 Qwen3-TTS（情绪/声音克隆）。

### 修复 2：[Embedder] 每轮对话刷屏
- 根因：`src/memory/embedder.py` 的 `MemoryEmbedder._ensure_loaded` 失败后没有"只试一次"
  的熔断，`self._model` 仍 None，导致每轮对话（记忆检索 + 记录各一次 embed）都重新
  连接 HuggingFace 并尝试加载、并打印镜像信息与失败原因。
- 改动：`__init__` 新增 `self._load_attempted=False`；`_ensure_loaded` 开头若已尝试过
  直接返回缓存的 `available`，不再重试、不再打印。加载失败仅首次打印一次，之后静默
  降级关键词检索。
- 验证：managed python 实测——连续 3 次 `embed_texts` 仅首次打印（HF 镜像提示 + fastembed
  不可用），后两次静默返回 None，符合预期。

#### 修复 2 补全：向量模型权重实际下载 + 缓存持久化（同日后续）
- 用户实际装的是 `fastembed==0.8.0`（新版），内部把模型文件映射成 `onnx/model.onnx`，
  而 HF 镜像仓库（Qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q）只有
  `model_optimized.onnx`，下到第 5 个文件即 404 → `Could not load model ... from any source`。
- 已在 `.venv` 把 fastembed 锁回 `0.5.1`（用 `model_optimized.onnx`，匹配镜像），并同步把
  `requirements.txt` 第 106 行锁版本。重装后通过 `HF_ENDPOINT=https://hf-mirror.com` 拉取成功，
  维度 384。
- **缓存持久化（关键）**：fastembed 0.5.1 在未设 `FASTEMBED_CACHE_PATH` 时，默认把权重放进
  系统临时目录（macOS 是 `/var/folders/.../T/fastembed_cache`），重启/清临时后会被清掉，导致
  每次启动都重新下载。在 `embedder.py::_apply_hf_mirror` 中新增：未显式设置时把
  `FASTEMBED_CACHE_PATH` 固定为 `~/.cache/fastembed`。已把已下载权重从临时目录搬到该持久目录，
  验证从持久路径加载、`available=True`、不再刷屏、不重复下载。

### 修复 3：大脑返回为空导致跳过回复
- 根因：非视觉查询走 `brain.think_stream`（LM Studio SSE 流式）。当 LM Studio 并发/繁忙
  偶发返回空 choice 时，`_query_lm_studio_stream` 没有任何兜底，生成器不产出文本，
  `full_response` 为空 → `main.process_response` 打印「大脑返回为空，跳过回复」。
- 改动：
  - `src/brain/llm.py::_query_lm_studio_stream`：累计已产出文本，流结束若全程为空则
    yield 一句兜底「（刚才走神了，能再问一次吗？）」，保证非空。
  - `main.py::process_response`：流式结束后若 `full_response` 仍为空，改用非流式
    `brain.think(...)` 重试一次（非流式路径本就有空兜底），双保险。

### 未做（TODO）：回声问题
- 用户报告程序把自己读出来的话当成语音输入（TTS 输出被麦克风拾回 → 误唤醒/误识别）。
- 本次不实现，留待后续：方案为「发声期间挂起 VAD 监听 + 把刚播出的音频做声纹/波形比对
  做回声消除」，或简单在 `context.is_speaking` 期间丢弃识别结果。

---

## 2026-08-04 — 新电脑依赖补全脚本修复 & 迁移排障

### 背景
在新 Mac 上首次运行 `new_computer_download/setup_new_computer.py` 时，pip 阶段所有包整批+逐个安装失败，
日志一片红。经排查定位到以下真实问题：

- 第一次失败主要是**清华镜像临时抽风**（重跑时网络已恢复，全部装成功）；
- `PySide6` 在清华镜像 `pypi.tuna.tsinghua.edu.cn` 对大 wheel 返回 **403 Forbidden**，是唯一真正装不上的包
  （但迁移残留的副本仍可 `import`，版本 6.11.1，GUI 可用）；
- 脚本自检把 TTS 模型路径写错（去 `models/` 根找，实际在 `models/qwen_tts/`），导致「模型缺失」误报；
- 缺 `sox`（音频处理可选依赖，whisper/soundfile 会用到）与 `cmake`（`llama-cpp-python==0.3.26`
  在 Python 3.13 上需源码编译）。

### 脚本改动 `new_computer_download/setup_new_computer.py`
1. **pip 失败可见性**：新增 `_log_pip_error()`，安装失败时打印 pip stderr 关键尾部（去 ANSI 颜色），
   不再静默吞错，便于定位 403 / 超时 / 编译错误。
2. **失败包自动回退官方源**：`_pip_install()` 在整批→逐个均失败后，对残余失败包用官方源
   `https://pypi.org/simple` 再重试一次（解决 PySide6 清华 403）。`step_ffmpeg()` 安装 imageio-ffmpeg
   也加了同样的官方源回退。
3. **系统依赖补全**：`step_system()` 在 macOS / Linux 额外安装 `sox`（音频转换）与 `cmake`
   （llama-cpp 源码编译），消除「SoX could not be found」警告并保障 llama-cpp-python 构建。
4. **模型自检路径修正**：`step_verify()` 的 TTS 模型检查改为多候选路径
   （`models/qwen_tts/Qwen3-TTS-12Hz-1.7B-Base` 优先，兼容 `models/` 根），消除误报。
5. **文档**：模块 docstring「网络问题应对」段补充官方源回退说明。

### 运行前置（迁移后必读）
- 激活虚拟环境后再运行：`source .venv/bin/activate`（见说明：venv 隔离依赖，激活后 `python`/`pip`
  才指向项目里的解释器与已装的 19 个包）。
- 启动 **LM Studio** 并加载 `Qwen3.5-9B` 到 `127.0.0.1:12345`（默认 `backend="lm_studio"`）。
- 记忆向量模型可选预下载：`python new_computer_download/setup_new_computer.py --only embed`。

---

> 早期改动（架构/模型迁移、TTS 切换等）见 `codingLOG.md` 与 `AGENTS.md`，本文件只记录具体代码/脚本修改。
