#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量执行工具。

提供 ``batch_execute`` 工具，让 LLM 可以一次性提交多个工具调用，
由 ``ToolDispatcher.dispatch_batch`` 统一调度执行。
"""

from __future__ import absolute_import
from __future__ import print_function

from typing import Any
from typing import Dict
from typing import List

from .dispatcher import ToolDispatcher
from .registry import tool


# 全局 dispatcher 单例，延迟创建。
# UI/Worker 通常会在启动时注入带 confirm_callback 的实例，
# 这里作为工具函数侧的兜底获取点。
_global_dispatcher = None  # type: ToolDispatcher | None


def _get_dispatcher():
    # type: () -> ToolDispatcher
    """获取当前可用的 ToolDispatcher 实例。

    优先返回已注入的全局单例；没有则创建一个默认实例。
    """
    global _global_dispatcher  # pylint: disable=global-statement
    if _global_dispatcher is None:
        _global_dispatcher = ToolDispatcher()
    return _global_dispatcher


def set_global_dispatcher(dispatcher):
    # type: (ToolDispatcher) -> None
    """注入全局 dispatcher 实例。

    由 UI/Worker 在启动时调用，确保 batch_execute 能复用
    带 confirm_callback 和 result_max_bytes 配置的 dispatcher。
    """
    global _global_dispatcher  # pylint: disable=global-statement
    _global_dispatcher = dispatcher


@tool(
    description='批量执行多个工具调用。适用于对多个对象执行相同或不同操作的场景。',
    category='system',
    dangerous=False,
    parameters={
        "type": "object",
        "properties": {
            "calls": {
                "type": "array",
                "description": (
                    "工具调用列表，每项为 {\"tool\": \"工具名\", "
                    "\"arguments\": {...}}"
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "tool": {
                            "type": "string",
                            "description": "要调用的工具名",
                        },
                        "arguments": {
                            "type": "object",
                            "description": "工具参数对象",
                        },
                    },
                    "required": ["tool", "arguments"],
                },
            },
        },
        "required": ["calls"],
    },
    examples=[{"summary": "典型调用", "args": {"calls": []}}],
notes=['参数必须严格符合 JSON Schema 声明的类型。', '调用失败时应先检查对象/文件是否存在。'],
returns_desc="dict {\"ok\": True, ...}"
)
def batch_execute(calls):
    # type: (List[Dict[str, Any]]) -> Dict[str, Any]
    """批量执行工具。

    :param calls: 工具调用列表，每项为 {"tool": "工具名", "arguments": {...}}
    :returns: 批量执行结果摘要
    """
    dispatcher = _get_dispatcher()
    return dispatcher.dispatch_batch(calls)