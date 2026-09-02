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
        '支持的 deformer_type: bend, twist, taper, flare, sine, squash, wave, ffd。',
        '非线性变形器（bend/twist 等）会自动创建变形器 handle transform。',
        'ffd 会创建 lattice 晶格；调整晶格顶点可控制变形。',
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
        valid = {'bend', 'twist', 'taper', 'flare', 'sine', 'squash',
                 'wave', 'ffd'}
        if deformer_type not in valid:
            raise ValueError('不支持的 deformer_type: {}，可选: {}'.format(
                deformer_type, ', '.join(sorted(valid)),
            ))
        # 先选中目标对象，nonLinear/lattice 会作用在选中项上
        cmds.select(name, replace=True)
        if deformer_type == 'ffd':
            # lattice 返回 (ffdNode, latticeShape, latticeBase)
            result = cmds.lattice(name, divisions=(2, 5, 2), objectCentered=True)
            deformer_node = result[0] if result else None
        else:
            # nonLinear 返回 [deformerNode, handleTransform]
            result = cmds.nonLinear(name, type=deformer_type)
            deformer_node = result[0] if result else None
        return deformer_node

    deformer_node = run_on_main(_do)
    return {'name': name, 'deformer': deformer_node}


__all__ = ['add_maya_deformer']
