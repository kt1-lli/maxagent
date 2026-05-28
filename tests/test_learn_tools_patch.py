#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试 patch_learned_tool 流程：仅能改 user tool / 必填 rationale /
弹窗审批回路 / 落盘 + 热加载。"""

from __future__ import absolute_import
from __future__ import print_function

import os

import pytest

from maxagent import user_tools_loader as utl
from maxagent.tools import learn_tools


@pytest.fixture
def user_tools_base(tmp_path):
    base = str(tmp_path / 'user_tools')
    utl.set_user_tools_dir_override(base)
    # 记录测试前 registry 中的名称，结束时清理新增的
    from maxagent.tools.registry import _REGISTRY
    pre = set(_REGISTRY.keys())
    yield base
    utl.set_user_tools_dir_override(None)
    # 清理本测试期间新注册的 user tools，避免污染其它用例
    new_names = set(_REGISTRY.keys()) - pre
    for n in new_names:
        del _REGISTRY[n]


def _seed_user_tool(name, body='return 1'):
    """先写一个最简 user tool 进盘，作为 patch 的目标。"""
    code = (
        'from maxagent.tools.registry import tool\n\n'
        '@tool(name="{name}", description="seed",\n'
        '      parameters={{"type":"object","properties":{{}}}},\n'
        '      run_on_main_thread=False, dangerous=False)\n'
        'def {name}():\n'
        '    {body}\n'
    ).format(name=name, body=body)
    utl.write_tool(name, code, meta={'description': 'seed'})


class TestPatchLearnedTool:
    def test_reject_unknown_tool(self, user_tools_base):
        result = learn_tools.patch_learned_tool(
            name='nonexistent_tool',
            new_code='def x(): return 1\n',
            rationale='修个 bug',
        )
        assert result['approved'] is False
        assert result['stage'] == 'not_user_tool'

    def test_reject_invalid_name(self, user_tools_base):
        result = learn_tools.patch_learned_tool(
            name='Bad-Name',
            new_code='def x(): return 1\n',
            rationale='x',
        )
        assert result['approved'] is False
        assert result['stage'] == 'validate_name'

    def test_reject_empty_rationale(self, user_tools_base):
        _seed_user_tool('seed_tool')
        result = learn_tools.patch_learned_tool(
            name='seed_tool',
            new_code='def y(): return 2\n',
            rationale='   ',
        )
        assert result['approved'] is False
        assert result['stage'] == 'validate_rationale'

    def test_reject_syntax_error_code(self, user_tools_base):
        _seed_user_tool('seed_tool')
        result = learn_tools.patch_learned_tool(
            name='seed_tool',
            new_code='def x(\n',  # 语法错
            rationale='测试语法校验',
        )
        assert result['approved'] is False
        assert result['stage'] == 'validate_code'

    def test_approval_flow_save_and_reload(self, user_tools_base):
        _seed_user_tool('seed_tool')
        new_code = (
            'from maxagent.tools.registry import tool\n\n'
            '@tool(name="seed_tool", description="patched",\n'
            '      parameters={"type":"object","properties":{}},\n'
            '      run_on_main_thread=False, dangerous=False)\n'
            'def seed_tool():\n'
            '    return 999\n'
        )

        captured_proposals = []

        def fake_cb(proposal):
            captured_proposals.append(proposal)
            return {
                'approved': True,
                'edited_code': proposal['code'],
                'edited_description': proposal.get('description', ''),
                'reason': '',
            }

        learn_tools.set_approval_callback(fake_cb)
        try:
            result = learn_tools.patch_learned_tool(
                name='seed_tool',
                new_code=new_code,
                rationale='把返回值改成 999',
            )
        finally:
            learn_tools.set_approval_callback(None)

        assert result['approved'] is True
        assert result['saved'] is True
        assert result['loaded'] is True

        # 弹窗回调收到的 proposal 必须包含 rationale 信息
        assert len(captured_proposals) == 1
        assert '把返回值改成 999' in captured_proposals[0]['rationale']

        # 文件被覆盖
        py_path = os.path.join(user_tools_base, 'seed_tool.py')
        with open(py_path, 'r', encoding='utf-8') as fh:
            assert 'return 999' in fh.read()

        # registry 已重新加载
        from maxagent.tools.registry import get_tool
        spec = get_tool('seed_tool')
        assert spec is not None
        assert spec.func() == 999

    def test_user_rejection_blocks_write(self, user_tools_base):
        _seed_user_tool('reject_tool')

        def fake_cb(proposal):
            return {
                'approved': False,
                'reason': '我不喜欢这个改动',
                'edited_code': proposal['code'],
                'edited_description': '',
            }

        learn_tools.set_approval_callback(fake_cb)
        try:
            result = learn_tools.patch_learned_tool(
                name='reject_tool',
                new_code='def reject_tool(): return 0\n',
                rationale='想改',
            )
        finally:
            learn_tools.set_approval_callback(None)

        assert result['approved'] is False
        assert result['stage'] == 'rejected'
        assert '不喜欢' in result['reason']
        # 原文件未被改动
        py_path = os.path.join(user_tools_base, 'reject_tool.py')
        with open(py_path, 'r', encoding='utf-8') as fh:
            assert 'return 1' in fh.read()
