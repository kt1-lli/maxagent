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


def launch():
    # type: () -> None
    """启动 MaxAgent for Maya。"""
    _ensure_repo_on_path()
    from maxagent.ui.maya_startup import onMayaDroppedPythonFile  # pylint: disable=import-outside-toplevel
    onMayaDroppedPythonFile()


if __name__ == '__main__':
    launch()
