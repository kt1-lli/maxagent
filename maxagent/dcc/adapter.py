#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DCC 适配器抽象基类。

每个受支持的 DCC（3ds Max、Maya 等）都需要实现 DCCAdapter，
把主线程调度、场景查询、节点操作、undo 等能力统一暴露给上层。
"""

from __future__ import absolute_import
from __future__ import print_function

import abc
from typing import Any
from typing import Callable
from typing import Optional


class DCCAdapter(object, metaclass=abc.ABCMeta):
    """DCC 适配器抽象基类。"""

    @abc.abstractproperty
    def name(self):
        # type: () -> str
        """返回 DCC 标识名，如 '3dsmax' / 'maya'。"""

    @abc.abstractmethod
    def is_available(self):
        # type: () -> bool
        """当前进程是否处于该 DCC 环境中。"""

    @abc.abstractmethod
    def get_main_window(self):
        # type: () -> Any
        """返回宿主程序主窗口句柄（QWidget 或 long ptr）。"""

    @abc.abstractmethod
    def run_on_main(self, fn, *args, **kwargs):
        # type: (Callable[..., Any], *Any, **Any) -> Any
        """把可调用对象投递到 DCC 主线程同步执行。"""

    @abc.abstractmethod
    def undo_block(self, label="agent op"):
        # type: (str) -> Any
        """返回一个上下文管理器，包裹一次可撤销的操作块。"""

    @abc.abstractmethod
    def get_selection(self):
        # type: () -> list
        """返回当前选中的节点对象列表。"""

    @abc.abstractmethod
    def get_node_by_name(self, name):
        # type: (str) -> Optional[Any]
        """按名称查找场景节点。"""

    @abc.abstractmethod
    def create_primitive(self, typename, **kwargs):
        # type: (str, **Any) -> Optional[Any]
        """创建一个基础图元。"""

    @abc.abstractmethod
    def execute_script(self, code, language="python"):
        # type: (str, str) -> Any
        """在当前 DCC 进程中执行脚本。"""
