#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试 user tool 使用统计累加（bump_tool_usage + dispatcher 集成）。"""

from __future__ import absolute_import
from __future__ import print_function

import importlib
import json
import os
import sys

import pytest


def _utl():
    """返回 sys.modules 中当前生效的 user_tools_loader 实例。

    之所以不用 ``from maxagent import user_tools_loader as utl`` 模块级
    缓存：``test_reload.fresh_imports`` fixture 会把 sys.modules 中的
    ``maxagent.*`` 全部弹掉，下次再 import 时是新对象。dispatcher 内部
    通过延迟 ``from ..user_tools_loader import bump_tool_usage`` 拿到的
    永远是 sys.modules 里"现在"那个，而测试模块级 ``utl`` 仍指向旧实例，
    导致 ``set_user_tools_dir_override`` 写到旧实例 dispatcher 不生效。
    """
    if 'maxagent.user_tools_loader' not in sys.modules:
        importlib.import_module('maxagent.user_tools_loader')
    return sys.modules['maxagent.user_tools_loader']


def _registry():
    """同理：保证 _REGISTRY 也总是 sys.modules 里那一份。"""
    if 'maxagent.tools.registry' not in sys.modules:
        importlib.import_module('maxagent.tools.registry')
    return sys.modules['maxagent.tools.registry']


@pytest.fixture
def user_tools_base(tmp_path):
    base = str(tmp_path / 'user_tools')
    _utl().set_user_tools_dir_override(base)
    yield base
    _utl().set_user_tools_dir_override(None)


@pytest.fixture
def isolated_registry():
    reg_mod = _registry()
    backup = dict(reg_mod._REGISTRY)
    reg_mod._REGISTRY.clear()
    yield reg_mod
    reg_mod._REGISTRY.clear()
    reg_mod._REGISTRY.update(backup)


class TestBumpDirect:
    def test_bump_nonexistent_returns_false(self, user_tools_base):
        assert _utl().bump_tool_usage('builtin_xx', ok=True) is False

    def test_bump_success_increments(self, user_tools_base):
        utl = _utl()
        utl.write_tool(
            'my_tool',
            'def f(): return 1\n',
            meta={'description': 'x'},
        )
        assert utl.bump_tool_usage('my_tool', ok=True) is True
        meta_path = os.path.join(user_tools_base, 'my_tool.meta.json')
        with open(meta_path, 'r', encoding='utf-8') as fh:
            meta = json.load(fh)
        assert meta['use_count'] == 1
        assert meta['success_count'] == 1
        assert meta.get('error_count', 0) == 0
        assert meta['last_ok'] is True
        assert isinstance(meta['last_used_at'], (int, float))

    def test_bump_failure_increments_error(self, user_tools_base):
        utl = _utl()
        utl.write_tool(
            'my_tool',
            'def f(): return 1\n',
            meta={'description': 'x'},
        )
        utl.bump_tool_usage('my_tool', ok=False)
        utl.bump_tool_usage('my_tool', ok=False)
        meta_path = os.path.join(user_tools_base, 'my_tool.meta.json')
        with open(meta_path, 'r', encoding='utf-8') as fh:
            meta = json.load(fh)
        assert meta['use_count'] == 2
        assert meta['error_count'] == 2
        assert meta.get('success_count', 0) == 0
        assert meta['last_ok'] is False

    def test_bump_mixed(self, user_tools_base):
        utl = _utl()
        utl.write_tool(
            'my_tool',
            'def f(): return 1\n',
            meta={'description': 'x'},
        )
        utl.bump_tool_usage('my_tool', ok=True)
        utl.bump_tool_usage('my_tool', ok=False)
        utl.bump_tool_usage('my_tool', ok=True)
        meta_path = os.path.join(user_tools_base, 'my_tool.meta.json')
        with open(meta_path, 'r', encoding='utf-8') as fh:
            meta = json.load(fh)
        assert meta['use_count'] == 3
        assert meta['success_count'] == 2
        assert meta['error_count'] == 1


class TestDispatcherBumpsUsage:
    def test_success_path_bumps(
        self, user_tools_base, isolated_registry,
    ):
        from maxagent.tools.dispatcher import ToolDispatcher
        ToolSpec = isolated_registry.ToolSpec  # noqa: N806

        def _ok():
            return {'value': 42}

        isolated_registry._REGISTRY['my_user_tool'] = ToolSpec(
            name='my_user_tool',
            func=_ok,
            description='ut',
            parameters={'type': 'object'},
            run_on_main_thread=False,
        )
        _utl().get_user_tools_dir()
        meta_path = os.path.join(
            user_tools_base, 'my_user_tool.meta.json',
        )
        with open(meta_path, 'w', encoding='utf-8') as fh:
            json.dump(
                {'name': 'my_user_tool', 'use_count': 0},
                fh,
            )

        d = ToolDispatcher()
        out = d.dispatch('my_user_tool', {})
        assert out['ok'] is True

        with open(meta_path, 'r', encoding='utf-8') as fh:
            meta = json.load(fh)
        assert meta['use_count'] == 1
        assert meta['success_count'] == 1

    def test_failure_path_bumps_error(
        self, user_tools_base, isolated_registry,
    ):
        from maxagent.tools.dispatcher import ToolDispatcher
        ToolSpec = isolated_registry.ToolSpec  # noqa: N806

        def _boom():
            raise RuntimeError('nope')

        isolated_registry._REGISTRY['my_failing_tool'] = ToolSpec(
            name='my_failing_tool',
            func=_boom,
            description='ut',
            parameters={'type': 'object'},
            run_on_main_thread=False,
        )
        _utl().get_user_tools_dir()
        meta_path = os.path.join(
            user_tools_base, 'my_failing_tool.meta.json',
        )
        with open(meta_path, 'w', encoding='utf-8') as fh:
            json.dump({'name': 'my_failing_tool'}, fh)

        d = ToolDispatcher()
        out = d.dispatch('my_failing_tool', {})
        assert out['ok'] is False

        with open(meta_path, 'r', encoding='utf-8') as fh:
            meta = json.load(fh)
        assert meta['use_count'] == 1
        assert meta['error_count'] == 1
        assert meta.get('success_count', 0) == 0

    def test_builtin_tool_no_meta_no_crash(self, isolated_registry):
        from maxagent.tools.dispatcher import ToolDispatcher
        ToolSpec = isolated_registry.ToolSpec  # noqa: N806

        def _ok():
            return 'fine'

        isolated_registry._REGISTRY['builtin_like'] = ToolSpec(
            name='builtin_like',
            func=_ok,
            description='ut',
            parameters={'type': 'object'},
            run_on_main_thread=False,
        )
        d = ToolDispatcher()
        out = d.dispatch('builtin_like', {})
        assert out['ok'] is True
