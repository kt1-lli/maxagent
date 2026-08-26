#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Maya 适配器实现。

提供 Maya 环境下的主线程调度、undo 包裹、场景查询等统一能力。
"""

from __future__ import absolute_import
from __future__ import print_function

import threading
import traceback
from typing import Any
from typing import Callable
from typing import Optional

from ..logger import get_logger
from .adapter import DCCAdapter

logger = get_logger(__name__)


def _get_cmds():
    # type: () -> Any
    """延迟导入并返回 maya.cmds 模块。"""
    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
    return cmds


class MayaAdapter(DCCAdapter):
    """Maya DCC 适配器。"""

    @property
    def name(self):
        return 'maya'

    def is_available(self):
        try:
            import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
            return cmds is not None
        except ImportError:
            return False

    def get_main_window(self):
        # type: () -> Any
        """返回 Maya 主窗口 QWidget。"""
        # pylint: disable=import-outside-toplevel
        from ..qt_compat import get_maya_main_window
        return get_maya_main_window()

    def run_on_main(self, fn, *args, **kwargs):
        # type: (Callable[..., Any], *Any, **Any) -> Any
        """把 fn 投递到 Maya 主线程同步执行。

        Maya 中所有 cmds 调用都必须在主线程执行。使用 maya.utils.executeInMainThreadWithResult
        阻塞等待结果返回。
        """
        timeout = kwargs.pop('_timeout', 60.0)
        if _is_maya_main_thread():
            return fn(*args, **kwargs)

        from maya.utils import executeInMainThreadWithResult  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
        result = [None]  # type: list
        error = [None]  # type: list
        done = threading.Event()

        def _wrapper():
            try:
                result[0] = fn(*args, **kwargs)
            except Exception as exc:  # pylint: disable=broad-except
                error[0] = exc
                traceback.print_exc()
            finally:
                done.set()

        executeInMainThreadWithResult(_wrapper)
        if not done.wait(timeout=timeout):
            raise TimeoutError('Maya 主线程派发超时（{} 秒）'.format(timeout))
        if error[0] is not None:
            raise error[0]
        return result[0]

    def undo_block(self, label="agent op"):
        # type: (str) -> MayaUndoBlock
        """返回 Maya 专用的 undo 上下文。"""
        return MayaUndoBlock(label)

    def get_selection(self):
        # type: () -> list
        """返回当前选中的 Maya 节点名列表。"""
        cmds = _get_cmds()
        return cmds.ls(selection=True, long=True) or []

    def get_node_by_name(self, name):
        # type: (str) -> Optional[Any]
        """按名称查找 Maya 节点；返回节点名或 None。"""
        cmds = _get_cmds()
        if cmds.objExists(name):
            return name
        return None

    def create_primitive(self, typename, **kwargs):
        # type: (str, **Any) -> Optional[Any]
        """创建一个 Maya 基础图元，返回 transform 名。"""
        cmds = _get_cmds()
        fn = getattr(cmds, typename, None)
        if fn is None:
            raise RuntimeError('未知 Maya 图元类型: {}'.format(typename))
        result = fn(**kwargs)
        if isinstance(result, (list, tuple)):
            return result[0]
        return result

    def execute_script(self, code, language="python"):
        # type: (str, str) -> Any
        """在 Maya 进程中执行脚本。"""
        cmds = _get_cmds()
        if language == 'mel':
            return self.run_on_main(lambda: cmds.meval(code))
        return self.run_on_main(lambda: exec(code, {'cmds': cmds}))  # pylint: disable=exec-used


class MayaUndoBlock(object):
    """以 with 语法包裹一段 Maya 操作，作为单次 undo 块。"""

    def __init__(self, label="agent op"):
        self._label = label

    def __enter__(self):
        cmds = _get_cmds()
        cmds.undoInfo(openChunk=True, chunkName=self._label)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        cmds = _get_cmds()
        cmds.undoInfo(closeChunk=True)
        return False


def _is_maya_main_thread():
    # type: () -> bool
    """判断当前线程是否是 Maya 主线程。"""
    try:
        from maya.api import OpenMaya as om  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
        return om.MThreadUtils.isMainThread()
    except Exception:  # pylint: disable=broad-except
        return threading.current_thread() is threading.main_thread()
