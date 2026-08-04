#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Todo 工具：让 LLM 自己维护一份任务清单，并在 UI 上实时呈现。

设计目标
--------
成熟 Agent（Cursor / Claude Code / Cline 等）都提供 "TodoWrite / TodoUpdate"
一类工具，让 LLM 在多步任务中主动列清单、逐项勾选：

1. 用户看得见 Agent 打算做什么（透明度）
2. LLM 有一个显式的"目标记忆"，防止在长循环中忘记未完成的项
3. 用户可以随时中途插话调整方向

数据模型
--------
一份 Todo 清单归属于当前"用户请求批次"（一次 LLM 主循环）。清单存在
进程级 ``_STORE`` 单例中，key 是 session_id，value 是 ``TodoList``：

    TodoList
      ├── items: List[TodoItem]
      └── revision: int  (每次修改自增)

    TodoItem
      ├── id: str  (LLM 生成或自动分配)
      ├── content: str  (任务描述)
      ├── status: 'pending' | 'in_progress' | 'done' | 'skipped' | 'failed'
      └── note: str  (可选备注)

线程模型
--------
Todo 工具 **不需要** 主线程 marshal（不碰 pymxs），全部在 dispatcher
调用线程执行，注册时使用 ``run_on_main_thread=False``。

事件通知
--------
每次写入/更新后触发一次 ``_on_change_callback``（由 worker 注入），
worker 再通过 Qt Signal 通知 UI 刷新气泡。
"""

from __future__ import absolute_import
from __future__ import print_function

import threading
import time
import uuid
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional

from .registry import tool


# 合法状态集合。'skipped' 让 LLM 在用户改主意时能优雅地跳过某项，
# 不需要伪造 'done'。
_VALID_STATUS = ('pending', 'in_progress', 'done', 'skipped', 'failed')

# 单份清单最多项数，防止 LLM 一次塞 100 条把上下文占爆
_MAX_ITEMS = 30

# 单条描述最大字符数
_MAX_CONTENT_CHARS = 200


class TodoItem(object):
    """单个任务项。"""

    __slots__ = ('id', 'content', 'status', 'note', 'updated_at')

    def __init__(self, id_, content, status='pending', note=''):
        # type: (str, str, str, str) -> None
        self.id = id_
        self.content = content
        self.status = status
        self.note = note
        self.updated_at = time.time()

    def to_dict(self):
        # type: () -> Dict[str, Any]
        return {
            'id': self.id,
            'content': self.content,
            'status': self.status,
            'note': self.note,
        }


class TodoList(object):
    """一份任务清单，含 revision 号和线程锁。"""

    def __init__(self):
        # type: () -> None
        self.items = []  # type: List[TodoItem]
        self.revision = 0
        self.created_at = time.time()

    def snapshot(self):
        # type: () -> Dict[str, Any]
        """快照当前状态（可 JSON 序列化）。"""
        counts = {s: 0 for s in _VALID_STATUS}
        for item in self.items:
            counts[item.status] = counts.get(item.status, 0) + 1
        return {
            'revision': self.revision,
            'total': len(self.items),
            'counts': counts,
            'items': [item.to_dict() for item in self.items],
        }


# 全局 Store：session_id -> TodoList
_STORE = {}  # type: Dict[str, TodoList]
_STORE_LOCK = threading.Lock()

# 当前活跃 session_id（由 worker 每轮启动前 set）。使用模块级变量而非
# 参数传递，避免侵入 dispatcher 协议。多 worker 同进程时需保证同一时刻
# 只有一个活跃会话（当前架构本来就是单会话）。
_ACTIVE_SESSION_ID = ''  # type: str

# 变更回调：签名 (session_id, snapshot_dict) -> None
_ON_CHANGE_CALLBACK = None  # type: Optional[Callable[[str, Dict[str, Any]], None]]


def set_active_session(session_id):
    # type: (str) -> None
    """由 worker 在每轮主循环开始时调用，声明当前活跃会话。"""
    global _ACTIVE_SESSION_ID  # pylint: disable=global-statement
    _ACTIVE_SESSION_ID = str(session_id or '')


def set_change_callback(callback):
    # type: (Optional[Callable[[str, Dict[str, Any]], None]]) -> None
    """注册变更回调（由 worker 注入，用来触发 Qt signal）。"""
    global _ON_CHANGE_CALLBACK  # pylint: disable=global-statement
    _ON_CHANGE_CALLBACK = callback


def get_todo_snapshot(session_id=''):
    # type: (str) -> Optional[Dict[str, Any]]
    """外部只读接口：拿当前会话的 Todo 快照，用于 verify / UI 恢复。"""
    sid = session_id or _ACTIVE_SESSION_ID
    with _STORE_LOCK:
        lst = _STORE.get(sid)
        if lst is None:
            return None
        return lst.snapshot()


def reset_todo(session_id=''):
    # type: (str) -> None
    """清空指定会话的 Todo。session_id 空时清当前活跃会话。"""
    sid = session_id or _ACTIVE_SESSION_ID
    if not sid:
        return
    with _STORE_LOCK:
        _STORE.pop(sid, None)
    _emit_change(sid, {'revision': 0, 'total': 0, 'counts': {}, 'items': []})


def _emit_change(session_id, snap):
    # type: (str, Dict[str, Any]) -> None
    """安全触发变更回调。回调异常不影响主路径。"""
    cb = _ON_CHANGE_CALLBACK
    if cb is None:
        return
    try:
        cb(session_id, snap)
    except Exception:  # pylint: disable=broad-except
        pass


def _current_list(create=False):
    # type: (bool) -> Optional[TodoList]
    """获取当前活跃 session 的 TodoList；create=True 时不存在则创建。"""
    sid = _ACTIVE_SESSION_ID
    if not sid:
        # 无活跃 session 时给一个兜底 key，避免工具直接失败
        sid = '__default__'
    with _STORE_LOCK:
        lst = _STORE.get(sid)
        if lst is None and create:
            lst = TodoList()
            _STORE[sid] = lst
        return lst


def _sanitize_content(text):
    # type: (Any) -> str
    """裁剪单条内容长度，去两端空白。"""
    s = str(text or '').strip()
    if len(s) > _MAX_CONTENT_CHARS:
        s = s[:_MAX_CONTENT_CHARS - 1] + '…'
    return s


# ---------------------------------------------------------------------- #
# 工具定义
# ---------------------------------------------------------------------- #

@tool(
    name='todo_write',
    description=(
        '一次性写入完整任务清单，覆盖当前会话已有的清单。'
        '当你决定要执行一个包含 3 步或以上的复合任务时，'
        '**应主动调用本工具**先列清单再动手，让用户看见你的计划。\n'
        '典型场景：\n'
        '- 「创建一个包含桌子、椅子、台灯的场景」→ 建议先列 3~5 项\n'
        '- 「批量重命名并按 X 轴排列这批对象」→ 建议先列步骤\n'
        '简单单步任务（如"创建一个球"）不要写清单，避免噪声。\n'
        '每一项应描述用户能理解的目标，而不是内部工具名。'
    ),
    parameters={
        'type': 'object',
        'properties': {
            'items': {
                'type': 'array',
                'description': (
                    '任务清单，每项 3~15 字为宜。'
                    '总数不超过 {}。'.format(_MAX_ITEMS)
                ),
                'items': {
                    'type': 'object',
                    'properties': {
                        'content': {
                            'type': 'string',
                            'description': '任务描述（面向用户可读）',
                        },
                        'status': {
                            'type': 'string',
                            'enum': list(_VALID_STATUS),
                            'description': '初始状态，通常写 pending',
                        },
                    },
                    'required': ['content'],
                },
                'minItems': 1,
                'maxItems': _MAX_ITEMS,
            },
        },
        'required': ['items'],
    },
    category='todo',
    dangerous=False,
    wrap_undo=False,
    run_on_main_thread=False,
)
def todo_write(items):
    """写入完整清单（覆盖已有）。返回快照供 LLM 立即基于此推进。"""
    if not isinstance(items, list) or not items:
        return {'ok': False, 'error': 'items 不能为空'}

    if len(items) > _MAX_ITEMS:
        items = items[:_MAX_ITEMS]

    new_items = []  # type: List[TodoItem]
    seen_ids = set()
    for idx, raw in enumerate(items):
        if not isinstance(raw, dict):
            continue
        content = _sanitize_content(raw.get('content', ''))
        if not content:
            continue
        status = raw.get('status', 'pending')
        if status not in _VALID_STATUS:
            status = 'pending'
        # id 由服务端分配，避免 LLM 重复
        item_id = 't{}'.format(idx + 1)
        while item_id in seen_ids:
            item_id = 't{}_{}'.format(idx + 1, uuid.uuid4().hex[:4])
        seen_ids.add(item_id)
        new_items.append(TodoItem(item_id, content, status=status))

    if not new_items:
        return {'ok': False, 'error': '所有 items 内容为空'}

    lst = _current_list(create=True)
    lst.items = new_items
    lst.revision += 1
    snap = lst.snapshot()
    _emit_change(_ACTIVE_SESSION_ID, snap)
    return {
        'ok': True,
        'revision': snap['revision'],
        'total': snap['total'],
        'items': snap['items'],
        'hint': (
            '清单已建立。请按顺序开工：先将第一项标记为 in_progress '
            '（调用 todo_update_status），完成后标记为 done，'
            '再推进下一项。'
        ),
    }


@tool(
    name='todo_update_status',
    description=(
        '更新单条任务的状态。开始处理某项前应先将其置为 in_progress，'
        '完成后置为 done。若跳过或失败请如实标记。'
        '状态取值：pending / in_progress / done / skipped / failed。'
    ),
    parameters={
        'type': 'object',
        'properties': {
            'id': {
                'type': 'string',
                'description': '任务 id（来自 todo_write 返回的 items）',
            },
            'status': {
                'type': 'string',
                'enum': list(_VALID_STATUS),
                'description': '新状态',
            },
            'note': {
                'type': 'string',
                'description': '可选备注（如失败原因、跳过原因）',
            },
        },
        'required': ['id', 'status'],
    },
    category='todo',
    dangerous=False,
    wrap_undo=False,
    run_on_main_thread=False,
)
def todo_update_status(id, status, note=''):  # pylint: disable=redefined-builtin
    """更新单项状态。"""
    if status not in _VALID_STATUS:
        return {
            'ok': False,
            'error': '非法状态 "{}"；应为 {} 之一'.format(
                status, list(_VALID_STATUS),
            ),
        }
    lst = _current_list(create=False)
    if lst is None or not lst.items:
        return {
            'ok': False,
            'error': '当前会话没有任务清单，请先调用 todo_write',
        }
    target = None
    for item in lst.items:
        if item.id == id:
            target = item
            break
    if target is None:
        return {
            'ok': False,
            'error': '未找到 id={}，现有 id={}'.format(
                id, [it.id for it in lst.items],
            ),
        }
    old_status = target.status
    target.status = status
    if note:
        target.note = _sanitize_content(note)
    target.updated_at = time.time()
    lst.revision += 1
    snap = lst.snapshot()
    _emit_change(_ACTIVE_SESSION_ID, snap)
    return {
        'ok': True,
        'id': target.id,
        'content': target.content,
        'old_status': old_status,
        'new_status': target.status,
        'revision': snap['revision'],
        'progress': '{}/{}'.format(
            snap['counts'].get('done', 0), snap['total'],
        ),
    }


@tool(
    name='todo_read',
    description=(
        '读取当前会话的任务清单。用于你在长循环中忘了自己列过什么时'
        '查阅，一般不需要频繁调用。'
    ),
    parameters={
        'type': 'object',
        'properties': {},
    },
    category='todo',
    dangerous=False,
    wrap_undo=False,
    run_on_main_thread=False,
)
def todo_read():
    """读取当前清单快照。"""
    lst = _current_list(create=False)
    if lst is None or not lst.items:
        return {'ok': True, 'total': 0, 'items': []}
    return {'ok': True, **lst.snapshot()}


__all__ = [
    'set_active_session',
    'set_change_callback',
    'get_todo_snapshot',
    'reset_todo',
    'todo_write',
    'todo_update_status',
    'todo_read',
]
