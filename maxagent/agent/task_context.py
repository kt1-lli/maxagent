#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""任务情景记忆（Mission Card）：跨轮次持久化用户核心意图。

设计目标：
- 在多轮对话中保持"用户到底想干什么"的记忆，防止 LLM 在工具调用
  间隙丢失上下文或偏离主线。
- 轻量级：只保存关键决策（对象名、位置目标、约束条件），不保存
  完整对话历史。
- 自动注入：每次 LLM 请求时，若存在未完成任务，自动 prepend 到
  user message 前作为情景锚点。

使用场景：
  用户："在 Box01 上面放个茶壶"
  → 任务卡记录：目标=创建+摆放, 参考对象=Box01, 空间关系=顶部,
    待创建对象=茶壶
  → LLM 调用 create_teapot 后，下一轮的 system/user prompt 中
    自动注入任务卡，提醒"现在进入步骤 ③：位置调整"
"""

from __future__ import absolute_import
from __future__ import print_function

import json
import os
from typing import Any
from typing import Dict
from typing import Optional


# ------------------------------------------------------------------ #
# 任务卡结构
# ------------------------------------------------------------------ #
class MissionCard:
    """单条任务卡的内存表示。"""

    # 任务卡字段（全部可选，按需填充）
    # 示例 fill_template = {
    #     "mission": "create_and_place",  # 任务类型：create / modify / query / layout / composite
    #     "target_object": "Teapot01",    # 主要操作对象（已创建或待创建）
    #     "reference_object": "Box01",    # 空间参考对象
    #     "spatial_relation": "on_top",   # 空间关系：on_top / inside / beside / aligned / etc.
    #     "constraints": ["no_rotation", "snap_to_center"],  # 约束条件
    #     "status": "pending_placement",  # 当前状态：pending / in_progress / completed
    #     "step": 2,                       # 当前执行到第几步（从 1 开始）
    #     "total_steps": 4,                # 预估总步数
    # }

    def __init__(self, data):
        # type: (Dict[str, Any]) -> None
        self._data = dict(data) if data else {}  # type: Dict[str, Any]

    def to_dict(self):
        # type: () -> Dict[str, Any]
        return dict(self._data)

    def get(self, key, default=None):
        # type: (str, Any) -> Any
        return self._data.get(key, default)

    def set(self, key, value):
        # type: (str, Any) -> None
        self._data[key] = value

    def is_empty(self):
        # type: () -> bool
        return not self._data

    def __bool__(self):
        return bool(self._data)

    # Python 2 兼容
    __nonzero__ = __bool__

    def to_prompt_text(self):
        # type: () -> str
        """将任务卡转为 LLM 友好的提示文本。

        格式简明，每行一个关键信息，避免冗余。仅在任务卡非空时生成。

        :returns: 提示文本；若任务卡为空则返回空串。
        """
        if not self._data:
            return ''
        lines = ['【📋 当前任务卡 - 请不要偏离以下主线】']
        # 按优先级排序输出
        priority_keys = [
            'mission', 'target_object', 'reference_object',
            'spatial_relation', 'constraints', 'status', 'step', 'total_steps',
        ]
        key_names = {
            'mission': '任务类型',
            'target_object': '操作对象',
            'reference_object': '参考对象',
            'spatial_relation': '空间关系',
            'constraints': '约束条件',
            'status': '当前状态',
            'step': '当前步骤',
            'total_steps': '总步骤',
        }
        for key in priority_keys:
            if key in self._data:
                val = self._data[key]
                name = key_names.get(key, key)
                if isinstance(val, list):
                    val_str = ', '.join(str(v) for v in val)
                else:
                    val_str = str(val)
                lines.append('  - {}: {}'.format(name, val_str))
        # 追加通用提醒
        lines.append('  → 请继续推进任务，不要切换主题或添加无关操作。')
        return '\n'.join(lines)


# ------------------------------------------------------------------ #
# 持久化管理器
# ------------------------------------------------------------------ #
class TaskContextManager:
    """任务情景记忆管理器。

    - 单例模式（每个 worker/context 一个实例）
    - 内存为主，磁盘为备份（会话结束时清理）
    - 自动注入：将当前任务状态转换为 prompt 文本，prepend 到 user message
    """

    def __init__(self, persist_path=None):
        # type: (Optional[str]) -> None
        """
        :param persist_path: 持久化文件路径；None 表示纯内存（不持久化到磁盘）。
        """
        self._card = MissionCard({})  # type: MissionCard
        self._persist_path = persist_path  # type: Optional[str]
        if persist_path and os.path.exists(persist_path):
            try:
                with open(persist_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self._card = MissionCard(data)
            except (IOError, ValueError):
                self._card = MissionCard({})

    # ------------------------------------------------------------------ #
    # 公共 API
    # ------------------------------------------------------------------ #
    def create(self, mission, target_object='', reference_object='',
               spatial_relation='', constraints=None, total_steps=1):
        # type: (str, str, str, str, Optional[list], int) -> None
        """创建新任务卡，覆盖旧任务。

        :param mission: 任务类型标识，如 "create_and_place" / "modify_material"
        :param target_object: 主要操作对象名（若已知）
        :param reference_object: 空间参考对象名（若涉及）
        :param spatial_relation: 空间关系描述，如 "on_top" / "inside"
        :param constraints: 约束条件列表，如 ["no_rotation"]
        :param total_steps: 预估总执行步数
        """
        self._card = MissionCard({
            'mission': mission,
            'target_object': target_object,
            'reference_object': reference_object,
            'spatial_relation': spatial_relation,
            'constraints': constraints or [],
            'status': 'pending',
            'step': 1,
            'total_steps': total_steps,
        })
        self._sync()

    def update(self, **kwargs):
        # type: (Any) -> None
        """更新任务卡字段。

        :param kwargs: 任意键值对，如 step=2, status='in_progress'
        """
        for key, val in kwargs.items():
            self._card.set(key, val)
        self._sync()

    def advance_step(self):
        """推进到下一步。"""
        current = self._card.get('step', 1)
        total = self._card.get('total_steps', 1)
        next_step = min(current + 1, total)
        self._card.set('step', next_step)
        if next_step >= total:
            self._card.set('status', 'completed')
        else:
            self._card.set('status', 'in_progress')
        self._sync()

    def complete(self):
        """标记任务完成并清空。"""
        self._card.set('status', 'completed')
        self._sync()
        self.clear()

    def clear(self):
        """清空当前任务卡。"""
        self._card = MissionCard({})
        self._sync()

    def get_card(self):
        # type: () -> MissionCard
        return self._card

    def get_prompt(self):
        # type: () -> str
        """获取任务卡的 prompt 文本，用于注入 LLM 请求。

        :returns: 提示文本；若任务卡为空则返回空串。
        """
        return self._card.to_prompt_text()

    def is_active(self):
        # type: () -> bool
        """是否有正在进行的任务。"""
        return bool(self._card) and self._card.get('status') != 'completed'

    # ------------------------------------------------------------------ #
    # 内部方法
    # ------------------------------------------------------------------ #
    def _sync(self):
        # type: () -> None
        """内存到磁盘的同步（若配置了 persist_path）。"""
        if not self._persist_path:
            return
        try:
            with open(self._persist_path, 'w', encoding='utf-8') as f:
                json.dump(self._card.to_dict(), f, ensure_ascii=False, indent=2)
        except IOError:
            pass


# 全局便捷函数（供 worker 直接使用）

def get_task_prompt(manager):
    # type: (Optional[TaskContextManager]) -> str
    """从 TaskContextManager 获取任务卡 prompt 文本。

    :param manager: TaskContextManager 实例，可为 None
    :returns: 提示文本或空串
    """
    if manager is None:
        return ''
    return manager.get_prompt()


__all__ = [
    'MissionCard',
    'TaskContextManager',
    'get_task_prompt',
]
