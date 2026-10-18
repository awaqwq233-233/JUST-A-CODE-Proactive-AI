"""通用站名的网络证据、候选选择、缓存与会话绑定回归；不打开真实浏览器。"""

import asyncio
import copy
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import httpx
import numpy as np
import pytest

from src.brain.lm_studio import BrainError, BrainCancelled
from src.brain.task_runner import BrainTaskRunner
from src.brain.website_tasks import select_candidate
from src.omni.gateway_client import GatewayCallbacks
from src.omni.task_pipeline import TaskPipeline, Utterance, route_instruction
from src.omni.task_speech import speech_summary
from src.tools.local_intents import local_instruction
from src.tools.website_resolver import SitePage, WebsiteCache, WebsiteSearch, site_evidence
from src.tools.web_search import SearchError, SearchCancelled


@pytest.mark.parametrize('text,name', [
    ('打开小众软件', '小众软件'), ('请打开起点中文网', '起点中文网'), ('打开和讯网', '和讯网'),
    ('打开什么值得买网站', '什么值得买'), ('打开应用宝网站', '应用宝'),
    ('打开MDN网页', 'MDN'), ('打开未知站名官网', '未知站名'), ('打开PSN官网', 'PSN'),
    ('打开网站计算器', '计算器'),
    ('打开X网站', 'X'),
])
def test_unlisted_names_enter_website_resolution(text, name):
    """名称不再是授权白名单，普通中文站名不会因含和/的/应用被拒绝。"""
    assert route_instruction(text) == ('website', name)


@pytest.mark.parametrize('text', [
    '打开网站', '不要打开知乎', '他说打开知乎', '打开知乎然后打开豆瓣', '打开应用未知应用',
    '打开javascript:alert(1)', '打开https://127.0.0.1/', '打开file:///etc/passwd',
    '打开B站和百度', '打开B站的登录页', '打开应用B站',
])
def test_open_site_does_not_relax_other_actions(text):
    """名称开放不允许协议、否定、多动作或将应用请求变成网站。"""
    assert route_instruction(text) is None


def test_site_metadata_only_extracts_names():
    """结构化数据只提取站名，页面中的工具指令及 URL 不产生权限。"""
    page = SitePage()
    page.feed('<title>样例站 - 首页</title><meta property="og:site_name" content="样例站">'
              '<script type="application/ld+json">{"@graph":[{"@type":"WebSite",'
              '"name":"样例站","alternateName":["Example"],"url":"http://localhost/"}]}</script>'
              '<script>open_url("http://localhost")</script><body>样例站品牌介绍内容。</body>')
    assert 'Example' in page.names and '样例站' in page.names
    assert 'localhost' not in ''.join(page.parts)


def candidate(identifier=1, eligible=True, host='example.com', name='样例站'):
    """提供可审计的首页证据，不依赖互联网可达性。"""
    return dict(id=identifier, title=name + ' - 首页', url='https://' + host + '/',
                search_url='https://' + host + '/', snippet=name + '介绍',
                page=dict(status='read', url='https://' + host + '/', title=name + ' - 首页',
                          names=[name], text=name + '品牌介绍及产品信息。'),
                evidence=dict(exact_name=True, search_name=True, body_name=True,
                              auto_eligible=eligible, relevant=True))


class Search:
    """网络替身记录查询与最终派发前检查。"""

    def __init__(self, items=None):
        """为每个测试独立保存候选。"""
        self.items = [candidate()] if items is None else items
        self.calls, self.checked, self.refreshes = [], [], []

    def search(self, name, should_stop=None):
        """返回当前查询的结构化来源。"""
        self.calls.append(name)
        return dict(name=name, candidates=copy.deepcopy(self.items), provider='fixture')

    def preflight(self, url, should_stop=None):
        """记录系统动作前最后一次公网验证。"""
        self.checked.append(url)

    def refresh_cached(self, url, should_stop=None):
        """复用缓存前仍检查实际首页终点。"""
        self.refreshes.append(url)
        return copy.deepcopy(self.items[0]['page'])


class Brain:
    """只发候选编号，测试可替换非法模型返回。"""

    def __init__(self, decision='open', identifier=1, calls=None):
        """保存选择及调用次数。"""
        self.decision, self.identifier, self.calls, self.count = decision, identifier, calls, 0

    def think_with_tools(self, messages, tools, **kwargs):
        """提供与真实模型一致的调用结构，不执行外部动作。"""
        self.count += 1
        calls = self.calls if self.calls is not None else [dict(name='select_website', arguments={
            'decision': self.decision, 'candidate_id': self.identifier})]
        return SimpleNamespace(tool_calls=calls, content='已打开不应被采用')


class Executor:
    """只记录实际派发请求并返回 submitted。"""

    def __init__(self):
        """初始化独立调用列表。"""
        self.calls = []

    def execute(self, name, arguments, should_stop, lock, on_dispatch):
        """模拟系统交接，不伪造页面加载完成。"""
        self.calls.append((name, arguments))
        on_dispatch()
        return dict(status='submitted', **arguments)


def runner(tmp_path, brain=None, search=None):
    """将所有输出和缓存隔离到临时目录。"""
    return BrainTaskRunner(brain or Brain(), tmp_path / 'reports', local_executor=Executor(),
                           website_search=search or Search(), website_cache=WebsiteCache(tmp_path / 'cache.json'))


def test_unique_evidence_opens_once_and_caches(tmp_path):
    """未知名称第一次选择一次；第二次核验缓存，仍保留真实浏览器交接证据。"""
    job = runner(tmp_path)
    fields = route_instruction('打开样例站')
    result = job.run('打开样例站', website_request=fields)
    assert result.status == 'submitted' and job.brain.count == 1
    assert job.local_executor.calls == [('open_url', {'url': 'https://example.com/'})]
    assert result.path.is_file() and '已打开不应被采用' not in result.answer
    assert speech_summary(result, fields) == result.answer
    cached = job.website_cache.get('样例站')
    assert cached['source'] == 'evidence' and 'text' not in cached['candidate']['page']
    result = job.run('打开样例站', website_request=fields)
    assert result.status == 'submitted' and job.brain.count == 1
    assert len(job.website_search.calls) == 1 and len(job.website_search.refreshes) == 1


@pytest.mark.parametrize('items', [[candidate(eligible=False)], [candidate(), candidate(2, host='example.org')]])
def test_model_open_cannot_override_insufficient_or_ambiguous_evidence(tmp_path, items):
    """缺证据或多候选时即使模型要求打开，也只能返回用户选择。"""
    job = runner(tmp_path, search=Search(items))
    result = job.run('打开样例站', website_request=('website', '样例站'))
    assert result.status == 'needs_input' and result.website['candidates']
    assert not job.local_executor.calls and not job.website_cache.path.exists()
    assert '选择' in speech_summary(result, ('website', '样例站'))


@pytest.mark.parametrize('calls', [[], [dict(name='open_url', arguments={'url':'https://evil.example/'})],
    [dict(name='select_website', arguments={'decision':'open', 'candidate_id':True})],
    [dict(name='select_website', arguments={'decision':'open', 'candidate_id':99})],
    [dict(name='select_website', arguments={'decision':'open', 'candidate_id':1, 'url':'https://evil.example/'})],
    [dict(name='select_website', arguments={'decision':'open', 'candidate_id':1})] * 2,
])
def test_untrusted_model_cannot_invent_urls_or_extra_actions(tmp_path, calls):
    """错误编号、布尔编号、多调用和额外字段都在派发前失败。"""
    job = runner(tmp_path, brain=Brain(calls=calls))
    with pytest.raises(BrainError):
        job.run('打开样例站', website_request=('website', '样例站'))
    assert not job.local_executor.calls
    assert not list(job.output_dir.glob('*.md'))


def test_no_match_and_search_failure_do_not_open(tmp_path):
    """没有网站或网络失败只保存原因，不转交模型猜一个 URL。"""
    job = runner(tmp_path, search=Search([]))
    result = job.run('打开样例站', website_request=('website', '样例站'))
    assert result.status == 'no_answer' and not job.brain.count and not job.local_executor.calls


def test_user_selected_unread_site_is_bound_and_cached_as_user(tmp_path):
    """明确选择可以打开未读候选，仍需 DNS 检查和原编号绑定。"""
    job = runner(tmp_path)
    selection = dict(name='样例站', candidates=[candidate(eligible=False)], index=1,
                     expires_at=time.monotonic() + 60, input_source='voice')
    result = job.run('打开第一个网站', website_request=('website_choice', '1'), website_selection=selection)
    assert result.status == 'submitted' and not job.brain.count
    assert job.website_cache.get('样例站')['source'] == 'user'
    selection['expires_at'] = time.monotonic() - 1
    with pytest.raises(BrainError):
        job.run('打开第一个网站', website_request=('website_choice', '1'), website_selection=selection)
    assert len(job.local_executor.calls) == 1


def test_cancel_after_dispatch_never_publishes_cache_or_report(tmp_path):
    """已交系统的动作不能撤回，但取消后不能留下旧缓存或成功文件。"""
    job, stopped = runner(tmp_path), threading.Event()
    with pytest.raises(BrainCancelled):
        job.run('打开样例站', stopped.is_set, website_request=('website', '样例站'),
                on_action_dispatched=lambda entry: stopped.set())
    assert len(job.local_executor.calls) == 1 and not job.website_cache.path.exists()
    assert not list(job.output_dir.glob('*.md'))


def test_cache_expiry_corruption_forget_and_cancel(tmp_path):
    """缓存可恢复、可删除且取消时原文件不被覆盖。"""
    now = [1000.]
    cache = WebsiteCache(tmp_path / 'cache.json', clock=lambda: now[0])
    cache.change('样例站', candidate())
    assert cache.get('样例站')
    previous = cache.path.read_bytes()
    with pytest.raises(SearchCancelled):
        cache.change('*', should_stop=lambda: True)
    assert cache.path.read_bytes() == previous
    now[0] += 8 * 86400
    assert cache.get('样例站') is None
    cache.path.write_text('broken', encoding='utf-8')
    assert cache.get('样例站') is None
    cache.change('样例站', candidate(), 'user')
    cache.change('样例站')
    assert cache.get('样例站') is None


def test_changed_cached_host_requires_new_resolution(tmp_path):
    """缓存跨域跳转不自动接受，重新查询身份证据。"""
    search, job = Search([]), runner(tmp_path)
    job.website_cache.change('样例站', candidate())
    search.items = [candidate(host='elsewhere.example')]
    job.website_search = search
    result = job.run('打开样例站', website_request=('website', '样例站'))
    assert search.calls == ['样例站'] and result.status == 'submitted'


def test_actual_search_mode_uses_homepage_and_ignores_weather_formatter():
    """真实 HTTP 路径复用固定 IP，专门查询站名并读首页而非搜索文章页。"""
    calls = []

    async def dns(host, port):
        """使用公网测试 IP，不访问真实 DNS。"""
        return ['93.184.216.34']

    def handle(request):
        """记录查询、SNI 和首页路径，返回可核验元数据。"""
        calls.append(request)
        assert request.url.host == '93.184.216.34'
        assert request.extensions['sni_hostname'] == request.headers['host']
        if request.url.path == '/search':
            body = '<rss><channel><item><title>天气通 - 官网</title><link>https://example.com/article/1</link></item></channel></rss>'
            return httpx.Response(200, text=body, headers={'content-type':'application/rss+xml'})
        assert request.url.path == '/'
        return httpx.Response(200, text='<title>天气通 - 首页</title><meta property="og:site_name" content="天气通"><body>天气通为您提供天气服务，这里是网站品牌介绍与入口。</body>',
                              headers={'content-type':'text/html; charset=utf-8'})

    data = WebsiteSearch(transport=httpx.MockTransport(handle), resolver=dns).search('天气通')
    assert calls[0].url.params['q'] == '天气通 官网'
    assert data['candidates'][0]['url'] == 'https://example.com/'
    assert data['candidates'][0]['evidence']['auto_eligible']


def test_news_title_is_not_official_website_identity():
    """文章中提及站名不能使新闻网站成为目标站。"""
    item = candidate()
    page = dict(status='read', title='新闻网 - 首页', names=['新闻网'], text='介绍样例站的新产品')
    assert not site_evidence('样例站', item, page)['auto_eligible']


def test_choice_token_generation_expiry_and_repetition(tmp_path):
    """GUI/语音选择共用当前候选，只消费一次，过期和重连立即失效。"""
    pipeline = TaskPipeline(GatewayCallbacks(), decoder_factory=lambda path: SimpleNamespace())
    pipeline.begin_session()
    pending = dict(name='样例站', candidates=[candidate()], generation=pipeline.generation,
                   created_at=time.monotonic(), expires_at=time.monotonic()+60, token='token')
    pipeline.website_pending = pending
    assert not pipeline.choose_website('old', 1)
    assert pipeline.choose_website('token', 1)
    assert not pipeline.choose_website('token', 1)
    queued = pipeline.tasks.get_nowait()
    assert queued[3]['index'] == 1 and queued[3]['input_source'] == 'gui'
    pipeline.busy = False
    pipeline.website_pending = dict(pending, expires_at=time.monotonic()-1)
    assert not pipeline.choose_website('token', 1)
    pipeline.website_pending = pending
    pipeline.invalidate()
    pipeline.begin_session()
    assert not pipeline.choose_website('token', 1) and pipeline.website_pending is None


def test_full_pipeline_asks_then_voice_selects_once(tmp_path):
    """生产任务线程完成解析追问、下一句选择、报告与原生摘要，不在追问时打开。"""
    events, requested, submitted = [], threading.Event(), threading.Event()
    job = runner(tmp_path, brain=Brain('ask', 0), search=Search([candidate(eligible=False)]))
    callbacks = GatewayCallbacks()

    def event(state, detail):
        """记录任务事件，用独立事件等待终态。"""
        events.append((state, detail))
        if state == 'needs_input':
            requested.set()
        if state == 'submitted':
            submitted.set()

    callbacks.on_task_event = event
    pipeline = TaskPipeline(callbacks, decoder_factory=lambda path: SimpleNamespace(), runner_factory=lambda: job)
    pipeline.begin_session()
    thread = threading.Thread(target=pipeline._task_loop)
    thread.start()
    try:
        utterance = Utterance('first', pipeline.generation, time.monotonic(), np.zeros(16000), False)
        assert pipeline.submit(utterance, '打开样例站', ('website', '样例站'))
        assert requested.wait(3)
        deadline = time.monotonic()+2
        while pipeline.busy and time.monotonic() < deadline:
            time.sleep(.01)
        assert pipeline.website_pending and not job.local_executor.calls
        # 候选生成之前说出的选择，不能因转写迟到而授权当前候选。
        late = Utterance('late', pipeline.generation, pipeline.website_pending['created_at'] - .5, np.zeros(16000), False)
        assert not pipeline.submit(late, '打开第一个网站', ('website_choice', '1'))
        assert pipeline.website_pending and not job.local_executor.calls
        # GUI 获取副本，即使外部回调修改候选也不能替换真实目标。
        detail = next(d for s, d in events if s == 'needs_input')
        detail['website']['candidates'][0]['url'] = 'https://evil.example/'
        utterance = Utterance('second', pipeline.generation, time.monotonic(), np.zeros(16000), False)
        assert pipeline.submit(utterance, '打开第一个网站', ('website_choice', '1'))
        assert not pipeline.submit(utterance, '打开第一个网站', ('website_choice', '1'))
        assert submitted.wait(3)
        assert job.local_executor.calls == [('open_url', {'url': 'https://example.com/'})]
        assert pipeline.website_pending is None
        assert pipeline.stats()['tasks_launch_submitted'] == 1
    finally:
        pipeline.stopped.set()
        pipeline.invalidate()
        thread.join(2)


def test_preflight_failure_and_model_cancellation_prevent_launch(tmp_path):
    """搜索之后 DNS 失效或模型期间取消，均不得触发浏览器。"""
    job = runner(tmp_path)

    def refuse(url, should_stop):
        """模拟最后一次公网检查发现不可访问地址。"""
        raise SearchError('拒绝内网')

    job.website_search.preflight = refuse
    with pytest.raises(SearchError):
        job.run('打开样例站', website_request=('website', '样例站'))
    assert not job.local_executor.calls
    stopped = threading.Event()
    original = job.brain.think_with_tools

    def cancel(*args, **kwargs):
        """模型返回之前使任务失效。"""
        stopped.set()
        return original(*args, **kwargs)

    job.brain.think_with_tools = cancel
    with pytest.raises(BrainCancelled):
        job.run('打开样例站', stopped.is_set, website_request=('website', '样例站'))
    assert not job.local_executor.calls


def test_forget_and_refresh_are_explicit_local_controls(tmp_path):
    """清除/更正映射不需要模型猜路径或全量记忆权限。"""
    assert local_instruction('忘记样例站的网站地址') == ('website_forget', '样例站')
    assert local_instruction('重新查找样例站网站') == ('website', '样例站', 'refresh')
    job = runner(tmp_path)
    job.website_cache.change('样例站', candidate())
    result = job.run('清除网站缓存', website_request=('website_forget', '*'))
    assert result.status == 'completed' and not job.brain.count and not job.local_executor.calls
    assert job.website_cache.get('样例站') is None


def test_copied_homepage_with_conflicting_canonical_cannot_auto_open():
    """复刻的标题和正文不能覆盖页面声明的另一个主站域名。"""
    item = candidate(host='mirror.example')
    item['page']['claimed_urls'] = ['https://original.example/']
    data = site_evidence('样例站', item, item['page'])
    assert data['identity_conflict'] and not data['auto_eligible']


def test_forum_does_not_compete_with_unspecified_homepage():
    """明确是论坛/下载分站的候选不冒充用户只说站名时的主站。"""
    item = candidate()
    item['title'] = '样例站官方论坛 - 样例站'
    assert not site_evidence('样例站', item, item['page'])['auto_eligible']


def test_http_only_identity_requires_explicit_choice():
    """站名解析的自动派发只选 HTTPS；HTTP 搜索候选仍可由用户明确选择。"""
    item = candidate()
    item['url'] = 'http://example.com/'
    assert not site_evidence('样例站', item, item['page'])['auto_eligible']


def test_title_and_hidden_name_cannot_supply_body_identity():
    """标题、head 和隐藏脚本中的站名不能重复算作可见正文证据。"""
    page = SitePage()
    page.feed('<head><title>样例站 - 官网</title>样例站</head>'
              '<script>样例站</script><div hidden>样例站</div>'
              '<body>这里是一段未说明身份的介绍文字，不能仅凭标题确认网站。</body>')
    item = candidate()
    item['page']['text'] = ' '.join(page.body_parts)
    data = site_evidence('样例站', item, item['page'])
    assert data['exact_name'] and not data['body_name'] and not data['auto_eligible']


def test_gateway_probe_statistics_omit_site_names_and_choices():
    """文件探针只统计网站路由类别，用户站名与选项参数留在任务报告中。"""
    from verify_gateway_tasks import ProbeCallbacks
    callbacks = ProbeCallbacks()
    for task in ('打开样例站', '打开第一个网站', '忘记样例站的网站地址'):
        callbacks.on_user_transcript(task)
    assert callbacks.fields == [['website'], ['website_choice'], ['website_forget']]


def test_incomplete_cache_entry_is_not_reused(tmp_path):
    """缺少候选身份字段的损坏缓存回退搜索，不进入页面核验时报错。"""
    cache = WebsiteCache(tmp_path / 'cache.json')
    cache.change('样例站', candidate())
    data = json.loads(cache.path.read_text(encoding='utf-8'))
    del data['entries']['样例站']['candidate']['title']
    cache.path.write_text(json.dumps(data), encoding='utf-8')
    assert cache.get('样例站') is None


@pytest.mark.parametrize('global_matches', [True, False])
def test_unrelated_domestic_results_try_global_without_reading_wrong_sites(global_matches):
    """搜索正常返回却失配时换入口；两次失配不得读取无关站点或选择它们。"""
    calls = []

    async def dns(host, port):
        """提供测试公网 IP。"""
        return ['93.184.216.34']

    def handle(request):
        """国内结果失配，全球结果按测试参数匹配或继续失配。"""
        host = request.headers['host']
        calls.append(host)
        if host.endswith('bing.com'):
            matches = host == 'www.bing.com' and global_matches
            title, domain = ('样例站官网', 'example.com') if matches else ('其他内容', 'unrelated.example')
            return httpx.Response(200, text=f'<rss><channel><item><title>{title}</title>'
                                  f'<link>https://{domain}/</link></item></channel></rss>',
                                  headers={'content-type':'application/rss+xml'})
        assert host == 'example.com'
        return httpx.Response(200, text='<title>样例站</title><body>样例站品牌产品介绍，这是当前网站可读取的静态主页内容。</body>',
                              headers={'content-type':'text/html; charset=utf-8'})

    search = WebsiteSearch(transport=httpx.MockTransport(handle), resolver=dns)
    if global_matches:
        result = search.search('样例站')
        assert result['provider'] == 'www.bing.com' and result['candidates'][0]['evidence']['auto_eligible']
        assert calls == ['cn.bing.com', 'www.bing.com', 'example.com']
    else:
        with pytest.raises(SearchError, match='未取得相关候选'):
            search.search('样例站')
        assert calls == ['cn.bing.com', 'www.bing.com']
