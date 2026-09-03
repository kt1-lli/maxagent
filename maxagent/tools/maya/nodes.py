#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Maya 节点/属性/连接类工具。

覆盖 Maya 绑定与节点编程中最常见的底层操作：
- 通用节点创建（DG 节点，如 multiplyDivide / condition / blendColors / setRange / remapValue / plusMinusAverage / reverse / clamp）
- 属性读写（setAttr / getAttr / 支持多种类型）
- 属性连接与断开（connectAttr / disconnectAttr）
- 自定义属性（addAttr，支持 float / int / bool / enum / message）
- 属性锁定与显示（lock / keyable / channelBox）
- 查询节点连接（listConnections）
- 节点删除、重命名、类型查询

所有会修改场景的操作都通过 run_on_main 提交到 Maya 主线程，避免 pymxs 风格
的多线程崩溃问题（Maya API 同样非线程安全）。
"""

from __future__ import absolute_import
from __future__ import print_function

from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ...dcc.runtime import current_dcc
from ...dcc.runtime import run_on_main
from ...tools.registry import tool


# DG 节点白名单：常用的绑定/材质/动画节点类型
# 使用白名单避免 LLM 乱写节点类型触发 Maya 弹窗
_ALLOWED_DG_NODES = {
    # 数学/逻辑节点
    'multiplyDivide',
    'plusMinusAverage',
    'condition',
    'blendColors',
    'setRange',
    'remapValue',
    'remapColor',
    'reverse',
    'clamp',
    'blendTwoAttr',
    'multDoubleLinear',
    'addDoubleLinear',
    'unitConversion',
    'distanceBetween',
    'angleBetween',
    'vectorProduct',
    'decomposeMatrix',
    'composeMatrix',
    'multMatrix',
    'inverseMatrix',
    'fourByFourMatrix',
    'aimMatrix',
    'pickMatrix',
    'blendMatrix',
    # 曲线/表面节点
    'curveInfo',
    'motionPath',
    'pointOnCurveInfo',
    'pointOnSurfaceInfo',
    'nearestPointOnCurve',
    # 变形器辅助
    'follicle',
    'closestPointOnMesh',
    # 数据流
    'choice',
    'network',
}


def _ensure_in_maya():
    # type: () -> None
    """确保当前运行在 Maya 环境，否则抛出 RuntimeError。"""
    if current_dcc() != 'maya':
        raise RuntimeError('非 Maya 环境')


@tool(
    dcc=['maya'],
    description=(
        '创建通用 Maya DG 节点（multiplyDivide / condition / blendColors / setRange / '
        'remapValue / plusMinusAverage / reverse / clamp / decomposeMatrix / multMatrix 等）。'
        '这是绑定中最常用的底层节点工具，比 shadingNode/createNode 更安全（限制在白名单内）。'
    ),
    category='node',
    examples=[
        {
            'summary': '创建 multiplyDivide 节点用于旋转值转换',
            'args': {'node_type': 'multiplyDivide', 'name': 'wrist_rot_multiply'},
        },
        {
            'summary': '创建 condition 节点用于 IKFK 切换',
            'args': {'node_type': 'condition', 'name': 'ikfk_condition'},
        },
    ],
    notes=[
        'node_type 必须在白名单内，防止 LLM 乱写节点类型触发弹窗。',
        '如果 node_type 是常规 DG 节点用 createNode，若是着色器节点建议改用 create_maya_shader。',
        '返回的节点名可能与传入的 name 不同（Maya 自动去重后缀）。',
    ],
    returns_desc='str: 实际创建的节点名',
    prerequisites=['Maya 场景已打开'],
)
def create_maya_node(node_type, name=None):
    # type: (str, Optional[str]) -> str
    """创建通用 Maya DG 节点。

    :param node_type: 节点类型，必须在 _ALLOWED_DG_NODES 白名单内
    :param name: 节点名，None 时由 Maya 自动命名
    """
    _ensure_in_maya()

    if node_type not in _ALLOWED_DG_NODES:
        raise ValueError(
            '不支持的节点类型: {}。支持列表: {}'.format(
                node_type, ', '.join(sorted(_ALLOWED_DG_NODES)),
            ),
        )

    def _impl():
        import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
        kwargs = {}
        if name:
            kwargs['name'] = name
        return cmds.createNode(node_type, **kwargs)

    return run_on_main(_impl)


@tool(
    dcc=['maya'],
    description=(
        '连接两个 Maya 属性（source.attr → target.attr），是绑定/节点网络最核心的操作。'
    ),
    category='node',
    examples=[
        {
            'summary': '把控制器的 translateX 连接到关节',
            'args': {'source': 'ctrl.translateX', 'target': 'joint1.translateX'},
        },
        {
            'summary': '把 multiplyDivide 输出连接到旋转',
            'args': {'source': 'multi_node.outputX', 'target': 'wrist_joint.rotateX'},
        },
    ],
    notes=[
        'source 与 target 必须是完整的 "node.attribute" 格式（不能只写节点名）。',
        '如果目标属性已有连接，需要先断开或使用 force=True。',
        '常见错误：把 vector 属性连接到 scalar 属性会失败，请连子属性（如 .outputX / .rotateY）。',
    ],
    returns_desc='dict: {"ok": True, "source": str, "target": str}',
    prerequisites=['source 与 target 对应的节点和属性都存在'],
)
def connect_maya_attr(source, target, force=False):
    # type: (str, str, bool) -> Dict[str, Any]
    """连接两个属性。

    :param source: 源属性完整路径，如 "ctrl.translateX"
    :param target: 目标属性完整路径
    :param force: 如目标已有连接是否强制断开重连
    """
    _ensure_in_maya()

    if '.' not in source or '.' not in target:
        raise ValueError(
            'source/target 必须是 "node.attribute" 格式，'
            '收到: source={}, target={}'.format(source, target),
        )

    def _impl():
        import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
        src_node = source.split('.', 1)[0]
        dst_node = target.split('.', 1)[0]
        if not cmds.objExists(src_node):
            raise ValueError('源节点不存在: {}'.format(src_node))
        if not cmds.objExists(dst_node):
            raise ValueError('目标节点不存在: {}'.format(dst_node))
        cmds.connectAttr(source, target, force=force)
        return {'ok': True, 'source': source, 'target': target}

    return run_on_main(_impl)


@tool(
    dcc=['maya'],
    description='断开两个 Maya 属性之间的连接。',
    category='node',
    examples=[
        {
            'summary': '断开控制器与关节的连接',
            'args': {'source': 'ctrl.translateX', 'target': 'joint1.translateX'},
        },
    ],
    notes=[
        '如果不确定源属性是什么，可以先用 list_maya_connections 查询。',
        '与 connect_maya_attr 参数含义一致。',
    ],
    returns_desc='dict: {"ok": True}',
)
def disconnect_maya_attr(source, target):
    # type: (str, str) -> Dict[str, Any]
    """断开属性连接。

    :param source: 源属性完整路径
    :param target: 目标属性完整路径
    """
    _ensure_in_maya()

    if '.' not in source or '.' not in target:
        raise ValueError(
            'source/target 必须是 "node.attribute" 格式',
        )

    def _impl():
        import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
        cmds.disconnectAttr(source, target)
        return {'ok': True}

    return run_on_main(_impl)


@tool(
    dcc=['maya'],
    description=(
        '设置 Maya 属性值，自动识别常见类型（float / int / bool / string / '
        'vector [x,y,z] / matrix / enum）。'
    ),
    category='node',
    examples=[
        {
            'summary': '设置浮点属性',
            'args': {'attribute': 'pCube1.translateX', 'value': 5.0},
        },
        {
            'summary': '设置向量属性（一次设 3 个分量）',
            'args': {'attribute': 'pCube1.translate', 'value': [1.0, 2.0, 3.0]},
        },
        {
            'summary': '设置字符串属性',
            'args': {'attribute': 'myNode.notes', 'value': 'hello'},
        },
    ],
    notes=[
        '如果 attribute 已被连接（有 input connection），setAttr 会失败。'
        '此时请先 disconnect_maya_attr 或改设驱动节点的属性。',
        'string 类型属性需要 value 是 str；vector 需要 3 个数值的列表。',
        '如需强制解锁再设置，使用 force=True。',
    ],
    returns_desc='dict: {"ok": True, "attribute": str}',
)
def set_maya_attr(attribute, value, force=False):
    # type: (str, Any, bool) -> Dict[str, Any]
    """设置 Maya 属性值。

    :param attribute: "node.attr" 格式
    :param value: 数值 / 布尔 / 字符串 / 3 元素列表
    :param force: 强制解锁再设置
    """
    _ensure_in_maya()

    if '.' not in attribute:
        raise ValueError('attribute 必须是 "node.attr" 格式')

    def _impl():
        import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

        node = attribute.split('.', 1)[0]
        if not cmds.objExists(node):
            raise ValueError('节点不存在: {}'.format(node))

        if force and cmds.getAttr(attribute, lock=True):
            cmds.setAttr(attribute, lock=False)

        real_value = value
        # 字符串输入可能是：纯字符串属性值、"[1,2,3]" 向量、或 "2.0" / "1" / "true" 等数值/布尔字面量。
        # 优先尝试解析为 JSON 得到真实类型，失败再退回字符串。
        if isinstance(real_value, str):
            s = real_value.strip()
            if s:
                try:
                    import json  # pylint: disable=import-outside-toplevel
                    parsed = json.loads(s)
                    if isinstance(parsed, (list, tuple, int, float, bool)):
                        real_value = parsed
                except Exception:  # pylint: disable=broad-except
                    pass

        # 字符串类型
        if isinstance(real_value, str):
            cmds.setAttr(attribute, real_value, type='string')
        # 布尔（注意：bool 是 int 子类，必须在 int/float 之前判断）
        elif isinstance(real_value, bool):
            cmds.setAttr(attribute, bool(real_value))
        # 向量/矩阵：list/tuple
        elif isinstance(real_value, (list, tuple)):
            if len(real_value) == 3:
                cmds.setAttr(
                    attribute,
                    float(real_value[0]),
                    float(real_value[1]),
                    float(real_value[2]),
                    type='double3',
                )
            elif len(real_value) == 16:
                cmds.setAttr(attribute, real_value, type='matrix')
            else:
                raise ValueError(
                    'value 是列表时长度必须是 3（vector）或 16（matrix），收到 {}'.format(
                        len(real_value),
                    ),
                )
        elif isinstance(real_value, (int, float)):
            cmds.setAttr(attribute, float(real_value))
        else:
            raise ValueError(
                '不支持的 value 类型: {}'.format(type(real_value).__name__),
            )

        return {'ok': True, 'attribute': attribute}

    return run_on_main(_impl)


@tool(
    dcc=['maya'],
    description='读取 Maya 属性值。',
    category='node',
    examples=[
        {'summary': '读取 translateX', 'args': {'attribute': 'pCube1.translateX'}},
    ],
    notes=[
        'vector 属性返回 [(x, y, z)] 嵌套列表，是 Maya 原始格式，本工具会自动解包为 [x, y, z]。',
    ],
    returns_desc='任意类型：float / int / bool / str / list',
)
def get_maya_attr(attribute):
    # type: (str) -> Any
    """读取 Maya 属性值。

    :param attribute: "node.attr" 格式
    """
    _ensure_in_maya()

    if '.' not in attribute:
        raise ValueError('attribute 必须是 "node.attr" 格式')

    def _impl():
        import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
        val = cmds.getAttr(attribute)
        # Maya vector 属性返回 [(x, y, z)]，解包为 [x, y, z]
        if isinstance(val, list) and len(val) == 1 and isinstance(val[0], tuple):
            return list(val[0])
        return val

    return run_on_main(_impl)


@tool(
    dcc=['maya'],
    description=(
        '给 Maya 节点添加自定义属性（addAttr）。绑定中最常用于给控制器添加'
        'IKFK 切换开关、可见性开关、拉伸开关等。'
    ),
    category='node',
    examples=[
        {
            'summary': '给控制器添加 IKFK 切换属性',
            'args': {
                'node': 'wrist_ctrl',
                'attr_name': 'ikfk',
                'attr_type': 'float',
                'min_value': 0.0,
                'max_value': 1.0,
                'default': 0.0,
                'keyable': True,
            },
        },
        {
            'summary': '添加枚举属性',
            'args': {
                'node': 'wrist_ctrl',
                'attr_name': 'space',
                'attr_type': 'enum',
                'enum_names': 'world:local:cog',
            },
        },
    ],
    notes=[
        'attr_type: float / int / bool / enum / string / message。',
        'enum 类型必须提供 enum_names（用 : 分隔）。',
        'keyable=True 会在通道盒显示且可 K 帧，keyable=False + channel_box=True 只在通道盒显示。',
    ],
    returns_desc='str: 完整属性路径 "node.attr_name"',
)
def add_maya_attr(
    node,
    attr_name,
    attr_type='float',
    min_value=None,
    max_value=None,
    default=None,
    keyable=True,
    channel_box=False,
    enum_names=None,
):
    # type: (str, str, str, Any, Any, Any, bool, bool, Optional[str]) -> str
    """在节点上添加自定义属性。

    :param node: 目标节点名
    :param attr_name: 新属性名（长名）
    :param attr_type: float / int / bool / enum / string / message
    :param min_value: 数值属性的最小值
    :param max_value: 数值属性的最大值
    :param default: 默认值
    :param keyable: 是否可 K 帧
    :param channel_box: keyable=False 时是否仍在通道盒显示
    :param enum_names: enum 类型时的枚举值（用冒号分隔，如 "world:local:cog"）
    """
    _ensure_in_maya()

    valid_types = {'float', 'int', 'bool', 'enum', 'string', 'message'}
    if attr_type not in valid_types:
        raise ValueError(
            '不支持的 attr_type: {}，可选: {}'.format(attr_type, ', '.join(sorted(valid_types))),
        )

    def _impl():
        import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
        if not cmds.objExists(node):
            raise ValueError('节点不存在: {}'.format(node))

        kwargs = {'longName': attr_name, 'keyable': keyable}

        if attr_type == 'float':
            kwargs['attributeType'] = 'double'
            if min_value is not None:
                kwargs['minValue'] = float(min_value)
            if max_value is not None:
                kwargs['maxValue'] = float(max_value)
            if default is not None:
                kwargs['defaultValue'] = float(default)
        elif attr_type == 'int':
            kwargs['attributeType'] = 'long'
            if min_value is not None:
                kwargs['minValue'] = int(min_value)
            if max_value is not None:
                kwargs['maxValue'] = int(max_value)
            if default is not None:
                kwargs['defaultValue'] = int(default)
        elif attr_type == 'bool':
            kwargs['attributeType'] = 'bool'
            if default is not None:
                kwargs['defaultValue'] = bool(default)
        elif attr_type == 'enum':
            if not enum_names:
                raise ValueError('enum 类型必须提供 enum_names（用 : 分隔）')
            kwargs['attributeType'] = 'enum'
            kwargs['enumName'] = enum_names
        elif attr_type == 'string':
            kwargs['dataType'] = 'string'
            kwargs.pop('keyable', None)  # string 无 keyable
        elif attr_type == 'message':
            kwargs['attributeType'] = 'message'
            kwargs.pop('keyable', None)

        cmds.addAttr(node, **kwargs)

        full = '{}.{}'.format(node, attr_name)
        if not keyable and channel_box:
            cmds.setAttr(full, channelBox=True)
        if attr_type == 'string' and default is not None:
            cmds.setAttr(full, str(default), type='string')

        return full

    return run_on_main(_impl)


@tool(
    dcc=['maya'],
    description=(
        '锁定 / 隐藏 / 设置通道盒可见性。绑定中常用于锁死控制器的不该操作的通道。'
    ),
    category='node',
    examples=[
        {
            'summary': '锁定并隐藏控制器的 scale 通道',
            'args': {
                'node': 'wrist_ctrl',
                'attrs': ['scaleX', 'scaleY', 'scaleZ', 'visibility'],
                'lock': True,
                'keyable': False,
                'channel_box': False,
            },
        },
    ],
    notes=[
        'lock=True + keyable=False + channel_box=False 是"完全锁死通道"的标准做法。',
        'attrs 可以是单个属性名或列表。',
    ],
    returns_desc='dict: {"ok": True, "processed": int}',
)
def lock_maya_attrs(node, attrs, lock=True, keyable=False, channel_box=False):
    # type: (str, Any, bool, bool, bool) -> Dict[str, Any]
    """批量锁定 / 隐藏属性。

    :param node: 节点名
    :param attrs: 属性名列表或单个字符串
    :param lock: 是否锁定
    :param keyable: 是否可 K 帧（默认 False，即不可 K 帧）
    :param channel_box: keyable=False 时是否在通道盒显示
    """
    _ensure_in_maya()

    if isinstance(attrs, str):
        attr_list = [attrs]
    elif isinstance(attrs, (list, tuple)):
        attr_list = [str(a) for a in attrs]
    else:
        raise ValueError('attrs 必须是字符串或字符串列表')

    def _impl():
        import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
        if not cmds.objExists(node):
            raise ValueError('节点不存在: {}'.format(node))
        for a in attr_list:
            full = '{}.{}'.format(node, a)
            if not cmds.attributeQuery(a, node=node, exists=True):
                # 不存在的属性跳过而非报错
                continue
            cmds.setAttr(full, lock=lock, keyable=keyable, channelBox=channel_box)
        return {'ok': True, 'processed': len(attr_list)}

    return run_on_main(_impl)


@tool(
    dcc=['maya'],
    description=(
        '查询 Maya 节点的连接：可查上游驱动、下游被驱动、或某个特定属性的连接。'
    ),
    category='node',
    examples=[
        {
            'summary': '查节点所有上游驱动',
            'args': {'node': 'wrist_joint', 'direction': 'source'},
        },
        {
            'summary': '查某属性的下游连接',
            'args': {'node': 'ctrl', 'attribute': 'translateX', 'direction': 'destination'},
        },
    ],
    notes=[
        'direction: source (上游) / destination (下游) / both (双向)。',
        '返回 [{"src": "...", "dst": "..."}] 列表，src/dst 都是完整 "node.attr" 格式。',
        '如果传了 attribute，只查该属性的连接。',
    ],
    returns_desc='List[Dict]: 连接对列表',
)
def list_maya_connections(node, attribute=None, direction='both'):
    # type: (str, Optional[str], str) -> List[Dict[str, str]]
    """查询节点的连接。

    :param node: 节点名
    :param attribute: 只查该属性的连接，None 表示查所有
    :param direction: source / destination / both
    """
    _ensure_in_maya()

    if direction not in ('source', 'destination', 'both'):
        raise ValueError("direction 必须是 'source' / 'destination' / 'both'")

    def _impl():
        import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
        if not cmds.objExists(node):
            raise ValueError('节点不存在: {}'.format(node))

        target = '{}.{}'.format(node, attribute) if attribute else node

        results = []  # type: List[Dict[str, str]]
        # 上游：source=True, destination=False
        if direction in ('source', 'both'):
            pairs = cmds.listConnections(
                target, source=True, destination=False,
                plugs=True, connections=True,
            ) or []
            for i in range(0, len(pairs), 2):
                if i + 1 >= len(pairs):
                    break
                results.append({'src': pairs[i + 1], 'dst': pairs[i]})
        # 下游：source=False, destination=True
        if direction in ('destination', 'both'):
            pairs = cmds.listConnections(
                target, source=False, destination=True,
                plugs=True, connections=True,
            ) or []
            for i in range(0, len(pairs), 2):
                if i + 1 >= len(pairs):
                    break
                results.append({'src': pairs[i], 'dst': pairs[i + 1]})

        return results

    return run_on_main(_impl)


@tool(
    dcc=['maya'],
    description='删除一个或多个 Maya 节点。',
    category='node',
    examples=[
        {'summary': '删除单个节点', 'args': {'nodes': 'temp_locator1'}},
        {'summary': '批量删除', 'args': {'nodes': ['temp1', 'temp2']}},
    ],
    notes=['删除后无法恢复（除了 undo）。绑定调试时慎用。'],
    returns_desc='dict: {"deleted": int}',
)
def delete_maya_nodes(nodes):
    # type: (Any) -> Dict[str, Any]
    """删除节点。

    :param nodes: 节点名或名列表；字符串可以是单个名字，也可以是逗号/分号分隔的多个名字
    """
    _ensure_in_maya()

    if isinstance(nodes, str):
        s = nodes.strip()
        if not s:
            name_list = []  # type: List[str]
        else:
            # 支持逗号/分号/中文标点分隔的多节点字符串
            found_sep = None
            for sep in (',', ';', '\uff0c', '\uff1b'):
                if sep in s:
                    found_sep = sep
                    break
            if found_sep:
                name_list = [p.strip() for p in s.split(found_sep) if p.strip()]
            else:
                name_list = [s]
    elif isinstance(nodes, (list, tuple)):
        name_list = [str(n).strip() for n in nodes if str(n).strip()]
    else:
        raise ValueError('nodes 必须是字符串或列表')

    def _impl():
        import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
        existing = [n for n in name_list if cmds.objExists(n)]
        missing = [n for n in name_list if not cmds.objExists(n)]
        if existing:
            cmds.delete(existing)
        return {
            'deleted': len(existing),
            'deleted_names': existing,
            'missing': missing,
        }

    return run_on_main(_impl)


@tool(
    dcc=['maya'],
    description='重命名 Maya 节点。',
    category='node',
    examples=[
        {'summary': '重命名', 'args': {'old_name': 'pCube1', 'new_name': 'body_geo'}},
    ],
    notes=[
        '返回的名字可能与 new_name 不同（如场景中已存在同名节点，Maya 会加后缀）。',
    ],
    returns_desc='str: 实际新名字',
)
def rename_maya_node(old_name, new_name):
    # type: (str, str) -> str
    """重命名节点。

    :param old_name: 原名
    :param new_name: 新名
    """
    _ensure_in_maya()

    def _impl():
        import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
        if not cmds.objExists(old_name):
            raise ValueError('节点不存在: {}'.format(old_name))
        return cmds.rename(old_name, new_name)

    return run_on_main(_impl)


@tool(
    dcc=['maya'],
    description='查询 Maya 节点的类型（nodeType）。',
    category='node',
    examples=[
        {'summary': '查节点类型', 'args': {'node': 'pCube1'}},
    ],
    notes=['transform 节点会返回 "transform"，网格 shape 返回 "mesh"，关节返回 "joint" 等。'],
    returns_desc='str: 节点类型',
)
def get_maya_node_type(node):
    # type: (str) -> str
    """查询节点类型。"""
    _ensure_in_maya()

    def _impl():
        import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
        if not cmds.objExists(node):
            raise ValueError('节点不存在: {}'.format(node))
        return cmds.nodeType(node)

    return run_on_main(_impl)


__all__ = [
    'create_maya_node',
    'connect_maya_attr',
    'disconnect_maya_attr',
    'set_maya_attr',
    'get_maya_attr',
    'add_maya_attr',
    'lock_maya_attrs',
    'list_maya_connections',
    'delete_maya_nodes',
    'rename_maya_node',
    'get_maya_node_type',
]
