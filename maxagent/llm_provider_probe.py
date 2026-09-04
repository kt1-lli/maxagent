#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM 运营商模型列表探测器。

给定 base_url + api_key，请求运营商的 /models 端点列出可用模型。
支持 OpenAI 兼容协议（默认走 GET /v1/models）以及 Ollama、Anthropic、
Gemini 等自定义端点。

设计原则：
- 零外部依赖，纯 urllib
- 结果本地缓存到 ~/.config/maxagent/model_cache.json，
  key = (base_url, hash(api_key))
- 失败时按 HTTP 状态码/异常类型返回可读中文原因
"""

from __future__ import print_function

import hashlib
import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- #
# 常量
# ---------------------------------------------------------------- #
DEFAULT_TIMEOUT = 15
CACHE_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 天


def _cache_dir():
    # type: () -> str
    home = os.path.expanduser('~')
    d = os.path.join(home, '.config', 'maxagent')
    if not os.path.isdir(d):
        try:
            os.makedirs(d)
        except OSError:
            pass
    return d


def _cache_key(base_url, api_key):
    # type: (str, str) -> str
    """缓存 key：base_url 明文 + api_key 短哈希。"""
    h = hashlib.sha1((api_key or '').encode('utf-8')).hexdigest()[:8]
    return '{}#{}'.format(base_url.rstrip('/'), h)


def _cache_load():
    # type: () -> Dict[str, Any]
    path = os.path.join(_cache_dir(), 'model_cache.json')
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError) as exc:
        log.debug('读取模型缓存失败: %s', exc)
        return {}


def _cache_save(data):
    # type: (Dict[str, Any]) -> None
    path = os.path.join(_cache_dir(), 'model_cache.json')
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError as exc:
        log.debug('写入模型缓存失败: %s', exc)


# ---------------------------------------------------------------- #
# HTTP
# ---------------------------------------------------------------- #
def _http_get_json(url, headers=None, timeout=DEFAULT_TIMEOUT):
    # type: (str, Optional[Dict[str, str]], int) -> Any
    req = urllib.request.Request(url, method='GET')
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw.decode('utf-8'))


# ---------------------------------------------------------------- #
# 分派：按 base_url 特征选择探测策略
# ---------------------------------------------------------------- #
def _detect_flavor(base_url):
    # type: (str) -> str
    url = (base_url or '').lower()
    if 'anthropic' in url:
        return 'anthropic'
    if 'generativelanguage.googleapis' in url or 'aiplatform.googleapis' in url:
        return 'gemini'
    if ':11434' in url or 'ollama' in url:
        return 'ollama'
    return 'openai'


def _probe_openai(base_url, api_key, extra_headers=None, timeout=DEFAULT_TIMEOUT):
    # type: (str, str, Optional[Dict[str, str]], int) -> List[Dict[str, Any]]
    """OpenAI 兼容协议：GET {base}/models，Bearer 鉴权。"""
    url = base_url.rstrip('/') + '/models'
    headers = {'Accept': 'application/json'}
    if api_key:
        headers['Authorization'] = 'Bearer {}'.format(api_key)
    if extra_headers:
        headers.update(extra_headers)
    data = _http_get_json(url, headers=headers, timeout=timeout)
    items = data.get('data') if isinstance(data, dict) else None
    if not isinstance(items, list):
        # 有些兼容实现直接返回 list（如某些自建网关）
        items = data if isinstance(data, list) else []
    result = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        mid = entry.get('id') or entry.get('model') or entry.get('name')
        if not mid:
            continue
        result.append({
            'id': str(mid),
            'label': str(mid),
            'context': entry.get('context_length'),
        })
    return result


def _probe_ollama(base_url, api_key, extra_headers=None, timeout=DEFAULT_TIMEOUT):
    # type: (str, str, Optional[Dict[str, str]], int) -> List[Dict[str, Any]]
    """Ollama: GET /api/tags 返回 {models: [{name, ...}]}。"""
    root = base_url.rstrip('/')
    # 兼容用户把 /v1 加到 URL 尾巴的情况
    if root.endswith('/v1'):
        root = root[:-3]
    url = root + '/api/tags'
    headers = {'Accept': 'application/json'}
    if extra_headers:
        headers.update(extra_headers)
    data = _http_get_json(url, headers=headers, timeout=timeout)
    items = data.get('models') if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    result = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        name = entry.get('name') or entry.get('model')
        if not name:
            continue
        result.append({
            'id': str(name),
            'label': str(name),
            'context': None,
        })
    return result


def _probe_anthropic(base_url, api_key, extra_headers=None, timeout=DEFAULT_TIMEOUT):
    # type: (str, str, Optional[Dict[str, str]], int) -> List[Dict[str, Any]]
    """Anthropic: GET /v1/models，x-api-key + anthropic-version 头。"""
    url = base_url.rstrip('/') + '/v1/models'
    headers = {
        'Accept': 'application/json',
        'anthropic-version': '2023-06-01',
    }
    if api_key:
        headers['x-api-key'] = api_key
    if extra_headers:
        headers.update(extra_headers)
    data = _http_get_json(url, headers=headers, timeout=timeout)
    items = data.get('data') if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    result = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        mid = entry.get('id') or entry.get('name')
        if not mid:
            continue
        result.append({'id': str(mid), 'label': str(mid), 'context': None})
    return result


def _probe_gemini(base_url, api_key, extra_headers=None, timeout=DEFAULT_TIMEOUT):
    # type: (str, str, Optional[Dict[str, str]], int) -> List[Dict[str, Any]]
    """Gemini: GET /v1beta/models?key=xxx"""
    root = base_url.rstrip('/')
    if not root.endswith('/v1beta'):
        # 允许用户填到 https://generativelanguage.googleapis.com
        root = root + '/v1beta'
    url = root + '/models?key=' + (api_key or '')
    data = _http_get_json(url, headers=extra_headers, timeout=timeout)
    items = data.get('models') if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    result = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        name = entry.get('name') or ''
        # Gemini 返回形如 "models/gemini-1.5-pro"，剥离前缀
        if name.startswith('models/'):
            name = name[7:]
        if not name:
            continue
        result.append({
            'id': str(name),
            'label': str(name),
            'context': entry.get('inputTokenLimit'),
        })
    return result


_PROBERS = {
    'openai': _probe_openai,
    'ollama': _probe_ollama,
    'anthropic': _probe_anthropic,
    'gemini': _probe_gemini,
}


# ---------------------------------------------------------------- #
# 对外 API
# ---------------------------------------------------------------- #
def list_models(
    base_url,
    api_key='',
    extra_headers=None,
    timeout=DEFAULT_TIMEOUT,
    use_cache=True,
    force_refresh=False,
):
    # type: (str, str, Optional[Dict[str, str]], int, bool, bool) -> Tuple[List[Dict[str, Any]], Optional[str]]
    """列出 base_url 后面能用的模型。

    :returns: (models, error)
      - 成功: (list, None)
      - 失败: ([], error_msg)
      - 有缓存但失败: (cached_list, error_msg)  以便 UI 兜底展示
    """
    if not base_url:
        return [], 'Base URL 为空'

    cache = _cache_load() if use_cache else {}
    ck = _cache_key(base_url, api_key or '')
    cached = cache.get(ck) or {}
    cached_models = cached.get('models') or []

    if use_cache and not force_refresh:
        ts = cached.get('ts', 0)
        if cached_models and (time.time() - ts) < CACHE_TTL_SECONDS:
            return cached_models, None

    flavor = _detect_flavor(base_url)
    prober = _PROBERS.get(flavor, _probe_openai)
    try:
        models = prober(base_url, api_key, extra_headers, timeout)
    except urllib.error.HTTPError as exc:
        err = 'HTTP {}: {}'.format(exc.code, exc.reason)
        if exc.code == 401:
            err = 'API Key 未通过认证（HTTP 401）'
        elif exc.code == 403:
            err = 'API Key 无权访问模型列表（HTTP 403）'
        elif exc.code == 404:
            err = '{} 未提供 /models 端点（HTTP 404）'.format(base_url)
        return cached_models, err
    except urllib.error.URLError as exc:
        return cached_models, '网络错误: {}'.format(exc.reason)
    except (ValueError, KeyError) as exc:
        return cached_models, '解析响应失败: {}'.format(exc)
    except Exception as exc:  # pylint: disable=broad-except
        log.warning('探测模型列表异常', exc_info=True)
        return cached_models, '未知错误: {}'.format(exc)

    if not models:
        return cached_models, '未返回任何模型'

    # 写缓存
    if use_cache:
        cache[ck] = {'ts': time.time(), 'models': models, 'flavor': flavor}
        _cache_save(cache)

    return models, None


def get_cached_models(base_url, api_key=''):
    # type: (str, str) -> List[Dict[str, Any]]
    """只读缓存，不发起网络请求。"""
    ck = _cache_key(base_url, api_key or '')
    cache = _cache_load()
    entry = cache.get(ck) or {}
    return entry.get('models') or []


__all__ = ['list_models', 'get_cached_models']
