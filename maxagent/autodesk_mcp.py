#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Autodesk Knowledge MCP 客户端（零外部依赖）。

目标
====
- 对接 Autodesk 官方 Knowledge MCP 端点：
  ``https://developer.api.autodesk.com/knowledge/public/v1/mcp``
- 只做与 3ds Max 相关的知识/文档检索。将本模块暴露的工具注入到 LLM
  的 function-calling 工具集，让模型在需要"权威、准确、最新"的 3ds Max
  文档时优先调用这里而不是通用联网搜索。

为什么用 urllib 手写而不是引第三方 mcp SDK？
============================================
项目全局约束：3ds Max 内嵌 Python 不允许 pip 装包，所以运行时严禁
出现任何非标准库依赖。MCP 的 Streamable HTTP 传输本质上就是
JSON-RPC over HTTP + SSE，完全可以用 ``urllib`` + 一点 SSE 解析
覆盖到 ``initialize / tools/list / tools/call`` 这三个我们真正会用
到的方法。

传输协议要点
============
1. 单一 URL 承载全部 JSON-RPC；HTTP method 恒为 ``POST``。
2. 请求 header: ``Content-Type: application/json``、
   ``Accept: application/json, text/event-stream``。
3. 服务端会返回：
   - ``Content-Type: application/json``：单条 JSON-RPC 响应
   - ``Content-Type: text/event-stream``：SSE 帧流，每帧 ``data:`` 后跟
     一段 JSON-RPC 消息；我们只需要 ``id`` 与 request 匹配的那条。
4. 首次 ``initialize`` 响应可能带 ``Mcp-Session-Id`` header；后续所有
   请求必须回传这个 header。

3ds Max 作用域约束
==================
Autodesk Knowledge MCP 的知识面横跨 Maya / 3ds Max / Revit / AutoCAD 等
多条产品线。为了确保 LLM 拿到的答案落在 3ds Max 上下文，本模块在
调用 tools/call 时会：
- 把用户的自然语言 query 前缀强制拼上 "3ds Max"
- 若服务端工具支持 ``product`` / ``filter`` / ``scope`` 之类结构化
  参数，也一并注入 "3dsMax" 值（服务端未识别时会被忽略，不影响主流程）
"""

from __future__ import absolute_import
from __future__ import print_function

import json
import threading
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from .logger import get_logger


logger = get_logger(__name__)


# Autodesk 官方 Knowledge MCP 端点
DEFAULT_MCP_URL = 'https://developer.api.autodesk.com/knowledge/public/v1/mcp'

# 默认作用域：所有查询都拼上这个前缀，保证结果聚焦 3ds Max
PRODUCT_SCOPE = '3ds Max'
PRODUCT_SCOPE_ID = '3dsMax'

# 默认 locale：Autodesk 帮助中心使用的语言代码（ENU/CHS/JPN/DEU/FRA/...）
# 端点要求必须传 locale，否则会拒绝或返回空。ENU 覆盖面最广。
DEFAULT_LOCALE = 'ENU'

# Autodesk 常见 locale 码 → BCP-47 兜底映射（有的字段要 "en-US" 而不是 "ENU"）
_LOCALE_BCP47 = {
    'ENU': 'en-US',
    'CHS': 'zh-CN',
    'CHT': 'zh-TW',
    'JPN': 'ja-JP',
    'KOR': 'ko-KR',
    'DEU': 'de-DE',
    'FRA': 'fr-FR',
    'ESP': 'es-ES',
    'ITA': 'it-IT',
    'PTB': 'pt-BR',
    'RUS': 'ru-RU',
    'PLK': 'pl-PL',
    'CSY': 'cs-CZ',
    'HUN': 'hu-HU',
}

# 单次 HTTP 请求超时（秒）
DEFAULT_HTTP_TIMEOUT = 15.0

# MCP 协议版本（跟 Streamable HTTP 现行规范一致）
MCP_PROTOCOL_VERSION = '2025-03-26'

# 客户端标识（UA & MCP client info）
_CLIENT_NAME = 'maxagent'
_CLIENT_VERSION = '1.0'


class MCPError(Exception):
    """MCP 调用出错时抛出的异常。"""


class _MCPClient(object):
    """极简的 MCP over Streamable HTTP 客户端。

    只覆盖 3 个方法：``initialize`` / ``tools/list`` / ``tools/call``，
    足以满足"让 LLM 调 Autodesk 官方工具查 3ds Max 文档"的场景。

    并发安全：所有对内部状态（session id / 递增 request id / 缓存）
    的访问都由 ``self._lock`` 保护，可从任意线程/工具调用。
    """

    def __init__(self, url=DEFAULT_MCP_URL, timeout=DEFAULT_HTTP_TIMEOUT):
        # type: (str, float) -> None
        self._url = url
        self._timeout = float(timeout)
        self._lock = threading.RLock()
        self._session_id = None    # type: Optional[str]
        self._initialized = False
        self._req_id = 0
        # tools/list 结果缓存：{tool_name: raw_tool_dict}
        self._tools_cache = None   # type: Optional[Dict[str, Dict[str, Any]]]

    # ---------- 内部 HTTP / JSON-RPC ----------

    def _next_id(self):
        # type: () -> int
        with self._lock:
            self._req_id += 1
            return self._req_id

    def _post(self, payload, extra_headers=None):
        # type: (Dict[str, Any], Optional[Dict[str, str]]) -> Tuple[Dict[str, str], bytes]
        """发送 JSON-RPC 请求，返回 ``(响应 headers, 原始响应 body)``。"""
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json, text/event-stream',
            'User-Agent': '{}/{}'.format(_CLIENT_NAME, _CLIENT_VERSION),
        }
        if self._session_id:
            headers['Mcp-Session-Id'] = self._session_id
        if extra_headers:
            headers.update(extra_headers)
        req = urllib.request.Request(self._url, data=data, headers=headers, method='POST')
        try:
            resp = urllib.request.urlopen(req, timeout=self._timeout)
        except urllib.error.HTTPError as exc:
            body = b''
            try:
                body = exc.read()
            except Exception:  # pylint: disable=broad-except
                pass
            raise MCPError(
                'Autodesk MCP HTTP {}: {}'.format(exc.code, body.decode('utf-8', errors='replace'))
            ) from exc
        except urllib.error.URLError as exc:
            raise MCPError('Autodesk MCP 网络错误: {}'.format(exc.reason)) from exc
        except Exception as exc:  # pylint: disable=broad-except
            raise MCPError('Autodesk MCP 请求失败: {}'.format(exc)) from exc

        with resp:
            body_bytes = resp.read()
            resp_headers = {k: v for k, v in resp.headers.items()}
        return resp_headers, body_bytes

    @staticmethod
    def _parse_response(resp_headers, body_bytes, want_id):
        # type: (Dict[str, str], bytes, int) -> Dict[str, Any]
        """把 JSON 或 SSE 帧解析成 JSON-RPC 响应对象。

        关键点：SSE 流中会掺杂服务端下发的通知（不含 ``id``）与与本次请求
        对应的响应（``id`` 与 ``want_id`` 一致）。我们只关心后者，其它帧
        忽略即可。
        """
        content_type = ''
        for k, v in resp_headers.items():
            if k.lower() == 'content-type':
                content_type = (v or '').lower()
                break

        text = body_bytes.decode('utf-8', errors='replace')

        if 'text/event-stream' in content_type:
            # SSE：按空行分帧，取 data: 拼起来解析 JSON
            frames = _iter_sse_events(text)
            for frame in frames:
                obj = _try_json(frame)
                if not isinstance(obj, dict):
                    continue
                if obj.get('id') == want_id and ('result' in obj or 'error' in obj):
                    return obj
            raise MCPError('Autodesk MCP 未在 SSE 流中返回 id={} 的响应'.format(want_id))

        # 默认按单条 JSON-RPC 响应解析
        obj = _try_json(text)
        if not isinstance(obj, dict):
            raise MCPError('Autodesk MCP 响应不是合法 JSON: {!r}'.format(text[:200]))
        return obj

    def _rpc_call(self, method, params=None):
        # type: (str, Optional[Dict[str, Any]]) -> Any
        """发一次 request/response 语义的 JSON-RPC 调用，返回 ``result``。"""
        rid = self._next_id()
        payload = {'jsonrpc': '2.0', 'id': rid, 'method': method}
        if params is not None:
            payload['params'] = params
        resp_headers, body = self._post(payload)
        # 首次 initialize 响应带的 session id 需要保留
        for k, v in resp_headers.items():
            if k.lower() == 'mcp-session-id' and v:
                with self._lock:
                    self._session_id = v
        obj = self._parse_response(resp_headers, body, rid)
        if 'error' in obj:
            err = obj['error'] or {}
            raise MCPError(
                'Autodesk MCP 返回错误 [{}] {}'.format(err.get('code'), err.get('message'))
            )
        return obj.get('result')

    def _rpc_notify(self, method, params=None):
        # type: (str, Optional[Dict[str, Any]]) -> None
        """发一条 notification（无 id / 无响应）。"""
        payload = {'jsonrpc': '2.0', 'method': method}
        if params is not None:
            payload['params'] = params
        try:
            self._post(payload)
        except MCPError as exc:
            # notification 允许失败，仅打日志
            logger.debug('MCP notify %s 失败: %s', method, exc)

    # ---------- 会话生命周期 ----------

    def _ensure_initialized(self):
        # type: () -> None
        with self._lock:
            if self._initialized:
                return
            result = self._rpc_call('initialize', {
                'protocolVersion': MCP_PROTOCOL_VERSION,
                'capabilities': {},
                'clientInfo': {
                    'name': _CLIENT_NAME,
                    'version': _CLIENT_VERSION,
                },
            })
            logger.debug('Autodesk MCP initialize result: %s', result)
            # 按协议要求发一条 initialized notification
            self._rpc_notify('notifications/initialized')
            self._initialized = True

    def list_tools(self, use_cache=True):
        # type: (bool) -> Dict[str, Dict[str, Any]]
        """返回 ``{tool_name: tool_dict}`` 结构。"""
        with self._lock:
            if use_cache and self._tools_cache is not None:
                return dict(self._tools_cache)
        self._ensure_initialized()
        result = self._rpc_call('tools/list')
        tools = {}
        if isinstance(result, dict):
            for item in result.get('tools') or []:
                if isinstance(item, dict) and item.get('name'):
                    tools[item['name']] = item
        with self._lock:
            self._tools_cache = tools
        return dict(tools)

    def call_tool(self, name, arguments):
        # type: (str, Dict[str, Any]) -> Any
        """调用远端工具，返回 ``result`` 原文。"""
        self._ensure_initialized()
        return self._rpc_call('tools/call', {
            'name': name,
            'arguments': arguments or {},
        })


# 单例：整个进程共享一个 MCP 会话，避免重复 initialize
_singleton = None            # type: Optional[_MCPClient]
_singleton_lock = threading.Lock()


def get_client(url=DEFAULT_MCP_URL, timeout=DEFAULT_HTTP_TIMEOUT):
    # type: (str, float) -> _MCPClient
    """获取（首次调用时创建）进程级单例客户端。"""
    global _singleton  # pylint: disable=global-statement
    with _singleton_lock:
        if _singleton is None or _singleton._url != url:  # pylint: disable=protected-access
            _singleton = _MCPClient(url=url, timeout=timeout)
        return _singleton


def reset_client():
    # type: () -> None
    """重置单例（测试用）。"""
    global _singleton  # pylint: disable=global-statement
    with _singleton_lock:
        _singleton = None


# ---------- 辅助：搜索用的高层封装 ----------


# 猜测远端可能提供的"搜索类工具"的名字关键字。Autodesk 迭代过几版命名，
# 这里按优先级从最具体到最泛用列出，命中第一个可用工具即使用。
_SEARCH_TOOL_HINTS = (
    'search_documentation',
    'search_knowledge',
    'search_docs',
    'knowledge_search',
    'search',
    'query',
    'ask',
)


def pick_search_tool(client):
    # type: (_MCPClient) -> Optional[Dict[str, Any]]
    """从远端 tools/list 里挑一个"看起来像搜索"的工具。"""
    tools = client.list_tools()
    if not tools:
        return None
    lowered = {k.lower(): v for k, v in tools.items()}
    for hint in _SEARCH_TOOL_HINTS:
        if hint in lowered:
            return lowered[hint]
    # 兜底：找 name 里含 search / query / ask 的
    for k, v in tools.items():
        low = k.lower()
        if 'search' in low or 'query' in low or 'ask' in low:
            return v
    # 再兜底：返回第一个
    return next(iter(tools.values()))


def _augment_arguments_for_max_scope(schema, query, locale=DEFAULT_LOCALE, limit=None):
    # type: (Optional[Dict[str, Any]], str, str, Optional[int]) -> Dict[str, Any]
    """按远端工具的 inputSchema 尽量把 query + 3ds Max 作用域 + locale + limit 塞进合适字段。

    - ``schema`` 一般形如 ``{"type": "object", "properties": {...}, "required": [...]}``
    - 常见 query 字段名：query / q / question / text / prompt
    - 常见 scope 字段名：product / products / filter / scope / domain
    - 常见 locale 字段名：locale / language / lang / hl
    - 常见 limit 字段名：limit / top_k / topK / max_results / maxResults / count / size / n
    """
    args = {}   # type: Dict[str, Any]
    props = {}  # type: Dict[str, Any]
    required = []  # type: List[str]
    if isinstance(schema, dict):
        raw_props = schema.get('properties')
        if isinstance(raw_props, dict):
            props = raw_props
        raw_required = schema.get('required')
        if isinstance(raw_required, list):
            required = [x for x in raw_required if isinstance(x, str)]

    query_field = _find_field(props, ('query', 'q', 'question', 'text', 'prompt', 'input'))
    if query_field is None:
        # 服务端未声明 schema 时，兜底用最常见字段名
        query_field = 'query'
    args[query_field] = '{}: {}'.format(PRODUCT_SCOPE, query.strip())

    product_field = _find_field(props, ('product', 'products', 'productLine', 'application'))
    if product_field is not None:
        # 有的服务端要 array，有的要 string。按 schema 类型自适应
        prop_def = props.get(product_field) or {}
        if _prop_wants_array(prop_def):
            args[product_field] = [PRODUCT_SCOPE_ID]
        else:
            args[product_field] = PRODUCT_SCOPE_ID

    filter_field = _find_field(props, ('filter', 'filters', 'scope', 'domain'))
    if filter_field is not None and filter_field not in args:
        prop_def = props.get(filter_field) or {}
        if _prop_wants_array(prop_def):
            args[filter_field] = [PRODUCT_SCOPE_ID]
        elif (prop_def.get('type') or '').lower() == 'object':
            args[filter_field] = {'product': PRODUCT_SCOPE_ID}
        else:
            args[filter_field] = PRODUCT_SCOPE_ID

    # locale 注入：远端强制要求；schema 未声明时也无脑传 "locale" 兜底，
    # 服务端不认识会忽略。若声明了 language/lang/hl 则同时填入。
    loc = (locale or DEFAULT_LOCALE).strip() or DEFAULT_LOCALE
    loc_bcp47 = _LOCALE_BCP47.get(loc.upper(), loc)
    locale_field = _find_field(props, ('locale', 'language', 'lang', 'hl'))
    if locale_field is not None:
        prop_def = props.get(locale_field) or {}
        # enum 里明确列了值就按 enum 匹配
        enum_vals = prop_def.get('enum') if isinstance(prop_def, dict) else None
        if isinstance(enum_vals, list) and enum_vals:
            args[locale_field] = _match_locale_in_enum(loc, loc_bcp47, enum_vals)
        else:
            # BCP-47 优先 for language/lang/hl；Autodesk locale 码优先 for locale
            if locale_field.lower() == 'locale':
                args[locale_field] = loc
            else:
                args[locale_field] = loc_bcp47
    else:
        # schema 未声明也兜底传一份
        args['locale'] = loc

    # 若 required 里还有未填的字段，尝试用最保守的默认值兜底
    for name in required:
        if name in args:
            continue
        prop_def = props.get(name) or {}
        default = prop_def.get('default')
        if default is not None:
            args[name] = default

    # limit 注入：远端返回体上限约 16KB，条数越少单条内容越完整
    if limit is not None:
        try:
            n = int(limit)
        except (TypeError, ValueError):
            n = 0
        if n > 0:
            limit_field = _find_field(props, (
                'limit', 'top_k', 'topK', 'topk',
                'max_results', 'maxResults', 'max_result', 'maxResult',
                'count', 'size', 'n', 'k',
            ))
            if limit_field is not None:
                args[limit_field] = n
            # schema 未声明时不兜底传 limit，避免服务端 400

    return args


def _match_locale_in_enum(loc, loc_bcp47, enum_vals):
    # type: (str, str, List[Any]) -> Any
    """在 enum 候选里挑一个和 loc 最匹配的值；找不到就退化到第一个含 en 的。"""
    lowered = {str(v).lower(): v for v in enum_vals}
    for cand in (loc, loc_bcp47, loc.upper(), loc.lower()):
        if cand and cand.lower() in lowered:
            return lowered[cand.lower()]
    for k, v in lowered.items():
        if 'en' in k:
            return v
    return enum_vals[0]


def _find_field(props, candidates):
    # type: (Dict[str, Any], Tuple[str, ...]) -> Optional[str]
    if not props:
        return None
    lowered = {k.lower(): k for k in props.keys()}
    for cand in candidates:
        if cand.lower() in lowered:
            return lowered[cand.lower()]
    return None


def _prop_wants_array(prop_def):
    # type: (Dict[str, Any]) -> bool
    t = prop_def.get('type')
    if isinstance(t, list):
        return 'array' in [x.lower() for x in t if isinstance(x, str)]
    if isinstance(t, str):
        return t.lower() == 'array'
    return False


def _extract_text_from_result(result):
    # type: (Any) -> str
    """把 tools/call 返回的复合结构（content 数组）浓缩成纯文本。

    MCP tools/call 结果通常形如::

        {"content": [{"type": "text", "text": "..."}, {"type": "resource", ...}], "isError": false}
    """
    if isinstance(result, str):
        return result
    if not isinstance(result, dict):
        return json.dumps(result, ensure_ascii=False)
    content = result.get('content')
    parts = []  # type: List[str]
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get('type') == 'text' and isinstance(item.get('text'), str):
                parts.append(item['text'])
            elif isinstance(item.get('text'), str):
                parts.append(item['text'])
            elif isinstance(item.get('resource'), dict):
                res = item['resource']
                uri = res.get('uri') or ''
                text = res.get('text') or ''
                if uri:
                    parts.append('[{}]\n{}'.format(uri, text))
                elif text:
                    parts.append(text)
    if not parts:
        # 兜底：把整个 result 序列化返回，交给 LLM 自己解读
        return json.dumps(result, ensure_ascii=False)
    return '\n\n'.join(p for p in parts if p)


def search_max_knowledge(query, timeout=DEFAULT_HTTP_TIMEOUT, locale=DEFAULT_LOCALE, limit=None):
    # type: (str, float, str, Optional[int]) -> Dict[str, Any]
    """在 Autodesk 官方知识库检索 3ds Max 相关内容。

    :param query: 用户自然语言查询
    :param timeout: 单次 HTTP 超时
    :param locale: Autodesk locale 码（ENU/CHS/JPN/DEU/FRA/...），默认 ENU
    :param limit: 期望返回的结果条数（服务端返回体上限 ~16KB，条数越少单条越完整）
    :returns: ``{"ok": bool, "tool": str, "text": str, "raw": Any, "error": str?}``
    """
    q = (query or '').strip()
    if not q:
        return {'ok': False, 'error': 'query 不能为空'}
    client = get_client(timeout=timeout)
    try:
        tool = pick_search_tool(client)
    except MCPError as exc:
        return {'ok': False, 'error': str(exc)}
    if tool is None:
        return {'ok': False, 'error': 'Autodesk MCP 未暴露任何可用工具'}
    tool_name = tool.get('name')
    args = _augment_arguments_for_max_scope(
        tool.get('inputSchema'), q, locale=locale, limit=limit,
    )
    try:
        result = client.call_tool(tool_name, args)
    except MCPError as exc:
        return {'ok': False, 'tool': tool_name, 'error': str(exc)}
    text = _extract_text_from_result(result)
    return {
        'ok': True,
        'tool': tool_name,
        'query': q,
        'scope': PRODUCT_SCOPE,
        'locale': locale,
        'limit': limit,
        'text': text,
        'raw': result,
    }


# ---------- 内部辅助 ----------


def _try_json(text):
    # type: (str) -> Any
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def _iter_sse_events(text):
    # type: (str) -> List[str]
    """把 SSE 响应体拆成一系列 ``data:`` 拼接文本（每个事件一条）。"""
    events = []  # type: List[str]
    buf = []     # type: List[str]
    for raw_line in text.splitlines():
        line = raw_line.rstrip('\r')
        if line == '':
            # 空行 = 一个事件结束
            if buf:
                events.append('\n'.join(buf))
                buf = []
            continue
        if line.startswith(':'):
            # 注释行，忽略
            continue
        if line.startswith('data:'):
            buf.append(line[len('data:'):].lstrip())
        # 其它字段（event: / id: / retry:）当前流程用不到，忽略
    if buf:
        events.append('\n'.join(buf))
    return events


__all__ = [
    'DEFAULT_MCP_URL',
    'PRODUCT_SCOPE',
    'DEFAULT_LOCALE',
    'MCPError',
    'get_client',
    'reset_client',
    'pick_search_tool',
    'search_max_knowledge',
]
