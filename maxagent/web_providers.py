#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""联网搜索 Provider 配置管理。

设计目标
========
1. **数据驱动**：每个 Provider 是一份"HTTP 调用模板"，覆盖大多数搜索 API
   的请求/响应结构（query/header/body 模板 + JSON 路径解析），新增搜索源
   只需要在 UI 里填一份配置，**不需要改代码**。
2. **零外部依赖**：纯 ``urllib`` + 标准 ``json``，不依赖 jsonpath 等第三方包。
3. **与 ``AppConfig`` 解耦**：providers 单独存到 ``_userdata/web_providers.json``，
   AppConfig 仅引用激活的 provider id（向前兼容老的 ``web_search_backend``
   字符串字段，会自动映射到内置 provider）。
4. **内置预设可重新生成**：用户误删后通过"恢复内置预设"按钮一键回填。

文件结构
========
``_userdata/web_providers.json``::

    {
        "version": 1,
        "active_id": "duckduckgo",
        "providers": [
            { "id": "...", "name": "...", "url": "...", ... },
            ...
        ]
    }

Provider 字段约定
=================
- ``id``: 全局唯一标识，``[a-zA-Z0-9_-]+``
- ``name``: UI 上显示的名字
- ``builtin``: True 时不可删除，仅可关闭/复制
- ``enabled``: 是否可用
- ``method``: ``GET`` / ``POST``
- ``url``: 请求 URL（不含 query string）
- ``params``: 字典，进入 URL query string；值支持 ``{{query}} {{n}} {{api_key}}
  {{extra.foo}}`` 占位
- ``headers``: 请求头字典；同样支持占位
- ``body_json``: POST JSON body 字典；同样支持占位
- ``api_key``: 用户填的 API Key（写盘时按 base64 弱混淆）
- ``extra``: provider 自定义字段，给 ``{{extra.xxx}}`` 占位用
- ``response.items_path``: JSON 路径（点号分隔）取结果列表
- ``response.title_path``: 单个 item 内取标题
- ``response.url_path``: 单个 item 内取链接
- ``response.snippet_path``: 单个 item 内取摘要
- ``response.html_scrape``: True 时把响应当 HTML 抓取（DDG 这种无 JSON 的）
- ``timeout_sec``: 默认 8 秒
"""

from __future__ import absolute_import
from __future__ import print_function

import base64
import json
import os
import re
from copy import deepcopy
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from .config import get_config_dir
from .logger import get_logger


logger = get_logger(__name__)


PROVIDERS_VERSION = 1

# Provider id 必须满足这个正则；UI 表单也会校验
ID_PATTERN = re.compile(r'^[a-zA-Z][a-zA-Z0-9_-]*$')


# ---------------------------------------------------------------------- #
# 内置预设
# ---------------------------------------------------------------------- #
# 设计原则：
# 1. DDG 必须保留——免 Key 兜底，主 UI 永远能用。
# 2. 其他几个示例都填好 url/params/response 路径，用户只需要填 api_key
#    （Bing/Google CSE/Brave/Tavily/博查）。
# 3. ``builtin=True`` 防止误删；用户想清单可以先 ``enabled=False``。
BUILTIN_PROVIDERS = [
    {
        'id': 'duckduckgo',
        'name': 'DuckDuckGo（免费，零依赖）',
        'builtin': True,
        'enabled': True,
        'method': 'GET',
        'url': 'https://html.duckduckgo.com/html/',
        'params': {'q': '{{query}}', 'kl': 'wt-wt'},
        'headers': {},
        'body_json': {},
        'api_key': '',
        'extra': {},
        'response': {
            'html_scrape': True,
            'items_path': '',
            'title_path': '',
            'url_path': '',
            'snippet_path': '',
        },
        'timeout_sec': 8.0,
    },
    {
        'id': 'bing_api',
        'name': 'Bing Search API（需 Key）',
        'builtin': True,
        'enabled': True,
        'method': 'GET',
        'url': 'https://api.bing.microsoft.com/v7.0/search',
        'params': {'q': '{{query}}', 'count': '{{n}}'},
        'headers': {'Ocp-Apim-Subscription-Key': '{{api_key}}'},
        'body_json': {},
        'api_key': '',
        'extra': {},
        'response': {
            'html_scrape': False,
            'items_path': 'webPages.value',
            'title_path': 'name',
            'url_path': 'url',
            'snippet_path': 'snippet',
        },
        'timeout_sec': 8.0,
    },
    {
        'id': 'google_cse',
        'name': 'Google Custom Search（需 Key + cx）',
        'builtin': True,
        'enabled': True,
        'method': 'GET',
        'url': 'https://www.googleapis.com/customsearch/v1',
        'params': {
            'q': '{{query}}',
            'num': '{{n}}',
            'key': '{{api_key}}',
            'cx': '{{extra.cx}}',
        },
        'headers': {},
        'body_json': {},
        'api_key': '',
        'extra': {'cx': ''},
        'response': {
            'html_scrape': False,
            'items_path': 'items',
            'title_path': 'title',
            'url_path': 'link',
            'snippet_path': 'snippet',
        },
        'timeout_sec': 8.0,
    },
    {
        'id': 'brave',
        'name': 'Brave Search（需 Key）',
        'builtin': True,
        'enabled': True,
        'method': 'GET',
        'url': 'https://api.search.brave.com/res/v1/web/search',
        'params': {'q': '{{query}}', 'count': '{{n}}'},
        'headers': {'X-Subscription-Token': '{{api_key}}'},
        'body_json': {},
        'api_key': '',
        'extra': {},
        'response': {
            'html_scrape': False,
            'items_path': 'web.results',
            'title_path': 'title',
            'url_path': 'url',
            'snippet_path': 'description',
        },
        'timeout_sec': 8.0,
    },
    {
        'id': 'tavily',
        'name': 'Tavily AI Search（需 Key）',
        'builtin': True,
        'enabled': True,
        'method': 'POST',
        'url': 'https://api.tavily.com/search',
        'params': {},
        'headers': {'Content-Type': 'application/json'},
        'body_json': {
            'api_key': '{{api_key}}',
            'query': '{{query}}',
            'max_results': '{{n_int}}',
            'search_depth': 'basic',
        },
        'api_key': '',
        'extra': {},
        'response': {
            'html_scrape': False,
            'items_path': 'results',
            'title_path': 'title',
            'url_path': 'url',
            'snippet_path': 'content',
        },
        'timeout_sec': 12.0,
    },
    {
        'id': 'bocha',
        'name': '博查 AI 搜索（中文，需 Key）',
        'builtin': True,
        'enabled': False,
        'method': 'POST',
        'url': 'https://api.bochaai.com/v1/web-search',
        'params': {},
        'headers': {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer {{api_key}}',
        },
        'body_json': {
            'query': '{{query}}',
            'count': '{{n_int}}',
            'summary': True,
        },
        'api_key': '',
        'extra': {},
        'response': {
            'html_scrape': False,
            'items_path': 'data.webPages.value',
            'title_path': 'name',
            'url_path': 'url',
            'snippet_path': 'snippet',
        },
        'timeout_sec': 12.0,
    },
]


# ---------------------------------------------------------------------- #
# 数据校验 / 默认值补齐
# ---------------------------------------------------------------------- #
def _normalize(provider):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    """补齐缺失字段，返回新副本；不修改入参。"""
    p = deepcopy(provider)
    p.setdefault('id', '')
    p.setdefault('name', p.get('id') or '未命名')
    p.setdefault('builtin', False)
    p.setdefault('enabled', True)
    p.setdefault('method', 'GET')
    p.setdefault('url', '')
    p.setdefault('params', {})
    p.setdefault('headers', {})
    p.setdefault('body_json', {})
    p.setdefault('api_key', '')
    p.setdefault('extra', {})
    p.setdefault('timeout_sec', 8.0)
    resp = p.setdefault('response', {})
    resp.setdefault('html_scrape', False)
    resp.setdefault('items_path', '')
    resp.setdefault('title_path', '')
    resp.setdefault('url_path', '')
    resp.setdefault('snippet_path', '')
    return p


def validate_id(provider_id):
    # type: (str) -> bool
    """校验 provider id 合法性。"""
    return bool(provider_id) and ID_PATTERN.match(provider_id) is not None


# ---------------------------------------------------------------------- #
# 持久化
# ---------------------------------------------------------------------- #
def get_providers_path():
    # type: () -> str
    return os.path.join(get_config_dir(), 'web_providers.json')


def _encode_for_disk(provider):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    """写盘前对 ``api_key`` 做 base64 弱混淆，与 LLMProfile 一致。"""
    out = deepcopy(provider)
    key = out.get('api_key') or ''
    if key and not key.startswith('b64:'):
        out['api_key'] = (
            'b64:' + base64.b64encode(key.encode('utf-8')).decode('ascii')
        )
    return out


def _decode_from_disk(provider):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    out = deepcopy(provider)
    key = out.get('api_key') or ''
    if isinstance(key, str) and key.startswith('b64:'):
        try:
            out['api_key'] = base64.b64decode(key[4:]).decode('utf-8')
        except (ValueError, UnicodeDecodeError):
            out['api_key'] = ''
    return out


def _default_data():
    # type: () -> Dict[str, Any]
    return {
        'version': PROVIDERS_VERSION,
        'active_id': BUILTIN_PROVIDERS[0]['id'],
        'providers': [_normalize(p) for p in BUILTIN_PROVIDERS],
    }


def load_providers(path=None):
    # type: (Optional[str]) -> Dict[str, Any]
    """加载 providers 配置；不存在时写入内置预设。

    返回字典含 ``version`` / ``active_id`` / ``providers``。
    """
    p = path or get_providers_path()
    if not os.path.exists(p):
        data = _default_data()
        save_providers(data, path=p)
        return data
    try:
        with open(p, 'r', encoding='utf-8') as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as exc:
        logger.warning('providers 加载失败，使用默认: %s', exc)
        data = _default_data()
        save_providers(data, path=p)
        return data
    # 兼容老版本：缺字段时自动补齐
    raw.setdefault('version', PROVIDERS_VERSION)
    raw.setdefault('providers', [])
    raw['providers'] = [
        _normalize(_decode_from_disk(item)) for item in raw['providers']
    ]
    # 内置预设缺失自动补回（防止用户误删 ddg 之后无可用后端）
    have_ids = {item['id'] for item in raw['providers']}
    for builtin in BUILTIN_PROVIDERS:
        if builtin['id'] not in have_ids:
            raw['providers'].append(_normalize(builtin))
    if not raw.get('active_id'):
        raw['active_id'] = raw['providers'][0]['id'] if raw['providers'] else ''
    return raw


def save_providers(data, path=None):
    # type: (Dict[str, Any], Optional[str]) -> None
    p = path or get_providers_path()
    os.makedirs(os.path.dirname(p) or '.', exist_ok=True)
    out = {
        'version': int(data.get('version') or PROVIDERS_VERSION),
        'active_id': str(data.get('active_id') or ''),
        'providers': [
            _encode_for_disk(_normalize(item))
            for item in data.get('providers', [])
        ],
    }
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    if os.path.exists(p):
        os.replace(tmp, p)
    else:
        os.rename(tmp, p)


# ---------------------------------------------------------------------- #
# 高层 API
# ---------------------------------------------------------------------- #
class ProviderRegistry(object):
    """provider 列表的内存视图 + 持久化包装器。

    设计上模仿 ``ConfigManager``：所有变更类 API 都自动落盘，避免
    外部调用方忘记 save。
    """

    def __init__(self, path=None):
        # type: (Optional[str]) -> None
        self._path = path
        self._data = load_providers(path=path)

    @property
    def data(self):
        # type: () -> Dict[str, Any]
        return self._data

    def list_providers(self):
        # type: () -> List[Dict[str, Any]]
        return list(self._data['providers'])

    def get(self, provider_id):
        # type: (str) -> Optional[Dict[str, Any]]
        for p in self._data['providers']:
            if p['id'] == provider_id:
                return p
        return None

    def get_active(self):
        # type: () -> Optional[Dict[str, Any]]
        return self.get(self._data.get('active_id') or '')

    def set_active(self, provider_id):
        # type: (str) -> None
        if self.get(provider_id) is None:
            raise ValueError('Provider 不存在: {}'.format(provider_id))
        self._data['active_id'] = provider_id
        self._save()

    def upsert(self, provider):
        # type: (Dict[str, Any]) -> None
        p = _normalize(provider)
        if not validate_id(p['id']):
            raise ValueError(
                'Provider id 非法: "{}"，必须以字母开头，仅含字母数字_-'
                .format(p['id']),
            )
        for i, exist in enumerate(self._data['providers']):
            if exist['id'] == p['id']:
                # 内置 provider 的关键字段不允许改
                if exist.get('builtin'):
                    p['builtin'] = True
                self._data['providers'][i] = p
                self._save()
                return
        self._data['providers'].append(p)
        self._save()

    def delete(self, provider_id):
        # type: (str) -> None
        target = self.get(provider_id)
        if target is None:
            return
        if target.get('builtin'):
            raise ValueError('内置 Provider 不能删除，可在编辑页禁用')
        if provider_id == self._data.get('active_id'):
            raise ValueError('不能删除当前激活的 Provider，请先切换')
        self._data['providers'] = [
            p for p in self._data['providers'] if p['id'] != provider_id
        ]
        self._save()

    def restore_builtins(self):
        """把所有内置预设重置为出厂值（保留用户自定义）。"""
        builtins_by_id = {p['id']: _normalize(p) for p in BUILTIN_PROVIDERS}
        new_list = []
        seen = set()
        for p in self._data['providers']:
            if p.get('builtin') and p['id'] in builtins_by_id:
                # 出厂回填，但保留用户填好的 api_key/extra
                fresh = builtins_by_id[p['id']]
                fresh['api_key'] = p.get('api_key', '')
                if p.get('extra'):
                    fresh['extra'] = dict(p['extra'])
                new_list.append(fresh)
                seen.add(p['id'])
            else:
                new_list.append(p)
        # 补上完全缺失的内置项
        for pid, fresh in builtins_by_id.items():
            if pid not in seen:
                new_list.append(fresh)
        self._data['providers'] = new_list
        self._save()

    def _save(self):
        save_providers(self._data, path=self._path)


__all__ = [
    'BUILTIN_PROVIDERS',
    'PROVIDERS_VERSION',
    'ProviderRegistry',
    'get_providers_path',
    'load_providers',
    'save_providers',
    'validate_id',
]
