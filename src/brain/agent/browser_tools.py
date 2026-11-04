"""浏览器进程生命周期与任务级授权；界面动作使用近期观察编号。"""
import json
import os
from pathlib import Path
import re
import select
import signal
import subprocess
import threading
import time

from .action_policy import scope, origin
from .action_tools import require_approval, project_ui
from .boundary import AgentRefused, atomic_json
from .profile import ROOT

SESSIONS = {}
LOCK = threading.RLock()
RISK = re.compile(r'发送|提交|付款|支付|购买|删除|移除|安装|授权|发布|转账|send|submit|pay|buy|delete|remove|install|publish|authorize', re.I)


def close_browser(task_id=None):
    """关闭指定任务或全部自有进程组；不终止用户自行启动的浏览器。"""
    with LOCK:
        for key in list(SESSIONS):
            if task_id is not None and key != task_id:
                continue
            process = SESSIONS.pop(key)
            try:
                process.stdin.close()
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=2)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            process.stdout.close()


def session(tools):
    """使用锁定独立环境启动专用浏览器配置，不读取默认浏览器Cookies。"""
    with LOCK:
        process = SESSIONS.get(tools.task_id)
        if process and process.poll() is None:
            return process
        python = ROOT / '.cache/actions/venv/bin/python'
        if not python.is_file():
            raise AgentRefused('浏览器环境未安装，请运行 --only actions')
        folder = tools.store.root / tools.task_id / 'browser'
        folder.mkdir(mode=0o700, exist_ok=True)
        config = folder / 'config.json'
        browser_config = json.loads((ROOT / '.cache/actions/browser.json').read_text(encoding='utf-8'))
        atomic_json(config, {'profile': str(folder / 'profile'), 'origins': scope(tools.record)['origins'],
                             'channel': browser_config.get('channel')})
        env = {'PATH': os.environ.get('PATH', ''), 'PYTHONPATH': str(ROOT), 'PYTHONUTF8': '1',
               'PLAYWRIGHT_BROWSERS_PATH': str(ROOT / '.cache/actions/browsers')}
        process = subprocess.Popen([str(python), '-m', 'src.brain.agent.browser_worker', str(config)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   env=env, start_new_session=True)
        SESSIONS[tools.task_id] = process
        return process


def request(tools, payload):
    """有界等待浏览器响应，取消时立即回收本任务进程组。"""
    process = session(tools)
    process.stdin.write(json.dumps(payload, ensure_ascii=False).encode() + b'\n')
    process.stdin.flush()
    data, deadline = bytearray(), time.monotonic()+25
    try:
        while b'\n' not in data:
            tools.check()
            if time.monotonic() > deadline or process.poll() is not None:
                raise AgentRefused('浏览器未及时返回；本次动作结果需核对')
            if select.select([process.stdout], [], [], .1)[0]:
                chunk = os.read(process.stdout.fileno(), 8192)
                if not chunk:
                    raise AgentRefused('浏览器进程已退出，请检查安装或重新建立任务')
                data.extend(chunk)
                if len(data) > 128000:
                    raise AgentRefused('浏览器观察结果超过预算')
        result = json.loads(data)
        if result.get('error'):
            raise AgentRefused(result['error'])
        return result['result']
    except BaseException:
        close_browser(tools.task_id)
        raise


def execute_browser(tools, operation, parameters):
    """核验授权目标、准确快照及危险动作确认，随后交给固定浏览器实现。"""
    if not scope(tools.record).get('origins') or not isinstance(parameters, dict):
        raise AgentRefused('网站操作未授权或参数不正确')
    parameters = dict(parameters)
    offset = parameters.pop('offset', 0) if operation == 'observe' else 0
    project_ui({}, offset)
    if operation in {'observe', 'verify'}:
        parameters.pop('snapshot', None)  # 只读操作始终重新观察，旧编号不能指定动作目标。
    if operation == 'verify':
        if set(parameters) != {'expected'}:
            raise AgentRefused('verify参数仅需要expected结果文字，可附带snapshot；不接受target或其他字段')
        expected = parameters['expected']
        if not isinstance(expected, str) or not 1 <= len(expected) <= 500:
            raise AgentRefused('核验文本须为1–500字')
        state = request(tools, {'operation': 'observe', 'parameters': {}})
        if expected not in state['text']:
            raise AgentRefused('当前页面未找到要求的结果文字')
        tools.record['browser_observation'] = state
        tools.record.setdefault('ui_unverified', {})['browser'] = False
        tools.save()
        return {'verified': True, 'expected': expected, 'url': state['url']}
    fields = {'open': {'url'}, 'observe': set(), 'click': {'snapshot', 'target'},
              'fill': {'snapshot', 'target', 'text'}, 'select': {'snapshot', 'target', 'value'},
              'press': {'snapshot', 'target', 'key'}, 'scroll': {'snapshot', 'target', 'dy'}}
    if operation not in fields:
        raise AgentRefused('未开放的浏览器操作')
    if set(parameters) != fields[operation]:
        raise AgentRefused('浏览器'+operation+'必须包含字段：'+','.join(sorted(fields[operation]))+
                           '；缺少：'+','.join(sorted(fields[operation]-set(parameters)))+
                           '；多余：'+','.join(sorted(set(parameters)-fields[operation]))+
                           '。snapshot须原样复制最新观察结果，target须使用实际控件编号。')
    permit_write = False
    if operation == 'open' and origin(parameters['url']) not in scope(tools.record)['origins']:
        raise AgentRefused('网站来源未授权')
    if operation not in {'open', 'observe'}:
        observation = tools.record.get('browser_observation', {})
        target = next((item for item in observation.get('elements', []) if item['target'] == parameters['target']), None)
        if observation.get('snapshot') != parameters['snapshot'] or target is None:
            raise AgentRefused('须先观察页面，使用当前目标编号')
        if target.get('password'):
            raise AgentRefused('密码由用户在专用浏览器手动输入')
        risky = operation == 'press' and parameters['key'] == 'Enter' or operation == 'click' and (
            target.get('type') == 'submit' or RISK.search(target.get('name', '') + target.get('href', '')))
        if risky:
            action = {'tool': 'browser', 'operation': operation, 'parameters': parameters}
            require_approval(tools, action, '网站：' + observation['url'] + '\n操作：' + operation + '\n目标：' + target['name'])
            permit_write = True
    if operation != 'observe':
        tools.record.setdefault('ui_unverified', {})['browser'] = True
        tools.record['effect_started'] = True
        tools.save()
    result = request(tools, {'operation': operation, 'parameters': parameters, 'permit_write': permit_write})
    tools.record['browser_observation'] = result
    tools.save()
    if result.get('blocked_requests'):
        result['scope_note'] = '存在被权限边界阻止的网络请求；操作结果需按页面状态核对，不能宣称提交成功'
    projected = project_ui(result, offset)
    if result.get('scope_note'):
        projected['scope_note'] = result['scope_note']
    return projected
