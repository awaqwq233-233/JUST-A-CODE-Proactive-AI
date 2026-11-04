"""独立桌面组件的应用授权、快照约束、精确确认与取消控制。"""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import time

from .action_policy import scope, BLOCKED_APPS
from .action_tools import require_approval, project_ui
from .boundary import AgentRefused, atomic_json
from .browser_tools import RISK
from .profile import ROOT

SOURCE = ROOT / 'src/tools/desktop_controller.swift'
BUNDLE = ROOT / '.cache/actions/native/JACDesktopController.app'


def native_request(tools, operation, parameters):
    """通过LaunchServices保持辅助功能授权身份；取消标记在组件动作前检查。"""
    binary = BUNDLE / 'Contents/MacOS/desktop-controller'
    marker = BUNDLE.parent / 'desktop-controller.sha256'
    if not binary.is_file() or not marker.is_file() or marker.read_text().strip() != hashlib.sha256(SOURCE.read_bytes()).hexdigest():
        raise AgentRefused('桌面组件未安装或需要更新，请运行 --only actions')
    folder = tools.store.root / tools.task_id / 'desktop'
    folder.mkdir(mode=0o700, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='action-', dir=folder))
    request, output = directory / 'request.json', directory / 'response.json'
    atomic_json(request, {'operation': operation, 'parameters': parameters})
    process = subprocess.Popen(['/usr/bin/open', '-n', '-g', '-W', '-a', str(BUNDLE),
                                '--args', '--request', str(request), '--output', str(output)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic()+15
        while not output.is_file():
            tools.check()
            if time.monotonic() > deadline or process.poll() not in {None, 0}:
                raise AgentRefused('桌面组件异常退出或未及时返回；动作结果需核对，不能据此判定权限缺失')
            time.sleep(.05)
        tools.check()
        raw = output.read_bytes()
        if len(raw) > 128000:
            raise AgentRefused('桌面观察超过预算')
        result = json.loads(raw)
        if result.get('error'):
            if result.get('dispatched') is False:
                tools.record.pop('effect_started', None)
            raise AgentRefused(result['error'])
        return result
    finally:
        Path(str(output)+'.cancel').touch()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=2)


def execute_desktop(tools, operation, parameters):
    """动作只绑定用户授权应用和实际观察编号；普通文本不能指定任意屏幕坐标。"""
    if not isinstance(parameters, dict):
        raise AgentRefused('桌面参数须为对象')
    app = parameters.get('app')
    if app not in scope(tools.record).get('apps', []) or app in BLOCKED_APPS:
        raise AgentRefused('目标应用未授权或须使用专用工具')
    parameters = dict(parameters)
    offset = parameters.pop('offset', 0) if operation == 'observe' else 0
    project_ui({}, offset)
    if operation in {'observe', 'verify'}:
        parameters.pop('snapshot', None)  # 只读核验使用实时状态，不沿用旧快照执行事件。
    if operation == 'locate' and set(parameters) == {'app', 'description'}:
        from .screen_grounding import locate
        return locate(tools, app, parameters['description'])
    if operation == 'click_candidate' and set(parameters) == {'app', 'candidate'}:
        from .screen_grounding import click_candidate
        result = click_candidate(tools, app, parameters['candidate'])
        tools.record['desktop_observation'] = result
        tools.save()
        return project_ui(result)
    if operation == 'verify':
        if set(parameters) != {'app', 'expected'}:
            raise AgentRefused('verify参数仅需要app和expected结果文字，可附带snapshot；不接受target或其他字段')
        result = native_request(tools, 'observe', {'app': app})
        expected = parameters['expected']
        if not isinstance(expected, str) or not 1 <= len(expected) <= 500:
            raise AgentRefused('核验文本须为1–500字')
        if expected not in '\n'.join(x.get('name', '')+' '+x.get('value', '') for x in result['elements']):
            raise AgentRefused('当前窗口未找到要求的结果文字')
        tools.record['desktop_observation'] = result
        tools.record.setdefault('ui_unverified', {})['desktop'] = False
        tools.save()
        return {'verified': True, 'expected': expected, 'snapshot': result['snapshot']}
    fields = {'observe': {'app'}, 'click': {'app', 'snapshot', 'target'},
              'double_click': {'app', 'snapshot', 'target'}, 'fill': {'app', 'snapshot', 'target', 'text'},
              'press': {'app', 'snapshot', 'target', 'key'}, 'scroll': {'app', 'snapshot', 'target', 'dy'},
              'drag': {'app', 'snapshot', 'target', 'destination'}}
    if operation not in fields:
        raise AgentRefused('未开放的桌面操作')
    if set(parameters) != fields[operation]:
        raise AgentRefused('桌面'+operation+'必须包含字段：'+','.join(sorted(fields[operation]))+
                           '；缺少：'+','.join(sorted(fields[operation]-set(parameters)))+
                           '；多余：'+','.join(sorted(set(parameters)-fields[operation]))+
                           '。snapshot须原样复制最新observe结果，target须使用实际控件编号。')
    if operation != 'observe':
        previous = tools.record.get('desktop_observation', {})
        target = next((x for x in previous.get('elements', []) if x['target'] == parameters['target']), None)
        if previous.get('app') != app or previous.get('snapshot') != parameters['snapshot'] or target is None:
            raise AgentRefused('请先观察目标应用，旧快照不得用于其他窗口')
        if target.get('secure'):
            raise AgentRefused('密码控件由用户手动操作')
        if operation in {'click', 'double_click', 'press', 'drag'} and (
                operation in {'drag', 'double_click'} or parameters.get('key') == 'Enter'
                or RISK.search(target.get('name', '') + target.get('description', ''))):
            require_approval(tools, {'tool': 'desktop', 'operation': operation, 'parameters': parameters},
                             '应用：' + app + '\n窗口：' + previous.get('title', '') + '\n操作：' + operation + '\n目标：' + target.get('name', ''))
    if operation != 'observe':
        tools.record.setdefault('ui_unverified', {})['desktop'] = True
        tools.record['effect_started'] = True
        tools.save()
    result = native_request(tools, operation, parameters)
    tools.record['desktop_observation'] = result
    tools.save()
    return project_ui(result, offset)
