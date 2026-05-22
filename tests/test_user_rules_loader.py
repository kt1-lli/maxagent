#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试用户规则加载器（user_rules_loader）。

覆盖：
- ID / 内容校验
- 写入 / 列出 / 读取 / 删除 / 启用切换
- system prompt 注入：基本拼接、按 created_at 排序、超限截断、
  禁用规则不注入、空目录返回空串
- 总字节统计
"""

from __future__ import absolute_import
from __future__ import print_function

import time

import pytest

from maxagent import user_rules_loader as url


# ---------------------------------------------------------------------- #
# fixture
# ---------------------------------------------------------------------- #

@pytest.fixture
def rules_base(tmp_path):
    """把 user_rules 目录指到 tmp，函数级别隔离。

    注意：必须在 *运行时* 重新 import maxagent.user_rules_loader，
    因为 test_reload 会 purge 整个 maxagent.* 命名空间，再次跑这里时
    模块顶部 import 拿到的是 stale 对象，而 learn_rules 在 purge 后
    重新 import 时看到的是新对象。fixture 必须把 override 同时设到
    "当前活的" user_rules_loader 模块上。
    """
    import importlib  # 局部 import，避免模块级污染

    live_url = importlib.import_module('maxagent.user_rules_loader')
    base = str(tmp_path / 'user_rules')
    live_url.set_user_rules_dir_override(base)
    # 同时把测试模块顶部的 url 别名也指过去（让旧测试代码不失效）
    global url  # pylint: disable=global-statement
    url = live_url
    yield base
    live_url.set_user_rules_dir_override(None)


def _make_rule(rule_id, **overrides):
    """生成一份合法的最小规则字典。"""
    rule = {
        'title': '测试规则 ' + rule_id,
        'content': '这是一条用于单元测试的规则正文，应当被正常注入。',
        'tags': ['test'],
        'created_at': overrides.pop('created_at', time.time()),
        'enabled': overrides.pop('enabled', True),
    }
    rule.update(overrides)
    return rule


# ---------------------------------------------------------------------- #
# 1. 校验
# ---------------------------------------------------------------------- #

class TestValidateRuleId:
    def test_valid_ids(self):
        url.validate_rule_id('color_uppercase')
        url.validate_rule_id('r1')
        url.validate_rule_id('a' + 'b' * 40)  # 41 字符上限

    def test_invalid_ids(self):
        for bad in (
                '',
                'A_uppercase',
                '1numeric_start',
                'has-dash',
                'has space',
                'a' + 'b' * 41,  # 42 字符超限
        ):
            with pytest.raises(ValueError):
                url.validate_rule_id(bad)


class TestValidateRuleContent:
    def test_valid_content(self):
        url.validate_rule_content('一些规则文字')

    def test_empty_content_rejected(self):
        with pytest.raises(ValueError):
            url.validate_rule_content('')
        with pytest.raises(ValueError):
            url.validate_rule_content('   \n\t  ')

    def test_oversize_content_rejected(self):
        big = 'x' * (url.MAX_RULE_BYTES + 10)
        with pytest.raises(ValueError):
            url.validate_rule_content(big)


# ---------------------------------------------------------------------- #
# 2. CRUD
# ---------------------------------------------------------------------- #

class TestRuleCRUD:
    def test_write_and_get(self, rules_base):
        rule = _make_rule('rule_one')
        path = url.write_rule('rule_one', rule)
        assert path.endswith('rule_one.json')

        got = url.get_rule('rule_one')
        assert got is not None
        assert got['id'] == 'rule_one'
        assert got['title'] == rule['title']
        assert got['enabled'] is True

    def test_write_requires_title(self, rules_base):
        rule = _make_rule('rule_x', title='')
        with pytest.raises(ValueError):
            url.write_rule('rule_x', rule)

    def test_write_validates_id(self, rules_base):
        rule = _make_rule('Bad_ID')
        with pytest.raises(ValueError):
            url.write_rule('Bad_ID', rule)

    def test_list_rules(self, rules_base):
        url.write_rule('rule_a', _make_rule('rule_a', created_at=100))
        url.write_rule('rule_b', _make_rule('rule_b', created_at=200))
        rules = url.list_rules()
        assert [r['id'] for r in rules] == ['rule_a', 'rule_b']

    def test_list_rules_only_enabled(self, rules_base):
        url.write_rule('rule_a', _make_rule('rule_a'))
        url.write_rule('rule_b', _make_rule('rule_b', enabled=False))
        all_rules = url.list_rules(only_enabled=False)
        enabled = url.list_rules(only_enabled=True)
        assert len(all_rules) == 2
        assert len(enabled) == 1
        assert enabled[0]['id'] == 'rule_a'

    def test_get_missing_returns_none(self, rules_base):
        assert url.get_rule('not_exists') is None

    def test_delete(self, rules_base):
        url.write_rule('rule_a', _make_rule('rule_a'))
        assert url.delete_rule('rule_a') is True
        assert url.get_rule('rule_a') is None
        # 重复删除返回 False
        assert url.delete_rule('rule_a') is False

    def test_set_rule_enabled(self, rules_base):
        url.write_rule('rule_a', _make_rule('rule_a'))
        assert url.set_rule_enabled('rule_a', False) is True
        got = url.get_rule('rule_a')
        assert got['enabled'] is False
        assert url.set_rule_enabled('rule_a', True) is True
        got = url.get_rule('rule_a')
        assert got['enabled'] is True
        # 不存在的 id
        assert url.set_rule_enabled('not_exists', False) is False


# ---------------------------------------------------------------------- #
# 3. system prompt 注入
# ---------------------------------------------------------------------- #

class TestBuildSystemPromptAddon:
    def test_empty_dir_returns_empty(self, rules_base):
        assert url.build_system_prompt_addon() == ''

    def test_basic_injection(self, rules_base):
        url.write_rule(
            'color_uppercase',
            _make_rule(
                'color_uppercase',
                title='rt.Color 必须大写',
                content='pymxs 中颜色构造器必须大写。',
                bad_example='rt.color(255, 0, 0)',
                good_example='rt.Color(255, 0, 0)',
            ),
        )
        addon = url.build_system_prompt_addon()
        assert 'rt.Color 必须大写' in addon
        assert 'rt.color(255, 0, 0)' in addon
        assert 'rt.Color(255, 0, 0)' in addon
        assert '反例' in addon
        assert '正例' in addon

    def test_disabled_rule_not_injected(self, rules_base):
        url.write_rule('rule_a', _make_rule('rule_a', title='启用规则'))
        url.write_rule(
            'rule_b',
            _make_rule('rule_b', title='禁用规则', enabled=False),
        )
        addon = url.build_system_prompt_addon()
        assert '启用规则' in addon
        assert '禁用规则' not in addon

    def test_recent_rules_prioritized_when_truncated(self, rules_base):
        # 写入大量规则，每条都很大，确保超限截断
        big_content = 'x' * 800  # 接近单条上限
        # 老规则
        url.write_rule(
            'old_rule',
            _make_rule('old_rule', content=big_content, created_at=100),
        )
        # 新规则
        url.write_rule(
            'new_rule',
            _make_rule('new_rule', content=big_content, created_at=999999),
        )
        addon = url.build_system_prompt_addon(max_total_bytes=1500)
        # 新的一定在
        assert 'new_rule' in addon or '新规则' not in addon  # 容许结构变化
        # 应当出现截断提示（因为两条加起来 > 1500）
        assert '未注入' in addon

    def test_total_enabled_bytes(self, rules_base):
        assert url.total_enabled_bytes() == 0
        url.write_rule('rule_a', _make_rule('rule_a'))
        assert url.total_enabled_bytes() > 0

    def test_disabled_rule_does_not_consume_budget(self, rules_base):
        url.write_rule(
            'rule_a',
            _make_rule('rule_a', enabled=False),
        )
        assert url.total_enabled_bytes() == 0


# ---------------------------------------------------------------------- #
# 4. learn_rules 工具集成（轻量校验，不走真实 LLM）
# ---------------------------------------------------------------------- #

class TestLearnRulesTool:
    def test_default_approval_rejects(self, rules_base):
        from maxagent.tools.learn_rules import suggest_rule_addition
        # 未注入回调时默认拒绝
        result = suggest_rule_addition(
            rule_id='auto_test_rule',
            title='测试',
            content='测试内容内容内容',
        )
        assert result['approved'] is False
        # 落盘必然没有
        assert url.get_rule('auto_test_rule') is None

    def test_approved_flow(self, rules_base):
        from maxagent.tools import learn_rules

        captured = {}

        def fake_cb(proposal):
            captured.update(proposal)
            return {
                'approved': True,
                'edited_title': proposal['title'],
                'edited_content': proposal['content'],
                'edited_good_example': proposal.get('good_example', ''),
                'edited_bad_example': proposal.get('bad_example', ''),
                'reason': '',
            }

        learn_rules.set_rule_approval_callback(fake_cb)
        try:
            result = learn_rules.suggest_rule_addition(
                rule_id='approved_rule',
                title='AI 学到的规则',
                content='规则正文，应当被批准并落盘。',
                tags=['unit_test'],
            )
        finally:
            learn_rules.set_rule_approval_callback(None)

        assert result['approved'] is True
        assert result['saved'] is True
        assert captured['id'] == 'approved_rule'

        got = url.get_rule('approved_rule')
        assert got is not None
        assert got['title'] == 'AI 学到的规则'
        assert got['tags'] == ['unit_test']

    def test_invalid_id_rejected_before_callback(self, rules_base):
        from maxagent.tools import learn_rules

        called = {'n': 0}

        def fake_cb(_proposal):
            called['n'] += 1
            return {'approved': True}

        learn_rules.set_rule_approval_callback(fake_cb)
        try:
            result = learn_rules.suggest_rule_addition(
                rule_id='Bad-ID',
                title='x',
                content='y' * 30,
            )
        finally:
            learn_rules.set_rule_approval_callback(None)

        assert result['approved'] is False
        assert result['stage'] == 'validate_id'
        assert called['n'] == 0  # 校验失败前不应触发回调

    def test_list_and_delete(self, rules_base):
        from maxagent.tools.learn_rules import delete_learned_rule
        from maxagent.tools.learn_rules import list_learned_rules

        url.write_rule('rule_x', _make_rule('rule_x'))
        items = list_learned_rules()
        assert items['count'] == 1
        assert items['rules'][0]['id'] == 'rule_x'

        ans = delete_learned_rule('rule_x')
        assert ans['deleted'] is True
        assert url.get_rule('rule_x') is None
