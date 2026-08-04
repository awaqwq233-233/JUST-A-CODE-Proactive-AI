# 修改日志 (Changelog)

记录 J.A.C. 项目对代码 / 脚本 / 配置的实际改动。最新改动在最上方。

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
