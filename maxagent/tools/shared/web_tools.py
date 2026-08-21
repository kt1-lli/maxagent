#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""联网搜索相关工具。

注册两个 LLM 可调用的工具：
- ``web_search``：根据用户提供的 query 联网搜索，返回最多 N 条结果。
- ``web_fetch``：单独抓取一个 URL 的可读正文（用于 LLM 拿到搜索结果后追加阅读）。

工具运行时如何拿到当前 ``AppConfig``？
=====================================
工具是无状态函数，但联网相关参数（默认后端、Bing Key、是否抓正文）
存在 ``AppConfig`` 上。这里通过 :func:`maxagent.config.load_config`
按需懒加载——保证测试时也能 monkeypatch ``load_config`` 注入 mock。

为何 ``run_on_main_thread=False``？
==================================
联网工具不调用任何 pymxs API，纯 Python 层网络 IO，应该在子线程跑，
避免占用 Max 主线程事件循环。
"""

from __future__ import absolute_import
from __future__ import print_function

from typing import Optional

from ...logger import get_logger
from ...web_search import fetch_page_text
from ...web_search import search as _do_search
from ...web_search import SearchError
from ..registry import tool


logger = get_logger(__name__)


def _resolve_web_settings():
    """统一取当前 AppConfig + ProviderRegistry 联网相关字段。

    返回：
    - ``mode`` / ``max_results`` / ``fetch_page_text``：来自 AppConfig
    - ``provider``：当前激活的 provider 配置字典（可能为 None；为兼容
      老配置或被禁用时）
    - ``backend`` / ``bing_api_key``：旧路径兜底字段，仅当 provider 为
      None 且非 disabled 时使用

    便于测试 monkeypatch：测试用例可以替换 ``load_config`` 或
    ``ProviderRegistry`` 的实现注入 mock。
    """
    fallback = {
        'mode': 'off',
        'backend': 'duckduckgo',
        'max_results': 5,
        'fetch_page_text': True,
        'bing_api_key': '',
        'provider': None,
    }
    try:
        from ...config import load_config
        cfg = load_config()
    except Exception:  # pylint: disable=broad-except
        return fallback

    settings = {
        'mode': getattr(cfg, 'web_search_mode', 'auto'),
        'backend': getattr(cfg, 'web_search_backend', 'duckduckgo'),
        'max_results': getattr(cfg, 'web_search_max_results', 5),
        'fetch_page_text': getattr(cfg, 'web_fetch_page_text', True),
        'bing_api_key': getattr(cfg, 'bing_api_key', ''),
        'provider': None,
    }
    # 解析当前激活 provider（新路径）。失败时静默回退到旧路径，
    # 不影响主功能。
    try:
        from ...web_providers import ProviderRegistry
        reg = ProviderRegistry()
        active = reg.get_active()
        # 兼容老配置：AppConfig.web_search_backend 可能是 'duckduckgo'
        # /'bing_api'/'disabled' 字符串。如果用户没显式选过 provider，
        # 优先按这个字符串映射到对应的内置 provider id。
        explicit_id = getattr(cfg, 'web_search_backend', '') or ''
        if explicit_id and explicit_id != 'disabled':
            mapped = reg.get(explicit_id)
            if mapped is not None:
                active = mapped
        if active and active.get('enabled', True) and explicit_id != 'disabled':
            settings['provider'] = active
    except Exception as exc:  # pylint: disable=broad-except
        logger.debug('ProviderRegistry 加载失败，回退老路径: %s', exc)
    return settings


@tool(
    name='web_search',
    description=(
        '联网搜索：当用户问的内容超出你的知识或要求"最新""最近"信息时调用。'
        '返回若干条标题/链接/摘要；如果设置中开启了"抓取网页正文"，'
        '每条结果还会附带一段清洗后的页面文本可直接引用。'
        '\n\n'
        '调用时尽量把 query 浓缩为 1~2 句关键词组合（中英文均可），'
        '避免整段问题原文塞进去导致命中率下降。'
    ),
    category='web',
    dangerous=False,
    wrap_undo=False,
    run_on_main_thread=False,
    examples=[{"summary": "典型调用", "args": {"query": 'Box.position', "max_results": 'value', "fetch_page": 'value'}}],
notes=['参数必须严格符合 JSON Schema 声明的类型。', '调用失败时应先检查对象/文件是否存在。'],
returns_desc="dict {\"ok\": True, ...}"
)
def web_search(query, max_results=None, fetch_page=None):
    """联网搜索。

    :param query: 搜索关键词
    :param max_results: 限制返回结果数（不传则用设置中的默认值）
    :param fetch_page: 是否同步抓取每条结果的网页正文（不传则用设置中默认）
    """
    settings = _resolve_web_settings()
    if settings['mode'] == 'off':
        return {
            'ok': False,
            'error': '联网搜索已被全局关闭。请到"设置 → 联网"开启。',
            'results': [],
        }
    if settings['backend'] == 'disabled':
        return {
            'ok': False,
            'error': '当前后端为 disabled，请到"设置 → 联网"选择搜索后端。',
            'results': [],
        }
    q = (query or '').strip()
    if not q:
        return {'ok': False, 'error': 'query 不能为空', 'results': []}

    n = (
        int(max_results) if max_results is not None
        else int(settings['max_results'])
    )
    n = max(1, min(10, n))
    fp = (
        bool(fetch_page) if fetch_page is not None
        else bool(settings['fetch_page_text'])
    )
    try:
        items = _do_search(
            q, max_results=n,
            backend=settings['backend'],
            bing_api_key=settings['bing_api_key'] or '',
            fetch_page=fp,
            provider=settings.get('provider'),
        )
    except SearchError as exc:
        return {'ok': False, 'error': str(exc), 'results': []}
    backend_label = (
        (settings.get('provider') or {}).get('id')
        or settings['backend']
    )
    return {
        'ok': True,
        'backend': backend_label,
        'query': q,
        'count': len(items),
        'results': [r.to_dict() for r in items],
    }


@tool(
    name='web_fetch',
    description=(
        '抓取并清洗指定 URL 的可读正文。'
        '常用场景：``web_search`` 返回了候选链接但摘要不够，'
        '让你单独拉某条 URL 的完整文本继续阅读。'
        '默认截断到 4000 字符；不需要 page_text 时不要重复调用。'
    ),
    category='web',
    dangerous=False,
    wrap_undo=False,
    run_on_main_thread=False,
    examples=[{"summary": "典型调用", "args": {"url": 'https://example.com', "max_chars": 4000}}],
notes=['参数必须严格符合 JSON Schema 声明的类型。', '调用失败时应先检查对象/文件是否存在。'],
returns_desc="dict {\"ok\": True, ...}"
)
def web_fetch(url, max_chars=4000):
    """抓取单个 URL 的正文文本。

    :param url: 完整 URL（http/https）
    :param max_chars: 截断长度，默认 4000 字符
    """
    settings = _resolve_web_settings()
    if settings['mode'] == 'off':
        return {
            'ok': False,
            'error': '联网功能已被全局关闭，无法抓取 URL。',
        }
    u = (url or '').strip()
    if not u:
        return {'ok': False, 'error': 'url 不能为空'}
    if not (u.startswith('http://') or u.startswith('https://')):
        return {'ok': False, 'error': 'url 必须以 http:// 或 https:// 开头'}
    text = fetch_page_text(u, max_chars=int(max_chars or 4000))
    if not text:
        return {
            'ok': False,
            'url': u,
            'error': '抓取失败或正文为空',
        }
    return {
        'ok': True,
        'url': u,
        'chars': len(text),
        'text': text,
    }


__all__ = ['web_search', 'web_fetch']