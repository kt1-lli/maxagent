#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MaxAgent 热重载模块。

3ds Max 进程一旦 import 过 ``maxagent.*``，常规的修改 .py 文件后保存并不会
让 Max 内的代码生效——Python 把模块缓存进 ``sys.modules``，下次 import 直接
返回旧对象。开发期反复重启 Max 体验非常差。

本模块提供 ``reload_maxagent()``，按以下顺序安全地重载整个包：

1. 主动 flush 当前 UI 状态 + 当前会话到磁盘，避免卡在脏状态
2. 关闭并销毁旧的 DockWidget / QDockWidget 单例
3. 把 ``sys.modules`` 里所有 ``maxagent`` / ``maxagent.*`` 条目移除
4. 用 ``importlib`` 重新 import ``maxagent`` 并调用 ``show_panel(force=True)``

设计权衡：
- 用 "purge sys.modules + 重新 import" 而不是 ``importlib.reload``，因为后者
  对子模块依赖的处理顺序很微妙，循环引用 / 类继承场景容易出现 "isinstance
  失败" 之类的诡异问题。整包 purge 后重新 import 是最朴素也最稳的方案。
- 第三方库（PySide / pymxs 等）刻意**不**移除，避免破坏 Max 主进程的
  Qt 单例和 MaxScript 桥接。
- 不依赖 Max 环境，单测也能跑（异常时只打日志不抛）。

使用方式（任选其一）：

* 通过 UI 顶栏的 "🔄 重加载" 按钮触发。
* 在 MaxScript Listener 中::

      python.execute "import maxagent.reload as _r; _r.reload_maxagent()"

* 或注册全局 MaxScript 函数::

      g_reload_max_agent()

这是开发态便利工具，不应在生产环境频繁调用——重载会丢弃工具注册表、技能
缓存等内存状态（已落盘的会安全恢复）。
"""

from __future__ import absolute_import
from __future__ import print_function

import importlib
import sys
import traceback
from typing import List
from typing import Optional

from .logger import get_logger


logger = get_logger(__name__)

# 顶层包名，所有以此为前缀的模块都会在重载时被清掉
_PACKAGE_PREFIX = 'maxagent'

# reload 自身所属模块名（避免被 purge 时误杀，导致无法继续执行后续语句）
_THIS_MODULE = __name__

# 不应被卸载的模块白名单：这些是绑定到 Max 主进程或 Qt 单例的"重型"对象，
# 重载时刻意保留，不会污染普通 Python 代码热替换的语义。
_SKIP_MODULE_PREFIXES = (
    'maxagent.qt_compat',  # Qt 兼容层，含 PySide 单例引用
)


def _list_maxagent_modules():
    # type: () -> List[str]
    """返回当前 sys.modules 里所有 maxagent 包内模块名（含子模块）。"""
    names = []
    for name in list(sys.modules.keys()):
        if name == _PACKAGE_PREFIX or name.startswith(_PACKAGE_PREFIX + '.'):
            names.append(name)
    return names


def _close_existing_panel():
    """尽可能优雅地关闭旧 DockWidget 单例。失败不抛，只打日志。"""
    try:
        startup_mod = sys.modules.get(_PACKAGE_PREFIX + '.startup')
        if startup_mod is None:
            return
        # 先 flush 一次状态：UI 几何 + 当前会话
        flush = getattr(startup_mod, 'flush_state', None)
        if callable(flush):
            try:
                flush()
            except Exception:  # pylint: disable=broad-except
                logger.exception('reload: flush_state 失败')

        dock = getattr(startup_mod, '_DOCK_WIDGET', None)
        holder = getattr(startup_mod, '_QDOCK_HOLDER', None)
        # 触发会话保存（dock_widget 自己实现了 closeEvent 兜底，
        # 这里再显式调一次是双保险）
        if dock is not None:
            save_session = getattr(dock, '_save_current_session', None)
            if callable(save_session):
                try:
                    save_session()
                except Exception:  # pylint: disable=broad-except
                    logger.exception('reload: 会话保存失败')
        # 销毁外层 QDockWidget（Max 主窗口的真实容器）
        if holder is not None:
            try:
                holder.setParent(None)
                holder.deleteLater()
            except Exception:  # pylint: disable=broad-except
                logger.exception('reload: 销毁 QDockWidget 容器失败')
        # 销毁内部 widget
        if dock is not None:
            try:
                dock.setParent(None)
                dock.deleteLater()
            except Exception:  # pylint: disable=broad-except
                logger.exception('reload: 销毁 DockWidget 内部 widget 失败')
        # 清空模块级单例引用
        try:
            startup_mod._DOCK_WIDGET = None  # noqa: SLF001
            startup_mod._QDOCK_HOLDER = None  # noqa: SLF001
        except Exception:  # pylint: disable=broad-except
            pass
    except Exception:  # pylint: disable=broad-except
        logger.exception('reload: _close_existing_panel 整体失败')


def _purge_modules(skip_self=True):
    # type: (bool) -> int
    """从 sys.modules 移除 maxagent 包内的所有模块条目。

    :param skip_self: True 时保留当前模块（``maxagent.reload``）和
                      白名单中的 Qt 兼容层，避免 "执行到一半被自己卸载"
                      的诡异情况。
    :returns: 实际被移除的模块数
    """
    targets = _list_maxagent_modules()
    removed = 0
    for name in targets:
        if skip_self and name == _THIS_MODULE:
            continue
        if any(name == p or name.startswith(p + '.') or name == p
               for p in _SKIP_MODULE_PREFIXES):
            continue
        try:
            del sys.modules[name]
            removed += 1
        except KeyError:
            pass
    return removed


def reload_maxagent(reshow=True):
    # type: (bool) -> Optional[object]
    """热重载整个 ``maxagent`` 包。

    :param reshow: True 时重载完成后强制显示新 DockWidget；False 仅清理 +
                   重新 import，不弹窗（CI / 调试用）
    :returns: 新的 DockWidget 实例（``reshow=True`` 时）或 None
    :raises ImportError: 重新 import 阶段失败时抛出，调用方据此提示用户

    使用前提：``maxagent`` 所在目录已经在 ``sys.path`` 中
    （通过 ``MAXAGENT_INSTALL.ms`` 或手工 ``sys.path.insert`` 加进去）。
    """
    logger.info('reload: 开始热重载')
    print('[MaxAgent] reload: 开始热重载...')

    # 1. 先把状态落盘 + 关旧 UI
    _close_existing_panel()

    # 1.5 关闭旧的 logging handler，避免重新 import 后旧文件句柄泄露
    try:
        logger_mod = sys.modules.get(_PACKAGE_PREFIX + '.logger')
        if logger_mod is not None:
            shutdown = getattr(logger_mod, 'shutdown_logging', None)
            if callable(shutdown):
                shutdown()
    except Exception:  # pylint: disable=broad-except
        # logger 自身已被 shutdown，不能再用 logger.exception，退回 traceback
        traceback.print_exc()

    # 2. 清掉模块缓存
    n = _purge_modules(skip_self=True)
    print('[MaxAgent] reload: 已卸载 {} 个模块'.format(n))

    # 3. 重新 import 顶层包，触发子模块重新加载
    # 注意：先把"自己"也卸载，让下次 import maxagent.reload 拿到新版
    # 但是保留**当前正在执行的栈帧**所引用的旧模块对象（Python 允许这样做）
    try:
        sys.modules.pop(_THIS_MODULE, None)
        importlib.invalidate_caches()
        new_pkg = importlib.import_module(_PACKAGE_PREFIX)
    except ImportError:
        # 重 import 失败时 logger 已被 shutdown 且尚未恢复，统一走 traceback
        traceback.print_exc()
        raise

    # 重新拿一个 logger（新模块对象）
    try:
        new_logger_mod = importlib.import_module(_PACKAGE_PREFIX + '.logger')
        new_logger = new_logger_mod.get_logger(__name__)
        new_logger.info('reload: 已卸载 %d 个模块并重新 import', n)
    except Exception:  # pylint: disable=broad-except
        new_logger = None  # noqa: F841

    # 4. 重新创建 DockWidget
    if not reshow:
        print('[MaxAgent] reload: 仅卸载，不重新显示（reshow=False）')
        return None

    try:
        startup = importlib.import_module(_PACKAGE_PREFIX + '.startup')
        dock = startup.show_panel(force=True)
        print('[MaxAgent] reload: 完成 ✓')
        return dock
    except Exception:  # pylint: disable=broad-except
        traceback.print_exc()
        return None


def register_maxscript_hook():
    """在 Max 中注册 ``g_reload_max_agent`` 全局函数，方便 Listener 调用。

    非 Max 环境（缺 ``pymxs``）时静默跳过。
    """
    try:
        from pymxs import runtime as rt  # pylint: disable=import-error
    except ImportError:
        return False
    try:
        rt.execute(
            'global g_reload_max_agent\n'
            'fn g_reload_max_agent = python.execute '
            '"import maxagent.reload as _r; _r.reload_maxagent()"\n'
        )
        logger.info('已注册 MaxScript 函数: g_reload_max_agent()')
        print(
            '[MaxAgent] 已注册 MaxScript 函数: g_reload_max_agent()',
        )
        return True
    except Exception:  # pylint: disable=broad-except
        logger.exception('注册 MaxScript hook 失败')
        return False


__all__ = [
    'reload_maxagent',
    'register_maxscript_hook',
]
