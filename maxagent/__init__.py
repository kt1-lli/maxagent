#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""maxagent 包入口。

3ds Max 内嵌的 AI 助手，支持本地模型（Ollama / LM Studio）与云端 API
（OpenAI / DeepSeek / 兼容协议），通过 Function Calling 操作 Max 场景。

Quick Start (在 Max 中):
    >>> import maxagent
    >>> maxagent.show()

或者直接调:
    >>> from maxagent.startup import show_panel
    >>> show_panel()
"""

from __future__ import absolute_import

__version__ = '0.1.0'
__author__ = 'MaxAgent Team'
__license__ = 'MIT'

# 公开 API
__all__ = [
    '__version__',
    'show',
    'hide',
    'toggle',
]


def show():
    """显示 MaxAgent 面板。"""
    from .startup import show_panel
    return show_panel()


def hide():
    """隐藏 MaxAgent 面板（保留对话历史）。"""
    from .startup import hide_panel
    hide_panel()


def toggle():
    """切换显示/隐藏。"""
    from .startup import toggle_panel
    toggle_panel()
