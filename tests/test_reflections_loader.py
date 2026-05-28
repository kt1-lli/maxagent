#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试 reflections_loader 的核心行为：写入、列举、删除、prompt 注入。"""

from __future__ import absolute_import
from __future__ import print_function

import json
import os
import time

import pytest

from maxagent import reflections_loader as rfl


@pytest.fixture
def reflections_base(tmp_path):
    """把反思目录指到 tmp，函数级别隔离。"""
    base = str(tmp_path / 'reflections')
    rfl.set_reflections_dir_override(base)
    yield base
    rfl.set_reflections_dir_override(None)


class TestValidateReflectionId:
    def test_valid(self):
        rfl.validate_reflection_id('rfl_1700000000_abc12345')
        rfl.validate_reflection_id('rfl_test_1')

    def test_invalid(self):
        for bad in ('', 'no_prefix', 'rfl_', 'RFL_upper', 'rfl_with-dash'):
            with pytest.raises(ValueError):
                rfl.validate_reflection_id(bad)


class TestValidatePayload:
    def test_valid(self):
        rfl.validate_reflection_payload({
            'task_summary': '批量改名 100 个对象',
            'lessons': '先 isValidNode 过滤再操作',
        })

    def test_missing_summary(self):
        with pytest.raises(ValueError):
            rfl.validate_reflection_payload({
                'task_summary': '',
                'lessons': 'x',
            })

    def test_missing_lessons(self):
        with pytest.raises(ValueError):
            rfl.validate_reflection_payload({
                'task_summary': 'x',
                'lessons': '   ',
            })

    def test_too_large(self):
        big = 'x' * (rfl.MAX_REFLECTION_BYTES + 100)
        with pytest.raises(ValueError):
            rfl.validate_reflection_payload({
                'task_summary': 'short',
                'lessons': big,
            })


class TestWriteAndList:
    def test_write_then_list(self, reflections_base):
        rid = rfl.write_reflection({
            'task_summary': '改材质颜色',
            'lessons': 'rt.Color 必须大写 C',
            'tags': ['material', 'pymxs'],
        })
        assert rid.startswith('rfl_')
        items = rfl.list_reflections()
        assert len(items) == 1
        assert items[0]['id'] == rid
        assert items[0]['lessons'] == 'rt.Color 必须大写 C'

    def test_list_sorted_desc(self, reflections_base):
        r1 = rfl.write_reflection({
            'task_summary': 'task1',
            'lessons': 'lesson1',
        })
        time.sleep(0.01)
        r2 = rfl.write_reflection({
            'task_summary': 'task2',
            'lessons': 'lesson2',
        })
        items = rfl.list_reflections()
        # 新的在前
        assert items[0]['id'] == r2
        assert items[1]['id'] == r1

    def test_get_and_delete(self, reflections_base):
        rid = rfl.write_reflection({
            'task_summary': 's',
            'lessons': 'l',
        })
        assert rfl.get_reflection(rid) is not None
        assert rfl.delete_reflection(rid) is True
        assert rfl.get_reflection(rid) is None
        # 双删返回 False
        assert rfl.delete_reflection(rid) is False

    def test_only_recent_filters_old(self, reflections_base):
        # 手工写一条"很老"的反思
        rfl.get_reflections_dir()  # 确保目录存在
        old_rid = 'rfl_1_old00000'
        old_path = os.path.join(reflections_base, old_rid + '.json')
        with open(old_path, 'w', encoding='utf-8') as fh:
            json.dump({
                'id': old_rid,
                'task_summary': 'old',
                'lessons': 'old lesson',
                'created_at': time.time() - rfl.MAX_AGE_SECONDS - 100,
            }, fh)
        rfl.write_reflection({
            'task_summary': 'new',
            'lessons': 'new lesson',
        })
        all_items = rfl.list_reflections(only_recent=False)
        assert len(all_items) == 2
        recent = rfl.list_reflections(only_recent=True)
        assert len(recent) == 1
        assert recent[0]['task_summary'] == 'new'


class TestPromptAddon:
    def test_empty_returns_only_guidance(self, reflections_base):
        # 无反思时也应至少返回主动反思指引（让 LLM 知道工具存在）
        addon = rfl.build_system_prompt_addon()
        assert addon != ''
        assert 'reflect_on_outcome' in addon
        assert '主动反思' in addon
        # 不应该出现"历史反思列表段头"
        assert '## 你最近的反思' not in addon

    def test_addon_includes_lessons(self, reflections_base):
        rfl.write_reflection({
            'task_summary': 'rename objects',
            'lessons': 'use isValidNode',
            'what_went_wrong': '5 nodes were deleted before rename',
        })
        addon = rfl.build_system_prompt_addon()
        assert 'rename objects' in addon
        assert 'use isValidNode' in addon
        assert '反思' in addon  # header keyword

    def test_addon_respects_max_count(self, reflections_base):
        for i in range(5):
            rfl.write_reflection({
                'task_summary': 'task_{}'.format(i),
                'lessons': 'lesson_{}'.format(i),
            })
            time.sleep(0.001)
        addon = rfl.build_system_prompt_addon(max_count=2)
        # 只取最新 2 条
        assert 'task_4' in addon
        assert 'task_3' in addon
        assert 'task_2' not in addon

    def test_addon_respects_byte_limit(self, reflections_base):
        for i in range(5):
            rfl.write_reflection({
                'task_summary': 'task_{}'.format(i),
                'lessons': 'l' * 100,
            })
        # 反思条目部分应该被字节预算限制——验证最多只能塞下 2 条
        # （guidance 头部固定 ~1500 字节是不计入预算的）
        addon_small = rfl.build_system_prompt_addon(max_total_bytes=200)
        addon_full = rfl.build_system_prompt_addon(max_total_bytes=10000)
        # 小预算时反思条目应该明显少于无限预算
        assert addon_full.count('task_') > addon_small.count('task_')

    def test_addon_skips_old_reflections(self, reflections_base):
        rfl.get_reflections_dir()
        old_rid = 'rfl_1_old00000'
        old_path = os.path.join(reflections_base, old_rid + '.json')
        with open(old_path, 'w', encoding='utf-8') as fh:
            json.dump({
                'id': old_rid,
                'task_summary': 'too old',
                'lessons': 'old lesson',
                'created_at': time.time() - rfl.MAX_AGE_SECONDS - 100,
            }, fh)
        addon = rfl.build_system_prompt_addon()
        # 老于 30 天的不注入
        assert 'too old' not in addon
