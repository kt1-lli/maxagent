#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""3ds Max 启动入口。

放在 Max 的 startup script 目录下：
- Windows: %LOCALAPPDATA%\\Autodesk\\3dsMax\\<ver>\\ENU\\scripts\\startup\\
- 或者用户菜单/快捷键调用 ``maxagent.startup.show_panel()``

启动流程:
1. 加载所有工具
2. 创建/复用单例 DockWidget
3. 用 QtMax.GetQMaxMainWindow() 当 parent 包到 QDockWidget 里
4. 注册到 Max 主窗口右侧

也支持非 Max 环境直接 ``python -m maxagent.startup`` 起一个独立窗口调试 UI。
"""

from __future__ import absolute_import
from __future__ import print_function

import os
import sys
import traceback


# 单例 DockWidget 引用，防止被 GC
_DOCK_WIDGET = None
_QDOCK_HOLDER = None


def _ensure_package_path():
    """确保 maxagent 包能被 import 到。

    Max 的 startup 脚本位置和包安装位置可能不同，加一层兜底。
    """
    # 当前文件在 maxagent/startup.py，把 maxagent 的父目录加进 sys.path
    here = os.path.dirname(os.path.abspath(__file__))
    parent = os.path.dirname(here)
    if parent and parent not in sys.path:
        sys.path.insert(0, parent)


def _get_max_main_window():
    """尝试拿到 3ds Max 主窗口（Qt QWidget），失败时返回 None。"""
    try:
        # Max 2020+ 推荐方式
        from qtmax import GetQMaxMainWindow  # pylint: disable=import-error
        return GetQMaxMainWindow()
    except ImportError:
        pass
    try:
        # 旧版本 Max 走 MaxPlus
        import MaxPlus  # pylint: disable=import-error
        try:
            from PySide2 import shiboken2 as shiboken  # noqa
            ptr = MaxPlus.GetQMaxMainWindow()
            return ptr
        except Exception:  # pylint: disable=broad-except
            return None
    except ImportError:
        return None
    except Exception:  # pylint: disable=broad-except
        return None


def show_panel():
    """显示 MaxAgent 面板（在 Max 内或独立窗口模式）。"""
    _ensure_package_path()

    # 延迟 import 让 sys.path 先生效
    from maxagent.config import ConfigManager
    from maxagent.qt_compat import QtCore
    from maxagent.qt_compat import QtWidgets
    from maxagent.tools import load_all_tools
    from maxagent.ui.dock_widget import MaxAgentDockWidget

    global _DOCK_WIDGET, _QDOCK_HOLDER  # pylint: disable=global-statement

    # 1. 加载工具
    n = load_all_tools(include_escape_hatch=True)
    print('[MaxAgent] 已加载 {} 个工具'.format(n))

    # 2. 复用单例
    if _DOCK_WIDGET is not None:
        try:
            if _QDOCK_HOLDER is not None:
                _QDOCK_HOLDER.show()
                _QDOCK_HOLDER.raise_()
            else:
                _DOCK_WIDGET.show()
                _DOCK_WIDGET.raise_()
            return _DOCK_WIDGET
        except Exception:  # pylint: disable=broad-except
            # 之前的窗口被销毁了，重建
            _DOCK_WIDGET = None
            _QDOCK_HOLDER = None

    # 3. 准备配置
    config = ConfigManager()

    main_win = _get_max_main_window()
    if main_win is not None:
        # Max 环境：包到 QDockWidget 里
        dock_widget = MaxAgentDockWidget(config_manager=config, parent=main_win)
        qdock = QtWidgets.QDockWidget('MaxAgent', main_win)
        qdock.setObjectName('MaxAgentQDockWidget')
        qdock.setWidget(dock_widget)
        qdock.setAllowedAreas(
            QtCore.Qt.LeftDockWidgetArea | QtCore.Qt.RightDockWidgetArea,
        )
        try:
            main_win.addDockWidget(QtCore.Qt.RightDockWidgetArea, qdock)
        except Exception:  # pylint: disable=broad-except
            # 某些 Max 主窗口不接受 addDockWidget，退化为浮动
            qdock.setFloating(True)
        qdock.show()
        qdock.raise_()
        _DOCK_WIDGET = dock_widget
        _QDOCK_HOLDER = qdock
    else:
        # 独立窗口：当作普通顶层 widget
        dock_widget = MaxAgentDockWidget(config_manager=config)
        dock_widget.resize(720, 800)
        dock_widget.show()
        _DOCK_WIDGET = dock_widget
        _QDOCK_HOLDER = None

    return _DOCK_WIDGET


def hide_panel():
    """隐藏面板（不销毁，保留对话历史）。"""
    global _DOCK_WIDGET, _QDOCK_HOLDER  # pylint: disable=global-statement
    if _QDOCK_HOLDER is not None:
        _QDOCK_HOLDER.hide()
    elif _DOCK_WIDGET is not None:
        _DOCK_WIDGET.hide()


def toggle_panel():
    """切换显示/隐藏。"""
    if _DOCK_WIDGET is not None:
        target = _QDOCK_HOLDER if _QDOCK_HOLDER is not None else _DOCK_WIDGET
        if target.isVisible():
            target.hide()
        else:
            target.show()
            target.raise_()
    else:
        show_panel()


# ---------------------------------------------------------------------- #
# 自启动钩子（放在 Max startup 目录时会被自动执行）
# ---------------------------------------------------------------------- #
def _auto_register():
    """注册菜单 / 快捷键到 Max。失败不影响功能（用户手动调 show_panel 也行）。"""
    try:
        # Max 2018+ 走 pymxs
        from pymxs import runtime as rt  # pylint: disable=import-error
        # 注册一个全局 MaxScript 函数供菜单调用
        # 通过 python.execute 让 mxs 调到这里
        rt.execute(
            'global g_show_max_agent\n'
            'fn g_show_max_agent = python.execute '
            '"import maxagent.startup as _s; _s.show_panel()"\n'
        )
        print(
            '[MaxAgent] 已注册 MaxScript 函数: g_show_max_agent()'
            ' (在 MAXScript Listener 中调用即可显示面板)',
        )
    except ImportError:
        # 非 Max 环境，跳过
        pass
    except Exception:  # pylint: disable=broad-except
        traceback.print_exc()


# ---------------------------------------------------------------------- #
# 入口
# ---------------------------------------------------------------------- #
if __name__ == '__main__':
    # 独立窗口调试模式：python -m maxagent.startup
    _ensure_package_path()
    from maxagent.qt_compat import QtWidgets
    app = QtWidgets.QApplication.instance()
    created_app = False
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
        created_app = True
    show_panel()
    if created_app:
        sys.exit(app.exec_())
else:
    # 被 Max 自动加载时执行
    try:
        _auto_register()
    except Exception:  # pylint: disable=broad-except
        traceback.print_exc()
