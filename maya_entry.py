#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Maya 插件/脚本入口包装。

本文件放在仓库根目录，方便用户通过拖拽或 source 命令直接执行：
    import maya_entry
    maya_entry.launch()

实际逻辑转发到 maxagent.ui.maya_startup，保持入口薄且可替换。
"""

from __future__ import absolute_import
from __future__ import print_function

import os
import sys


def _ensure_repo_on_path():
    # type: () -> None
    """把本文件所在目录加入 sys.path。"""
    this_file = os.path.abspath(__file__)
    repo_root = os.path.dirname(this_file)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)


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


def onMayaDroppedPythonFile(*args, **kwargs):
    # type: (*object, **object) -> str
    """Maya 拖拽回调要求函数。

    Maya 拖拽执行 Python 文件时，会查找并调用本函数。
    :returns: 提示字符串，Maya 会在脚本编辑器中显示
    """
    _ensure_repo_on_path()
    _startup()
    return 'MaxAgent for Maya 已启动'


def launch():
    # type: () -> None
    """启动 MaxAgent for Maya。"""
    _ensure_repo_on_path()
    onMayaDroppedPythonFile()


if __name__ == '__main__':
    launch()
