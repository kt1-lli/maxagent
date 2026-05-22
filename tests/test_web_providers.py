#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""web_providers + GenericHttpBackend 单元测试。

覆盖：
- 内置 provider 默认数据完整性
- ProviderRegistry CRUD 持久化
- 占位符渲染 ``{{query}} {{n}} {{n_int}} {{api_key}} {{extra.x}}``
- GenericHttpBackend 走 JSON 模式 / HTML 模式
- 新版 search() 通过 provider 参数路由到 GenericHttpBackend
"""

from __future__ import absolute_import
from __future__ import print_function

import json
import os

import pytest


from maxagent import web_search as ws
from maxagent import web_providers as wp


# ---------------------------------------------------------------------- #
# fixtures
# ---------------------------------------------------------------------- #
@pytest.fixture
def isolated_path(tmp_path):
    """提供一个临时 providers.json 路径，避免触碰用户真实配置。"""
    return str(tmp_path / 'web_providers.json')


@pytest.fixture(autouse=True)
def _clear_search_cache():
    ws.clear_cache()
    yield
    ws.clear_cache()


# ---------------------------------------------------------------------- #
# 1. 内置预设 + ProviderRegistry
# ---------------------------------------------------------------------- #
def test_builtin_providers_have_required_fields():
    for prov in wp.BUILTIN_PROVIDERS:
        assert prov['id']
        assert prov['name']
        assert prov['url'] or prov.get('response', {}).get('html_scrape')
        assert wp.validate_id(prov['id']), prov['id']


def test_load_providers_writes_default(isolated_path):
    assert not os.path.exists(isolated_path)
    data = wp.load_providers(path=isolated_path)
    assert os.path.exists(isolated_path)
    assert data['providers']
    assert data['active_id']


def test_registry_upsert_and_active(isolated_path):
    reg = wp.ProviderRegistry(path=isolated_path)
    custom = {
        'id': 'my_custom',
        'name': 'My Custom',
        'url': 'https://example.com/api',
        'method': 'GET',
        'params': {'q': '{{query}}'},
        'response': {'items_path': 'data'},
    }
    reg.upsert(custom)
    assert reg.get('my_custom') is not None
    reg.set_active('my_custom')
    assert reg.get_active()['id'] == 'my_custom'


def test_registry_cannot_delete_builtin(isolated_path):
    reg = wp.ProviderRegistry(path=isolated_path)
    with pytest.raises(ValueError):
        reg.delete('duckduckgo')


def test_registry_cannot_delete_active(isolated_path):
    reg = wp.ProviderRegistry(path=isolated_path)
    custom = {
        'id': 'drop_me', 'name': 'Drop Me',
        'url': 'https://example.com',
    }
    reg.upsert(custom)
    reg.set_active('drop_me')
    with pytest.raises(ValueError):
        reg.delete('drop_me')


def test_registry_invalid_id(isolated_path):
    reg = wp.ProviderRegistry(path=isolated_path)
    with pytest.raises(ValueError):
        reg.upsert({
            'id': '123-bad',
            'name': 'Bad',
            'url': 'https://example.com',
        })


def test_registry_api_key_persists_across_load(isolated_path):
    reg = wp.ProviderRegistry(path=isolated_path)
    p = dict(reg.list_providers()[0])
    p['api_key'] = 'secret-key-123'
    reg.upsert(p)
    # 重新加载，API Key 还在
    reg2 = wp.ProviderRegistry(path=isolated_path)
    same = reg2.get(p['id'])
    assert same['api_key'] == 'secret-key-123'


# ---------------------------------------------------------------------- #
# 2. 占位符渲染
# ---------------------------------------------------------------------- #
def test_render_placeholders_basic():
    ctx = {
        'query': 'hello',
        'n': '5', 'n_int': 5,
        'api_key': 'kk',
        'extra': {'cx': 'CXVAL'},
    }
    result = ws._render_placeholders(
        {
            'q': '{{query}}',
            'count': '{{n}}',
            'real_count': '{{n_int}}',
            'header': 'Bearer {{api_key}}',
            'cx': '{{extra.cx}}',
        },
        ctx,
    )
    assert result == {
        'q': 'hello', 'count': '5',
        'real_count': 5,            # 单占位符整串保留 int 类型
        'header': 'Bearer kk',
        'cx': 'CXVAL',
    }


def test_render_placeholders_recursive():
    ctx = {'query': 'x', 'n': '3', 'n_int': 3, 'api_key': '', 'extra': {}}
    result = ws._render_placeholders(
        {'body': {'query': '{{query}}', 'extra': ['{{n}}', '{{n}}']}},
        ctx,
    )
    assert result == {'body': {'query': 'x', 'extra': ['3', '3']}}


def test_render_missing_extra_yields_empty():
    ctx = {'query': 'x', 'n': '1', 'n_int': 1, 'api_key': '', 'extra': {}}
    result = ws._render_placeholders('cx={{extra.unknown}}!', ctx)
    assert result == 'cx=!'


# ---------------------------------------------------------------------- #
# 3. GenericHttpBackend
# ---------------------------------------------------------------------- #
def test_generic_backend_json_mode(monkeypatch):
    """JSON 模式：用 items_path 取列表，按字段路径取 title/url/snippet。"""
    captured = {}

    def fake_do_http(method, url, headers, body_json, timeout):
        captured['method'] = method
        captured['url'] = url
        captured['headers'] = dict(headers)
        captured['body'] = body_json
        return json.dumps({
            'webPages': {
                'value': [
                    {'name': 'T1', 'url': 'https://e.com/1', 'snippet': 'S1'},
                    {'name': 'T2', 'url': 'https://e.com/2', 'snippet': 'S2'},
                ],
            },
        })

    monkeypatch.setattr(
        ws.GenericHttpBackend, '_do_http', staticmethod(fake_do_http),
    )

    prov = {
        'id': 'fake_bing',
        'name': 'Fake Bing',
        'enabled': True,
        'method': 'GET',
        'url': 'https://api.example.com/search',
        'params': {'q': '{{query}}', 'count': '{{n}}'},
        'headers': {'X-Key': '{{api_key}}'},
        'body_json': {},
        'api_key': 'KKK',
        'extra': {},
        'response': {
            'html_scrape': False,
            'items_path': 'webPages.value',
            'title_path': 'name',
            'url_path': 'url',
            'snippet_path': 'snippet',
        },
        'timeout_sec': 5.0,
    }
    backend = ws.GenericHttpBackend(prov)
    results = backend.search('hello world', max_results=2)
    assert len(results) == 2
    assert results[0].title == 'T1'
    assert results[1].url == 'https://e.com/2'
    # 校验占位符已被渲染
    assert 'q=hello+world' in captured['url'] or 'q=hello%20world' in captured['url']
    assert captured['headers']['X-Key'] == 'KKK'


def test_generic_backend_missing_api_key(monkeypatch):
    """{{api_key}} 占位但 api_key 留空时，预检直接报错。"""
    monkeypatch.setattr(
        ws.GenericHttpBackend, '_do_http',
        staticmethod(lambda *a, **kw: '{}'),
    )
    prov = {
        'id': 'needs_key',
        'name': 'Needs Key',
        'enabled': True,
        'method': 'GET',
        'url': 'https://api.example.com',
        'params': {},
        'headers': {'X-Key': '{{api_key}}'},
        'body_json': {},
        'api_key': '',
        'extra': {},
        'response': {
            'items_path': 'items', 'title_path': 'title',
            'url_path': 'url', 'snippet_path': 'snippet',
        },
        'timeout_sec': 5.0,
    }
    backend = ws.GenericHttpBackend(prov)
    with pytest.raises(ws.SearchError) as excinfo:
        backend.search('q')
    assert 'API Key' in str(excinfo.value)


def test_generic_backend_post_body(monkeypatch):
    """POST + body_json 占位符正确渲染并发出去。"""
    seen = {}

    def fake_do_http(method, url, headers, body_json, timeout):
        seen['method'] = method
        seen['body'] = body_json
        return json.dumps({'results': [
            {'title': 'X', 'url': 'https://x.com', 'content': 'CC'},
        ]})

    monkeypatch.setattr(
        ws.GenericHttpBackend, '_do_http', staticmethod(fake_do_http),
    )
    prov = {
        'id': 'tavily_like',
        'name': 'Tavily-like',
        'enabled': True,
        'method': 'POST',
        'url': 'https://api.example.com/search',
        'params': {},
        'headers': {'Content-Type': 'application/json'},
        'body_json': {
            'api_key': '{{api_key}}',
            'query': '{{query}}',
            'max_results': '{{n_int}}',
        },
        'api_key': 'tav-key',
        'extra': {},
        'response': {
            'items_path': 'results',
            'title_path': 'title',
            'url_path': 'url',
            'snippet_path': 'content',
        },
        'timeout_sec': 5.0,
    }
    out = ws.GenericHttpBackend(prov).search('foo', max_results=3)
    assert seen['method'] == 'POST'
    assert seen['body'] == {
        'api_key': 'tav-key',
        'query': 'foo',
        'max_results': 3,             # n_int 是真 int，不是字符串
    }
    assert out and out[0].title == 'X' and out[0].snippet == 'CC'


# ---------------------------------------------------------------------- #
# 4. search() 通过 provider 参数走新路径
# ---------------------------------------------------------------------- #
def test_search_routes_to_provider(monkeypatch):
    """传入 provider 时，应走 GenericHttpBackend，而不是旧 backend 字符串。"""
    monkeypatch.setattr(
        ws.GenericHttpBackend, '_do_http',
        staticmethod(lambda *a, **kw: json.dumps({
            'items': [{'title': 'Z', 'link': 'https://z.com', 'snippet': 'zz'}],
        })),
    )
    prov = {
        'id': 'g_cse',
        'name': 'Google CSE',
        'enabled': True,
        'method': 'GET',
        'url': 'https://www.googleapis.com/customsearch/v1',
        'params': {
            'q': '{{query}}', 'num': '{{n}}',
            'key': '{{api_key}}', 'cx': '{{extra.cx}}',
        },
        'headers': {},
        'body_json': {},
        'api_key': 'API-KEY',
        'extra': {'cx': 'CX-ID'},
        'response': {
            'items_path': 'items',
            'title_path': 'title',
            'url_path': 'link',
            'snippet_path': 'snippet',
        },
        'timeout_sec': 5.0,
    }
    results = ws.search(
        'hello', max_results=1, provider=prov, use_cache=False,
    )
    assert len(results) == 1
    assert results[0].title == 'Z'
