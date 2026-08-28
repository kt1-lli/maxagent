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


def _startup():
    # type: () -> None
    """导入并启动 MaxAgent UI。"""
    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
    from maxagent.dcc.runtime import current_dcc  # pylint: disable=import-outside-toplevel
    from maxagent.dcc.runtime import set_current_dcc  # pylint: disable=import-outside-toplevel
    from maxagent.tools import load_all_tools  # pylint: disable=import-outside-toplevel
    from maxagent.ui.dock_widget import get_or_create_dock  # pylint: disable=import-outside-toplevel

    # 强制确认 DCC 探测为 maya（拖拽启动时理论上一定在 Maya 内）
    if current_dcc() != 'maya':
        cmds.warning('current_dcc() 未识别为 maya，尝试强制设置为 maya')
    # 显式锁定 DCC 为 maya，避免后续模块从旧缓存或错误探测拿到 3dsmax
    set_current_dcc('maya')

    load_all_tools()
    get_or_create_dock()


# 如果直接 exec/script 执行本文件（非拖拽），也尝试启动
if __name__ == '__main__':
    onMayaDroppedPythonFile()
