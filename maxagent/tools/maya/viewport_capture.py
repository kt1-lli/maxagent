#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Maya 视口截图工具。

把当前 Maya 视口渲染成图片并保存到指定路径。
"""

from __future__ import absolute_import
from __future__ import print_function

import os
from typing import Any
from typing import Dict
from typing import Optional

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
    description='截取当前 Maya 视口并保存为图片。',
    category='viewport_capture',
    examples=[
        {'summary': '截图并保存', 'args': {'output_path': 'C:/Temp/viewport.png', 'width': 1280, 'height': 720}},
    ],
    returns_desc='dict: {"ok": True, "file_path": str}',
)
def capture_viewport(output_path, width=1280, height=720, camera=None):
    # type: (str, int, int, Optional[str]) -> Dict[str, Any]
    """截取当前视口。

    :param output_path: 保存路径，支持 .png/.jpg/.tif
    :param width: 宽度
    :param height: 高度
    :param camera: 指定相机名，None 使用当前视口相机
    """
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    def _impl():
        path = os.path.normpath(output_path)
        directory = os.path.dirname(path)
        if directory and not os.path.exists(directory):
            os.makedirs(directory)

        fmt = 'png'
        ext = os.path.splitext(path)[1].lower()
        if ext in ('.jpg', '.jpeg'):
            fmt = 'jpg'
        elif ext == '.tif':
            fmt = 'tif'
        elif ext == '.iff':
            fmt = 'iff'

        if camera:
            cmds.lookThru(camera)

        cmds.setAttr('defaultResolution.width', width)
        cmds.setAttr('defaultResolution.height', height)

        cmds.playblast(
            frame=cmds.currentTime(query=True),
            format='image',
            filename=path,
            percent=100,
            quality=100,
            widthHeight=(width, height),
            showOrnaments=False,
            forceOverwrite=True,
            completeFilename=path,
        )
        return {'ok': True, 'file_path': path}

    return run_on_main(_impl)
