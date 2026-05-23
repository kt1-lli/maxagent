#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试 LLMClient 的纯函数：base_url 清理、错误格式化、SSE 解析。"""

from __future__ import absolute_import
from __future__ import print_function

import io
import json

import pytest

from maxagent.llm_client import (
    LLMClient,
    LLMError,
    _format_http_error,
    _sanitize_base_url,
    diagnose_base_url,
)


class TestSanitizeBaseUrl:
    def test_strip_trailing_slash(self):
        assert _sanitize_base_url('https://api/v1/') == 'https://api/v1'

    def test_strip_chat_completions_tail(self):
        assert (
            _sanitize_base_url(
                'https://api.deepseek.com/v1/chat/completions',
            )
            == 'https://api.deepseek.com/v1'
        )

    def test_strip_completions_only(self):
        assert (
            _sanitize_base_url('https://x.com/v1/completions')
            == 'https://x.com/v1'
        )

    def test_no_change_when_clean(self):
        assert (
            _sanitize_base_url('https://api.openai.com/v1')
            == 'https://api.openai.com/v1'
        )

    def test_empty(self):
        assert _sanitize_base_url('') == ''


class TestDiagnoseBaseUrl:
    def test_missing_protocol(self):
        msg = diagnose_base_url('api.openai.com/v1')
        assert msg is not None
        assert 'http' in msg

    def test_with_chat_completions_warned(self):
        msg = diagnose_base_url('https://api/v1/chat/completions')
        assert msg is not None
        assert 'chat/completions' in msg

    def test_no_version_path_hint(self):
        msg = diagnose_base_url('https://example.com/foo')
        # 应给出 /v1 提示
        assert msg is not None

    def test_clean_no_warning(self):
        msg = diagnose_base_url('https://api.openai.com/v1')
        assert msg is None

    def test_deepseek_root_domain_no_warning(self):
        # DeepSeek 官方文档（2026/05）首推根域名形式，不应触发 /v1 提示
        msg = diagnose_base_url('https://api.deepseek.com')
        assert msg is None

    def test_deepseek_root_domain_with_trailing_slash_no_warning(self):
        msg = diagnose_base_url('https://api.deepseek.com/')
        assert msg is None

    def test_deepseek_with_v1_still_no_warning(self):
        # 兼容老用户：带 /v1 也合法
        msg = diagnose_base_url('https://api.deepseek.com/v1')
        assert msg is None


class _FakeHttpError(Exception):
    """模拟 urllib HTTPError 子集。"""

    def __init__(self, code, body, headers=None):
        super().__init__('mock')
        self.code = code
        self._body = body.encode('utf-8') if isinstance(body, str) else body
        self.headers = headers or {}

    def read(self):
        return self._body


class TestFormatHttpError:
    def test_basic_with_message(self):
        exc = _FakeHttpError(
            401,
            json.dumps({'error': {'message': 'invalid api key'}}),
        )
        out = _format_http_error(exc, 'https://x/y')
        assert 'HTTP 401' in out
        assert 'invalid api key' in out
        assert 'url=https://x/y' in out

    def test_with_governor_header(self):
        exc = _FakeHttpError(
            401,
            'governor reject',
            headers={'Server': 'governor', 'X-Tencent-Reqid': 'abc'},
        )
        out = _format_http_error(exc, 'https://x/y')
        # 应当把关键 header 拼在错误信息里，方便定位
        assert 'governor' in out

    def test_unparseable_body(self):
        exc = _FakeHttpError(500, '<html>oops</html>')
        out = _format_http_error(exc, 'https://x')
        assert 'HTTP 500' in out
        # 至少要带上原始 body 片段
        assert 'oops' in out

    def test_long_body_truncated(self):
        body = 'A' * 5000
        exc = _FakeHttpError(500, body)
        out = _format_http_error(exc, 'https://x')
        # 不应该直接把 5000 字节都打出来
        assert len(out) < 4500
        assert 'truncated' in out


class TestLLMClientInit:
    def test_sanitize_in_constructor(self):
        c = LLMClient(
            base_url='https://api.deepseek.com/v1/chat/completions',
            api_key='k', model='m',
        )
        # 内部 _base_url 应已被清理（虽然是 _前缀，测试访问也无所谓）
        assert c._base_url == 'https://api.deepseek.com/v1'

    def test_get_last_usage_initial_empty(self):
        c = LLMClient(base_url='http://x', api_key='', model='m')
        assert c.get_last_usage() == {}
