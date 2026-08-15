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

from ..logger import get_logger
from ..runtime_helpers import IN_MAX
from ..runtime_helpers import rt
from .registry import tool


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

    pymxs 中 ``node.pos.controller`` 等点号访问不稳定，容易报 AttributeError。
    经过回归测试验证，通过 ``rt.execute`` 在 MAXScript 环境里访问
    ``(getNodeByName \"...\").pos.controller`` 最为可靠。

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

    node_name = str(node.name).replace('"', '\\"')
    script = 'ctrl = (getNodeByName "{}").{}.controller\nctrl'.format(
        node_name, mxs_prop,
    )
    try:
        ctrl = rt.execute(script)
    except Exception as exc:  # pylint: disable=broad-except
        raise ValueError(
            "获取控制器失败: {}.{} ({})".format(node.name, controller_name, exc),
        ) from exc
    if ctrl is None:
        raise ValueError("控制器为空: {}.{}".format(node.name, controller_name))
    return ctrl


# ---------------------------------------------------------------------- #
# 关键帧
# ---------------------------------------------------------------------- #

@tool(
    description=(
        "给对象在指定帧设置关键帧。"
        "controller 可选 'position'/'rotation'/'scale'/'transform'，"
        "不传则对所有 transform 属性打关键帧。"
    ),
    category="animation",
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
        controllers = ["pos", "rotation", "scale"]
    elif controller in ("position", "rotation", "scale"):
        controllers = [controller]
    else:
        raise ValueError(
            "controller 必须是 position/rotation/scale/transform 之一",
        )

    node_name = str(node.name).replace('"', '\\"')
    for ctrl_name in controllers:
        mxs_prop = "pos" if ctrl_name == "position" else ctrl_name
        script = 'addNewKey (getNodeByName "{}").{}.controller {}'.format(
            node_name, mxs_prop, frame,
        )
        try:
            rt.execute(script)
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
    description="删除对象指定帧的关键帧。controller 不传则删除 transform 关键帧。",
    category="animation",
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
        controllers = ["pos", "rotation", "scale"]
    elif controller in ("position", "rotation", "scale"):
        controllers = [controller]
    else:
        raise ValueError(
            "controller 必须是 position/rotation/scale/transform 之一",
        )

    node_name = str(node.name).replace('"', '\\"')
    for ctrl_name in controllers:
        mxs_prop = "pos" if ctrl_name == "position" else ctrl_name
        # deleteKey 删除控制器在指定帧的关键帧；若该帧无关键帧不报错
        script = 'deleteKey (getNodeByName "{}").{}.controller {}'.format(
            node_name, mxs_prop, frame,
        )
        try:
            rt.execute(script)
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
    description="获取对象在指定帧的 transform 关键帧值（如果有的话）。",
    category="animation",
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

    node_name = str(node.name).replace('"', '\\"')
    mxs_prop = "pos" if controller == "position" else controller
    script = (
        'at time {frame} val = (getNodeByName "{name}").{prop}.value\nval'
    ).format(frame=frame, name=node_name, prop=mxs_prop)
    try:
        val = rt.execute(script)
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
    description=(
        "给对象添加 LookAt 约束，使其始终朝向目标对象。"
        "常用于眼睛注视、武器瞄准、灯光跟随等。"
    ),
    category="animation",
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
    description=(
        "给对象添加 Position 约束，使其位置跟随目标对象。"
    ),
    category="animation",
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
    description=(
        "给对象添加 Orientation 约束，使其旋转跟随目标对象。"
    ),
    category="animation",
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
    description=(
        "查询对象当前的控制器栈。"
    ),
    category="animation",
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
    description=(
        "在两个对象的属性之间建立参数线（Wire Parameter）。"
        "例如让 'Wheel.rotation.x' 驱动 'Car.position.x'。"
    ),
    category="animation",
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
    description="查询对象现有的参数线连接。",
    category="animation",
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
    description="设置动画时间范围（start/end 帧）。",
    category="animation",
    run_on_main_thread=True,
)
def set_time_range(start: int, end: int):
    """设置动画范围。"""
    _ensure_in_max()
    rt.animationRange = rt.interval(start, end)
    return {"start": start, "end": end}


@tool(
    description="设置当前时间到指定帧。",
    category="animation",
    run_on_main_thread=True,
)
def set_current_frame(frame: int):
    """设置当前帧。"""
    _ensure_in_max()
    rt.currentTime = frame
    return {"frame": frame}


@tool(
    description="播放/停止动画。",
    category="animation",
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
    description=(
        "烘焙对象指定帧范围的动画关键帧（塌陷约束/参数线为关键帧）。"
        "默认烘焙 position/rotation/scale。"
    ),
    category="animation",
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
