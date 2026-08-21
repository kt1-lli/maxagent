#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Maya 变形器工具（最小集）。"""

from __future__ import absolute_import
from __future__ import print_function

from typing import Any
from typing import Dict

from ...dcc.runtime import current_dcc
from ...dcc.runtime import run_on_main
from ...tools.registry import tool


def _ensure_in_maya():
    # type: () -> None
    if current_dcc() != 'maya':
        raise RuntimeError('非 Maya 环境')


@tool(
    dcc=['maya'],
    description="给指定 Maya 对象添加 bend / twist / taper / noise 等变形器。",
    category="modifier",
    examples=[
        {
            'summary': '给 pCube1 添加 bend 变形器',
            'args': {'name': 'pCube1', 'deformer_type': 'bend'},
        },
    ],
    notes=[
        '支持的 deformer_type: bend, twist, taper, noise, ffd。',
        'Maya 中 noise 变形器类型名为 "noise"。',
    ],
    returns_desc='dict {"name": 对象名, "deformer": 变形器节点名}',
    prerequisites=['场景中必须存在名为 name 的对象'],
)
def add_maya_deformer(name: str, deformer_type: str = 'bend'):
    # type: (...) -> Dict[str, Any]
    """给 Maya 对象添加变形器。"""
    _ensure_in_maya()

    def _do():
        import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
        if not cmds.objExists(name):
            raise ValueError('对象不存在: {}'.format(name))
        if cmds.objectType(name) == 'transform':
            shapes = cmds.listRelatives(name, shapes=True) or []
            if not shapes:
                raise ValueError('对象没有 shape 节点: {}'.format(name))
            target = shapes[0]
        else:
            target = name
        valid = {'bend', 'twist', 'taper', 'noise', 'ffd'}
        if deformer_type not in valid:
            raise ValueError('不支持的 deformer_type: {}，可选: {}'.format(
                deformer_type, ', '.join(sorted(valid)),
            ))
        nodes = cmds.deformer(target, type=deformer_type) or []
        return nodes[0] if nodes else None

    deformer_node = run_on_main(_do)
    return {'name': name, 'deformer': deformer_node}


__all__ = ['add_maya_deformer']
