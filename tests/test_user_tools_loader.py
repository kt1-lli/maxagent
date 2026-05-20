#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试用户工具加载器：name/code 校验、写盘、加载、删除。"""

from __future__ import absolute_import
from __future__ import print_function

import os

import pytest

from maxagent import user_tools_loader as utl


@pytest.fixture
def user_tools_base(tmp_path):
    """把 user_tools 目录指到 tmp，函数级别隔离。"""
    base = str(tmp_path / 'user_tools')
    utl.set_user_tools_dir_override(base)
    yield base
    utl.set_user_tools_dir_override(None)


class TestValidateName:
    def test_valid_names(self):
        utl.validate_name('cleanup_temp')
        utl.validate_name('my_tool_v2')
        utl.validate_name('a1')

    def test_invalid_names(self):
        for bad in ('', 'A_bad', '1numeric', 'has-dash', 'too' + 'x' * 60):
            with pytest.raises(ValueError):
                utl.validate_name(bad)


class TestValidateCode:
    def test_valid_code(self):
        code = 'def f():\n    return 1\n'
        utl.validate_code(code)

    def test_empty_code(self):
        with pytest.raises(ValueError):
            utl.validate_code('')
        with pytest.raises(ValueError):
            utl.validate_code('   \n')

    def test_syntax_error(self):
        with pytest.raises(ValueError):
            utl.validate_code('def f(\n')

    def test_too_large(self):
        big = 'x = 1\n' * (utl.MAX_CODE_BYTES // 6 + 100)
        with pytest.raises(ValueError):
            utl.validate_code(big)


class TestWriteAndList:
    def test_write_then_list(self, user_tools_base):
        code = (
            'from maxagent.tools.registry import tool\n\n'
            '@tool(name="my_test_tool",\n'
            '      description="x",\n'
            '      params_schema={"type":"object","properties":{}},\n'
            '      run_on_main_thread=False, dangerous=True)\n'
            'def my_test_tool():\n'
            '    return 42\n'
        )
        utl.write_tool('my_test_tool', code, meta={'created_at': 0})
        items = utl.list_user_tools()
        assert any(it['name'] == 'my_test_tool' for it in items)

    def test_overwrite_existing(self, user_tools_base):
        code1 = 'def x(): return 1\n'
        code2 = 'def x(): return 2\n'
        utl.write_tool('overwrite_target', code1, meta={})
        utl.write_tool('overwrite_target', code2, meta={})
        py = os.path.join(
            user_tools_base, 'overwrite_target.py',
        )
        assert os.path.exists(py)
        with open(py, 'r', encoding='utf-8') as fh:
            assert 'return 2' in fh.read()

    def test_delete_user_tool(self, user_tools_base):
        utl.write_tool('to_delete', 'def f(): pass\n', meta={})
        assert utl.delete_user_tool('to_delete') is True
        # 双删返回 False
        assert utl.delete_user_tool('to_delete') is False

    def test_reject_overwrite_builtin(self, user_tools_base):
        # 模拟一个内置工具占据该名字
        from maxagent.tools.registry import _REGISTRY
        # 借用内置工具命名（有些一定存在）
        # 简单插一个临时占位
        from maxagent.tools.registry import ToolSpec
        _REGISTRY['list_scene_objects_BUILTIN_PROBE'] = ToolSpec(
            name='list_scene_objects_BUILTIN_PROBE',
            func=lambda: None,
            description='probe',
            parameters={'type': 'object'},
            run_on_main_thread=False,
        )
        try:
            with pytest.raises(ValueError):
                utl.validate_name('list_scene_objects_BUILTIN_PROBE')
        finally:
            del _REGISTRY['list_scene_objects_BUILTIN_PROBE']
