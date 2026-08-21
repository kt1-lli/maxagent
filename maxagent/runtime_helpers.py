#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pymxs / MaxScript 运行时辅助（兼容 shim）。

本模块已整体迁移到 ``maxagent.dcc`` 包。保留此处仅为了兼容旧 import，
所有符号都直接转发到新的 DCC 适配层。
"""

from __future__ import absolute_import
from __future__ import print_function

# pylint: disable=unused-import
from .dcc.max_adapter import escape_maxscript_string
from .dcc.max_adapter import get_max_version
from .dcc.max_adapter import has_runtime_attr
from .dcc.max_adapter import MaxUndoBlock as undo_block
from .dcc.runtime import current_dcc
from .dcc.runtime import run_on_main

# 为了向后兼容，保留 IN_MAX 和 rt 变量
# 注意：这些变量在 Max 之外为 None/False
try:
    import pymxs  # type: ignore  # pylint: disable=import-error
    rt = pymxs.runtime  # pylint: disable=invalid-name
    IN_MAX = True
except ImportError:
    pymxs = None  # type: ignore
    rt = None  # type: ignore  # pylint: disable=invalid-name
    IN_MAX = False


def execute_maxscript(code):
    """兼容旧 API：通过 MaxAdapter 执行 MaxScript 代码。"""
    from .dcc.runtime import get_adapter
    return get_adapter().execute_script(code, language='maxscript')


__all__ = [
    'IN_MAX',
    'rt',
    'current_dcc',
    'run_on_main',
    'undo_block',
    'get_max_version',
    'has_runtime_attr',
    'escape_maxscript_string',
    'execute_maxscript',
]
