"""J.A.C. MiniCPM-o-4_5 全双工模块。

把本地 llama.cpp-omni 服务封装成 SDK：
  - OmniClient        全双工 WebSocket 客户端（摄像头 + 麦克风实时推流，
                       接收 omni 文本/语音，声纹克隆，回调广播，M2 令牌检测/静音）
  - OmniServerLauncher  定位 / 启动 llama-omni-server，TCP 就绪探测
  - OmniCallbacks     事件回调（状态 / 文本增量 / 语音块 / 听写 / 升级令牌）
  - SYSTEM_PROMPT     含 <<CALL_QWEN>> 升级令牌约定的系统提示（见 prompts.py）
  - EscalationRouter  M2 升级路由：把令牌任务交给 qwen+tools 执行（大脑 + 手）
  - parse_call_qwen   从文本解析 <<CALL_QWEN>>{task} 令牌（纯函数，便于测试）

设计要点：omni 是「耳朵 + 眼睛 + 嘴巴」，qwen+tools 是「大脑 + 手」，
二者职责分离（详见项目根 AGENTS.md / 升级计划）。
"""
from .client import OmniClient, OmniCallbacks
from .server_launcher import OmniServerLauncher
from .prompts import SYSTEM_PROMPT, build_omni_system_prompt, TOOL_SYSTEM_PROMPT
from .router import EscalationRouter, parse_call_qwen

__all__ = [
    "OmniClient",
    "OmniCallbacks",
    "OmniServerLauncher",
    "SYSTEM_PROMPT",
    "TOOL_SYSTEM_PROMPT",
    "build_omni_system_prompt",
    "EscalationRouter",
    "parse_call_qwen",
]
