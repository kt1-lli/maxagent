#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""3ds Max 启动入口。

放在 Max 的 startup script 目录下：
- Windows: %LOCALAPPDATA%\\Autodesk\\3dsMax\\<ver>\\<lang>\\scripts\\startup\\
  （<lang> 为 zh-CN 或 ENU，由 Max 当前语言决定）
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


def _restore_main_window_state(main_win, ui_state):
    """如果有保存过 ``main_win.saveState()``，恢复整个 Max 主窗口的 dock 布局。

    Max 的主窗口除了 Qt 自带的 saveGeometry 之外，还需要 saveState 才能
    完整恢复"嵌入到第几列、什么宽度、相对其他 dockWidget 的顺序"。
    """
    state_b64 = (getattr(ui_state, 'main_state_b64', '') or '').strip()
    if not state_b64:
        return False
    try:
        from .qt_compat import QtCore
        ba_bytes = base64.b64decode(state_b64.encode('ascii'))
        ba = QtCore.QByteArray(ba_bytes)
        if hasattr(main_win, 'restoreState'):
            return bool(main_win.restoreState(ba))
    except Exception:  # pylint: disable=broad-except
        traceback.print_exc()
    return False


def _connect_qdock_save_hooks(qdock, dock_widget):
    """挂上保存钩子：浮动切换 / 关闭 / 区域变化时持久化 UI 状态。

    同时保存：
    - ``qdock.saveGeometry()`` —— QDockWidget 自身的位置/大小/浮动
    - ``main_win.saveState()`` —— Max 主窗口的 dock 布局（嵌入哪一列、
      多宽、与其他 dockWidget 的顺序）。少了这一份，重启后 Qt 不知道
      把 QDockWidget 放回哪里，会回退到默认右侧。

    本函数对保存做了三层防御，避免把"用户拖动中间过渡态"误存为最终态：

    1. **启动稳定期**：``_save_armed`` 在 1.2 秒后才置 True。期间 Qt
       仍在做初始 layout 适应（show/raise 都会发 visibilityChanged 等
       信号），这些都不是用户意图，必须屏蔽。
    2. **隐藏过滤**：``visibilityChanged(False)`` 时 ``saveGeometry``
       拿到的几何在多平台上不稳定（屏外坐标 / 0 大小），整个 hide
       事件直接跳过保存。
    3. **300ms 防抖**：拖动过程中 ``dockLocationChanged`` /
       ``topLevelChanged`` 会高频触发。统一用 ``QTimer.singleShot(300)``
       合并到一次最终保存，避免落盘的是 mouse-drag 中间帧。

    另外暴露 ``_flush_qdock_state``（同步立即保存，绕过防抖与稳定期）
    给关闭 / 用户主动 "记忆当前布局" 等强意图路径使用。
    """
    from .qt_compat import QtCore

    # 闭包状态：用 list 包一层是为了 Python2 风格（无 nonlocal）兼容
    # 同时让所有内部函数共享同一个标志位
    _state = {
        'armed': False,           # 启动稳定期过后才允许 debounced 保存
        'pending': False,         # 已有一次 singleShot 在排队
    }

    def _do_save_sync():
        """真正执行落盘的同步实现。失败时只打日志不抛。"""
        try:
            ba = qdock.saveGeometry()
            try:
                geo_bytes = bytes(ba)
            except TypeError:
                geo_bytes = ba.data() if hasattr(ba, 'data') else b''
            # 几何二进制 < 50 字节几乎必然是无效快照（隐藏 / 未 layout
            # 完成 / 屏外）。Qt 正常 saveGeometry 的最小尺寸约 60+ 字节。
            if len(geo_bytes) < 50:
                return
            geo_b64 = base64.b64encode(geo_bytes).decode('ascii')

            area = None
            main_state_b64 = ''
            try:
                main_win = qdock.parent()
                if main_win is not None and hasattr(main_win, 'dockWidgetArea'):
                    area = int(main_win.dockWidgetArea(qdock))
                # 主窗口完整 dock 布局
                if main_win is not None and hasattr(main_win, 'saveState'):
                    state_ba = main_win.saveState()
                    try:
                        st_bytes = bytes(state_ba)
                    except TypeError:
                        st_bytes = (
                            state_ba.data()
                            if hasattr(state_ba, 'data') else b''
                        )
                    # saveState 二进制太短同样视为不可用，不覆盖旧值
                    if len(st_bytes) >= 20:
                        main_state_b64 = base64.b64encode(
                            st_bytes,
                        ).decode('ascii')
            except Exception:  # pylint: disable=broad-except
                area = None

            dock_widget.save_ui_state(
                geometry_b64=geo_b64,
                floating=qdock.isFloating(),
                dock_area=area,
                embedded_ok=True,
                # 仅在拿到合法二进制时才传 main_state_b64，否则保持
                # ``save_ui_state`` 的"None 表示沿用旧值"语义，避免把
                # 不稳定瞬态覆盖掉用户上次确认的好布局
                main_state_b64=main_state_b64 or None,
            )
        except Exception:  # pylint: disable=broad-except
            traceback.print_exc()

    def _schedule_save():
        """防抖入口：armed 后才合并到 300ms 末尾的一次落盘。"""
        if not _state['armed']:
            return
        if _state['pending']:
            return
        _state['pending'] = True

        def _fire():
            _state['pending'] = False
            _do_save_sync()

        # 仅在有 QApplication 时才走 QTimer 防抖；否则同步执行
        # （测试场景 / 极端环境的兜底，避免悬空 timer 在 GC 时崩溃）
        try:
            from .qt_compat import QtWidgets as _QtWidgets
            _has_app = _QtWidgets.QApplication.instance() is not None
        except Exception:  # pylint: disable=broad-except
            _has_app = False
        if _has_app:
            try:
                QtCore.QTimer.singleShot(300, _fire)
                return
            except Exception:  # pylint: disable=broad-except
                pass
        # 退化到同步保存
        _state['pending'] = False
        _do_save_sync()

    # ----- 启动稳定期：1.2 秒后再开启自动保存 ----- #
    def _arm():
        _state['armed'] = True

    # 仅在已有 QApplication 实例时才用 QTimer.singleShot 排稳定期任务；
    # 否则（测试 / 早期初始化 / 极端环境）直接立刻 arm，避免悬空
    # timer 在 GC 时引发崩溃
    try:
        from .qt_compat import QtWidgets as _QtWidgets
        _has_app = _QtWidgets.QApplication.instance() is not None
    except Exception:  # pylint: disable=broad-except
        _has_app = False
    if _has_app:
        try:
            QtCore.QTimer.singleShot(1200, _arm)
        except Exception:  # pylint: disable=broad-except
            _state['armed'] = True
    else:
        # 没有事件循环就同步 arm，反正没有 GUI 信号会触发保存
        _state['armed'] = True

    # ----- 信号挂载（带过滤） ----- #
    # topLevelChanged：浮动 / 嵌入切换。用户行为，应保存
    try:
        qdock.topLevelChanged.connect(lambda _f: _schedule_save())
    except Exception:  # pylint: disable=broad-except
        pass
    # dockLocationChanged：用户拖到不同 dock area。应保存
    try:
        qdock.dockLocationChanged.connect(lambda _a: _schedule_save())
    except Exception:  # pylint: disable=broad-except
        pass
    # visibilityChanged：仅在显示态保存；隐藏时几何不可靠
    try:
        qdock.visibilityChanged.connect(
            lambda visible: _schedule_save() if visible else None,
        )
    except Exception:  # pylint: disable=broad-except
        pass

    # 把 hook 挂到 dock_widget 上：
    # - _flush_qdock_state: 同步强保存，绕过防抖与稳定期（关闭 / 显式
    #   "记忆当前布局" 用）
    # - _arm_qdock_save: 立即开启 armed（测试 / 强制场景用）
    dock_widget._flush_qdock_state = _do_save_sync  # noqa: SLF001
    dock_widget._arm_qdock_save = _arm  # noqa: SLF001


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
    from maxagent.logger import get_logger
    from maxagent.logger import setup_logging
    from maxagent.qt_compat import QtCore
    from maxagent.qt_compat import QtWidgets
    from maxagent.tools import load_all_tools
    from maxagent.ui.dock_widget import MaxAgentDockWidget
    from maxagent.ui.emoji_compat import install_app_font_fallback

    # 启动日志系统（幂等，重复调用安全）
    setup_logging()
    logger = get_logger(__name__)

    # 字体回退族安装到 QApplication 级别——必须在创建任何业务 QWidget
    # 之前调用，这样后续所有 QPushButton/QLabel 默认就会继承到回退族。
    # 在 PySide2 + Windows 上，这能消除"纯中文按钮在嵌入 Max 后糊掉"
    # 的问题。已存在的控件（含 Max 主窗口）不会回溯生效。
    try:
        install_app_font_fallback()
    except Exception:  # pylint: disable=broad-except
        # 字体设置失败不应阻塞启动
        logger.debug('install_app_font_fallback failed', exc_info=True)

    global _DOCK_WIDGET, _QDOCK_HOLDER  # pylint: disable=global-statement

    # 0. 配置门控：非 force 模式必须尊重 auto_show_on_startup
    #    这样无论调用方是 _auto_register、ms 启动器还是其他入口，
    #    只要不显式 force=True，关闭"自动显示"开关都能真正生效。
    if not force:
        try:
            cfg_mgr = ConfigManager()
            if not bool(cfg_mgr.config.auto_show_on_startup):
                logger.info(
                    'auto_show_on_startup=False，本次启动跳过自动显示。'
                    '可执行 g_show_max_agent() 手动显示。',
                )
                return None
        except Exception:  # pylint: disable=broad-except
            # 配置读不到不应阻塞显示，按默认开启处理
            pass

    # 1. 加载工具
    n = load_all_tools(include_escape_hatch=True)
    logger.info('已加载 %d 个工具', n)

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

        # ----------------------------------------------------------------- #
        # 嵌入 / 浮动决策
        #
        # 判定标准：完全以用户上次保存的 ``ui_state.floating`` 字段为准。
        #   - 配置文件不存在 / 字段缺省 → 默认 True（浮动）
        #   - 用户上次主动嵌入到侧栏 → 持久化时 floating=False，本次嵌入恢复
        #   - 用户上次是浮动状态 → 本次继续浮动
        #
        # 之前的实现用 ``last_embedded_ok`` 或 ``main_state_b64`` 作判据，
        # 但这两个字段在每次成功 show 后都会被无条件写入，无法区分用户
        # 真实意图，导致即便从未嵌入过也会被判定为"上次嵌入"。
        # ----------------------------------------------------------------- #
        # 浮动模式：默认 True；只有用户上次明确嵌入过才走嵌入分支
        want_floating = bool(getattr(ui_state, 'floating', True))
        # 配置中 floating 字段缺省时（首次运行），强制浮动
        if not (ui_state.geometry_b64 or '').strip():
            want_floating = True

        if want_floating:
            # 浮动呈现：先以 floating QDockWidget 的方式直接 show，
            # 不调用 addDockWidget。addDockWidget 即使紧跟 setFloating(True)
            # 也会让 Max 主窗口记住"这里有个 dock"，重启后被 Max 自己
            # 的状态机恢复成嵌入态。
            qdock.setFloating(True)
            # 应用上次保存的几何；没有则用合理默认值
            restored = _restore_qdock_geometry(qdock, ui_state)
            if not restored:
                # 首次运行 / 几何丢失：固定一个不会"巴掌大"的默认尺寸
                # 并居中到 Max 主窗口
                qdock.resize(440, 760)
                try:
                    mg = main_win.geometry()
                    cx = mg.x() + mg.width() // 2 - 220
                    cy = mg.y() + mg.height() // 2 - 380
                    qdock.move(max(cx, 50), max(cy, 50))
                except Exception:  # pylint: disable=broad-except
                    pass
        else:
            # 之前用户主动嵌入过：先 addDockWidget 再恢复 main_win 状态
            try:
                area = QtCore.Qt.DockWidgetArea(int(ui_state.dock_area or 2))
            except Exception:  # pylint: disable=broad-except
                area = QtCore.Qt.RightDockWidgetArea
            try:
                main_win.addDockWidget(area, qdock)
            except Exception:  # pylint: disable=broad-except
                qdock.setFloating(True)
            _restore_qdock_geometry(qdock, ui_state)
            _restore_main_window_state(main_win, ui_state)

        # 注册保存钩子
        _connect_qdock_save_hooks(qdock, dock_widget)

        qdock.show()
        qdock.raise_()
        _DOCK_WIDGET = dock_widget
        _QDOCK_HOLDER = qdock

        # ⚠ 不要在这里立刻 flush —— 启动期 Qt 还在做 layout 适应，
        # ``saveState() / saveGeometry()`` 拿到的是 dirty 中间帧。
        # 之前在此处 flush 是导致用户上次保存的好布局被启动瞬态覆盖的
        # 元凶之一（图1 → 图2 现象）。保存钩子内部会在 1.2 秒稳定期
        # 之后再响应信号，自然完成首次落盘，无需在此手动触发。
    else:
        # 独立窗口：当作普通顶层 widget
        dock_widget = MaxAgentDockWidget(config_manager=config)
        ui_state = dock_widget.get_ui_state()
        _restore_standalone_geometry(dock_widget, ui_state)
        dock_widget.show()
        _DOCK_WIDGET = dock_widget
        _QDOCK_HOLDER = None

    # 4. 按配置启动 IDE bridge（默认关闭；用户在设置面板手动开启）
    try:
        _maybe_start_bridge(config)
    except Exception:  # pylint: disable=broad-except
        # bridge 启动失败不应阻塞面板显示
        traceback.print_exc()

    return _DOCK_WIDGET


def _maybe_start_bridge(config_manager):
    """根据 AppConfig.bridge_enabled 决定是否启动桥接服务。"""
    cfg = config_manager.config
    if not bool(getattr(cfg, 'bridge_enabled', False)):
        return
    # 延迟拿 logger，避免循环 import
    try:
        from maxagent.logger import get_logger
        logger = get_logger('maxagent.startup.bridge')
    except Exception:  # pylint: disable=broad-except
        logger = None
    try:
        from maxagent.bridge import start_global_server
    except Exception as exc:  # pylint: disable=broad-except
        if logger is not None:
            logger.warning('import bridge module failed: %s', exc)
        return
    if logger is not None:
        logger.info(
            'auto-starting bridge from startup: %s:%d (dispatch=%s)',
            getattr(cfg, 'bridge_host', '127.0.0.1'),
            int(getattr(cfg, 'bridge_port', 7003) or 7003),
            'on' if getattr(cfg, 'bridge_dispatch_enabled', True) else 'off',
        )
    try:
        start_global_server(
            host=getattr(cfg, 'bridge_host', '127.0.0.1'),
            port=int(getattr(cfg, 'bridge_port', 7003) or 7003),
            token=str(getattr(cfg, 'bridge_token', '') or ''),
            config_manager=config_manager,
            dispatch_enabled=bool(
                getattr(cfg, 'bridge_dispatch_enabled', True),
            ),
            dispatch_max_rounds=int(
                getattr(cfg, 'bridge_dispatch_max_rounds', 20) or 20,
            ),
            dispatch_timeout_sec=int(
                getattr(cfg, 'bridge_dispatch_timeout_sec', 300) or 300,
            ),
        )
    except OSError as exc:
        # 端口冲突等：仅打日志，不弹框打断启动
        if logger is not None:
            logger.warning(
                'bridge auto-start failed (port busy?): %s', exc,
            )
        print('[MaxAgent] bridge 启动失败（端口冲突？）: {}'.format(exc))
    except Exception as exc:  # pylint: disable=broad-except
        if logger is not None:
            logger.exception('bridge auto-start failed: %s', exc)
        print('[MaxAgent] bridge 启动失败: {}'.format(exc))


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


def pin_current_layout():
    """显式记忆当前 dock 布局，绕过启动稳定期与防抖。

    适用于用户主动确认"我现在调好了，就以这个为准"的场景。
    内部行为：

    1. 立即 arm 保存（解除 1.2 秒稳定期屏蔽）
    2. 同步调用一次 ``_flush_qdock_state``，把当前 ``saveGeometry`` /
       ``saveState`` 立刻落盘
    3. 失败时只打日志不抛，不阻塞调用方

    :returns: ``True`` 表示完成保存调用；``False`` 表示当前没有可用的
              dock 实例
    """
    global _DOCK_WIDGET  # pylint: disable=global-statement
    if _DOCK_WIDGET is None:
        return False
    try:
        arm = getattr(_DOCK_WIDGET, '_arm_qdock_save', None)
        if callable(arm):
            arm()
        flusher = getattr(_DOCK_WIDGET, '_flush_qdock_state', None)
        if callable(flusher):
            flusher()
            return True
        # 退化到只保存 dock_widget 自身（splitter / 会话），没有外层
        # qdock 时即此分支
        _DOCK_WIDGET.save_ui_state()
        return True
    except Exception:  # pylint: disable=broad-except
        traceback.print_exc()
        return False


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
    # 测试 / CI 环境可设置 ``MAXAGENT_NO_AUTOSTART=1`` 跳过自动注册，
    # 避免 import startup 时直接创建 QWidget 导致测试进程崩溃
    if not os.environ.get('MAXAGENT_NO_AUTOSTART'):
        try:
            _auto_register()
        except Exception:  # pylint: disable=broad-except
            traceback.print_exc()
