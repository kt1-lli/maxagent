#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试 reflection_tools 三个工具：reflect_on_outcome / list / delete。"""

from __future__ import absolute_import
from __future__ import print_function

import pytest

from maxagent import reflections_loader as rfl
from maxagent.tools import reflection_tools as rt


@pytest.fixture
def reflections_base(tmp_path):
    base = str(tmp_path / 'reflections')
    rfl.set_reflections_dir_override(base)
    yield base
    rfl.set_reflections_dir_override(None)


class TestReflectOnOutcome:
    def test_minimal_save(self, reflections_base):
        result = rt.reflect_on_outcome(
            task_summary='测试任务',
            lessons='下次先校验输入',
        )
        assert result['saved'] is True
        assert result['reflection_id'].startswith('rfl_')

    def test_full_fields(self, reflections_base):
        result = rt.reflect_on_outcome(
            task_summary='批量改名',
            lessons='先 isValidNode',
            what_went_wrong='5 个对象漏改',
            what_went_well='正则匹配准',
            tags=['rename', 'pymxs'],
        )
        assert result['saved'] is True
        rfls = rfl.list_reflections()
        assert len(rfls) == 1
        assert rfls[0]['tags'] == ['rename', 'pymxs']
        assert rfls[0]['what_went_wrong'] == '5 个对象漏改'

    def test_empty_task_summary_rejected(self, reflections_base):
        result = rt.reflect_on_outcome(
            task_summary='',
            lessons='x',
        )
        assert result['saved'] is False
        assert 'task_summary' in result['error']

    def test_empty_lessons_rejected(self, reflections_base):
        result = rt.reflect_on_outcome(
            task_summary='ok',
            lessons='',
        )
        assert result['saved'] is False
        assert 'lessons' in result['error']

    def test_oversized_rejected(self, reflections_base):
        big = 'x' * (rfl.MAX_REFLECTION_BYTES + 100)
        result = rt.reflect_on_outcome(
            task_summary='ok',
            lessons=big,
        )
        assert result['saved'] is False


class TestListReflections:
    def test_empty(self, reflections_base):
        result = rt.list_reflections()
        assert result['count'] == 0
        assert result['reflections'] == []

    def test_after_write(self, reflections_base):
        rt.reflect_on_outcome(
            task_summary='t1',
            lessons='l1',
            tags=['t'],
        )
        rt.reflect_on_outcome(
            task_summary='t2',
            lessons='l2',
        )
        result = rt.list_reflections()
        assert result['count'] == 2
        # 字段应当被脱敏暴露：含 id/summary/lessons/tags/created_at
        first = result['reflections'][0]
        assert 'id' in first
        assert 'task_summary' in first
        assert 'lessons' in first
        assert 'tags' in first


class TestDeleteReflection:
    def test_delete_existing(self, reflections_base):
        save_result = rt.reflect_on_outcome(
            task_summary='x',
            lessons='y',
        )
        rid = save_result['reflection_id']
        del_result = rt.delete_reflection(rid)
        assert del_result['deleted'] is True
        assert rt.list_reflections()['count'] == 0

    def test_delete_nonexistent(self, reflections_base):
        result = rt.delete_reflection('rfl_does_not_exist')
        assert result['deleted'] is False
