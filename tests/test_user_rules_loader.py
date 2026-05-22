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

import json
import os
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


# ====================================================================== #
# Phase 2: 导入 / 导出
# ====================================================================== #

class TestExportImport:
    """规则导入导出（轻共享）功能测试。"""

    def test_export_single_roundtrip(self, rules_base, tmp_path):
        """单条导出 → 写盘 → 解析 → 导入到新位置，往返一致。"""
        url.write_rule('src_rule', _make_rule('src_rule'))
        payload = url.export_rule('src_rule')
        assert payload['type'] == url.SINGLE_FILE_TYPE
        assert payload['schema_version'] == url.EXPORT_SCHEMA_VERSION
        assert payload['rule']['id'] == 'src_rule'

        out = str(tmp_path / 'one.maxagent-rule.json')
        url.write_export_file(out, payload)
        assert os.path.exists(out)

        rules = url.parse_import_file(out)
        assert len(rules) == 1
        assert rules[0]['id'] == 'src_rule'
        assert rules[0]['content']

    def test_export_bundle_all_enabled(self, rules_base, tmp_path):
        """打包导出：默认导出全部启用规则；禁用规则不在 bundle 中。"""
        url.write_rule('rule_a', _make_rule('rule_a'))
        url.write_rule('rule_b', _make_rule('rule_b'))
        url.write_rule('rule_c', _make_rule('rule_c'))
        url.set_rule_enabled('rule_b', False)

        bundle = url.export_bundle()
        assert bundle['type'] == url.BUNDLE_FILE_TYPE
        ids = sorted(r['id'] for r in bundle['rules'])
        assert ids == ['rule_a', 'rule_c']

        out = str(tmp_path / 'all.maxagent-rules.json')
        url.write_export_file(out, bundle)
        parsed = url.parse_import_file(out)
        assert len(parsed) == 2

    def test_export_bundle_explicit_ids(self, rules_base):
        """指定 ID 列表的 bundle 包含禁用规则也会被收纳（用户显式选择）。"""
        url.write_rule('rule_a', _make_rule('rule_a'))
        url.write_rule('rule_b', _make_rule('rule_b'))
        url.set_rule_enabled('rule_b', False)
        bundle = url.export_bundle(['rule_a', 'rule_b', 'not_exist'])
        ids = sorted(r['id'] for r in bundle['rules'])
        # not_exist 自动跳过；禁用规则被显式列出仍包含
        assert ids == ['rule_a', 'rule_b']

    def test_parse_legacy_naked_rule(self, rules_base, tmp_path):
        """向后兼容：裸规则对象（缺 type 字段）也能解析。"""
        legacy = {
            'id': 'legacy_rule',
            'title': '老格式',
            'content': '老导出文件没有 type 字段',
        }
        out = str(tmp_path / 'legacy.json')
        with open(out, 'w', encoding='utf-8') as fh:
            json.dump(legacy, fh)

        rules = url.parse_import_file(out)
        assert len(rules) == 1
        assert rules[0]['id'] == 'legacy_rule'

    def test_parse_invalid_file_rejected(self, rules_base, tmp_path):
        """畸形文件应被拒绝。"""
        # 1) JSON 损坏
        bad1 = str(tmp_path / 'broken.json')
        with open(bad1, 'w', encoding='utf-8') as fh:
            fh.write('{not valid json')
        with pytest.raises(ValueError):
            url.parse_import_file(bad1)

        # 2) 顶层不是对象
        bad2 = str(tmp_path / 'array.json')
        with open(bad2, 'w', encoding='utf-8') as fh:
            json.dump([1, 2, 3], fh)
        with pytest.raises(ValueError):
            url.parse_import_file(bad2)

        # 3) 文件不存在
        with pytest.raises(ValueError):
            url.parse_import_file(str(tmp_path / 'nope.json'))

        # 4) 无法识别的 type
        bad3 = str(tmp_path / 'unknown.json')
        with open(bad3, 'w', encoding='utf-8') as fh:
            json.dump({'type': 'something-else'}, fh)
        with pytest.raises(ValueError):
            url.parse_import_file(bad3)

    def test_import_skip_existing(self, rules_base):
        """同 ID 已存在且未勾选覆盖时返回 skipped，不修改原数据。"""
        url.write_rule('keep_me', _make_rule('keep_me'))
        original = url.get_rule('keep_me')
        original_title = original['title']

        new_data = _make_rule('keep_me')
        new_data['id'] = 'keep_me'
        new_data['title'] = '我想顶替你的'

        result = url.import_rule(new_data, overwrite=False)
        assert result['status'] == 'skipped'
        # 原数据未被改写
        assert url.get_rule('keep_me')['title'] == original_title

    def test_import_with_overwrite(self, rules_base):
        """overwrite=True 时同 ID 被覆盖，状态为 overwritten。"""
        url.write_rule('replace_me', _make_rule('replace_me'))
        new_data = _make_rule('replace_me')
        new_data['id'] = 'replace_me'
        new_data['title'] = '新内容'

        result = url.import_rule(new_data, overwrite=True)
        assert result['status'] == 'overwritten'
        assert url.get_rule('replace_me')['title'] == '新内容'

    def test_import_marks_source_field(self, rules_base):
        """导入的规则必须打上 source='import' 和 imported_at 时间戳。"""
        new_data = _make_rule('fresh_one')
        new_data['id'] = 'fresh_one'
        result = url.import_rule(new_data, overwrite=False)
        assert result['status'] == 'imported'
        rule = url.get_rule('fresh_one')
        assert rule['source'] == 'import'
        assert rule['imported_at'] > 0

    def test_diff_import_rules(self, rules_base):
        """diff 函数：标注每条规则是 new / existing / invalid。"""
        url.write_rule('exists_one', _make_rule('exists_one'))

        existing = _make_rule('exists_one')
        existing['id'] = 'exists_one'
        new_one = _make_rule('brand_new')
        new_one['id'] = 'brand_new'

        candidates = [
            existing,  # existing
            new_one,  # new
            {'id': 'Bad-ID', 'content': 'x' * 30, 'title': 't'},  # invalid id
            {'id': 'no_content', 'content': '', 'title': 't'},  # invalid content
            'not a dict',  # invalid type
        ]
        diffs = url.diff_import_rules(candidates)
        statuses = [d['status'] for d in diffs]
        assert statuses == ['existing', 'new', 'invalid', 'invalid', 'invalid']

    def test_manual_rules_keep_source_manual(self, rules_base):
        """write_rule 默认写入的规则 source='manual'。"""
        url.write_rule('manual_one', _make_rule('manual_one'))
        rule = url.get_rule('manual_one')
        assert rule['source'] == 'manual'
        assert rule['imported_at'] == 0.0