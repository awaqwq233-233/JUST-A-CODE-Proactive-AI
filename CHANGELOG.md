# 修改日志 (Changelog)

记录 J.A.C. 项目对代码 / 脚本 / 配置的实际改动。最新改动在最上方。

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
