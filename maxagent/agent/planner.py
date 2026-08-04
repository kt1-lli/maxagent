#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""任务规划器：把用户请求拆成可跟踪、可干预的子目标序列。

P1-1 任务规划：在 MissionCard 之上增加层级规划能力。
规划器只做"计划"，不替代 worker 执行；worker 按计划逐条推进。
"""

from __future__ import absolute_import
from __future__ import print_function

import json
import re
import time
import uuid
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple


# 合法状态
_PENDING = 'pending'
_IN_PROGRESS = 'in_progress'
_DONE = 'done'
_FAILED = 'failed'
_SKIPPED = 'skipped'
_NEED_HELP = 'need_help'


class PlanStep(object):
    """单个规划步骤。"""

    def __init__(self, description, step_id=None, depends_on=None,
                 needs_vision=False):
        # type: (str, Optional[str], Optional[List[str]], bool) -> None
        self.id = step_id or 'step_{}'.format(uuid.uuid4().hex[:6])
        self.description = description
        self.status = _PENDING  # type: str
        self.depends_on = list(depends_on or [])  # type: List[str]
        self.result_summary = ''  # type: str
        self.started_at = 0.0
        self.finished_at = 0.0
        self.needs_vision = bool(needs_vision)  # type: bool

    def to_dict(self):
        # type: () -> Dict[str, Any]
        return {
            'id': self.id,
            'description': self.description,
            'status': self.status,
            'depends_on': list(self.depends_on),
            'result_summary': self.result_summary,
            'started_at': self.started_at,
            'finished_at': self.finished_at,
            'needs_vision': self.needs_vision,
        }

    @classmethod
    def from_dict(cls, data):
        # type: (Dict[str, Any]) -> PlanStep
        s = cls(
            description=data.get('description', ''),
            step_id=data.get('id'),
            depends_on=data.get('depends_on') or [],
            needs_vision=bool(data.get('needs_vision', False)),
        )
        s.status = data.get('status', _PENDING)
        s.result_summary = data.get('result_summary', '')
        s.started_at = float(data.get('started_at', 0) or 0)
        s.finished_at = float(data.get('finished_at', 0) or 0)
        return s


class TaskPlanner(object):
    """轻量级任务规划器。

    策略：
    - 简单请求（<8 字或明显单步）：生成单步骤 plan
    - 复杂请求：用规则/模板拆分为多个步骤
    - 依赖关系默认线性：step N 依赖 step N-1
    """

    def __init__(self):
        # type: () -> None
        self.steps = []  # type: List[PlanStep]
        self.title = ''  # type: str
        self.created_at = 0.0

    # ------------------------------------------------------------------ #
    # 公共 API
    # ------------------------------------------------------------------ #
    def is_active(self):
        # type: () -> bool
        return bool(self.steps)

    def reset(self):
        """清空当前计划。"""
        self.steps = []
        self.title = ''
        self.created_at = 0.0

    def create_plan(self, user_input):
        # type: (str) -> None
        """根据用户输入生成任务计划。"""
        text = (user_input or '').strip()
        self.title = text[:40] + ('...' if len(text) > 40 else '')
        self.created_at = time.time()
        self.steps = self._build_steps(text)

    def upgrade_plan_with_llm(self, user_input, llm_client, max_tokens=512):
        # type: (str, Any, int) -> bool
        """用 LLM 生成更精准的结构化任务计划，替换当前规则版计划。

        :param user_input: 用户请求原文
        :param llm_client: 已构建好的 LLMClient 实例
        :param max_tokens: 生成 plan 允许的 max_tokens 上限
        :returns: True 表示 LLM 规划成功替换，False 表示失败并保留原计划

        LLM 严格输出 JSON schema：
        {
          "steps": [
            {"description": "...", "needs_vision": false},
            ...
          ]
        }
        任何解析异常都会静默降级到规则版计划，不抛出。
        """
        text = (user_input or '').strip()
        if not text or llm_client is None:
            return False

        system_prompt = (
            '你是一个 3ds Max 内嵌 AI Agent 的任务规划助手。'
            '把用户的自然语言请求拆解成 2~6 个具体、可独立执行的步骤，'
            '每一步都是一个明确的动作或验证。\n'
            '规则：\n'
            '1. 严格输出 JSON，不含任何解释文字或 markdown 围栏。\n'
            '2. schema：{"steps": [{"description": "...", "needs_vision": bool}]}\n'
            '3. description 使用中文，动词开头，说明要做什么。\n'
            '4. 涉及"看看/复核/效果/截图/渲染"等视觉验证时 needs_vision=true。\n'
            '5. 简单查询类请求可只返回 1~2 步；复杂多阶段请求最多 6 步。\n'
            '6. 步骤应按执行顺序线性排列，后一步默认依赖前一步。\n'
            '示例输入："在茶壶上放一个红色球体，看看效果"\n'
            '示例输出：'
            '{"steps": ['
            '{"description": "定位茶壶对象并读取其位置/边界", "needs_vision": false},'
            '{"description": "创建红色球体材质", "needs_vision": false},'
            '{"description": "创建球体并摆放到茶壶顶部", "needs_vision": false},'
            '{"description": "截取视口复核效果", "needs_vision": true}'
            ']}'
        )
        messages = [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': text},
        ]
        try:
            # 尝试用 llm_client 的 chat 接口（非流式，快速返回）
            resp = llm_client.chat(
                messages,
                stream=False,
                max_tokens=max_tokens,
                temperature=0.2,
            )
        except Exception:  # pylint: disable=broad-except
            return False

        # 抽取生成的文本
        raw = ''
        try:
            if isinstance(resp, dict):
                choices = resp.get('choices') or []
                if choices and isinstance(choices, list):
                    msg = choices[0].get('message') or {}
                    raw = msg.get('content') or ''
            elif isinstance(resp, str):
                raw = resp
        except Exception:  # pylint: disable=broad-except
            return False
        raw = (raw or '').strip()
        if not raw:
            return False

        # 去掉可能的 markdown 围栏
        if raw.startswith('```'):
            # 剥离 ```json / ```
            raw = re.sub(r'^```[a-zA-Z]*\n', '', raw)
            if raw.endswith('```'):
                raw = raw[:-3]
            raw = raw.strip()

        # 解析 JSON
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return False
        if not isinstance(data, dict):
            return False
        raw_steps = data.get('steps') or []
        if not isinstance(raw_steps, list) or not raw_steps:
            return False

        # 构造新 steps 列表；每步都做 schema 兼容处理
        new_steps = []  # type: List[PlanStep]
        for item in raw_steps[:6]:  # 上限 6 步防止 LLM 幻觉爆炸
            if isinstance(item, dict):
                desc = str(item.get('description') or '').strip()
                needs_vision = bool(item.get('needs_vision', False))
            elif isinstance(item, str):
                desc = item.strip()
                needs_vision = False
            else:
                continue
            if not desc:
                continue
            new_steps.append(
                PlanStep(desc, needs_vision=needs_vision)
            )

        # LLM 什么都没给出有效步骤时保留规则版
        if not new_steps:
            return False

        self.steps = new_steps
        return True

    def current_step(self):
        # type: () -> Optional[PlanStep]
        """找到当前应该执行的步骤。"""
        for s in self.steps:
            if s.status == _IN_PROGRESS:
                return s
            if s.status == _PENDING:
                if all(
                    self._get_status(dep) in (_DONE, _SKIPPED)
                    for dep in s.depends_on
                ):
                    return s
        return None

    def advance(self):
        # type: () -> Optional[PlanStep]
        """推进到下一步。"""
        s = self.current_step()
        if s is None:
            return None
        s.status = _DONE
        s.finished_at = time.time()
        return self.current_step()

    def mark_current_failed(self, summary=''):
        # type: (str) -> None
        s = self.current_step()
        if s is not None:
            s.status = _FAILED
            s.result_summary = summary
            s.finished_at = time.time()

    def mark_current_need_help(self, summary=''):
        # type: (str) -> None
        s = self.current_step()
        if s is not None:
            s.status = _NEED_HELP
            s.result_summary = summary
            s.finished_at = time.time()

    def skip_current(self):
        # type: () -> Optional[PlanStep]
        """跳过当前步骤，返回下一个待执行步骤。"""
        s = self.current_step()
        if s is not None:
            s.status = _SKIPPED
            s.finished_at = time.time()
        return self.current_step()

    def rollback(self, step_id):
        # type: (str) -> bool
        """回滚到指定步骤，将其后续所有步骤重置为 pending。"""
        idx = self._index_of(step_id)
        if idx < 0:
            return False
        for s in self.steps[idx:]:
            s.status = _PENDING
            s.result_summary = ''
            s.started_at = 0.0
            s.finished_at = 0.0
        return True

    def to_prompt_text(self):
        # type: () -> str
        """把当前计划转成 prompt 文本，注入到 LLM 请求中。"""
        if not self.steps:
            return ''
        lines = ['【📋 当前任务计划】']
        for idx, s in enumerate(self.steps, start=1):
            icon = self._status_icon(s.status)
            lines.append('  {} {}. {}'.format(icon, idx, s.description))
        cur = self.current_step()
        if cur is not None:
            lines.append('')
            lines.append('→ 当前步骤：{}'.format(cur.description))
            lines.append('→ 请只执行当前步骤，不要跳到后续步骤。')
        else:
            lines.append('\n→ 计划已全部完成或暂停。')
        return '\n'.join(lines)

    def to_dict(self):
        # type: () -> Dict[str, Any]
        return {
            'title': self.title,
            'created_at': self.created_at,
            'steps': [s.to_dict() for s in self.steps],
        }

    def from_dict(self, data):
        # type: (Dict[str, Any]) -> None
        self.title = data.get('title', '')
        self.created_at = float(data.get('created_at', 0) or 0)
        self.steps = [
            PlanStep.from_dict(s) for s in data.get('steps', [])
        ]

    # ------------------------------------------------------------------ #
    # 内部方法
    # ------------------------------------------------------------------ #
    def _build_steps(self, text):
        # type: (str) -> List[PlanStep]
        """基于规则拆分步骤。"""
        if not text:
            return []

        # 简单查询/单步指令：不拆
        if len(text) <= 8:
            return [PlanStep(text)]

        query_keywords = ['查', '列出', '多少', '看看', '显示', '统计']
        if any(kw in text for kw in query_keywords):
            return [PlanStep('查询：{}'.format(text), needs_vision='看看' in text)]

        # 创建 + 空间定位
        if any(kw in text for kw in ['创建', '新建', '生成', '添加']) \
                and any(kw in text for kw in ['放在', '放到', '上面', '上面', '旁边']):
            steps = []
            steps.append(PlanStep('确认参考对象存在并记录其位置/边界'))
            steps.append(PlanStep('创建目标对象'))
            steps.append(PlanStep('将目标对象摆放到指定位置'))
            steps.append(PlanStep('复核结果是否符合预期', needs_vision=True))
            return steps

        # 创建无空间
        if any(kw in text for kw in ['创建', '新建', '生成', '添加', '做', '画']):
            return [
                PlanStep('创建目标对象'),
                PlanStep('复核结果', needs_vision=True),
            ]

        # 修改
        modify_keywords = ['修改', '调整', '设置', '改变', '改', '替换']
        if any(kw in text for kw in modify_keywords):
            return [
                PlanStep('定位需要修改的对象'),
                PlanStep('执行修改'),
                PlanStep('复核修改结果', needs_vision=True),
            ]

        # 布局
        layout_keywords = ['排列', '阵列', '分布', '环绕', '等距']
        if any(kw in text for kw in layout_keywords):
            return [
                PlanStep('确定布局中心和基准对象'),
                PlanStep('按规则计算目标位置'),
                PlanStep('摆放对象并复核', needs_vision=True),
            ]

        # 视觉/效果/渲染相关触发词：直接进视觉复核流程
        vision_keywords = ['截图', '截屏', '看看', '效果', '渲染', '材质',
                           '灯光', '相机', '视图', '截图看看', '截个图']
        if any(kw in text for kw in vision_keywords):
            return [
                PlanStep('执行用户请求：{}'.format(text)),
                PlanStep('截取视口复核结果', needs_vision=True),
            ]

        # 默认：两步
        return [
            PlanStep('执行用户请求：{}'.format(text)),
            PlanStep('复核结果'),
        ]

    def _get_status(self, step_id):
        # type: (str) -> str
        for s in self.steps:
            if s.id == step_id:
                return s.status
        return _DONE

    def _index_of(self, step_id):
        # type: (str) -> int
        for i, s in enumerate(self.steps):
            if s.id == step_id:
                return i
        return -1

    def _status_icon(self, status):
        # type: (str) -> str
        return {
            _PENDING: '⬜',
            _IN_PROGRESS: '🔄',
            _DONE: '✅',
            _FAILED: '❌',
            _SKIPPED: '⏭️',
            _NEED_HELP: '⚠️',
        }.get(status, '⬜')


__all__ = [
    'PlanStep',
    'TaskPlanner',
]
