#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""技能管理工具：让 LLM 自己学会、查询、删除技能。

本模块定义 4 个工具：
- save_skill：保存一个新技能或更新已有技能
- list_skills：列出所有已学技能
- show_skill：查看某个技能的详细 instructions
- delete_skill：删除一个技能

设计原则：
- 这些工具不操作 Max 场景，所以 ``run_on_main_thread=False``、
  ``wrap_undo=False``，纯文件 IO，子线程直接执行即可。
- ``save_skill`` 不算 dangerous（不执行代码、可随时删除），
  无需用户弹窗确认。
- 触发时机：当用户说"以后这种叫 XXX"、"把刚才的流程记下来"、
  "学会一个新技能"等指令时，LLM 应自动调用 save_skill。
  这部分引导写在 system prompt 里（在 conversation.DEFAULT_SYSTEM_PROMPT
  之外，由 SkillManager.build_system_prompt_addon 拼接）。
"""

from __future__ import absolute_import
from __future__ import print_function

from typing import List
from typing import Optional

from ..skills import Skill
from ..skills import SkillManager
from .registry import tool


# 模块级单例 manager，避免每次工具调用都重新扫盘
_MGR = None  # type: Optional[SkillManager]


def _mgr():
    global _MGR  # pylint: disable=global-statement
    if _MGR is None:
        _MGR = SkillManager()
    return _MGR


def reset_manager_for_test(base_dir=None):
    """测试用：重置 manager 单例到指定目录。"""
    global _MGR  # pylint: disable=global-statement
    _MGR = SkillManager(base_dir=base_dir)


@tool(
    name='save_skill',
    description=(
        '保存一个新技能（或更新已有同名技能）。'
        '当用户明确说"以后这种叫 XXX"、"把刚才的流程记下来"、'
        '"学会一个新技能"等长期保留意图时调用此工具。'
        '技能本质是一段自然语言流程描述，用户下次提到 trigger_keywords 中的'
        '任一关键词时，会自动把 instructions 注入给你参考执行。'
    ),
    category='skills',
    dangerous=False,
    wrap_undo=False,
    run_on_main_thread=False,
)
def save_skill(name, description, instructions, trigger_keywords=None):
    # type: (str, str, str, Optional[List[str]]) -> dict
    """保存技能。

    :param name: 技能名（中英文/数字/下划线/短横线/空格，1-32 字符），
        例如 "标准导出"
    :param description: 一句话描述这个技能的用途，便于以后回忆
    :param instructions: 详细的执行步骤，自然语言描述；下次触发时会
        被注入给你阅读，所以请写得清晰可执行（步骤、参数、检查点等）
    :param trigger_keywords: 触发关键词列表（用户输入命中其中任一时
        会激活本技能的详细 instructions）。例如 ["标准导出", "export fbx"]
    """
    skill = Skill(
        name=name,
        description=description,
        trigger_keywords=trigger_keywords or [],
        instructions=instructions,
    )
    saved = _mgr().save(skill, overwrite=True)
    return {
        'saved': True,
        'name': saved.name,
        'description': saved.description,
        'trigger_keywords': saved.trigger_keywords,
        'instructions_chars': len(saved.instructions),
    }


@tool(
    name='list_skills',
    description=(
        '列出所有已学技能的简要信息。'
        '当用户问"你会做什么"、"我教过你哪些技能"时调用。'
    ),
    category='skills',
    dangerous=False,
    wrap_undo=False,
    run_on_main_thread=False,
)
def list_skills():
    """列出所有技能（不返回完整 instructions，避免 token 浪费）。"""
    skills = _mgr().list_skills()
    out = []
    for s in skills:
        out.append({
            'name': s.name,
            'description': s.description,
            'trigger_keywords': s.trigger_keywords,
            'use_count': s.use_count,
        })
    return {'count': len(out), 'skills': out}


@tool(
    name='show_skill',
    description=(
        '查看某个技能的完整 instructions（详细执行步骤）。'
        '当你需要按某个技能办事但 system prompt 里没有自动注入时调用。'
    ),
    category='skills',
    dangerous=False,
    wrap_undo=False,
    run_on_main_thread=False,
)
def show_skill(name):
    """查看指定技能的完整内容。

    :param name: 技能名
    """
    s = _mgr().get(name)
    if s is None:
        return {'found': False, 'error': '技能不存在: {}'.format(name)}
    return {
        'found': True,
        'name': s.name,
        'description': s.description,
        'trigger_keywords': s.trigger_keywords,
        'instructions': s.instructions,
        'use_count': s.use_count,
    }


@tool(
    name='delete_skill',
    description=(
        '删除一个已学技能。'
        '当用户明确说"忘掉 XXX"、"删除技能 YYY"时调用。'
    ),
    category='skills',
    dangerous=False,
    wrap_undo=False,
    run_on_main_thread=False,
)
def delete_skill(name):
    """删除指定技能。

    :param name: 要删除的技能名
    """
    ok = _mgr().delete(name)
    return {'deleted': ok, 'name': name}


__all__ = [
    'save_skill',
    'list_skills',
    'show_skill',
    'delete_skill',
    'reset_manager_for_test',
]
