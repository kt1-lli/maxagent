#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Maya 动画类工具：关键帧、时间控制、烘焙。

面向动画/技术美术，补齐 MayaAgent 在动画管线上的能力缺口。
所有会修改场景的操作都默认包在 undo 块内。
"""

from __future__ import absolute_import
from __future__ import print_function

import json
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ...dcc.runtime import current_dcc
from ...dcc.runtime import run_on_main
from ...tools.registry import tool


def _ensure_in_maya():
    # type: () -> None
    """确保当前运行在 Maya 环境，否则抛出 RuntimeError。"""
    if current_dcc() != 'maya':
        raise RuntimeError('非 Maya 环境')


def _to_float_list(value, name='value'):
    # type: (Any, str) -> Optional[List[float]]
    """把 JSON 字符串或列表转为 float 列表。"""
    if value is None:
        return None
    coords = value
    if isinstance(coords, str):
        try:
            coords = json.loads(coords)
        except json.JSONDecodeError as exc:
            raise ValueError(
                '{} 字符串不是合法 JSON: {} ({})'.format(name, value, exc),
            ) from exc
    try:
        return [float(v) for v in coords]
    except (TypeError, ValueError) as exc:
        raise ValueError(
            '{} 参数解析失败: {} ({})'.format(name, value, exc),
        ) from exc


def _normalize_names(names):
    # type: (Any) -> List[str]
    """把 names 归一化为 list[str]。"""
    if names is None:
        return []
    if isinstance(names, (list, tuple)):
        return [str(x).strip() for x in names if str(x).strip()]
    if isinstance(names, str):
        s = names.strip()
        if not s:
            return []
        for sep in (',', ';', '\uff0c', '\uff1b'):
            if sep in s:
                return [p.strip() for p in s.split(sep) if p.strip()]
        return [s]
    return [str(names)]


@tool(
    dcc=['maya'],
    description='给对象在指定帧设置关键帧。',
    category='animation',
    examples=[
        {
            'summary': '在 30 帧记录 pCube1 的 translate 关键帧',
            'args': {'name': 'pCube1', 'frame': 30, 'attribute': 'translate'},
        },
    ],
    returns_desc='dict: {"ok": True}',
)
def set_keyframe(name, frame, attribute='translate'):
    # type: (str, float, str) -> Dict[str, Any]
    """在指定帧给对象属性设置关键帧。

    :param name: 对象名
    :param frame: 帧数
    :param attribute: 属性名，如 translate / rotate / scale / translateX
    """
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    def _impl():
        if not cmds.objExists(name):
            raise ValueError('对象不存在: {}'.format(name))
        cmds.currentTime(frame)
        cmds.setKeyframe(name, attribute=attribute)
        return {'ok': True}

    return run_on_main(_impl)


@tool(
    dcc=['maya'],
    description='获取对象某属性在指定帧的关键帧值。',
    category='animation',
    examples=[
        {'summary': '查询 pCube1 在第 10 帧的 translateY', 'args': {'name': 'pCube1', 'frame': 10, 'attribute': 'translateY'}},
    ],
    returns_desc='dict: {"value": float}',
)
def get_keyframe_value(name, frame, attribute='translateY'):
    # type: (str, float, str) -> Dict[str, Any]
    """查询关键帧值。

    :param name: 对象名
    :param frame: 帧数
    :param attribute: 属性名
    """
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    def _impl():
        if not cmds.objExists(name):
            raise ValueError('对象不存在: {}'.format(name))
        cmds.currentTime(frame)
        value = cmds.getAttr('{}.{}'.format(name, attribute))
        return {'value': value}

    return run_on_main(_impl)


@tool(
    dcc=['maya'],
    description='删除对象某属性在指定帧的关键帧。',
    category='animation',
    examples=[
        {'summary': '删除 pCube1 第 30 帧 rotate 关键帧', 'args': {'name': 'pCube1', 'frame': 30, 'attribute': 'rotate'}},
    ],
    returns_desc='dict: {"ok": True}',
)
def delete_keyframe(name, frame, attribute='translate'):
    # type: (str, float, str) -> Dict[str, Any]
    """删除指定关键帧。

    :param name: 对象名
    :param frame: 帧数
    :param attribute: 属性名
    """
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    def _impl():
        if not cmds.objExists(name):
            raise ValueError('对象不存在: {}'.format(name))
        cmds.currentTime(frame)
        cmds.cutKey(name, attribute=attribute, clear=True, time=(frame, frame))
        return {'ok': True}

    return run_on_main(_impl)


@tool(
    dcc=['maya'],
    description='设置 Maya 播放起始帧和结束帧。',
    category='animation',
    examples=[
        {'summary': '设置播放范围为 1-120 帧', 'args': {'start': 1, 'end': 120}},
    ],
    returns_desc='dict: {"ok": True}',
)
def set_playback_range(start, end):
    # type: (int, int) -> Dict[str, Any]
    """设置播放范围。

    :param start: 起始帧
    :param end: 结束帧
    """
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    def _impl():
        cmds.playbackOptions(minTime=start, maxTime=end)
        return {'ok': True}

    return run_on_main(_impl)


@tool(
    dcc=['maya'],
    description='获取当前时间滑块所在帧。',
    category='animation',
    examples=[{'summary': '获取当前帧', 'args': {}}],
    returns_desc='dict: {"frame": float}',
)
def get_current_frame():
    # type: () -> Dict[str, Any]
    """获取当前帧。"""
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    def _impl():
        return {'frame': cmds.currentTime(query=True)}

    return run_on_main(_impl)


@tool(
    dcc=['maya'],
    description='将当前帧移动到指定位置。',
    category='animation',
    examples=[{'summary': '跳到第 30 帧', 'args': {'frame': 30}}],
    returns_desc='dict: {"frame": float}',
)
def set_current_frame(frame):
    # type: (float) -> Dict[str, Any]
    """设置当前帧。

    :param frame: 帧数
    """
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    def _impl():
        cmds.currentTime(frame)
        return {'frame': frame}

    return run_on_main(_impl)


@tool(
    dcc=['maya'],
    description='把约束、IK、表达式等驱动的动画烘焙成关键帧。',
    category='animation',
    examples=[
        {
            'summary': '烘焙手腕控制器的 1-120 帧关键帧',
            'args': {'names': 'wrist_ctrl', 'start': 1, 'end': 120, 'step': 1},
        },
    ],
    returns_desc='dict: {"ok": True, "baked": List[str]}',
)
def bake_simulation(names, start=None, end=None, step=1):
    # type: (Any, Optional[int], Optional[int], int) -> Dict[str, Any]
    """烘焙模拟/约束到关键帧。

    :param names: 对象名列表或逗号分隔字符串
    :param start: 起始帧，None 表示使用播放范围起点
    :param end: 结束帧，None 表示使用播放范围终点
    :param step: 采样步长
    """
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    targets = _normalize_names(names)

    def _impl():
        if not targets:
            raise ValueError('必须指定要烘焙的对象')
        missing = [n for n in targets if not cmds.objExists(n)]
        if missing:
            raise ValueError('对象不存在: {}'.format(', '.join(missing)))

        if start is None or end is None:
            rng = cmds.playbackOptions(query=True, minTime=True, maxTime=True)
            s = start if start is not None else int(rng[0])
            e = end if end is not None else int(rng[1])
        else:
            s, e = start, end

        cmds.bakeResults(
            targets,
            time=(s, e),
            simulation=True,
            sampleBy=step,
            attribute=['tx', 'ty', 'tz', 'rx', 'ry', 'rz', 'sx', 'sy', 'sz'],
        )
        return {'ok': True, 'baked': targets}

    return run_on_main(_impl)


@tool(
    dcc=['maya'],
    description='给多个对象批量平移关键帧。',
    category='animation',
    examples=[
        {
            'summary': '把所有对象的关键帧向后移动 5 帧',
            'args': {'names': 'pCube1,pSphere1', 'offset': 5},
        },
    ],
    returns_desc='dict: {"ok": True}',
)
def shift_keyframes(names, offset, attribute=None):
    # type: (Any, float, Optional[str]) -> Dict[str, Any]
    """平移对象的关键帧。

    :param names: 对象名列表或逗号分隔字符串
    :param offset: 平移帧数
    :param attribute: 只平移某属性，None 表示全部关键帧
    """
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    targets = _normalize_names(names)

    def _impl():
        if not targets:
            raise ValueError('必须指定对象')
        missing = [n for n in targets if not cmds.objExists(n)]
        if missing:
            raise ValueError('对象不存在: {}'.format(', '.join(missing)))

        kwargs = {}
        if attribute:
            kwargs['attribute'] = attribute
        cmds.keyframe(targets, edit=True, relative=True, timeChange=offset, **kwargs)
        return {'ok': True}

    return run_on_main(_impl)
