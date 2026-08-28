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

import os
import sys
import threading
from typing import Any
from typing import Callable
from typing import Dict
from typing import Optional

from ..logger import get_logger

logger = get_logger(__name__)

# 使用 sys.modules 中的共享可变对象保存 DCC 状态，确保热重载（purge
# sys.modules 后重新 import）时，旧函数闭包与新模块对象仍引用同一份
# 运行时状态，避免 current_dcc() / get_adapter() 出现多版本不一致。
_DCC_STATE_KEY = 'maxagent.dcc.runtime._DCC_STATE'
_ADAPTER_STATE_KEY = 'maxagent.dcc.runtime._ADAPTER_STATE'
if _DCC_STATE_KEY not in sys.modules:
    sys.modules[_DCC_STATE_KEY] = {'name': None}
if _ADAPTER_STATE_KEY not in sys.modules:
    sys.modules[_ADAPTER_STATE_KEY] = {'adapter': None}
_DCC_STATE = sys.modules[_DCC_STATE_KEY]  # type: Dict[str, Optional[str]]
_ADAPTER_STATE = sys.modules[_ADAPTER_STATE_KEY]  # type: Dict[str, Optional[Any]]
_DCC_NAME = None  # type: Optional[str]
_ADAPTER = None  # type: Optional[Any]
_LOCK = threading.Lock()


def set_current_dcc(name):
    # type: (str) -> None
    """显式设置当前 DCC 标识名。

    入口脚本（如 maya_entry.py / maxagent.startup）应在导入任何依赖
    ``current_dcc()`` 的模块之前调用本函数，避免自动探测出现偏差或
    被历史缓存污染。合法取值为 ``'3dsmax'``、``'maya'``。
    """
    name = (name or '').strip().lower()
    if name not in ('3dsmax', 'maya'):
        raise ValueError("DCC 标识必须是 '3dsmax' 或 'maya'， got: {}".format(name))
    global _DCC_NAME  # pylint: disable=global-statement
    with _LOCK:
        _DCC_NAME = name
        dcc_state = sys.modules.get(_DCC_STATE_KEY)
        if dcc_state is not None:
            dcc_state['name'] = name
        # 显式切换 DCC 后，旧适配器可能不合法，直接清空让 get_adapter 重建
        adapter_state = sys.modules.get(_ADAPTER_STATE_KEY)
        if adapter_state is not None:
            adapter_state['adapter'] = None
        global _ADAPTER  # pylint: disable=global-statement
        _ADAPTER = None


def current_dcc():
    # type: () -> str
    """返回当前 DCC 标识名。

    首次调用时探测环境，之后缓存结果。无法在已知 DCC 中识别时返回 'unknown'。
    """
    # 优先从 sys.modules 共享状态读取，确保热重载后旧函数闭包与新模块
    # 对象始终看到同一份运行时状态
    dcc_state = sys.modules.get(_DCC_STATE_KEY)
    if dcc_state is not None:
        name = dcc_state.get('name')
        if name is not None:
            return name
    global _DCC_NAME  # pylint: disable=global-statement
    if _DCC_NAME is not None:
        return _DCC_NAME
    with _LOCK:
        if dcc_state is not None:
            name = dcc_state.get('name')
            if name is not None:
                return name
        if _DCC_NAME is not None:
            return _DCC_NAME
        _DCC_NAME = _detect_dcc()
        if dcc_state is not None:
            dcc_state['name'] = _DCC_NAME
        return _DCC_NAME


def _detect_dcc():
    # type: () -> str
    """探测当前 DCC 环境。"""
    forced = os.environ.get('MAXAGENT_FORCE_DCC', '').strip().lower()
    if forced in ('3dsmax', 'max'):
        return '3dsmax'
    if forced in ('maya',):
        return 'maya'

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
    # 优先从 sys.modules 共享状态读取，确保热重载后旧函数闭包与新模块
    # 对象始终看到同一份运行时状态
    adapter_state = sys.modules.get(_ADAPTER_STATE_KEY)
    if adapter_state is not None:
        adapter = adapter_state.get('adapter')
        if adapter is not None:
            return adapter
    global _ADAPTER  # pylint: disable=global-statement
    if _ADAPTER is not None:
        return _ADAPTER
    # current_dcc 内部也会竞争同一把 _LOCK，必须在持锁前完成探测
    dcc = current_dcc()
    with _LOCK:
        if adapter_state is not None:
            adapter = adapter_state.get('adapter')
            if adapter is not None:
                return adapter
        if _ADAPTER is not None:
            return _ADAPTER
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
        if adapter_state is not None:
            adapter_state['adapter'] = _ADAPTER
        return _ADAPTER


def run_on_main(fn, *args, **kwargs):
    # type: (Callable[..., Any], *Any, **Any) -> Any
    """把可调用对象投递到当前 DCC 主线程同步执行。

    非 DCC 环境下会抛出 RuntimeError。
    """
    return get_adapter().run_on_main(fn, *args, **kwargs)
