/** 生产任务插件：只加载本次授权 schema，完整模型回合经 Python 核验后才释放。 */
import { readFileSync } from 'node:fs';
export const name = 'jac-tools';
export const inject = ['tools', 'llm'];

/** 带任务凭据访问本机边界；不直接操作系统、文件或公网。 */
async function request(name, args, id, session, signal) {
  const response = await fetch(`${process.env.JAC_TOOL_URL}/tool`, {
    method: 'POST', signal,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${process.env.JAC_TOOL_TOKEN}` },
    body: JSON.stringify({ name, args, call_id: id, session_id: session }),
  });
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || 'JAC request refused');
  return value;
}

/** 保留工具文字结果，不把它变成新的控制指令。 */
function render(_args, text) { return [{ type: 'text', text }]; }

/** 安装精确工具及有界缓冲，阻止截断、重复或未授权批次提前产生实际操作。 */
export function apply(ctx) {
  const config = JSON.parse(readFileSync(process.env.JAC_VOICE_CONFIG, 'utf8'));
  const allowed = config.tools.map(tool => tool.function.name);
  let proposalComplete = false;
  /** 单动作提议登记后暂停 SDK 回合，由外层核验和执行，不再请求模型重复提议。 */
  ctx.on('agent/pre-step', async (_payload, next) => {
    if (proposalComplete) return { kind: 'reject' };
    return next();
  });
  for (const tool of config.tools) {
    const spec = tool.function;
    ctx.tools.register({ ...spec, output: { schema: { type: 'string' }, render },
      /** 执行只转交预先批准的调用 ID，由 Python 持有实际工具权限。 */
      async execute(args, exec) {
        const result = await request(spec.name, args, exec.callId, exec.agent.id, exec.signal);
        if (config.proposal_only) proposalComplete = true;
        if (spec.name === config.stop_tool && JSON.parse(result).status === 'staged') proposalComplete = true;
        return result;
      },
    });
  }
  ctx.tools.guard(exec => allowed.includes(exec.name) ? undefined : 'JAC unauthorized tool');
  let steps = 0, tokens = 0, bytes = 0;
  /** 缓冲整个流，校验 finish 与整批参数后才让 Harness 执行任何工具。 */
  ctx.on('llm/stream', async function* (options, next) {
    if (options.provider !== 'jac-local' || options.model !== 'qwen/qwen3.6-35b-a3b'
        || options.reasoningEffort !== 'off' || options.maxTokens !== 768
        || ++steps > config.max_steps) throw new Error('JAC model contract or step budget');
    if ((options.tools || []).some(tool => !allowed.includes(tool.name)))
      throw new Error('JAC unexpected schema');
    const input = JSON.stringify({ messages: options.messages, tools: options.tools, system: options.system });
    // 独立任务按文字估算容量，最终仍受模型实际上下文限制；旧语音域预算保持。
    const estimate = config.domain === 'assistant'
      ? Math.ceil(input.replace(/[^\x00-\x7f]/g, '').length / 2) + (input.match(/[^\x00-\x7f]/g) || []).length * 2
      : Buffer.byteLength(input);
    if (estimate > Number(process.env.JAC_CONTEXT_LIMIT) - 1536) throw new Error('JAC context budget');
    const chunks = [], calls = [];
    let finish;
    for await (const chunk of next()) {
      if ((chunk.type === 'reasoning-delta' && chunk.text)
          || (chunk.type === 'block-end' && chunk.block.type === 'reasoning')
          || (chunk.type === 'usage' && chunk.usage.reasoningTokens)) throw new Error('JAC reasoning enabled');
      if (chunk.type === 'block-end' && chunk.block.type === 'tool-call') {
        calls.push({ id: chunk.block.id, name: chunk.block.name, arguments: JSON.parse(chunk.block.arguments) });
      }
      if (chunk.type === 'usage') {
        tokens += chunk.usage.totalTokens ?? (chunk.usage.inputTokens + chunk.usage.outputTokens
          + (chunk.usage.cacheReadTokens || 0) + (chunk.usage.cacheWriteTokens || 0));
        if (tokens > (config.domain === 'assistant' ? 64000 : 24000)) throw new Error('JAC token budget');
      }
      bytes += Buffer.byteLength(JSON.stringify(chunk));
      if (bytes > 262144) throw new Error('JAC output budget');
      if (finish) throw new Error('JAC data after finish');
      if (chunk.type === 'finish') finish = chunk.reason.kind;
      chunks.push(chunk);
    }
    if (finish !== (calls.length ? 'tool-calls' : 'stop')) throw new Error('JAC incomplete model turn');
    await request('__validate_batch__', { calls }, `batch-${steps}`, config.session_id, options.signal);
    yield* chunks;
  });
}
