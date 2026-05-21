#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""技能（Skill / Playbook）管理。

Skill 与"工具（Tool）"的区别：
- Tool 是 Python 函数，对应一次具体的 pymxs 调用（如 ``create_box``）。
- Skill 是一段"自然语言流程描述 + 触发关键词"，存为 JSON。
  当用户的输入命中触发关键词时，把 Skill 的详细 instructions
  注入到 LLM 的 system prompt，让 LLM 按照流程调用既有工具。

为什么不让 Skill 也写代码？
1. 安全：Skill 不执行任何代码，纯文本指令，没有 RCE 风险。
2. 灵活：LLM 可以根据当前场景情况调整步骤，而不是死板按脚本走。
3. 复用：Skill 直接复用已有的 ToolDispatcher，无需独立运行时。

文件位置::

    {config_dir}/skills/<skill_name>.json
    {config_dir}/skills/_index.json

数据结构::

    {
      "name": "标准导出",
      "description": "把当前选中物体烘焙后导出为 FBX，按命名规范处理",
      "trigger_keywords": ["标准导出", "导出fbx"],
      "instructions": "1. 检查选中物体...\n2. ...",
      "created_at": 1700000000.0,
      "updated_at": 1700000000.0,
      "use_count": 3,
      "source_session_sid": "abc12345"
    }
"""

from __future__ import absolute_import
from __future__ import print_function

import json
import os
import re
import time
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from .config import get_config_dir
from .logger import get_logger


SKILLS_DIRNAME = 'skills'
INDEX_FILENAME = '_index.json'

logger = get_logger(__name__)

# Skill 名字校验：只允许中英文 + 数字 + 下划线 + 短横线 + 空格
_NAME_RE = re.compile(r'^[\w\u4e00-\u9fa5\- ]{1,32}$')

# 单个 instructions 最大长度（防止 LLM 写出过长指令污染 prompt）
MAX_INSTRUCTIONS_CHARS = 4000

# 在 system prompt 中注入"已学技能"摘要时每条 desc 最大字符数
MAX_BRIEF_DESC_CHARS = 80


def get_skills_dir():
    base = get_config_dir()
    path = os.path.join(base, SKILLS_DIRNAME)
    if not os.path.isdir(path):
        os.makedirs(path)
    return path


def _safe_filename(name):
    """把 skill name 转为安全的文件名片段。"""
    cleaned = re.sub(r'[^\w\u4e00-\u9fa5\-]+', '_', name).strip('_')
    return cleaned[:48] or 'skill'


class Skill(object):
    """一个 Skill 实例（纯数据对象）。"""

    def __init__(self, name, description='', trigger_keywords=None,
                 instructions='', created_at=None, updated_at=None,
                 use_count=0, source_session_sid='', file_path=None):
        # type: (str, str, Optional[List[str]], str, Optional[float], Optional[float], int, str, Optional[str]) -> None
        self.name = name
        self.description = description or ''
        self.trigger_keywords = list(trigger_keywords or [])
        self.instructions = instructions or ''
        now = time.time()
        self.created_at = float(created_at if created_at is not None else now)
        self.updated_at = float(updated_at if updated_at is not None else now)
        self.use_count = int(use_count)
        self.source_session_sid = source_session_sid or ''
        self.file_path = file_path

    def to_dict(self):
        return {
            'name': self.name,
            'description': self.description,
            'trigger_keywords': list(self.trigger_keywords),
            'instructions': self.instructions,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'use_count': self.use_count,
            'source_session_sid': self.source_session_sid,
            'file_path': self.file_path,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            name=data.get('name', ''),
            description=data.get('description', ''),
            trigger_keywords=data.get('trigger_keywords') or [],
            instructions=data.get('instructions', ''),
            created_at=data.get('created_at'),
            updated_at=data.get('updated_at'),
            use_count=int(data.get('use_count', 0) or 0),
            source_session_sid=data.get('source_session_sid', ''),
            file_path=data.get('file_path'),
        )

    def brief(self):
        """简短一行描述，给 system prompt 用。"""
        desc = self.description.strip().replace('\n', ' ')
        if len(desc) > MAX_BRIEF_DESC_CHARS:
            desc = desc[:MAX_BRIEF_DESC_CHARS] + '...'
        kws = ' / '.join(self.trigger_keywords[:3]) if self.trigger_keywords else ''
        if kws:
            return '- {name}（触发词: {kw}）：{desc}'.format(
                name=self.name, kw=kws, desc=desc,
            )
        return '- {name}：{desc}'.format(name=self.name, desc=desc)


class SkillManager(object):
    """技能 CRUD + 触发匹配。"""

    def __init__(self, base_dir=None):
        # type: (Optional[str]) -> None
        self._base = base_dir or get_skills_dir()
        if not os.path.isdir(self._base):
            os.makedirs(self._base)

    # ------------------------------------------------------------------ #
    # 路径
    # ------------------------------------------------------------------ #
    def _index_path(self):
        return os.path.join(self._base, INDEX_FILENAME)

    def _file_path_for(self, skill):
        # type: (Skill) -> str
        if skill.file_path and os.path.dirname(skill.file_path) == self._base:
            return skill.file_path
        return os.path.join(
            self._base, '{}.json'.format(_safe_filename(skill.name)),
        )

    # ------------------------------------------------------------------ #
    # 索引（损坏时扫描重建）
    # ------------------------------------------------------------------ #
    def _scan(self):
        # type: () -> List[Skill]
        out = []
        if not os.path.isdir(self._base):
            return out
        for fname in os.listdir(self._base):
            if fname == INDEX_FILENAME or not fname.endswith('.json'):
                continue
            full = os.path.join(self._base, fname)
            try:
                with open(full, 'r', encoding='utf-8') as fh:
                    data = json.load(fh)
                s = Skill.from_dict(data)
                s.file_path = full
                out.append(s)
            except (OSError, ValueError) as exc:
                logger.warning(
                    'skip 损坏的 skill 文件 %s: %s', fname, exc,
                )
        out.sort(key=lambda s: s.updated_at, reverse=True)
        return out

    # ------------------------------------------------------------------ #
    # 公共 API
    # ------------------------------------------------------------------ #
    def list_skills(self):
        # type: () -> List[Skill]
        return self._scan()

    def get(self, name):
        # type: (str) -> Optional[Skill]
        for s in self._scan():
            if s.name == name:
                return s
        return None

    def save(self, skill, overwrite=True):
        # type: (Skill, bool) -> Skill
        """保存（创建或更新）一个 Skill。

        :param overwrite: 同名时是否覆盖；False 且已存在则抛 ValueError
        """
        # 校验
        if not skill.name or not _NAME_RE.match(skill.name):
            raise ValueError(
                '技能名只能包含中英文/数字/下划线/短横线/空格，'
                '长度1-32: {}'.format(skill.name),
            )
        if not skill.instructions.strip():
            raise ValueError('技能 instructions 不能为空')
        if len(skill.instructions) > MAX_INSTRUCTIONS_CHARS:
            raise ValueError(
                '技能 instructions 过长（{}字符 > 上限 {}）'.format(
                    len(skill.instructions), MAX_INSTRUCTIONS_CHARS,
                ),
            )
        if not overwrite and self.get(skill.name) is not None:
            raise ValueError('同名技能已存在: {}'.format(skill.name))

        skill.updated_at = time.time()
        if not skill.created_at:
            skill.created_at = skill.updated_at
        path = self._file_path_for(skill)
        skill.file_path = path
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(skill.to_dict(), fh, ensure_ascii=False, indent=2)
        if os.path.exists(path):
            os.replace(tmp, path)
        else:
            os.rename(tmp, path)
        return skill

    def delete(self, name):
        # type: (str) -> bool
        s = self.get(name)
        if s is None:
            return False
        if s.file_path and os.path.exists(s.file_path):
            try:
                os.remove(s.file_path)
            except OSError as exc:
                logger.warning('删除 skill 失败: %s', exc)
                return False
        return True

    def increment_use_count(self, name):
        # type: (str) -> None
        s = self.get(name)
        if s is None:
            return
        s.use_count += 1
        s.updated_at = time.time()
        try:
            self.save(s)
        except (OSError, ValueError):
            pass

    # ------------------------------------------------------------------ #
    # Prompt 注入
    # ------------------------------------------------------------------ #
    def build_system_prompt_addon(self, user_input=None):
        # type: (Optional[str]) -> str
        """根据当前已学技能生成可拼到 system prompt 的附加文本。

        策略：
        - 始终列出已学技能的 ``- name（触发词）：description`` 简介
        - 如果 user_input 命中某个 skill 的 trigger_keywords，把该 skill 的
          完整 instructions 也注入（让 LLM 按部就班执行）
        """
        skills = self._scan()
        if not skills:
            return ''
        lines = ['', '## 你已经学会的技能（Skills）']
        for s in skills:
            lines.append(s.brief())
        lines.append('')
        lines.append(
            '当用户的请求与某个技能的描述或触发词相符时，'
            '请按照该技能的 instructions 执行；'
            '如果没有完全匹配的技能，按用户的具体要求处理即可。'
        )

        # 命中触发词时把完整 instructions 注入
        matched = []
        if user_input:
            ui_lower = user_input.lower()
            for s in skills:
                for kw in s.trigger_keywords:
                    if not kw:
                        continue
                    if kw.lower() in ui_lower:
                        matched.append(s)
                        break

        if matched:
            lines.append('')
            lines.append('## 当前用户输入命中的技能详细流程')
            for s in matched:
                lines.append('')
                lines.append('### 技能：{}'.format(s.name))
                if s.description:
                    lines.append('描述：{}'.format(s.description))
                lines.append('')
                lines.append(s.instructions.strip())

        return '\n'.join(lines)


__all__ = [
    'Skill',
    'SkillManager',
    'get_skills_dir',
    'MAX_INSTRUCTIONS_CHARS',
]
