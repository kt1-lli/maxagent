#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dock 面板与 DCC 相关的小工具。

从 dock_widget.py 抽出的极小工具集, 集中处理:
- 窗口标题按 DCC 生成
- 未来若新增 Blender / Houdini 分支, 在这里加即可, 无需再改 dock_widget

之所以没做完整的 DockHost 协议抽象, 是因为当前只有 3 处 DCC 分支,
过度抽象反而更难读; 保留 dispatch_dock_creation 作为集中开关。
"""

from __future__ import annotations

from typing import Any, Callable

from ..dcc.runtime import current_dcc


# 每个 DCC 对应的中文子标题
_DCC_TITLE_SUFFIX = {
    'maya': 'Maya AI 助手',
    '3dsmax': '3ds Max AI 助手',
}


def dock_window_title():
    # type: () -> str
    """按当前 DCC 生成 MaxAgent 面板窗口标题。"""
    dcc = current_dcc()
    suffix = _DCC_TITLE_SUFFIX.get(dcc, 'AI 助手')
    return 'MaxAgent · ' + suffix


def dispatch_dock_creation(config, create_max, create_maya, create_standalone):
    # type: (Any, Callable, Callable, Callable) -> Any
    """按当前 DCC 选择合适的 dock 创建函数。

    :param config: ConfigManager 实例, 传给具体 creator
    :param create_max: 3ds Max 分支的 creator, 签名 (config,) -> dock_or_holder
    :param create_maya: Maya 分支的 creator
    :param create_standalone: 未识别 DCC 的兜底 (纯 Qt 独立窗)
    :returns: 具体 creator 返回值
    """
    dcc = current_dcc()
    if dcc == '3dsmax':
        return create_max(config)
    if dcc == 'maya':
        return create_maya(config)
    return create_standalone(config)


__all__ = [
    'dock_window_title',
    'dispatch_dock_creation',
]
