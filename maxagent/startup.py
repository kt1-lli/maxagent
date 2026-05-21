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
4. 注册到 Max 主窗口（停靠区域 / 浮动状态由 ui_state.json 决定）

是否在 Max 启动时自动弹出由 ``AppConfig.auto_show_on_startup`` 决定，
用户可在「设置」里关闭。关闭后仍可手动调 ``g_show_max_agent()``。

UI 状态（窗口几何 / 停靠位置 / 分割器）持久化到 ``ui_state.json``，
关闭面板时自动保存，下次启动时恢复。

也支持非 Max 环境直接 ``python -m maxagent.startup`` 起一个独立窗口调试 UI。
"""

from __future__ import absolute_import
from __future__ import print_function

import base64
import os
import sys
import traceback


# 单例引用，防止被 GC
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


def _restore_qdock_geometry(qdock, ui_state):
    """根据 ui_state 恢复 QDockWidget 的几何与浮动状态。

    Qt saveGeometry 的二进制内容序列化为 base64 存盘，这里反序列化。
    出错时静默回退到默认布局。
    """
    geometry_b64 = (ui_state.geometry_b64 or '').strip()
    if geometry_b64:
        try:
            from .qt_compat import QtCore
            ba_bytes = base64.b64decode(geometry_b64.encode('ascii'))
            ba = QtCore.QByteArray(ba_bytes)
            ok = qdock.restoreGeometry(ba)
            if ok:
                return True
        except Exception:  # pylint: disable=broad-except
            traceback.print_exc()
    # 没有保存或恢复失败：根据 floating 字段做简单恢复
    if ui_state.floating:
        try:
            qdock.setFloating(True)
        except Exception:  # pylint: disable=broad-except
            pass
    return False


def _connect_qdock_save_hooks(qdock, dock_widget):
    """挂上保存钩子：浮动切换 / 关闭 / 区域变化时持久化 UI 状态。"""
    from .qt_compat import QtCore

    def _save():
        try:
            ba = qdock.saveGeometry()
            # PySide2/6 的 QByteArray 都能转 bytes
            try:
                geo_bytes = bytes(ba)
            except TypeError:
                geo_bytes = ba.data() if hasattr(ba, 'data') else b''
            geo_b64 = base64.b64encode(geo_bytes).decode('ascii')
            area = None
            try:
                main_win = qdock.parent()
                if main_win is not None and hasattr(main_win, 'dockWidgetArea'):
                    area = int(main_win.dockWidgetArea(qdock))
            except Exception:  # pylint: disable=broad-except
                area = None
            dock_widget.save_ui_state(
                geometry_b64=geo_b64,
                floating=qdock.isFloating(),
                dock_area=area,
                embedded_ok=True,
            )
        except Exception:  # pylint: disable=broad-except
            traceback.print_exc()

    # Qt5/6 都支持以下信号
    try:
        qdock.topLevelChanged.connect(lambda _f: _save())
    except Exception:  # pylint: disable=broad-except
        pass
    try:
        qdock.dockLocationChanged.connect(lambda _a: _save())
    except Exception:  # pylint: disable=broad-except
        pass
    try:
        qdock.visibilityChanged.connect(lambda _v: _save())
    except Exception:  # pylint: disable=broad-except
        pass

    # 把 hook 挂到 dock_widget 上，调用 ``show_panel`` 的代码
    # 也能主动调用 ``flush_state``
    dock_widget._flush_qdock_state = _save  # pylint: disable=protected-access


def _restore_standalone_geometry(widget, ui_state):
    """独立窗口模式下应用 fallback 几何。"""
    try:
        w = max(int(ui_state.window_w or 720), 320)
        h = max(int(ui_state.window_h or 800), 240)
        widget.resize(w, h)
        x = int(ui_state.window_x)
        y = int(ui_state.window_y)
        if x >= 0 and y >= 0:
            widget.move(x, y)
        if ui_state.maximized:
            widget.showMaximized()
    except Exception:  # pylint: disable=broad-except
        traceback.print_exc()


def show_panel(force=False):
    """显示 MaxAgent 面板（在 Max 内或独立窗口模式）。

    :param force: True 时即便配置里关闭了 ``auto_show_on_startup`` 也强制显示
                  （供用户从菜单 / 快捷键调用时使用）；False 时遵守配置。
    :returns: 面板实例；若被配置抑制则返回 ``None``
    """
    _ensure_package_path()

    # 延迟 import 让 sys.path 先生效
    from maxagent.config import ConfigManager
    from maxagent.qt_compat import QtCore
    from maxagent.qt_compat import QtWidgets
    from maxagent.tools import load_all_tools
    from maxagent.ui.dock_widget import MaxAgentDockWidget

    global _DOCK_WIDGET, _QDOCK_HOLDER  # pylint: disable=global-statement

    # 0. 配置门控：非 force 模式必须尊重 auto_show_on_startup
    #    这样无论调用方是 _auto_register、ms 启动器还是其他入口，
    #    只要不显式 force=True，关闭"自动显示"开关都能真正生效。
    if not force:
        try:
            cfg_mgr = ConfigManager()
            if not bool(cfg_mgr.config.auto_show_on_startup):
                print(
                    '[MaxAgent] auto_show_on_startup=False，'
                    '本次启动跳过自动显示。'
                    '可执行 g_show_max_agent() 手动显示。',
                )
                return None
        except Exception:  # pylint: disable=broad-except
            # 配置读不到不应阻塞显示，按默认开启处理
            pass

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
        ui_state = dock_widget.get_ui_state()

        qdock = QtWidgets.QDockWidget('MaxAgent', main_win)
        qdock.setObjectName('MaxAgentQDockWidget')
        qdock.setWidget(dock_widget)
        qdock.setAllowedAreas(
            QtCore.Qt.LeftDockWidgetArea | QtCore.Qt.RightDockWidgetArea,
        )
        # 默认停靠区域用 ui_state 中的值，没有则右侧
        try:
            area = QtCore.Qt.DockWidgetArea(int(ui_state.dock_area or 2))
        except Exception:  # pylint: disable=broad-except
            area = QtCore.Qt.RightDockWidgetArea
        try:
            main_win.addDockWidget(area, qdock)
        except Exception:  # pylint: disable=broad-except
            # 某些 Max 主窗口不接受 addDockWidget，退化为浮动
            qdock.setFloating(True)

        # 恢复几何（位置、大小、是否浮动）
        _restore_qdock_geometry(qdock, ui_state)

        # 注册保存钩子
        _connect_qdock_save_hooks(qdock, dock_widget)

        qdock.show()
        qdock.raise_()
        _DOCK_WIDGET = dock_widget
        _QDOCK_HOLDER = qdock

        # 标记一次"嵌入成功"
        try:
            dock_widget.save_ui_state(embedded_ok=True)
        except Exception:  # pylint: disable=broad-except
            pass
    else:
        # 独立窗口：当作普通顶层 widget
        dock_widget = MaxAgentDockWidget(config_manager=config)
        ui_state = dock_widget.get_ui_state()
        _restore_standalone_geometry(dock_widget, ui_state)
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
        show_panel(force=True)


def flush_state():
    """手动触发一次 UI 状态保存（供菜单/快捷键 / 退出钩子调用）。"""
    global _DOCK_WIDGET  # pylint: disable=global-statement
    if _DOCK_WIDGET is None:
        return
    try:
        # 优先调外层 QDockWidget 的 flush（包含完整 geometry）
        flusher = getattr(_DOCK_WIDGET, '_flush_qdock_state', None)
        if callable(flusher):
            flusher()
        else:
            _DOCK_WIDGET.save_ui_state()
    except Exception:  # pylint: disable=broad-except
        traceback.print_exc()


# ---------------------------------------------------------------------- #
# 自启动钩子（放在 Max startup 目录时会被自动执行）
# ---------------------------------------------------------------------- #
def _auto_register():
    """注册菜单 / 快捷键到 Max。失败不影响功能（用户手动调 show_panel 也行）。

    是否自动弹出面板由 ``show_panel`` 内部根据 ``AppConfig.auto_show_on_startup``
    门控，这里不再重复判断，避免两处行为不一致。
    """
    _ensure_package_path()

    try:
        # Max 2018+ 走 pymxs
        from pymxs import runtime as rt  # pylint: disable=import-error
        # 注册一个全局 MaxScript 函数供菜单调用
        rt.execute(
            'global g_show_max_agent\n'
            'fn g_show_max_agent = python.execute '
            '"import maxagent.startup as _s; _s.show_panel(force=True)"\n'
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

    # 同步注册热重载钩子（开发态便利，非 Max 环境会静默跳过）
    try:
        from maxagent.reload import register_maxscript_hook
        register_maxscript_hook()
    except Exception:  # pylint: disable=broad-except
        traceback.print_exc()

    # 非强制：让 show_panel 自己决定是否显示
    try:
        show_panel(force=False)
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
    show_panel(force=True)
    if created_app:
        sys.exit(app.exec_())
else:
    # 被 Max 自动加载时执行
    try:
        _auto_register()
    except Exception:  # pylint: disable=broad-except
        traceback.print_exc()
