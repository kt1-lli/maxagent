#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Maya 约束与驱动关键帧相关工具。

本模块由 rigging.py 拆分而来，rigging.py 保留为兼容门面。
"""

from __future__ import absolute_import
from __future__ import print_function

from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ....dcc.runtime import run_on_main
from ....tools.registry import tool
from .._common import _ensure_in_maya, _normalize_names, _to_xyz_list


@tool(
    dcc=['maya'],
    description='在控制器和关节/对象之间创建父约束、点约束、方向约束或缩放约束。',
    category='rigging',
    examples=[
        {
            'summary': '用 wrist_ctrl 控制 wrist_joint 的旋转和位置',
            'args': {'driver': 'wrist_ctrl', 'driven': 'wrist_joint', 'constraint_type': 'parent'},
        },
    ],
    returns_desc='str: 约束节点名',
    notes=['支持类型: parent / point / orient / scale / aim / pole_vector。', 'maintain_offset=True 表示以当前偏移为初始状态。'],
)
def create_constraint(driver, driven, constraint_type='parent', maintain_offset=True, name=None):
    # type: (str, str, str, bool, Optional[str]) -> str
    """创建约束。

    :param driver: 驱动对象名
    :param driven: 被驱动对象名
    :param constraint_type: parent / point / orient / scale
    :param maintain_offset: 是否保持偏移
    :param name: 约束节点名，None 则自动生成
    """
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    def _impl():
        if not cmds.objExists(driver):
            raise ValueError('驱动对象不存在: {}'.format(driver))
        if not cmds.objExists(driven):
            raise ValueError('被驱动对象不存在: {}'.format(driven))

        fn = getattr(cmds, '{}Constraint'.format(constraint_type), None)
        if fn is None:
            raise ValueError('未知约束类型: {}'.format(constraint_type))

        kwargs = {
            'maintainOffset': maintain_offset,
            'name': name,
        }
        result = fn(driver, driven, **{k: v for k, v in kwargs.items() if v is not None})
        return result[0]

    return run_on_main(_impl)

@tool(
    dcc=['maya'],
    description=(
        '创建 Set Driven Key（SDK 驱动关键帧）。让一个属性根据另一个属性的值变化。'
        '绑定中常用于校正形态（如手腕转动带动手指弯曲）。'
    ),
    category='rigging',
    examples=[
        {
            'summary': '让 IKFK 开关驱动 IK 链可见性',
            'args': {
                'driver': 'ctrl.ikfk',
                'driven': 'ik_group.visibility',
                'driver_value': 1.0,
                'driven_value': 1.0,
            },
        },
    ],
    notes=[
        '每次调用只设置一个关键点。要形成完整驱动关系，通常需要至少调用 2 次（起点+终点）。',
        'driver 和 driven 都是 "node.attr" 格式。',
    ],
    returns_desc='dict: {"ok": True}',
)
def create_set_driven_key(driver, driven, driver_value, driven_value):
    # type: (str, str, float, float) -> Dict[str, Any]
    """创建 SDK 驱动关键帧。

    :param driver: 驱动属性 "node.attr"
    :param driven: 被驱动属性 "node.attr"
    :param driver_value: 驱动值
    :param driven_value: 被驱动值
    """
    _ensure_in_maya()

    if '.' not in driver or '.' not in driven:
        raise ValueError('driver / driven 都必须是 "node.attr" 格式')

    def _impl():
        import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
        driver_node = driver.split('.', 1)[0]
        driven_node = driven.split('.', 1)[0]
        if not cmds.objExists(driver_node):
            raise ValueError('驱动节点不存在: {}'.format(driver_node))
        if not cmds.objExists(driven_node):
            raise ValueError('被驱动节点不存在: {}'.format(driven_node))

        # 先把 driven 属性设成目标值，再设驱动关键点
        cmds.setAttr(driven, float(driven_value))
        cmds.setDrivenKeyframe(
            driven,
            currentDriver=driver,
            driverValue=float(driver_value),
            value=float(driven_value),
        )
        return {'ok': True}

    return run_on_main(_impl)
