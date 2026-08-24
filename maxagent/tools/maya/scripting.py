#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Maya 脚本执行工具。

支持在 Maya 进程中执行 MEL 或 Python 代码片段。
危险操作，默认 dangerous=True。
"""

from __future__ import absolute_import
from __future__ import print_function

from typing import Any
from typing import Dict

from ...dcc.runtime import current_dcc
from ...dcc.runtime import run_on_main
from ...tools.registry import tool


def _ensure_in_maya():
    # type: () -> None
    """确保当前运行在 Maya 环境，否则抛出 RuntimeError。"""
    if current_dcc() != 'maya':
        raise RuntimeError('非 Maya 环境')


@tool(
    dcc=['maya'],
    description='在 Maya 进程中执行 MEL 脚本片段。',
    category='scripting',
    dangerous=True,
    examples=[
        {'summary': '执行简单 MEL 命令', 'args': {'code': 'polyCube;'}},
    ],
    returns_desc='str: MEL 返回值',
)
def run_mel(code):
    # type: (str) -> str
    """执行 MEL 代码。

    :param code: MEL 脚本字符串
    """
    _ensure_in_maya()

    import maya.mel as mel  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    def _impl():
        return mel.eval(code)

    return run_on_main(_impl)


@tool(
    dcc=['maya'],
    description='在 Maya 进程中执行 Python 脚本片段。',
    category='scripting',
    dangerous=True,
    examples=[
        {
            'summary': '创建立方体',
            'args': {'code': 'import maya.cmds as cmds; cmds.polyCube(name="myCube")'},
        },
    ],
    returns_desc='Any: 脚本返回值',
)
def run_python(code):
    # type: (str) -> Any
    """执行 Python 代码。

    :param code: Python 脚本字符串
    """
    _ensure_in_maya()

    def _impl():
        # pylint: disable=exec-used
        namespace = {'__name__': '__maxagent_scripting__'}
        exec(code, namespace)  # nosec B102
        return namespace.get('result', None)

    return run_on_main(_impl)


@tool(
    dcc=['maya'],
    description='执行 Python 脚本文件（.py）。',
    category='scripting',
    dangerous=True,
    examples=[{'summary': '运行脚本文件', 'args': {'file_path': 'C:/scripts/setup.py'}}],
    returns_desc='dict: {"ok": True}',
)
def run_python_file(file_path):
    # type: (str) -> Dict[str, Any]
    """执行 Python 文件。

    :param file_path: 脚本路径
    """
    _ensure_in_maya()

    def _impl():
        with open(file_path, 'r', encoding='utf-8') as f:
            source = f.read()
        # pylint: disable=exec-used
        exec(source, {'__name__': '__maxagent_scriptfile__', '__file__': file_path})  # nosec B102
        return {'ok': True}

    return run_on_main(_impl)
