# J.A.C. 主动服务层设计（Proactive Layer）

- **日期**：2026-09-12
- **状态**：设计已获 bo s s 批准
- **适用范围**：仅 OMNI 全双工模式
- **下一步**：交由 `writing-plans` skill 拆解为可执行任务；本文档面向实施 agent，可直接作为编码规格

---

## 0. 给实施 agent 的前置说明

### 0.1 必读文件（按此顺序）

1. `AGENTS.md` — 项目契约，含文档同步硬性规定
2. `codingLOG.md` — 与最终目标的差距笔记
3. `CHANGELOG.md` — 最新改动在最上方；**其中 2026-08-15 / 2026-08-16 / 2026-09-06 三批修复记录是本设计约束的来源**，实施前必须读完
4. 本文档 §12「实施约束（必须遵守的已验证坑）」

### 0.2 核心设计原则

> **场景是运行时数据，不是代码结构。**

bo s s 举的任何具体场景（如停车场）**只是说明需求的例子，不是设计目标**。因此：

- 禁止在代码中出现场景专有字段名、场景枚举、场景硬编码常量
- 策略（policy）由 agent 在运行时从对话中创作，**开发者不预写场景规则库**
- 场景名是 omni 输出的**开放词表自由文本**，靠语义相似度匹配，不做枚举
- 验收时须用**两个互不相关的场景**证明引擎通用性（见 §11 P3 验收标准）

违反此原则的实现（例如 `if scene == "parking_garage":`）应被视为设计错误并返工。

### 0.3 需求来源（bo s s 原话要点）

1. 助手要能**主动提供帮助与提醒**，不必等用户显式触发
2. 必须能跑在 MacBook Pro M5 Pro 48GB 本地
3. 运行速度不能太慢，需要及时反馈
4. 由 **MiniCPM-o-4_5 实时识别**"我在哪、在做什么、J.A.C. 能提供什么帮助、我需要什么帮助"；omni 自己解决不了的**升级 qwen3.6-35b-a3b** 处理；全程可自行调用任何组件，**让 J.A.C. 是一个 agent**
5. 不在乎耗电（保持供电）；权限可以给任何权限；不在乎隐私性
6. web search **本期不实现**（bo s s 说"以后再加"），只留接口位

---

## 1. 已核实的硬约束

以下三条由代码/日志/物理事实核实，**不可绕过**：

| # | 约束 | 证据 | 架构后果 |
|---|---|---|---|
| C1 | llama.cpp-omni server **单会话** | `CHANGELOG.md` 2026-08-15 M7a：第二个 turn_based 会话被拒，server 日志 `session.init rejected — active session exists`，客户端收到 `ConnectionClosedOK` | **不能另开 omni 会话做观察**。观察必须走**带内协议**，复用主会话输出流 |
| C2 | 合盖后摄像头对键盘、麦克风被遮挡 | 物理事实 | **视觉/音频感知只在开盖有效**。离场期间的提醒必须靠定位 + 时间戳算术，**不能靠"看见场景"触发** |
| C3 | OMNI 分支启动后直接 return | `src/runtime.py:137-141`；`_start_omni()` 内不建 camera/YOLO/SharedContext/judge | 仅 OMNI 模式下**不加载 MiniCPM-v**，省下第二个 9B（约 9-10GB） |

### 1.1 内存预算（仅 OMNI）

| 组件 | 占用 |
|---|---|
| MiniCPM-o-4_5 GGUF Q8_0（llama.cpp-omni，Metal） | ~9-10GB |
| qwen3.6-35b-a3b（LM Studio，升级时才激活） | ~20GB |
| **合计** | **~30GB / 48GB，余量充足** |

### 1.2 可复用的现有接口（已核实）

| 接口 | 位置 | 复用方式 |
|---|---|---|
| `EscalationRouter.escalate(task_text, on_progress=None) -> str` | `src/omni/router.py:60` | **advisor 升级路径直接复用**，已含 agentic 工具循环 + 流式回传 |
| `OmniClient.get_latest_frame()` | `src/omni/client.py:365` | 需要独立取帧时使用（本期不依赖） |
| `OmniClient.get_reply_text()` / `get_latest_mic_level()` / `append_reply()` | `src/omni/client.py:968-977` | GUI 面板与主动层状态展示 |
| `MemoryEmbedder.embed_texts(texts, mode="passage") -> Optional[list]` | `src/memory/embedder.py:154` | **策略语义匹配**：观察文本用 `mode="query"`，策略意图用 `mode="passage"`（非对称检索） |
| `MemoryEmbedder.dim -> Optional[int]` | `src/memory/embedder.py:176` | 向量维度校验 |
| `MemoryStore` 原子写 / `.bak` 恢复 / TTL compaction / 向量检索 | `src/memory/store.py` | **世界知识存储直接复用，零改动** |
| `playback.is_playback_active()` / `seconds_since_playback_end()` / `mark_external_playback()` | `src/audio/playback.py` | 回声门控协同（见 C4） |
| `VoiceboxSpeaker.speak(text, emotion_hint=None)`（含 RLock 串行锁 + uuid 文件名） | `src/audio/voicebox_tts.py` | 语音通知通道 |
| `execute_tool(name, arguments)` / `get_tool_schemas()` | `src/tools/executor.py:8`、`src/tools/registry.py:79` | 新增工具挂同一注册表 |

### 1.3 C4：回声自激（2026-09-06 真机事故）

**主动层的任何播报都必须走 `playback.mark_external_playback()`**，否则 TTS 声音会被麦克风重新采集，omni 把自己的通知声当成用户发言 → 自问自答 → 幻觉出升级任务。

真机日志铁证（`CHANGELOG.md` 2026-09-06）：

```
[mic] ····· RMS=0.0031 [TTS] 正在播放（Voicebox / JAC声纹引擎）: ...wav
[mic] ███· RMS=0.0220 [omni-client] 🎙 检测到人声（RMS=0.022 峰值=0.106）
```

现有解法：`_push_loop` 在回声窗口内用等长零字节替换真实采集；`_has_recent_speech()` 在回声期一律判幻觉。**主动层必须复用这套机制，不得绕过。**

---

## 2. 双工况模型

因 C2，主动层必须在两种工况下工作：

```
Regime A（开盖）
  omni 持续感知 → 带内观察协议 → ObservationState → 场景/活动/需求识别
  omni 自己解决 or <<CALL_QWEN>> 升级 qwen3.6-35b
  全部能力可用

Regime B（合盖）
  omni 感知失效（摄像头对键盘、麦克风被遮挡）
  仅 CoreLocation + Scheduler + Notify 可用
  已注册的定时任务照常触发；不产生新的观察驱动策略
```

工况切换由 `RegimeTracker` 维护，`lid.closed` / `lid.opened` 事件驱动；无 IOKit 时降级为「定位静止 + 观察中断」联合推断。

### 2.1 跨工况场景示例（bo s s 提出的停车场景，仅作夹具）

```
开盖接近 → omni 观察(scene_text="地下车库…", poi_hint=…)
        + LocationSource 确认进入 POI 半径
        → FusionEngine 滞回确认 Scene(entered_at=T0, regime="A")
        → PolicyMatcher 命中已教策略
        → KnowledgeService 查计费规则（memory-first）
        → fire_at = T0 + free_minutes*60 - lead_seconds
        → ScheduledTask(misfire_policy="reschedule")
用户合盖走开 → lid.closed → Regime B（omni 停，scheduler 继续）
到点触发 → 查当前是否仍在 POI：在 → 通知；不在 → skip
        → Voicebox(强制默认输出，覆盖蓝牙) + macOS 通知中心
```

`T0 = min(观察进入时刻, 定位进入时刻)`；支持语音修正（"我停了半小时了" → 重算 `entered_at`）。

---

## 3. 架构总览

```
┌─ 感知源适配层（全部通用，无场景特化）──────────────────────┐
│ OmniObservationSource  带内观察协议解析（核心，挂 OmniCallbacks）│
│ LocationSource         CoreLocation + haversine + 地理围栏      │
│ LidStateSource         开合盖事件                               │
│ SystemSource           时间 / 电池                              │
└──────────────────────┬────────────────────────────────────────┘
                       ▼
              EventBus（发布订阅，线程安全队列）
                       ▼
              ObservationState（FusionEngine）
              多源融合 + 滞回防抖 + 开放词表 Scene 维护
                       ▼
              PolicyMatcher
              ├─ 结构谓词：kind / 地理围栏 / dwell / 时间窗 / regime
              │            （确定性，零模型成本）
              └─ 语义匹配：观察文本 vs policy.trigger_embedding
                           （复用 MemoryEmbedder，阈值可调）
                       ▼
         ┌─────────────┴─────────────┐
    命中 ≥1 条                  命中 0 条 且 user_need≠none
         │                             │
         │                             ▼
         │                    advisor（长尾旁路）
         │                    复用 EscalationRouter.escalate
         │                    + 全部幻觉护栏
         ▼                             │
   GuardGate                           │
   冷却 / 免打扰 / 去重                 │
         ▼                             │
   ActionDispatcher ◄──────────────────┘
   action 四型：
     notify     → NotifyRouter（分级多通道）
     set_timer  → Scheduler（时间轮 + 落盘 + 睡眠对账）
     escalate   → EscalationRouter（qwen3.6-35b + 工具）
     tool_call  → execute_tool（现有白名单）
                       ▼
              KnowledgeService（memory-first）
              MemoryProvider / TeachingProvider / [WebSearchProvider 预留]
```

**与现有 judge 的关系**：主动层与 `src/judgment/judge.py` **不是替代关系**，但在 OMNI 模式下 judge 本就不启动（C3）。judge 管"此刻要不要插话"，主动层管"未来某时刻要做什么 + 此刻观察到了什么"。本设计**不修改 judge.py**。

**与现有 `<<CALL_QWEN>>` 升级链路的关系**：主动层**订阅同一条文本流**，新增 `<<JAC_OBS>>` 带内协议与之并存（§6）。advisor 的 escalate 路径复用 `EscalationRouter`，与现有升级共用大脑实例。

---

## 4. 文件结构

```
src/proactive/
├── __init__.py            导出 ProactiveService
├── service.py             ProactiveService 编排器（daemon 线程生命周期）
├── models.py              全部 dataclass（§7）
├── events.py              EventBus 发布订阅
├── fusion.py              FusionEngine / ObservationState 融合 + 滞回
├── regime.py              RegimeTracker 工况状态机
├── sources/
│   ├── __init__.py
│   ├── base.py            SourceAdapter 抽象基类
│   ├── omni_obs.py        OmniObservationSource（带内观察解析）
│   ├── location.py        LocationSource（CoreLocation）
│   ├── lid.py             LidStateSource（开合盖）
│   └── system.py          SystemSource（时间/电池）
├── policy/
│   ├── __init__.py
│   ├── store.py           PolicyStore（复刻 MemoryStore 的已验证模式）
│   ├── matcher.py         PolicyMatcher（结构谓词 + 语义匹配）
│   ├── dsl.py             trigger/condition/timing 表达式求值器
│   ├── authoring.py       PolicyAuthoring（对话教学 → 策略，含 agent 提议）
│   └── examples/          仅作测试夹具，非运行时依赖（见 §0.2）
├── knowledge.py           KnowledgeService + Provider 接口族
├── scheduler.py           TimeWheel + 落盘 + 睡眠对账
├── advisor.py             Advisor（长尾旁路 + 护栏）
├── dispatcher.py          ActionDispatcher
└── notify/
    ├── __init__.py
    ├── router.py          NotifyRouter 分级路由
    ├── base.py            NotifyChannel 抽象
    ├── voicebox.py        VoiceboxChannel
    └── macos.py           MacOSChannel（osascript）
```

### 4.1 接线改动（最小侵入）

| 文件 | 改动 | 说明 |
|---|---|---|
| `src/runtime.py` | `_start_omni()` 末尾创建并启动 `ProactiveService`，注入 `self.omni_client`、`self.speaker`（VoiceboxSpeaker 单例） | 只在 OMNI 分支；传统模式不启动 |
| `src/omni/client.py` | `_on_text` 增加 `<<JAC_OBS>>` 拦截分支，与 `<<CALL_QWEN>>` 同层同优先级 | 见 §6 解析规则 |
| `src/omni/prompts.py` | `SYSTEM_PROMPT` 增加观察输出约定 | 见 §6.1 |
| `src/utils/config.py` | 新增 `proactive_*` 配置组 | 见 §10 |
| `src/tools/registry.py` | 新增 3 个工具 | 见 §9 |
| `requirements.txt` | 新增 pyobjc 两项 | 见 §10.1 |
| `new_computer_download/READMEfirst.md` + 一键安装脚本 | 同步新依赖与定位授权说明 | AGENTS.md 硬性规定 |

**不修改**：`src/judgment/judge.py`、`src/memory/store.py`、`src/memory/models.py`、`src/omni/router.py`、`src/audio/playback.py`（只调用，不改）。

---

## 5. 模块职责与方法签名

### 5.1 `service.py`

```python
class ProactiveService:
    """主动服务层编排器。daemon 线程，OMNI 模式下由 runtime 启动。"""

    def __init__(self, omni_client, speaker, config, context=None):
        """
        Args:
            omni_client: OmniClient 实例（订阅其 callbacks 拿观察）
            speaker: VoiceboxSpeaker 单例（复用，绝不新建，见 §12.1）
            config: Config 实例
            context: 保留参数，传统模式将来接入用（本期恒 None）
        """

    def start(self) -> bool: ...
    def stop(self) -> None: ...
    def is_running(self) -> bool: ...

    # 供 GUI / 工具调用的状态查询
    def get_state_snapshot(self) -> dict:
        """返回 {regime, scene, pending_tasks, policies_count, last_observation_ts}"""

    def get_active_scene(self) -> Optional[Scene]: ...
    def get_pending_tasks(self) -> list[dict]: ...
```

内部持有的子组件：`EventBus` / `FusionEngine` / `RegimeTracker` / `PolicyStore` / `PolicyMatcher` / `ActionDispatcher` / `TimeWheel` / `Advisor` / `NotifyRouter` / `KnowledgeService`。

`_loop()` 为唯一 daemon 线程主体，tick 间隔 `proactive_tick_interval`（默认 1.0s），职责顺序：
1. 排空 EventBus 待处理事件 → FusionEngine
2. **睡眠对账**（§8.3，必须在触发前做）
3. TimeWheel 取到期任务 → GuardGate → NotifyRouter
4. 写回 `last_tick`

### 5.2 `events.py`

```python
class EventBus:
    """线程安全发布订阅。生产者=各 Source，消费者=Service._loop 单线程排空。"""
    def publish(self, event: PerceptionEvent) -> None: ...
    def drain(self, max_items: int = 100) -> list[PerceptionEvent]: ...
    def subscribe(self, kind: str, handler: Callable[[PerceptionEvent], None]) -> None: ...
    def clear(self) -> None: ...
```

队列有界（默认 1000），满时**丢弃最旧**并计数，绝不阻塞生产者（生产者含 PyAudio 回调线程，阻塞会导致音频断续）。

### 5.3 `fusion.py`

```python
class FusionEngine:
    """多源事件融合为稳定 ObservationState；维护 Scene 滞回。"""
    def __init__(self, config, embedder=None): ...

    def apply(self, event: PerceptionEvent) -> Optional[FusionResult]:
        """返回本次融合是否产生 Scene 切换/更新，供 PolicyMatcher 消费"""

    def get_scene(self) -> Optional[Scene]: ...
    def get_regime(self) -> str: ...                    # "A" | "B"
    def latest_observation(self) -> Optional[Observation]: ...
    def observation_age(self) -> float: ...             # 秒；> TTL 视为无新鲜观察
```

**滞回规则**：场景切换需连续 `proactive_scene_debounce`（默认 2）次语义一致的观察。语义一致性判定：`descriptor` 归一化后相等，或 embedding 余弦相似度 ≥ `proactive_scene_same_threshold`（默认 0.85）。防单次幻觉切场景。

### 5.4 `regime.py`

```python
class RegimeTracker:
    def __init__(self, config): ...
    def current(self) -> str: ...                       # "A" | "B"
    def on_lid_event(self, closed: bool, ts: float) -> None: ...
    def on_observation_gap(self, gap_seconds: float) -> None:
        """无 lid 源时的降级推断：观察中断超过阈值 + 定位静止 → 疑似 Regime B"""
    def is_inference_only(self) -> bool: ...            # True=靠降级推断，非硬件事件
```

### 5.5 `sources/base.py`

```python
class SourceAdapter(ABC):
    """所有感知源的统一抽象。"""
    name: str = ""

    def __init__(self, bus: EventBus, config): ...

    @abstractmethod
    def start(self) -> bool: ...
    @abstractmethod
    def stop(self) -> None: ...
    def available(self) -> bool: ...                    # 依赖缺失时 False，优雅降级
```

`available()` 返回 False 时，Service 记一行日志并继续，**不报错、不中断启动**。定位权限未授予、pyobjc 未装、IOKit 不可用都走这条路径。

### 5.6 `sources/omni_obs.py`

```python
class OmniObservationSource(SourceAdapter):
    """订阅 OmniClient 文本流，解析 <<JAC_OBS>> 带内协议。"""
    name = "omni_observation"

    def __init__(self, bus, config, omni_client): ...
    def start(self) -> bool: ...    # 挂到 omni_client.cb.on_text（不替换现有回调）
    def stop(self) -> None: ...

    # 纯函数，便于单测
    @staticmethod
    def feed_chunk(buffer: str, chunk: str) -> tuple[Optional[dict], str, str]:
        """
        增量解析。返回 (parsed_json_or_None, 剩余buffer, 可朗读文本)。
        「可朗读文本」= 本协议之外的部分，交回 voicebox_bridge.feed。
        """

    def stats(self) -> dict:
        """{observed: n, parse_failed: n, stale: n, last_ts: float}"""
```

### 5.7 `sources/location.py`

```python
class LocationSource(SourceAdapter):
    """CoreLocation 定位 + 地理围栏。pyobjc 缺失或权限未授予时 available()=False。"""
    name = "location"

    def start(self) -> bool: ...
    def stop(self) -> None: ...
    def current_position(self) -> Optional[tuple[float, float, float]]:
        """(lat, lon, accuracy_m)；无定位返回 None"""

    def watch_geofence(self, key: str, lat: float, lon: float,
                       radius_m: float) -> None: ...
    def unwatch_geofence(self, key: str) -> None: ...
    def is_inside(self, key: str) -> Optional[bool]: ...   # None=未知

    @staticmethod
    def haversine_m(lat1, lon1, lat2, lon2) -> float: ...   # 纯函数，单测
```

产出事件：`location.entered` / `location.left` / `location.moved`（payload 含 `lat/lon/accuracy/poi_hint`）。

### 5.8 `sources/lid.py`

```python
class LidStateSource(SourceAdapter):
    """开合盖检测。IOKit 通知优先；不可用时由 FusionEngine 降级推断（本类 available()=False）。"""
    name = "lid"
    def start(self) -> bool: ...
    def stop(self) -> None: ...
    def is_closed(self) -> Optional[bool]: ...     # None=未知
```

产出事件：`lid.closed` / `lid.opened`。

### 5.9 `policy/store.py`

```python
class PolicyStore:
    """策略持久化。复刻 src/memory/store.py 已验证的模式（不复用类，独立实现）。"""

    FILE_VERSION = "1.0.0"
    DEFAULT_MAX_POLICIES = 500

    def __init__(self, path: Optional[str] = None, embedder=None): ...

    def load(self) -> None: ...                 # 启动一次性加载进内存 dict
    def upsert(self, policy: ProactivePolicy) -> None: ...
    def remove(self, policy_id: str) -> bool: ...
    def get(self, policy_id: str) -> Optional[ProactivePolicy]: ...
    def list_enabled(self) -> list[ProactivePolicy]: ...
    def flush(self) -> None: ...                # 同步强制落盘
    def close(self) -> None: ...
    def stats(self) -> dict: ...
    def export_json(self) -> str: ...
```

必须复刻的已验证行为（`src/memory/store.py` 文档字符串与实现为准）：
- 原子写：`tmp → fsync → os.replace`
- 保留 `.bak` 用于损坏恢复
- 版本信封 `{"version", "policies"}`，MAJOR 不符抛异常
- 单条缺必填字段 → 容忍跳过并计入 `invalid_policies`
- 权限 `0o700` 目录 + `0o600` 文件
- 内存为权威副本，落盘交后台防抖批量写线程
- 所有内存读写持 `self._lock`；序列化→写盘路径由 `self._flush_lock` 串行化

### 5.10 `policy/matcher.py`

```python
class PolicyMatcher:
    def __init__(self, store: PolicyStore, config, embedder=None): ...

    def match(self, state: ObservationState,
              event: Optional[PerceptionEvent] = None) -> list[MatchResult]:
        """返回按 priority 排序的命中策略；未命中返回空列表"""

    def _match_structural(self, policy, state, event) -> bool: ...
    def _match_semantic(self, policy, state) -> float: ...   # 返回相似度 [0,1]
    def check_cooldown(self, policy_id: str, scene_key: str) -> bool: ...
    def mark_fired(self, policy_id: str, scene_key: str, ts: float) -> None: ...
```

`MatchResult = {policy, similarity, matched_by: ["structural"|"semantic"], reason: str}`；`reason` 进 `Scene.evidence`，服务可审计性（回答"你为什么提醒我这个"）。

**匹配顺序**：先结构谓词（零成本），结构全通过才做语义匹配（有 embedding 成本）。纯语义策略（`trigger` 为空）直接走语义分支。

### 5.11 `policy/dsl.py`

```python
class ExprEvaluator:
    """trigger / condition / timing.formula 的安全求值器。"""

    ALLOWED_OPS = frozenset({...})   # 算术 + 比较 + 布尔，白名单

    @staticmethod
    def eval_expr(expr: str, variables: dict) -> Any:
        """
        安全求值。禁止 eval/exec，用 ast.literal_eval + 受限 AST 遍历。
        变量名不在 variables 中 → 抛 MissingVariable（调用方降级处理）。
        """

    @staticmethod
    def validate_policy(policy: ProactivePolicy) -> list[str]:
        """返回错误列表；空列表=合法。用于工具侧拒绝非法策略。"""
```

⚠️ **安全要求**：`timing.formula` 是用户/模型可控字符串，**绝不能用 `eval()`**。必须用 AST 白名单遍历，只允许算术运算、比较、布尔运算与已注册变量名。这是安全红线。

### 5.12 `policy/authoring.py`

```python
class PolicyAuthoring:
    """把对话教学 / agent 观察归纳转成 ProactivePolicy。"""

    def __init__(self, store: PolicyStore, brain=None, embedder=None, config=None): ...

    def from_user_text(self, intent_text: str) -> Optional[ProactivePolicy]:
        """用户说"以后每次我到公司提醒我打卡" → 抽取出策略并落库。
        抽取失败返回 None（不抛异常）。"""

    def propose_from_pattern(self, observations: list[Observation]) -> Optional[ProactivePolicy]:
        """agent 主动归纳重复模式。产出 provenance="agent_proposed", confirmed=False。"""

    def confirm(self, policy_id: str) -> bool:
        """bo s s 点头后生效。"""

    def list_unconfirmed(self) -> list[ProactivePolicy]: ...
```

### 5.13 `knowledge.py`

```python
class KnowledgeProvider(ABC):
    name: str = ""
    @abstractmethod
    def lookup(self, topic_key: str, query_text: str) -> Optional[KnowledgeFact]: ...
    def available(self) -> bool: ...

class MemoryProvider(KnowledgeProvider):
    """查现有 memory store（向量 + tags）。本期默认实现。"""
    name = "memory"
    def lookup(self, topic_key, query_text) -> Optional[KnowledgeFact]: ...

class TeachingProvider(KnowledgeProvider):
    """对话教学直接落库。本期实现。"""
    name = "teaching"
    def teach(self, topic_key: str, facts: list[dict],
              source_url: str = "taught_by_user",
              ttl_days: int = 180) -> KnowledgeFact: ...
    def lookup(self, topic_key, query_text) -> Optional[KnowledgeFact]: ...

class WebSearchProvider(KnowledgeProvider):
    """接口预留，本期 NOT IMPLEMENTED。available() 恒 False。"""
    name = "web_search"

class KnowledgeService:
    """按优先级串联 providers；命中即返回并回写缓存。"""
    PROVIDER_ORDER = ["memory", "teaching"]   # web_search 将来插在此列表

    def __init__(self, memory_manager=None, embedder=None, config=None): ...
    def lookup(self, topic_key: str, query_text: str = "") -> Optional[KnowledgeFact]: ...
    def store(self, fact: KnowledgeFact) -> None: ...
    def invalidate(self, topic_key: str) -> bool: ...
```

**世界知识落库映射**（复用现有 `MemoryStore`，零改动）：

```python
KnowledgeFact → MemoryFact(
    kind      = MemoryKind.convention,
    source    = MemorySource.inferred,      # 推断，非用户明示
    tags      = ["proactive", "knowledge", topic_key],
    ttl       = <ISO8601 或 None>,          # 交给 store 的 compaction 自动清理
    weight    = 0.4,                        # 低于用户明示，检索时自然让位
    content   = json.dumps(fact_dict, ensure_ascii=False),
)
```

### 5.14 `scheduler.py`

```python
class TimeWheel:
    """时间轮 + JSON 落盘 + 睡眠对账。纯标准库（heapq）。"""

    TICK_KEY = "last_tick"

    def __init__(self, path: Optional[str] = None, config=None): ...

    def schedule(self, task: ScheduledTask) -> str: ...       # 返回 task.id
    def cancel(self, task_id: str) -> bool: ...
    def due(self, now: Optional[float] = None) -> list[ScheduledTask]: ...
    def pending(self) -> list[ScheduledTask]: ...
    def peek_next_fire_at(self) -> Optional[float]: ...

    def load(self) -> None: ...
    def flush(self) -> None: ...

    # 睡眠对账（§8.3）
    def reconcile_after_sleep(self, now: float) -> list[ReconcileAction]:
        """检测 now - last_tick 异常 → 按各任务 misfire_policy 产出动作"""
    def resolve_sleep_policy(self) -> str:
        """auto|adaptive|always_awake；auto 时读 pmset 判定"""
    def sleep_gap_detected(self, now: float) -> bool: ...
```

`ReconcileAction = {task_id, policy: "fire_now"|"skip"|"reschedule", new_fire_at: Optional[float]}`

**不使用 APScheduler**：核心难点是"系统睡眠后对账"，这个逻辑无论用不用 APScheduler 都要自己写；自己写更可控、零依赖、可单测。

### 5.15 `advisor.py`

```python
class Advisor:
    """长尾旁路：策略未命中但 user_need≠none 时，升级 qwen3.6-35b 判断。"""

    def __init__(self, router: EscalationRouter, config, playback_state=None): ...

    def should_consult(self, state: ObservationState, ts: float) -> bool:
        """全部护栏在此，任一不过即 False。见 §12.4"""

    def consult(self, state: ObservationState) -> Optional[AdvisorVerdict]: ...
    def in_cooldown(self, ts: float) -> bool: ...
    def mark_consulted(self, ts: float) -> None: ...

    @staticmethod
    def sanitize_verdict(raw: str) -> Optional[AdvisorVerdict]:
        """输出校验：剥离内部协议残留、长度上限、格式校验。不合格返回 None。"""
```

`AdvisorVerdict = {should_notify: bool, level: str, message: str, evidence: str}`

⚠️ **advisor 的 verdict 绝不直接朗读**，必须先过 `sanitize_verdict`，再进 `NotifyRouter`。理由见 §12.4。

### 5.16 `dispatcher.py`

```python
class ActionDispatcher:
    def __init__(self, notify_router, time_wheel, escalation_router, config): ...

    def dispatch(self, action: dict, ctx: ActionContext) -> DispatchResult: ...
    def _do_notify(self, action, ctx) -> bool: ...
    def _do_set_timer(self, action, ctx) -> Optional[str]: ...   # 返回 task_id
    def _do_escalate(self, action, ctx) -> Optional[str]: ...
    def _do_tool_call(self, action, ctx) -> Optional[str]: ...
```

`ActionContext = {scene, policy_id, event, knowledge, ts, level}`

### 5.17 `notify/`

```python
class NotifyChannel(ABC):
    name: str = ""
    @abstractmethod
    def send(self, title: str, body: str, level: str) -> bool: ...
    def available(self) -> bool: ...

class NotifyRouter:
    LEVEL_CHANNELS = {
        "urgent": ["voicebox", "macos"],
        "normal": ["macos"],
        "low":    [],           # 攒批，下次交互一并播报
    }
    def __init__(self, channels: dict[str, NotifyChannel], config, playback=None): ...
    def send(self, level: str, title: str, body: str,
             dedupe_key: Optional[str] = None) -> list[str]:
        """返回成功送达的通道名列表；免打扰窗口内 urgent 降级 normal"""
    def is_quiet_hours(self, ts: Optional[float] = None) -> bool: ...
    def should_dedupe(self, dedupe_key: str) -> bool: ...
    def flush_pending_low(self) -> list[dict]:
        """取出攒批的 low 级通知，供下次交互时播报"""
```

`VoiceboxChannel.send()` 实现要点（全部必须遵守，见 §12.1-12.3）：
1. 复用注入的 `VoiceboxSpeaker` **单例**，绝不新建实例
2. 播报前调 `playback.mark_external_playback()`
3. 强制默认音频输出（覆盖蓝牙），确保合盖插耳机场景出声
4. 返回 False 时降级 macOS 通知，**不静默丢弃**

`MacOSChannel.send()`：`osascript -e 'display notification ...'`，零依赖。注意转义（用 `subprocess` 列表参数，`shell=False`）。

---

## 6. 带内观察协议

因 C1（单会话），观察必须复用主会话输出流。协议与现有 `<<CALL_QWEN>>` **同构同层**。

### 6.1 协议格式

```
<<JAC_OBS>>{"v":1,"scene":"地下车库，水泥柱，停满车","scene_conf":0.82,
"activity":"正在步行离开","user_need":"none","need_text":"",
"poi_hint":"万达广场B2","entities":["车牌","指示牌"],"note":""}<<JAC_OBS_END>>
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `v` | int | 是 | 协议版本，当前 1 |
| `scene` | str | 是 | **开放词表自由描述**，禁止枚举约束 |
| `scene_conf` | float | 是 | [0,1] |
| `activity` | str | 是 | 自由描述用户正在做什么 |
| `user_need` | str | 是 | `none` \| `help_detected` \| `explicit_request` |
| `need_text` | str | 否 | `user_need≠none` 时的自由描述 |
| `poi_hint` | str | 否 | 来自画面标牌或定位反查 |
| `entities` | list[str] | 否 | 画面/语音关键实体，开放列表 |
| `note` | str | 否 | 补充说明 |

### 6.2 prompts 改造要点（`src/omni/prompts.py`）

在现有 `SYSTEM_PROMPT` 基础上追加，必须保留现有 `<<CALL_QWEN>>` 约定与「纯寒暄必须口语回应、不吐令牌」的硬化规则：

- 每隔约 `proactive_observe_interval`（默认 10s）或场景发生显著变化时，输出一个 `<<JAC_OBS>>…<<JAC_OBS_END>>` 块
- 观察块**只描述客观事实**，不面向用户说话
- 判断 bo s s 是否需要帮助：`user_need` 取三值之一
- **协议块之外的正常对话文本照常输出**（两者可在同一轮流式输出中共存）
- 纯寒暄（"你好/在吗"）**只回应寒暄**，观察块按周期输出，不得用观察块代替回应

### 6.3 客户端解析规则（`src/omni/client.py` `_on_text`）

| # | 规则 |
|---|---|
| R1 | 命中 `<<JAC_OBS>>` 进入累积态；累积期间的文本**不进** `voicebox_bridge.feed`、**不** `_broadcast`、**不进** `_reply_buf` |
| R2 | 遇 `<<JAC_OBS_END>>` 结算：JSON 解析成功 → 发 `PerceptionEvent(kind="observe")`；失败 → 丢弃 + `parse_failed` 计数 + 一行日志 |
| R3 | 与 `<<CALL_QWEN>>` 共存：按在文本流中出现的**先后顺序**分别处理，互不干扰；一段 delta 内可能同时含两者 |
| R4 | 复用现有 `_shown_len` 裁剪机制，**保证用户看不到任何内部协议文本**（对齐 2026-09-06 修复） |
| R5 | 跨 delta 累积：协议块可能被切分到多个 delta，必须按现有 `_try_finalize_pending` 的跨换行累积经验实现，**不得假设单 delta 完整** |
| R6 | 观察 TTL：`proactive_observe_ttl`（默认 15.0s）过期即视为无新鲜观察。**不假设模型守时** |
| R7 | 协议块超过 `proactive_observe_max_bytes`（默认 4096）仍未闭合 → 丢弃累积缓冲 + 计数，防内存泄漏 |

### 6.4 解析纯函数契约

```python
OmniObservationSource.feed_chunk(buffer, chunk) -> (parsed_or_None, remaining_buffer, speakable_text)
```

`speakable_text` 是协议块**之外**的部分，调用方把它交回 `voicebox_bridge.feed`。这个三分返回值设计使解析逻辑可完全单测，不依赖 WebSocket。

---

## 7. 数据模型（`models.py`）

```python
@dataclass
class PerceptionEvent:
    id: str                      # uuid4
    kind: str                    # observe|location|lid|timer|system
    source: str                  # omni_observation|location|lid|system|scheduler
    ts: float                    # time.time()
    payload: dict
    confidence: float            # [0,1]
    ttl: float = 0.0             # >0 表示多久后失去意义，防陈旧事件误触发

@dataclass
class Observation:               # 解析 <<JAC_OBS>> 得到，开放词表
    scene_text: str
    activity_text: str
    user_need: str               # none|help_detected|explicit_request
    need_text: str = ""
    poi_hint: str = ""
    entities: list[str] = field(default_factory=list)
    note: str = ""
    scene_conf: float = 0.0
    ts: float = 0.0

@dataclass
class Scene:                     # 融合后的稳定态
    key: str                     # 归一化键（滞回去重用），非枚举
    descriptor: str              # 保留原始开放描述
    confidence: float
    entered_at: float
    exited_at: Optional[float] = None
    dwell_seconds: float = 0.0
    evidence: dict = field(default_factory=dict)   # 可审计："你为什么提醒我这个"
    regime: str = "A"            # "A" 开盖 | "B" 合盖
    poi_hint: str = ""

@dataclass
class ProactivePolicy:           # 运行时创作，开发者不预写
    id: str
    intent: str                  # 用户原话或 agent 归纳的意图，开放文本
    trigger: dict = field(default_factory=dict)
    trigger_embedding: Optional[list[float]] = None
    semantic_threshold: float = 0.62
    condition: Optional[dict] = None
    action: dict = field(default_factory=dict)
    timing: Optional[dict] = None
    misfire_policy: str = "fire_now"
    notify: dict = field(default_factory=dict)
    provenance: str = "user_taught"   # user_taught|agent_proposed|builtin_example
    confirmed: bool = True       # agent_proposed 初始必须 False
    cooldown: float = 300.0
    ttl: Optional[str] = None    # 支持"这周提醒我"类时效策略
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    # 序列化
    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, d: dict) -> "ProactivePolicy": ...

@dataclass
class ScheduledTask:
    id: str
    policy_id: str               # 无策略来源时为 "manual" 或 "advisor"
    fire_at: float               # 绝对 epoch 秒
    payload: dict
    level: str                   # urgent|normal|low
    misfire_policy: str = "fire_now"   # fire_now|skip|reschedule
    created_at: float = field(default_factory=time.time)
    reschedule_fn: Optional[str] = None  # 重算函数注册名，如 "recalc_by_dwell"
    cancel_on: Optional[dict] = None     # 如 {"event": "location.left"} → 取消

@dataclass
class KnowledgeFact:
    topic_key: str               # 开放字符串，如 "parking:<poi_hint>"
    facts: list[dict]            # 开放结构，键名由来源决定，代码不硬编码
    source_url: str
    retrieved_at: float
    ttl_days: int = 30
    confidence: float = 0.5

@dataclass
class AdvisorVerdict:
    should_notify: bool
    level: str                   # urgent|normal|low
    message: str
    evidence: str

@dataclass
class ObservationState:
    scene: Optional[Scene]
    observation: Optional[Observation]
    regime: str                  # "A"|"B"
    last_observe_ts: float
    location: Optional[dict] = None
    is_stale: bool = False       # 观察超过 TTL
```

### 7.1 策略 DSL（确定性、可单测、绝不过模型）

```yaml
id: policy.<uuid>
intent: "在停车库停久了，快超免费时长前提醒我"
trigger:
  all:
    - kind: observe
    - scene_matches: "地下车库 停车场 车库"      # 语义匹配用文本，非枚举
      min_confidence: 0.6
condition:
  min_dwell_seconds: 60                          # 防路过误判
  regime: any                                    # any|A|B
knowledge_query: "parking:{scene.poi_hint}"
timing:
  offset_from: "scene.entered_at"
  formula: "knowledge.free_minutes * 60 - config.proactive_lead_seconds"
action:
  type: notify
  level: urgent
  channels: [voicebox, macos]
  message_template: "停车免费时长还剩 {remain} 分钟，超时后每 {step_minutes} 分钟加收 {step_fee} 元"
cancel_on:
  event: location.left
misfire_policy: reschedule
cooldown: 600
provenance: user_taught
confirmed: true
```

⚠️ 上面 YAML **只是 DSL 语法示例**，不是内置规则。`proactive/examples/` 下的文件仅作单测夹具，**运行时不加载**（见 §0.2）。`formula` 中的 `knowledge.free_minutes` 是 `KnowledgeFact.facts` 字典的任意键，由数据决定，代码不硬编码该键名。

**trigger 支持的结构谓词**（`policy/dsl.py` 实现）：
- `kind` — 事件类型
- `scene_matches` — 与 `scene.descriptor` 的语义相似度匹配（走 embedder）
- `min_confidence` / `max_confidence`
- `activity_matches` / `user_need` / `entity_any` / `entity_all`
- `regime` — `any`|`A`|`B`
- `poi_type` / `inside_geofence`
- `all` / `any` / `not` — 组合器，可嵌套

---

## 8. 调度器与睡眠对账

### 8.1 三种策略

`proactive_sleep_policy`: `auto` | `adaptive` | `always_awake`

| 模式 | 行为 | 依赖 |
|---|---|---|
| `auto`（默认） | 启动时 `pmset -g custom` 读 `SleepDisabled`，据此自动选下面两种；读不到则取 `adaptive` | 无 sudo |
| `adaptive` | 每 tick 比对 `now - last_tick`，远超预期即判定睡过 → 按 `misfire_policy` 处理 | 无 sudo |
| `always_awake` | 假设进程不冻结，精准定时；**仍保留对账兜底**（拔电/重启仍会睡） | bo s s 手动执行 `sudo pmset -c disablesleep 1` |

bo s s 已选定「两者都做，可切换」，故 `auto` 必须真实探测系统状态，不能只读配置。

### 8.2 `misfire_policy` 语义

| 值 | 语义 | 适用 |
|---|---|---|
| `fire_now` | 仍有效，立即补发 | 会议、吃药、约定事项 |
| `skip` | 已过期无意义，丢弃 + 记日志 | "还剩 15 分钟"类时效提醒睡过头后**不该再说** |
| `reschedule` | 调 `reschedule_fn` 用**当前事实**重算 | 需按实际状态重新推算的（如按实际停留时长重算） |

`reschedule_fn` 是**注册名字符串**（如 `"recalc_by_dwell"`），由 `TimeWheel` 内的注册表解析，**禁止动态导入或 eval 任意代码**。

### 8.3 对账算法（`reconcile_after_sleep`）

```
now = time.time()
gap = now - last_tick
if gap > max(tick_interval * GAP_FACTOR, MIN_GAP_SECONDS):   # 默认 factor=5, min=30s
    for task in pending_tasks:
        if task.fire_at >= now: continue                     # 未到期，跳过
        overdue = now - task.fire_at
        match task.misfire_policy:
            "fire_now":   emit ReconcileAction(fire_now)      # 立即补发
            "skip":       cancel(task); log; emit ReconcileAction(skip)
            "reschedule": new_at = registry[task.reschedule_fn](task, now)
                          if new_at is None or new_at <= now: → 转 skip
                          else: task.fire_at = new_at; emit ReconcileAction(reschedule)
last_tick = now
```

⚠️ **不能盲目按原定时间触发**。若睡了 3 小时才开盖，提醒"还剩 15 分钟"就是错的——这正是 `skip` / `reschedule` 存在的理由。

### 8.4 预约唤醒（可选增强）

`pmset schedule wakeorpoweron` **需要 sudo**。`adaptive` 模式拿不到权限时，**降级为"唤醒后对账补发"，不预约唤醒**。这个降级路径必须实现，不能假设 sudo 可用。

### 8.5 落盘

- 路径：`~/.jac/proactive/schedule.json`（`proactive_schedule_path` 可覆盖）
- 权限：目录 `0o700`，文件 `0o600`
- 信封：`{"version": "1.0.0", "tasks": [...], "last_tick": <float>}`
- 写法：复刻 `MemoryStore` 的原子写 + `.bak`
- `last_tick` 就是对账依据，**每次 tick 结束都要更新**（防抖批量写即可，不必每 tick fsync）
- 路径在用户目录，**不进仓库**，需加入 `.gitignore`

---

## 9. 新增工具（挂 `src/tools/registry.py`）

| 工具 | 参数 | 行为 |
|---|---|---|
| `create_proactive_policy` | `intent: str, trigger: dict, action: dict, timing: dict?, notify: dict?, cooldown: float?, ttl: str?, confirmed: bool=true` | 经 `ExprEvaluator.validate_policy` 校验 → `PolicyStore.upsert`。**agent 自主创建的策略必须传 `confirmed=false`**，返回文本提示需 bo s s 确认 |
| `set_reminder` | `text: str, fire_after_seconds: float? \| fire_at: str?, level: str="normal", misfire_policy: str="fire_now"` | 建 `ScheduledTask(policy_id="manual")` → `TimeWheel.schedule`。返回 task_id |
| `get_proactive_context` | 无 | 返回当前 `{regime, scene, pending_tasks, policies_count, last_observation_age}`，让大脑拥有主动层上下文 |
| ~~`web_search`~~ | — | **本期不实现**（bo s s："以后再加"）。仅在 `KnowledgeProvider` 留接口位 |

工具 schema 需遵循 `registry.py` 现有格式（`name` / `description` / `parameters` JSON Schema / `func`）。`func` 签名统一 `def f(arguments: dict) -> str`，异常由 `execute_tool` 捕获转错误文本（现有行为，不改）。

`create_proactive_policy` 是**本设计"agent 自我扩展"的核心机制**：大脑听到"以后每次我到公司提醒我打卡"就自己抽策略写库；观察到重复模式也能主动提议。**开发者不预写场景**。

---

## 10. 配置项（`src/utils/config.py`）

遵循现有 dataclass 字段 + 环境变量覆盖模式（参考 `omni_*` / `judgment_*` 写法）：

```python
# --- 主动服务层 ---
proactive_enabled: bool = True
proactive_tick_interval: float = 1.0        # 主循环 tick（秒）
proactive_sleep_policy: str = "auto"        # auto|adaptive|always_awake
proactive_sleep_gap_factor: float = 5.0     # 对账触发倍率
proactive_sleep_min_gap: float = 30.0       # 对账最小间隔（秒）
proactive_lead_seconds: int = 900           # 提前 15 分钟
proactive_observe_interval: float = 10.0    # 期望 omni 输出观察的周期
proactive_observe_ttl: float = 15.0         # 观察新鲜期
proactive_observe_max_bytes: int = 4096     # 协议块上限，防泄漏
proactive_scene_debounce: int = 2           # 连续 N 次一致才切场景
proactive_scene_same_threshold: float = 0.85  # 场景语义一致性阈值
proactive_semantic_threshold: float = 0.62  # 策略语义匹配门槛
proactive_advisor_enabled: bool = True
proactive_advisor_cooldown: float = 300.0
proactive_quiet_hours: str = ""             # "23:00-07:00"，空=不禁
proactive_location_enabled: bool = True     # bo s s 已同意授权
proactive_geofence_radius_m: float = 80.0
proactive_lid_enabled: bool = True
proactive_schedule_path: str = ""           # 空 → ~/.jac/proactive/schedule.json
proactive_policy_path: str = ""             # 空 → ~/.jac/proactive/policies.json
proactive_max_policies: int = 500
```

环境变量名 = 字段名全大写（与现有 `OMNI_ECHO_GATE` / `JUDGMENT_ENGINE_ENABLED` 一致）。

### 10.1 依赖变更

| 包 | 用途 | 必需性 |
|---|---|---|
| `pyobjc-framework-CoreLocation` | 定位 / 地理围栏 | 必需（bo s s 已同意授权弹窗） |
| `pyobjc-framework-Cocoa` | pyobjc 基础，随之引入 | 必需 |

**不引入 APScheduler**（理由见 §5.14）。macOS 通知走系统自带 `osascript`，零依赖。`beautifulsoup4` / `requests` 已在 `requirements.txt`，将来 web search 直接可用。

同步要求（AGENTS.md 硬性规定）：更新 `requirements.txt` + `new_computer_download/` 一键安装脚本 + `READMEfirst.md`（含 macOS 定位授权步骤说明）。

---

## 11. 分期计划与验收标准

| 期 | 内容 | 验收标准（可执行、可判定） |
|---|---|---|
| **P0** | `models` / `events` / `service` / `regime` 骨架 + `runtime` 接线 + `config` 配置组 | ①启动 OMNI 后 `ProactiveService` daemon 线程存活 ②注入事件能在 bus 中流转并被 `FusionEngine` 消费 ③退出无残留线程 ④传统模式启动不受影响（不启动主动层）⑤单测覆盖 models 序列化往返 |
| **P1** | 观察协议：`prompts` 改造 + `omni_obs` 解析 + 客户端令牌拦截 + `fusion` 滞回 | ①omni 周期性输出观察 ②**用户听不到、也看不到任何协议文本**（TTS 与 GUI 均无残留）③`feed_chunk` 纯函数单测覆盖：单 delta 完整 / 跨 delta 切分 / JSON 非法 / 超长未闭合 ④同一段文本含 `<<JAC_OBS>>` 与 `<<CALL_QWEN>>` 时互不干扰 ⑤单次异常观察**不**切场景（滞回生效）⑥真机跑通并记录 `parse_failed` 率 |
| **P2** | 调度 + 分级通知：`TimeWheel` + 落盘 + **睡眠对账** + `notify` 两通道 + 分级路由 | ①手动塞 60s 后 urgent 任务 → 蓝牙出声 + 通知弹出 ②**伪造 `last_tick`**（monkeypatch，不真睡眠）验证 `fire_now` 补发 / `skip` 丢弃 / `reschedule` 重算三策略各自正确 ③通知播报后回声门控不误判为用户说话（断言 `mark_external_playback` 被调用）④进程重启后 pending 任务从 JSON 恢复 ⑤免打扰窗口内 urgent 降级 normal ⑥去重键 + 冷却生效 |
| **P3** | **策略引擎**：`PolicyStore` + `PolicyMatcher`（结构+语义）+ `ExprEvaluator` + `ActionDispatcher` + 3 个新工具 | ①对话教一条策略 → 落库 → 下次匹配触发 ②`provenance="agent_proposed"` 且 `confirmed=false` 的策略**不生效** ③**同一引擎跑通两个互不相关的场景**（场景由 bo s s 实施时指定，设计不预设）④`ExprEvaluator` 安全测试：注入 `__import__` / `os.system` / 任意属性访问**全部被拒** ⑤`timing.formula` 变量缺失时降级不崩溃 ⑥非法策略被 `validate_policy` 拒绝并回喂错误文本给模型 |
| **P4** | 感知源扩展：`location` / `lid` / `system` | ①定位进出 POI 半径产出正确 entered/left 事件（haversine 纯函数单测）②合盖事件被 `RegimeTracker` 捕获并切 Regime B ③IOKit 不可用时 `available()=False`，服务正常降级不崩 ④权限未授予时同样优雅降级 ⑤真机：合盖走开 → Regime B → 已注册任务照常触发 |
| **P5** | `advisor` 长尾旁路 + 全部护栏 | ①单测覆盖「静默期 / 回声期幻觉任务**绝不触达用户**」，对齐现有 `tests/test_omni_echo_gate.py` 验证思路 ②`sanitize_verdict` 剥离协议残留、拒超长、拒非法 level ③advisor 冷却生效 ④升级结果经 `EscalationRouter` 且**一定出声**（Voicebox 优先 → macOS 通知兜底，不静默跳过）⑤每次触发写 `evidence` 留痕 |
| **P6** | 可选：GUI 策略管理面板 / `WebSearchProvider` / launchd 常驻 | 按需 |

### 11.1 P3 通用性硬门禁（关键）

P3 验收③是**防止再次把示例当目标的硬门禁**：必须用两个语义无关的场景（例如一个基于地点、一个基于时间或活动）跑通同一套引擎，证明策略匹配不含任何场景特化分支。若实现中出现 `if scene == "xxx"` 类代码，视为设计错误、P3 不通过。

### 11.2 分期理由

P1 与 P2 刻意分离——**协议解析**与**调度可靠性**是两个独立失败域，混在一起调试极难定位。P2 的睡眠对账是整个方案最容易翻车的部分，必须独立验收后再叠策略引擎。

---

## 12. 实施约束（必须遵守的已验证坑）

以下每条都来自 bo s s 的真机事故记录，**违反会重现已修复的 bug**。

### 12.1 VoiceboxSpeaker 必须复用单例

CHANGELOG 2026-08-15：回灌线程与桥接线程并发使用**两个无锁 `VoiceboxSpeaker` 实例** → 重叠播放 + 同毫秒临时文件互覆盖 → 声音丢失。

**要求**：主动层接收 `runtime` 已创建的 speaker 实例注入，**绝不 `VoiceboxSpeaker()` 新建**。若必须新建，须自带 `RLock` 串行锁 + `uuid` 文件名（现有类已具备）。

### 12.2 HTTP 客户端必须绕过本机代理

CHANGELOG 2026-08-16 夜：`requests.Session()` 未绕过代理 → localhost 的 `/generate`、`/audio` 被代理劫持成 502/504（日志表现为 `：None`）→ 轮询 60s 全失败降级。

**要求**：主动层任何 `requests.Session()` 必须：

```python
s = requests.Session()
s.trust_env = False
s.proxies = {"http": None, "https": None}
```

单次请求超时 ≤10s（快速暴露 hang），总轮询超时可配（默认 120s）。错误文案带「最近 HTTP 状态」以区分代理问题（502/504）vs 服务端慢（500）。

### 12.3 播报必须注册到 playback 出声状态

见 §1.3 C4。2026-09-06 真机事故：TTS 外放被麦克风采集 → omni 把自己的语音当用户发言 → 自问自答 → 幻觉升级任务并**真的执行**了。

**要求**：主动层所有语音播报前后必须调 `playback.mark_external_playback()`；advisor 护栏必须查 `playback.is_playback_active()` 与 `seconds_since_playback_end()`。

### 12.4 模型输出绝不直接朗读，必须有护栏

CHANGELOG 2026-08-16 续：静音期（RMS 0.003~0.005，bo s s 未开口）omni **凭空生成**升级令牌任务并自动执行；因触发后静音主对话，连带吞掉 bo s s 随后真实发言的回复。

**要求**（`Advisor.should_consult` 全部实现）：
1. 静默期禁止触发：令牌前 `proactive_speech_window`（默认 3.0s）内无真实人声证据（RMS ≥ 0.02）→ 判幻觉，丢弃
2. 回声期一律判幻觉：`playback` 出声窗口内 → 丢弃
3. 冷却 `proactive_advisor_cooldown`（默认 300s）
4. `_hallucinated` 式一次性守卫，防「拦了又被后续标点偷偷触发」
5. verdict 过 `sanitize_verdict` 后才进通知路由
6. 每次触发写 `evidence` 留痕

### 12.5 内部协议文本绝不显示给用户

CHANGELOG 2026-09-06：bo s s 看到的「给您推荐一部电」实为 omni 幻觉输出，被误认为 ASR 识别结果（full_duplex 协议**不回传用户 ASR 原文**）。

**要求**：`<<JAC_OBS>>…<<JAC_OBS_END>>` 之间的内容一律不进 `_broadcast` / `_reply_buf` / TTS。复用现有 `_shown_len` 裁剪机制。

### 12.6 麦克风设备切换

CHANGELOG 2026-08-15 午后：戴耳机时 macOS 自动把默认输入切到耳机麦，若耳机麦未授权/静音/增益低则采静音 → VAD 永判无人说话。

**要求**：主动层不新开音频采集，复用 omni 的 `_start_capture`（已打印设备名）。若将来需要独立录音，必须支持 `mic_index` 显式绑定。

### 12.7 表达式求值安全红线

`timing.formula` / `trigger` 条件来自用户对话与模型输出，**绝不能用 `eval()` / `exec()`**。必须 AST 白名单遍历（§5.11）。P3 验收④有对应注入测试。

### 12.8 阻塞约束

`OmniObservationSource` 的解析代码可能被 PyAudio 回调线程或 omni WebSocket 接收协程调用。**禁止在其中做阻塞 IO**（模型推理、网络请求、文件同步写）。所有重活通过 `EventBus.publish` 交给 `Service._loop` 单线程处理。EventBus 队列满时丢最旧、绝不阻塞生产者。

### 12.9 `shell=False`

`MacOSChannel` 调 `osascript` 必须用 `subprocess` 列表参数 + `shell=False`，防注入（与现有 `src/tools/shell.py` 的双重防护思路一致）。

---

## 13. 测试要求

新增 `tests/test_proactive_*.py`，纳入现有 `pytest.ini`（`tests/unit/` 已有先例）。

| 测试文件 | 必须覆盖 |
|---|---|
| `test_proactive_models.py` | 全部 dataclass 的 `to_dict` / `from_dict` 往返；缺字段容错默认值；非法枚举降级 |
| `test_proactive_observe_protocol.py` | `feed_chunk` 纯函数：单 delta 完整 / 跨 delta 切分 / JSON 非法 / 超长未闭合 / **协议文本不出现在 speakable_text** / 与 `<<CALL_QWEN>>` 共存 |
| `test_proactive_fusion.py` | 滞回：单次异常观察不切场景；连续 N 次一致才切；TTL 过期置 `is_stale` |
| `test_proactive_scheduler.py` | 三种 `misfire_policy` 各一用例（**monkeypatch 伪造 `last_tick`，不真睡眠**）；落盘 + 重启恢复；`reschedule` 返回过期时间时转 `skip`；`cancel_on` 取消 |
| `test_proactive_policy_dsl.py` | `all`/`any`/`not` 组合与嵌套；**注入攻击全部被拒**（`__import__` / `os.system` / 属性访问）；`validate_policy` 拒非法策略；变量缺失降级 |
| `test_proactive_policy_store.py` | 原子写 + `.bak` 恢复；版本不符抛异常；缺必填字段跳过并计数；文件权限 `0o600` |
| `test_proactive_notify.py` | 分级路由；去重键 + 冷却；免打扰降级；**断言 `mark_external_playback` 被调用**；VoiceboxChannel 失败降级 macOS 不静默丢弃 |
| `test_proactive_advisor.py` | 静默期不触发；回声期不触发；冷却生效；`sanitize_verdict` 剥离协议残留 / 拒超长 / 拒非法 level；**幻觉 verdict 绝不触达用户** |
| `test_proactive_regime.py` | lid 事件切换；无 lid 源时降级推断；`is_inference_only` 正确 |
| `test_proactive_location.py` | `haversine_m` 纯函数精度；进出半径事件；pyobjc 缺失时 `available()=False` |
| `test_proactive_service.py` | 启动/停止生命周期；退出无残留线程；某源不可用时其余源正常；传统模式不启动 |

**验收方式**：`pytest` 全绿（含既有回归 `tests/test_omni_echo_gate.py` 29 passed、`tests/test_omni_m2.py`、`tests/test_omni_m3_token.py`、`tests/unit/test_tools.py` 不得退化）。

---

## 14. 文档同步（AGENTS.md 硬性规定）

**每期收尾必须同步四类文档**：

| 文件 | 同步内容 |
|---|---|
| `README.md` | 双语（英文在前、中文在后）；主动服务层能力描述与「尚未实现」清单更新 |
| `AGENTS.md` | 「当前实现」新增主动服务层小节；「重要文件与目录」新增 `src/proactive/`；「已知限制」更新；「模型与资产」如涉及新依赖 |
| `CHANGELOG.md` | 追加用户可读改动条目，最新在最上方 |
| `codingLOG.md` | 更新差距状态。**本设计落地后需改的条目**：§1（主动介入从「部分解决」升级）、§2（工具集扩展）、§5（新增「无场景图/场景分类」已解决、「无定时器/调度器」已解决、「无 MCP/OpenClaw」仍未解决） |

**配套检查项**：
- `.gitignore`：新增 `~/.jac/proactive/`（用户目录，本就不在仓库内，但需确认 `temp/` 等运行时产物规则一致）
- `requirements.txt`：新增 pyobjc 两项
- `new_computer_download/` 一键安装脚本 + `READMEfirst.md`：同步新依赖与 macOS 定位授权步骤

⚠️ **`codinglog_by_awaqwq233/` 目录只由 bo s s 手动维护，实施 agent 严禁编辑或同步其内容。**

---

## 15. 开放问题（不阻塞 P0-P2）

| # | 问题 | 现状与处置 |
|---|---|---|
| Q1 | **蓝牙音频在离场场景的可靠性**：AirPods 可能自动切到 iPhone，此时 Mac 播报听不到 | bo s s 已选定「蓝牙音频播报 + 仅 macOS 通知中心」，按原样实现。**需知悉此失效模式**。将来兜底方案是 P6 的手机推送通道（ntfy/Bark） |
| Q2 | **`lid.py` 的开合盖检测可靠性**：IOKit 通知需额外 pyobjc 绑定 | P4 实施时按真机表现定；不稳则 `available()=False`，由 `FusionEngine` 降级为「定位静止 + 观察中断」联合推断（降级路径已在设计中） |
| Q3 | **合盖禁睡的系统设置** | `caffeinate` **做不到**（只挡 idle sleep，合盖是 clamshell sleep，另一条触发路径）；官方蛤壳模式必须外接显示器+键鼠；唯一无需外设的办法是 `sudo pmset -c disablesleep 1`（需 sudo、系统级）。**实施 agent 不代替 bo s s 执行任何 sudo 命令**，只在文档中说明。调度器已设计成睡不睡都正确 |
| Q4 | **合盖 + 禁睡 + 满载推理的散热** | 热量闷在闭合机身里，**放包里有物理过热风险**。这是安全层面而非耗电层面，需在文档与 GUI 提示中告知 bo s s |
| Q5 | **进程不跑就提醒不了** | bo s s 未选 launchd 常驻，故接受「只在 J.A.C. 运行时管用」。P6 可选增强 |
| Q6 | **知识来源本期无 web search** | bo s s 明确「以后再加」。**但知识库并非冷启动为空**——`MemoryProvider` + `TeachingProvider` 已能工作：对话教学中积累的事实直接落 memory store。`WebSearchProvider` 将来作为第三个 provider 插入 `KnowledgeService.PROVIDER_ORDER`，不改架构 |
| Q7 | **omni 是否稳定遵守观察协议** | 最大未知项。MiniCPM-o-4_5 对结构化带内协议的遵守度需 P1 真机验证。若不达标，备选方案是降低观察频率、简化 JSON 字段、或改用现有 `<<CALL_QWEN>>` 单向升级承载观察。**P1 必须先做小规模真机验证再全量实施** |

---

## 16. 批准记录

- bo s s 于 2026-09-12 确认混合方案方向（A 主干 + C 长尾旁路 + B 通知通道）
- bo s s 于 2026-09-12 追加需求：omni 实时感知为主、升级 qwen3.6-35b、agent 化、不在乎耗电与隐私、web search 后加
- bo s s 选定：仅 OMNI 模式、接受 pyobjc 定位授权、蓝牙音频 + macOS 通知、睡眠策略两者都做可切换
- bo s s 于 2026-09-12 纠正：**具体场景只是例子，不是设计目标** → 架构改为策略运行时创作（本文档 §0.2、§7、§9、§11.1）
- bo s s 于 2026-09-12 批准保存本设计

---

## 17. 交接说明

本设计已批准。下一步交由 `writing-plans` skill 拆解为可执行任务。

实施 agent 接手时请：
1. 按 §0.1 顺序读完前置文件
2. 严格遵守 §12 全部实施约束
3. 按 §11 分期推进，每期通过验收标准后再进下一期
4. 每期收尾执行 §14 文档同步
5. P1 开始编码前，先做 Q7 的小规模真机验证

---

内容由 AI 生成
