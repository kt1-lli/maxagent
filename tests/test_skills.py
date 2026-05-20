#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试 SkillManager：CRUD + 触发词匹配。"""

from __future__ import absolute_import
from __future__ import print_function

import os

import pytest

from maxagent.skills import (
    MAX_INSTRUCTIONS_CHARS,
    Skill,
    SkillManager,
)


@pytest.fixture
def skills_dir(tmp_path):
    """每个测试一个独立目录。"""
    d = tmp_path / 'skills'
    d.mkdir()
    return str(d)


class TestSkillCRUD:
    def test_save_then_get(self, skills_dir):
        m = SkillManager(base_dir=skills_dir)
        s = Skill(
            name='测试技能',
            description='做某事',
            trigger_keywords=['key1', 'key2'],
            instructions='step1\nstep2',
        )
        m.save(s)
        out = m.get('测试技能')
        assert out is not None
        assert out.description == '做某事'
        assert 'key1' in out.trigger_keywords

    def test_list_sorted_by_updated_at(self, skills_dir):
        m = SkillManager(base_dir=skills_dir)
        m.save(Skill(name='old', instructions='x'))
        m.save(Skill(name='new', instructions='y'))
        # 第二个更新更晚，应排在前面
        names = [s.name for s in m.list_skills()]
        assert names[0] == 'new'

    def test_delete(self, skills_dir):
        m = SkillManager(base_dir=skills_dir)
        m.save(Skill(name='a', instructions='x'))
        assert m.delete('a') is True
        assert m.get('a') is None
        assert m.delete('not_exist') is False

    def test_invalid_name_rejected(self, skills_dir):
        m = SkillManager(base_dir=skills_dir)
        with pytest.raises(ValueError):
            m.save(Skill(name='', instructions='x'))
        with pytest.raises(ValueError):
            # 超长 name
            m.save(Skill(name='x' * 100, instructions='y'))

    def test_empty_instructions_rejected(self, skills_dir):
        m = SkillManager(base_dir=skills_dir)
        with pytest.raises(ValueError):
            m.save(Skill(name='ok', instructions=''))

    def test_too_long_instructions(self, skills_dir):
        m = SkillManager(base_dir=skills_dir)
        with pytest.raises(ValueError):
            m.save(Skill(
                name='big',
                instructions='x' * (MAX_INSTRUCTIONS_CHARS + 1),
            ))


class TestSkillPromptInjection:
    def test_no_skills_returns_empty(self, skills_dir):
        m = SkillManager(base_dir=skills_dir)
        assert m.build_system_prompt_addon('anything') == ''

    def test_lists_all_skills_briefs(self, skills_dir):
        m = SkillManager(base_dir=skills_dir)
        m.save(Skill(
            name='A', description='descA',
            trigger_keywords=['kA'], instructions='iA',
        ))
        m.save(Skill(
            name='B', description='descB',
            trigger_keywords=['kB'], instructions='iB',
        ))
        out = m.build_system_prompt_addon('hello')
        # 两个 skill 的 brief 都应出现
        assert 'A' in out and 'B' in out
        assert 'descA' in out and 'descB' in out
        # 没有命中触发词时不应注入完整 instructions
        assert 'iA' not in out
        assert 'iB' not in out

    def test_injects_full_instructions_on_trigger(self, skills_dir):
        m = SkillManager(base_dir=skills_dir)
        m.save(Skill(
            name='Export',
            description='export FBX',
            trigger_keywords=['标准导出'],
            instructions='1. 检查\n2. 导出\n3. 命名规范',
        ))
        out = m.build_system_prompt_addon('请帮我做一次标准导出')
        # 应注入完整 instructions
        assert '1. 检查' in out
        assert '导出' in out
        assert '命名规范' in out

    def test_case_insensitive_trigger(self, skills_dir):
        m = SkillManager(base_dir=skills_dir)
        m.save(Skill(
            name='Export',
            trigger_keywords=['EXPORT'],
            instructions='do export',
        ))
        out = m.build_system_prompt_addon('please export now')
        assert 'do export' in out

    def test_no_trigger_match(self, skills_dir):
        m = SkillManager(base_dir=skills_dir)
        m.save(Skill(
            name='X',
            trigger_keywords=['rare_kw'],
            instructions='secret',
        ))
        out = m.build_system_prompt_addon('totally unrelated')
        assert 'secret' not in out


class TestSkillFileBackend:
    def test_corrupt_file_skipped(self, skills_dir):
        # 写入一个非法 JSON 文件
        bad = os.path.join(skills_dir, 'bad.json')
        with open(bad, 'w', encoding='utf-8') as fh:
            fh.write('{not json')

        m = SkillManager(base_dir=skills_dir)
        # 应不抛异常
        skills = m.list_skills()
        assert len(skills) == 0

    def test_increment_use_count_persists(self, skills_dir):
        m = SkillManager(base_dir=skills_dir)
        m.save(Skill(name='Y', instructions='z'))
        m.increment_use_count('Y')
        m.increment_use_count('Y')
        s = m.get('Y')
        assert s.use_count == 2
