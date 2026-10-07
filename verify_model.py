"""独立检查本机 Qwen 精确实例；Gateway 听看说仍需要其独立后端。"""

from src.brain.lm_studio import LMStudioClient, BrainError


def verify():
    """验证已加载目标与零思考回答，不回退 Ollama 或清单首项。"""
    try:
        client = LMStudioClient()
        print(f'[模型] {client.require_loaded_model()}')
        reply = client.complete([{'role': 'user', 'content': '用一句简体中文介绍你自己。'}], max_tokens=256)
        print(reply.content)
        print('[通过] Qwen 独立大脑可用；语音入口的升级仍待后续接入。')
        return 0
    except BrainError as exc:
        print(f'[未通过] {exc}')
        return 2


if __name__ == '__main__':
    raise SystemExit(verify())
