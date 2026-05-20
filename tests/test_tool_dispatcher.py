#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试 ToolDispatcher 的核心行为：分派、参数校验、未知工具、结果裁剪。"""

from __future__ import absolute_import
from __future__ import print_function

import json

import pytest

from maxagent.tools.dispatcher import (
    DEFAULT_RESULT_MAX_BYTES,
    ToolDispatcher,
    _maybe_truncate_result,
    _truncate_dict,
    _truncate_list,
)
from maxagent.tools.registry import _REGISTRY, ToolSpec


@pytest.fixture
def stub_registry():
    """临时注入若干测试工具，结束后清理。"""
    # 不污染真实 registry：保存现场
    backup = dict(_REGISTRY)
    _REGISTRY.clear()

    def _echo(**kwargs):
        return kwargs

    def _bigret(n=1000):
        # 返回一个超大 list
        return [{'i': i, 'name': 'obj_{}'.format(i)} for i in range(n)]

    def _raise():
        raise RuntimeError('boom')

    _REGISTRY['echo'] = ToolSpec(
        name='echo',
        func=_echo,
        description='echo back',
        parameters={'type': 'object'},
        run_on_main_thread=False,
    )
    _REGISTRY['bigret'] = ToolSpec(
        name='bigret',
        func=_bigret,
        description='big return',
        parameters={'type': 'object'},
        run_on_main_thread=False,
    )
    _REGISTRY['boom'] = ToolSpec(
        name='boom',
        func=_raise,
        description='throws',
        parameters={'type': 'object'},
        run_on_main_thread=False,
    )
    yield
    _REGISTRY.clear()
    _REGISTRY.update(backup)


class TestDispatchBasic:
    def test_unknown_tool(self, stub_registry):
        d = ToolDispatcher()
        out = d.dispatch('not_exist', {})
        assert out['ok'] is False
        assert out['type'] == 'unknown_tool'

    def test_bad_arguments_type(self, stub_registry):
        d = ToolDispatcher()
        out = d.dispatch('echo', 'not_a_dict')
        assert out['ok'] is False
        assert out['type'] == 'bad_arguments'

    def test_dispatch_success(self, stub_registry):
        d = ToolDispatcher()
        out = d.dispatch('echo', {'a': 1, 'b': 'x'})
        assert out['ok'] is True
        assert out['result'] == {'a': 1, 'b': 'x'}

    def test_exception_captured(self, stub_registry):
        d = ToolDispatcher()
        out = d.dispatch('boom', {})
        assert out['ok'] is False
        assert 'RuntimeError' in out['error']
        assert out['type'] == 'exec_error'


class TestResultTruncation:
    def test_no_truncation_under_limit(self, stub_registry):
        d = ToolDispatcher(result_max_bytes=DEFAULT_RESULT_MAX_BYTES)
        out = d.dispatch('echo', {'small': 'value'})
        assert '__truncated__' not in out

    def test_huge_list_truncated(self, stub_registry):
        d = ToolDispatcher(result_max_bytes=2048)
        out = d.dispatch('bigret', {'n': 500})
        assert out['ok'] is True
        # 应该被打上截断标记
        assert '__truncated__' in out
        info = out['__truncated__']
        assert info['original_count'] == 500
        assert info['kept_count'] < 500
        # 整体序列化大小应在限制内（允许小幅超出）
        size = len(json.dumps(out, ensure_ascii=False).encode('utf-8'))
        # 元信息会让最终大小可能稍微超 max_bytes，但不能超得离谱
        assert size <= 2048 * 3

    def test_truncation_disabled(self, stub_registry):
        d = ToolDispatcher(result_max_bytes=0)
        out = d.dispatch('bigret', {'n': 200})
        assert '__truncated__' not in out


class TestTruncateHelpers:
    def test_truncate_list_keeps_some(self):
        items = list(range(1000))
        new, kept = _truncate_list(items, max_bytes=200)
        assert kept < 1000
        # 末尾应有截断标记
        assert any(
            isinstance(x, str) and 'truncated' in x for x in new
        )

    def test_truncate_list_empty(self):
        new, kept = _truncate_list([], 100)
        assert new == []
        assert kept == 0

    def test_truncate_list_under_limit(self):
        items = [1, 2, 3]
        new, kept = _truncate_list(items, 10000)
        assert new == items
        assert kept == 3

    def test_truncate_dict_keeps_some(self):
        d = {'k{}'.format(i): 'val_' + str(i) for i in range(500)}
        new, kept = _truncate_dict(d, max_bytes=200)
        assert kept < 500
        assert '__omitted__' in new

    def test_maybe_truncate_string_result(self):
        out = {'ok': True, 'result': 'x' * 5000}
        new = _maybe_truncate_result(out, 500, tool_name='t')
        assert '__truncated__' in new
        assert len(new['result']) < 5000
