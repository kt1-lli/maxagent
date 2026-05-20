#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pymxs / MaxScript 运行时辅助。

封装：
1. 主线程派发：Max 不是线程安全的，所有 pymxs 调用必须 marshal 回主线程。
2. Undo 包装：每次 agent 操作包一层 undo 上下文，让用户可以 Ctrl+Z 回滚。
3. Max 版本探测、API 能力探测：屏蔽 22~27 版本差异。
4. MaxScript 字符串转义：避免代码里的引号/换行破坏 rt.execute() 调用。
"""

from __future__ import absolute_import
from __future__ import print_function

import threading
import traceback
from typing import Any
from typing import Callable
from typing import Optional


# ---------------------------------------------------------------------- #
# pymxs 引用（在 Max 之外运行时延迟导入失败）
# ---------------------------------------------------------------------- #

try:
    import pymxs  # type: ignore  # pylint: disable=import-error
    rt = pymxs.runtime  # pylint: disable=invalid-name
    IN_MAX = True
except ImportError:
    pymxs = None  # type: ignore
    rt = None  # type: ignore  # pylint: disable=invalid-name
    IN_MAX = False


# ---------------------------------------------------------------------- #
# 版本与能力探测
# ---------------------------------------------------------------------- #

def get_max_version():
    """返回 Max 版本号 int（如 2024 / 2025），获取不到时返回 0。"""
    if not IN_MAX:
        return 0
    try:
        ver = rt.maxVersion()
        # Max 不同版本 maxVersion() 返回元组格式略有差异，
        # 遍历找 4 位数年份最稳
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
    if not IN_MAX:
        return False
    return hasattr(rt, name)


# ---------------------------------------------------------------------- #
# 主线程派发器
# ---------------------------------------------------------------------- #

def _make_bridge_class():
    """延迟创建 Qt Bridge 类，避免模块导入期就依赖 Qt。"""
    # pylint: disable=import-outside-toplevel
    from .qt_compat import QtCore

    class _Bridge(QtCore.QObject):
        """Qt 桥接对象，用 signal 把任务从子线程送到主线程。"""

        invoke = QtCore.Signal(object)

        def __init__(self):
            super(_Bridge, self).__init__()
            self.invoke.connect(self._on_invoke, QtCore.Qt.QueuedConnection)

        @staticmethod
        def _on_invoke(task):
            try:
                task["result"] = task["fn"](*task["args"], **task["kwargs"])
            except Exception as exc:  # pylint: disable=broad-except
                task["error"] = exc
                task["traceback"] = traceback.format_exc()
            finally:
                task["done"].set()

    return _Bridge, QtCore


class _MainThreadDispatcher(object):
    """通过 Qt 信号/槽把 callable 投递到主线程执行。"""

    _instance = None
    _instance_lock = threading.Lock()

    def __init__(self):
        bridge_cls, qt_core = _make_bridge_class()
        self._qt_core = qt_core
        # Bridge 必须在主线程构造（Qt 要求 parent 关系），首次 instance() 通常发生在 UI 创建期
        self._bridge = bridge_cls()

    @classmethod
    def instance(cls):
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def call(
        self,
        fn,                         # type: Callable[..., Any]
        args=None,                  # type: Optional[tuple]
        kwargs=None,                # type: Optional[dict]
        timeout=60.0,               # type: float
    ):
        """把 fn 投递到主线程执行并等待返回。

        :param fn: 任意可调用对象
        :param args: 位置参数
        :param kwargs: 关键字参数
        :param timeout: 等待超时（秒）
        :returns: fn 的返回值
        :raises TimeoutError: 超时未完成
        :raises Exception: fn 执行抛出的异常会被原样重抛
        """
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
            print("[maxagent] 主线程执行异常:\n" + task["traceback"])
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


def run_on_main(fn, *args, **kwargs):
    """便捷函数：把 fn 投递到主线程同步执行。

    支持通过 _timeout 关键字参数指定超时（默认 60 秒）。
    """
    timeout = kwargs.pop("_timeout", 60.0)
    return _MainThreadDispatcher.instance().call(
        fn, args=args, kwargs=kwargs, timeout=timeout,
    )


# ---------------------------------------------------------------------- #
# Undo 上下文
# ---------------------------------------------------------------------- #

class undo_block(object):  # pylint: disable=invalid-name
    """以 with 语法包裹一段 pymxs 代码，使其作为单次 undo 操作。

    用法::

        with undo_block("create box"):
            rt.box()
    """

    def __init__(self, label="agent op"):
        self._label = label
        self._holder = None

    def __enter__(self):
        if not IN_MAX:
            return self
        try:
            # pymxs.undo 在不同版本签名略有差异：
            #   早期：pymxs.undo(True)  # 只接受 bool
            #   新版：pymxs.undo(True, "label")  # 支持 label
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


# ---------------------------------------------------------------------- #
# MaxScript 字符串转义与执行
# ---------------------------------------------------------------------- #

def escape_maxscript_string(text):
    """把 Python 字符串转义为可嵌入 MaxScript 双引号字符串字面量的形式。

    MaxScript 字符串中需要转义的字符：双引号、反斜杠、换行、回车、制表。
    """
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


def execute_maxscript(code):
    """在主线程同步执行一段 MaxScript 源码字符串。

    :param code: MaxScript 源码
    :returns: rt.execute 的返回值
    :raises RuntimeError: Max 之外或执行失败
    """
    if not IN_MAX:
        raise RuntimeError("非 3ds Max 环境，无法执行 MaxScript")

    def _do():
        return rt.execute(code)

    return run_on_main(_do)
