#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""渲染类工具。

支持：渲染当前帧、配置渲染参数、批量渲染序列帧。
渲染会阻塞 Max 主线程，所以这些工具的 timeout 应当由调用方调大。
"""

from __future__ import absolute_import
from __future__ import print_function

import os
from typing import Optional

from ..runtime_helpers import IN_MAX
from ..runtime_helpers import rt
from .registry import tool


def _ensure_in_max():
    if not IN_MAX:
        raise RuntimeError('非 3ds Max 环境')


@tool(
    description=(
        '渲染当前活动视口为图像文件。'
        '注意：渲染过程会阻塞 Max 主线程，可能耗时数秒到数分钟。'
    ),
    category='render',
    dangerous=True,  # 长耗时 + 写文件,
    examples=[{"summary": "典型调用", "args": {"output_path": 'C:/Work/render.png', "width": 1920, "height": 1080, "frame": 30, "camera": 'Camera01'}}],
notes=['参数必须严格符合 JSON Schema 声明的类型。', '调用失败时应先检查对象/文件是否存在。'],
returns_desc="dict {\"ok\": True, ...}"
)
def render_current_frame(
    output_path,
    width=1920,
    height=1080,
    frame=None,
    camera=None,
):
    """渲染当前帧。

    :param output_path: 输出文件绝对路径（.png / .jpg / .exr / .tif 等）
    :param width: 渲染宽度（像素）
    :param height: 渲染高度
    :param frame: 渲染帧号（None 表示当前帧）
    :param camera: 渲染相机名（None 表示活动视口）
    :returns: dict {"output": ..., "width": ..., "height": ..., "ok": True}
    """
    _ensure_in_max()
    out_dir = os.path.dirname(output_path)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir)

    cam_node = None
    if camera:
        cam_node = rt.getNodeByName(camera, exact=True, all=False)
        if cam_node is None:
            raise ValueError('相机不存在: {}'.format(camera))

    kwargs = {
        'outputwidth': int(width),
        'outputheight': int(height),
        'outputfile': output_path,
        'vfb': False,
    }
    if frame is not None:
        kwargs['frame'] = int(frame)
    if cam_node is not None:
        kwargs['camera'] = cam_node

    rt.render(**kwargs)
    return {
        'output': output_path,
        'width': int(width),
        'height': int(height),
        'ok': True,
    }


@tool(
    description='渲染一段帧序列到指定目录（自动按 ####.ext 格式编号）。',
    category='render',
    dangerous=True,
    examples=[{"summary": "典型调用", "args": {"output_dir": 'C:/Work/frames', "file_basename": 'frame', "file_ext": 'png', "start_frame": 0, "end_frame": 100, "width": 1920, "height": 1080, "camera": 'Camera01'}}],
notes=['参数必须严格符合 JSON Schema 声明的类型。', '调用失败时应先检查对象/文件是否存在。'],
returns_desc="dict {\"ok\": True, ...}"
)
def render_animation(
    output_dir,
    file_basename='frame',
    file_ext='png',
    start_frame=0,
    end_frame=10,
    width=1920,
    height=1080,
    camera=None,
):
    """批量渲染序列帧。

    :param output_dir: 输出目录（不存在会自动创建）
    :param file_basename: 文件名前缀（最终生成 frame_0000.png 等）
    :param file_ext: 扩展名（不含点）
    :param start_frame: 起始帧
    :param end_frame: 结束帧（包含）
    :param width: 渲染宽度
    :param height: 渲染高度
    :param camera: 相机名
    :returns: dict {"frames": N, "output_dir": ..., "ok": True}
    """
    _ensure_in_max()
    if not os.path.isdir(output_dir):
        os.makedirs(output_dir)
    if start_frame > end_frame:
        raise ValueError(
            'start_frame({}) > end_frame({})'.format(start_frame, end_frame),
        )

    cam_node = None
    if camera:
        cam_node = rt.getNodeByName(camera, exact=True, all=False)
        if cam_node is None:
            raise ValueError('相机不存在: {}'.format(camera))

    rendered = 0
    for f in range(int(start_frame), int(end_frame) + 1):
        fname = '{}_{:04d}.{}'.format(file_basename, f, file_ext)
        out_path = os.path.join(output_dir, fname)
        kwargs = {
            'outputwidth': int(width),
            'outputheight': int(height),
            'outputfile': out_path,
            'frame': f,
            'vfb': False,
        }
        if cam_node is not None:
            kwargs['camera'] = cam_node
        rt.render(**kwargs)
        rendered += 1

    return {
        'frames': rendered,
        'output_dir': output_dir,
        'start_frame': int(start_frame),
        'end_frame': int(end_frame),
        'ok': True,
    }


@tool(
    description='设置渲染输出分辨率与图像比例。不会立即渲染，只是配置 RenderSettings。',
    category='render',
    examples=[{"summary": "典型调用", "args": {"width": 1920, "height": 1080, "pixel_aspect": 1.0}}],
notes=['参数必须严格符合 JSON Schema 声明的类型。', '调用失败时应先检查对象/文件是否存在。'],
returns_desc="dict {\"ok\": True, ...}"
)
def set_render_resolution(width=1920, height=1080, pixel_aspect=1.0):
    """配置渲染分辨率。

    :param width: 宽（像素）
    :param height: 高（像素）
    :param pixel_aspect: 像素长宽比（一般 1.0）
    :returns: dict {"width": ..., "height": ..., "pixel_aspect": ...}
    """
    _ensure_in_max()
    rt.renderWidth = int(width)
    rt.renderHeight = int(height)
    try:
        rt.renderPixelAspect = float(pixel_aspect)
    except Exception:  # pylint: disable=broad-except
        pass
    return {
        'width': int(rt.renderWidth),
        'height': int(rt.renderHeight),
        'pixel_aspect': float(rt.renderPixelAspect),
    }