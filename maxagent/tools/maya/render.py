#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Maya 渲染类工具。

支持：渲染当前帧、配置渲染分辨率/格式、批量渲染序列帧。
渲染会阻塞 Maya 主线程，调用方应调大 timeout。

实现说明：
- 渲染走 cmds.render(camera, x=, y=)，渲染器由场景 Render Settings 决定
  （Arnold 场景即用 Arnold 渲染），输出路径通过 defaultRenderGlobals 配置。
- imageFormat 枚举：png=32, jpg=8, exr=51（51 是插件格式大类，需同时设置
  imfPluginKey / imfkey 指定具体扩展名）。
"""

from __future__ import absolute_import
from __future__ import print_function

import os
from typing import Any
from typing import Dict
from typing import Optional

from ...dcc.runtime import run_on_main
from ._common import _ensure_in_maya
from ...tools.registry import tool


# 扩展名 -> defaultRenderGlobals.imageFormat 枚举值
_IMAGE_FORMAT_MAP = {
    'png': 32,
    'jpg': 8,
    'jpeg': 8,
    'exr': 51,
    'iff': 7,
    'tif': 3,
    'tiff': 3,
    'tga': 19,
    'bmp': 20,
}


def _configure_render_globals(output_dir, prefix, file_ext):
    # type: (str, str, str) -> Dict[str, Any]
    """配置 defaultRenderGlobals 的输出目录/前缀/格式。

    :returns: dict {"dir": ..., "prefix": ..., "ext": ...}
    """
    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    fmt_code = _IMAGE_FORMAT_MAP.get(file_ext)
    if fmt_code is None:
        raise ValueError(
            '不支持的图片格式: {}，可选: {}'.format(
                file_ext, ', '.join(sorted(_IMAGE_FORMAT_MAP)),
            ),
        )
    cmds.setAttr('defaultRenderGlobals.imageFormat', fmt_code)
    if fmt_code == 51:
        # exr 属于插件格式大类，需要同时指定具体扩展名
        cmds.setAttr('defaultRenderGlobals.imfPluginKey', 'exr', type='string')
        cmds.setAttr('defaultRenderGlobals.imfkey', 'exr', type='string')
    # 输出目录写入 imageFilePrefix（目录不存在会自动创建）
    cmds.setAttr(
        'defaultRenderGlobals.imageFilePrefix',
        output_dir.rstrip('/\\') + '/' + prefix,
        type='string',
    )
    return {'dir': output_dir, 'prefix': prefix, 'ext': file_ext}


def _resolve_render_camera(camera):
    # type: (Optional[str]) -> Optional[str]
    """解析渲染相机名，支持传 transform 或 shape 名。"""
    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    if not camera:
        return None
    if not cmds.objExists(camera):
        raise ValueError('相机不存在: {}'.format(camera))
    # render 命令要求传 shape；传 transform 时自动换算
    if cmds.objectType(camera) == 'transform':
        shapes = cmds.listRelatives(camera, shapes=True, type='camera') or []
        if not shapes:
            raise ValueError('指定节点不是相机: {}'.format(camera))
        return shapes[0]
    return camera


@tool(
    dcc=['maya'],
    description=(
        '渲染 Maya 当前帧为图像文件。'
        '注意：渲染过程会阻塞 Maya 主线程，可能耗时数秒到数分钟。'
    ),
    category='render',
    dangerous=True,
    wrap_undo=False,
    examples=[
        {
            'summary': '用当前 Render Settings 渲染当前帧为 PNG',
            'args': {'output_path': 'C:/Work/render.png'},
        },
        {
            'summary': '指定相机与分辨率渲染',
            'args': {
                'output_path': 'C:/Work/render.png',
                'width': 1920, 'height': 1080,
                'camera': 'shotCam', 'frame': 30,
            },
        },
    ],
    notes=[
        'output_path 的扩展名决定输出格式（png/jpg/exr/tif/tga/bmp/iff）。',
        'camera 支持传相机 transform 名或 shape 名；不传时用场景激活相机。',
        'frame 用于渲染指定帧，不传则渲染当前帧。',
        '渲染器取自场景 Render Settings（如 Arnold）；分辨率仅在显式传入时覆盖。',
    ],
    returns_desc=(
        'dict {"output": 实际输出文件路径, "width": int, "height": int, "ok": True}；'
        '实际文件名可能带帧号填充（由 Render Settings 的 Frame/Animation ext 决定），'
        '以 render 命令返回值为准。'
    ),
)
def render_maya_frame(
    output_path,
    width=None,
    height=None,
    frame=None,
    camera=None,
):
    # type: (str, Optional[int], Optional[int], Optional[float], Optional[str]) -> Dict[str, Any]
    """渲染当前帧/指定帧。

    :param output_path: 输出文件绝对路径（.png / .jpg / .exr / .tif 等）
    :param width: 渲染宽度（像素）；None 用场景 Render Settings 的设置
    :param height: 渲染高度；None 用场景设置
    :param frame: 渲染帧号；None 表示当前帧
    :param camera: 渲染相机名（transform 或 shape）；None 用激活相机
    :returns: dict {"output": ..., "width": ..., "height": ..., "ok": True}
    """
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    def _impl():
        path = os.path.normpath(output_path)
        out_dir = os.path.dirname(path)
        if out_dir and not os.path.isdir(out_dir):
            os.makedirs(out_dir)
        basename = os.path.basename(path)
        stem, ext = os.path.splitext(basename)
        file_ext = ext.lstrip('.').lower()

        # 配置输出目录/前缀/格式（目录 + 前缀写入 imageFilePrefix）
        _configure_render_globals(out_dir or '.', stem, file_ext)

        # 可选：覆盖分辨率
        if width:
            cmds.setAttr('defaultResolution.width', int(width))
        if height:
            cmds.setAttr('defaultResolution.height', int(height))

        # 可选：跳到指定帧（渲染完不回跳，与 set_keyframe 行为一致）
        if frame is not None:
            cmds.currentTime(float(frame))

        # 解析相机（render 需要 shape 名）
        cam_shape = _resolve_render_camera(camera)

        # 执行渲染
        kwargs: Dict[str, Any] = {}
        if width:
            kwargs['x'] = int(width)
        if height:
            kwargs['y'] = int(height)
        if cam_shape:
            result = cmds.render(cam_shape, **kwargs)
        else:
            result = cmds.render(**kwargs)

        # render 返回实际渲染的图像路径（str 或 list）
        if isinstance(result, (list, tuple)):
            rendered = result[0] if result else ''
        else:
            rendered = result or ''
        return {
            'output': rendered,
            'width': int(width) if width else int(
                cmds.getAttr('defaultResolution.width'),
            ),
            'height': int(height) if height else int(
                cmds.getAttr('defaultResolution.height'),
            ),
            'ok': True,
        }

    return run_on_main(_impl)


@tool(
    dcc=['maya'],
    description='渲染一段帧序列到指定目录（自动按 ####.ext 格式编号）。',
    category='render',
    dangerous=True,
    wrap_undo=False,
    examples=[
        {
            'summary': '渲染 1-48 帧 PNG 序列',
            'args': {
                'output_dir': 'C:/Work/frames', 'file_basename': 'frame',
                'file_ext': 'png', 'start_frame': 1, 'end_frame': 48,
                'width': 1920, 'height': 1080, 'camera': 'shotCam',
            },
        },
    ],
    notes=[
        '逐帧调用 cmds.render，最终文件名为 {basename}_{帧号4位}.{ext}。',
        'start_frame 大于 end_frame 时报错。',
        '渲染器取自场景 Render Settings；渲染过程中请勿操作 Maya 界面。',
    ],
    returns_desc=(
        'dict {"frames": 成功帧数, "output_dir": ..., "start_frame": ..., '
        '"end_frame": ..., "ok": True}'
    ),
)
def render_maya_animation(
    output_dir,
    file_basename='frame',
    file_ext='png',
    start_frame=1,
    end_frame=10,
    width=None,
    height=None,
    camera=None,
):
    # type: (str, str, str, float, float, Optional[int], Optional[int], Optional[str]) -> Dict[str, Any]
    """批量渲染序列帧。

    :param output_dir: 输出目录（不存在会自动创建）
    :param file_basename: 文件名前缀（最终生成 frame_0001.png 等）
    :param file_ext: 扩展名（不含点）
    :param start_frame: 起始帧
    :param end_frame: 结束帧（包含）
    :param width: 渲染宽度；None 用场景设置
    :param height: 渲染高度；None 用场景设置
    :param camera: 相机名
    :returns: dict {"frames": N, "output_dir": ..., "ok": True}
    """
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    def _impl():
        if not os.path.isdir(output_dir):
            os.makedirs(output_dir)
        if start_frame > end_frame:
            raise ValueError(
                'start_frame({}) > end_frame({})'.format(start_frame, end_frame),
            )
        file_ext = file_ext.lower()
        _configure_render_globals(output_dir, file_basename, file_ext)

        if width:
            cmds.setAttr('defaultResolution.width', int(width))
        if height:
            cmds.setAttr('defaultResolution.height', int(height))

        cam_shape = _resolve_render_camera(camera)

        rendered = 0
        last_output = ''
        frame_val = float(start_frame)
        while frame_val <= float(end_frame):
            cmds.currentTime(frame_val)
            kwargs: Dict[str, Any] = {}
            if width:
                kwargs['x'] = int(width)
            if height:
                kwargs['y'] = int(height)
            if cam_shape:
                result = cmds.render(cam_shape, **kwargs)
            else:
                result = cmds.render(**kwargs)
            if isinstance(result, (list, tuple)):
                last_output = result[0] if result else ''
            else:
                last_output = result or ''
            rendered += 1
            frame_val += 1.0

        return {
            'frames': rendered,
            'output_dir': output_dir,
            'start_frame': float(start_frame),
            'end_frame': float(end_frame),
            'last_output': last_output,
            'ok': True,
        }

    return run_on_main(_impl)


@tool(
    dcc=['maya'],
    description='设置 Maya 渲染分辨率与像素比例。不会立即渲染，只改 Render Settings。',
    category='render',
    wrap_undo=False,
    examples=[
        {
            'summary': '设置 1920x1080',
            'args': {'width': 1920, 'height': 1080},
        },
    ],
    notes=[
        '修改的是 defaultResolution 节点（Render Settings > Common > Image Size）。',
        '渲染器切换请用 run_mel 或 run_python 操作 Render Settings。',
    ],
    returns_desc='dict {"width": int, "height": int, "pixel_aspect": float}',
)
def set_maya_render_resolution(width=1920, height=1080, pixel_aspect=1.0):
    # type: (int, int, float) -> Dict[str, Any]
    """配置渲染分辨率。

    :param width: 宽（像素）
    :param height: 高（像素）
    :param pixel_aspect: 像素长宽比（一般 1.0）
    :returns: dict {"width": ..., "height": ..., "pixel_aspect": ...}
    """
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    def _impl():
        cmds.setAttr('defaultResolution.width', int(width))
        cmds.setAttr('defaultResolution.height', int(height))
        try:
            cmds.setAttr('defaultResolution.pixelAspect', float(pixel_aspect))
        except Exception:  # pylint: disable=broad-except
            pass
        return {
            'width': int(cmds.getAttr('defaultResolution.width')),
            'height': int(cmds.getAttr('defaultResolution.height')),
            'pixel_aspect': float(cmds.getAttr('defaultResolution.pixelAspect')),
        }

    return run_on_main(_impl)


__all__ = [
    'render_maya_frame',
    'render_maya_animation',
    'set_maya_render_resolution',
]
