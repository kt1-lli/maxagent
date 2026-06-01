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
    _humanize_network_error,
    _is_retryable_network_error,
    _sanitize_base_url,
    _winerror_of,
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


class TestNetworkErrorClassification:
    """锁定 WinError 10053 等瞬断网络错误的分类与友好提示。

    背景：Max 内 LLM 调用偶发 'WinError 10053 你的主机中的软件中止了
    一个已建立的连接'，根因多为 VPN/防火墙/代理切包。修复策略是：
    1. 把这类 socket 异常识别为可重试错误（_is_retryable_network_error）
    2. 把 winerror 编号翻译成中文可读提示（_humanize_network_error）
    3. 安装在 _chat_blocking / _chat_stream 的 urlopen 重试循环中
    本套测试锁定上述函数的语义，防止后续重构误删。
    """

    def _fake_oserror_with_winerror(self, code):
        """构造一个真实的 OSError 并设置 winerror 属性（跨平台兼容）。"""
        exc = OSError(code, 'fake msg')
        # winerror 在非 Windows 上不会自动赋值；测试里手动塞入
        try:
            exc.winerror = code
        except AttributeError:
            # Python 3.x 下 OSError.winerror 在某些平台是只读 slot；
            # 这种环境下退而求其次：跳过该断言（测试在 Windows 上会有效）
            pytest.skip('winerror is not writable on this platform')
        return exc

    def test_winerror_of_extracts_from_oserror(self):
        exc = self._fake_oserror_with_winerror(10053)
        assert _winerror_of(exc) == 10053

    def test_winerror_of_returns_none_for_plain_value_error(self):
        assert _winerror_of(ValueError('x')) is None

    def test_is_retryable_for_winerror_10053(self):
        exc = self._fake_oserror_with_winerror(10053)
        assert _is_retryable_network_error(exc) is True

    def test_is_retryable_for_winerror_10054(self):
        exc = self._fake_oserror_with_winerror(10054)
        assert _is_retryable_network_error(exc) is True

    def test_is_retryable_false_for_dns_failure(self):
        # 11001 / 11004 是 DNS 解析失败，重试也是徒劳，不应重试
        exc = self._fake_oserror_with_winerror(11001)
        assert _is_retryable_network_error(exc) is False

    def test_is_retryable_for_pure_connection_aborted(self):
        # 不带 winerror 的 ConnectionAbortedError（Linux/macOS）也算可重试
        assert _is_retryable_network_error(
            ConnectionAbortedError('reset by peer'),
        ) is True
        assert _is_retryable_network_error(
            ConnectionResetError('reset'),
        ) is True

    def test_humanize_winerror_10053(self):
        exc = self._fake_oserror_with_winerror(10053)
        msg = _humanize_network_error(exc)
        # 用户能从提示里看出 winerror 编号 + 中文说明 + 排查建议
        assert 'WinError 10053' in msg
        assert 'VPN' in msg or '防火墙' in msg
        assert '建议' in msg

    def test_humanize_unknown_error_falls_back_to_reason(self):
        # 没有 winerror 的普通错误：不能崩，至少把 reason/原文带出来
        exc = ValueError('some random reason')
        msg = _humanize_network_error(exc)
        assert '网络错误' in msg
        assert 'random reason' in msg
