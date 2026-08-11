# 修改日志 (Changelog)

记录 J.A.C. 项目对代码 / 脚本 / 配置的实际改动。最新改动在最上方。

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
