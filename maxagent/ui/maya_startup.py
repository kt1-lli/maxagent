#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Maya 拖拽启动入口（onMayaDroppedPythonFile）。

用户把本文件拖入 Maya 视口即可触发安装/启动：
1. 把 maxagent 包路径加入 sys.path（支持开发目录或插件目录）。
2. 启动 MaxAgent 主 UI 并停靠到 Maya 主窗口侧边。

本文件故意保持无业务逻辑，只负责环境和启动，便于后续切换到插件分发模式。
"""

from __future__ import absolute_import
from __future__ import print_function

import os
import sys
from typing import Optional

from ..logger import get_logger
from ..logger import setup_logging


logger = get_logger(__name__)


# Maya 内置 dropped 回调要求函数签名：
#   def onMayaDroppedPythonFile(*args, **kwargs): ...
def onMayaDroppedPythonFile(*args, **kwargs):
    # type: (*Any, **Any) -> Optional[str]
    """被 Maya 拖拽回调调用。

    :returns: 可选提示字符串，Maya 会在脚本编辑器中显示
    """
    _ensure_maxagent_on_path()
    _startup()
    return 'MaxAgent for Maya 已启动'


def g_show_max_agent():
    # type: () -> object
    """手动显示 MaxAgent 面板（Maya 侧，绕过 auto_show 门控）。

    与 3ds Max 侧 MAXScript 同名函数对齐：用户关闭"启动时自动显示"
    后，可在 Maya 脚本编辑器里执行本函数手动唤出面板。
    """
    from maxagent.ui.dock_widget import get_or_create_dock
    return get_or_create_dock(force=True)


def _ensure_maxagent_on_path():
    # type: () -> None
    """确保 maxagent 包能被 import。

    策略：
    - 优先用本文件所在目录的父目录（开发目录结构：ui/maya_startup.py）。
    - 若找不到，回退到 Maya 插件目录下的 maxagent/runtime/maxagent。
    """
    this_file = os.path.abspath(__file__)
    # 开发目录：.../maxagent/ui/maya_startup.py -> 添加 .../
    dev_root = os.path.dirname(os.path.dirname(this_file))
    if os.path.isdir(os.path.join(dev_root, 'maxagent')):
        if dev_root not in sys.path:
            sys.path.insert(0, dev_root)
        return

    # 插件目录：.../MaxAgent/plug-ins/runtime/maxagent
    plugin_root = os.path.dirname(os.path.dirname(os.path.dirname(this_file)))
    runtime_pkg = os.path.join(plugin_root, 'runtime')
    if os.path.isdir(runtime_pkg):
        if runtime_pkg not in sys.path:
            sys.path.insert(0, runtime_pkg)
        return


def restore_workspace_control():
    # type: () -> Optional[str]
    """workspaceControl 的 ``uiScript`` 回调入口。

    当用户切换 Maya workspace / layout，或从折叠状态重新展开面板时，
    Maya 会执行创建 control 时登记的 ``uiScript``。若此时面板内容还
    没建起来（进程刚启动就被 layout 恢复），在这里补建一次。

    已经存在且内容正常时直接返回，避免重复创建。
    """
    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
    from maxagent.dcc.runtime import (  # pylint: disable=import-outside-toplevel
        ensure_current_dcc,
    )
    from maxagent.ui import dock_widget as _dw_mod  # pylint: disable=import-outside-toplevel

    control_name = 'MaxAgentWorkspaceControl'

    # 正在创建的调用栈里（loadImmediately=True 会同步回调 uiScript）：
    # 此刻 _DOCK_WIDGET 还没赋值，若据此判定"内容缺失"就会再建一个业务
    # widget 挂到同一个 control 上——面板里多出一块空白界面。创建由外层
    # 调用栈负责完成，这里直接返回。
    try:
        if _dw_mod._MAYA_DOCK_CREATING:  # noqa: SLF001
            return 'creating-in-progress'
    except Exception:  # pylint: disable=broad-except
        logger.debug('读取创建中标志失败', exc_info=True)

    # 读模块属性而不是 from-import：后者会把当前值拷到局部名，语义上
    # 容易误判成"快照"。虽然函数内的 from-import 每次调用都会重新绑定，
    # 但显式取属性让"读的是最新值"这件事一目了然。
    need_rebuild = _dw_mod._DOCK_WIDGET is None  # noqa: SLF001

    # control 已存在且内容还在：只补一次显示，绝不重建。
    # uiScript 会在每次 workspace 切换 / layout 恢复 / 面板重新展开时
    # 被 Maya 回调；此前这里一进来就 get_or_create_dock()，导致每次
    # 切 workspace 都重跑一遍 load_all_tools + 完整 UI 构建。
    if not need_rebuild:
        try:
            if cmds.workspaceControl(control_name, query=True, exists=True):
                cmds.evalDeferred(
                    lambda *a: _dw_mod._maya_edit_if_exists(  # noqa: SLF001
                        cmds, control_name, restore=True,
                    )
                )
                return 'already-restored'
        except Exception:  # pylint: disable=broad-except
            logger.debug('uiScript 复用分支查询失败', exc_info=True)

    # 走到这里说明内容确实没了（进程刚启动就被 layout 恢复，或面板被
    # 销毁过）。此时 control 可能还在，要复用它而不是再建一个。
    if not cmds.workspaceControl(control_name, exists=True):
        return 'control-missing'
    # 切到 Maya 主线程之外先锁定 DCC，避免探测漂移
    ensure_current_dcc('maya')
    # workspace 恢复可能先于 _startup 执行（如 Maya 启动时自动还原布局），
    # 必须在这里也初始化日志：未初始化时 maxagent.* logger propagate=True，
    # 日志会冒泡到 Maya root handler 刷屏 Script Editor
    setup_logging()
    _dw_mod.get_or_create_dock()
    return 'restored'


def _startup():
    # type: () -> None
    """导入并启动 MaxAgent UI。"""
    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
    from maxagent.config import ConfigManager  # pylint: disable=import-outside-toplevel
    from maxagent.dcc.runtime import current_dcc  # pylint: disable=import-outside-toplevel
    from maxagent.dcc.runtime import ensure_current_dcc  # pylint: disable=import-outside-toplevel
    from maxagent.tools import load_all_tools  # pylint: disable=import-outside-toplevel
    from maxagent.ui.dock_widget import get_or_create_dock  # pylint: disable=import-outside-toplevel

    # 强制确认 DCC 探测为 maya（拖拽启动时理论上一定在 Maya 内）
    if current_dcc() != 'maya':
        cmds.warning('current_dcc() 未识别为 maya，尝试强制设置为 maya')
    # 显式锁定 DCC 为 maya，避免后续模块从旧缓存或错误探测拿到 3dsmax
    ensure_current_dcc('maya')
    # 初始化日志（幂等）：只写文件不写控制台，避免 Maya Script Editor 刷屏
    setup_logging()

    load_all_tools()
    # 与 3ds Max 侧 show_panel 的门控对齐：auto_show_on_startup=False
    # 时本次不自动弹出面板，用户可通过 g_show_max_agent() 手动显示。
    # get_or_create_dock 内部已有同样的门控逻辑，force=False 即可复用。
    get_or_create_dock()
    # 按配置启动 IDE bridge（默认关闭；用户在设置面板手动开启）。
    # 与面板显示解耦：面板被门控跳过时 bridge 也应照常可用。
    try:
        _maybe_start_bridge(ConfigManager())
    except Exception:  # pylint: disable=broad-except
        import traceback  # pylint: disable=import-outside-toplevel
        traceback.print_exc()


def _maybe_start_bridge(config_manager):
    # type: (ConfigManager) -> None
    """根据 AppConfig.bridge_enabled 决定是否启动桥接服务。

    与 maxagent/startup.py 中同名函数行为对齐：读同一组 bridge_* 配置，
    启停同一全局实例；失败仅打印日志，不阻塞启动。
    """
    cfg = config_manager.config
    if not bool(getattr(cfg, 'bridge_enabled', False)):
        return
    try:
        from maxagent.logger import get_logger
        logger = get_logger('maxagent.ui.maya_startup.bridge')
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
            'auto-starting bridge from maya startup: %s:%d (dispatch=%s)',
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


# 如果直接 exec/script 执行本文件（非拖拽），也尝试启动
if __name__ == '__main__':
    onMayaDroppedPythonFile()