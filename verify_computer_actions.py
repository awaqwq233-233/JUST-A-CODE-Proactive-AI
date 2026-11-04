#!/usr/bin/env python3
"""使用合成窗口、网页和代码验证电脑操作，不读取实际文档或聊天。"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from uuid import uuid4

from src.brain.agent.task_store import TaskStore
from src.brain.agent.durable import DurableTools


def make_tools(root, task, actions):
    """每项验证使用独立任务状态和明确授权范围。"""
    store = TaskStore(root / 'tasks')
    record = store.create(task, {'actions': actions})
    return DurableTools(record, store, None)


def browser_probe(root):
    """真实浏览器操作本机合成页面，并核验输入、点击和最终DOM文字。"""
    from src.brain.agent.browser_tools import execute_browser, close_browser
    class Handler(BaseHTTPRequestHandler):
        """只提供内存测试页面，不服务磁盘上的任何文件。"""
        def do_GET(self):
            """返回有明确语义控件和可核对结果的UTF-8页面。"""
            data = ('<!doctype html><meta charset="utf-8"><title>JAC测试</title>'
                    '<label>测试输入<input aria-label="测试输入"></label>'
                    '<button type="button" onclick="document.getElementById(\'result\').textContent=document.querySelector(\'input\').value">更新预览</button>'
                    '<p id="result">等待更新</p>').encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            """测试报告仅保存断言，不记录浏览器请求日志。"""
            return
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}'
    tools = make_tools(root, '在合成页面输入中文并更新预览', {'origins': [url]})
    try:
        state = execute_browser(tools, 'open', {'url': url})
        field = next(x for x in state['elements'] if x['tag'] == 'INPUT')
        state = execute_browser(tools, 'fill', {'snapshot': state['snapshot'], 'target': field['target'], 'text': 'boss，网页输入成功'})
        button = next(x for x in state['elements'] if x['name'] == '更新预览')
        state = execute_browser(tools, 'click', {'snapshot': state['snapshot'], 'target': button['target']})
        assert 'boss，网页输入成功' in state['text']
        return {'passed': True, 'input_and_click_verified': True, 'blocked_requests': state.get('blocked_requests', [])}
    finally:
        close_browser(tools.task_id)
        server.shutdown()
        server.server_close()


def desktop_probe(root):
    """仅操作独立验收应用，文字输入与按钮结果都由AX回读确认。"""
    from src.brain.agent.desktop_tools import execute_desktop
    app = 'local.jac.desktop-fixture'
    tools = make_tools(root, '在独立桌面测试窗口输入中文并更新预览', {'apps': [app]})
    state = execute_desktop(tools, 'observe', {'app': app})
    field = next(x for x in state['elements'] if x['role'] == 'AXTextField')
    state = execute_desktop(tools, 'fill', {'app': app, 'snapshot': state['snapshot'], 'target': field['target'], 'text': 'boss，中文输入验证成功。'})
    button = next(x for x in state['elements'] if x.get('name') == '更新预览')
    state = execute_desktop(tools, 'click', {'app': app, 'snapshot': state['snapshot'], 'target': button['target']})
    assert any('已确认：boss，中文输入验证成功。' in x.get('value', '') for x in state['elements'])
    return {'passed': True, 'input_and_click_verified': True}


def sandbox_probe(root):
    """真实容器核验Python/Node、网络隔离、宿主不可见及非零退出。"""
    from src.brain.agent.sandbox_tools import execute_code
    work = root / 'workspace'
    work.mkdir()
    (work / 'check.py').write_text('''import os, socket
assert not os.path.exists('/Users')
try:
    socket.create_connection(('1.1.1.1', 443), timeout=1)
except OSError:
    print('network_disabled')
else:
    raise AssertionError('network enabled')
print('中文执行通过', 123 + 456)
''', encoding='utf-8')
    (work / 'check.js').write_text("console.log('Node中文执行通过', 12+34);", encoding='utf-8')
    (work / 'failure.py').write_text("raise RuntimeError('expected failure')\n", encoding='utf-8')
    tools = make_tools(root, '验证隔离运行环境', {'workspace': str(work), 'runtimes': ['python', 'node']})
    python = execute_code(tools, 'python', 'check.py', [])
    node = execute_code(tools, 'node', 'check.js', [])
    failure = execute_code(tools, 'python', 'failure.py', [])
    assert python['exit_code'] == 0 and '579' in python['stdout'] and 'network_disabled' in python['stdout']
    assert node['exit_code'] == 0 and '46' in node['stdout']
    assert failure['exit_code'] != 0 and 'expected failure' in failure['stderr']
    return {'passed': True, 'python': python, 'node': node, 'failure_exit_code': failure['exit_code']}


def vision_probe(root):
    """只对合成按钮核对视觉候选，确认前不点击，结果与AX实际矩形交叉校验。"""
    from src.brain.agent.desktop_tools import execute_desktop
    from src.brain.agent.action_tools import ApprovalRequired
    app = 'local.jac.desktop-fixture'
    tools = make_tools(root, '验证合成测试按钮截图点击', {'apps': [app]})
    state = execute_desktop(tools, 'observe', {'app': app})
    field = next(x for x in state['elements'] if x['role'] == 'AXTextField')
    marker = '截图点击已验证'
    state = execute_desktop(tools, 'fill', {'app': app, 'snapshot': state['snapshot'], 'target': field['target'], 'text': marker})
    state = execute_desktop(tools, 'press', {'app': app, 'snapshot': state['snapshot'], 'target': field['target'], 'key': 'Tab'})
    button = next(x for x in tools.record['desktop_observation']['elements'] if x['name'] == '更新预览')
    result = execute_desktop(tools, 'locate', {'app': app, 'description': '更新预览按钮'})
    candidate = tools.record['screen_candidate']
    frame = candidate['frame']
    x = frame[0]+candidate['x']/candidate['width']*frame[2]
    y = frame[1]+candidate['y']/candidate['height']*frame[3]
    rect = button['rect']
    assert rect[0] <= x <= rect[0]+rect[2] and rect[1] <= y <= rect[1]+rect[3], result
    parameters = {'app': app, 'candidate': result['candidate']}
    try:
        execute_desktop(tools, 'click_candidate', parameters)
    except ApprovalRequired:
        pass
    else:
        raise AssertionError('截图点击未经确认执行')
    # 本验收已明确授权这个合成按钮；生产确认仍只能由GUI/CLI用户发出。
    tools.record['approved_action'] = {'digest': tools.record['action_request']['digest'], 'expires_at': time.time()+30}
    execute_desktop(tools, 'click_candidate', parameters)
    verified = execute_desktop(tools, 'verify', {'app': app, 'expected': '已确认：'+marker})
    return {'passed': True, 'candidate_inside_fixture_button': True, 'confirmation_enforced': True, 'verification': verified}


def model_probe(root):
    """真实Qwen通过生产Harness修改测试脚本、运行容器并交付报告。"""
    from src.brain.agent.assistant import AssistantTaskRunner
    from src.brain.agent.durable import DurableTaskRunner
    work = root / 'model-work'
    work.mkdir()
    (work / 'check.py').write_text('def add(a, b):\n    """返回两个数的和。"""\n    return a - b\n\nassert add(2, 4) == 6\nprint("验证通过：6")\n', encoding='utf-8')
    store = TaskStore(root / 'model-tasks')
    record = store.create('修复工作目录check.py中的加法错误，保留函数注释。先读取、暂存修改、提交，再用python运行check.py验证。成功后给出简短报告。',
                          {'actions': {'workspace': str(work), 'edit': True, 'runtimes': ['python']}})
    runner = DurableTaskRunner(store, AssistantTaskRunner(output_dir=root / 'reports'))
    for _ in range(6):
        runner.run_stage(record)
        if record['status'] != 'queued':
            break
    result = {'status': record['status'], 'message': record.get('message'), 'report': record.get('report'),
              'tools': [{k: e.get(k) for k in ('name', 'status', 'output')} for e in record.get('checkpoint', {}).get('trace', [])]}
    assert record['status'] == 'completed', result
    assert any(x['exit_code'] == 0 for x in record.get('executions', [])), result
    result['passed'] = True
    return result


def main():
    """按显式选项启动验收，失败也保存报告且不宣称未执行项目通过。"""
    parser = argparse.ArgumentParser(description=__doc__)
    for item in ('browser', 'desktop', 'sandbox', 'model', 'vision'):
        parser.add_argument('--'+item, action='store_true')
    args = parser.parse_args()
    root = Path('.cache/actions/verification') / uuid4().hex
    root.mkdir(parents=True)
    results = {}
    for name, probe in [('browser', browser_probe), ('desktop', desktop_probe), ('sandbox', sandbox_probe), ('model', model_probe), ('vision', vision_probe)]:
        if getattr(args, name):
            try:
                results[name] = probe(root)
            except Exception as error:
                results[name] = {'passed': False, 'error': str(error)}
            (root / 'result.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
            print(name, json.dumps(results[name], ensure_ascii=False), flush=True)
    print('报告：', root / 'result.json')
    if not results or any(not value['passed'] for value in results.values()):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
