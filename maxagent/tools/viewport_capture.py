#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""视口截图辅助函数与工具。

提供非交互式抓取 3ds Max 当前活动视口的能力，用于视觉感知自动触发。
所有截图逻辑必须在 Max 主线程执行（通过 run_on_main 或 tool 装饰器）。
"""

from __future__ import absolute_import
from __future__ import print_function

import os
import tempfile
import uuid
from typing import Optional

from ..attachments import save_image_bytes
from ..attachments import Attachment
from ..logger import get_logger
from ..runtime_helpers import IN_MAX
from ..runtime_helpers import run_on_main
from ..runtime_helpers import rt
from .registry import tool


logger = get_logger(__name__)


# 截图默认质量参数
_VIEWPORT_CAPTURE_QUALITY = 90


def _capture_viewport_dib_main():
    """在主线程内部执行：调用 gw.getViewportDib() 并落盘为 PNG。

    :returns: 图片二进制 bytes；失败返回 None
    """
    if not IN_MAX or rt is None:
        logger.warning('非 Max 环境，无法截取视口')
        return None
    try:
        # gw = rt.gw 是 Max 的 GraphicWindow 接口，getViewportDib 返回位图
        dib = rt.gw.getViewportDib()
        if dib is None:
            logger.warning('gw.getViewportDib() 返回空')
            return None
        tmp_path = os.path.join(
            tempfile.gettempdir(),
            'maxagent_vp_{}.png'.format(uuid.uuid4().hex),
        )
        # 通过 MaxScript 保存 PNG
        rt.save(dib, tmp_path)
        try:
            with open(tmp_path, 'rb') as fh:
                data = fh.read()
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        return data
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning('视口截图失败: %s', exc)
        return None


def capture_viewport_attachment(name='viewport.png'):
    """抓取当前活动视口并返回 Attachment。

    线程安全：内部通过 run_on_main 投递到 Max 主线程执行。

    :param name: 附件展示名
    :returns: Attachment 实例；失败返回 None
    """
    data = run_on_main(_capture_viewport_dib_main, _timeout=30.0)
    if not data:
        return None
    return save_image_bytes(data, mime='image/png', name=name)


@tool(
    description=(
        "截取 3ds Max 当前活动视口并作为图片附件返回。"
        "用于视觉复核、效果检查等场景。"
    ),
    category="scene_query",
    run_on_main_thread=True,
    wrap_undo=False,
)
def capture_viewport():
    """工具封装：把视口截图能力暴露给 LLM。

    :returns: dict {"ok": True, "attachment": {...}} 或错误信息
    """
    att = capture_viewport_attachment(name='viewport_capture.png')
    if att is None:
        return {
            "ok": False,
            "error": "视口截图失败，可能当前不在 3ds Max 环境或视口不可访问",
        }
    return {
        "ok": True,
        "attachment": att.to_json(),
        "note": "截图已保存，可作为 image_url 发送给支持视觉的模型分析",
    }


__all__ = [
    'capture_viewport_attachment',
    'capture_viewport',
]
