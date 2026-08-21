#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""动画相关工具：关键帧、控制器、约束、参数线、时间控制、烘焙。

面向动画/技术美术，补齐 MaxAgent 在动画管线上的能力缺口。
所有会修改场景的操作都默认包在 undo 块内（run_on_main_thread=True + 不设置 wrap_undo=False）。
"""

from __future__ import absolute_import
from __future__ import print_function

from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ...logger import get_logger
from ...runtime_helpers import IN_MAX
from ...runtime_helpers import rt
from ...tools.registry import tool


logger = get_logger(__name__)


def _ensure_in_max():
    if not IN_MAX:
        raise RuntimeError("非 3ds Max 环境")


def _get_node(name: str):
    """按名查找节点。"""
    node = rt.getNodeByName(name, exact=True, all=False)
    if node is None:
        raise ValueError("对象不存在: {}".format(name))
    return node


def _get_controller(node, controller_name: str):
    """通过字符串名取对象的 transform 子控制器，例如 'position', 'rotation', 'scale'。

    注意：pymxs 节点的公开属性名是 ``pos`` 而不是 ``position``；``node['pos']``
    和 ``node.position`` 都可能返回 ``None``。``getPropertyController`` 在某些版本上
    也不稳定。最可靠的方式是 ``getattr(node, 'pos').controller``，这与
    MAXScript 的 ``obj.pos.controller`` 语义一致。

    该函数目前保留给需要直接操作 controller 的场景（如 get_keyframe_value、
    delete_keyframe、约束相关工具）；set_keyframe 已改用
    ``pymxs.animate(True) + pymxs.attime(frame) + setattr`` 路径，避免 controller 访问。

    :param node: pymxs 节点对象
    :param controller_name: 'position' / 'rotation' / 'scale'
    :return: 对应的控制器对象
    """
    if not controller_name:
        raise ValueError("controller_name 不能为空")
    prop_map = {
        "position": "pos",
        "rotation": "rotation",
        "scale": "scale",
    }
    mxs_prop = prop_map.get(controller_name)
    if mxs_prop is None:
        raise ValueError("未知控制器名: {}".format(controller_name))

    try:
        ctrl = getattr(node, mxs_prop).controller
    except Exception as exc:  # pylint: disable=broad-except
        raise ValueError(
            "获取控制器失败: {}.{} ({})".format(node.name, controller_name, exc),
        ) from exc
    if ctrl is None:
        raise ValueError("控制器为空: {}.{}".format(node.name, controller_name))
    return ctrl


def _prop_name(controller_name: str) -> str:
    """把对外的 'position'/'rotation'/'scale' 映射到 pymxs 节点真实属性名。"""
    mapping = {
        "position": "pos",
        "rotation": "rotation",
        "scale": "scale",
    }
    return mapping.get(controller_name, controller_name)


# ---------------------------------------------------------------------- #
# 关键帧
# ---------------------------------------------------------------------- #

@tool(
    dcc=['3dsmax'],
    description='给对象在指定帧设置关键帧。',
    category='animation',
    examples=[
        {
            'summary': '在 30 帧记录 Box 的位置关键帧',
            'args': {'name': 'Box01', 'frame': 30, 'controller': 'position'},
        },
        {
            'summary': '在 60 帧同时记录位置、旋转、缩放关键帧',
            'args': {'name': 'Box01', 'frame': 60, 'controller': 'transform'},
        },
    ],
    notes=[
        "controller 可选 'position' / 'rotation' / 'scale' / 'transform'。",
        "不传 controller 时默认对所有 transform 属性（位置/旋转/缩放）打关键帧。",
        "调用前对象必须已存在；会复用当前属性的值作为关键帧值。",
    ],
    returns_desc='dict {"name": 对象名, "frame": 帧号, "controller": 控制器类型}',
    prerequisites=['对象 name 必须已存在于场景中'],
    run_on_main_thread=True,
)
def set_keyframe(name: str, frame: float, controller: Optional[str] = None):
    """设置关键帧。

    :param name: 对象名
    :param frame: 帧号
    :param controller: 可选 'position'/'rotation'/'scale'/'transform'
    :returns: dict 描述执行结果
    """
    _ensure_in_max()
    node = _get_node(name)
    frame = float(frame)

    if controller is None or controller == "transform":
        controllers = ["position", "rotation", "scale"]
    elif controller in ("position", "rotation", "scale"):
        controllers = [controller]
    else:
        raise ValueError(
            "controller 必须是 position/rotation/scale/transform 之一",
        )

    # 在函数内部延迟导入 pymxs，非 Max 环境测试不会触发
    import pymxs  # pylint: disable=import-error,import-outside-toplevel

    # 实测验证的可靠路径：在 pymxs.animate(True) + pymxs.attime(frame)
    # 上下文中将当前 transform 属性值写回自身，即可自动记录关键帧。
    # 这种方式不直接访问 .controller，避开 position vs pos 的属性名陷阱。
    with pymxs.animate(True):
        with pymxs.attime(frame):
            for ctrl_name in controllers:
                prop_name = _prop_name(ctrl_name)
                try:
                    current_value = getattr(node, prop_name)
                    setattr(node, prop_name, current_value)
                except Exception as exc:  # pylint: disable=broad-except
                    raise RuntimeError(
                        "设置关键帧失败: {}.{} @ frame {} ({})".format(
                            name, ctrl_name, frame, exc,
                        ),
                    ) from exc
    return {
        "name": name,
        "frame": frame,
        "controller": controller or "transform",
    }


@tool(
    dcc=['3dsmax'],
    description="删除对象指定帧的关键帧。controller 不传则删除 transform 关键帧。",
    category="animation",
    examples=[
        {
            'summary': '删除对象在 30 帧的位置关键帧',
            'args': {'name': 'Box01', 'frame': 30, 'controller': 'position'},
        },
        {
            'summary': '删除对象在 60 帧的所有 transform 关键帧',
            'args': {'name': 'Box01', 'frame': 60},
        },
    ],
    notes=[
        "controller 可选 'position' / 'rotation' / 'scale' / 'transform'。",
        "不传 controller 时默认删除位置、旋转、缩放三类关键帧。",
        "若指定帧无对应关键帧，Max 通常静默忽略，不会报错。",
    ],
    returns_desc='dict {"name": 对象名, "frame": 帧号, "controller": 控制器类型, "deleted": true}',
    prerequisites=['对象 name 必须已存在于场景中'],
    run_on_main_thread=True,
)
def delete_keyframe(name: str, frame: float, controller: Optional[str] = None):
    """删除关键帧。

    :param name: 对象名
    :param frame: 帧号
    :param controller: 可选 'position'/'rotation'/'scale'/'transform'
    """
    _ensure_in_max()
    node = _get_node(name)
    frame = float(frame)

    if controller is None or controller == "transform":
        controllers = ["position", "rotation", "scale"]
    elif controller in ("position", "rotation", "scale"):
        controllers = [controller]
    else:
        raise ValueError(
            "controller 必须是 position/rotation/scale/transform 之一",
        )

    import pymxs  # pylint: disable=import-error,import-outside-toplevel

    with pymxs.animate(True):
        with pymxs.attime(frame):
            for ctrl_name in controllers:
                ctrl = _get_controller(node, ctrl_name)
                # deleteKey 删除控制器在指定帧的关键帧；若该帧无关键帧不报错
                try:
                    rt.deleteKey(ctrl, frame)
                except Exception as exc:  # pylint: disable=broad-except
                    raise RuntimeError(
                        "删除关键帧失败: {}.{} @ frame {} ({})".format(
                            name, ctrl_name, frame, exc,
                        ),
                    ) from exc
    return {
        "name": name,
        "frame": frame,
        "controller": controller or "transform",
        "deleted": True,
    }


@tool(
    dcc=['3dsmax'],
    description="获取对象在指定帧的 transform 关键帧值（如果有的话）。",
    category="animation",
    examples=[
        {
            'summary': '读取对象在 30 帧的位置关键帧值',
            'args': {'name': 'Box01', 'frame': 30, 'controller': 'position'},
        },
        {
            'summary': '读取对象在 60 帧的旋转关键帧值',
            'args': {'name': 'Box01', 'frame': 60, 'controller': 'rotation'},
        },
    ],
    notes=[
        "controller 只能是 'position' / 'rotation' / 'scale' 之一，不能传 'transform'。",
        "返回的 value 为 [x, y, z] 列表；如果控制器值不可分解，则返回字符串。",
        "指定帧无关键帧时返回的是该帧当前属性值，而非关键帧本身。",
    ],
    returns_desc='dict {"name": 对象名, "frame": 帧号, "controller": 控制器类型, "value": [x,y,z] 或字符串}',
    prerequisites=['对象 name 必须已存在于场景中'],
    run_on_main_thread=True,
)
def get_keyframe_value(name: str, frame: float, controller: str = "position"):
    """读取关键帧值。

    :param name: 对象名
    :param frame: 帧号
    :param controller: 'position' / 'rotation' / 'scale'
    :returns: dict {"frame": ..., "value": [...]}
    """
    _ensure_in_max()
    node = _get_node(name)
    if controller not in ("position", "rotation", "scale"):
        raise ValueError("controller 必须是 position/rotation/scale 之一")
    frame = float(frame)

    import pymxs  # pylint: disable=import-error,import-outside-toplevel

    ctrl = _get_controller(node, controller)
    try:
        with pymxs.animate(False), pymxs.attime(frame):
            val = ctrl.value
    except Exception as exc:  # pylint: disable=broad-except
        raise RuntimeError(
            "读取关键帧值失败: {}.{} @ frame {} ({})".format(
                name, controller, frame, exc,
            ),
        ) from exc

    # 如果是 Point3 / Quat / EulerAngles 等具有 x/y/z 成员的类型
    if hasattr(val, "x") and hasattr(val, "y") and hasattr(val, "z"):
        return {
            "name": name,
            "frame": frame,
            "controller": controller,
            "value": [float(val.x), float(val.y), float(val.z)],
        }
    return {
        "name": name,
        "frame": frame,
        "controller": controller,
        "value": str(val),
    }


# ---------------------------------------------------------------------- #
# 控制器与约束
# ---------------------------------------------------------------------- #

@tool(
    dcc=['3dsmax'],
    description=(
        "给对象添加 LookAt 约束，使其始终朝向目标对象。"
        "常用于眼睛注视、武器瞄准、灯光跟随等。"
    ),
    category="animation",
    examples=[
        {
            'summary': '让 Light01 始终注视 Box01',
            'args': {
                'source_name': 'Light01',
                'target_name': 'Box01',
                'weight': 1.0,
            },
        },
        {
            'summary': '使用 50% 权重添加注视约束',
            'args': {
                'source_name': 'Eye_L',
                'target_name': 'LookAtTarget',
                'weight': 0.5,
            },
        },
    ],
    notes=[
        "weight 取值范围为 0.0-1.0，决定约束影响力。",
        "该工具会直接替换 source_name 对象的 rotation.controller。",
        "注视轴向依赖 Max 默认设置，通常需要后续手动校正 up-vector。",
    ],
    returns_desc='dict {"source": 源对象名, "target": 目标对象名, "constraint": "LookAt_Constraint"}',
    prerequisites=[
        'source_name 和 target_name 必须已存在于场景中',
    ],
    run_on_main_thread=True,
)
def add_lookat_constraint(source_name: str, target_name: str, weight: float = 1.0):
    """添加 LookAt 约束。

    :param source_name: 被约束对象
    :param target_name: 注视目标
    :param weight: 约束权重 0.0-1.0
    """
    _ensure_in_max()
    src = _get_node(source_name)
    tgt = _get_node(target_name)
    constraint = rt.LookAt_Constraint()
    constraint.appendTarget(tgt, weight)
    src.rotation.controller = constraint
    return {
        "source": source_name,
        "target": target_name,
        "constraint": "LookAt_Constraint",
    }


@tool(
    dcc=['3dsmax'],
    description=(
        "给对象添加 Position 约束，使其位置跟随目标对象。"
    ),
    category="animation",
    examples=[
        {
            'summary': '让 Box01 位置跟随 Box02',
            'args': {
                'source_name': 'Box01',
                'target_name': 'Box02',
                'weight': 1.0,
            },
        },
        {
            'summary': '使用 30% 权重进行位置约束',
            'args': {
                'source_name': 'Accessory',
                'target_name': 'Character',
                'weight': 0.3,
            },
        },
    ],
    notes=[
        "weight 取值范围为 0.0-1.0，决定位置跟随程度。",
        "该工具会直接替换 source_name 对象的 position.controller。",
        "约束后 source_name 仍可被其它工具移动，但会受目标位置牵引。",
    ],
    returns_desc='dict {"source": 源对象名, "target": 目标对象名, "constraint": "Position_Constraint"}',
    prerequisites=[
        'source_name 和 target_name 必须已存在于场景中',
    ],
    run_on_main_thread=True,
)
def add_position_constraint(source_name: str, target_name: str, weight: float = 1.0):
    """添加 Position 约束。

    :param source_name: 被约束对象
    :param target_name: 目标位置来源
    :param weight: 约束权重
    """
    _ensure_in_max()
    src = _get_node(source_name)
    tgt = _get_node(target_name)
    constraint = rt.Position_Constraint()
    constraint.appendTarget(tgt, weight)
    src.position.controller = constraint
    return {
        "source": source_name,
        "target": target_name,
        "constraint": "Position_Constraint",
    }


@tool(
    dcc=['3dsmax'],
    description=(
        "给对象添加 Orientation 约束，使其旋转跟随目标对象。"
    ),
    category="animation",
    examples=[
        {
            'summary': '让 Box01 旋转跟随 Box02',
            'args': {
                'source_name': 'Box01',
                'target_name': 'Box02',
                'weight': 1.0,
            },
        },
        {
            'summary': '使用 70% 权重进行旋转约束',
            'args': {
                'source_name': 'Prop',
                'target_name': 'Hand',
                'weight': 0.7,
            },
        },
    ],
    notes=[
        "weight 取值范围为 0.0-1.0，决定旋转跟随程度。",
        "该工具会直接替换 source_name 对象的 rotation.controller。",
        "与 LookAt 约束不同，Orientation 约束复制目标旋转而非注视目标。",
    ],
    returns_desc='dict {"source": 源对象名, "target": 目标对象名, "constraint": "Orientation_Constraint"}',
    prerequisites=[
        'source_name 和 target_name 必须已存在于场景中',
    ],
    run_on_main_thread=True,
)
def add_orientation_constraint(source_name: str, target_name: str, weight: float = 1.0):
    """添加 Orientation 约束。"""
    _ensure_in_max()
    src = _get_node(source_name)
    tgt = _get_node(target_name)
    constraint = rt.Orientation_Constraint()
    constraint.appendTarget(tgt, weight)
    src.rotation.controller = constraint
    return {
        "source": source_name,
        "target": target_name,
        "constraint": "Orientation_Constraint",
    }


@tool(
    dcc=['3dsmax'],
    description=(
        "查询对象当前的控制器栈。"
    ),
    category="animation",
    examples=[
        {
            'summary': '查询 Box01 的 position/rotation/scale 控制器',
            'args': {'name': 'Box01'},
        },
    ],
    notes=[
        "返回的 class 字段显示控制器类名，例如 'Position_XYZ' 或 'Position_Constraint'。",
        "若某类控制器获取失败，结果中会包含 error 字段说明原因。",
        "该工具只查询不修改场景。",
    ],
    returns_desc='dict {"name": 对象名, "controllers": {"position": ..., "rotation": ..., "scale": ...}}',
    prerequisites=['对象 name 必须已存在于场景中'],
    run_on_main_thread=True,
)
def get_controllers(name: str):
    """获取对象的 position/rotation/scale 控制器信息。"""
    _ensure_in_max()
    node = _get_node(name)
    out = {}
    for ctrl_name in ("position", "rotation", "scale"):
        try:
            ctrl = _get_controller(node, ctrl_name)
            out[ctrl_name] = {
                "class": str(rt.classOf(ctrl)),
                "name": str(ctrl.name) if hasattr(ctrl, "name") else None,
            }
        except Exception as exc:  # pylint: disable=broad-except
            out[ctrl_name] = {"error": str(exc)}
    return {"name": name, "controllers": out}


# ---------------------------------------------------------------------- #
# 参数线（Wire Parameters）
# ---------------------------------------------------------------------- #

@tool(
    dcc=['3dsmax'],
    description=(
        "在两个对象的属性之间建立参数线（Wire Parameter）。"
        "例如让 'Wheel.rotation.x' 驱动 'Car.position.x'。"
    ),
    category="animation",
    examples=[
        {
            'summary': '让 Wheel 的旋转 X 驱动 Car 的位置 X',
            'args': {
                'source_name': 'Wheel',
                'source_prop': 'rotation.x',
                'target_name': 'Car',
                'target_prop': 'position.x',
            },
        },
        {
            'summary': '建立带表达式的双向参数线',
            'args': {
                'source_name': 'Slider',
                'source_prop': 'position.x',
                'target_name': 'Door',
                'target_prop': 'rotation.z',
                'expression': 'value * 2',
                'bidirectional': True,
            },
        },
    ],
    notes=[
        "source_prop / target_prop 使用点分路径，例如 'rotation.x'、'position.y'。",
        "expression 使用源属性值作为 'value'，例如 'value * 2'。",
        "bidirectional 仅作为记录，当前实现仍为单向连接。",
    ],
    returns_desc='dict {"source": 源属性路径, "target": 目标属性路径, "expression": 表达式, "bidirectional": 是否双向}',
    prerequisites=[
        'source_name 和 target_name 必须已存在于场景中',
    ],
    run_on_main_thread=True,
)
def wire_parameter(
    source_name: str,
    source_prop: str,
    target_name: str,
    target_prop: str,
    expression: str = "",
    bidirectional: bool = False,
):
    """建立参数线。

    :param source_name: 源对象名
    :param source_prop: 源属性路径，如 "rotation.x"
    :param target_name: 目标对象名
    :param target_prop: 目标属性路径，如 "position.x"
    :param expression: 可选表达式，例如 "value * 2"
    :param bidirectional: 是否双向
    :returns: dict 描述结果
    """
    _ensure_in_max()
    src = _get_node(source_name)
    tgt = _get_node(target_name)

    src_path = [source_name] + source_prop.split(".")
    tgt_path = [target_name] + target_prop.split(".")

    src_param = rt.paramWire.connect(src, src_path, "#(")
    if src_param is None:
        raise ValueError("源属性连接失败: {}.{}".format(source_name, source_prop))
    tgt_param = rt.paramWire.connect(tgt, tgt_path, "#)")
    if tgt_param is None:
        raise ValueError("目标属性连接失败: {}.{}".format(target_name, target_prop))

    if expression:
        tgt_param.controller.expression = expression

    return {
        "source": "{}.{}".format(source_name, source_prop),
        "target": "{}.{}".format(target_name, target_prop),
        "expression": expression,
        "bidirectional": bidirectional,
    }


@tool(
    dcc=['3dsmax'],
    description="查询对象现有的参数线连接。",
    category="animation",
    examples=[
        {
            'summary': '列出 Box01 现有的参数线连接',
            'args': {'name': 'Box01'},
        },
    ],
    notes=[
        "返回的 from/to 字段分别表示连接的来源和目标对象名。",
        "若对象没有参数线，count 为 0，connections 为空列表。",
        "该工具只查询不修改场景。",
    ],
    returns_desc='dict {"name": 对象名, "count": 连接数, "connections": [...]}',
    prerequisites=['对象 name 必须已存在于场景中'],
    run_on_main_thread=True,
)
def list_wire_parameters(name: str):
    """列出对象的参数线连接。"""
    _ensure_in_max()
    node = _get_node(name)
    connections = []
    try:
        for wire in node.wireparams:
            try:
                connections.append({
                    "from": str(wire.fromNode.name),
                    "to": str(wire.toNode.name),
                    "from_prop": str(wire.fromParam),
                    "to_prop": str(wire.toParam),
                })
            except Exception:  # pylint: disable=broad-except
                continue
    except Exception:  # pylint: disable=broad-except
        pass
    return {"name": name, "count": len(connections), "connections": connections}


# ---------------------------------------------------------------------- #
# 时间控制
# ---------------------------------------------------------------------- #

@tool(
    dcc=['3dsmax'],
    description="设置动画时间范围（start/end 帧）。",
    category='animation',
    examples=[
        {'summary': '设置动画时间范围为 0 到 120 帧', 'args': {'start': 0, 'end': 120}},
    ],
    notes=[
        'start 必须小于或等于 end。',
        '设置后会同步更新时间滑块的有效范围。',
    ],
    returns_desc='dict {"start": 起始帧, "end": 结束帧}',
    run_on_main_thread=True,
)
def set_time_range(start: int, end: int):

    """设置动画范围。"""
    _ensure_in_max()
    rt.animationRange = rt.interval(start, end)
    return {"start": start, "end": end}


@tool(
    dcc=['3dsmax'],
    description="设置当前时间到指定帧。",
    category='animation',
    examples=[
        {'summary': '跳到第 30 帧', 'args': {'frame': 30}},
    ],
    notes=[
        'frame 必须在当前动画时间范围内。',
        '设置后时间滑块和场景当前帧会同步更新。',
    ],
    returns_desc='dict {"frame": 当前帧号}',
    run_on_main_thread=True,
)
def set_current_frame(frame: int):

    """设置当前帧。"""
    _ensure_in_max()
    # 实测：rt.currentTime 不改变时间滑块，rt.sliderTime 才同步 UI 与场景
    rt.sliderTime = frame
    return {"frame": frame}


@tool(
    dcc=['3dsmax'],
    description="播放/停止动画。",
    category='animation',
    examples=[
        {'summary': '开始播放动画', 'args': {'play': True}},
        {'summary': '停止播放动画', 'args': {'play': False}},
    ],
    notes=[
        'play=True 开始播放，play=False 停止播放。',
        '播放会在 Max 主线程阻塞执行，直到用户手动停止或播放结束。',
    ],
    returns_desc='dict {"playing": True/False}',
    run_on_main_thread=True,
)
def play_animation(play: bool = True):

    """播放或停止动画。"""
    _ensure_in_max()
    if play:
        rt.playanimation()
        return {"playing": True}
    rt.stopanimation()
    return {"playing": False}


# ---------------------------------------------------------------------- #
# 烘焙
# ---------------------------------------------------------------------- #

@tool(
    dcc=['3dsmax'],
    description=(
        "烘焙对象指定帧范围的动画关键帧（塌陷约束/参数线为关键帧）。"
        "默认烘焙 position/rotation/scale。"
    ),
    category="animation",
    examples=[
        {
            'summary': '烘焙 Box01 当前时间范围的动画',
            'args': {'name': 'Box01'},
        },
        {
            'summary': '指定范围和步长进行烘焙',
            'args': {'name': 'Box01', 'start': 0, 'end': 100, 'step': 2},
        },
    ],
    notes=[
        "不传 start/end 时，使用当前 animationRange 作为烘焙范围。",
        "step 为采样步长（帧），值越大关键帧越稀疏。",
        "烘焙会塌陷 position/rotation/scale 控制器，约束和参数线将失效。",
    ],
    returns_desc='dict {"name": 对象名, "start": 开始帧, "end": 结束帧, "step": 步长, "baked": true}',
    prerequisites=['对象 name 必须已存在于场景中'],
    run_on_main_thread=True,
)
def bake_animation(
    name: str,
    start: Optional[int] = None,
    end: Optional[int] = None,
    step: int = 1,
):
    """烘焙动画。

    :param name: 对象名
    :param start: 开始帧，默认使用 animationRange.start
    :param end: 结束帧，默认使用 animationRange.end
    :param step: 采样步长（帧）
    """
    _ensure_in_max()
    node = _get_node(name)
    if start is None:
        start = int(rt.animationRange.start.frame)
    if end is None:
        end = int(rt.animationRange.end.frame)

    rt.select(node)
    rt.execute('''
    (
        local startFrame = {0}
        local endFrame = {1}
        local s = {2}
        local obj = selection[1]
        local bakeKeys = #(obj.pos.controller, obj.rotation.controller, obj.scale.controller)
        for c in bakeKeys do (
            if c != undefined then (
                for f = startFrame to endFrame by s do (
                    at time f (
                        addKnot c #linear #corner (getKeyTime c 1)
                    )
                )
            )
        )
        "ok"
    )
    '''.format(start, end, step))
    return {
        "name": name,
        "start": start,
        "end": end,
        "step": step,
        "baked": True,
    }


__all__ = [
    "set_keyframe",
    "delete_keyframe",
    "get_keyframe_value",
    "add_lookat_constraint",
    "add_position_constraint",
    "add_orientation_constraint",
    "get_controllers",
    "wire_parameter",
    "list_wire_parameters",
    "set_time_range",
    "set_current_frame",
    "play_animation",
    "bake_animation",
]
