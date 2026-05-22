#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""联网搜索抽象层。

设计目标
========
1. **零外部依赖**：纯 ``urllib`` + 简易 HTML 解析（正则 / html.parser），
   遵循项目"不在 Max 内 pip 装包"的总规则。
2. **多后端**：抽象 ``SearchBackend`` 基类，目前实现：
   - DuckDuckGo HTML scraping（默认，免 Key）
   - Bing Search API（占位，需用户填 Key）
3. **轻量正文抓取**：拿到 url 后可选拉取网页正文，剔除 script/style，
   截断到合理长度（默认 4000 字符）作为 LLM 上下文。
4. **进程内 LRU 缓存**：同一 query 在 5 分钟内重复搜索直接返回缓存，
   避免上下游调度抖动时反复打远端。
5. **可独立测试**：所有网络调用集中在 ``_http_get`` 一处，测试用例
   通过 monkeypatch 注入 mock 即可全覆盖，不依赖真实网络。

关键 API
========
- :class:`SearchResult`：单条搜索结果的数据结构
- :func:`search`：统一入口，按当前后端发起搜索，返回结果列表
- :func:`fetch_page_text`：抓取并清洗单个网页的正文文本
"""

from __future__ import absolute_import
from __future__ import print_function

import html
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from dataclasses import field
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional

from .logger import get_logger


logger = get_logger(__name__)


# 默认 UA：模仿主流桌面浏览器，提高 DuckDuckGo HTML 端点不被反爬的概率。
_DEFAULT_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/124.0 Safari/537.36'
)

# 单次 HTTP 请求超时（秒）；联网慢时可由调用方覆盖
DEFAULT_HTTP_TIMEOUT = 8.0

# 缓存 TTL（秒）；同一 query 在该时长内复用结果
DEFAULT_CACHE_TTL = 300.0

# 网页正文抓取后的最大字符数（避免把 LLM 上下文塞爆）
DEFAULT_PAGE_TEXT_LIMIT = 4000


@dataclass
class SearchResult:
    """单条搜索结果。

    :param title: 标题
    :param url:   链接
    :param snippet: 摘要（来自搜索引擎给出的简短描述）
    :param page_text: 抓取后的网页正文（仅当用户开启"抓正文"时填充）
    """

    title: str = ''
    url: str = ''
    snippet: str = ''
    page_text: str = ''

    def to_dict(self):
        return {
            'title': self.title,
            'url': self.url,
            'snippet': self.snippet,
            'page_text': self.page_text,
        }


# ---------------------------------------------------------------------- #
# HTTP 工具
# ---------------------------------------------------------------------- #
def _http_get(url, timeout=DEFAULT_HTTP_TIMEOUT, headers=None):
    # type: (str, float, Optional[Dict[str, str]]) -> str
    """统一的 GET 入口，把所有联网调用集中到这里方便测试。

    返回 utf-8 解码后的响应体；网络错误时抛 :class:`SearchError`。
    """
    hdrs = {'User-Agent': _DEFAULT_UA, 'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs, method='GET')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            # 部分站点不带 charset，做一次安全 fallback
            return raw.decode('utf-8', errors='replace')
    except urllib.error.HTTPError as exc:
        raise SearchError('HTTP {}: {}'.format(exc.code, exc.reason))
    except urllib.error.URLError as exc:
        raise SearchError('网络错误: {}'.format(exc.reason))
    except Exception as exc:  # pylint: disable=broad-except
        raise SearchError('请求异常: {}'.format(exc))


class SearchError(Exception):
    """搜索相关异常。"""


# ---------------------------------------------------------------------- #
# 后端
# ---------------------------------------------------------------------- #
class SearchBackend(object):
    """搜索后端抽象基类。"""

    name = 'base'

    def search(self, query, max_results=5):
        # type: (str, int) -> List[SearchResult]
        raise NotImplementedError


class DuckDuckGoBackend(SearchBackend):
    """DuckDuckGo HTML scraping 后端。

    访问 ``https://html.duckduckgo.com/html/?q=...``，解析返回的 HTML
    并抽取 ``<a class="result__a">`` / ``<a class="result__snippet">``。

    DDG 偶尔会返回反爬空页或 redirect 链接（``//duckduckgo.com/l/?uddg=...``），
    本实现做了 url 解码兜底。
    """

    name = 'duckduckgo'
    _ENDPOINT = 'https://html.duckduckgo.com/html/'

    # <a ... class="result__a" href="..."> 标题 </a>
    _LINK_RE = re.compile(
        r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.+?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    # 摘要：<a class="result__snippet" href="..."> 内容 </a>
    _SNIPPET_RE = re.compile(
        r'<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.+?)</a>',
        re.IGNORECASE | re.DOTALL,
    )

    def search(self, query, max_results=5):
        params = urllib.parse.urlencode({'q': query, 'kl': 'wt-wt'})
        url = '{}?{}'.format(self._ENDPOINT, params)
        body = _http_get(url)
        links = self._LINK_RE.findall(body)
        snippets = self._SNIPPET_RE.findall(body)
        results = []
        for i, (raw_url, raw_title) in enumerate(links):
            if i >= max_results:
                break
            target_url = self._unwrap_redirect(raw_url)
            title = _strip_html(raw_title)
            snippet = ''
            if i < len(snippets):
                snippet = _strip_html(snippets[i])
            results.append(SearchResult(
                title=title, url=target_url, snippet=snippet,
            ))
        return results

    @staticmethod
    def _unwrap_redirect(raw_url):
        """DDG HTML 链接通常是 ``//duckduckgo.com/l/?uddg=<urlenc(target)>``，
        需要剥一层取真实 url。
        """
        if not raw_url:
            return ''
        if raw_url.startswith('//'):
            raw_url = 'https:' + raw_url
        try:
            parsed = urllib.parse.urlparse(raw_url)
            if 'duckduckgo.com' in parsed.netloc and parsed.path.startswith('/l/'):
                qs = urllib.parse.parse_qs(parsed.query)
                target = qs.get('uddg') or qs.get('u')
                if target:
                    return urllib.parse.unquote(target[0])
        except Exception:  # pylint: disable=broad-except
            pass
        return raw_url


class BingApiBackend(SearchBackend):
    """Bing Search API 后端（占位实现，需要 ``api_key``）。

    商用 API 通常更稳定，但需要付费 Key；不传 key 直接降级为可读错误。
    """

    name = 'bing_api'
    _ENDPOINT = 'https://api.bing.microsoft.com/v7.0/search'

    def __init__(self, api_key=''):
        self._api_key = api_key or ''

    def search(self, query, max_results=5):
        if not self._api_key:
            raise SearchError(
                'Bing API 需要在设置-联网中填入 API Key',
            )
        params = urllib.parse.urlencode({
            'q': query, 'count': max(1, min(10, int(max_results))),
        })
        url = '{}?{}'.format(self._ENDPOINT, params)
        body = _http_get(url, headers={
            'Ocp-Apim-Subscription-Key': self._api_key,
        })
        # 留给后续按需 JSON 解析；当前最小可用：不阻塞功能上线
        try:
            import json
            data = json.loads(body)
        except Exception as exc:  # pylint: disable=broad-except
            raise SearchError('Bing API 响应解析失败: {}'.format(exc))
        out = []
        for item in (data.get('webPages') or {}).get('value', [])[:max_results]:
            out.append(SearchResult(
                title=item.get('name') or '',
                url=item.get('url') or '',
                snippet=item.get('snippet') or '',
            ))
        return out


# ---------------------------------------------------------------------- #
# HTML 清洗
# ---------------------------------------------------------------------- #
_TAG_RE = re.compile(r'<[^>]+>')
_WS_RE = re.compile(r'\s+')

_SCRIPT_STYLE_RE = re.compile(
    r'<(script|style|noscript)[^>]*>.*?</\1>',
    re.IGNORECASE | re.DOTALL,
)


def _strip_html(text):
    # type: (str) -> str
    """剥掉 HTML 标签，解码实体，压缩空白。"""
    if not text:
        return ''
    no_tag = _TAG_RE.sub(' ', text)
    decoded = html.unescape(no_tag)
    return _WS_RE.sub(' ', decoded).strip()


def fetch_page_text(url, max_chars=DEFAULT_PAGE_TEXT_LIMIT,
                    timeout=DEFAULT_HTTP_TIMEOUT):
    # type: (str, int, float) -> str
    """抓取单个网页正文并做粗清洗。

    步骤：
    1. GET 请求拿原始 HTML
    2. 删除 ``<script>`` / ``<style>`` / ``<noscript>`` 块
    3. 剥所有标签，解码 HTML 实体，压缩多空白
    4. 截断到 ``max_chars``，超长尾部追加 ``...(truncated)``

    任何异常都返回空字符串（让上层用搜索引擎给的 snippet 兜底）。
    """
    try:
        body = _http_get(url, timeout=timeout)
    except SearchError as exc:
        logger.debug('fetch_page_text 失败 %s: %s', url, exc)
        return ''
    cleaned = _SCRIPT_STYLE_RE.sub(' ', body)
    text = _strip_html(cleaned)
    if len(text) > max_chars:
        return text[:max_chars] + '...(truncated)'
    return text


# ---------------------------------------------------------------------- #
# 缓存
# ---------------------------------------------------------------------- #
class _SearchCache(object):
    """简单的 query → results 缓存，带 TTL。"""

    def __init__(self, ttl=DEFAULT_CACHE_TTL, max_entries=64):
        self._ttl = float(ttl)
        self._max = int(max_entries)
        self._lock = threading.Lock()
        self._data = {}  # type: Dict[str, tuple]

    def get(self, key):
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            ts, value = entry
            if (time.time() - ts) > self._ttl:
                self._data.pop(key, None)
                return None
            return value

    def put(self, key, value):
        with self._lock:
            if len(self._data) >= self._max:
                # 简单 FIFO 淘汰：删最早的一个
                oldest_key = min(self._data, key=lambda k: self._data[k][0])
                self._data.pop(oldest_key, None)
            self._data[key] = (time.time(), value)

    def clear(self):
        with self._lock:
            self._data.clear()


_CACHE = _SearchCache()


# ---------------------------------------------------------------------- #
# 顶层 API
# ---------------------------------------------------------------------- #
def _build_backend(backend_name, bing_api_key=''):
    # type: (str, str) -> Optional[SearchBackend]
    """根据配置名构造对应后端实例。"""
    name = (backend_name or '').lower()
    if name == 'duckduckgo':
        return DuckDuckGoBackend()
    if name == 'bing_api':
        return BingApiBackend(api_key=bing_api_key)
    if name == 'disabled':
        return None
    # 未知后端按 DDG 兜底，但打 warning
    logger.warning('未知搜索后端 "%s"，回退到 DuckDuckGo', backend_name)
    return DuckDuckGoBackend()


def search(query, max_results=5, backend='duckduckgo',
           bing_api_key='', fetch_page=False, page_text_limit=None,
           use_cache=True):
    # type: (str, int, str, str, bool, Optional[int], bool) -> List[SearchResult]
    """对外的统一搜索入口。

    :param query: 用户搜索词
    :param max_results: 限制返回结果数
    :param backend: ``duckduckgo`` / ``bing_api`` / ``disabled``
    :param bing_api_key: 仅 ``bing_api`` 后端需要
    :param fetch_page: 是否抓取每条结果的正文 ``page_text``。开启后
        会对每条结果各发一次 HTTP，用于给 LLM 喂正文质量更高
    :param page_text_limit: 每页正文截断字符数；默认 4000
    :param use_cache: 是否使用进程内缓存（5 分钟 TTL）

    :returns: :class:`SearchResult` 列表，可能为空（无结果或后端禁用）
    :raises SearchError: 网络错误或后端拒绝时
    """
    if not query or not query.strip():
        return []
    q = query.strip()
    cache_key = '{}|{}|{}|{}'.format(backend, q, max_results, fetch_page)
    if use_cache:
        cached = _CACHE.get(cache_key)
        if cached is not None:
            logger.debug('search cache hit: %s', cache_key)
            return cached

    backend_obj = _build_backend(backend, bing_api_key=bing_api_key)
    if backend_obj is None:
        logger.info('search 已禁用（backend=disabled），返回空')
        return []

    t0 = time.time()
    try:
        results = backend_obj.search(q, max_results=max_results)
    except SearchError:
        raise
    logger.info(
        'search backend=%s q="%s" hits=%d in %.2fs',
        backend_obj.name, q[:60], len(results), time.time() - t0,
    )

    if fetch_page and results:
        limit = page_text_limit or DEFAULT_PAGE_TEXT_LIMIT
        for r in results:
            if r.url:
                r.page_text = fetch_page_text(r.url, max_chars=limit)

    if use_cache:
        _CACHE.put(cache_key, results)
    return results


def clear_cache():
    """清空进程内搜索缓存。"""
    _CACHE.clear()
