#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DCC 运行时探测与主线程调度。

职责：
1. 通过尝试导入各 DCC 的 Python 模块判断当前环境。
2. 维护一个全局 DCCAdapter 实例，供上层统一调用。
3. 提供 ``run_on_main`` 便捷函数。
"""

from __future__ import absolute_import
from __future__ import print_function

import threading
from typing import Any
from typing import Callable
from typing import Optional

from ..logger import get_logger

logger = get_logger(__name__)

_DCC_NAME = None  # type: Optional[str]
_ADAPTER = None  # type: Optional[Any]
_LOCK = threading.Lock()


def current_dcc():
    # type: () -> str
    """返回当前 DCC 标识名。

    首次调用时探测环境，之后缓存结果。无法在已知 DCC 中识别时返回 'unknown'。
    """
    global _DCC_NAME  # pylint: disable=global-statement
    if _DCC_NAME is not None:
        return _DCC_NAME
    with _LOCK:
        if _DCC_NAME is not None:
            return _DCC_NAME
        _DCC_NAME = _detect_dcc()
        return _DCC_NAME


def _detect_dcc():
    # type: () -> str
    """探测当前 DCC 环境。"""
    try:
        import pymxs  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
        if pymxs is not None:
            return '3dsmax'
    except ImportError:
        pass
    try:
        import maya.cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
        if maya.cmds is not None:
            return 'maya'
    except ImportError:
        pass
    return 'unknown'


def get_adapter():
    # type: () -> Any
    """获取当前 DCC 的适配器实例。

    unknown 环境下返回一个占位适配器，所有方法都会抛出 RuntimeError。
    """
    global _ADAPTER  # pylint: disable=global-statement
    if _ADAPTER is not None:
        return _ADAPTER
    with _LOCK:
        if _ADAPTER is not None:
            return _ADAPTER
        dcc = current_dcc()
        if dcc == '3dsmax':
            from .max_adapter import MaxAdapter
            _ADAPTER = MaxAdapter()
        elif dcc == 'maya':
            from .maya_adapter import MayaAdapter
            _ADAPTER = MayaAdapter()
        else:
            from .adapter import DCCAdapter

            class UnknownAdapter(DCCAdapter):
                """unknown 环境下的占位适配器。"""

                @property
                def name(self):
                    return 'unknown'

                def is_available(self):
                    return False

                def _raise(self):
                    raise RuntimeError('当前不是已知 DCC 环境（3ds Max / Maya）')

                def get_main_window(self):
                    self._raise()

                def run_on_main(self, fn, *args, **kwargs):
                    self._raise()

                def undo_block(self, label="agent op"):
                    self._raise()

                def get_selection(self):
                    self._raise()

                def get_node_by_name(self, name):
                    self._raise()

                def create_primitive(self, typename, **kwargs):
                    self._raise()

                def execute_script(self, code, language="python"):
                    self._raise()

            _ADAPTER = UnknownAdapter()
        return _ADAPTER


def run_on_main(fn, *args, **kwargs):
    # type: (Callable[..., Any], *Any, **Any) -> Any
    """把可调用对象投递到当前 DCC 主线程同步执行。

    非 DCC 环境下会抛出 RuntimeError。
    """
    return get_adapter().run_on_main(fn, *args, **kwargs)
