# WebRTC AEC（声学回声消除）接入方案

> 状态：**方案待评审，未实施**（2026-09-24 编写）
> 决策人：bo s s ｜ 编写：J.A.C. Agent
> 关联：`src/omni/client.py` 的 `_apply_echo_gate()` / `_is_echoing()`、`src/audio/playback.py`、`AGENTS.md` 的「回声门控」条目

---

## 1. 要解决什么问题

当前外放场景下的自激是这样发生的：J.A.C. 用 TTS 说话 → 声音被本机麦克风重新采集 → 推给 omni → **omni 把「自己的声音」当成用户发言** → 自问自答，甚至幻觉出 `<<CALL_QWEN>>` 任务并真的触发升级（真机已复现：`[TTS] 正在播放` 与 `🎙 检测到人声 RMS=0.022` 同帧出现）。

现在的工程做法（`_apply_echo_gate`）是**在播放窗口内把麦克风整帧替换成等长静音**——便宜、可靠，但代价很重：

| 现状 | 后果 |
|---|---|
| 播放期间麦克风被"按静音推送" | **完全放弃 barge-in（插话打断）** |
| 一刀切，不区分"回声"与"用户真的在说话" | 门控窗口内用户说什么都听不见 |
| 靠 `_is_echoing()` 全局状态推断 | 戴耳机时（硬件已隔离回声）该逻辑仍在误杀真实提问 |
| 拖尾 `OMNI_ECHO_TAIL=0.8s` 硬编码 | 房间混响/蓝牙缓冲不同，需手工调参 |

**AEC 的目标是把「消除回声」与「听见用户」解耦**：只减掉自己播出去的那部分信号，用户的声音原样保留 → 恢复打断能力，并顺带干掉那条误杀真实提问的护栏。

---

## 2. 关键前提：AEC 需要什么，我们现在缺什么

AEC 不是"对麦克风信号做一次滤波"，它是**自适应滤波器**，必须同时拿到两路信号：

```
near-end（近端） = 麦克风采到的：用户声音 + 我的回声 + 噪声
far-end （远端） = 我刚刚送到扬声器播放的 PCM（参考信号）
                    ↓
        clean = AEC(near, far)   →  只留用户声音
```

**核心障碍：我们拿不到 far-end。**

`src/audio/playback.py:111` 确认，所有发声都交给外部进程：

```python
r = subprocess.run(["afplay", path], capture_output=True, text=True)
```

音频数据在子进程里，Python 侧只剩一个文件路径——**既没有 PCM 样本，也没有精确的播放起止时刻**。AEC 对帧级甚至样本级的时间对齐极其敏感（典型容差 ±几毫秒，工程上靠 `stream_delay_ms` 标定），所以：

> **本方案的第一件事不是选 AEC 库，而是让播放路径能吐出它播出去的 PCM。**

这条决定了后面所有路线的工作量分布。

---

## 3. 三条候选路线

### 路线 A：`pywebrtc-audio` + 自建播放（**推荐**）

用 Python 直接调用 Chrome 同款 WebRTC APM 做 AEC，同时把播放从 `afplay` 搬到 PyAudio，让"播出的 PCM"顺手成为参考信号。

**依赖可得性（已实测，非推测）**：
```
$ .venv/bin/pip download --no-deps --dest /tmp/pypi_probe pywebrtc-audio
Downloading pywebrtc_audio-0.2.0-cp313-cp313-macosx_11_0_arm64.whl (367 kB)
Successfully downloaded pywebrtc-audio
```
—— 与开发机（Apple Silicon / Python 3.13.14）**完全匹配的预编译 wheel，无需编译、无需 swig/meson**（对国内网络友好，可走清华镜像）。

**API 形态**（贴合本项目场景）：
```python
ap = AudioProcessor(sample_rate=16000, echo_cancellation=True,
                    noise_suppression=True, auto_gain_control=True,
                    stream_delay_ms=40)
clean = ap.process(near, far)      # 接受 int16 或 float32 numpy 数组
ap.speech_probability              # 0~1，可直接替代现在的 RMS 阈值判据
```

| 维度 | 评估 |
|---|---|
| AEC 质量 | 与 Chrome / Edge 同款算法，工程上最成熟的一档 |
| 性能 | 官方自测 M3 Pro 上 AEC 622µs / 100ms 音频（**161× 实时**）；按我们 1s 音频算约 6ms，可忽略 |
| 附加收益 | 白送 **VAD（`speech_probability`）**、NS 降噪、AGC——正好替掉现在那个"峰值 0.06 太灵敏"的土制人声判据 |
| 跨平台 | Linux/macOS/Windows wheel 齐全，将来若回到 Windows 开发机也不用重做 |
| 主要风险 | **要换播放实现**：历史上正是因为 `sounddevice` 选错设备导致"全程静音"才改用 `afplay`（见 `playback.py` 顶部注释），必须保留设备选择与 `afplay` 兜底 |

### 路线 B：macOS 原生 `AVAudioEngine` VoiceProcessingIO（质量最好，改造最大）

Apple 在 WWDC2019/510 正式引入语音处理模式，**定位就是 VoIP 回音消除**：

> "AVAudioEngine now has a voice processing mode. ... any audio that is coming from the device is taken out. This requires that both input and output nodes are in the voice processing mode."
> "Voice processing cannot be enabled dynamically, which means the engine needs to be in a stop state when enabling the mode."

因为采集与播放走同一个 engine，**参考信号由系统内部自动对齐**——不需要我们标定延迟，这是它的最大优势。

| 维度 | 评估 |
|---|---|
| AEC 质量 | 系统级、Apple 调优，通常优于通用 APM，且延迟最低 |
| 参考信号 | **无需自备**（engine 内部处理），绕开了第 2 节的核心障碍 |
| 前置改造 | **大**：采集与播放要**全部**从 PyAudio 搬到 AVAudioEngine（PyObjC），不能再让 `afplay` 发声 |
| 已知坑（社区经验，**未在本机实测**，需验证） | ① macOS 上 voice processing 音频单元**只接受 44100Hz**（>而 omni 要 16k，必须自己写 `AVAudioConverter`，官方 TN3136 明确指出不能用便捷方法，否则会爆音）；② 必须先在停止态初始化，且要"先起 playback 引擎、再建 VoiceProcessingIO"否则音量异常小；③ 回调运行在实时线程**不能有阻塞调用**，Python 回调带 GIL 有掉帧风险 |
| 跨平台 | 仅 macOS（本项目主平台，可接受） |

### 路线 C：保留 `afplay`，预解码 WAV 当参考（**不推荐，仅作快速对照**）

播放前用 `wave` + `numpy` 把同一份 WAV 解码成 PCM 作为 far-end，按"预计开始播放时刻"对齐。

- **优点**：几乎不动现有播放链路，改动最小。
- **致命弱点**：`afplay` 的**进程启动延迟 + 系统音频缓冲**无法精确预测，参考信号与真实回声之间会有几十毫秒的**未知、且可能漂移**的偏移。AEC 对未对齐的参考信号不仅无效，还可能**放大失真**。
- **定位**：只适合花半小时做个对照实验，确认"对齐问题到底有多严重"，不适合作为正式方案。

---

## 4. 推荐路线与分阶段实施

**建议走路线 A**：收益/风险比最好，依赖已验证可得，且顺带解决 VAD 判据问题；路线 B 留作"A 的效果不达标"时的升级选项。

### 阶段 0 · 可行性验证（建议先做，约半天）

**目的：在改任何生产代码之前，先确认 AEC 在本机这套设备组合上真的有效。**

1. 装依赖（走镜像）：`pip install pywebrtc-audio -i https://pypi.tuna.tsinghua.edu.cn/simple`
2. 写一个**独立最小脚本**（不进主干）：同时开 PyAudio 输入流与输出流，输出端循环播放一段固定语音，输入端实时跑 `ap.process(near, far)`，把 `near` 与 `clean` 分别落盘成两个 wav。
3. **人工听 + 量化**：`clean` 里应几乎听不到播放的语音、但能清楚听到对着麦说的话；用两段音频的回声段能量差算出抑制比。

**通过判据**：回声段抑制 **≥ 15~20 dB**，且用户语音的清晰度主观无明显损失。

> 之所以先做这一步：AEC 效果**强依赖具体设备组合与延迟标定**（AirPods 输出 + 内建麦输入的延迟特性跟扬声器完全不同），官方 demo 跑得好不代表本机跑得好。这一步失败，后面全白做。

### 阶段 1 · 播放链路改造（让 far-end 可得）

- 在 `src/audio/playback.py` 增加一条**可观测播放**实现：用 PyAudio 输出流播放，并在播放时把 PCM 副本投递到 AEC 的 far-end 队列。
- **保留 `afplay` 作为兜底**（新增开关，例如 `JAC_PLAYBACK_IMPL=pyudio|afplay`）：设备打开失败 / 无声检测失败时自动回退，避免重现历史"全程静音"问题。
- 同步维护现有的全局播放状态（`is_playback_active()`），`_is_echoing()` 仍作为**兜底**保留。

### 阶段 2 · 采集侧接入 AEC（本项目改动最小的一步）

`src/omni/client.py` 的 `_push_loop` 里，在计算 RMS **之前**插入：

```
audio(near) + far-end  →  ap.process()  →  clean  →  照旧算 RMS / 推给 omni
```

替换掉 `_apply_echo_gate()` 的"整帧置零"，改为**只对 far-end 成分做减法**。门控逻辑降级为"AEC 不可用时的兜底路径"（保留 `OMNI_ECHO_GATE=1` 强制开启）。

**这一步带来的连锁收益**：
- **恢复 barge-in**：播放期间用户插话能被听见了；
- 顺带修掉「戴耳机时真实提问被护栏误杀」——因为 `_has_recent_speech()` 里那条"播报期一律判幻觉"的前提（"播报期采集到的必是自己的声音"）在 AEC 之后不再成立，可放宽为"仅在 AEC 不可用时才启用"；
- `speech_probability` 可逐步替代 `OMNI_SPEECH_PEAK_TH`（当前 0.06 太低，底噪 0.061 就会越线）。

### 阶段 3 · 标定与观测

- **标定 `stream_delay_ms`**：用互相关测出"播出 → 采回"的真实声学延迟，写进配置（不同输出设备延迟不同，建议做成可配 + 自动初测）。
- **观测指标**：AEC 前后回声残余能量、`speech_probability` 分布、误杀率（用户说话被判无人声的比例）、barge-in 成功率。
- 保留一键回退：`OMNI_AEC=0` 回到现在的静音门控。

---

## 5. 待 bo s s 拍板的事项

1. **是否走路线 A**（pywebrtc-audio + 自建播放）？还是直接上路线 B（macOS 原生，效果最好但要把音视频采集全搬到 AVFoundation）？
2. **是否先做阶段 0 的独立验证脚本**？（我倾向必须做：半天时间就能证伪，避免在错误路线上投入）
3. 阶段 1 要替换播放实现，**是否接受新增一个第三方依赖**（`pywebrtc-audio`，Apache-2.0，367KB wheel）？如果要维持"零重型依赖"，那只能走路线 B（PyObjC 属系统自带框架，但改造量最大）。

---

## 6. 风险与回退总表

| 风险 | 概率 | 影响 | 回退/缓解 |
|---|---|---|---|
| AEC 在本机设备组合上效果不达标 | 中 | 高 | 阶段 0 先行验证；不达标则维持现有静音门控，方案作废、零代码损失 |
| 切换到 PyAudio 播放后出现设备选择错误/静音 | 中 | 高 | 保留 `afplay` 兜底 + 启动自检（播一小段并在播放后校验输出电平） |
| `pywebrtc-audio` 维护停滞（当前 0.2.0） | 低 | 中 | 备选：`aec-audio-processing`（需 swig/meson 编译，同源 WebRTC APM）、`macloop`（Rust/macOS 专用，Alpha）、官方 wheel 仓库（Python ≤3.12） |
| 参考信号对齐不准导致 AEC 反而恶化 | 中 | 中 | 阶段 1 里精确记录播放起止样本数；阶段 3 用互相关标定 |
| 走路线 B 时 Python 实时回调掉帧（GIL） | 中 | 中 | 回调内只做环形缓冲写入；重活挪到独立线程；必要时用 `PyObjC` + 小段 C 扩展 |

---

## 附：本方案中「已核实」与「待实测」的界限

| 结论 | 状态 |
|---|---|
| 当前播放走 `afplay` 子进程、拿不到参考 PCM | ✅ 已读源码确认（`playback.py:111`） |
| `pywebrtc-audio 0.2.0` 有 cp313 / macOS arm64 预编译 wheel | ✅ 已在本机 `pip download` 实测成功 |
| AVAudioEngine 语音处理模式 = 系统级 AEC；需 input+output 双端启用；不能在运行中动态启用 | ✅ Apple 官方文档（WWDC2019/510） |
| macOS voice processing 只接受 44100Hz、必须自写 `AVAudioConverter` | ⚠️ 社区经验（OpenAI 开发者论坛），**未在本机实测** |
| AEC 的 CPU 开销可忽略（161× 实时） | ⚠️ 库作者官方自测数据，非本机实测 |
| 本机「AirPods 输出 + 内建麦输入」组合下的实际抑制比 | ❌ **未知，必须由阶段 0 实测** |
