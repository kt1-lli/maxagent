#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""maxagent.autodesk_mcp 测试。

用 monkeypatch 拦截 ``urllib.request.urlopen``，模拟 Autodesk MCP 端点的
JSON / SSE 响应，验证：
- initialize + notifications/initialized 会话建立
- tools/list 结果被缓存
- tools/call 时 query 会被强制加上 3ds Max 前缀
- tools/call 会按 inputSchema 命中 product / filter 字段
- SSE 分帧解析
"""

from __future__ import absolute_import
from __future__ import print_function

import io
import json

import pytest

from maxagent import autodesk_mcp


class _FakeResponse:
    """伪造 urlopen 返回的 response 对象。"""

    def __init__(self, body, headers=None):
        self._body = body if isinstance(body, bytes) else body.encode('utf-8')
        self.headers = headers or {'Content-Type': 'application/json'}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._body


class _MockTransport:
    """记录调用序列 + 按顺序吐响应。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, req, timeout=None):
        payload = json.loads(req.data.decode('utf-8'))
        self.calls.append({
            'method': payload.get('method'),
            'params': payload.get('params'),
            'headers': dict(req.headers),
        })
        if not self.responses:
            raise AssertionError('未预置响应，实际 method={}'.format(payload.get('method')))
        item = self.responses.pop(0)
        body, headers = item if isinstance(item, tuple) else (item, None)
        return _FakeResponse(body, headers)


@pytest.fixture(autouse=True)
def _reset_client():
    autodesk_mcp.reset_client()
    yield
    autodesk_mcp.reset_client()


def _rpc_result(rid, result):
    return json.dumps({'jsonrpc': '2.0', 'id': rid, 'result': result}).encode('utf-8')


def test_initialize_and_list_tools(monkeypatch):
    tools_payload = {
        'tools': [
            {
                'name': 'search_documentation',
                'description': 'Search Autodesk docs',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'query': {'type': 'string'},
                        'product': {'type': 'string'},
                    },
                    'required': ['query'],
                },
            },
        ],
    }
    responses = [
        # initialize (id=1) -> 附带 session id
        (_rpc_result(1, {'protocolVersion': '2025-03-26'}),
         {'Content-Type': 'application/json', 'Mcp-Session-Id': 'sid-abc'}),
        # notifications/initialized（无返回体也可）
        (b'', {'Content-Type': 'application/json'}),
        # tools/list (id=2)
        (_rpc_result(2, tools_payload), {'Content-Type': 'application/json'}),
    ]
    transport = _MockTransport(responses)
    monkeypatch.setattr(autodesk_mcp.urllib.request, 'urlopen', transport)

    client = autodesk_mcp.get_client()
    tools = client.list_tools()
    assert 'search_documentation' in tools

    methods = [c['method'] for c in transport.calls]
    assert methods == ['initialize', 'notifications/initialized', 'tools/list']

    # 后两次请求都必须回传 session id（urllib 会把 header 名规整成 Title-Case）
    def _sid(headers):
        for k, v in headers.items():
            if k.lower() == 'mcp-session-id':
                return v
        return None

    assert _sid(transport.calls[1]['headers']) == 'sid-abc'
    assert _sid(transport.calls[2]['headers']) == 'sid-abc'


def test_search_max_knowledge_forces_max_scope(monkeypatch):
    tools_payload = {
        'tools': [
            {
                'name': 'search_documentation',
                'description': 'Search Autodesk docs',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'query': {'type': 'string'},
                        'product': {'type': 'string'},
                    },
                },
            },
        ],
    }
    call_result = {
        'content': [
            {'type': 'text', 'text': 'The Bend modifier bends geometry along an axis.'},
        ],
        'isError': False,
    }
    responses = [
        (_rpc_result(1, {'protocolVersion': '2025-03-26'}), None),
        (b'', None),
        (_rpc_result(2, tools_payload), None),
        (_rpc_result(3, call_result), None),
    ]
    transport = _MockTransport(responses)
    monkeypatch.setattr(autodesk_mcp.urllib.request, 'urlopen', transport)

    out = autodesk_mcp.search_max_knowledge('bend modifier')
    assert out['ok'] is True
    assert out['tool'] == 'search_documentation'
    assert 'Bend modifier' in out['text']

    # 校验 tools/call 参数：query 前缀 + product 字段被填充
    call_payload = transport.calls[-1]['params']
    assert call_payload['name'] == 'search_documentation'
    assert call_payload['arguments']['query'].startswith('3ds Max: ')
    assert call_payload['arguments']['product'] == '3dsMax'


def test_search_max_knowledge_array_filter(monkeypatch):
    tools_payload = {
        'tools': [
            {
                'name': 'search',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'q': {'type': 'string'},
                        'products': {'type': 'array', 'items': {'type': 'string'}},
                    },
                },
            },
        ],
    }
    responses = [
        (_rpc_result(1, {}), None),
        (b'', None),
        (_rpc_result(2, tools_payload), None),
        (_rpc_result(3, {'content': [{'type': 'text', 'text': 'ok'}]}), None),
    ]
    transport = _MockTransport(responses)
    monkeypatch.setattr(autodesk_mcp.urllib.request, 'urlopen', transport)

    autodesk_mcp.search_max_knowledge('macroScript quickstart')
    call_payload = transport.calls[-1]['params']
    args = call_payload['arguments']
    assert args['q'].startswith('3ds Max:')
    assert args['products'] == ['3dsMax']


def test_sse_response_parsing(monkeypatch):
    """SSE 响应也能正确解出目标 id 的响应。"""
    sse_body = (
        'event: message\n'
        'data: {"jsonrpc":"2.0","method":"notifications/log","params":{"msg":"hi"}}\n'
        '\n'
        'data: {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-03-26"}}\n'
        '\n'
    )
    responses = [
        (sse_body.encode('utf-8'), {'Content-Type': 'text/event-stream'}),
        (b'', None),
        (_rpc_result(2, {'tools': []}), None),
    ]
    transport = _MockTransport(responses)
    monkeypatch.setattr(autodesk_mcp.urllib.request, 'urlopen', transport)

    client = autodesk_mcp.get_client()
    tools = client.list_tools()
    assert tools == {}


def test_search_empty_query_short_circuits(monkeypatch):
    called = {'urlopen': 0}

    def _boom(*a, **k):
        called['urlopen'] += 1
        raise AssertionError('空 query 不应发起 HTTP 请求')

    monkeypatch.setattr(autodesk_mcp.urllib.request, 'urlopen', _boom)
    out = autodesk_mcp.search_max_knowledge('')
    assert out['ok'] is False
    assert called['urlopen'] == 0


def test_tool_registered_in_registry():
    """确认 autodesk_max_docs 已注册到 tools registry。"""
    from maxagent import tools as tools_pkg

    tools_pkg.load_all_tools(include_escape_hatch=False, load_user_tools=False)
    spec = tools_pkg.get_tool('autodesk_max_docs')
    assert spec is not None
    assert spec.category == 'web'
    assert spec.run_on_main_thread is False
    assert '3ds Max' in spec.description
