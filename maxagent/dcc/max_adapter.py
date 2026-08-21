#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""3ds Max 适配器实现。

把原 runtime_helpers.py 中的主线程调度、undo、版本探测等逻辑迁移到这里，
作为 DCCAdapter 的具体实现。
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


def _get_pymxs():
    # type: () -> Any
    """延迟导入并返回 pymxs 模块。"""
    import pymxs  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
    return pymxs


def _get_rt():
    # type: () -> Any
    """延迟导入并返回 pymxs.runtime。"""
    return _get_pymxs().runtime


class MaxAdapter(DCCAdapter):
    """3ds Max DCC 适配器。"""

    @property
    def name(self):
        return '3dsmax'

    def is_available(self):
        try:
            import pymxs  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
            return pymxs is not None
        except ImportError:
            return False

    def get_main_window(self):
        # type: () -> Any
        """返回 Max 主窗口 QWidget。"""
        # pylint: disable=import-outside-toplevel
        from ..qt_compat import QtWidgets
        app = QtWidgets.QApplication.instance()
        if app is None:
            return None
        for widget in app.topLevelWidgets():
            if isinstance(widget, QtWidgets.QMainWindow):
                return widget
        return None

    def run_on_main(self, fn, *args, **kwargs):
        # type: (Callable[..., Any], *Any, **Any) -> Any
        """把 fn 投递到 Max 主线程同步执行。"""
        timeout = kwargs.pop('_timeout', 60.0)
        return _MainThreadDispatcher.instance().call(
            fn, args=args, kwargs=kwargs, timeout=timeout,
        )

    def undo_block(self, label="agent op"):
        # type: (str) -> MaxUndoBlock
        """返回 Max 专用的 undo 上下文。"""
        return MaxUndoBlock(label)

    def get_selection(self):
        # type: () -> list
        """返回当前选中的 Max 节点。"""
        rt = _get_rt()
        return list(rt.selection)

    def get_node_by_name(self, name):
        # type: (str) -> Optional[Any]
        """按名称查找 Max 节点。"""
        rt = _get_rt()
        node = rt.getNodeByName(name)
        # getNodeByName 在部分场景下会返回 MXSWrapperBase，需做真值判断
        if node is None or not node:
            return None
        return node

    def create_primitive(self, typename, **kwargs):
        # type: (str, **Any) -> Optional[Any]
        """创建一个 Max 基础图元。"""
        rt = _get_rt()
        fn = getattr(rt, typename, None)
        if fn is None:
            raise RuntimeError('未知 Max 图元类型: {}'.format(typename))
        return fn(**kwargs) if kwargs else fn()

    def execute_script(self, code, language="python"):
        # type: (str, str) -> Any
        """在 Max 进程中执行脚本。"""
        if language == 'maxscript':
            rt = _get_rt()
            return self.run_on_main(lambda: rt.execute(code))
        # python 代码在 Max 环境下直接用 exec 执行，注入 pymxs
        import pymxs  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
        env = {'pymxs': pymxs, 'rt': pymxs.runtime}
        return self.run_on_main(lambda: exec(code, env))  # pylint: disable=exec-used


def get_max_version():
    """返回 Max 版本号 int（如 2024 / 2025），获取不到时返回 0。"""
    try:
        import pymxs  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
        rt = pymxs.runtime
        ver = rt.maxVersion()
        for v in list(ver):
            try:
                iv = int(v)
                if 2000 <= iv <= 2100:
                    return iv
            except (TypeError, ValueError):
                continue
    except Exception:  # pylint: disable=broad-except
        pass
    return 0


def has_runtime_attr(name):
    """检测 pymxs.runtime 是否有某个 API（用于跨版本能力探测）。"""
    try:
        import pymxs  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
        return hasattr(pymxs.runtime, name)
    except Exception:  # pylint: disable=broad-except
        return False


def escape_maxscript_string(text):
    """把 Python 字符串转义为可嵌入 MaxScript 双引号字符串字面量的形式。"""
    if text is None:
        return ""
    out = []
    for ch in text:
        if ch == "\\":
            out.append("\\\\")
        elif ch == "\"":
            out.append("\\\"")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        else:
            out.append(ch)
    return "".join(out)


class MaxUndoBlock(object):
    """以 with 语法包裹一段 pymxs 代码，使其作为单次 undo 操作。"""

    def __init__(self, label="agent op"):
        self._label = label
        self._holder = None

    def __enter__(self):
        try:
            pymxs = _get_pymxs()
            try:
                self._holder = pymxs.undo(True, self._label)
            except TypeError:
                self._holder = pymxs.undo(True)
            self._holder.__enter__()
        except Exception:  # pylint: disable=broad-except
            self._holder = None
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._holder is None:
            return False
        try:
            return self._holder.__exit__(exc_type, exc_val, exc_tb)
        except Exception:  # pylint: disable=broad-except
            return False


class _MainThreadDispatcher(object):
    """通过 Qt 信号/槽把 callable 投递到主线程执行。"""

    _instance = None
    _instance_lock = threading.Lock()

    def __init__(self):
        # pylint: disable=import-outside-toplevel
        from ..qt_compat import QtCore
        bridge_cls = self._make_bridge_class(QtCore)
        self._qt_core = QtCore
        self._bridge = bridge_cls()

    @classmethod
    def _make_bridge_class(cls, qt_core):
        """延迟创建 Qt Bridge 类。"""
        class _Bridge(qt_core.QObject):
            invoke = qt_core.Signal(object)

            def __init__(self):
                super(_Bridge, self).__init__()
                self.invoke.connect(self._on_invoke, qt_core.Qt.QueuedConnection)

            @staticmethod
            def _on_invoke(task):
                try:
                    task["result"] = task["fn"](*task["args"], **task["kwargs"])
                except Exception as exc:  # pylint: disable=broad-except
                    task["error"] = exc
                    task["traceback"] = traceback.format_exc()
                finally:
                    task["done"].set()

        return _Bridge

    @classmethod
    def instance(cls):
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def call(self, fn, args=None, kwargs=None, timeout=60.0):
        """把 fn 投递到主线程执行并等待返回。"""
        if self._is_main_thread():
            return fn(*(args or ()), **(kwargs or {}))

        task = {
            "fn": fn,
            "args": args or (),
            "kwargs": kwargs or {},
            "done": threading.Event(),
            "result": None,
            "error": None,
            "traceback": "",
        }
        self._bridge.invoke.emit(task)
        if not task["done"].wait(timeout=timeout):
            raise TimeoutError("主线程派发超时（{} 秒）".format(timeout))
        if task["error"] is not None:
            logger.error("主线程执行异常:\n%s", task["traceback"])
            raise task["error"]
        return task["result"]

    def _is_main_thread(self):
        try:
            app = self._qt_core.QCoreApplication.instance()
            if app is None:
                return threading.current_thread() is threading.main_thread()
            return self._qt_core.QThread.currentThread() is app.thread()
        except Exception:  # pylint: disable=broad-except
            return threading.current_thread() is threading.main_thread()
