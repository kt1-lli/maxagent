#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""联网搜索模块 + 工具的回归测试。

完全离线：所有真实 HTTP 调用都被 monkeypatch 替换为本地 mock，
保证在没有外网的环境也能稳跑。
"""

from __future__ import absolute_import
from __future__ import print_function

import pytest


from maxagent import web_search as ws
from maxagent.web_search import DuckDuckGoBackend
from maxagent.web_search import SearchError
from maxagent.web_search import _strip_html
from maxagent.web_search import fetch_page_text
from maxagent.web_search import search


@pytest.fixture(autouse=True)
def _isolate_web_state(monkeypatch):
    """每个用例前后清缓存 + 默认禁掉真实 HTTP，防止跨文件污染。

    具体测试用例需要走 HTTP 时，会用更具体的 monkeypatch 覆盖 ``_http_get``。
    """
    ws.clear_cache()

    def _no_network(*_a, **_kw):
        raise SearchError('网络已被测试禁用')

    monkeypatch.setattr(ws, '_http_get', _no_network)
    yield
    ws.clear_cache()


# ---------------------------------------------------------------------- #
# 辅助函数测试
# ---------------------------------------------------------------------- #
def test_strip_html_basic():
    assert _strip_html('<b>hi</b>') == 'hi'
    assert _strip_html('<a href="x">y</a>') == 'y'
    assert _strip_html('a&amp;b') == 'a&b'
    assert _strip_html('  <p>  spaced  </p>  ') == 'spaced'


def test_strip_html_empty():
    assert _strip_html('') == ''
    assert _strip_html(None) == ''


# ---------------------------------------------------------------------- #
# DuckDuckGo 解析
# ---------------------------------------------------------------------- #
_DDG_FAKE_HTML = '''
<html><body>
<div class="result">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa">Title <b>One</b></a>
  <a class="result__snippet" href="x">Snippet <i>1</i></a>
</div>
<div class="result">
  <a class="result__a" href="https://example.org/b">Title Two</a>
  <a class="result__snippet" href="x">Snippet 2</a>
</div>
</body></html>
'''


def test_ddg_parses_two_results(monkeypatch):
    monkeypatch.setattr(ws, '_http_get', lambda url, **kw: _DDG_FAKE_HTML)
    backend = DuckDuckGoBackend()
    out = backend.search('hello', max_results=5)
    assert len(out) == 2
    assert out[0].title == 'Title One'
    assert out[0].url == 'https://example.com/a'
    assert out[0].snippet == 'Snippet 1'
    assert out[1].title == 'Title Two'
    assert out[1].url == 'https://example.org/b'


def test_ddg_max_results(monkeypatch):
    monkeypatch.setattr(ws, '_http_get', lambda url, **kw: _DDG_FAKE_HTML)
    backend = DuckDuckGoBackend()
    out = backend.search('hello', max_results=1)
    assert len(out) == 1


# ---------------------------------------------------------------------- #
# 顶层 search()
# ---------------------------------------------------------------------- #
def test_search_empty_query():
    assert search('') == []
    assert search('   ') == []


def test_search_with_disabled_backend():
    assert search('q', backend='disabled') == []


def test_search_uses_cache(monkeypatch):
    ws.clear_cache()
    calls = {'n': 0}

    def _fake(url, **kw):
        calls['n'] += 1
        return _DDG_FAKE_HTML

    monkeypatch.setattr(ws, '_http_get', _fake)
    a = search('python', backend='duckduckgo', use_cache=True)
    b = search('python', backend='duckduckgo', use_cache=True)
    assert len(a) == 2
    assert len(b) == 2
    # 第二次应直接命中缓存，HTTP 只发一次
    assert calls['n'] == 1


def test_search_unknown_backend_falls_back_to_ddg(monkeypatch, caplog):
    ws.clear_cache()
    monkeypatch.setattr(ws, '_http_get', lambda url, **kw: _DDG_FAKE_HTML)
    out = search('q', backend='nonsense', use_cache=False)
    assert len(out) == 2  # DDG 路径生效


# ---------------------------------------------------------------------- #
# fetch_page_text
# ---------------------------------------------------------------------- #
def test_fetch_page_text_strips_script_and_style(monkeypatch):
    fake_html = (
        '<html><body><script>var a=1;</script>'
        '<style>.x{color:red}</style>'
        'hello <b>world</b>'
        '</body></html>'
    )
    monkeypatch.setattr(ws, '_http_get', lambda url, **kw: fake_html)
    txt = fetch_page_text('http://x', max_chars=200)
    assert 'hello' in txt and 'world' in txt
    assert 'var a' not in txt
    assert 'color' not in txt


def test_fetch_page_text_truncates(monkeypatch):
    long_text = 'a' * 9999
    monkeypatch.setattr(ws, '_http_get', lambda url, **kw: long_text)
    txt = fetch_page_text('http://x', max_chars=100)
    assert len(txt) <= 200
    assert txt.endswith('(truncated)')


def test_fetch_page_text_returns_empty_on_error(monkeypatch):
    def _boom(url, **kw):
        raise SearchError('mock fail')

    monkeypatch.setattr(ws, '_http_get', _boom)
    assert fetch_page_text('http://x') == ''


# ---------------------------------------------------------------------- #
# 工具层
# ---------------------------------------------------------------------- #
def _patch_load_config(monkeypatch, **fields):
    """方便地 monkeypatch maxagent.tools.web_tools._resolve_web_settings。"""
    settings = {
        'mode': fields.get('mode', 'auto'),
        'backend': fields.get('backend', 'duckduckgo'),
        'max_results': fields.get('max_results', 5),
        'fetch_page_text': fields.get('fetch_page_text', False),
        'bing_api_key': fields.get('bing_api_key', ''),
    }
    from maxagent.tools import web_tools as wt
    monkeypatch.setattr(wt, '_resolve_web_settings', lambda: settings)


def test_web_search_tool_off(monkeypatch):
    from maxagent.tools.web_tools import web_search as web_search_tool
    _patch_load_config(monkeypatch, mode='off')
    out = web_search_tool('hello')
    assert out['ok'] is False
    assert '关闭' in out['error']


def test_web_search_tool_normal(monkeypatch):
    from maxagent.tools.web_tools import web_search as web_search_tool
    _patch_load_config(monkeypatch)
    # 直接拦截到顶层 search，避免跨模块 monkeypatch 失效
    from maxagent.tools import web_tools as wt

    def _fake_search(query, max_results=5, backend='duckduckgo',
                     bing_api_key='', fetch_page=False, **_kw):
        from maxagent.web_search import SearchResult
        return [
            SearchResult(title='Title One', url='https://example.com/a',
                         snippet='Snippet 1'),
            SearchResult(title='Title Two', url='https://example.org/b',
                         snippet='Snippet 2'),
        ]

    monkeypatch.setattr(wt, '_do_search', _fake_search)
    out = web_search_tool('hello')
    assert out['ok'] is True
    assert out['count'] == 2
    assert out['results'][0]['title'] == 'Title One'


def test_web_fetch_tool_validates_url(monkeypatch):
    from maxagent.tools.web_tools import web_fetch as web_fetch_tool
    _patch_load_config(monkeypatch)
    out = web_fetch_tool('not_a_url')
    assert out['ok'] is False
    assert 'http' in out['error']


def test_web_fetch_tool_off(monkeypatch):
    from maxagent.tools.web_tools import web_fetch as web_fetch_tool
    _patch_load_config(monkeypatch, mode='off')
    out = web_fetch_tool('http://example.com')
    assert out['ok'] is False


def test_web_fetch_tool_success(monkeypatch):
    from maxagent.tools.web_tools import web_fetch as web_fetch_tool
    _patch_load_config(monkeypatch)
    # 同样直接拦截到 web_tools 模块级的 fetch_page_text
    from maxagent.tools import web_tools as wt
    monkeypatch.setattr(wt, 'fetch_page_text', lambda url, max_chars=4000: 'hello')
    out = web_fetch_tool('http://example.com', max_chars=200)
    assert out['ok'] is True
    assert out['text'] == 'hello'


# ---------------------------------------------------------------------- #
# AppConfig 序列化
# ---------------------------------------------------------------------- #
def test_app_config_roundtrip_web_fields():
    from maxagent.config import AppConfig
    cfg = AppConfig()
    cfg.web_search_mode = 'force'
    cfg.web_search_backend = 'bing_api'
    cfg.web_search_max_results = 8
    cfg.web_fetch_page_text = False
    cfg.bing_api_key = 'xx'
    data = cfg.to_dict()
    loaded = AppConfig.from_dict(data)
    assert loaded.web_search_mode == 'force'
    assert loaded.web_search_backend == 'bing_api'
    assert loaded.web_search_max_results == 8
    assert loaded.web_fetch_page_text is False
    assert loaded.bing_api_key == 'xx'


def test_app_config_invalid_values_fall_back():
    from maxagent.config import AppConfig
    cfg = AppConfig.from_dict({
        'web_search_mode': 'bogus',
        'web_search_backend': 'ghost',
        'web_search_max_results': -3,
    })
    assert cfg.web_search_mode == 'auto'
    assert cfg.web_search_backend == 'duckduckgo'
    # max_results 被 clamp 到 1（合法范围 1~10）
    assert 1 <= cfg.web_search_max_results <= 10
