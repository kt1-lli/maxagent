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
                    lambda *a: cmds.workspaceControl(
                        control_name, edit=True, restore=True,
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
    _dw_mod.get_or_create_dock()
    return 'restored'


def _startup():
    # type: () -> None
    """导入并启动 MaxAgent UI。"""
    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
    from maxagent.dcc.runtime import current_dcc  # pylint: disable=import-outside-toplevel
    from maxagent.dcc.runtime import ensure_current_dcc  # pylint: disable=import-outside-toplevel
    from maxagent.tools import load_all_tools  # pylint: disable=import-outside-toplevel
    from maxagent.ui.dock_widget import get_or_create_dock  # pylint: disable=import-outside-toplevel

    # 强制确认 DCC 探测为 maya（拖拽启动时理论上一定在 Maya 内）
    if current_dcc() != 'maya':
        cmds.warning('current_dcc() 未识别为 maya，尝试强制设置为 maya')
    # 显式锁定 DCC 为 maya，避免后续模块从旧缓存或错误探测拿到 3dsmax
    ensure_current_dcc('maya')

    load_all_tools()
    get_or_create_dock()


# 如果直接 exec/script 执行本文件（非拖拽），也尝试启动
if __name__ == '__main__':
    onMayaDroppedPythonFile()
