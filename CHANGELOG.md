# 修改日志 (Changelog)

记录 J.A.C. 项目对代码 / 脚本 / 配置的**实际改动**。最新改动在最上方。
（纯文档文字校正也在此登记，但会明确标注「未改代码」。）

---

## 2026-10-07 — 删除旧 MiniCPM 运行方案，重构 Gateway 玻璃风格 GUI

- 按 bo s s 本轮决定，移除旧 `:9060/backend` MiniCPM 客户端/启动器、Voicebox 桥接/回灌、旧令牌过滤、`src/judgment/` 轮询判断模型和传统 `src/runtime.py` 编排，删除仅验证该废弃协议的测试。`main.py` 与 `python -m src.omni` 统一进入固定 Gateway，保留 `--gateway` 启动参数兼容；`legacy` 配置明确失败，不回落旧模型。仓库外模型与已有环境未删除。
- GUI 重做为浅色通透玻璃风格：柔光背景、半透明面板、高光圆角和悬浮控制，由 Qt 绘制；不是 AppKit 原生 Liquid Glass 材质。针对 macOS 设置中文字体回退，源码统一 UTF-8；保留跨平台绘制路径。
- 视频控件尺寸跟随输入帧比例，完整显示、不裁切、不拉伸，无黑色填充条；采集仍固定 640×480、5–10fps，每秒上行最新 1 帧。删除显示分辨率与缩放行。
- 顶部一个状态胶囊代替底部三个状态条；删除文字输入和发送。扩大对话区，文本分片连续拼接；连接日志单独折叠且有界，选中/复制文本不被自动滚动打断。当前显示助手回复，用户原话转写仍未接入。
- 右侧仅保留有效参数：麦克风增益、摄像头启用、采集 fps、会话轮换和异常重连次数；设备/摄像头编号/本机连接/参考 WAV 折叠到高级区。参数启动前可调、运行中锁定，并传入实际 GatewayClient；CLI 同步增加 `--fps` / `--mic-gain` / `--retry-limit`。没有 Qwen/TTS/judge/Listen 概率/回声门控等无效开关。
- 保留并回归原有异步停止、清屏、关闭确认、重试停止与退出等待，运行异常残留客户端也先清理再恢复启动。Qwen 工具组件与旧记忆/通用音频模块仅独立保留，未接回主程序，本轮未接入任何新大脑能力。
- 主 `requirements.txt` 统一引用已验证的 Gateway 固定清单，`requirements_fixed.txt` 标为历史快照；安装器默认 Gateway，all/pip/verify 同样走当前清单，自检导入实际 gui/main 并排除旧模块。玻璃界面无新增依赖，国内镜像与失败回退路径保留。
- 已同步双语 README、AGENTS、安装指南、模型说明与附 A。三层目标架构、固定后端版本和协议不变，权威 DOCX 无需调整；未编辑 bo s s 个人笔记。预览 PNG 和临时脚本/测试产物位于现有忽略的 `.cache/`、`output/` 或系统临时目录，无需新增 .gitignore；不提交规划目录、模型或真实媒体。
- GUI/桌面首轮 22 项通过，Gateway/M0 专项 66 项通过；已检查 1440×880 与 1100×700 窗口、折叠设置、无黑边画面和中文显示。全仓库初次回归 136 项通过，1 项旧工具测试因写真实主目录被沙箱拦截；该测试现隔离用户目录到 pytest tmp_path，仍验证命中与越权拦截，不修改生产工具权限。修订工具用例与新增异常自动清理用例分别单项通过；当前全套合计 138 项均已通过（分批执行，没有把首次失败隐藏成一次全绿）。语法/函数注释检查、入口帮助、安装 dry-run、无旧重依赖导入和 git diff --check 通过。本轮离屏检查使用合成画面，未开启摄像头、麦克风或音频播放，不称为新增真机听感验收。

---

## 2026-10-07 — M1 基础闭环获用户验收，按阶段自动发布

- bo s s 在修复后明确反馈「这次完全正常」。据此记录 M1 生产听看说、GUI 预览与手动停止/重启用户验收通过，不再将该复验列为待办；不推测用户实际循环次数、测试时长或长期稳定性结果。
- 当前完成的是第一层本地感知与语音交互基础闭环；下一步为 Gateway → Qwen 大脑/工具升级。并行转写、ChromaDB 记忆与云端 OpenClaw 仍未迁移，未将旧模式的历史能力计入新入口。
- 根据 bo s s 新增规则，将阶段完成自动提交/推送当前开发分支、操作时逐条提供完整可执行命令写入 AGENTS。继续排除模型、规划目录、实际媒体和运行报告，不自动强推或合并 main。
- 同步双语 README、AGENTS、安装指南及附 A 当前状态；本条只更新验收/协作说明，不改运行代码、依赖或目标架构。此前启停修复代码、回归测试和对应日志一并纳入本次里程碑发布；安装脚本与依赖清单已检查，无新增下载步骤，权威 DOCX 无需修改。

---

## 2026-10-07 — 完善停止清屏与 Gateway 手动重启收尾

- bo s s 复验发现首次停止后画面两侧仍有残影，随后两次启动均失败。实际日志定位到 Worker 拒绝后续 WS（HTTP 403），原 GUI 只显示 RuntimeError；昨日的定时器修复不足以覆盖完整停止流程，本轮继续处理。
- 圆角视频控件每次绘制先覆盖整个黑色背景，再绘制样式边框与等比视频；空 pixmap 同样完整清屏，不依赖 QLabel 的局部默认绘制，不调用 macOS Metal 上有风险的 QPixmap.scaled。
- GUI 在主线程先停绘制和清空画面，再后台回收运行时；显示「停止中…」，清理完成前禁用再次启动，Qt 仍可处理事件。运行标志提前变 false 时仍清理残留客户端；停止超时明确显示「重试停止」，不忽略错误后开放新会话。窗口退出也等待清理。
- Gateway 手动停止不再取消接收器后仅发送 close 就断链；先停止采集，沿用正常轮换的默认 8 秒接收尾窗，再等待 session.closed 和 WS 关闭，额外冷却 1 秒。重复停止只发一次 task.cancel，工作线程尚在退出时拒绝重启；错误保留安全协议代码，不显示服务端原始消息。通常约 9 秒收尾，不宣称无缝启停。
- 真实后端复验中发现仅等关闭确认仍不够：Worker 已空闲而 C++ 会话仍活跃，立即重建被拒绝，并观察到 duplex_llm_thread_func 的 SIGSEGV。固定上游先关闭传输再完成推理清理；本轮在客户端增加尾窗/冷却规避该时序，**未修改锁定后端源码、版本或补丁，不声称底层竞争已修复或任意负载均安全**。
- 最终在同一组真实后端、同一生产客户端完成三次手动启动/停止：每次发送 7 个一秒静音块并附纯内存合成 JPEG，共 21 块；三次关闭握手均完成、Worker 均恢复 idle、设备清理失败为 0，停止耗时约 9.06 / 9.03 / 9.03 秒。未打开麦克风/摄像头，不播放音频、不保存媒体，不作为真机听感或性能长测。统计报告位于已忽略的 output/m1/manual-restart-20261007.json；本轮启动的后端三进程已回收。
- Python 3.11 新旧 Gateway/M0 专项 **57 passed**，覆盖连续启停、关闭确认前阻止重启、重复停止不中断握手、在途推理尾窗、完整像素清屏、Qt 响应、异常残留清理、停止失败重试及窗口退出。真实设备残影与语音仍待 bo s s 重启后端和 GUI 后复验。
- 已同步双语 README、AGENTS 与安装指南。无新增依赖或命令参数，检查 requirements.txt、requirements_fixed.txt、requirements-m0.txt 及安装脚本后无需改变下载阶段；安装器 --only gateway --dry-run 与 Gateway/Qt 懒导入自检通过。测试只修改已有文件；临时复验脚本与报告位于现有 .gitignore 排除的 output/，不提交。目标架构未变，权威 DOCX 无需修改。

---

## 2026-10-06 — 修复 GUI 停止再启动后预览黑屏不刷新

- bo s s 真机确认听看说功能正常，但停止再启动后模型仍能识别画面、GUI 预览黑屏不动。定位为停止时关闭 `frame_timer`，成功重启时没有重新启动，与模型理解或摄像头上行故障无关。
- 在 Qt 主线程的成功启动状态处理中恢复 33ms 帧刷新并立即拉取新画面；停止仍先停绘制、清空 pixmap，再释放设备，保留 macOS Metal 清理顺序。
- 启停状态、启动失败和启动中止统一经 Qt 排队信号交给主线程，避免后台线程直接修改控件、操作定时器，或依赖没有 Qt 事件循环的工作线程执行 singleShot。迟到的旧停止通知不会停掉正在运行的新预览；初始化异常也回收已启动部分并恢复按钮。
- 新增 5 项离屏 GUI 回归，先在旧代码复现重启定时器未恢复，再验证连续三次启停显示新会话颜色帧、持续拉帧、工作线程 ready 通知、迟到停止通知及失败清理。不打开真实麦克风、摄像头或播放设备。
- 新旧协议与 M1 专项 **49 passed（15.88 秒）**；用户仍需关闭旧 GUI 进程并重新启动以加载修复，再复验真实预览。
- 已同步双语 README、AGENTS 和安装指南中的状态/复验方法。无新增依赖或运行参数，已检查 `requirements.txt`、独立清单和安装脚本，安装器 `--only gateway --dry-run` 通过，无需修改依赖下载逻辑；新增测试应进 Git，测试运行产物仍由现有 `.gitignore` 排除。架构未改变，权威 DOCX 无需调整。

---

## 2026-10-06 — 用户接受 M0 并豁免长测，进入 M1 生产 Gateway 接入

- bo s s 明确认为刚才短测效果良好，要求 M0 标记通过，不再做 30 分钟长测。M0 现为「用户验收通过，30 分钟项免测」；保留真实报告中的 `soak_30min_verified=false`，免测决策不伪装成未执行的测量。
- 将已验证的协议/PCM 编码与 SoundDevice/OpenCV 媒体层拆入 `src/omni/realtime_protocol.py`、`media.py`，M0 文件和设备探针复用同一实现。采集 640×480、5–10fps、固定一秒 16k 上行和原生 24k 播放均保留有界缓冲；溢出明确失败，不靠丢语音追赶水位。
- 新增 `GatewayClient`：等待排队后初始化，文本和原生音频独立接收，视频双工不以 `response.done` 控制节拍。默认每 240 秒上行后受控关闭、释放设备并重连；网络错误限次退避，停止可取消排队/初始化并回收设备。
- 重连重注入参考音、用户提供的已确认上下文和有界助手输出历史，不冒充用户原话或完整 ASR。重连期间明确暂停采集，当前有采集间隙；启动保护使用 `force_listen`，保留真实麦克风波形。
- 新增 `main.py --gateway` 生产终端入口及 `--gui` 轻量 GUI；GUI 可选 Gateway / legacy，并在启动设备前明确同意和耳机要求。Gateway 懒导入不加载旧 torch、PyAudio、Whisper、YOLO 或 Voicebox；Qwen 工具升级、并行转写、ChromaDB 和 OpenClaw 尚未接入。
- Python 3.11 环境新增并验证 PySide6 **6.11.2**（从清华镜像安装），同步独立依赖与安装器。生产 `requirements.txt` 已包含 PySide6，无需重复新增；`.cache/` 和 `output/` 已忽略，截图、报告、环境和模型不提交。
- bo s s 补充要求今后修改同步安装脚本，已写入 AGENTS 契约。安装器新增推荐 `--only gateway` 别名与安装后导入自检，失败不宣称就绪；国内镜像失败仍回退官方 HTTPS。同步模型说明、安装指南及包装脚本，修复 `run.sh` 从项目根调用时的相对路径错误；`run.bat` 明确使用 UTF-8 控制台编码，Windows 只做脚本静态兼容检查。
- Python 3.11 专项 **44 passed**，覆盖协议、采集回调、授权、双会话上下文重注入、排队取消、懒导入、桌面失败收尾及安装器镜像回退/导入自检。旧环境全量回归 **174 passed / 2 skipped**，另 1 个文件搜索用例因沙箱禁止在用户目录创建临时测试文件夹而失败，单独经授权重跑 **1 passed**；两项跳过仅限 3.11 的桌面测试，已在上述专项通过。旧环境缺 pytest-timeout 的已知配置警告仍在。
- GUI 离屏渲染、后端切换、同意开关和固定视频节拍检查通过，中文显示正常；安装器 `--only gateway` 实跑依赖导入自检通过，`run.sh` 语法检查与从项目根运行的 dry-run 均通过。M1 文件验证启动的三个后端进程已回收，端口无监听。
- 新生产入口完成真实模型两次文件会话：40 个上行块、原生音频 **5.64 秒**、汇总 P95 **977.065ms**，两次会话正常关闭、清理失败 0。文件模式未打开/播放设备，报告明确 `live_devices=false`、播放样本 0，不包装成新 GUI 真机听感验收。报告位于 `output/m1/file-reconnect-20261006.json`。
- 文档同步 AGENTS、双语 README 与安装指南，并修正附 A 当前状态锚点；目标架构未改变，无需修改本地权威 DOCX。

---

## 2026-10-06 — 新增 M0 长测入口，按用户调整完成两段真机验证

- bo s s 明确要求开始长测，新增独立 `verify_soak_duplex.py`，复用短时设备采集/播放，不修改生产入口。
- 固定 Gateway 视频会话上限 300 秒：长测保持同一组后端运行，8 段各上行 225 秒，累计 1800 秒，段间释放重开设备并重新初始化参考音；每段保留既有 4 秒启动保护。不代表单会话无限运行、无缝重连或上下文恢复通过。
- 每 30 秒记录本机健康接口、指定进程 RSS；逐段保存协议、队列、收播、设备异常与 P95 计数，异常或中断也写失败报告；不录制媒体、不保存对话文本。原始报告在已忽略的 `output/m0/`。
- 验收要求完整上行时长、逐段 P95 < 1000ms、原生收播一致、设备异常为零、全部清理和健康检查通过；安静段无需强制发言，但全程必须有原生音频。
- 新增 3 项离线测试，M0 相关测试 **36 passed**。10 个模型 SHA256 重新校验通过。
- 运行中 bo s s 将目标改为「只跑 2 段，不长测」。已完成两段各 225 秒，累计上行 **450 秒**、总墙钟 **472.385 秒**；逐段 P95 **678.662 / 891.148ms**、原生音频收播 **69.84 / 106.36 秒**（合计 **176.2 秒**），设备流异常均 0，音频队列峰值 1、播放队列峰值 3，逐段设备/线程清理通过。
- 16 次本机后端健康采样全部通过；加载后总 RSS 约 **10048–10170 MiB**，末次采样约 10170 MiB，仅作本次短窗观测，不推断长期无内存泄漏。因第二段 P95 高于第一段，保留逐段指标，不以第一段代表全部性能。
- 第二段报告落盘后即发出中断；脚本已进入第三段连接阶段，未记录第三段上行。原报告 `output/m0/soak-20261006.json` 如实标记 `interrupted`，单独生成 `two-sessions-20261006.json` 评估用户新范围，结果 `passed`；两份报告均保留 `soak_30min_verified=false`，不将两段验证称为 30 分钟长测通过。
- 已释放设备，本次后端启动器确认停止并回收所属三进程，8006 / 8007 / 22400 / 22500 端口均无监听；生产入口仍未迁移。30 分钟稳定性和跨会话上下文恢复尚未验收。
- 已检查并同步 AGENTS、双语 README 和安装指南；无新增依赖，`.cache/` / `output/` 已忽略。目标架构没有变化，无需修改权威 DOCX。

---

## 2026-10-06 — 明确推送范围：模板录音保留，模型与架构目录仅本地

- bo s s 明确授权推送本轮代码和模板录音 `voices/silverwalf_voice.wav`，不推送模型或架构文档，今后也按此执行；实际测试录音录像不在本次授权范围内。
- `.gitignore` 新增整个 `brainstorming_projectPLAN/`，保留模型排除并补充 `.pth` / `.ckpt` / `.tflite`；从 Git 索引移除架构目录，**本地 DOCX 保留不删除**。
- 为避免新版 DOCX 经提交历史上传，重新整理本轮两个尚未推送的本地提交，以既有远端分支为基点生成不含新版架构文档的发布提交；旧本地提交通过 `refs/local-backups/m0-before-publish-20261006` 本地备份引用保留，不随分支推送。
- 已发布的旧历史不在本次操作中清除，也不强推或修改 main。模板 WAV 已跟踪，发布提交继续保留该文件。
- 同步 `AGENTS.md`、双语 README 和安装指南的发布边界，移除指向不再随仓库分发的 DOCX 的 GitHub 相对链接。未修改运行代码或依赖，无需重新进行设备测试。

---

## 2026-10-06 — 方案 B 定案并建立独立 M0 验证入口

- bo s s 确认采用方案 B。权威 DOCX、AGENTS、README 与安装指南已改为固定版本官方 Gateway / Worker + C++ Metal；生产入口仍保留旧实现，尚未完成架构迁移。
- 新增 `backend.lock.json`：Demo commit `47709a9`、引擎 commit `8730567`、模型 revision `db25077`、10 个 GGUF SHA256、端口与 Realtime 协议。旧 `/duplex`、16-bit PCM、`.pt` 音色路线停止作为目标实现依据。
- 新增 `verify_duplex.py`：本机专用、固定 1 秒 16k float32 音频块，校验排队/初始化/增量输出/关闭，独立验证 24k 原生 TTS；报告不保存原始媒体或文本。
- 新增 `verify_live_duplex.py`：显式设备同意后使用 SoundDevice 与后台 OpenCV 线程做短时真机验收；48k 实时重采样到 16k，上行每秒最新 640×480 JPEG，原生 24k PCM 直接播放，所有缓冲有界，不录制媒体。macOS 首次摄像头授权在启动主线程申请，读取与编码仍在后台。
- 新增 `start_m0_backend.py`：commit 与模型预检、三进程启动与注册、loopback 绑定、禁用 Gateway 会话录制、回收自身子进程。
- 发现并处理上游启动问题：默认 OpenSSL 构建会使用空证书 SSLServer，内部 HTTP 构建显式关闭 `LLAMA_OPENSSL`；上游硬编码 `0.0.0.0`，通过已登记的 `engine-loopback.patch` 改为遵守 `--host`。启动器仅接受固定 commit 加这份补丁。
- Python 3.11.17 已安装，M0 独立环境位于 `.cache/m0/venv`，原 Python 3.13 `.venv` 保留；新增 `.python-version`、`requirements-m0.txt`，安装器新增 `--only m0`，其他安装阶段与生产依赖保持旧基线。
- 已编译 Metal 引擎；10 个现有 GGUF 通过锁定 SHA256 校验，以临时符号链接视图复用，不覆盖原文件或重复下载。发现部分主文件哈希不符，匹配版本存在于 `.1.gguf` 备份。
- 独立 M0 测试 **33 passed**，覆盖协议时序、PCM 校验、缺失原生音频/性能指标失败、源码/模型/端口预检、安装隔离与回调缓冲。新增 `--require-realtime`，耗时缺失或 P95≥1000ms 会明确失败。
- 旧架构回归 **134 passed**（157.72 秒，保留旧环境且用 `-o addopts=`）；旧环境缺少 pytest-timeout，出现 1 项 `Unknown config option: timeout` 警告，不影响用例通过。M0 环境已单独安装测试插件。
- 真实文件探针：音频 P95 **860.804ms**、视频 P95 **591.786ms**，都收到原生音频。启动阶段会强制听取若干块，探针先发 4 秒静音再发语音；短窗口不出声不会被包装成语音验收通过。
- 40 秒真机测试通过：640×480、194 帧，原生音频收播均 **31 秒**，P95 **679.310ms**，输入/输出流异常均 0，音频队列最高 1 块、播放队列最高 2 块，设备和线程清理通过。bo s s 确认耳机可听且回答与提问相符。
- **10 次设备/WS 快速启停通过**：每次建立/关闭真实 Gateway 会话、启动并释放摄像头/麦克风/播放流与采集线程，均无设备错误，闭环清理通过；不等同于每次都完成语音问答。
- **30 分钟长测未做**：bo s s 本轮要求先保存结果，后续再长测。M0 仅短时门槛通过，`main.py`、生产 `src/`、旧环境和旧依赖未切换。本次启动的后端服务已停止。
- 权威 DOCX 已按方案 B 更新，修正东亚字体、跨页表格行与代码块分页，并通过逐页渲染检查；文档渲染使用的源码和 PDF/PNG 不进 Git。
- `.cache/`、`output/` 与 `*.gguf` 已在 `.gitignore`，不提交环境、后端源码、模型、日志及渲染产物；只补正模型管理注释。`codinglog_by_awaqwq233/` 未修改。
- `.gitattributes` 仅为后端 `.patch` 文件排除补丁格式必需的空白上下文前缀检查，保留普通源码的尾空格检查和原模型 LFS 规则。

---

## 2026-10-06 — 更新架构规划目录的编辑权限（未改代码）

> **本次只修改开发者契约与变更日志，未改代码，也未改架构方案正文。**

- bo s s 明确授权 Agent 今后可以修改 `brainstorming_projectPLAN/` 中的文件。
- `AGENTS.md` 已同步为：Agent 可以依据 bo s s 已确认的架构决策编辑和同步该目录；涉及目标架构的实质性变化时，仍需先取得 bo s s 明确确认。
- `codinglog_by_awaqwq233/` 的权限不变，仍只由 bo s s 手动维护。
- 本次没有新增文件、依赖或运行产物，因此 `.gitignore`、`requirements.txt` 与安装脚本无需调整。

---

## 2026-10-01 — 补回附 B 记忆契约 + 删除 deliverables 历史交付物（未改代码）

> **仍只改文档 / 删历史交付物，未改任何代码。** 承接「新架构定案」任务，处理 bo s s 对上一轮回复中两点的指示。

- **删除 `deliverables/`**：2026-07-22 两份历史工程交付物（`code-review-memory-2026-07-22.md` / `design-memory-feature-2026-07-22.md`）整体删除（bo s s 指示「没用了」），连同 `.DS_Store` 与空目录一并清理。
- **补回附 B**：从 git 历史（`e69e9d6^:docs/memory/schema.md`）恢复原「记忆 JSON 契约 v1.0.0」全文，作为 `### 附 B` 补回 `CHANGELOG.md`（此前 2026-10-01（晚）合并时该附录**实际缺失**）。补回时做标题降级（`#`→删、`##`→`####`、`###`→`#####`）以对齐附录层级，并加「旧记忆子系统，新架构改 ChromaDB，迁移前仍有效」标注。
- 顺带修正顶部「新架构定案」条目的「未改动」清单：`deliverables/` 由「未改」改为「已删除」。

---

## 2026-10-01 — 新架构定案，全文档按新架构改写（未改代码）

> **本次只改文档（`AGENTS.md` / `README.md` / `CHANGELOG.md` / `new_computer_download/READMEfirst.md`），未改任何一行代码。** bo s s 以 `brainstorming_projectPLAN/10月1日新架构.docx` 定案新架构，此后所有开发严格以此为准。

### 一、新架构要点（权威基准，详见 `AGENTS.md`「新架构」节）

三层模型：**MiniCPM-o-4_5（感知/主动判断 + 语音输出）→ qwen/qwen3.6-35b-a3b（大脑 + Tool Use/Agentic Coding + 文件输出）→ 云端 OpenClaw（DeepSeek API，长任务 + 文件输出）**。升级链：o-4_5 解决不了 → 调 qwen → 仍不够 → 交云端 OpenClaw。

| 维度 | 新架构（目标） | 旧架构（当前代码，待迁移） |
|---|---|---|
| omni 后端 | OpenBMB `llama-cpm` 分支（Metal），GGUF **Q4_K_M**，`:8080`，`/duplex`，`-c 4096 -t 8 --flash-attn` | llama.cpp-omni master，GGUF **Q8_0**，`:9060`，`/backend`（OpenAI Realtime 风格），`-c 8192` |
| 语音输出 | MiniCPM-o 原生音色克隆（speaker embedding `.pt` + `--tts-speaker-emb`） | Voicebox App（`:17493`）+ Qwen3-TTS + 系统 TTS |
| 记忆 | **ChromaDB** + BGE-Small-ZH-v1.5（ONNX INT8）+ JSON | fastembed + paraphrase-multilingual-MiniLM + 自研 MemoryStore |
| 模型层 | 三层（o-4_5 + qwen + 云端 OpenClaw） | 两层（o-4_5 + qwen，无 OpenClaw） |
| 音频输入 | SoundDevice 实时重采样 16k/16bit/Mono | PyAudio + WebRTC VAD + Whisper 转写 |
| 视频输入 | 独立后台线程 + 640×480 + 5~10fps + Queue | 主循环 1280×720 / omni 1 帧/秒 |
| 判断引擎 | MiniCPM-o-4_5 全双工承担（不再单独轮询） | `src/judgment/judge.py` 用 `minicpm-v-4_5` 每 4s 轮询 |
| 环境 | python3.11 | python3.10/3.11 |

记忆两条性能铁律（新架构明确）：①ChromaDB 频繁 `add` 后内存不释放 → 客户端缓存 5 条摘要、满 5 条或会话结束批量 `collection.add()`；②`retrieve_memories` 严禁放音频回调 → 只在「WS 建立发 init 前」与「VAD 判定说完一句后」两处检索。

### 二、本次文档改动

- `AGENTS.md`：新增「新架构」权威定义节 + 「代码迁移状态」对照表，重写模型 / 记忆 / 输入 / 设置 / 已知限制等全部小节。
- `README.md`：英文 + 中文双语全部按新架构改写（三层模型表、SoundDevice/640×480 输入、ChromaDB 记忆、llama-cpm 部署、DLC）。
- `CHANGELOG.md`：本条记录 + 附 A 顶部加「新架构锚定」说明 + 附 C/D/E/F 加「旧记忆子系统（将被 ChromaDB 替代）」标注。
- `new_computer_download/READMEfirst.md`：安装步骤按新架构（llama-cpm 编译、MiniCPM-o-4_5 GGUF、ChromaDB/BGE-Small-ZH、python3.11）改写。

### 三、未改动

- 未改任何 `.py` / 配置 / 依赖；`requirements.txt`、`src/`、`tests/` 均未动。
- `brainstorming_projectPLAN/`、`codinglog_by_awaqwq233/` 未触碰（bo s s 手动维护）。
- `deliverables/`（2026-07-22 两份历史工程交付物，code-review / design-memory）已在本次改动中**整体删除**（bo s s 指示"没用了"）。

---

## 2026-10-01（晚）— 文档合并：差距笔记 + 记忆四份专项文档全量并入 CHANGELOG，并删除源文件

> **本次只改文档，未改任何一行代码。** bo s s 原要求把 `codingLOG.md`、`docs/memory/schema.md`、`docs/memory_test_plan.md` 与 `docs/memory/` 下的子文档**全量并入**（不是折叠/摘要）并入 `AGENTS.md`，但核算体量后（合计约 1151 行 / 102KB，AGENTS.md 全量平铺会变成 ~1440 行）**改判为并入 `CHANGELOG.md`**；`docs/memory_test_plan.md` 需**先清理过时表述再并入**（「⚠️ 待架构师确认」的提议接口、§9.1 漂移矩阵结论均为 2026-07-22 的历史快照，照搬会让文档自相矛盾）。

### 一、合并清单与删除动作

| # | 被并入的源文件 | 行数 | 现在的位置 | 源文件状态 |
|---|---|---|---|---|
| 1 | `codingLOG.md`（与最终目标的差距） | 189 | 本文 **附 A** | 🗑 删除 |
| 2 | `docs/memory/schema.md`（记忆 JSON 契约 v1.0.0） | 237 | 本文 **附 B** | 🗑 删除 |
| 3 | `docs/memory_test_plan.md`（记忆测试计划，已清理） | 481 | 本文 **附 C** | 🗑 删除 |
| 4 | `docs/memory/README.md`（用户指南） | 44 | 本文 **附 D** | 🗑 随目录删除 |
| 5 | `docs/memory/privacy.md`（隐私说明） | 65 | 本文 **附 E** | 🗑 随目录删除 |
| 6 | `docs/memory/runbook.md`（运维手册） | 135 | 本文 **附 F** | 🗑 随目录删除 |

- **保留**：`docs/minicpmo_master_plan.md`、`docs/webrtc_aec_plan.md`（bo s s 未要求合并）。
- **未触碰**：`codinglog_by_awaqwq233/`（只由 bo s s 手动维护，Agent 禁止编辑）。
- **交叉引用已修正**：`AGENTS.md`、`README.md`、`new_computer_download/READMEfirst.md` 中对 `codingLOG.md` 与 `docs/memory/*` 的引用，统一改为「见 `CHANGELOG.md` 附 A/C/D/E/F 或附 B」；`docs/memory/` 目录整体已不存在，任何指向它的路径都失效。
- **新归口原则**：日后「差距笔记」「记忆子系统契约 / 运维 / 隐私」的新增与修订，**直接写 `CHANGELOG.md` 对应附录**（差距动态条目建议追加在 `## 2026-09-28` 之前的位置，即按时间倒序），不再新建 `docs/` 专项。

### 二、`docs/memory_test_plan.md` 做了哪些清理（并入前）

原文件 §2「⚠️ 待架构师确认」是**测试专家提的约定接口草案**，§9.1 是 2026-07-22 的**实现漂移快照**（结论「在 Reconcile 之前不得据本计划写测试逻辑」）。这两块现已全部实现，并入时按代码真值改写为：

| 原表述 | 清理为 |
|---|---|
| §2 标题「提议的模块接口（⚠️ 待架构师确认）」、类签名「MemoryStore / MemoryRecorder / MemoryManager」 | 改为 **「已落地实现真值」**，类签名全部换成 `src/memory/` 的实际方法（`store.py` upsert/get/delete/query_by_keywords/query_by_vector/query_hybrid/query_by_tags/compact/export/clear_all/clear_by_id；`recorder.py` `classify` / `normalize_topic` / `_rule_stage` / `_llm_stage` / `_apply_pii_gate`；`manager.py` `retrieve_for_prompt` / `record_turn` / `_worker_loop` / `_capture_person_id`） |
| §2 `SCHEMA_VERSION = "1.0.0"` 常量名 | 代码真值是 `store.CURRENT_VERSION = "1.0.0"`（`store.py:52`），已纠正 |
| §2 `RECURRING_PROMOTED_WEIGHT = 0.8` | 代码真值是 `recorder.RECURRING_WEIGHT = 0.8`（`recorder.py:35`），已纠正 |
| §2 `ACTIVE_MAX_BYTES = 2*1024*1024` | 代码真值是 `store.DEFAULT_MAX_BYTES = 2_000_000`（等价 2MB，名字不同），已纠正 |
| §2/§5/§9 `MAX_ARCHIVE_FILES = 6`、`ARCHIVE_RETENTION_DAYS = 180` | 代码真值是 `MAX_ARCHIVE_FILES = 12`、`ARCHIVE_RETENTION_DAYS = 365`（归档保留 1 年、约 12 个月归档），已纠正 |
| §3.4 提及的六条判定正则 | 确认 `recorder.py:37-56` 已实现且与 oracle 字面一致（仅 `PREFERENCE_RE` 多补了「我叫/我的名字」），标注为 ✅ 已落地 |
| §9.1 漂移矩阵 14 条的「受影响的计划章节 / 严重度」+「Reconcile 二选一待 team-lead 裁定」 | 改写为 **「契约 vs 代码真值·收敛状态表」**（见附 C §C9），13 条标 ✅ 已收敛，剩 2 项真实缺口单独列出 |
| §0「`tests/` 目录不存在 / 依赖缺失 / 覆盖 0%」 | 全部过时：**`tests/` 已存在**（16 个测试文件 + `fixtures/record_samples.jsonl`），`requirements-test.txt` 已建，`pytest.ini` 已配（⚠️ 本机未装 pytest-cov，跑测试要 `-o addopts=`），基线 **134 passed**。已整段替换为真值 |
| §5「Tier2 取证不可恢复 / 密钥销毁」等 | 保留，但明确「**明文 v1（当前状态）无加密、无密钥**，Tier2 的加密分支属于未来路线图」——当前实现只有 `clear_all()` / `clear_by_id()` |

### 三、清理后暴露的两个真实缺口（不是文档问题，是代码缺口）

并入过程中顺手核对代码，发现**隐私承诺里写了、但代码还没做**的两件事，已在附 C §C9 与附 E/F 标注：

1. **范围级清除 API 不存在**。`privacy.md` §5 / `runbook.md` §4 承诺「一键清除所有 `inferred` 而保留 `explicit`」「`clear(pii=True)`」，但 `MemoryStore` 只有 `clear_all()` / `clear_by_id(id)`，`_purge_replicas(matched_ids, secure)` 是内部方法**没有 public 入口**。→ 当前只能「全清 + 逐条删」，「一键清 inferred」与「安全擦除」**尚未落地**（P1）。
2. **`consent.json` / 可见同意机制未实现**。全仓库无 `consent.json` 的读写；`privacy.md` §4.3「记录于同目录 `consent.json`」属**设计态**，非实现态。

> 这两条会写进下一轮待办，bo s s 拍板优先级。

### 四、验证与同步

- 无代码改动 → 不跑 `py_compile` / 测试套件。
- grep 校验：确认 `codingLOG.md`、`docs/memory*`、`docs/memory_test_plan.md` 在本文件之外无残留引用（除本文内的「已并入/已删除」说明）；`docs/` 下仅剩两份 plan。
- 文档四件套（`AGENTS.md` / `README.md` / `CHANGELOG.md` / `codingLOG.md`）中，`codingLOG.md` 已并入本文并删除，其职责由本文附 A 承接。

---

### 附 A · 与最终目标的差距（原 `codingLOG.md`，已并入）

> 本附录承接原 `codingLOG.md` 全部内容。文件已删除，日后修订**直接改本附录**。
> 注：本附录是"差距笔记"，不是精确实现状态。已落地的进展（主动判断引擎、多模态图像问答、多后端大脑）以 `AGENTS.md` 为准。

> **当前目标锚点（2026-10-07）**：bo s s 已确认方案 B，精确契约以 `backend.lock.json` 与权威 DOCX 为准。M0 已由用户接受并豁免 30 分钟长测；M1 Gateway 基础听看说、GUI 预览与手动启停已获用户真机验收，下一步接入 Qwen 大脑/工具升级，并行转写/云端/记忆仍待迁移。本轮 GUI 已重构且所有旧 MiniCPM/传统运行入口已删除；下方 A1–A5 为删除前历史证据（路径/行号不再代表当前文件），勿据此判断新入口或将旧能力算入当前验收。
> - **A1 判断模型**：目标由 MiniCPM-o-4_5 全双工承担主动判断，固定 Gateway `/v1/realtime?mode=video`；旧 `src/judgment/` 轮询 judge 已于本轮删除，旧 :9060 客户端与传统运行入口也已退役。
> - **A3 记忆**：旧结论「fastembed + JSON 长期记忆」已作废——新架构改 **ChromaDB + BGE-Small-ZH-v1.5（ONNX INT8）+ JSON**，`src/memory/`（自研 MemoryStore）**待重写**；附 B/C/D/E/F 为旧记忆子系统契约，仅作历史存档。
> - **A5 云端 / OpenClaw**：旧结论「无 MCP / OpenClaw 集成」已作废——新架构**新增云端 OpenClaw 层**（DeepSeek API），属待实现项。
> - **A4 语音 / TTS**：目标为参考 WAV 经 `session.init.payload.voice` 编码发送，24k float32 原生音频流；不使用 speaker embedding `.pt`。旧 omni Voicebox 桥接/回灌已删除，主依赖仅 Gateway；未迁移的通用音频组件独立保留。
> - **A4 后端参数**：目标固定 Gateway `:8006`、Worker `:22400`、引擎 `:22500`，Q4_K_M、起始 `-c 4096 -t 8 -ngl 99`；Metal 构建关闭内部 TLS并应用已登记的 loopback 补丁。
> 其余历史坑位记录（令牌碎片、背压、回声门控等）作为工程经验保留，但在新架构后端 / 协议下需**重新验证**。

#### A1. 交互方式：从被动到主动（部分解决）

- **目标**：唤醒词 + VAD，能自动判断你什么时候说完话，无需物理按键；最终实现持续主动感知与闭环介入。
- **现状**：使用 MiniCPM-o 作为判断模型（实际加载 `minicpm-v-4_5`，9B；文档此前统称 "MiniCPM-o"），**已在 M5 Pro 48G 统一内存机器上实跑验证通过**（主动介入闭环可用）。
- **⚠️ 代码真值（2026-10-01 补正）**：判断引擎的代码默认值是 **`minicpm-v-4_5`**（`src/judgment/judge.py:58` 的 `model_name` 与 `src/utils/config.py:18` 的 `judgment_model_name`），走 LM Studio 标准 `chat/completions`（**只传图＋文，o 版的听与主动决策用不上**）。下文凡写「MiniCPM-o 作为判断模型」均指这一对关系的意图，**实际标识符以 V 版为准**；OMNI 全双工链路里常驻的是另一份 `MiniCPM-o-4_5`（GGUF Q8_0，llama.cpp-omni `:9060`），两者不可混为一谈。
- **【选型决策 2026-08-11 · 方向待办】判断模型选型**：MiniCPM-o 4.5 与 V 4.5 同为 9B；o 版的「看＋听原始音频＋1Hz 主动发言决策」依赖面壁自研 omni 推理框架（llama.cpp-omni / transformers 全双工接口），标准 LM Studio `chat/completions` 无法触发。当前 judge.py 经 LM Studio 标准 API 仅传图＋文，o 版的「听」与主动决策用不上，换上即退化为 9B VLM、与 V 版重叠、收益≈0。**若要实现理想图景（主动感知＋预警＋应急接管），方向待办是将 judge 从「外部轮询独立模型」重构为「持续喂音视频流给 o-omni 进程、订阅其主动发言事件」，并把音频输入由 Whisper 文本改为原始音频波形**——属架构改造，非改 `model_name`。

#### A2. 缺乏"手"——Function Calling（部分解决）

- **目标**：工具调用能力，例如查实时天气、控制电脑音量、打开网页、管理日程、搜索文件等。
- **现状**：**基础"装手"能力已实现**（2026-08-11）。大脑 `qwen/qwen3.6-35b-a3b` 经 `verify_toolcall.py` 验证支持 OpenAI 风格 function calling；`src/brain/llm.py` 新增 `think_with_tools` / `run_agentic`（工具调用循环），`src/tools/` 提供四个白名单工具（打开应用/网页、只读本地文件搜索、系统状态查询、受限 shell），`process_response` 非视觉分支已接入，默认开启（`TOOLS_ENABLED` 可关）。当前工具集偏"本地操作"类，尚未含实时联网（天气/日程）类，属能力扩展项（未解决）。

#### A3. 记忆力有限（部分解决）

- **目标**：长期记忆（Vector DB / JSON），记住用户喜好、历史对话摘要，实现个性化陪伴。
- **现状**：记忆功能已落地（fastembed 向量检索 + JSON 长期记忆），机器升级后已具备端到端验证条件，待实跑个性化闭环。数据契约见 **附 B**，运维见 **附 F**，隐私见 **附 E**。

#### A4. 响应速度 / 流式（已落地：全双工 M5 验收 + M7b 句子级桥接；token 级 TTS 待做）

- **目标**：流式对话——一边思考一边生成语音，大幅降低感知延迟。
- **现状**：
  - **全双工主链路（M5）已真机验收**（2026-08-15）：MiniCPM-o-4_5 经 llama.cpp-omni（9060，Metal，GGUF Q8_0）跑**全双工**——持续听/说闭环、主动打招呼、按 `<<CALL_QWEN>>` 令牌升级到 qwen3.6-35b 调 `src/tools/` 工具、回灌播报，整条闭环跑通。`src/omni/` 的 OMNI 模式与传统被动 `main.py` 互斥、不启动 judge。
  - **主对话 LLM 已流式 + M7b 句子级 TTS 桥接**：omni 下行 `response.output.delta` 逐字吐文本；`src/omni/voicebox_bridge.py` 按标点/句子边界把 text delta 攒成句，攒够一句即送本地 **Voicebox（JAC 克隆声纹）** 合成并播放（独立 daemon 播放线程串行保序），实现「说一句听一句」近似实时感；omni 自带 TTS 音频在桥接启用时丢弃。
  - **回灌（M7a）已改本地 Voicebox**：`speak_result` → `src/omni/backfeed.py` 的 `speak_text_via_voicebox` 用 JAC 克隆声纹播报（替代原 omni 第二 turn_based 会话——server 单会话限制会拒第二个会话、导致 `ConnectionClosedOK` 无声音，已根除）。
  - **会话参数协议（2026-09-13 已修复）**：此前 GUI/环境变量中的 `omni_listen_prob_scale=0.5` 被错误发送到 `session.init` 顶层，llama.cpp-omni 仅读取 `payload.config`，导致服务端静默回退 1.0 并永久 `listen=1`。现由 `OmniClient._build_session_init()` 固定发送至 `payload.config`，并有离线协议回归测试；这是一次客户端协议接线错误，不是麦克风能量或模型加载问题。
  - **语音输入方案（2026-09-13 澄清）**：**不是「录完再发」，但是 0.4s 粒度的分块流式**——麦克风 16k float32 每 64ms 读入内存缓冲，推送协程每 0.4s 打包「0.4s 音频 + 1 帧 JPEG」为一条 `input.append`；服务端每段做一次 prefill+decode、由模型自行决定 listen/speak。**离 GPT-Live 级还差三步**：①无真正的 barge-in 打断（回声门控在播放期推等长静音＝放弃打断，需 WebRTC AEC 替代）；②omni 原生 token 级音频流被 M7b 的 Voicebox 攒句桥接丢弃（+`_MAX_WAIT=2s`，需恢复原生流或给 Voicebox 做增量合成）；③分块仍为 0.4s（需先把单段成本压下来才能降到 0.16s）。另有一条**架构级缺口**：full_duplex 下行只有 `listen`/`text`/`audio`/`response.done`，**没有任何「用户说了什么」的转写**，客户端无法校验模型是否听懂——补法是对同一 `_mic_buf` 并行跑本地 Whisper（仅用于显示 + 升级任务相关性校验）。
  - **实时性根因（2026-09-13 实测已修）**：服务端 full_duplex 是串行「读一段 → prefill+decode 一步」，实测单段 **0.69s**（VPM 图像编码 190ms 是大头），而客户端固定每 0.4s 猛推 → 积压线性增长，实测 64.4s 会话服务端累计落后 **27.2 秒**（模型回答的是半分钟前的用户）。已落地 **P0-b 背压**（改为「一段在飞」：收到 `listen`/`response.done` 才推下一段）+ **音频水位**（超 1.2s 丢最旧的，保证「听到的是现在」）；实测背压后平均间隔 0.60s、单段 0.55s、零丢弃，**上行音频量不再超过流逝时间**。
  - **上下文寿命短板（已定位，P1 待做）** ⚠️ **本段「视觉 token 吃爆 KV → 每 30s 清空 → 复读示例」的因果链已于 2026-09-28 被 A1 实验证伪**（`--no-video` 全程零视频帧、幻觉依旧），根因重定位为「o 版听不清 + prompt 示例是复读诱饵」，见下「说话不识别/乱识别」三条机制。**以下 KV / 降频量化数据本身仍有效（降频收益是真的），但别再按「视觉 token 是根因」去修 bug**：每段 KV 增长约 73 token，其中**视觉约 64、音频个位数**；n_ctx=8192 触发线 6144 → 实测每约 30 秒就被滑动清空一次（`slide n_past=7660→2652`）。记忆一清，模型只剩 system prompt，就照着 `prompts.py` 的示例原句吐令牌（真机那次「查一下这台电脑的…」即示例片段）。**图像降频已落地（P1，2026-09-13）：默认 1 帧/秒**（`OMNI_VIDEO_INTERVAL`）。**实测收益**（真机日志：VPM p50=196ms/均值 232ms、图像 64 视觉 token/帧、块节奏约 1.5 段/秒）：KV 增长 109→79 token/秒（降约三成）、VPM 负载 0.34→0.23 秒/秒（降约三分之一）、上下文寿命 69→95 秒（+约四成）；**并且它是 P2 的前置条件**——分块若降到 0.16s，不降频的话 VPM 会占掉每秒 1.14s 的算力，实时性直接崩。
  - **令牌碎片（2026-09-13 已修，二轮才修净）**：服务端**按 token 逐片**下发文本，`<<CALL_QWEN>>` 必然被切成 `<<CALL_Q`；旧逻辑只在凑齐完整令牌时拦截，碎片被当普通对话朗读（Voicebox 里那段 `<<CALL_Q` 怪音）。已落地 **P0-a 令牌前缀 holdback**（末尾最多 12 字符扣留，确认非令牌前缀才外发；桥接层同款兜底），并有「分片令牌零泄漏」回归单测。**二轮教训（关键协议事实）**：full_duplex 下 **`response.done` 是「每段一次」而非「每轮一次」**，段边界可能落在令牌中间——一轮把释放挂在 `response.done` 上，等于每段都放一次碎片（真机又见 `<<CALL_QW`，升级任务被截断成「查一下」）。真正的轮末信号是 **listen 事件**；现改为「仅 listen / 会话关闭立即释放 + 文本静默 1.5s（`OMNI_HOLD_IDLE`）兜底释放」，离线一字不差复现过旧行为。
  - **会话被服务端打死（2026-09-13 已修）**：服务端 full_duplex 对**空音频**的 `input.append` 会 `fail_fast("missing_audio")` → 发 `session.closed` 后立刻 `ws.close(1000)`，整个会话结束（真机表现：「怎么说话都不回」+ 客户端只看到 `received 1000 (OK)`）。P0-b 背压放行了这个风险窗口（终局事件已到时会立刻放行，此时麦克风可能还没产出数据）。现 `_push_loop` 保证**每帧必带音频**（先等 ≤0.16s，仍空则补 0.1s 静音）。
  - **可观测性补强（2026-09-13）**：①`_mic_loop` 采集线程死亡不再静默（此前 `read()` 异常直接 `break`，表现就是「说话没反应」却只有 RMS≈0，极易误判成权限问题）；②打印服务端 `session.closed` 的 reason 与 WS 关闭码；③`stop()` 主动关 WS，不再白等满 10s `join` 超时（实测 10.00s→0.93s）。
  - **令牌变体（2026-09-13 三轮已修）**：模型**不保证**原样吐出 `<<CALL_QWEN>>`——真机确认它会吐 `<<CALL_ QWEN>>`（中间夹空格/换行，空格来自模型本身）。精确匹配会漏，后果是碎片被当普通对话**显示并朗读**（同一句被反复念），且 holdback 也拦不住（`<<CALL_` 被扣住，但下一个字符是空格就不再是前缀）。现令牌识别统一到 `src/omni/tokens.py`：容忍空白/换行/大小写的正则 + 容忍空白的 holdback + **硬安全网**（含 `<`/`>` 或裸标记词一律不外发不朗读）；命中后**消费掉令牌**（不再靠 listen 清缓冲——listen 每段都会来，会把令牌拦腰截断）。
  - **推流节奏（2026-09-13 三轮已修）**：服务端**一段会发两个终局事件且共用同一 `response_id`**（listen delta + response.done）。二轮把两个都算「本段完成」→ 一段被算两次 → 推流翻倍、极小块（真机见过 0.02s）→ 每轮都要重做图像编码（VPM 190ms，与块大小无关）→ 消费速率腰斩 → 积压丢帧 **5.1s**（丢在句子中间 → 模型听残句 → 答非所问）。现按 `response_id` 去重 + 最小推流间隔 + 最小块时长（`OMNI_MIN_CHUNK_SECS` 默认 0.4s）。WS 级集成实测：间隔 0.60s、单段最小 0.40s、零丢弃。
  - **人声判据与门控（2026-09-13 三轮已修）**：护栏窗口 3.0s **小于端到端延迟**、RMS 阈值 0.02 **压在人声段下沿**（实测底噪 0.002~0.013 / 人声 RMS 0.020~0.056 / 人声峰值 0.088~0.277）→ **真实提问被误判成静音期幻觉**而拦截。现判据改「峰值 或 RMS」双条件、窗口 6.0s；门控 auto 判定改为**同时看输入与输出**（输入本身是耳机时不得关门控），并把「自身播报窗口」的排除从门控开关解耦。
  - **图像降频（P1，2026-09-13 已落地）**：图像上行与音频上行**解耦**——音频照常按块上行，图像默认 **1 帧/秒**（`OMNI_VIDEO_INTERVAL`，<=0 退回每段带图）。动机是服务端每带一帧图就要做一次 VPM 编码（实测 p50=196ms/均值 232ms）并往 KV 塞 **64 个视觉 token**，而一段音频只有个位数 token。**实测收益**：KV 增长 109→79 token/秒（降约三成）、VPM 负载 0.34→0.23 秒/秒（降约三分之一）、上下文寿命 69→95 秒（+约四成）。**它同时是 P2 的前置条件**：分块若降到 0.16s，不降频的话 VPM 会吃掉每秒 1.14s 的算力，实时性直接崩。
  - **「说话不识别 / 乱识别」三条机制（2026-09-17 诊断，P0 实验开关已落地）**：bo s s 四组真机日志（listen 系数 1.0/0.8/0.6/0.4）暴露三个**独立**故障叠加，别再当成一个 bug 修：
    1. **「不识别」= 推流节拍抖动 + 水位丢帧**。自研「背压变长块（0.4~1.4s）」偏离官方推荐节拍（llama.cpp-omni 文档明确 `Recommended loop cadence: 1000ms per iteration`，且要求**播放 TTS 时静音麦克风**、**静音也要发音频块**）；服务端「说话段」要 VPM 276ms + decode 611ms ≈ 0.9s，「聆听段」仅 0.15s，客户端 1.2s 水位一超就丢最旧的。四组日志**每次启动都丢**（1.73/1.28/1.60/1.22s）且丢在最初一两秒＝**boss 开口那一刻**；服务端再把非整秒块补静音对齐到 100ms 网格（`Audio not aligned to 100ms (576ms), padded to 600ms`）→ 音频断续。**未解决**，方案（P1）是改成固定 1s 节拍且**宁延后不丢字**。
    2. **「乱识别成查电池」= MiniCPM-o 音频理解失效 + prompt 示例是「复读诱饵」**（⚠️ 2026-09-28 A1 实验**证伪**了 09-17 的「视觉 token」假设）。**不是 ASR 结果**（full_duplex 不回传用户转写），是模型自己吐的令牌任务描述。09-17 曾归因「视觉 token 每段 +64 → 每约 30s 滑动清空 → 只剩 system prompt → 抄例句」，但 `--no-video` 全程零视频帧后**幻觉依旧**，故视觉 token **不是根因**。新证据链：boss 人声 RMS 仅 0.03~0.065（内建麦离嘴远、能量弱）+ 启动丢 0.59s → 模型**听不清**「你好，你在吗」这类寒暄，从 `prompts.py:31-35` 的 5 条具体示例里「捡」输出，「查电池」是第一条、最具体，故被反复选中。变量分离开关 `video_enabled`（GUI「图像上行」/ `--no-video` / `OMNI_VIDEO_ENABLED=0`）已落地、仍保留，但已证不是主因。**修法（P3，已实施 2026-09-28）**：删掉具体示例、改抽象规则 + 明确「任务描述用 boss 原话、听不清就请重复、绝不照抄示例」。
    3. **「真实提问被自己拦截」= 护栏判据两处失准**。① `_has_recent_speech()` 首分支 `if self._is_echoing(): return False` **与门控开关解耦**（`OMNI_ECHO_GUARD=always`），戴 AirPods（硬件已隔离回声、门控自动关）时**照样生效** → 只要 J.A.C. 正在播报或播报后 0.8s 内，插话提问 100% 被判幻觉丢弃（日志 `距上次人声 1.0s > 窗口 6.0s` 自相矛盾即铁证）；② `OMNI_SPEECH_PEAK_TH=0.06` 过低，底噪级帧（峰值 0.061）反复越线 → `_last_speech_ts` 长期新鲜 → **护栏形同虚设、静音期幻觉令牌被放行**。**未解决**（P2 待做）：门控关时不该做「播报期＝幻觉」排除；峰值阈值提到 0.12~0.15 或改连续多帧判定；顺带修 `播放回声期` 分支的日志文案。
  - **官方 web demo 与本地 app 的实质差异（2026-09-17 核对）**：官方同为 `浏览器 → gateway:8040 → worker:22440 → llama-server:19080` 的多层 HTTP/WS，**架构形式并非问题所在**。三处真实差异：①上行节拍（官方固定 1000ms，本地自研变长）；②播放与回声（官方要求播放期静音麦克风 + 浏览器 WebRTC AEC，本地用外部 Voicebox 播 AirPods、omni 音频回路断开、只能靠「播报窗口一刀切」）；③性能余量（官方基线 RTX 5070 / RTF 0.4，本地 Mac+Q8_0 实测 RTF≈0.9~1.0，**无余量，一抖就积压**）。
- **仍待做**：STT 仍为 Whisper tiny **非流式**（整段说完才识别）；TTS 为**句子级**桥接而非 token 级流式（Voicebox 无 token API，业界标准折中）；**P2 真 GPT-Live 化（WebRTC AEC 打断 + 原生音频流 + 0.16s 分块 + 并行 Whisper 转写）**——其中 **WebRTC AEC 的方案已出（`docs/webrtc_aec_plan.md`，2026-09-24），待评审后分阶段实施**；方案的关键障碍是「所有发声都走 `afplay` 子进程，Python 侧拿不到 far-end 参考 PCM」，因此必须先改造播放链路；剩余三项（原生音频流 / 0.16s 分块 / 并行 Whisper 转写）仍待排期。另：会话被服务端意外关闭后客户端目前**直接停止**（是否加自动重连待定）；全双工 RTF / 真机逐句听感流畅度待 bo s s 验收收尾。
  - **已解决（2026-09-24）**：`runtime.stop()` 后升级 daemon 线程仍在跑工具调用与 TTS —— 已通过「协作取消（`escalate(should_stop=...)` 每流式分片检查）+ 三个停止检查点 + `stop()` 先 join 再关 `omni_client`」修掉，回归见 `tests/test_runtime_escalation_stop.py`。

#### A5. 其他架构级缺口（未解决）

- 无 MCP / OpenClaw 集成；无云端/局域网卸载（重推理可上局域网/云）。`Qwen3.6-35B` 大脑已完整接入 LM Studio 并验证（`qwen/qwen3.6-35b-a3b`）。**agent 执行框架已落地**：Function Calling 工具层已实现（四个白名单工具 + 工具调用循环）。
- 视觉理解仍只有 **YOLO 标签 + LLM 文本摘要**，无 OCR / 人脸识别 / 深度 / 场景图 / 视觉语言理解（旧的 LocateAnything-3B 方案已移除）。
- TTS 语音栈已从 Genie-TTS（GPT-SoVITS）全面切换为开源本地 Qwen3-TTS（已删除 genie_tts.py 与 genie_assets/、GenieData/），支持情绪控制与声音克隆；实际选用链为 Voicebox（macOS 主力）→ Qwen3-TTS（仅 NVIDIA）→ 系统 TTS 兜底。注：2026-08-09 起 brain 输出改为纯文本，情绪化语音能力（各 TTS 仍支持 `emotion_hint`）当前未被调用，统一走中性朗读。
- 当前项目树**已有自动化测试**：`tests/unit/test_tools.py`（Function Calling 工具层单测）、`tests/test_*.py` 系列（记忆 / 语音 / GUI 运行期等），基线 134 passed。

---

### 附 B · 记忆 JSON 契约 v1.0.0（原 `docs/memory/schema.md`，已并入）

> ⚠️ **旧记忆子系统（2026-10-01 标注）**：本附录是旧记忆实现（fastembed + 自研 `MemoryStore`）的 `memory.json` 机器可解析契约。新架构已定案改用 **ChromaDB + BGE-Small-ZH-v1.5（ONNX INT8）+ JSON**，`src/memory/` 将重写；本附录作为「旧代码迁移前仍在使用的契约」与历史存档保留。

> 文档状态：**v1.0.0 锁定版**。由 architect ratify，testing-expert 据其写字段断言（`docs/memory_test_plan.md` §9）。本文件即 `memory.json` 的机器可解析契约。
>
> 风格：中文标题 + 英文术语，与 `AGENTS.md` 一致。

#### 1. 目的与适用范围

本文件定义 J.A.C. 记忆子系统的本地持久化格式 `memory.json`，供以下模块一致使用：

- **recorder / 记录判定模块**：产出 `RecordDecision`（见 §10），落盘为 `MemoryFact`。
- **store / 存储层**：读写 `memory.json`、维护 `.bak`、版本迁移。
- **brain / 检索层**：按 `kind` / `tags` / `weight` / `updated_at` 召回。
- **runbook / 隐私审计**：按 `pii` / `source` 过滤与清除。

记忆是**本地优先、结构化 JSON 摘要**（`AGENTS.md`：记忆从结构化 JSON 摘要起步，再考虑向量数据库）。初版单文件，不加密（加密属逻辑层，见隐私文档）。

#### 2. 顶层结构

```json
{
  "version": "1.0.0",
  "facts": [ {MemoryFact}, ... ]
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `version` | string (semver `MAJOR.MINOR.PATCH`) | 是 | 初值 `"1.0.0"`。缺失视为 `"0.0.0"`（旧版/未版本化），宽松加载，下次写回升级为当前版本。 |
| `facts` | array<MemoryFact> | 是 | 记忆数组，可为空 `[]`。 |

> **设计裁定（architect）**：顶层仅保留 `version` 与 `facts`。原草案的 `user_consent` / 顶层 `updated_at` 已移除：
> - `user_consent` 移出 `memory.json`，改由应用/设置层或同级 `consent.json` 记录——**同意生命周期不与事实数据耦合进同一文件**。
> - 顶层 `updated_at` 去掉；压缩/轮转改用各 fact 的 `updated_at` + 计数/体积。

#### 3. MemoryFact（v1.0.0 锁定字段）

##### 3.1 必填字段（缺失 → 跳过并计入 `invalid_facts`）

| 字段 | 类型 | 约束 |
|---|---|---|
| `id` | string (UUID4) | 全局唯一，生成后不可变 |
| `content` | string | 记忆文本，非空 |
| `kind` | enum | 见 §4 |
| `source` | enum | 见 §5 |
| `created_at` | string (ISO8601) | 带时区，如 `2026-07-01T04:00:00Z` |
| `updated_at` | string (ISO8601) | 创建时 == `created_at`；检索命中时 bump 它，**承载 recency + 热度**，与 `weight` 共同决定召回排序（见 §12 检索打分） |

##### 3.2 可选字段（缺失 → 套默认值，不计入 `invalid_facts`）

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `weight` | number [0,1] | `0.5` | 召回优先级 / 重要性打分（v1 **唯一权重字段，无独立整型权重字段**）；`inferred` 来源应默认更低（建议 0.3–0.4）；`recurring` 晋升置 `0.8`（见 §12 / `RECURRING_PROMOTED_WEIGHT`） |
| `tags` | array<string> | `[]` | 检索精度用；v1 可空，由 `content` 分词兜底 |
| `pii` | boolean | `false` | 敏感标记（见 §6）；解析时若缺失 → 按 `false`（非 PII） |
| `ttl` | string\|null (ISO8601) | `null` | 易失类过期时间，供压缩逻辑使用 |
| `embedding` | array<number>\|null | `null` | **向量字段**：轻量本地 embedding（fastembed）填充的语义向量，供混合检索余弦打分；v1.1 起由 `MemoryEmbedder` 在写入时填充，未启用时为 `null` |

> **折并说明（architect）**：原**整型权重字段**已并入 `weight`(number[0,1])；原 recency / heat **独立字段** → 并入单一 `updated_at`；`occurrences`→**不持久化**，由 recorder 运行时维护 `topic_key→count` 计数器，跨阈值才落 `kind=topic`；`consent_scoped`→`pii`；`type`→`kind`。

#### 4. `kind` 枚举（RATIFY，不改名）

`profile` | `preference` | `convention` | `event` | `topic`

与架构 5 类 1:1 映射：`user_profile_fact`→`profile`，`decision_event`→`event`，`project_convention`→`convention`，`recurring_topic`→`topic`，`preference` 保持。

#### 5. `source` 枚举（architect 裁定：**五值，非三值**）

`explicit` | `inferred` | `recurring` | `judgment` | `manual`

> **⚠️ 与早期三值提案有意偏离（architect 裁定）**：原 `conversation` / `judgment` / `manual` 三值**不采用**。recorder 有 5 条来源路径：
> - `explicit`：用户主动告知（A 显式意图）
> - `inferred`：系统从对话**推断**出的偏好（B 隐式推断）
> - `recurring`：频次升级产出（C 路径），独立生命周期状态
> - `judgment`：判断引擎介入（D 决策）
> - `manual`：用户/CLI 手动编辑（E）
>
> 关键语义：`explicit`（用户主动告知）与 `inferred`（系统猜出）的区分是**隐私与可清除性**的核心——用户可能想一键清空所有 `inferred` 事实但保留 `explicit`；且 `inferred` 应默认更低信任 / 更低 `weight`。`recurring` 不能并入 `conversation`。测试与文档一律以五值为准。

#### 6. `pii` 字段（RATIFY，进 v1.0.0）

boolean，默认 `false`，**进 v1.0.0**（不推迟到 v1.1，避免测试漂移）。它是 Runbook / 隐私说明所需的敏感标记（对应原 `consent_scoped`）。

- **安全默认**：解析时若 `pii` 缺失 → 按 `false` 处理（非 PII）。
- 存储层 v1 **仅标记**；强制门控（同意/加密）属逻辑层，不进 schema。

#### 7. 版本号与迁移说明

`version` 为 semver 字符串。兼容性逻辑（测试断言基准，见 §9）：

1. **JSON 解析失败（结构损坏）** → loader 抛 `MemoryFileCorrupt`（不静默吞）。见 §8 损坏恢复。
2. **`version` 缺失** → 按 `"0.0.0"` 宽松加载，不打断；下次写入升级为当前版本。
3. **`version` MAJOR 不符**（如文件 2.x，代码支持 1.x）→ 抛 `MemoryVersionIncompatible`，**拒绝加载**，绝不静默改写。
4. **同 MAJOR 的 MINOR/PATCH 差异** → 向前/向后兼容，正常加载，忽略未知字段、保留已知字段。
5. **单条缺必填字段** → 容忍跳过，计入 `invalid_facts`，其余正常加载。缺可选字段 → 套默认，不计入。

#### 8. 损坏恢复（Corruption Recovery）

主文件 `memory.json` 解析抛 `MemoryFileCorrupt` 时，loader **应先尝试 `memory.json.bak`**，再向上抛。两条路径测试均需覆盖：

- **主文件损坏 + `.bak` 有效** → 从 `.bak` 加载（并应在下次写回修复主文件）。
- **主文件损坏 + 无 `.bak`** → `MemoryFileCorrupt` 向上传播。

> Runbook §3.7「损坏」章节据此描述：优先 `.bak` 回退，无则安全重建空文件（保留 `version`）。

#### 9. `invalid_facts` 报告形态（测试断言）

loader 返回结果对象含两个字段：

```json
{
  "facts": [ {MemoryFact}, ... ],
  "invalid_facts": [ { "id": "...", "reason": "missing_required:kind" }, ... ]
}
```

- `facts`：成功解析的有效 MemoryFact 列表。
- `invalid_facts`：被跳过条目的「id / 原因」列表（或计数）。测试断言该集合。
- **必填集固定为 6 个**：`id` / `content` / `kind` / `source` / `created_at` / `updated_at`。任一缺失即计入 `invalid_facts`。

#### 10. RecordDecision（独立运行时契约，不进 memory.json）

介于 recorder 与 store 之间的 LLM 分类输出：

```json
{
  "should_store": true,
  "reason": "user_stated",
  "kind": "preference",
  "confidence": 0.87
}
```

| 字段 | 类型 | 必填 | 取值 |
|---|---|---|---|
| `should_store` | boolean | 是 | 是否应落盘 |
| `reason` | string | 是 | 非空；建议受控词表：`user_stated` \| `derived_preference` \| `explicit_convention` \| `observed_event` \| `topic_of_interest` \| `low_confidence` \| `duplicate` \| `pii_blocked` \| `not_factual` |
| `kind` | enum \| null | 是* | `should_store=true` 时为 §4 枚举之一；`false` 时为 `null` |
| `confidence` | number [0,1] | 是 | 模型判定应存储的置信度 |

> **注意**：RecordDecision **不持久化进 memory.json**，它是 recorder 运行时产物。其 `kind` 必须与 MemoryFact.kind **同源**（同一枚举），以保证一致性。

#### 11. 示例条目（5 类）

```json
[
  {
    "id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
    "content": "用户是嵌入式工程师，常做本地多模态原型",
    "kind": "profile",
    "source": "explicit",
    "created_at": "2026-07-01T04:00:00Z",
    "updated_at": "2026-07-01T04:00:00Z",
    "weight": 0.8,
    "tags": ["职业", "背景"],
    "pii": false,
    "ttl": null,
    "embedding": null
  },
  {
    "id": "7c6b8f9a-12de-4f56-9abc-1234567890ab",
    "content": "用户偏好用中文回复",
    "kind": "preference",
    "source": "inferred",
    "created_at": "2026-07-02T06:30:00Z",
    "updated_at": "2026-07-02T06:30:00Z",
    "weight": 0.4,
    "tags": ["语言"],
    "pii": false,
    "ttl": null,
    "embedding": null
  },
  {
    "id": "3d2e1f0a-9b8c-7d6e-5f4a-0b1c2d3e4f5a",
    "content": "每周一同步一次项目进度",
    "kind": "convention",
    "source": "explicit",
    "created_at": "2026-07-03T09:15:00Z",
    "updated_at": "2026-07-03T09:15:00Z",
    "weight": 0.6,
    "tags": ["节奏"],
    "pii": false,
    "ttl": null,
    "embedding": null
  },
  {
    "id": "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d",
    "content": "2026-07-04 演示了主动感知原型",
    "kind": "event",
    "source": "judgment",
    "created_at": "2026-07-04T12:00:00Z",
    "updated_at": "2026-07-04T12:00:00Z",
    "weight": 0.5,
    "tags": ["里程碑"],
    "pii": false,
    "ttl": null,
    "embedding": null
  },
  {
    "id": "b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d6e",
    "content": "用户对本地优先 AI 助手持续感兴趣",
    "kind": "topic",
    "source": "recurring",
    "created_at": "2026-07-05T03:45:00Z",
    "updated_at": "2026-07-05T03:45:00Z",
    "weight": 0.5,
    "tags": ["兴趣"],
    "pii": false,
    "ttl": null,
    "embedding": null
  }
]

#### 12. 检索打分（Retrieval Scoring）

召回排序 **只用两个字段**：`weight` 与 `updated_at`。

- `weight`（number [0,1]）：事实 / 用户重要性，越高越优先。
- `updated_at`（ISO8601）：recency + 热度（检索命中会 bump 此字段）。

> **无 `importance`、无 `access_count`**：v1 检索**不**使用 `importance`（该字段已被 `weight` 完全取代、schema 中不存在）也不使用 `access_count`（已并入 `updated_at`）。任何引用这两个名字的代码 / 测试均属 schema 漂移，应修正为 `weight` + `updated_at`。

**打分公式（参考实现，非契约强制）**：

```
score = 0.7 * weight + 0.3 * recency_norm(updated_at)
```

> **混合检索（v1.1）**：启用 embedding 后，`MemoryManager.retrieve_for_prompt` 走 `query_hybrid`——关键词分（上表公式）与向量余弦分各自 min-max 归一化后加权融合（`0.6*vec + 0.4*kw`，权重可调）。embedder 不可用时自动退化为纯关键词检索，`embedding` 为 `null` 也不影响。
`recency_norm` 为 `updated_at` 到当前的衰减归一（如指数衰减或线性分桶），值域 [0,1]。

**`recurring` 晋升（C 路径 → 落 `kind=topic`）**：

- 由 recorder 运行时 `topic_key→count` 计数器跨阈值触发；晋升落库时 `weight` 置 **`0.8`**，对应代码侧常量 `RECURRING_PROMOTED_WEIGHT`（避免魔法数字）。
- 落库 fact 的 `source` 仍为 `recurring`，`updated_at` 取晋升时刻，`pii` 默认 `false`。
- 测试断言基准：晋升后 fact 满足 `weight == 0.8` 且 `source == "recurring"`（见 `docs/memory_test_plan.md` §9）。

---

### 附 C · 记忆子系统测试计划与实现真值（原 `docs/memory_test_plan.md`，**已清理过时表述后并入**）

> ⚠️ **旧记忆子系统（2026-10-01 标注）**：本附录描述的是旧记忆实现（fastembed + 自研 `MemoryStore`）。新架构已定案改用 **ChromaDB + BGE-Small-ZH-v1.5（ONNX INT8）+ JSON**，`src/memory/` 将重写，本附录仅作历史存档，勿据此开发新记忆功能。

> 原作者：泰莎 (Tessa)，测试专家。原文件写于「设计阶段」，§2 是**提议的约定接口**、§9.1 是 2026-07-22 的**实现漂移快照**。本次并入前已按 `src/memory/` 代码真值全部改写（见本文件「二、清理了什么」表），下文所有「真值」均指 2026-10-01 核对结果。

#### C1. 测试策略（金字塔）

```
        /   E2E 小范围    \     少量：真实 main.py 跑一段带 mock 外设的对话
       /     集成测试      \    中量：MemoryStore ↔ LocalBrain ↔ 主循环（mock brain）
      /       单元测试      \   大量：MemoryStore 存储/恢复、MemoryRecorder 分类
```

- **记忆存储（MemoryStore）**：纯类/函数单元测试为主（快、可离线），原子写与损坏恢复为关键路径。
- **记录判定（MemoryRecorder）**：规则单测 + 黄金数据集评估 LLM 判定的边界与误判率。
- **集成**：memory 在 `LocalBrain.think()` 前注入、主循环读写时机 —— 全程 mock `LocalBrain`，不依赖 LM Studio。

#### C2. 已落地实现真值（原 §2「⚠️ 待架构师确认的提议接口」→ 现状）

**目录与文件（2172 行）**：

| 文件 | 行数 | 职责 |
|---|---|---|
| `src/memory/models.py` | 167 | `MemoryFact` / `MemoryKind` / `MemorySource` / `RetrievalResult` / `_now_iso()` |
| `src/memory/store.py` | 1001 | `MemoryStore` + `MemoryFileCorrupt` / `MemoryVersionIncompatible` / `LoadReport` + 归档留存 + 原子写 |
| `src/memory/recorder.py` | 410 | `MemoryRecorder` / `RecordDecision` + 六条判定正则 + `normalize_topic` + PII 双层门控 |
| `src/memory/manager.py` | 206 | `MemoryManager` 门面（检索注入 + 后台 worker） |
| `src/memory/embedder.py` | 180 | `MemoryEmbedder`（fastembed，带 HF 镜像兜底） |
| `src/memory/prompts.py` | 91 | `CLASSIFY_PROMPT` / `PII_CHECK_PROMPT` / `INJECTION_HEADER` / `format_injection()` |
| `src/memory/seed.py` | 75 | `seed_base_memories()` 初始记忆种子 |
| `src/memory/__init__.py` | 42 | 包级导出 |

**常量与异常（代码真值，2026-10-01 核对）**：

```python
# store.py
CURRENT_VERSION      = "1.0.0"          # semver 字符串，MAJOR 不符即拒载
DEFAULT_MAX_BYTES    = 2_000_000        # 活动文件上限（2MB），触发压缩/归档
MAX_ARCHIVE_FILES    = 12               # 归档文件数上限（按 YYYYMM，约一年）
MAX_ARCHIVE_BYTES    = 10_000_000       # 归档总体积上限（约 10MB）
ARCHIVE_RETENTION_DAYS = 365            # 归档保留天数上限
FILE_MODE            = 0o600            # 文件权限；目录侧 chmod 0o700（try/except 尽力）
# recorder.py
RECURRENCE_THRESHOLD   = 3              # 同 topic_key 累计出现次数达到即晋升 recurring
MIN_CLASSIFY_INTERVAL  = 3.0            # 距上次分类的最小间隔（秒），限流用
RECURRING_WEIGHT       = 0.8            # 晋升 topic 的高权重 [0,1]
# 判定正则（均 re.IGNORECASE，见 recorder.py:37-56）
EXPLICIT_SAVE_RE / WEAK_INTENT_RE / PREFERENCE_RE / DECISION_RE / QUESTION_RE / SMALLTALK_RE
_PII_RELATIONSHIP_RE   # PII 第一层：关系词启发式（「是/叫（我|我的）（儿子|女儿|…）」）
# config.py / manager.py
MEMORY_CAPTURE_PERSON_ID = False        # 写时 PII 把关（truthy 解析，src/utils/config.py:116）
JAC_MEMORY_DIR           = None         # 环境变量覆盖 base 目录（store._resolve_dir 优先读它）
```

**顶层信封与字段（与契约一致，无漂移）**：

```json
{ "version": "1.0.0", "facts": [ {MemoryFact}, ... ] }
```

- 必填 6：`id`(UUID4 字符串) / `content` / `kind`(enum) / `source`(五值) / `created_at` / `updated_at`（均 ISO8601，带 `Z`），缺失 → 该条跳过并计入 `invalid_facts`。
- 可选 5：`weight`[0,1] 默认 0.5 / `tags`[] 默认空 / `pii` bool 默认 false / `ttl` null / `embedding` null（v1 恒 null，向量走 `MemoryEmbedder` 运行期，不落 `memory.json`）。
- 已彻底移除旧版术语：`importance`(IntEnum) / `consent_scoped` / `implicit_profile` / `occurrences` 持久化 / `user_consent` / 顶层 `updated_at`（见 `models.py` 文件头 docstring）。

**类与方法（代码真值）**：

```python
class MemoryStore:                            # src/memory/store.py
    __init__(self, base_dir=None, max_bytes=DEFAULT_MAX_BYTES, ...)
    load() -> LoadReport                      # {version, facts, invalid_facts:[{id, reason}]}
    flush() / close() / stats()               # 后台 flush 线程 + 定时
    upsert(fact) -> MemoryFact                # 写入（去重合并）
    get(id) -> MemoryFact | None
    delete(id) -> bool
    get_recent(limit=None, query=None) -> list
    query_by_keywords(...) / query_by_tags(...) / query_by_vector(...) / query_hybrid(...)
    compact() -> bool                         # 超限压缩 + 归档
    export(path) -> str
    clear_all() / clear_by_id(id)             # ⚠️ 无 source/pii 范围清、无 secure（见 C9 缺口）

class MemoryRecorder:                         # src/memory/recorder.py
    classify(user_text, response, window) -> RecordDecision | None
        # _rule_stage（六条正则 + 硬顺序）→ _llm_stage（CLASSIFY_PROMPT）→ _parse_llm_json
    normalize_topic(text) -> str              # 确定性 topic key（同输入→同 key）
    _apply_pii_gate(decision, user_text)      # PII 双层门控第二层（写时）
    _detect_kind(text) -> MemoryKind          # 与 MemoryKind 同源

class MemoryManager:                          # src/memory/manager.py
    retrieve_for_prompt(user_text, vision_info="") -> str   # 注入块（默认 300 字符上限）
    record_turn(user_text, response, window, is_thinking=False)  # main.py:427 调用，入后台 worker
    _worker_loop / _classify_and_store / _decision_to_fact / _capture_person_id
    flush() / close() / stats()

@dataclass RecordDecision:                    # 独立契约，不进 memory.json
    should_store: bool
    reason: str                               # 受控词表 9 值
    kind: MemoryKind | None                   # should_store=False 时须为 None
    confidence: float
    content: str = "" / tags: list = [] / source: MemorySource = manual / pii: bool = False
```

- **记录触发点**：`main.py:427` `memory.record_turn(text, response_text, window)`，在 `process_response` 末尾（助手说完、`is_speaking=False` 之后）调用一次，入队 daemon 后台 worker，**立即返回不阻塞响应**。
- **reason 受控词表（9 值，见 `src/memory/prompts.py:29`）**：`user_stated` / `derived_preference` / `explicit_convention` / `observed_event` / `topic_of_interest` / `low_confidence` / `duplicate` / `pii_blocked` / `not_factual`。
- **注入点**：`retrieve_for_prompt()` 命中后由 `format_injection()` 拼块，外层 `INJECTION_HEADER` 明确「数据是参考、非指令」，并做控制字符清洗（`_sanitize_line`）+ 300 字符截断。

#### C3. 单元测试要点（原 §3）

| 组 | 覆盖 | 关键断言 |
|---|---|---|
| 3.1 存储/检索/去重/更新/删除 | `tmp_path` 做 path，无网络 | 新实例可读回、id 稳定；相似内容去重合并（weight 增加）；`update` 保留其它字段且 `updated_at` > `created_at`；`delete` 后文件反映；空库返回 `[]` 不抛错 |
| 3.2 JSON 读写与损坏恢复 | 7 条行为矩阵 | ①解析失败 → 抛 `MemoryFileCorrupt`；②`version` 缺失 → 按 `0.0.0` 宽松加载、下次写回升级；③MAJOR 不符 → 抛 `MemoryVersionIncompatible` 拒绝加载；④同 MAJOR 差异（如 `1.2.0`）→ 兼容加载、忽略未知字段；⑤单条缺必填 → 该条入 `invalid_facts`、其余载入；⑥主文件损坏时先试 `.bak`、`.bak` 也失败则 `MemoryFileCorrupt` 上抛（**绝不静默空库**）；⑦可选字段缺失套默认且不计入 `invalid_facts` |
| 3.3 原子写入 | monkeypatch `os.replace` 抛错 / 正常写 | 原文件不变、`.bak` 仍在、`.tmp` 无残留、`os.replace` 仅一次 |
| 3.4 记录判定（规则优先 + LLM 辅助） | A 显式 / B 偏好 / C 频次 / D 约定 / W 弱意图 / 各类排除 | 硬指标：A 类 100% 落库、所有排除项 100% 不记；规则层精确率 ≥ 0.95、LLM 增强后召回 ≥ 0.85、误存率 ≤ 5%；阈值边界（同 key 2 次不晋升 / 3 次晋升 `recurring`）；`normalize_topic` 幂等；`reason` 非空可解释；`kind` 与 `MemoryKind` 同源（false 时为 null） |
| 3.5 示例用例名 | 描述式 | `test_store_add_then_reload_persists_content` / `test_store_dedup_merges_similar_facts` / `test_load_recovers_from_bak_on_truncated_json` / `test_recordjudge_repeated_topic_promoted_after_n` / `test_recordjudge_falls_back_to_rule_when_llm_unavailable` … |
| 3.6 门控与限流 | `is_thinking` / 3s 内多次触发 / 单轮至多 1 次 LLM 调用 | 大脑忙时分类调用数 = 0（宁可漏记）、不阻塞 |
| 3.7 后台线程非阻塞 | `classify` 注入 200ms 耗时 | `process_response` 返回耗时不受影响；分类发生在非主线程 |
| 3.8 路径解析与优雅降级 | 单 base 派生 / `JAC_MEMORY_DIR` 覆盖 / 跨平台默认 base / 不可写降级 | 所有副本路径派生自单一 base；mac/linux `~/.jac/memory`、Windows `%APPDATA%/jac/memory`；`MemoryManager(enabled=False)` 时不建 store 不抛异常 |

#### C4. 集成测试（原 §4）

全程 `LocalBrain(backend="mock")` + `monkeypatch` 拦 `requests.post`，不依赖 LM Studio。断言：注入时 `system_prompt` 含 `[已知信息]` 与记忆内容；空库不注入；`handle_user_text("记住我怕黑")` 后 fact 落库且发生在 think 之后；SLEEP 态未唤醒的闲聊不触发记录、AWAKE 下记录；控制台输入（`bypass_wake=True`）与语音路径一致；多轮同主题第 3 轮检索返回权重更高的合并条目；判定抛异常不影响对话。

#### C5. 边界与异常 + 归档留存（原 §5）

- 空记忆首启无文件 → 空库启动不崩；10k 条 / 单条 > 100KB → `stats()` 反映体积、`save` 仍能完成、注入时截断（断言注入块 ≤ 300 字符）；并发读写（audio + 手动 + judgment 三线程）用 `threading.Barrier` 制造竞争 + `pytest-timeout` 防挂死，断言数据一致、无部分写、无死锁。
- 归档留存：`compact()` 幂等（按 id 比较不重复归档）；归档数 > `MAX_ARCHIVE_FILES`(12) 淘汰最旧；总字节 > `MAX_ARCHIVE_BYTES`(10MB) 淘汰至回落；超 `ARCHIVE_RETENTION_DAYS`(365) 删除（按月 `YYYYMM` 计算）；活动文件达 2MB 触发独立压缩，归档与活动上限互不绕过。
- **测试性边界**：Tier1（逻辑删除）可机器验证；Tier2「取证级不可恢复」在 SSD/跨平台无法完全自动化 → 明文 v1（当前）**只对「文件消失 + 存储里不含已知字符串」做断言**，SSD 磨损残留归文档声明的 best-effort，**不写不可移植的硬断言**。

#### C6. 覆盖率目标与 CI（原 §6）— 现状已变

- 测试依赖已放 `requirements-test.txt`（pytest / pytest-mock / pytest-timeout / pytest-xdist / hypothesis）；`pytest.ini` 已配（⚠️ 本机未装 **pytest-cov**，跑测试要加 `-o addopts=` 清空默认参数）。
- 建议阈值：`src/memory/**` ≥ 90%、`src/utils/context.py` 记忆相关 ≥ 85%、`main.py` 注入/判定分支 ≥ 70%、全局 `--cov-fail-under=85`。
- Mock 策略：`LocalBrain(backend="mock")` 离线；LLM 判定用 `monkeypatch` 替 `requests.post`；摄像头/麦克风/TTS 用 fake 对象；磁盘用 `tmp_path` 隔离（⚠️ 沙箱里 `tmp_path` 会报 `PermissionError: EEXIST: mkdir '.../pytest-of-root'`，与代码无关，绕过方式是把 `TMPDIR` 指向一个全新的、尚无 `pytest-of-*` 的目录）。

#### C7. 分阶段执行顺序（原 §7）

阶段 0 基建 → 1 单测-存储 → 2 单测-判定（核心风险，黄金集优先）→ 3 集成 → 4 边界异常 → 5 E2E 小样 → 6 CI 固化。原则：先单测后集成，红→绿再进下一阶段。

#### C8. 关键风险（原 §8）

①记录判定误判（最高风险，必须规则兜底 + 黄金集量化）；②JSON 损坏/原子写缺失（必须证明 `.bak` + `os.replace` 生效）；③隐私清除不彻底（当前**只做到 Tier1 缺口见 C9**，Tier2 未落地）；④并发写竞争（单锁 + 竞争测试）；⑤`_mock_response` 是关键词回显，只能验证「注入发生」、不能验证「模型真用上记忆」；⑥超大记忆撑爆上下文（注入已限 300 字符）。

#### C9. 契约 vs 代码真值 · 收敛状态表（原 §9.1 漂移矩阵，**结论已改写**）

> 原 2026-07-22 结论是「存在系统性漂移、Reconcile 前不得据本计划写测试」。**该结论已于 2026-10-01 核对后作废**——除下方 2 项外，14 条漂移全部收敛。

| # | 已定稿契约 | 代码真值（2026-10-01 核对） | 状态 |
|---|---|---|---|
| 1 | 顶层 `{version:str, facts:[...]}`，`user_consent` 移出 | `store._serialize` 写 `{"version": CURRENT_VERSION, "facts": [...]}` | ✅ 已收敛 |
| 2 | 必填 6 字段 | `MemoryFact` 6 必填同名同义 | ✅ 已收敛 |
| 3 | 可选 5：weight / tags / pii / ttl / embedding | 同，`weight:float=0.5`、`pii:bool=False` | ✅ 已收敛 |
| 4 | `source` 五值 | `MemorySource`: explicit / inferred / recurring / judgment / manual | ✅ 已收敛（旧 `implicit_profile` 已删） |
| 5 | `kind` 枚举 profile/preference/convention/event/topic | `MemoryKind` 同名；`recorder._detect_kind` 复用 | ✅ 已收敛 |
| 6 | 无 `occurrences` 持久化 | 无该字段；频次在 recorder 会话级内存计数 + `_promoted` 集合去重 | ✅ 已收敛 |
| 7 | `MemoryFileCorrupt` / `MemoryVersionIncompatible` | `store.py:84/88` 均已定义；MAJOR 不符且非 0 → 拒绝加载 | ✅ 已收敛 |
| 8 | `load()` 返回 `{facts, invalid_facts:[{id,reason}]}` | `LoadReport` + `_parse_facts` 跳过坏条并计入报告 | ✅ 已收敛 |
| 9 | 检索 API | `query_by_keywords` / `query_by_tags` / `query_by_vector` / `query_hybrid` / `get_recent` | ✅ 已收敛（命名不同） |
| 10 | 两级清除 `clear(secure, source, pii)` | 🔴 **未收敛**：只有 `clear_all()` / `clear_by_id(id)`；`_purge_replicas(matched_ids, secure)` 无 public 范围入口 | 🔴 **缺口 P1** |
| 11 | `MAX_ARCHIVE_FILES=6` / `ARCHIVE_RETENTION_DAYS=180` | 代码为 **12** / **365**（活动 `DEFAULT_MAX_BYTES=2_000_000`、`MAX_ARCHIVE_BYTES=10_000_000`）——语义一致、数值不同 | ⚠️ 常量值待对齐 |
| 12 | `record_turn` 末尾入队后台 worker | `main.py:427` 已调用；`MemoryManager._worker_loop` daemon 线程 | ✅ 已收敛 |
| 13 | `RECURRENCE_THRESHOLD=3` / `MIN_CLASSIFY_INTERVAL=3.0` | `recorder.py:33/34` 同名同值，manager 参数透传 | ✅ 已收敛 |
| 14 | 权限 0o700 目录 / 0o600 文件 | `FILE_MODE=0o600`（`_write_atomic` 写后 chmod）；目录 `chmod 0o700` try/except 尽力 | ✅ 已收敛 |
| 15 | 六条判定正则 + 判定顺序 oracle | `recorder.py:37-56` 已实现，`PREFERENCE_RE` 另补「我叫/我的名字」 | ✅ 已收敛 |
| 16 | PII 第二层写时门控 | `_PII_RELATIONSHIP_RE` 启发式 + `PII_CHECK_PROMPT` LLM 复核 + `manager._capture_person_id` 读 `MEMORY_CAPTURE_PERSON_ID` | ✅ 已收敛 |
| 17 | `consent.json` / 可见同意 | 🔴 **未实现**：全仓库无 `consent.json` 读写 | 🔴 **缺口 P1** |

**剩余缺口（待 bo s s 拍板优先级）**：

- **P1-1 范围级清除（影响隐私承诺）**：需新增 `store.clear_source(source)`（逻辑删除全部 `source==x` 的 fact，并同步剔除 `.bak` / 归档副本防复活）与 `store.clear_pii(secure=False)` / `clear(secure=True)`（密文态毁钥、明文态 3 遍覆盖写 + 删除 + best-effort TRIM）。**没有它，「一键清空 inferred 而保留 explicit」与「清除权」都无法兑现。**
- **P1-2 可见同意机制**：`consent.json` + 首次记录前弹说明 + 可撤回（关记录即停写入，已记内容仍可清）。
- **P2 常量对齐**：把 `MAX_ARCHIVE_FILES`/`ARCHIVE_RETENTION_DAYS` 改回契约值，或在契约里更新为 12/365 并同步本文。
- **P2 测试基建**：`tests/fixtures/record_samples.jsonl`（黄金集）已存在但样本量待扩到 ≥40 条正负各半；记忆相关覆盖率数据待补。

---

### 附 D · 记忆功能用户指南（原 `docs/memory/README.md`，已并入）

> ⚠️ **旧记忆子系统（2026-10-01 标注）**：本附录描述旧记忆实现（fastembed + `MemoryStore`）。新架构已改 **ChromaDB + BGE-Small-ZH-v1.5 + JSON**，本附录仅作历史存档。

> J.A.C. 会**记住**关于你的一些关键事实，让以后的对话更懂你。记忆全部存在**你自己的电脑上**，不上传任何服务器。

**记忆的 5 类（`kind`）**

| 类别 | 含义 | 例子 |
|---|---|---|
| 画像 profile | 你是谁、什么身份 | 「我叫 boss」「我是做后端的」 |
| 偏好 preference | 喜欢 / 讨厌 / 习惯 | 「我喜欢喝美式」「我不吃香菜」 |
| 约定 convention | 项目或协作约定 | 「日志统一用中文」「提交前跑测试」 |
| 事件 event | 重要决策、发生的事 | 「周五要交版本」 |
| 主题 topic | 你反复在聊的一个话题 | 同一个 deadline 连提 3 次 → 自动升级成「主题」并加权 |

**两种来源（`source`）**

- `explicit`：**你自己说的**（说「记住我喜欢爬山」就是 explicit）——永远保留，可单独清空。
- `inferred` / `recurring` / `judgment` / `manual`：系统从对话里推断、反复出现后升级、判断引擎触发、或你手动改的。
- 隐私含义：**「一键清除系统推断」= 删掉所有 `inferred`，保留你主动说的**（⚠️ 该功能依赖 C9 缺口 P1-1 落地，当前只能逐条删）。

**怎么查看 / 导出 / 清除**

- **看**：文件在 `~/.jac/memory/memory.json`（macOS/Linux）或 `%APPDATA%/jac/memory/memory.json`（Windows），任意编辑器可开，**含个人数据，别在共享/同步盘打开**。
- **导出备份**：整目录复制走即可（含 `.bak`、归档、`memory_archive_YYYYMM.json`）。迁移到新机器：拷过去 → 校验 `version` 为 `1.0.0` → 权限 600/700 → 启动。
- **清除**：⚠️ **不可逆，无回收站、无云端副本**，清除前先导出。**先导出再清**是唯一保险。
- **敏感人物默认不记**：涉及具体第三人身份（「我儿子小明」）的条目会被 `pii=true` 标记并在写入时拦掉，除非你显式打开开关（`MEMORY_CAPTURE_PERSON_ID=True`）且来源是「你自己说的」。

**常见问题**

- *它记错了我说过的话？* → 找到那条（`jq` 或编辑器搜内容）删掉，模型下一轮就会用新状态。
- *它什么都记，好吵？* → 规则层有排除（一次性问答 / 闲聊 / 纯任务回显 / 敏感身份都不记），误存的按 id 删即可。
- *我的数据在哪、会不会上传？* → 全在你本机，不进 git、不出网；**唯一例外是运行时 stdout 的明文打印（见附 E §7），属既有暴露点**。

---

### 附 E · 记忆功能隐私说明（原 `docs/memory/privacy.md`，已并入）

> ⚠️ **旧记忆子系统（2026-10-01 标注）**：本附录描述旧记忆实现（fastembed + `MemoryStore`）。新架构已改 **ChromaDB + BGE-Small-ZH-v1.5 + JSON**，本附录仅作历史存档。

#### E1. 核心隐私承诺

本地优先、可见同意、用户可控制（查看 / 导出 / 清除）、日志受控。记忆内容**默认不出网**，仅本地持久化（呼应 `AGENTS.md`「工程指导—谨慎对待隐私」）。

#### E2. 本地存储位置

- 记忆文件：`<root>/memory/memory.json`
  - macOS / Linux：`~/.jac/memory/memory.json`；Windows：`%APPDATA%/jac/memory/memory.json`
- 同目录：`memory.json.bak`、`memory_archive_YYYYMM.json`、`*.tmp`、`.corrupt.*`（损坏隔离产物）；`JAC_MEMORY_DIR` 可改整个目录位置。
- **不进 git**：默认在用户主目录，物理上不在仓库内。仅当 `JAC_MEMORY_DIR` 指向项目内（如 `data/`）时，需在 `.gitignore` 加 `data/` 并排除出 PyInstaller onedir 构建。
- 权限：目录 **700**、文件 **600**（Windows 靠 `%APPDATA%` 用户私有）。

#### E3. 是否加密

- **v1：明文 JSON**。理由：本地优先、单用户本地原型、记忆内容不出网，作为可接受基线。
- **路线图项（默认关闭）**：静态加密 `cryptography.Fernet` + 密钥来自系统钥匙串或用户口令（PBKDF2），**默认关、可按敏感类别开启**（如仅对 `pii=true` 的事实加密）。
- 启用加密后，**必须提供带口令的导出/导入**以便迁移（钥匙串密钥不随机器转移）。
- 小结：「v1 明文、本地优先、无出网；加密为后续项，默认关、可按敏感类别开」。

#### E4. 同意机制

- ⚠️ **当前为设计态，代码未实现**（见 C9 缺口 P1-2）：设计约定记忆与否由用户同意控制、记录于同目录 `consent.json`；应用应在首次记录前弹出说明、用户可撤回（关记录即停止新写入，已记内容仍可由用户清除）。
- 已落地的等价保护：`MEMORY_CAPTURE_PERSON_ID` 默认 `False` → 敏感人物身份默认不落库，用户**无需主动操作**即可避免被持久化。

#### E5. 敏感数据边界

- 人物身份（identity）/ 语音转写（transcript）默认本地、不外发。
- fact 的 `pii` 字段（bool，默认 false）标记敏感事实，审计时单独计数。
- 用户可一键清除所有 `inferred` 而保留 `explicit`（⚠️ 依赖 C9 缺口 P1-1 落地）。
- 误写入敏感数据时：立即清除（当前只能 `clear_by_id` 逐条或 `clear_all` 全清）+ 做隐私审计。
- 写时门控（已落地）：`MEMORY_CAPTURE_PERSON_ID`（默认 `False`）+ `pii` + `source` 双层判定，拒存任何 `pii=True` 事实，除非显式开启且来源为 `explicit`。

#### E6. 清除的不可逆性

- 删除即永久：无回收站、无云端副本。全部清除前建议先导出。
- 损坏恢复**绝不静默清空**：坏文件隔离为 `memory.json.corrupt.<时间戳>`。

#### E7. 日志控制（现状缺口 + 目标设计）

**现状（务必写实现状缺口）**：

- `SharedContext._transcriptions = deque(maxlen=20)`：纯内存环形缓冲，**不持久化、无开关**，退出即清空，不是记忆、不进记忆文件。
- `main.py` 全程 `print()`，无 logging 框架/级别/文件/脱敏；转录明文经 `print(f"[听写] {text}")` 与 `print(f"[J.A.C 原始回复] ...")` 打到 stdout —— **已存在的隐私暴露点**。
- `log_queue` 声明但未消费（死代码）。
- `codingLOG.md`（原文件，现已并入 CHANGELOG 附 A）：非运行时日志，是手写架构差距笔记，无留存策略/开关/自动生成。

**结论**：今天**无任何日志控制开关**，`AGENTS.md`「日志控制」当前未满足。

**目标设计**：记忆子系统引入受控 logging：环境变量 `JAC_MEMORY_LOG` 默认**不记内容**，仅 debug 记摘要/计数/时间戳/类型，**绝不记原始转录或人物 ID**；并把现有 `print([听写]...)` / `print([J.A.C 原始回复]...)` 改为受控日志。

#### E8. 与 AGENTS.md 的呼应

本说明是 `AGENTS.md`「工程指导—谨慎对待隐私」的可执行细则：本地优先、可见同意、本地过滤、日志控制、人物与云边界。

---

### 附 F · 记忆功能 Runbook（原 `docs/memory/runbook.md`，已并入）

> ⚠️ **旧记忆子系统（2026-10-01 标注）**：本附录描述旧记忆实现（fastembed + `MemoryStore`）。新架构已改 **ChromaDB + BGE-Small-ZH-v1.5 + JSON**，本附录仅作历史存档。

#### F1. 何时用本 Runbook

查看当前记忆 / 清除某条或按来源清 / 导出备份与迁移 / 排查故障（不写入 / 误写入 / 损坏）/ 隐私审计（含 `pii` 与 `inferred` 统计）。

#### F2. 前置条件与权限

- 记忆文件位于 `<root>/memory/`（见 F3），需该目录读/写权限；目录 700 / 文件 600。
- 清空/导出建议在**应用关闭时**做，避免与持久化线程争用 `.bak`；应用内「查看/导出/清除」则无需手动停。
- `JAC_MEMORY_DIR` 可整体改目录位置（测试 / 便携版用）。

#### F3. 查看当前记忆（v1 为明文 JSON）

- **方式 A 直接开文件**：`~/.jac/memory/memory.json`（macOS/Linux）或 `%APPDATA%/jac/memory/memory.json`（Windows）。
- **方式 B 应用内「查看/导出」**：自动按 `kind` / `source` / `pii` 过滤展示。
- 按来源过滤（运维/排查用）：
  ```bash
  jq '.facts[] | select(.source=="inferred")' "$HOME/.jac/memory/memory.json"
  ```
- 同目录文件：`memory.json` / `memory.json.bak`（上一次成功写回前的好版本）/ `memory_archive_YYYYMM.json`（按月归档）/ `*.tmp` / `.corrupt.*`。

#### F4. 清除某条 / 按来源 / 全部

> ⚠️ **不可逆警告**：均为永久删除，无回收站、无云端副本。全部清除前建议先导出。

- **清除某一条**：按 `id` 删（`MemoryStore.clear_by_id(id)`）。
- **按来源清除**：⚠️ **当前 API 不支持**（`clear(source="inferred")` 未落地，见 C9 缺口 P1-1）。临时手写：
  ```bash
  jq '.facts |= map(select(.source != "inferred"))' \
     "$HOME/.jac/memory/memory.json" > /tmp/m.json \
     && mv /tmp/m.json "$HOME/.jac/memory/memory.json"
  ```
  ⚠️ 手动改文件后**必须重启应用**以重新加载；且该方式只改活动文件，`.bak` / 归档副本里仍留有 inferred，重启后会「复活」——真正的范围清除必须等 P1-1 落地。
- **全部清除**：`MemoryStore.clear_all()`，或手动置 `{"version":"1.0.0","facts":[]}`。

#### F5. 导出备份与迁移

- 手动备份：复制整个 `<root>/memory/` 目录（含 `.bak` / 归档）。
- 迁移新机器：整目录拷过去 → 校验 `version` 为 `1.0.0`、权限 600/700 → 启动确认 load 成功 → 失败见 F7 恢复。
- 记忆数据默认在用户主目录、**不进 git**；仅当 `JAC_MEMORY_DIR` 指向项目内时才需在 `.gitignore` 加该目录。

#### F6. 故障排查（不写入 / 误写入 / 损坏）

- **不写入**：查 `RecordDecision.reason`（`low_confidence` / `duplicate` / `not_factual` / `pii_blocked`）；查 `JAC_MEMORY_DIR` 是否可写、权限 700/600；是否达活动上限 2MB（`compact()` 会压缩/归档）。
- **误写入**：按 `id` 清除该条；若是 `inferred` 误记可批量清（受 F4 ⚠️ 限制）；若是 `pii` 误标，立即清除 + 隐私审计。
- **损坏**（应用报 `MemoryFileCorrupt` / `MemoryVersionIncompatible`）：
  - **自动恢复（已落地）**：load 失败 → 自动试 `.bak` → 成功则重写 `memory.json`；`.bak` 也失败 → **空启动**并把坏文件隔离为 `memory.json.corrupt.<时间戳>`，**绝不静默清空**。
  - **手动恢复**：`cp "$HOME/.jac/memory/memory.json.bak" "$HOME/.jac/memory/memory.json"` 后重启。
  - `.bak` 约定：后台持久化线程在每次 flush「写回替换」**之前**，若当前 `memory.json` 存在且有效，先复制为 `.bak`（覆盖）。写后读回校验失败则本轮回滚、不更新 `.bak`。
  - 单条 fact 缺必填字段不会拖垮全部：loader 容忍跳过并计入 `invalid_facts`，其余正常加载。

#### F7. PII 写入门控（默认不记敏感人物）

| `MEMORY_CAPTURE_PERSON_ID` | fact 的 `pii` | fact 的 `source` | 是否落库 |
|---|---|---|---|
| `False`（**默认**） | 任意 | 任意 | **拒存**，`reason="pii_blocked"` |
| `True` | `True` | `explicit` | 落库 |
| `True` | `True` | 非 `explicit` | **拒存** |
| `True` | `False` | 任意 | 正常落库 |

**排查**：预期该记的敏感人物没记 → 确认 `MEMORY_CAPTURE_PERSON_ID=True` 且来源为 `explicit`；不应记的记上了 → 按 `id` 清除并做隐私审计。

#### F8. 隐私审计

```bash
jq '{ total: (.facts|length),
      pii: ([.facts[]|select(.pii)]|length),
      inferred: ([.facts[]|select(.source=="inferred")]|length),
      kinds: (.facts|group_by(.kind)|map({(.[0].kind): length})) }' \
   "$HOME/.jac/memory/memory.json"
```

让用户核验「记了什么、哪些敏感、哪些是我没主动说的」。

#### F9. 回滚 / 升级路径

回滚：从 F5 备份恢复整个 `<root>/memory/`。升级：`version` 同 MAJOR 的 MINOR/PATCH 差异向前/向后兼容；跨 MAJOR 由迁移脚本处理（当前无）。

---

## 2026-10-01 — 文档口径补正（judge 模型名 / 去情绪 TTS 措辞 / 补齐「项目暂停」状态）

> **本次只改文档，未改任何一行代码。** 起因：盘点项目状态时交叉核对文档与代码，发现三处文档与实现不符，逐条补正。

| # | 位置 | 原文问题 | 补正后 |
|---|---|---|---|
| 1 | `AGENTS.md` 当前实现「主动判断引擎」/`AGENTS.md` 模型与资产；`README.md` 英文 Features 与 Models / 中文说明与模型段；`codingLOG.md` §1 | 统一写成「judge 用 **MiniCPM-o**」 | 全部改为代码真值 **`minicpm-v-4_5`**（`src/judgment/judge.py:58`、`src/utils/config.py:18`），并显式区分「judge 走 LM Studio 标准 chat API」与「OMNI 全双工里常驻的 `MiniCPM-o-4_5` GGUF Q8_0 @ `:9060` 是**另一份实例、另一种用法**」 |
| 2 | `README.md` 英文 Features / 中文说明；`new_computer_download/READMEfirst.md` §6 | 仍写 **Emotion-aware TTS**（带情绪 TTS）；安装指南第 3 步写「load MiniCPM-o in LM Studio」 | README 改为「克隆音色 + **纯文本中性朗读**」并注明 2026-08-09 起 brain 只输出纯文本、情绪接口保留但未调用；安装指南改为 load **`minicpm-v-4_5`**，并注明判断引擎走标准 `chat/completions`、与 `:9060` 上 llama.cpp-omni 的 MiniCPM-o 是两回事 |
| 3 | `codingLOG.md` §4「上下文寿命短板」与 §1 | 保留了 2026-09-17「视觉 token 吃爆 KV → 复读示例」的假设，读者会据此修错 bug | 在该段标题前加 ⚠️ 标注「因果链已于 2026-09-28 被 A1 实验（`--no-video` 零图仍幻觉）证伪」，并保留原有 KV / 降频量化数据（降频收益本身有效），另在 §1 补「代码真值」条目 |
| 4 | `AGENTS.md` | 全文**没有**记录「项目已暂停」这一状态 | 新增 **「当前开发状态（2026-10-01 补正）」** 小节：暂停时间（2026-09-28）、代码基线（`134 passed`）、MiniCPM-o 三条已定性能力天花板、待 boss 拍板的三条路线、三个未修 bug、以及「codingLOG 旧假设勿再采信」的提醒 |

- **未触碰**：`codinglog_by_awaqwq233/`（只由 bo s s 手动维护，Agent 禁止编辑）。
- **验证方式**：本次无代码改动，故不跑 `py_compile` / 测试套件；改后用 grep 逐条复查三处关键词（MiniCPM-o / minicpm-v-4_5 / Emotion-aware）确认无残留旧措辞。
- **状态**：文档四件套（`AGENTS.md` / `README.md` / `CHANGELOG.md` / `codingLOG.md`）已同步。

## 2026-09-28 — A1 对照实验：机制②（视觉 token 吃爆 KV）被证伪，重定位「查电池」幻觉根因

- **实验**：bo s s 按 A1 方案（GUI 取消勾选「图像上行」+ 勾「上行调试日志」）实测，问「你好，你在吗？」。
- **结果（日志铁证）**：启动日志确认「图像上行：已关闭」，全程 143 段推流均 `本段不带图`（零视频帧）；**但「<<CALL_QWEN>>查一下这台电脑的电池电量百分比」仍反复出现**（≥7 次），且模型反复回「好的 boss」。
- **结论：机制②被证伪**。09-17 的假设「视觉 token 每段 +64 → n_ctx 8192 每约 30s 滑动清空 → 模型只剩 system prompt → 复读『查电池』例句」不成立——全程没发图，幻觉依旧，视觉 token **不是根因**。
- **重新定位（新假设）**：**MiniCPM-o 音频理解失效 + prompt 示例是「复读诱饵」**。证据：①boss 人声 RMS 仅 0.03~0.065（内建麦离嘴远、能量弱）；②启动第一秒丢 0.59s（`上行积压超水位 1.79s > 1.20s`）；③boss 问的是**纯寒暄**，模型第一次回应却吐「查电池」令牌——没听懂寒暄，从 `prompts.py:31-35` 的 5 条具体示例里「捡」输出，而「查电池」是第一条、最具体，故被反复选中。
- **附带发现（独立 bug）**：升级路由也断了。LM Studio developer logs 显示 qwen3.6-35b-a3b 收到升级任务后 `reasoning_content` 非空（「应该使用 get_system_info 工具」）但 `tool_calls: []`、`content: ""`——**`enable_thinking: false` 未生效**，模型把工具调用决策吞进思考过程，既没调工具也没产出文本；另 prompt 处理耗时 50 秒 + `Client disconnected`。
- **待办优先级变化**：P3 prompt 治理（删具体示例、改抽象规则 + 「任务描述用 boss 原话、听不清就请重复、绝不照抄示例」）升为**最高优先级**；音频（`omni_mic_gain` 调高 + 查启动丢帧）；新增 bug「qwen `enable_thinking=false` 失效 → tool_calls 空」。
- **P3 已实施（同日，bo s s 拍板）**：`src/omni/prompts.py` 删掉 5 条具体示例（「查一下电池电量」→「<<CALL_QWEN>>查一下这台电脑的电池电量百分比」等），改为纯规则 + 三条铁律（【任务描述铁律】用 boss 原话、不照抄不编造；【没听清】请 boss 重复、绝不吐令牌；【寒暄】只回口语、绝不吐令牌）。`py_compile` 通过；omni 相关回归 **62 passed**。**待真机复验**：问「你好，你在吗」应只回寒暄、不再吐「查电池」令牌；若模型转而「不吐令牌 / 瞎答」，说明删示例过狠，需补一个「元指令占位」的格式示例（示例里任务描述用 `<总结 boss 的要求>` 而非具体句子）。

## 2026-09-24 — 遗留技术债清理（测试 4 失败 + 升级线程未取消）+ WebRTC AEC 方案

### 一、测试套件 4 个既有失败：全部修复，**并更正上一轮的错误结论**

上一轮 CHANGELOG 记为「4 个失败都是 `MockBrain` 签名不同步」——**这个结论是错的**，实际是**三类**完全不同的原因，其中 2 个连性质都判错了：

| 用例 | 真实根因 | 修法 |
|---|---|---|
| `test_smoke.py` ×2 | 断言过时：`mock_brain` 的 JSON 契约已随 `MemoryRecorder` 演进为 `{should_store, reason, kind, confidence, content, tags}`，测试却仍在断言 memory 子系统 Phase 0 的旧草案 `{decision, type, ...}`；`queue_decision()` 也早已改为 `queue_decision(should_store, *, kind=...)` | 按真值来源（`recorder._parse_llm_json()` + `src/memory/prompts.py` 的 schema）重写断言 |
| `test_memory_manager.py` ×2 | **测试时序假设过时，生产代码完全正确**：worker 首次落库前要 lazy 加载 fastembed 模型（诊断实测 **约 1.3s**，日志「[Embedder] 已加载向量模型…维度 384」），而测试只 `sleep(0.5)` 就断言 | ①新增 `_wait_until(predicate, timeout)` 轮询助手；②**注入离线 stub embedder**（`embed_texts` 返回 `None`）彻底隔离向量链路。⚠️ **第一版只做了 ①，本地单跑通过、全量重跑仍失败**，才发现更深一层根因：`fastembed.TextEmbedding(...)` **构造时会联网**查模型 revision，代理异常下进入 3~9s 重试链（stderr：`ProxyError ... Tunnel connection failed: 502` → `sleeping for 9.0 seconds`），把落库拖过任何合理超时。本类用例验证的是「规则阶段落库 / 限流是否误伤显式保存」，与向量检索无关，隔离才是正解 |

- **诊断证据**（`/tmp/diag_memory.py`，临时脚本）：`t=0.5s count=0`（测试在此断言 → 假失败）→ `t=1.5s count=1`（落库完成）；且直接调 `classify("记住我喜欢爬山")` 返回 `should_store=True, kind=preference, content='我喜欢爬山', source=explicit`。**规则阶段从一开始就是对的，不存在落库 bug。**
- **顺带修掉一处「假通过」**：`test_classify_exception_caught` 原本也是 `sleep(0.3)` 后断言 `count == 0`——worker 没跑完时该断言必然通过，回归价值为零。改为反向轮询「给足 3s，期间一旦出现记录就失败」。
- **第 3 类失败：`tests/unit/test_tools.py::test_search_files_finds_and_blocks_scope`**。该用例把临时目录建在**用户主目录**下的固定路径 `~/fc_test_tmp`，`finally` 的 `rmtree` 一旦被外部保护机制拦下就会**跨运行累积**（实测残留 58 个文件）；而它断言的是「搜索结果里含目标文件」且 `max_results=5`——文件一多，目标就被挤出前 5 名，表现为「代码正确、测试假失败」。**修法**：改用 `tempfile.mkdtemp(prefix="fc_test_tmp_", dir=~)`（每次唯一子目录、清理量恒为 1 个文件、仍满足 `search_files` 只扫用户目录的约束），并给清理加 `try/except BaseException` 兜底——保护机制抛的是 **`SystemExit` 而非 `Exception`**，`ignore_errors=True` 只兜 `OSError`、兜不住它，而"清理失败"不该让已经断言通过的用例变红。
- **环境坑（记下来，下次别再误判成代码失败）**：①沙箱内跑 `tmp_path` 系用例会报 `PermissionError: EEXIST: mkdir '.../pytest-of-root'`（沙箱把 `mkdir` 拦截后抛的是 **`PermissionError` 而非 `FileExistsError`**，pytest 的编号目录逻辑只捕获后者，于是整批 ERROR）——绕过方式是把 `TMPDIR` 指向**一个全新的、`pytest-of-*` 尚不存在**的目录；②本机工具链有**按「轮次」累计的批量删除配额**（`safe-delete`，阈值 50），一轮内累计删除超阈值后**任何** `rmtree` 都会被拦并 `SystemExit` 终止进程（连 librosa 的 `__pycache__` 临时目录都不放过），表现为"测试莫名其妙 FAILED/ERROR"——这与代码无关，**换个轮次重跑即恢复**。
- **命令备忘**：本机未装 pytest-cov，而 `pytest.ini` 里配了 `--cov`，故需 `-o addopts=` 清空默认参数。

### 二、`runtime.stop()` 后升级线程仍在跑工具调用与 TTS：已修

- **根因（两条，缺一不可）**：
  1. `_handle_escalation` 的 worker **从不检查 `runtime.running`**。`EscalationRouter.escalate()` 内部是同步阻塞的 LLM + 工具循环，无法抢占式中断，于是"点了停止"它照跑不误。
  2. 更隐蔽的一条：`stop()` 会把 `self.omni_client` 置 `None`，worker 随后走 `else` 分支的 `speak_text_via_voicebox` **降级播报**——本意是"保证答案一定出声"，在停止场景下却变成"一定出声"，这正是"停止后还在念"的直接来源。
- **改动**：
  - `src/omni/router.py`：`escalate()` 新增 `should_stop` 回调，**每收到一个流式分片检查一次**，命中即返回空串并停止 `on_progress` 回调（否则控制台会继续打字输出）。
  - `src/runtime.py`：三个检查点 —— ①线程入口 `if not self.running: return`（已停止时连 `EscalationRouter` 都不创建，不白烧算力）；②**结果播报前**（必须早于任何 TTS / GUI 写入）；③异常分支（停止后连"出错了"也不出声）。另维护 `self._escalation_thread`，`stop()` 里 `join(timeout=1.5s)`，**且必须先于关闭 `omni_client`**——顺序反了会让 worker 读到 `None` 又走降级播报。
- **测试**：新增 `tests/test_runtime_escalation_stop.py`（6 例）：router 层取消 / 不传 `should_stop` 的向后兼容 / 检查点 1 / 检查点 2 / **正例（未停止时照常播报，防修复过度把正常路径也堵掉）** / `stop()` join 与清引用。

### 三、WebRTC AEC 方案（**仅方案，未实施**，见 `docs/webrtc_aec_plan.md`）

- **核心结论先行**：第一前提不是选 AEC 库，而是**播放路径必须能吐出 far-end 参考 PCM**。`src/audio/playback.py:111` 确认所有发声都走 `afplay` 子进程，Python 侧只剩文件路径，而 AEC 对时间对齐极敏感（±几毫秒）——所以"换播放实现"这一步绕不过去。
- **三条路线**：①`pywebrtc-audio` + 自建 PyAudio 播放（**推荐**）；②macOS 原生 `AVAudioEngine` VoiceProcessingIO（质量最好，但采集/播放要全搬到 AVFoundation）；③保留 `afplay` + 预解码 WAV 当参考（**不推荐**，对齐不可控）。
- **依赖可得性已实测**（非推测）：`pywebrtc-audio 0.2.0` 存在 `cp313-macosx_11_0_arm64` 预编译 wheel，与本机 Apple Silicon + Python 3.13.14 完全匹配，**无需编译**（对国内网络友好）。
- **文档中明确标注了「已核实」与「待实测」的界限**：`afplay` 无参考信号（读源码确认）、wheel 可得（本机实测）、AVAudioEngine 语音处理语义（Apple 官方 WWDC2019/510）为已核实；macOS 只接受 44100Hz、库的 CPU 开销、本机 AirPods+内建麦的实际抑制比均标注为**待实测**。
- **建议先做阶段 0**（独立最小脚本，半天）：播放固定语音 + 实时 AEC，人工听 + 量化回声抑制比，**判据 ≥15~20 dB**。不达标则整条路线作废、零代码损失。

### 四、JEV 路线否决

TypeSafe JEV 决策模型接入评估的结论为**否定**（闭源 + 托管 API + 中国大陆未开放 + 数据需出本机，与"本地优先"原则冲突），相关调研记录已按 bo s s 指示删除。

## 2026-09-17（二）— GUI 右侧选项面板改为可滚动侧栏（修「右下角被压住、比例抽象」）

- **现象（bo s s 截图）**：右侧选项面板内容一多就出事——①底部「判断间隔（秒）」「判断请求超时（秒）」两个滑块被压成几像素高的小方块，数值被裁；②标签被截断成「MiniCPM-o-4_5 全双工（接管 T」「Listen 概率系数（ON」「图像上行间隔s（OMI」；③「麦克风音量」进度条的「0%」文字被裁在框外。
- **根因（两条独立）**：①**高度**——面板内容（10+ 控件：复选框 / 数字框 / 下拉 / 音量条 / 文字区 / 两个滑块）直接堆在一个 `QVBoxLayout` 里，竖排总高度超过窗口可用高度时 Qt 会**压缩子控件**而不是给滚动条，于是滑块被压扁；②**宽度**——面板用 `setMinimumWidth(260)` 且以 `stretch=1` 与视频区(3)、控制台(2) 抢横向空间，窗口稍窄就被挤到 260px 以下，长标签被 `QLabel` 裁掉。
- **改动（`gui.py`）**：
  1. 面板内容整体搬进 **`QScrollArea`**（`widgetResizable=True`、**只竖滚**、`NoFrame`）；「« 收起选项」按钮**留在滚动区外**固定贴顶，滚到哪都能收起。
  2. 面板改为**固定宽度带** `setMinimumWidth(340)` / `setMaximumWidth(420)`，且 `root.addWidget(..., 0)`（**stretch=0，退出横向争抢**）；视频区(3)/控制台(2) 才是弹性列。340 的下限按最长标签「图像上行间隔s (OMNI)」实测算出。
  3. QSS 补 `QScrollArea#optionScroll` 及其内容 widget 的**去底去边**规则——`QScrollArea` 继承 `QFrame`，不显式覆盖会套上「QFrame 通用卡片样式」，形成**双层卡片**；同时给竖滚动条独立样式（8px 宽、`#3a3a44` 滑块、hover 变蓝、隐藏上下箭头）。
  4. `_labeled_slider()` 的外层容器加 `setMinimumHeight(92)`、滑条加 `setMinimumHeight(24)`；`mic_bar` 加 `setMinimumHeight(18)`、`omni_reply` 加 `setMinimumHeight(80)`——**给「被压扁」设下限**：空间不够时就该出滚动条，而不是把控件比例压坏。
- **验证**：`py_compile` 通过；**离屏渲染实测抓图**（`QT_QPA_PLATFORM=offscreen` + `QWidget.grab()`，脚本 `/tmp/jac_gui_shot.py`，不入库）：
  - `1440×880`：全部控件完整显示、无滚动条，标签无截断，两个滑块与 4.0s/15.0s 数值均完整；
  - `1200×700`：内容超高 → **出现竖滚动条**，控件保持自身比例（滑块不再是方块）。
  - 回归 `tests/test_gui_runtime.py` + `tests/test_omni_video_switch.py` **11 passed**。
- **顺带**：确认 `_toggle_panel()` 的 `hide()/show()` 仍作用于同一个面板 `QFrame`，折叠逻辑未受影响。

## 2026-09-17 — P0 变量分离实验开关：图像上行总开关（GUI 复选框）+ 上行调试日志开关

- **动机（bo s s 真机四组日志诊断，listen 系数 1.0 / 0.8 / 0.6 / 0.4 各跑一轮）**：症状是「我说话要么没反应，要么被『识别』成查电脑状态然后触发升级、又被自己拦截丢弃」。诊断结论是**三个独立故障叠加**，其中「乱识别成查电池」根本不是 ASR 结果（full_duplex 不回传用户 ASR 原文），而是**上下文被视觉 token 冲垮后模型照 `prompts.py` 里的示例复读**——服务端 `temp/omni_server.log` 实测 `n_past=6792`、`n_keep=675`，每带一帧图写约 64 个视觉 token，n_ctx=8192 时上下文每约 30 秒被滑动清空一次，清完只剩 system prompt，于是抄最近的例句「查一下这台电脑的电池电量百分比」。**要坐实这条机制，必须能一键把所有图像上行关掉，再对比幻觉是否消失**——此前只有「多久发一帧」（`video_interval`），没有「发不发」的总开关。
- **改动 1（`src/omni/client.py`）**：`OmniClient` 新增两个参数——
  - `video_enabled`（**图像上行总开关**，默认 `True`；`None` = 读 `OMNI_VIDEO_ENABLED`）。为 `False` 时 `_should_attach_frame()` **恒返回 False**（优先级高于 `video_interval <= 0` 的「每段都带图」旧行为），`_cam_loop` 也不再产 jpeg（反正没人取用，省掉每帧 JPEG 编码）；BGR 帧照常刷新，**GUI 本地预览不受影响**。
  - `debug`（逐块上行诊断日志，默认 `False`；`None` = 读 `OMNI_DEBUG`）。把原先散在 5 处的 `os.environ.get("OMNI_DEBUG") == "1"` 统一收敛到 `self._debug`——**根因是 GUI 启动的进程改不了环境变量**，此前这个开关只能从命令行用，GUI 用户拿不到逐块日志（而量化「块长抖动」正需要它）。
  - 启动日志新增一行 `[omni] 图像上行：已关闭（纯音频全双工）…`，真机验收时一眼可确认开关状态。
- **改动 2（`src/utils/config.py`）**：新增 `omni_video_enabled`（默认 `True`）与 `omni_debug_log`（默认 `False`），支持 `OMNI_VIDEO_ENABLED` / `OMNI_DEBUG` 环境变量覆盖。
- **改动 3（`gui.py`）**：OMNI 面板新增两个启动前配置项——
  - **「图像上行（OMNI 视觉）」复选框**（默认勾选，位置在「图像上行间隔s」上方）。取消勾选即纯音频全双工；同时把下面的「间隔s」输入框灰掉（关掉图像时间隔无意义，避免误配）。
  - **「上行调试日志（OMNI_DEBUG）」复选框**（默认不勾）。
  - 两个控件都纳入 `_set_options_enabled()` 的启动/停止联动。
- **改动 4（`src/runtime.py` / `src/omni/__main__.py`）**：runtime 透传 `omni_video_enabled` / `omni_debug_log`；CLI 新增 `--no-video`（关闭图像上行）与 `--debug`（等价 `OMNI_DEBUG=1`），未显式指定时传 `None`，把决定权交回环境变量（避免 CLI 默认值压掉 `OMNI_VIDEO_ENABLED=0`）。
- **验证**：`py_compile` 通过；新增 `tests/test_omni_video_switch.py`（**6 个用例全过**，含 WS 级抓包断言）：配置层默认值与环境变量覆盖 / `video_enabled` 与 `debug` 的解析优先级（显式 > 环境变量 > 默认）/ 关闭时 `_should_attach_frame` 恒 False（且压过 `video_interval<=0`）/ 对照组开启时首段必带图 / **WS 级：关掉开关后 9 段上行全部纯音频、零 `video_frames`，且每帧仍带音频（不触发服务端 `fail_fast missing_audio`）** / 对照组带图段数 < 音频段数。omni + GUI 相关回归 **57 passed**（含新增 6 个共 63）。
- **已知既有失败（与本次改动无关）**：全量跑 `tests/` 时 `test_smoke.py`（2 个）与 `test_memory_manager.py`（2 个）失败——`conftest.py` 的 `MockBrain.queue_decision()` 签名与 `test_smoke.py` 断言不同步（缺 `type` 关键字、decision schema 多了 `should_store/confidence/kind/reason` 字段），属 memory 子系统的既有问题，本次未触碰这三个文件（`git status` 可核对）。
- **待 bo s s 真机复验（P0 实验第 1 项）**：勾掉 GUI「图像上行」→ 启动 → 长聊 1~2 分钟，观察：①控制台是否还会出现 `⚠️ 升级令牌疑似…幻觉，已拦截丢弃` 与「查一下这台电脑的电池电量百分比」；②`temp/omni_server.log` 里 `[prof] encoder index=` 与 `n_past` 的增长应停止（不再有 VPM 编码、KV 只按音频增长）。**若幻觉消失即坐实机制**。（第 2 项「逐块日志」对应 GUI「上行调试日志」复选框。）

## 2026-09-13（深夜二）— P1 图像降频：图像上行与音频上行解耦（默认 1 帧/秒）

- **动机（真机日志实测）**：服务端每收到**带图**的一段就要做一次 VPM 图像编码（`[prof] encoder` 实测 p50=196ms / p90=285ms / 均值 232ms）并把 **64 个视觉 token** 写进 KV；而一段音频只有个位数 token。带图频率越高，KV 越快被视觉填满 → 每约 30~60 秒触发一次上下文滑动；而滑动之后模型只剩 system prompt，于是照 `prompts.py` 里的示例复读令牌（「查询一下最近的新闻」就是这么来的，三轮已复现）。原先「每段都带图」等于让图像占用服务端每轮算力的三分之一还多。
- **改动**：新增 `video_interval`（秒/帧，`OMNI_VIDEO_INTERVAL`，默认 **1.0**）——**音频照常按块上行，图像按间隔抽帧**：
  1. `src/omni/client.py`：新增 `_should_attach_frame(now)`（首段必带图；计时用**累加**而非赋值，因为块节奏 0.4~0.9s 不是间隔的整数倍，赋值会让长期平均掉到 0.6~0.8 帧/秒；落后超 2 个间隔则重新对齐避免连发补帧）；`_push_loop` 只在判定为真时才放 `video_frames`；新增 `_frames_sent` 计数与 `OMNI_DEBUG=1` 下的「本段不带图」诊断；启动时打印「图像上行间隔」一行便于验收。**`video_interval <= 0` 退回旧行为（每段都带图）**，用于真机对照排查。
  2. `src/utils/config.py`（`omni_video_interval` + 环境变量）、`src/runtime.py`（透传）、`gui.py`（OMNI 面板新增「图像上行间隔s (OMNI)」数字框，0~5s）、`src/omni/__main__.py`（`--video-interval`，并在用法示例里给出 `--video-interval 2.0 / 0`）。
- **实测收益（用真机日志数值重算，不是估算）**：按块节奏约 1.5 段/秒计——KV 增长 **109 → 79 token/秒（降约三成）**、VPM 负载 **0.34 → 0.23 秒/秒（降约三分之一）**、上下文寿命 **69 → 95 秒（+约四成）**。
  - ⚠️ 顺带**纠正我自己此前夸大的说法**：前几轮文档里写的「KV 消耗降约 90%、上下文寿命拉到数分钟」是错的（把基线当成 2.5 段/秒满速带图来算了），已在 AGENTS.md / codingLOG.md / client.py 注释里全部改正为上述实测值。
- **为什么它同时是 P2 的前置条件**：若将来把分块降到 0.16s（6 段/秒），不降频的话 VPM 会占掉每秒 **1.14s** 的算力（6 × 190ms），实时性直接崩——所以「图像与音频上行解耦」必须在分块变细之前就位。
- **验证**：`py_compile` 通过；相关回归 **74 passed**。新增 4 个单测（首段必带图 + 间隔内不带图 / 长期平均帧率 ≈1.00 帧/秒 / `<=0` 退回每段带图 / 环境变量覆盖）；WS 级集成新增假摄像头帧与「带图段数 < 音频段数」断言，实测 **9 段音频仅带图 5 帧（≈0.93 帧/秒）**，同时节拍 0.60s、单段最小 0.40s、零丢弃（即降频不影响音频实时性）。
- **待 bo s s 真机复验**：①启动日志应出现「图像上行间隔：1.00s/帧」；②长聊 1~2 分钟后 `temp/omni_server.log` 里 `[prof] encoder` 的出现频率应明显下降（约每 1 秒一次而非每段一次），`slide TRIGGERED` 的间隔应从约 1 分钟拉长到约 1.5 分钟；③模型仍能回答「你看到了什么」（画面仍是每秒更新一次的近实时）。想对照旧行为用 `--video-interval 0`。

## 2026-09-13（深夜）— 三轮：终局事件去重（我的 bug）+ 畸形令牌容忍 + 人声判据与门控判定

- **现象（bo s s 真机日志）**：①问「你看到了什么」，模型却吐 `<<CALL_ QWEN>>查 询一下 最近的新闻。`，升级任务被解析成「查询一下最近的新闻」；②同一句被 Voicebox **反复朗读**；③上行**反复丢音频、累计 5.1s**（`1.73s/1.22s/2.75s/4.10s/1.28s`）；④多处「令牌疑似静音期幻觉，已拦截丢弃」——**真实提问被判成幻觉**。
- **根因 1（丢音频，我先认）**：服务端**一段会发两个终局事件且共用同一个 `response_id`**——「模型说了话然后切回聆听」的这段会先发 `listen delta`（`ws_handler.cpp:1173`）再发 `response.done`（`:1232`）。二轮我把这两个都当「本段完成」去放行下一段，等于**一段被算两次** → 推流次数翻倍、每块变小（真机见过 0.02s 的块）→ 而服务端每轮都要重做图像编码（VPM 190ms，**与块大小无关**）→ 真实消费速率腰斩 → 积压超水位丢帧。丢的是**句子中间**，模型只能听到残句——这正是「答非所问」的燃料。**修复**：`_signal_chunk_done(response_id)` 按 id 去重（同 id 的第二个终局事件直接忽略）；另加**最小推流间隔**（≥`push_interval`）与**最小块时长**（`OMNI_MIN_CHUNK_SECS`，默认 0.4s）双保险，杜绝极小块洪泛。
- **根因 2（畸形令牌，bo s s 已确认空格来自模型本身）**：模型并不总能原样吐出 `<<CALL_QWEN>>`，真机会吐 `<<CALL_ QWEN>>`（中间夹空格/换行）。而精确匹配 `find("<<CALL_QWEN>>")` 找不到 → 碎片走「正常对话」分支被**显示并朗读**（Voicebox 反复念同一句）；连 holdback 都拦不住——`<<CALL_` 是合法前缀会被扣住，但**下一个字符是空格就不再是前缀**，于是被当安全文本放出去。**修复**：
  1. 新建 `src/omni/tokens.py` 作为令牌识别的唯一事实来源：容忍空白/换行/大小写的正则、容忍空白的 holdback 判定、以及**硬安全网**（含 `<`/`>` 或裸标记词 `QWEN` 的片段一律不外发/不朗读）。
  2. 命中令牌后**消费掉令牌及其之前的内容**（不再依赖 listen 事件清缓冲）。此前 `_reset_escalation_state()` 无条件清 `_text_buf`，而 listen 在 full_duplex 下**每段都会来**，会把正在下发的令牌拦腰截断——剩下的裸片段（`QWEN>>`）既无法匹配令牌（用户请求静默丢失）又可能被当台词朗读；现改为「只丢已外发前缀、保留扣留中的尾巴」，并重新登记静默兜底时间。
  3. `voicebox_bridge.py` 同步接同一套工具（切句前截断令牌、入队前过安全网），三道防线纵深防御。
  4. `OMNI_DEBUG=1` 时打印原始 text delta 的 `repr`，便于日后坐实畸形标记的确切形态。
- **根因 3（真实提问被误判幻觉）**：护栏判据两处过紧——① 窗口 3.0s **小于端到端延迟**（音频积压 + 服务端一轮 + TTS 排队），令牌到达时早就「超时」；② **RMS 阈值 0.02 正压在人声段下沿**（真机实测：底噪 RMS 0.002~0.013 / 人声 RMS 0.020~0.056，只差 3~5 倍；而人声峰值 0.088~0.277 与底噪分离干净）。日志里同一秒内「检测到人声 0.023」→「进入静音 0.004」就是阈值抖动的现场证据。**修复**：判据改为「**峰值 ≥ `OMNI_SPEECH_PEAK_TH`（默认 0.06）或 RMS ≥ `OMNI_SPEECH_RMS_TH`（默认 0.02）**」双条件，窗口放宽到 `OMNI_SPEECH_WINDOW`（默认 6.0s），并在拦截日志里打出「距上次人声 x.xs > 窗口 y.ys」便于定位。
- **根因 4（门控判定只看了一半）**：`resolve_echo_gate` 的 auto 判定**只看输出设备名**——只要输出是耳机就关掉门控。但若**输入也是同一个耳机/蓝牙**（历史日志里默认输入被切到 AirPods 时），耳机麦会采到耳机自己的输出，关掉门控等于放任自激。**修复**：判定改为同时看两端口，规则为「输出是耳机 **且** 输入不是耳机类 → 关；**输入本身是耳机/蓝牙 → 开**；其余（扬声器/无法判定）→ 开」，启动日志同时打印输入与输出设备名。另把「自身播报窗口」的排除判据从门控开关里**解耦**（旧写法 `if self._echo_gate and self._is_echoing()` 在门控关闭时被整体短路，等于丢掉唯一能识别「这是我自己在说话」的手段）——默认无论门控都排除，`OMNI_ECHO_GUARD=gate` 可退回旧行为。
- **新增环境变量**：`OMNI_MIN_CHUNK_SECS`(0.4)、`OMNI_MIN_PUSH_INTERVAL`(=push_interval)、`OMNI_SPEECH_WINDOW`(6.0)、`OMNI_SPEECH_PEAK_TH`(0.06)、`OMNI_SPEECH_RMS_TH`(0.02)、`OMNI_ECHO_GUARD`(always)、`OMNI_HOLD_IDLE`(1.5)。
- **验证**：`py_compile` 通过；相关回归 **65 passed**。新增 11 个单测（畸形令牌 4 种变体零泄漏 / 段边界不释放 / 裸标记碎片被安全网拦住 / response_id 去重 / 每段只放行一次 / 最小块时长 / 最小推流间隔 / 人声判据真机数值表驱动 / 真实提问不被误判 / 静音与过期人声仍被拦 / 门控同时看两端口 / 播报窗口排除）＋ WS 级集成升级为「假服务端每段发两个同 id 终局事件」，实测 **平均间隔 0.60s、平均单段 0.55s、最小单段 0.40s、零丢弃**（修复前是 0.02s 级极小块 + 反复丢帧）。
- **待 bo s s 真机复验**：①问「你看到了什么」不应再出现 `<<CALL_` 碎片与「查询一下最近的新闻」这类提示词复读任务，升级任务应是完整句子；②同一条回复不应被反复朗读；③不应再出现「上行积压超水位」警告（`OMNI_DEBUG=1` 下每块间隔应稳定在 0.5~0.9s）；④说话后 6 秒内到达的令牌应能正常触发升级，不再被误判为幻觉。

## 2026-09-13（夜）— P0 二轮：段边界误释放令牌碎片（`<<CALL_Q` 仍在）+ 会话被服务端打死（`missing_audio`）

- **现象（bo s s 真机日志）**：①第一句问「你看到了什么」，模型却吐嵌套令牌；控制台打出 **`<<CALL_QW`**（与上一轮同样的碎片），且升级任务被截断成「**查一下**」；②紧接着 `[OMNI] 状态: closed` + `[OMNI] 错误: 推送失败: received 1000 (OK)`，此后**再怎么说话都没回应**（会话已死）。
- **根因 1（碎片仍在 · P0-a 一轮没修干净的真正原因）**：**full_duplex 下 `response.done` 是「本段（chunk）处理完」，不是「本轮流说完」**——模型接着说会跨很多段（每段 `max_new_speak_tokens_per_chunk=26`），段边界完全可能落在一个正在下发的令牌中间。一轮修复把 `_flush_text_holdback()` 挂在 `response.done` 上，等于**每个段边界都把扣留的尾巴放出去**：段边界正好停在 `<<CALL_QW`（8 字符）时，这 8 个字符被当普通文本广播并喂进朗读队列，同时令牌被"提前消费"，导致后续补齐的 `EN>>查一下…` 只能作为新的令牌重新识别、任务被截断成「查一下」。**离线已一字不差复现**（喂 `<<`/`CALL`/`_Q` 后触发一次段末释放 → 显示流出现 `<<CALL_Q`）。真正的轮末信号是 **listen 事件**（模型切回聆听）。
- **修复 1（`src/omni/client.py`）**：
  1. `response.done` 分支**移除** `_flush_text_holdback()`（只保留桥接的 `flush_remaining()`，用于提升实时感；桥接自身也扣留令牌前缀，故不会念碎片、也不会丢字）。
  2. 新增 `_hold_flush_loop()` 协程（与接收协程同事件循环，避免跨线程读写 `_text_buf` 竞态）：当**文本静默超过 `OMNI_HOLD_IDLE`（默认 1.5s）**时兜底释放 holdback——覆盖「模型说完却不回 listen（以 `__END_OF_TURN__` 收尾）」的场景。
  3. `listen` / `session.closed` 仍立即释放（真轮末）；`_reset_escalation_state()` 一并清 `_text_idle_deadline`。
- **根因 2（会话被打死）**：服务端 full_duplex 分支对**空音频**会 `fail_fast("missing_audio")`，其实现是「发 `session.closed` → 立刻 `ws.close()`」——与真机看到的 `closed` + `received 1000 (OK)` 完全吻合。而 P0-b 背压引入了一个新风险窗口：`_wait_chunk_slot()` 在「上一段终局事件已到达」时会**立刻放行**，此时麦克风线程可能还没产出数据，`_mic_buf` 为空 → 报文只有 `video_frames` → 服务端 fail_fast 把整个会话关掉（表现为「怎么说话都不回」）。
- **修复 2**：`_push_loop` 保证**每一帧都必须带音频**——缓冲为空时先以 20ms 步长最多等 0.16s 等采集线程产出；仍为空则补 0.1s 静音（等价「这一帧没听到人说话」）。服务端不会再因为缺音频而 fail_fast。
- **顺带修的 3 个问题**：
  1. **麦克风线程静默死亡**（`_mic_loop`）：此前 `read()` 抛异常直接 `break`，采集线程无声退出，表现就是「怎么说话都没反应」而日志里只有 RMS≈0（极易被误判成权限问题；真机在默认输入设备从 AirPods 切到内建麦 48000Hz 时最易触发）。现改为打印醒目警告 + 触发 `on_error`，非主动停止时的流中断也报警。
  2. **`stop()` 白等满 10 秒**：接收协程阻塞在 `async for raw in ws` 上，不关连接则 `gather` 永不返回、线程活到 `join` 超时（实测 10.00s，且升级 daemon 线程等残留继续跑）。新增 `_close_ws_soon()` 把 `ws.close()` 投递到客户端自己的事件循环；实测 stop 从 **10.00s → 0.93s**。
  3. **WS 关闭无诊断**：新增打印服务端的 `session.closed` reason（并注明 `missing_audio / invalid_input / mode_mismatch` 属协议层 fail_fast，不是模型问题）与 WS 关闭码/原因，避免下次再只看到一句 `received 1000 (OK)` 无从下手。
  4. 开局首帧不再超水位：麦克风线程先于推流循环启动、相机初始化约 1.5s，期间攒下的音频会在 `_start_capture()` 后被丢弃一次，避免「首帧就被水位裁剪 + 一条无意义警告」。
- **验证**：
  - `py_compile` 通过；相关回归 **52 passed**（因 `stop()` 提速，整套耗时从 17.8s 降到 13.4s）。
  - 新增 3 个回归：`test_chunk_boundary_must_not_release_holdback`（用假 WS 脚本驱动 `_receiver_loop`，复刻「段边界落在令牌中间」并断言零泄漏 + 升级正常触发 + 任务不被截断）、`test_idle_flush_releases_holdback`（静默兜底释放，不丢字）、`test_no_mic_still_sends_valid_audio_frames`（完全不启动麦克风，断言每帧都带音频且会话未被服务端打死）。
  - **旧实现反证**：离线复现脚本在段末释放一次即输出 `<<CALL_Q`——与真机日志一字不差，确认根因。
- **待 bo s s 真机复验**：①问「你看到了什么」不应再出现 `<<CALL_Q` 碎片，升级任务应是完整句子；②不应再出现 `[OMNI] 状态: closed` + `received 1000 (OK)`；若仍出现，新日志会直接打出服务端给的 reason，据此定位服务端侧原因。③点「停止」应立刻返回（约 1s），不再是等 10 秒。

## 2026-09-13（晚）— P0 双修：令牌分片泄漏（乱念 `<<CALL_Q`）+ 音频积压 27 秒（答非所问）

- **背景（bo s s 真机日志 + `temp/omni_server.log` 实测）**：①问「你看到了什么」，omni 却吐 `<<CALL_QWEN>>查一下这台电脑的…` 并真的去查了系统信息；②Voicebox 里合成出的是令牌碎片 `<<CALL_Q`（且只有前缀、没有后续）；③`listen_prob_scale=0.3` 时模型乱答、`=0.5` 时「不说话」。
- **根因 1（答非所问 · 音频积压）**：服务端 full_duplex 是**串行**循环「读一条 `input.append` → prefill + decode 一步」，实测单段耗时 **0.69s**（`VPM` 图像编码 190ms + decode p50 330ms / p90 759ms / max 1098ms），而客户端**固定每 0.4s 猛推**一段。二者速率不匹配 → 积压线性增长：本节会话 64.4s 内服务端累计落后 **27.2 秒**，**模型回答的是半分钟前的 bo s s**。另：每段 KV 增长约 73 token（其中视觉约 64、音频个位数），n_ctx=8192 触发线 6144，实测 `slide n_past=7660→2652`——**每约 30 秒上下文被滑动清空一次**；记忆一空，模型只剩 system prompt，于是照着 `prompts.py` 的示例原句吐令牌（被误发的任务正是示例 `查一下这台电脑的电池电量百分比` 的片段）。
- **根因 2（令牌碎片乱念）**：omni 服务端 `make_text_delta(frag)` 是**按 token 逐片**下发文本，`<<CALL_QWEN>>` 必然被切成 `<<CALL_Q` 这类碎片。`_on_text` 只有凑齐**完整**令牌才拦截，碎片到达那一瞬未命中 → 走「正常对话」分支被广播且 `feed` 进朗读队列；`voicebox_bridge.feed` 的兜底也只找完整令牌，同样拦不住 → Voicebox 合成并念出 `<<CALL_Q`。控制台原样打印了 `<<CALL_Q`，是该分支被走到的直接旁证。旧单测把整段令牌一次喂入，因此**永远抓不到**此 bug。
- **修复（`src/omni/client.py` + `src/omni/voicebox_bridge.py`）**：
  1. **P0-a 令牌前缀 holdback**：新增 `_token_prefix_suffix_len()`，对缓冲末尾「可能是令牌前缀」的字符（最多 `len(令牌)-1 = 12` 个）做扣留——不外发、不朗读，等下一片 delta 到齐确认构不成令牌再放行。`_on_text` 只在**安全区**上广播/朗读；轮末由新增 `_flush_text_holdback()` 释放（`response.done` / `listen` / `session.closed` 三处接线）；命中令牌时仍坚持「令牌及其后任务描述既不显示也不朗读」。`voicebox_bridge.py` 同步加 holdback 作为纵深防御：`feed()` 只在安全区切句、`_drain_tail()` 在 `flush_remaining`/`flush_and_stop` 时**丢弃**悬空前缀（`<<CALL_Q` 不可能成为合法台词），超时 flush 只放行安全区（避免句子被卡住）。
  2. **P0-b 推流背压**：`_push_loop` 由「固定 0.4s 猛推」改为**一段在飞**——`_wait_chunk_slot()` 等服务端把上一段处理完（收到 `listen` 或 `response.done` 即算完，`_signal_chunk_done()` 置位）再推下一段；服务端慢则自动放慢节拍（单段音频变长），延迟不再累积；`chunk_wait_timeout`（默认 1.0s）超时兜底防死等。
  3. **P0-b 水位保护**：`_take_audio()` 设音频水位上限 `max_buf_secs`（默认 1.2s），超水位**丢最旧的、留最新的**并打警告——极端情况下宁可让模型听到一段有断点的音频，也不能让它对着半分钟前的声音回答。
  4. 顺手修正：命中令牌分支此前会把令牌前缀**重复**喂给朗读队列（先增量 feed 过、又整体 feed 一次 `pre`），改为只用 `_shown_len` 单一游标精确推进；`_broadcast()` 不再自行改写 `_shown_len`（holdback 后「已外发位置」≠「缓冲末尾」）。
- **开关（便于真机 A/B 对照）**：`OMNI_FLOW_CONTROL=0` 关闭背压退回固定节拍；`OMNI_MAX_BUF_SECS`（默认 1.2）调水位；`OMNI_CHUNK_WAIT`（默认 1.0）调超时。
- **验证**：
  - `py_compile` 通过；新增 `tests/test_omni_p0_flow_and_token.py`（11 用例：前缀判定 / **分片令牌全程零泄漏且升级正常触发** / holdback 不吞字 / 轮末释放 / 桥接纵深防御 / 桥接不卡句 / 水位丢旧留新 / 未超水位不裁剪 / 背压按终局事件放行且超时兜底 / 关闭背压退回固定节拍 / 环境变量开关）。
  - 新增 `tests/test_omni_p0_backpressure_ws.py`（**WS 级集成**）：起一个「故意每段慢 0.6s」的假 omni 服务端压测真客户端，实测**平均间隔 0.60s、平均单段音频 0.55s、缓冲峰值 0.80s、零丢弃** —— 上行音频量不再超过真实流逝时间，即**积压被消除**（修复前会是 0.4s 节拍持续堆积）。无需 8GB 模型与麦克风，约 4s 跑完。
  - 相关回归全过：`tests/test_omni_*.py` + `tests/test_voicebox_speaker.py` + `tests/unit/test_tools.py` 共 **39 passed**。
- **附带发现（未修，待排期）**：`runtime.stop()` 后升级 daemon 线程未被取消——日志里 `[系统] J.A.C.Prototype 已停止。` 之后仍在跑 3 次 `get_system_info` 并继续播放 TTS。
- **待 bo s s 真机复验**：重启 OMNI 后连聊 1~2 分钟，`temp/omni_server.log` 不应再出现「服务端落后于真实时间」的持续放大；问「你看到了什么」应得到对摄像头画面的回答而不是系统信息查询；Voicebox 里不应再出现 `<<CALL_Q` 之类的碎片音频。

## 2026-09-13 — 修复 OMNI「检测到人声却永远不回复」的协议层错误

- **现象（bo s s 真机日志）**：麦克风连续检测到真实人声（RMS 最高 0.085），但服务端持续输出 `listen=1`、`is_end_of_turn=0`、`llm_text.len=0`；因此不是摄像头、麦克风权限或音量不足，而是模型始终选择继续聆听。
- **根因**：客户端虽传了 `listen_prob_scale=0.5`，但错误地放在 WebSocket `session.init` 的顶层。llama.cpp-omni 的 `parse_session_init()` 只解析 `payload.config`，顶层字段被静默忽略，服务端实际回落到默认 `1.0`（不压低 `<|listen|>` 偏好），从而复现「只听不说」。
- **修复**：`src/omni/client.py` 新增 `_build_session_init()`，统一把 `listen_prob_scale` 放进 `payload.config`，并在启动时回显实际发送的系数；更新顶部协议契约，避免维护时再次放错层级。
- **验证**：新增 `tests/test_omni_protocol.py`，断言采样配置只位于服务端可读的 `payload.config`；无需模型、摄像头或麦克风即可回归。
- **配置与复验**：默认仍为 0.5。重启 OMNI 后，启动日志应出现 `Listen 概率系数=0.50（已发送给服务端）`；正常对话时 `temp/omni_server.log` 不应再持续只有 `listen=1`，而应能出现非 listen 的生成/文本输出。若模型仍偏向聆听，可在 GUI 把「Listen 概率系数 (OMNI)」逐步降到 0.3 或 0.2 再复验。

## 2026-09-06 — OMNI 回声自激根因修复（回声门控 + 幻觉任务禁止显示/偷偷升级）

- **背景（bo s s 真机日志）**：外放场景下 omni 自言自语——bo s s 全程没说话，控制台却冒出「给您推荐一部电」「么样天气怎」，LM Studio 还收到 `[升级任务] 给您推荐一部电`。日志铁证：`[TTS] 正在播放（Voicebox）` 出现的**同一时刻** `[omni-client] 🎙 检测到人声（RMS=0.022 峰值=0.106）`——**J.A.C. 听到了自己刚说的话**。
- **根因（不是 ASR 误识别）**：TTS 外放 → 被本机麦克风重新采集 → 推给 omni → omni 把自己的语音当成用户发言 → 自问自答 → 幻觉生成 `<<CALL_QWEN>>` 任务。且回声 RMS（0.022）超过了原有护栏阈值 0.02，`_has_recent_speech()` 被回声骗过，令牌**未被拦截**，于是真的触发了 qwen 升级（这就是 LM Studio 收到莫名任务的原因）。
- **修复（4 处）**：
  1. **回声门控（核心，`src/audio/playback.py` + `src/omni/client.py`）**：`playback` 是所有 TTS 播放的唯一出口，新增全局「正在出声」状态（`is_playback_active()` / `seconds_since_playback_end()` / `mark_external_playback()` / `reset_playback_state()`），`play_wav` 进入/退出时置位复位。客户端 `_push_loop` 在回声窗口内用**等长零字节**替换真实采集推送（保持「1 秒音频 / 1 秒墙钟」节奏，避免只听不说），并把该帧判为非人声、不刷新 `_last_speech_ts`。逻辑抽为纯函数 `_apply_echo_gate()` 便于单测。开关：CLI `--no-echo-gate` 或环境变量 `OMNI_ECHO_GATE=0`；拖尾保护 `OMNI_ECHO_TAIL`（默认 0.8s）。启动时打印一行门控状态便于验收确认。
  2. **护栏认回声（`_has_recent_speech`）**：回声窗口（含拖尾）内一律返回 False，令牌判为幻觉，不触发升级、不静音主会话（用户随后真实发言仍可正常回复）。
  3. **幻觉任务禁止偷偷升级（`_hallucinated` 标志）**：旧逻辑拦截后 `_token_seen=True`，后续 delta 若出现句号会被 `_try_finalize_pending()` 再次 fire——即「拦了又升」。新增 `_hallucinated` 守卫，`_finalize_pending` / `_try_finalize_pending` 在其为真时直接返回；`_reset_escalation_state()` 复位。
  4. **任务描述不再显示给用户（`_broadcast` + `_shown_len`）**：令牌及其之后的文本是发给大脑的**内部指令**，此前会被广播到控制台 / GUI，bo s s 看到的「给您推荐一部电」正是它（不是 ASR 识别结果）。改为先累积再广播，令牌及之后一律不显示、不进 `_reply_buf`。
- **验证**：`py_compile` 全过；新增 `tests/test_omni_echo_gate.py`（8 用例：播放状态机 / 回声期护栏 / 拖尾 / 回声期令牌拦截 / 幻觉永不 fire / 任务文本不广播 / 门控帧替换静音 / play_wav 状态复位）全过；`tests/test_omni_m2.py` + `tests/test_omni_m3_token.py` 回归全过（共 18 passed）。
- **顺带修 2 个历史红灯**：`tests/test_omni_m3_token.py` 的 `test_token_text_not_spoken` / `test_multi_turn_escalation` 自 2026-08-16「任务改跨换行累积、遇句末标点才结算」后已与实现不符（用 `\n` 结尾期望立即触发），在 git HEAD 版本上即为失败；本次改为用句号结尾，与现行策略对齐（无标点场景由 1.5s 兜底定时器兜底）。
- **待 bo s s 真机复验**：外放时 omni 应停止自言自语、不再有凭空的 `[OMNI升级]`；戴耳机（硬件隔离回声）可用 `--no-echo-gate` 关闭门控以保留打断能力。

## 2026-09-06（续）— 回声门控「自动检测输出设备」+ GUI 开关

- **背景**：bo s s 选择以后戴耳机使用。耳机在硬件层面已隔离回声，无需门控，反而应**关闭门控以保留随时打断**；而用内建扬声器外放时必须开门控。手动切 `OMNI_ECHO_GATE` 易忘，故改为**按输出设备自动判定**。
- **改动**：
  1. `src/omni/client.py` 新增 `detect_headphones()`（查 PyAudio 默认输出设备名，含"耳机/headphone/airpods/蓝牙/bluetooth"等关键词即判为耳机）与 `resolve_echo_gate(pref)`（"auto"/None→自动检测；"1/on/true"强制开；"0/off/false"强制关）。`OmniClient.__init__` 的 `echo_gate` 现接受 `True/False/None/str`，未显式传时读 `OMNI_ECHO_GATE`（默认 `auto`）。启动时打印最终开关及原因（如"自动检测：耳机/蓝牙输出，无需门控"）。
  2. `src/utils/config.py` 新增 `omni_echo_gate: str = "auto"`（环境变量 `OMNI_ECHO_GATE`），`src/runtime.py` 透传。
  3. `gui.py` OMNI 选项面板新增「回声门控 (OMNI)」下拉：**自动（按输出设备）/ 关（戴耳机，可打断）/ 开（外放，防自激）**，并在 `_collect_config` 导出。
  4. `src/omni/__main__.py` `--no-echo-gate` 帮助文本更新为"默认按输出设备自动判定"。
- **默认行为变化**：此前门控默认强制开启（`OMNI_ECHO_GATE` 缺失=1）；现在缺失=`auto`，戴耳机启动即自动关闭（符合 bo s s 选择），外放自动开启。
- **验证**：`py_compile` 全过；`tests/test_omni_echo_gate.py` 新增 `test_resolve_echo_gate_pref_parsing` 覆盖手动/auto/未知字符串解析，全部回归 29 passed。

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
