#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""操作确认清单（Approval Queue）——高风险批量操作前的用户闸门。

**动机**：LLM 一次回复里可能返回 5~20 个 tool_calls，如果全是写入类
操作（create_/delete_/set_），用户没有机会介入。此模块把 tool_calls
分类，若命中风险阈值就先返回一份"待批准清单"，等 UI 侧收集用户决定
（approve_all / reject_all / edit_specific）后再交给 dispatcher 执行。

**设计**：纯数据 + 分类判定；不包含 Qt / UI 依赖。UI 层负责渲染
``ApprovalPlan`` 并回填用户决定。worker 层负责在 tool_calls 分派前
先过一遍 ``build_plan``，然后根据 ``needs_approval`` 决定是否等待。

**执行策略与整轮 Undo（#11）**：worker 在真正跑批准清单时会主动
``theHold.Begin()``，全部执行完再 ``theHold.Accept('MaxAgent Batch')``；
中途异常则 ``theHold.Cancel()`` 回滚到批准前状态。这样一次对话里所有
被批准的写入操作在 Max 的 Undo Stack 里合并成一次撤销，用户按一次
Ctrl+Z 就能全撤。
"""

from __future__ import absolute_import
from __future__ import print_function

from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional


# ---------------------------------------------------------------------- #
# 分类：读 vs 写 vs 高风险
# ---------------------------------------------------------------------- #

# 只读工具前缀：完全不需要用户批准
_READONLY_PREFIXES = (
    'list_', 'get_', 'query_', 'count_', 'find_',
    'is_', 'has_', 'check_', 'describe_', 'build_scene_snapshot',
    'diff_snapshots', 'read_', 'search_', 'inspect_',
)

# 高风险工具前缀：即便阈值未满也应该单独确认
_HIGH_RISK_TOOL_NAMES = frozenset({
    'delete_objects',
    'save_max_file',
    'load_max_file',
    'merge_max_file',
    'export_file',
    'import_file',
    'run_python',
    'run_maxscript',
    'collapse_stack',
    'clear_scene',
    'reset_max',
})


def classify_tool(tool_name):
    # type: (str) -> str
    """把工具分类为 'read' / 'write' / 'high_risk'。"""
    if not tool_name:
        return 'write'
    if tool_name in _HIGH_RISK_TOOL_NAMES:
        return 'high_risk'
    for pref in _READONLY_PREFIXES:
        if tool_name.startswith(pref):
            return 'read'
    return 'write'


# ---------------------------------------------------------------------- #
# 数据模型
# ---------------------------------------------------------------------- #

@dataclass
class ApprovalItem(object):
    """清单里的一条待批准工具调用。"""

    tool_call_id: str = ''
    tool_name: str = ''
    arguments: Dict[str, Any] = field(default_factory=dict)
    kind: str = 'write'       # read / write / high_risk
    # 用户对该项的决定：pending / approved / rejected / edited
    decision: str = 'pending'
    # 编辑后的参数（仅 edited 生效）
    edited_arguments: Optional[Dict[str, Any]] = None
    # 语义化中文描述（供 UI 一行渲染）
    description: str = ''


@dataclass
class ApprovalPlan(object):
    """整批 tool_calls 的批准计划。"""

    items: List[ApprovalItem] = field(default_factory=list)
    # 是否需要用户批准（false 时 worker 可直接放行）
    needs_approval: bool = False
    # 触发批准的原因（UI 提示用）
    reason: str = ''

    def approved_calls(self):
        # type: () -> List[Dict[str, Any]]
        """按用户决定过滤后，构造回给 dispatcher 的 tool_calls 序列。

        - approved：原样保留
        - edited：使用 edited_arguments 覆盖 arguments
        - rejected / pending：跳过
        """
        out = []
        for it in self.items:
            if it.decision == 'approved':
                out.append({
                    'id': it.tool_call_id,
                    'type': 'function',
                    'function': {
                        'name': it.tool_name,
                        'arguments': dict(it.arguments or {}),
                    },
                })
            elif it.decision == 'edited':
                out.append({
                    'id': it.tool_call_id,
                    'type': 'function',
                    'function': {
                        'name': it.tool_name,
                        'arguments': dict(
                            it.edited_arguments or it.arguments or {},
                        ),
                    },
                })
        return out

    def summary_text(self):
        # type: () -> str
        """供 UI 顶部展示的一句中文摘要。"""
        n = len(self.items)
        writes = sum(1 for x in self.items if x.kind == 'write')
        risks = sum(1 for x in self.items if x.kind == 'high_risk')
        reads = n - writes - risks
        parts = []
        if reads:
            parts.append('读取 {}'.format(reads))
        if writes:
            parts.append('写入 {}'.format(writes))
        if risks:
            parts.append('高风险 {}'.format(risks))
        return '共 {} 个操作（{}）'.format(n, '，'.join(parts) or '未知')


# ---------------------------------------------------------------------- #
# 构建计划
# ---------------------------------------------------------------------- #

def build_plan(tool_calls, approval_threshold=3, describe_fn=None,
               force=False):
    # type: (List[Dict[str, Any]], int, Optional[Callable[[str, Dict[str, Any]], str]], bool) -> ApprovalPlan
    """把 tool_calls 分类并判定是否需要批准。

    :param tool_calls: OpenAI 风格 tool_calls 列表
    :param approval_threshold: 写入类操作数 >= 该值时触发批准
    :param describe_fn: 可选的语义化描述器（None 时回落到 tool_name）
    :param force: 强制触发批准（用于用户显式要求"逐个确认"）
    """
    plan = ApprovalPlan()
    write_cnt = 0
    risk_cnt = 0

    for tc in tool_calls or []:
        fn = (tc or {}).get('function') or {}
        name = str(fn.get('name') or '')
        args = fn.get('arguments') or {}
        if isinstance(args, str):
            # OpenAI 有时把 arguments 作为 JSON 字符串返回
            try:
                import json
                args = json.loads(args)
                if not isinstance(args, dict):
                    args = {}
            except Exception:  # pylint: disable=broad-except
                args = {}
        kind = classify_tool(name)
        if kind == 'write':
            write_cnt += 1
        elif kind == 'high_risk':
            risk_cnt += 1

        desc = ''
        if describe_fn is not None:
            try:
                desc = describe_fn(name, args) or ''
            except Exception:  # pylint: disable=broad-except
                desc = ''
        if not desc:
            desc = name or '(未知)'

        plan.items.append(ApprovalItem(
            tool_call_id=str(tc.get('id') or ''),
            tool_name=name,
            arguments=dict(args) if isinstance(args, dict) else {},
            kind=kind,
            description=desc,
            decision='pending',
        ))

    if force:
        plan.needs_approval = True
        plan.reason = '用户要求逐个确认'
    elif risk_cnt >= 1:
        plan.needs_approval = True
        plan.reason = '包含 {} 个高风险操作'.format(risk_cnt)
    elif write_cnt >= max(1, approval_threshold):
        plan.needs_approval = True
        plan.reason = '写入操作数达阈值（{} >= {}）'.format(
            write_cnt, approval_threshold,
        )
    else:
        plan.needs_approval = False
        plan.reason = ''

    return plan


# ---------------------------------------------------------------------- #
# 整轮 Undo（#11）
# ---------------------------------------------------------------------- #

class UndoBatch(object):
    """把一段代码块包装成 Max 单条 Undo 记录的上下文管理器。

    只在 Max 环境下真正生效；单测/非 Max 环境下退化为无 op。用法：

        with UndoBatch('新建三点布光'):
            dispatcher.execute(...)
    """

    def __init__(self, label='MaxAgent Batch'):
        self._label = label or 'MaxAgent Batch'
        self._active = False
        self._rt = None

    def __enter__(self):
        try:
            import pymxs  # type: ignore
            self._rt = pymxs.runtime
            self._rt.theHold.Begin()
            self._active = True
        except Exception:  # pylint: disable=broad-except
            self._active = False
        return self

    def __exit__(self, exc_type, exc, tb):
        if not self._active or self._rt is None:
            return False
        try:
            if exc_type is None:
                self._rt.theHold.Accept(self._label)
            else:
                self._rt.theHold.Cancel()
        except Exception:  # pylint: disable=broad-except
            pass
        return False  # 不吞异常
