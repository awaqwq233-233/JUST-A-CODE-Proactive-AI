"""专用 Playwright 进程：仅服务任务授权来源，浏览器状态不混入用户默认配置。"""
import hashlib
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit
from uuid import uuid4

from playwright.sync_api import sync_playwright


def origin(url):
    """获取网络请求来源，其他协议返回不可授权值。"""
    parsed = urlsplit(url)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
        return ''
    host = parsed.hostname.encode('idna').decode('ascii')
    if ':' in host:
        host = '['+host+']'
    port = parsed.port
    return parsed.scheme + '://' + host + (':' + str(port) if port and port != {'http': 80, 'https': 443}.get(parsed.scheme) else '')


class BrowserWorker:
    """在单一线程拥有浏览器，快照编号只能在本进程内映射到当前页面。"""

    def __init__(self, config, playwright):
        """创建专用会话，禁用服务工作线程和跨授权来源的网络访问。"""
        self.config, self.snapshots, self.write_until, self.blocked = config, {}, 0, []
        self.context = playwright.chromium.launch_persistent_context(
            config['profile'], headless=config.get('headless', False), channel=config.get('channel'),
            accept_downloads=False, service_workers='block', viewport={'width': 1280, 'height': 800})
        self.context.set_default_timeout(5000)
        self.context.route('**/*', self.route)
        self.context.route_web_socket('**/*', self.close_socket)
        self.context.on('page', self.on_page)
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.on_page(self.page)

    def close_socket(self, socket):
        """拒绝绕过HTTP来源及提交确认的WebSocket连接。"""
        socket.close()

    def on_page(self, page):
        """对弹窗沿用同一网络边界，默认拒绝脚本对话框和下载。"""
        page.on('dialog', lambda dialog: dialog.dismiss())
        page.on('download', lambda download: download.cancel())

    def route(self, route):
        """程序强制来源及写请求确认；网页脚本不能直接取得联网白名单之外的权限。"""
        request = route.request
        permitted = origin(request.url) in self.config['origins']
        writing = request.method not in {'GET', 'HEAD', 'OPTIONS'}
        if not permitted or writing and time.monotonic() > self.write_until:
            self.blocked.append({'url': request.url[:500], 'method': request.method})
            route.abort('blockedbyclient')
        else:
            route.continue_()

    def observe(self):
        """提供有界页面文字和可见元素，动作前后重新核验元素描述及当前位置。"""
        if self.page.is_closed():
            raise ValueError('任务页面已关闭，请重新打开')
        identifier = uuid4().hex
        elements = self.page.locator('a,button,input,textarea,select,[role="button"],[role="textbox"],[contenteditable="true"]').evaluate_all('''els => els.filter(e => {
            const r=e.getBoundingClientRect(); return r.width>0 && r.height>0 && getComputedStyle(e).visibility!=='hidden';
        }).slice(0,80).map((e,i) => {
            e.setAttribute('data-jac-target',String(i));
            return {target:String(i),tag:e.tagName,type:e.type||e.getAttribute('type')||'',role:e.getAttribute('role')||'',
                name:(e.getAttribute('aria-label')||e.innerText||e.getAttribute('placeholder')||e.name||'').slice(0,160),
                href:e.tagName==='A'?e.href:'',value:e.type==='password'?'':String(e.value||'').slice(0,200),
                password:e.type==='password',disabled:!!e.disabled};
        })''')
        state = {'snapshot': identifier, 'url': self.page.url, 'title': self.page.title(), 'elements': elements,
                 'text': self.page.locator('body').inner_text()[:3500]}
        self.snapshots = {identifier: {'at': time.monotonic(), 'state': state}}
        return state

    def execute(self, request):
        """只接受固定操作，不向模型开放evaluate、任意选择器或浏览器调试端口。"""
        op, args = request['operation'], request['parameters']
        self.blocked = []
        if op == 'open':
            if set(args) != {'url'} or origin(args['url']) not in self.config['origins']:
                raise ValueError('网址不在本次来源授权内')
            self.page.goto(args['url'], wait_until='domcontentloaded', timeout=15000)
        elif op != 'observe':
            previous = self.snapshots.get(args.get('snapshot'))
            if not previous or time.monotonic()-previous['at'] > 300 or previous['state']['url'] != self.page.url:
                raise ValueError('页面快照已失效，请重新观察')
            target = next((e for e in previous['state']['elements'] if e['target'] == args.get('target')), None)
            if target is None or target['password']:
                raise ValueError('目标不存在或属于密码输入框，请手动登录')
            locator = self.page.locator('[data-jac-target="' + target['target'] + '"]')
            if locator.count() != 1 or not locator.is_visible():
                raise ValueError('目标已改变，请重新观察')
            actual = locator.evaluate('e => ({tag:e.tagName,name:(e.getAttribute("aria-label")||e.innerText||e.getAttribute("placeholder")||e.name||"").slice(0,160),type:e.type||e.getAttribute("type")||"",href:e.tagName==="A"?e.href:""})')
            if any(actual[k] != target[k] for k in actual):
                raise ValueError('目标内容已改变，请重新观察')
            self.write_until = time.monotonic()+10 if request.get('permit_write') else 0
            if op == 'click':
                locator.click()
            elif op == 'fill':
                if len(args['text']) > 8000:
                    raise ValueError('输入超过8000字')
                locator.fill(args['text'])
                if locator.input_value() != args['text']:
                    raise ValueError('输入值核验失败')
            elif op == 'select':
                locator.select_option(args['value'])
            elif op == 'press' and args['key'] in {'Tab', 'Shift+Tab', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Escape', 'Enter'}:
                locator.press(args['key'])
            elif op == 'scroll' and type(args['dy']) is int and abs(args['dy']) <= 1000:
                locator.hover()
                self.page.mouse.wheel(0, args['dy'])
            else:
                raise ValueError('未开放的浏览器动作或参数')
            self.page.wait_for_timeout(250)
            self.write_until = 0
        result = self.observe()
        result['blocked_requests'] = self.blocked[:10]
        return result


def main():
    """逐行JSON请求响应，退出即关闭本任务浏览器，不接管其他浏览器进程。"""
    config = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    with sync_playwright() as playwright:
        worker = BrowserWorker(config, playwright)
        try:
            for line in sys.stdin:
                try:
                    result = {'result': worker.execute(json.loads(line))}
                except Exception as error:
                    result = {'error': str(error)[:1000]}
                print(json.dumps(result, ensure_ascii=False), flush=True)
        finally:
            worker.context.close()


if __name__ == '__main__':
    main()
