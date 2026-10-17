"""网站名称解析：有界检索、首页身份线索和可失效的本机 JSON 缓存。"""

import asyncio
from contextlib import nullcontext
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import unicodedata
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from .web_search import (WebSearchClient, PageText, SearchError, SearchCancelled,
                         check_search_cancelled, parse_results, public_url)

CACHE_PATH = Path(__file__).resolve().parents[2] / '.cache/sites/resolutions.json'


def name_key(value):
    """仅用于站名匹配与缓存键，不修改实际网址。"""
    return re.sub(r'[\W_]+', '', unicodedata.normalize('NFKC', value).casefold())


def host_key(url):
    """仅合并同一主机的 www 写法，不把不同子域或相似域当成同站。"""
    return (urlsplit(url).hostname or '').lower().removeprefix('www.')


class SitePage(PageText):
    """除静态正文外读取网站名称元数据；JSON-LD 只当数据，不跟随其中 URL。"""

    def __init__(self):
        """创建有界名称和结构化数据缓冲。"""
        super().__init__()
        self.names, self.claimed_urls, self.ld_parts, self.ld_active = [], [], [], False
        self.body_parts, self.body_size = [], 0

    def handle_starttag(self, tag, attrs):
        """采集站名元数据和最多 16KiB 的 JSON-LD，不执行脚本。"""
        values = {key: value or '' for key, value in attrs}
        if tag == 'meta' and values.get('property', values.get('name', '')).lower() in {'og:site_name', 'application-name'}:
            self.names.append(values.get('content', '')[:160])
        if tag == 'meta' and values.get('property', '').lower() == 'og:url':
            self.claimed_urls.append(values.get('content', '')[:2048])
        if tag == 'link' and 'canonical' in values.get('rel', '').lower().split():
            self.claimed_urls.append(values.get('href', '')[:2048])
        if tag == 'script':
            self.ld_active = values.get('type', '').lower() == 'application/ld+json'
            self.ld_parts = []
        super().handle_starttag(tag, attrs)

    def handle_data(self, data):
        """结构化脚本文本单独保存，正文仍遵守隐藏区限制。"""
        if self.ld_active and sum(map(len, self.ld_parts)) < 16384:
            self.ld_parts.append(data[:16384 - sum(map(len, self.ld_parts))])
        if self.body_size < 20000 and not any(hidden or tag in {'head', 'title'} for tag, hidden in self.stack):
            text = re.sub(r'\s+', ' ', data).strip()[:20000 - self.body_size]
            if text:
                self.body_parts.append(text)
                self.body_size += len(text)
        super().handle_data(data)

    def handle_endtag(self, tag):
        """有限遍历 WebSite/Organization 的名称；损坏数据忽略，不影响正文。"""
        if tag == 'script' and self.ld_active:
            self.ld_active = False
            try:
                pending = [json.loads(''.join(self.ld_parts))]
                for _ in range(128):
                    if not pending:
                        break
                    node = pending.pop()
                    if isinstance(node, list):
                        pending.extend(node[:32])
                    elif isinstance(node, dict):
                        kinds = node.get('@type', [])
                        if isinstance(kinds, str):
                            kinds = [kinds]
                        if isinstance(kinds, list) and any(k in {'WebSite', 'Organization'} for k in kinds if isinstance(k, str)):
                            for key in ('name', 'alternateName'):
                                names = node.get(key, [])
                                if isinstance(names, str):
                                    names = [names]
                                if isinstance(names, list):
                                    self.names.extend(n[:160] for n in names[:10] if isinstance(n, str))
                            if 'WebSite' in kinds and isinstance(node.get('url'), str):
                                self.claimed_urls.append(node['url'][:2048])
                        pending.extend(list(node.values())[:32])
            except (ValueError, RecursionError):
                pass
        super().handle_endtag(tag)


def site_evidence(name, item, page):
    """记录独立匹配信号；自动候选须首页读到身份线索且搜索名称相符。"""
    key = name_key(name)
    title = page.get('title', '')
    names = page.get('names', [])
    title_names = re.split(r'\s*[|_—–\-]\s*', title)
    exact = bool(key) and any(name_key(n) == key for n in names + title_names)
    if re.fullmatch(r'[a-z]{3,12}', key):
        exact |= any(re.search(r'(?<!\w)' + re.escape(key) + r'(?!\w)', n.casefold()) is not None for n in names + title_names)
    search_match = key in name_key(item['title'])
    body_match = key in name_key(page.get('text', ''))
    misleading = bool(re.search(r'百科|下载站|下載站|官网入口|官網入口|网址大全|網站大全|网站导航|網站導航', title))
    section_mismatch = any(word in title + item['title'] and word not in name
                           for word in ('论坛', '論壇', '社区', '社區', '下载', '下載', '百科', '文档', '文檔'))
    claimed_urls = page.get('claimed_urls', [])
    identity_conflict = any(host_key(urljoin(page.get('url', item['url']), value)) != host_key(item['url'])
                            for value in claimed_urls if value)
    return dict(exact_name=exact, search_name=search_match, body_name=body_match,
                section_mismatch=section_mismatch, identity_conflict=identity_conflict,
                auto_eligible=urlsplit(item['url']).scheme == 'https' and page.get('status') == 'read'
                and exact and search_match and body_match and not misleading and not section_mismatch and not identity_conflict,
                relevant=search_match or exact)


class WebsiteSearch(WebSearchClient):
    """站名专用搜索适配器，复用既有逐跳公网连接与取消，不套用天气规则。"""

    minimum_query_length = 1

    async def inspect(self, url):
        """读取候选首页的实际终点与身份线索；不执行 JS，也不猜测页面存在。"""
        final, body, kind = await self._get(url, 6)
        charset = re.search(r'charset\s*=\s*[\"\']?([\w-]+)', kind + ' ' + body[:4096].decode('ascii', errors='ignore'))
        try:
            text = body.decode(charset.group(1) if charset else 'utf-8', errors='replace')
        except LookupError:
            text = body.decode('utf-8', errors='replace')
        page = SitePage()
        page.feed(text)
        title, excerpt = ' '.join(page.title_parts)[:200], ' '.join(page.body_parts)[:2500]
        if (len(excerpt) < 20 or re.search(r'安全验证|访问验证|验证码|captcha|Access Denied|Just a moment', title, re.I)
                or excerpt.count('\ufffd') > max(3, len(excerpt) // 20)):
            raise SearchError('首页需要验证或没有可用身份信息')
        return dict(status='read', url=final, title=title, names=list(dict.fromkeys(page.names))[:16],
                    claimed_urls=list(dict.fromkeys(page.claimed_urls))[:16], text=excerpt)

    async def _search(self, name):
        """请求失败或结果失配再试全球入口，同主机去重后最多读取三站首页。"""
        query, results, failures = name + ' 官网', [], []
        for host in ('cn.bing.com', 'www.bing.com'):
            try:
                url = str(httpx.URL(f'https://{host}/search', params={'q': query, 'format': 'rss'}))
                _, body, _ = await self._get(url, 8, rss=True)
                results = parse_results(body, 20)
                if any(name_key(name) in name_key(item['title'] + ' ' + item['snippet']) for item in results):
                    break
                results = []
                failures.append(host + ' 未返回包含完整站名的候选')
            except SearchError as error:
                failures.append(str(error))
        if not results:
            raise SearchError('网站搜索未取得相关候选：' + '; '.join(failures))
        unique, seen = [], set()
        for item in results:
            host_id = host_key(item['url'])
            if host_id in seen:
                continue
            seen.add(host_id)
            parsed = urlsplit(item['url'])
            item['search_url'] = item['url']
            item['url'] = urlunsplit((parsed.scheme, parsed.netloc, '/', '', ''))
            unique.append(item)
            if len(unique) == 5:
                break

        async def read(item):
            """单站读取失败保留搜索候选，供明确选择，不作为自动打开证据。"""
            try:
                item['page'] = await self.inspect(item['url'])
                item['url'] = item['page']['url']
            except SearchError as error:
                item['page'] = dict(status='unavailable', reason=str(error))

        await asyncio.gather(*(read(item) for item in unique[:3]))
        candidates, seen = [], set()
        for item in unique:
            item.setdefault('page', dict(status='not_read'))
            evidence = site_evidence(name, item, item['page'])
            if not evidence['relevant'] or host_key(item['url']) in seen:
                continue
            seen.add(host_key(item['url']))
            item.update(id=len(candidates) + 1, evidence=evidence)
            candidates.append(item)
        return dict(name=name, query=query, provider=host, candidates=candidates[:3], checked_at=time.time())

    def preflight(self, url, should_stop=None):
        """最终派发前只检查地址与公网 DNS；浏览器自己的跳转和加载另行负责。"""
        async def check():
            """在后台事件循环执行有界 DNS 校验。"""
            parsed = urlsplit(public_url(url))
            await self.resolver(parsed.hostname, 443 if parsed.scheme == 'https' else 80)
        self._wait(check(), should_stop, 6)

    def refresh_cached(self, url, should_stop=None):
        """缓存复用前检查首页终点，域名变化必须重新解析。"""
        return self._wait(self.inspect(url), should_stop, 7)

    @staticmethod
    def _wait(work, should_stop, seconds):
        """统一等待支持 100ms 取消和总时限，回收 HTTP/DNS 等待任务。"""
        async def run():
            """包装已有异步操作，不阻塞 Qt 或音频线程。"""
            pending = asyncio.create_task(work)
            end = time.monotonic() + seconds
            try:
                while not pending.done():
                    check_search_cancelled(should_stop)
                    if time.monotonic() >= end:
                        raise SearchError('网站核验超时')
                    await asyncio.wait({pending}, timeout=.1)
                check_search_cancelled(should_stop)
                return await pending
            finally:
                if not pending.done():
                    pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
        return asyncio.run(run())


class WebsiteCache:
    """可删除、带来源和有效期的本地解析缓存，不作为永久身份真源。"""

    def __init__(self, path=CACHE_PATH, clock=time.time):
        """绑定忽略目录与时钟，文件读取延后到后台任务。"""
        self.path, self.clock, self.lock = Path(path), clock, threading.RLock()

    def _read(self):
        """损坏、过大或版本不符的缓存视为空，不使用无法核验的记录。"""
        try:
            if self.path.stat().st_size > 1024 * 1024:
                return {}
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if data.get('version') != 1 or not isinstance(data.get('entries'), dict):
                return {}
            return {key: value for key, value in data['entries'].items()
                    if isinstance(value, dict) and isinstance(value.get('checked_at'), (float, int))}
        except (OSError, ValueError, TypeError, AttributeError):
            return {}

    def get(self, name):
        """仅返回未过期、名称与网址格式完整的副本。"""
        with self.lock:
            item = self._read().get(name_key(name))
            try:
                if (not isinstance(item, dict) or name_key(item['name']) != name_key(name)
                        or not isinstance(item['expires_at'], (float, int))
                        or not self.clock() < item['expires_at'] <= self.clock() + 31 * 86400
                        or item['source'] not in {'user', 'evidence'}):
                    return None
                if (not isinstance(item['candidate']['title'], str)
                        or type(item['candidate']['id']) is not int):
                    return None
                public_url(item['candidate']['url'])
                return item
            except (KeyError, ValueError, TypeError, SearchError):
                return None

    def change(self, name, candidate=None, source='evidence', should_stop=None, publication_lock=None):
        """原子更正/删除缓存；取消代次不得发布旧记录，容量限 128 条。"""
        with self.lock:
            data = self._read()
            if name == '*':
                data = {}
            elif candidate is None:
                data.pop(name_key(name), None)
            else:
                candidate = json.loads(json.dumps(candidate, ensure_ascii=False))
                public_url(candidate['url'])
                # 缓存只保留身份依据，不积累整页正文或搜索历史。
                candidate['page'] = {k: v for k, v in candidate.get('page', {}).items() if k in {'status', 'title', 'names', 'claimed_urls', 'url'}}
                data[name_key(name)] = dict(name=name, candidate=candidate, source=source, checked_at=self.clock(),
                                           expires_at=self.clock() + (30 if source == 'user' else 7) * 86400)
                data = dict(sorted(data.items(), key=lambda kv: kv[1].get('checked_at', 0))[-128:])
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent, delete=False) as handle:
                    temporary = Path(handle.name)
                    json.dump(dict(version=1, entries=data), handle, ensure_ascii=False, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
                with publication_lock if publication_lock is not None else nullcontext():
                    check_search_cancelled(should_stop)
                    os.replace(temporary, self.path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
