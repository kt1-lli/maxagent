#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GT Tools 精华移植（来源：github.com/TrevisanGMW/gt-tools，MIT 许可）。

从 gt-tools 抽取适合 AI 调用的纯 cmds 算法，按 MaxAgent @tool 规范封装：
- rename_gt_batch：批量重命名（大小写/搜索替换/编号/字母/前后缀）
- match_gt_transform：对齐变换（免约束版 point/orient/scale 匹配）
- reset_gt_transforms：安全归零变换（跳过有连接/锁定的通道）
- orient_gt_joints：批量定向骨骼（aimConstraint + 翻转一致性）
- make_gt_stretchy_ik：IK 拉伸系统（测量距离驱动 scaleX）
- add_gt_sine_attributes：正弦驱动属性网络（time 驱动往复动画）
- transfer_gt_uvs：网格间 UV 传递（含中间对象处理）
- connect_gt_attributes：批量属性连接（经 utility 节点/反向）
- bake_gt_world_space：世界空间动画采样与重烘焙
- stagger_gt_keyframes：按选择顺序错帧（stagger 朋克曲线）

移植原则（与 gt-tools 原实现保持一致的算法语义）：
- 只依赖延迟导入的 maya.cmds，不依赖 gt 包本体，保证 CI 可 import。
- 破坏性操作包 undo chunk；结果返回结构化 dict 供 LLM 判断。
- rename 列表操作按 gt-tools 习惯倒序应用，降低重名冲突概率。
"""

from __future__ import absolute_import
from __future__ import print_function

import re
from typing import Any
from typing import Dict
from typing import List
from typing import Tuple

from ...tools.registry import tool
from ._common import _ensure_in_maya
from ...dcc.runtime import run_on_main


def _cmds():
    """延迟导入 maya.cmds。"""
    import maya.cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
    return maya.cmds


def _normalize_list(value):
    # type: (Any) -> List[str]
    """把 str / list / tuple 统一为 list[str]（逗号分号均可分隔）。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(x).strip() for x in value if str(x).strip()]
    text = str(value).strip()
    if not text:
        return []
    parts = re.split(r'[,;，；]', text)
    return [p.strip() for p in parts if p.strip()]


def _short_name(obj):
    # type: (str) -> str
    """从 DAG 长名取短名（gt-tools get_short_name 语义）。"""
    return str(obj).split('|')[-1]


def _is_renamable(cmds, obj):
    # type: (object, str) -> bool
    """shape 节点不可直接重命名（跟随 transform），与 gt-tools 一致。"""
    if not cmds.objExists(obj):
        return False
    inherited = cmds.nodeType(obj, inherited=True) or []
    return 'shape' not in inherited


def _open_chunk(cmds, name):
    # type: (object, str) -> None
    """打开 undo chunk（破坏性操作统一入口）。"""
    cmds.undoInfo(openChunk=True, chunkName=name)


def _close_chunk(cmds, name):
    # type: (object, str) -> None
    """关闭 undo chunk。"""
    cmds.undoInfo(closeChunk=True, chunkName=name)


def _apply_rename_pairs(cmds, pairs):
    # type: (object, List[Tuple[str, str]]) -> Tuple[int, List[str]]
    """按 gt-tools 习惯倒序应用重命名，返回 (成功数, 失败列表)。"""
    ok = 0
    failed = []
    for old_name, new_name in reversed(pairs):
        if not cmds.objExists(old_name):
            failed.append('{}: 节点已不存在'.format(old_name))
            continue
        try:
            cmds.rename(old_name, new_name)
            ok += 1
        except Exception as exc:  # pylint: disable=broad-except
            failed.append('{} -> {}: {}'.format(old_name, new_name, exc))
    return (ok, failed)


# ---------------------------------------------------------------------- #
# 1. 批量重命名
# ---------------------------------------------------------------------- #
def _increment_letter_suffix(suffix):
    # type: (str) -> str
    """字母后缀递增：A->B ... Z->AA（gt-tools 同名函数语义）。"""
    if not suffix:
        return 'A'
    left = suffix.rstrip('Z')
    z_count = len(suffix) - len(left)
    if left:
        return left[:-1] + chr(ord(left[-1]) + 1) + 'Z' * z_count
    return 'A' * (z_count + 1)


def _build_rename_pairs(cmds, objects, mode, **kwargs):
    # type: (object, List[str], str, Any) -> Tuple[List[Tuple[str, str]], int]
    """按 mode 生成 (旧名, 新名) 对。返回 (pairs, 跳过数)。

    kwargs 视 mode 而定：search/replace、name/start/padding、
    prefix/suffix、case、keep_name。
    """
    pairs = []
    skipped = 0
    if mode == 'case':
        case = str(kwargs.get('case', 'upper'))
        for obj in objects:
            short = _short_name(obj)
            if case == 'upper':
                new_name = short.upper()
            elif case == 'lower':
                new_name = short.lower()
            else:
                # capitalize：下划线分段的每段首字母大写（gt-tools 语义）
                parts = short.split('_')
                if len(parts) > 1:
                    new_name = '_'.join(p.capitalize() for p in parts)
                else:
                    new_name = short.capitalize()
            if _is_renamable(cmds, obj) and short != new_name:
                pairs.append((obj, new_name))
            else:
                skipped += 1
    elif mode == 'search_replace':
        search = str(kwargs.get('search', ''))
        replace = str(kwargs.get('replace', ''))
        if not search:
            raise ValueError('search 不能为空')
        for obj in objects:
            short = _short_name(obj)
            new_name = short.replace(search, replace)
            if _is_renamable(cmds, obj) and short != new_name:
                pairs.append((obj, new_name))
            else:
                skipped += 1
    elif mode == 'number':
        base = str(kwargs.get('name', ''))
        keep_name = bool(kwargs.get('keep_name', False))
        if not base and not keep_name:
            raise ValueError('name 不能为空（keep_name=True 时可留空）')
        start = int(kwargs.get('start', 1))
        padding = max(1, int(kwargs.get('padding', 3)))
        count = start
        for obj in objects:
            short = _short_name(obj)
            base_name = short if keep_name else base
            new_name = base_name + str(count).zfill(padding)
            if _is_renamable(cmds, obj):
                pairs.append((obj, new_name))
                count += 1
            else:
                skipped += 1
    elif mode == 'letter':
        base = str(kwargs.get('name', ''))
        keep_name = bool(kwargs.get('keep_name', False))
        upper = bool(kwargs.get('upper', True))
        if not base and not keep_name:
            raise ValueError('name 不能为空（keep_name=True 时可留空）')
        suffix = 'A'
        for obj in objects:
            short = _short_name(obj)
            base_name = short if keep_name else base
            sfx = suffix if upper else suffix.lower()
            new_name = base_name + sfx
            if _is_renamable(cmds, obj):
                pairs.append((obj, new_name))
                suffix = _increment_letter_suffix(suffix)
            else:
                skipped += 1
    elif mode == 'prefix':
        prefix = str(kwargs.get('prefix', ''))
        if not prefix:
            raise ValueError('prefix 不能为空')
        for obj in objects:
            short = _short_name(obj)
            # 已带该前缀则跳过，避免重复叠加（gt-tools 语义）
            new_name = short if short.startswith(prefix) else prefix + short
            if _is_renamable(cmds, obj) and short != new_name:
                pairs.append((obj, new_name))
            else:
                skipped += 1
    elif mode == 'suffix':
        suffix = str(kwargs.get('suffix', ''))
        if not suffix:
            raise ValueError('suffix 不能为空')
        for obj in objects:
            short = _short_name(obj)
            if short.endswith(suffix):
                new_name = short
            else:
                # 后缀插在数字序号之前：cube01 -> cubeR01（gt-tools 语义）
                match = re.match(r'^(.*?)(\d+)$', short)
                if match:
                    new_name = match.group(1) + suffix + match.group(2)
                else:
                    new_name = short + suffix
            if _is_renamable(cmds, obj) and short != new_name:
                pairs.append((obj, new_name))
            else:
                skipped += 1
    else:
        raise ValueError('不支持的 mode: {}'.format(mode))
    return (pairs, skipped)


@tool(
    dcc=['maya'],
    description='批量重命名 Maya 对象（GT Tools Renamer 精华移植）。'
                '支持大小写转换、搜索替换、序号/字母后缀、前后缀添加。',
    category='high_level',
    examples=[
        {
            'summary': '把选中的骨骼统一命名并编号',
            'args': {
                'objects': 'arm_01,arm_02,arm_03',
                'mode': 'number',
                'name': 'L_arm_',
                'start': 1,
                'padding': 2,
            },
        },
        {
            'summary': '把名字里的 _L 替换成 _R（左右镜像命名）',
            'args': {
                'objects': ['L_arm_L_01'],
                'mode': 'search_replace',
                'search': '_L',
                'replace': '_R',
            },
        },
    ],
    notes=[
        'objects 支持列表或逗号/分号分隔字符串。',
        'mode 可选：case（大小写）/ search_replace / number / letter / prefix / suffix。',
        'case=capitalize 时按下划线分段每段首字母大写：L_arm -> L_Arm。',
        'suffix 会插在数字序号之前：cube01 加 _R 后变成 cubeR01。',
        'shape 节点不可直接重命名，会被自动跳过。',
    ],
    returns_desc='dict {"ok": True, "renamed": 数量, "skipped": 跳过数, "failed": [失败项], "pairs": [(旧, 新)]}',
    prerequisites=['objects 中的节点必须存在于当前场景'],
)
def rename_gt_batch(
    objects,
    mode='number',
    search='',
    replace='',
    name='',
    start=1,
    padding=3,
    prefix='',
    suffix='',
    case='upper',
    keep_name=False,
    upper=True,
):
    # type: (Any, str, str, str, str, int, int, str, str, str, bool, bool) -> Dict[str, Any]
    """批量重命名对象。

    :param objects: 对象名列表或逗号/分号分隔字符串
    :param mode: 重命名模式（case / search_replace / number / letter / prefix / suffix）
    :param search: mode=search_replace 时的搜索文本
    :param replace: mode=search_replace 时的替换文本
    :param name: mode=number/letter 时的基础名
    :param start: mode=number 时的起始编号
    :param padding: mode=number 时的编号补零宽度
    :param prefix: mode=prefix 时的前缀
    :param suffix: mode=suffix 时的后缀
    :param case: mode=case 时的转换方式（upper / lower / capitalize）
    :param keep_name: mode=number/letter 时保留原名作为基础名
    :param upper: mode=letter 时后缀是否大写
    :returns: dict {"ok": True, "renamed": ..., "skipped": ..., "failed": [...], "pairs": [...]}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        objs = _normalize_list(objects)
        if not objs:
            raise ValueError('必须指定要重命名的对象')
        pairs, skipped = _build_rename_pairs(
            cmds, objs, mode,
            search=search, replace=replace, name=name,
            start=start, padding=padding, prefix=prefix,
            suffix=suffix, case=case, keep_name=keep_name, upper=upper,
        )
        _open_chunk(cmds, 'GT Batch Rename')
        try:
            ok, failed = _apply_rename_pairs(cmds, pairs)
        finally:
            _close_chunk(cmds, 'GT Batch Rename')
        return {
            'ok': True,
            'renamed': ok,
            'skipped': skipped,
            'failed': failed,
            'pairs': pairs,
        }

    return run_on_main(_do)


# ---------------------------------------------------------------------- #
# 2. 变换对齐（免约束）
# ---------------------------------------------------------------------- #
_AXIS_INDEX = {'x': 0, 'y': 1, 'z': 2}


def _overwrite_xyz(source_xyz, target_xyz, skip):
    # type: (List[float], List[float], Any) -> List[float]
    """用 source 的值覆盖 target，skip 的轴保留 target 原值。"""
    skips = set()
    for axis in _normalize_list(skip):
        skips.add(_AXIS_INDEX.get(str(axis).lower(), -1))
    return [
        target_xyz[i] if i in skips else source_xyz[i]
        for i in range(3)
    ]


@tool(
    dcc=['maya'],
    description='把源对象的位移/旋转/缩放对齐到目标对象（GT Tools match_transform 移植）。'
                '等价于一次性 point/orient/scale 匹配但不产生约束节点。',
    category='high_level',
    examples=[
        {
            'summary': '把 loc_A 的位移旋转对齐到 cube1，但不动 Z 轴',
            'args': {
                'source': 'loc_A',
                'targets': 'cube1,cube2',
                'translate': True,
                'rotate': True,
                'skip_translate': 'z',
            },
        },
    ],
    notes=[
        '直接写 transform 值，不创建约束节点；后续可自由 keyframe。',
        'skip_translate / skip_rotate / skip_scale 可跳过指定轴（x/y/z）。',
        '位移按 rotatePivot（旋转枢轴）对齐，与 Maya 约束行为一致。',
    ],
    returns_desc='dict {"ok": True, "source": 源, "matched": [目标列表]}',
    prerequisites=['source 与 targets 必须存在于当前场景'],
)
def match_gt_transform(
    source,
    targets,
    translate=True,
    rotate=True,
    scale=True,
    skip_translate=None,
    skip_rotate=None,
    skip_scale=None,
):
    # type: (str, Any, bool, bool, bool, Any, Any, Any) -> Dict[str, Any]
    """对齐源对象的变换到目标对象。

    :param source: 源对象名
    :param targets: 目标对象名列表或逗号分隔字符串
    :param translate: 是否对齐位移
    :param rotate: 是否对齐旋转
    :param scale: 是否对齐缩放
    :param skip_translate: 位移要跳过的轴（x/y/z）
    :param skip_rotate: 旋转要跳过的轴
    :param skip_scale: 缩放要跳过的轴
    :returns: dict {"ok": True, "source": ..., "matched": [...]}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        if not source or not cmds.objExists(source):
            raise ValueError('源对象不存在: {}'.format(source))
        tgt = _normalize_list(targets)
        if not tgt:
            raise ValueError('必须指定目标对象')
        matched = []
        _open_chunk(cmds, 'GT Match Transform')
        try:
            src_t = list(cmds.xform(source, query=True, rotatePivot=True, worldSpace=True))
            src_r = list(cmds.xform(source, query=True, rotation=True, worldSpace=True))
            src_s = list(cmds.xform(source, query=True, scale=True, worldSpace=True))
            for target in tgt:
                if not target or not cmds.objExists(target):
                    continue
                if translate:
                    t = list(cmds.xform(target, query=True, translation=True, worldSpace=True))
                    cmds.xform(
                        target, worldSpace=True,
                        translation=_overwrite_xyz(src_t, t, skip_translate),
                    )
                if rotate:
                    r = list(cmds.xform(target, query=True, rotation=True, worldSpace=True))
                    cmds.xform(
                        target, worldSpace=True,
                        rotation=_overwrite_xyz(src_r, r, skip_rotate),
                    )
                if scale:
                    s = list(cmds.xform(target, query=True, scale=True, worldSpace=True))
                    cmds.xform(
                        target, worldSpace=True,
                        scale=_overwrite_xyz(src_s, s, skip_scale),
                    )
                matched.append(target)
        finally:
            _close_chunk(cmds, 'GT Match Transform')
        return {'ok': True, 'source': source, 'matched': matched}

    return run_on_main(_do)


# ---------------------------------------------------------------------- #
# 3. 安全归零变换
# ---------------------------------------------------------------------- #
def _reset_channel_safe(cmds, obj, channel, default_value):
    # type: (object, str, str, float) -> bool
    """通道无连接且未锁定时才重置，返回是否真的重置。"""
    incoming = cmds.listConnections(
        '{}.{}'.format(obj, channel), destination=False, source=True,
    )
    if incoming:
        return False
    if cmds.getAttr('{}.{}'.format(obj, channel), lock=True):
        return False
    cmds.setAttr('{}.{}'.format(obj, channel), default_value)
    return True


@tool(
    dcc=['maya'],
    description='安全归零/复位 Maya 对象的变换（GT Tools reset_transforms 移植）。'
                '有连接或锁定的通道自动跳过，关节的位移默认不动。',
    category='high_level',
    examples=[
        {
            'summary': '复位控制器的旋转缩放（保留位移）',
            'args': {'objects': 'ctrl_main,ctrl_head', 'reset_translate': False},
        },
    ],
    notes=[
        '有输入连接或被锁定的通道自动跳过，不会破坏节点网络。',
        'joint 的 translate 默认不复位（关节位移承载骨骼长度）。',
        '旋转复位为 0，缩放复位为 1，位移复位为 0。',
    ],
    returns_desc='dict {"ok": True, "reset_objects": 数量, "skipped_channels": 总跳过数}',
)
def reset_gt_transforms(objects, reset_translate=True):
    # type: (Any, bool) -> Dict[str, Any]
    """安全复位对象变换。

    :param objects: 对象名列表或逗号分隔字符串
    :param reset_translate: 是否复位位移（joint 永远跳过位移）
    :returns: dict {"ok": True, "reset_objects": ..., "skipped_channels": ...}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        objs = _normalize_list(objects)
        if not objs:
            raise ValueError('必须指定对象')
        reset_objects = 0
        skipped = 0
        _open_chunk(cmds, 'GT Reset Transforms')
        try:
            for obj in objs:
                if not cmds.objExists(obj):
                    continue
                # 关节跳过位移：复位会让骨骼长度塌缩
                is_joint = cmds.nodeType(obj) == 'joint'
                do_translate = reset_translate and not is_joint
                local_skip = 0
                for ch, default in (
                    ('translateX', 0.0), ('translateY', 0.0), ('translateZ', 0.0),
                    ('rotateX', 0.0), ('rotateY', 0.0), ('rotateZ', 0.0),
                    ('scaleX', 1.0), ('scaleY', 1.0), ('scaleZ', 1.0),
                ):
                    if ch.startswith('translate') and not do_translate:
                        # joint 位移承载骨骼长度，跳过但计入统计
                        local_skip += 1
                        continue
                    if not _reset_channel_safe(cmds, obj, ch, default):
                        local_skip += 1
                skipped += local_skip
                reset_objects += 1
        finally:
            _close_chunk(cmds, 'GT Reset Transforms')
        return {
            'ok': True,
            'reset_objects': reset_objects,
            'skipped_channels': skipped,
        }

    return run_on_main(_do)


# ---------------------------------------------------------------------- #
# 4. 批量定向骨骼
# ---------------------------------------------------------------------- #
def _axis_tuple(value, default):
    # type: (Any, Tuple[int, int, int]) -> Tuple[int, int, int]
    """把 'x'/'-x'/'y' 等转换为带符号轴向量元组。"""
    text = str(value or '').strip().lower()
    if not text:
        return default
    sign = -1 if text.startswith('-') else 1
    axis = text.lstrip('+-')
    if axis not in _AXIS_INDEX:
        return default
    vec = [0, 0, 0]
    vec[_AXIS_INDEX[axis]] = sign
    return (vec[0], vec[1], vec[2])


def _orient_one_joint(cmds, jnt, aim, up, up_vec):
    # type: (object, str, Tuple[int, int, int], Tuple[int, int, int], Tuple[int, int, int]) -> bool
    """定向单个骨骼：aim 到子关节，无子关节时对齐父级方向。

    返回是否执行了 aim 定向（False 表示走了 orientConstraint 兜底）。
    """
    parent = cmds.listRelatives(jnt, parent=True, fullPath=True) or []
    parent = parent[0] if parent else ''
    children = cmds.listRelatives(jnt, children=True, fullPath=True) or []
    # 世界级断开子级，定向后再接回，保证约束计算不受现有父级影响
    world_children = []
    if children:
        world_children = cmds.parent(children, world=True) or []
    aim_target = ''
    for child in world_children:
        if cmds.nodeType(child) == 'joint':
            aim_target = child
            break
    try:
        if aim_target:
            constraint = cmds.aimConstraint(
                aim_target, jnt,
                aim=aim, upVector=up, worldUpVector=up_vec,
                worldUpType='vector',
            )
            cmds.delete(constraint)
            oriented = True
        elif parent:
            constraint = cmds.orientConstraint(parent, jnt, weight=1)
            cmds.delete(constraint)
            oriented = False
        else:
            oriented = False
        # 清零 jointOrient 之外的方向残留，冻结变换
        cmds.joint(jnt, edit=True, zeroScaleOrient=True)
        cmds.makeIdentity(jnt, apply=True)
        return oriented
    finally:
        if world_children:
            cmds.parent(world_children, jnt)


@tool(
    dcc=['maya'],
    description='批量定向 Maya 骨骼链（GT Tools Orient Joints 移植）。'
                '让每根骨骼的 aim 轴指向子关节，统一整条链的旋转轴向。',
    category='rigging',
    examples=[
        {
            'summary': '沿 X 轴正向、Y 轴朝上定向整条手臂链',
            'args': {'joints': 'shoulder,elbow,wrist', 'aim_axis': 'x', 'up_axis': 'y'},
        },
    ],
    notes=[
        'joints 传链上任意多根骨骼（通常传全部，含末端）。',
        'aim_axis 决定骨骼哪根轴指向子关节（绑定习惯常用 x 或 -x）。',
        '末端骨骼没有子关节，自动对齐父级方向兜底。',
        '定向后 makeIdentity 冻结变换，jointOrient 承载全部旋转。',
    ],
    returns_desc='dict {"ok": True, "oriented": 数量, "joints": [处理列表]}',
    prerequisites=['joints 必须是场景中的 joint 节点'],
)
def orient_gt_joints(
    joints,
    aim_axis='x',
    up_axis='y',
    up_vector='y',
):
    # type: (Any, str, str, str) -> Dict[str, Any]
    """批量定向骨骼。

    :param joints: 骨骼名列表或逗号分隔字符串
    :param aim_axis: 瞄准轴（+x/-x/+y/-y/+z/-z，指向子关节）
    :param up_axis: 上方向轴（骨骼哪根轴朝上）
    :param up_vector: 世界 上方向向量（x/y/z，配合 up_axis）
    :returns: dict {"ok": True, "oriented": ..., "joints": [...]}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        jnts = _normalize_list(joints)
        if not jnts:
            raise ValueError('必须指定骨骼')
        invalid = [j for j in jnts if not cmds.objExists(j)]
        if invalid:
            raise ValueError('骨骼不存在: {}'.format(', '.join(invalid)))
        aim = _axis_tuple(aim_axis, (1, 0, 0))
        up = _axis_tuple(up_axis, (0, 1, 0))
        up_vec = _axis_tuple(up_vector, (0, 1, 0))
        done = []
        _open_chunk(cmds, 'GT Orient Joints')
        try:
            for jnt in jnts:
                _orient_one_joint(cmds, jnt, aim, up, up_vec)
                done.append(jnt)
        finally:
            _close_chunk(cmds, 'GT Orient Joints')
        return {'ok': True, 'oriented': len(done), 'joints': done}

    return run_on_main(_do)


# ---------------------------------------------------------------------- #
# 5. IK 拉伸系统
# ---------------------------------------------------------------------- #
def _int_to_en(num):
    # type: (int) -> str
    """整数转英文单词（gt-tools 同名函数，用于节点命名）。"""
    words = {
        0: 'zero', 1: 'one', 2: 'two', 3: 'three', 4: 'four',
        5: 'five', 6: 'six', 7: 'seven', 8: 'eight', 9: 'nine',
        10: 'ten', 11: 'eleven', 12: 'twelve', 13: 'thirteen',
        14: 'fourteen', 15: 'fifteen', 16: 'sixteen', 17: 'seventeen',
        18: 'eighteen', 19: 'nineteen', 20: 'twenty', 30: 'thirty',
        40: 'forty', 50: 'fifty', 60: 'sixty', 70: 'seventy',
        80: 'eighty', 90: 'ninety',
    }
    if num < 20:
        return words[num]
    if num < 100:
        if num % 10 == 0:
            return words[num]
        return words[num // 10 * 10] + '-' + words[num % 10]
    if num < 1000:
        if num % 100 == 0:
            return words[num // 100] + ' hundred'
        return words[num // 100] + ' hundred and ' + _int_to_en(num % 100)
    return str(num)


@tool(
    dcc=['maya'],
    description='给 IK 手柄创建拉伸系统（GT Tools Make Stretchy IK 移植）。'
                '用测量距离自动驱动骨骼 scaleX，实现拉伸/挤压（squash & stretch）。',
    category='rigging',
    examples=[
        {
            'summary': '给手臂 IK 加拉伸，属性挂在控制器上',
            'args': {'ik_handle': 'ikHandle_arm', 'name': 'arm', 'attribute_holder': 'ctrl_arm'},
        },
    ],
    notes=[
        '会创建测量节点与分组 <name>_stretchy_grp，收纳全部辅助节点。',
        'attribute_holder 存在时额外加 stretch / squash / saveVolume 等控制属性。',
        '无 attribute_holder 时拉伸永远开启（scaleX 直连距离比）。',
        '前提：IK 链骨骼已就位，ikHandle 为有效的 ikHandle 节点。',
    ],
    returns_desc='dict {"ok": True, "grp": 系统组, "end_locator": 端点定位器, "nodes": [创建的节点]}',
    prerequisites=['ik_handle 必须是已存在的 ikHandle 节点'],
)
def make_gt_stretchy_ik(ik_handle, name='stretchy', attribute_holder=''):
    # type: (str, str, str) -> Dict[str, Any]
    """创建 IK 拉伸系统。

    :param ik_handle: IK 手柄节点名
    :param name: 生成节点命名前缀
    :param attribute_holder: 控制属性宿主节点名（空串表示不创建控制属性）
    :returns: dict {"ok": True, "grp": ..., "end_locator": ..., "nodes": [...]}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        if not cmds.objExists(ik_handle):
            raise ValueError('IK 手柄不存在: {}'.format(ik_handle))
        holder = str(attribute_holder or '').strip()
        if holder and not cmds.objExists(holder):
            raise ValueError('属性宿主不存在: {}'.format(holder))

        created = []
        _open_chunk(cmds, 'GT Make Stretchy IK')
        try:
            jnts = cmds.ikHandle(ik_handle, query=True, jointList=True) or []
            if not jnts:
                raise ValueError('IK 手柄未绑定任何骨骼: {}'.format(ik_handle))
            # 末端关节：优先取链尾唯一子关节，否则取离手柄最近的
            tail_children = cmds.listRelatives(jnts[-1], children=True, type='joint') or []
            end_jnt = ''
            if len(tail_children) == 1:
                end_jnt = tail_children[0]
            elif len(tail_children) > 1:
                handle_pos = cmds.xform(ik_handle, query=True, translation=True, worldSpace=True)
                best = None
                best_dist = None
                for child in tail_children:
                    pos = cmds.xform(child, query=True, translation=True, worldSpace=True)
                    dist = sum((a - b) ** 2 for a, b in zip(handle_pos, pos))
                    if best_dist is None or dist < best_dist:
                        best = child
                        best_dist = dist
                end_jnt = best or ''

            # 主测量：链根到 IK 手柄的实时距离（拉伸基准）
            def _mk_distance(prefix):
                dist_shape = cmds.distanceDimension(
                    sp=(1, 1 + 7.0 * len(created), 1), ep=(2, 2 + 7.0 * len(created), 2),
                )
                dist_tf = cmds.listRelatives(dist_shape, parent=True, fullPath=True)[0]
                locators = [n for n in (cmds.listConnections(dist_shape) or [])]
                cmds.delete(cmds.pointConstraint(jnts[0], locators[0]))
                cmds.delete(cmds.pointConstraint(ik_handle, locators[1]))
                return (dist_tf, locators)

            main_tf, main_locs = _mk_distance(name)
            main_tf = cmds.rename(main_tf, name + '_stretchyDistance')
            main_locs = [
                cmds.rename(main_locs[0], name + '_start'),
                cmds.rename(main_locs[1], name + '_end'),
            ]
            created.extend([main_tf] + main_locs)

            # 逐段测量：默认骨骼总长（拉伸比 = 实时/默认）
            seg_nodes = []
            for index in range(len(jnts)):
                dist_shape = cmds.distanceDimension(
                    sp=(1, 1 + 7.0 * len(created), 1), ep=(2, 2 + 7.0 * len(created), 2),
                )
                dist_tf = cmds.listRelatives(dist_shape, parent=True, fullPath=True)[0]
                locators = [n for n in (cmds.listConnections(dist_shape) or [])]
                label = _int_to_en(index + 1).capitalize()
                cmds.rename(dist_shape, name + '_defaultTerm' + label + '_distShape')
                dist_tf = cmds.rename(
                    dist_tf, name + '_defaultTerm' + label + '_dist',
                )
                locators = [
                    cmds.rename(locators[0], name + '_defaultTerm' + label + '_start'),
                    cmds.rename(locators[1], name + '_defaultTerm' + label + '_end'),
                ]
                cmds.delete(cmds.pointConstraint(jnts[index], locators[0]))
                nxt = jnts[index + 1] if index < len(jnts) - 1 else end_jnt
                if nxt:
                    cmds.delete(cmds.pointConstraint(nxt, locators[1]))
                seg_nodes.append(dist_tf)
                created.append(dist_tf)
                created.extend(locators)

            # 收纳进系统组
            grp = cmds.group(name=name + '_stretchy_grp', empty=True, world=True)
            created.append(grp)
            for node in [main_tf] + seg_nodes:
                related = cmds.listRelatives(node, children=True, fullPath=True) or []
                for rel in related:
                    if cmds.nodeType(rel) != 'mesh' and not cmds.nodeType(rel).startswith('distanceDim'):
                        pass
                cmds.parent(node, grp)
            for main_loc in main_locs:
                cmds.parent(main_loc, grp)

            # 求和默认长度 -> 非零保护 -> 归一化比 -> 条件限幅
            sum_node = cmds.createNode('plusMinusAverage', name=name + '_defaultTermSum')
            created.append(sum_node)
            for index, node in enumerate(seg_nodes):
                shape = cmds.listRelatives(node, shapes=True, fullPath=True) or [node]
                cmds.connectAttr(
                    '{}.distance'.format(shape[0]),
                    '{}.input1D[{}]'.format(sum_node, index),
                )
            main_shape = cmds.listRelatives(main_tf, shapes=True, fullPath=True) or [main_tf]

            cond_nonzero = cmds.createNode('condition', name=name + '_nonZero_condition')
            pct_multiply = cmds.createNode('multiplyDivide', name=name + '_onePct_multiply')
            created.extend([cond_nonzero, pct_multiply])
            cmds.connectAttr('{}.output1D'.format(sum_node), '{}.input1X'.format(pct_multiply))
            cmds.setAttr('{}.input2X'.format(pct_multiply), 0.01)
            cmds.connectAttr('{}.outputX'.format(pct_multiply), '{}.colorIfTrueR'.format(cond_nonzero))
            cmds.connectAttr('{}.outputX'.format(pct_multiply), '{}.secondTerm'.format(cond_nonzero))
            cmds.setAttr('{}.operation'.format(cond_nonzero), 5)  # not equal

            normalize = cmds.createNode('multiplyDivide', name=name + '_distNormalization')
            created.append(normalize)
            cmds.setAttr('{}.operation'.format(normalize), 2)  # divide
            cmds.connectAttr('{}.distance'.format(main_shape[0]), '{}.firstTerm'.format(cond_nonzero))
            cmds.connectAttr('{}.distance'.format(main_shape[0]), '{}.colorIfFalseR'.format(cond_nonzero))
            cmds.connectAttr('{}.outColorR'.format(cond_nonzero), '{}.input1X'.format(normalize))
            cmds.connectAttr('{}.output1D'.format(sum_node), '{}.input2X'.format(normalize))

            cond_stretch = cmds.createNode('condition', name=name + '_automation_condition')
            created.append(cond_stretch)
            cmds.setAttr('{}.operation'.format(cond_stretch), 3)  # greater than
            cmds.connectAttr('{}.outColorR'.format(cond_nonzero), '{}.firstTerm'.format(cond_stretch))
            cmds.connectAttr('{}.output1D'.format(sum_node), '{}.secondTerm'.format(cond_stretch))
            cmds.connectAttr('{}.outputX'.format(normalize), '{}.colorIfTrueR'.format(cond_stretch))

            # 主测量定位器跟随链根与手柄
            cmds.pointConstraint(jnts[0], main_locs[0])
            cmds.pointConstraint(ik_handle, main_locs[1])

            if holder:
                # 控制属性：stretch 开关 / squash / 体积保持
                for attr, dv, mn, mx in (
                    ('stretch', 1, 0, 1), ('squash', 0, 0, 1),
                    ('saveVolume', 1, 0, 1), ('baseVolumeMultiplier', 0.5, 0, 1),
                    ('minimumVolume', 0.4, 0.01, 1), ('maximumVolume', 2, 0, None),
                ):
                    if not cmds.attributeQuery(attr, node=holder, exists=True):
                        cmds.addAttr(
                            holder, longName=attr, attributeType='double',
                            keyable=True, minValue=mn,
                            **({'maxValue': mx} if mx is not None else {}),
                        )
                    cmds.setAttr('{}.{}'.format(holder, attr), dv)
                if not cmds.attributeQuery('stretchFromSource', node=holder, exists=True):
                    cmds.addAttr(
                        holder, longName='stretchFromSource',
                        attributeType='bool', keyable=True,
                    )
                cmds.setAttr('{}.stretchFromSource'.format(holder), 1)

                blend_stretch = cmds.createNode('blendTwoAttr', name=name + '_activation_blend')
                created.append(blend_stretch)
                cmds.setAttr('{}.input[0]'.format(blend_stretch), 1)
                cmds.connectAttr('{}.outColorR'.format(cond_stretch), '{}.input[1]'.format(blend_stretch))
                cmds.connectAttr('{}.stretch'.format(holder), '{}.attributesBlender'.format(blend_stretch))
                for jnt in jnts:
                    cmds.connectAttr('{}.output'.format(blend_stretch), '{}.scaleX'.format(jnt))

                # squash：condition 输出在 squash=1 时取 3 次幂压缩曲线
                cond_squash = cmds.createNode('condition', name=name + '_squash_condition')
                created.append(cond_squash)
                cmds.setAttr('{}.secondTerm'.format(cond_squash), 1)
                cmds.setAttr('{}.colorIfTrueR'.format(cond_squash), 1)
                cmds.setAttr('{}.colorIfFalseR'.format(cond_squash), 3)
                cmds.connectAttr('{}.squash'.format(holder), '{}.firstTerm'.format(cond_squash))
                cmds.connectAttr('{}.outColorR'.format(cond_squash), '{}.operation'.format(cond_stretch))
            else:
                # 无宿主：拉伸直连
                for jnt in jnts:
                    cmds.connectAttr('{}.outColorR'.format(cond_stretch), '{}.scaleX'.format(jnt))
        finally:
            _close_chunk(cmds, 'GT Make Stretchy IK')
        grp_name = name + '_stretchy_grp'
        return {
            'ok': True,
            'grp': grp_name if cmds.objExists(grp_name) else '',
            'end_locator': name + '_end',
            'nodes': created,
        }

    return run_on_main(_do)


# ---------------------------------------------------------------------- #
# 6. 正弦驱动属性
# ---------------------------------------------------------------------- #
@tool(
    dcc=['maya'],
    description='给对象添加正弦驱动属性网络（GT Tools Sine Attributes 移植）。'
                'time 驱动 sin 波自动往复动画，无需手 K 关键帧。',
    category='animation',
    examples=[
        {
            'summary': '给浮标加自动上下正弦浮动',
            'args': {'object': 'buoy', 'prefix': 'sine', 'frequency': 8},
        },
    ],
    notes=[
        '生成属性：<prefix>Influence(混合系数) / Amplitude / Frequency / Offset / Output。',
        'Influence=0 时无效果，1 时全幅度；Output 可连到任意驱动目标。',
        '内部用 eulerToQuat 生成三角函数（需要 quatNodes 插件，自动加载）。',
        '连接 Output 到 translateY 即可实现往复浮动。',
    ],
    returns_desc='dict {"ok": True, "output_attr": 输出属性路径, "nodes": [创建的节点]}',
    prerequisites=['object 必须存在于当前场景'],
)
def add_gt_sine_attributes(
    object,
    prefix='sine',
    frequency=10.0,
    amplitude=1.0,
    offset=0.0,
    add_absolute_output=False,
):
    # type: (str, str, float, float, float, bool) -> Dict[str, Any]
    """创建正弦驱动属性网络。

    :param object: 目标对象名
    :param prefix: 生成属性的命名前缀
    :param frequency: 初始频率（周期/单位时间）
    :param amplitude: 初始振幅
    :param offset: 初始相位偏移
    :param add_absolute_output: 是否额外创建绝对值输出属性
    :returns: dict {"ok": True, "output_attr": ..., "nodes": [...]}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        if not cmds.objExists(object):
            raise ValueError('对象不存在: {}'.format(object))
        # quatNodes 插件提供 eulerToQuat（与 gt-tools 一致）
        if not cmds.pluginInfo('quatNodes', query=True, loaded=True):
            cmds.loadPlugin('quatNodes', quiet=True)

        influence_attr = prefix + 'Influence'
        amplitude_attr = prefix + 'Amplitude'
        frequency_attr = prefix + 'Frequency'
        offset_attr = prefix + 'Offset'
        output_attr = prefix + 'Output'
        tick_attr = prefix + 'Tick'
        abs_attr = prefix + 'AbsOutput'

        created = []
        _open_chunk(cmds, 'GT Sine Attributes')
        try:
            mdl_node = cmds.createNode('multDoubleLinear', name='{}_multDoubleLinear'.format(object))
            quat_node = cmds.createNode('eulerToQuat', name='{}_eulerToQuat'.format(object))
            multiply_node = cmds.createNode('multiplyDivide', name='{}_amplitude_multiply'.format(object))
            sum_node = cmds.createNode('plusMinusAverage', name='{}_offset_sum'.format(object))
            infl_multiply = cmds.createNode('multiplyDivide', name='{}_influence_multiply'.format(object))
            created.extend([mdl_node, quat_node, multiply_node, sum_node, infl_multiply])

            def _add_attr(long_name, **kwargs):
                if not cmds.attributeQuery(long_name, node=object, exists=True):
                    cmds.addAttr(object, longName=long_name, **kwargs)

            _add_attr(influence_attr, attributeType='double', keyable=True, minValue=0, maxValue=1)
            _add_attr(amplitude_attr, attributeType='double', keyable=True)
            _add_attr(frequency_attr, attributeType='double', keyable=True)
            _add_attr(offset_attr, attributeType='double', keyable=True)
            _add_attr(tick_attr, attributeType='double', keyable=True)
            _add_attr(output_attr, attributeType='double', keyable=True)
            if add_absolute_output:
                _add_attr(abs_attr, attributeType='double', keyable=True)

            cmds.setAttr('{}.{}'.format(object, influence_attr), 1.0)
            cmds.setAttr('{}.{}'.format(object, amplitude_attr), float(amplitude))
            cmds.setAttr('{}.{}'.format(object, frequency_attr), float(frequency))
            cmds.setAttr('{}.{}'.format(object, offset_attr), float(offset))

            # time * influence -> tick；frequency * tick -> 相位；quaternion 取正弦
            cmds.connectAttr('time1.outTime', '{}.input1X'.format(infl_multiply))
            cmds.connectAttr('{}.input1X'.format(infl_multiply), '{}.{}'.format(object, tick_attr))
            cmds.connectAttr('{}.{}'.format(object, influence_attr), '{}.input2X'.format(infl_multiply))

            cmds.connectAttr('{}.{}'.format(object, amplitude_attr), '{}.input2X'.format(multiply_node))
            cmds.connectAttr('{}.{}'.format(object, frequency_attr), '{}.input1'.format(mdl_node))
            cmds.connectAttr('{}.{}'.format(object, tick_attr), '{}.input2'.format(mdl_node))
            cmds.connectAttr('{}.{}'.format(object, offset_attr), '{}.input1D[0]'.format(sum_node))
            cmds.connectAttr('{}.output'.format(mdl_node), '{}.inputRotateX'.format(quat_node))

            cmds.connectAttr('{}.outputQuatX'.format(quat_node), '{}.input1X'.format(multiply_node))
            cmds.connectAttr('{}.outputX'.format(multiply_node), '{}.input1D[1]'.format(sum_node))
            cmds.connectAttr('{}.output1D'.format(sum_node), '{}.{}'.format(object, output_attr))

            if add_absolute_output:
                squared = cmds.createNode('multiplyDivide', name='{}_abs_squared'.format(object))
                rev_squared = cmds.createNode('multiplyDivide', name='{}_abs_reverse'.format(object))
                created.extend([squared, rev_squared])
                cmds.setAttr('{}.operation'.format(squared), 3)  # power
                cmds.setAttr('{}.operation'.format(rev_squared), 3)  # power
                cmds.setAttr('{}.input2X'.format(squared), 2)
                cmds.setAttr('{}.input2X'.format(rev_squared), 0.5)
                cmds.connectAttr('{}.{}'.format(object, output_attr), '{}.input1X'.format(squared))
                cmds.connectAttr('{}.outputX'.format(squared), '{}.input1X'.format(rev_squared))
                cmds.connectAttr('{}.outputX'.format(rev_squared), '{}.{}'.format(object, abs_attr))
                out_name = '{}.{}'.format(object, abs_attr)
            else:
                out_name = '{}.{}'.format(object, output_attr)
        finally:
            _close_chunk(cmds, 'GT Sine Attributes')
        return {'ok': True, 'output_attr': out_name, 'nodes': created}

    return run_on_main(_do)


# ---------------------------------------------------------------------- #
# 7. UV 传递
# ---------------------------------------------------------------------- #
def _transfer_uvs_once(cmds, source, target):
    # type: (object, str, str) -> None
    """单对网格的 UV 传递（含中间对象特殊处理，gt-tools 语义）。"""
    history = cmds.transferAttributes(
        source, target,
        transferPositions=0, transferNormals=0,
        transferUVs=2, transferColors=2,
        sampleSpace=4, searchMethod=3, colorBorders=1,
    )
    # 中间对象（构建历史里的隐藏 mesh）也要单独处理，否则 UV 不完整
    for node in (cmds.listHistory(target) or []):
        if cmds.nodeType(node) != 'mesh':
            continue
        if not cmds.objExists(node + '.intermediateObject'):
            continue
        if not cmds.getAttr(node + '.intermediateObject'):
            continue
        cmds.setAttr(node + '.intermediateObject', 0)
        try:
            cmds.transferAttributes(
                source, node,
                transferPositions=0, transferNormals=0,
                transferUVs=2, transferColors=2,
                sampleSpace=4, searchMethod=3, colorBorders=1,
            )
            cmds.delete(node, constructionHistory=True)
        finally:
            cmds.setAttr(node + '.intermediateObject', 1)
    # 传完即删历史节点，保持场景干净
    if history:
        try:
            cmds.delete(history)
        except Exception:  # pylint: disable=broad-except
            pass


@tool(
    dcc=['maya'],
    description='把源网格的 UV 传递给目标网格（GT Tools Transfer UVs 移植）。'
                '要求两个网格拓扑一致（如镜像复制、拓扑对称编辑过的模型）。',
    category='uv',
    examples=[
        {
            'summary': '把干净 UV 传给改过拓扑历史的同款模型',
            'args': {'source': 'pSphere1', 'targets': 'pSphere1_deformed'},
        },
    ],
    notes=[
        'sampleSpace=4（世界空间）+ searchMethod=3（最接近面），拓扑一致时最稳。',
        '会自动处理构建历史中的中间对象（intermediateObject）。',
        '传递后自动删除 transferAttributes 历史节点。',
        '拓扑不一致时结果不可预期，建议先复制再清历史。',
    ],
    returns_desc='dict {"ok": True, "transferred": 数量, "targets": [目标列表]}',
    prerequisites=['source 与 targets 必须是 mesh（或带 mesh shape 的 transform）'],
)
def transfer_gt_uvs(source, targets):
    # type: (str, Any) -> Dict[str, Any]
    """网格间传递 UV。

    :param source: 源网格（带正确 UV）
    :param targets: 目标网格列表或逗号分隔字符串
    :returns: dict {"ok": True, "transferred": ..., "targets": [...]}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        if not source or not cmds.objExists(source):
            raise ValueError('源网格不存在: {}'.format(source))
        tgt = _normalize_list(targets)
        tgt = [t for t in tgt if t != source]
        if not tgt:
            raise ValueError('必须指定至少一个目标网格（不能与 source 相同）')
        done = []
        failed = []
        _open_chunk(cmds, 'GT Transfer UVs')
        try:
            for target in tgt:
                if not cmds.objExists(target):
                    failed.append('{}: 不存在'.format(target))
                    continue
                try:
                    _transfer_uvs_once(cmds, source, target)
                    done.append(target)
                except Exception as exc:  # pylint: disable=broad-except
                    failed.append('{}: {}'.format(target, exc))
        finally:
            _close_chunk(cmds, 'GT Transfer UVs')
        return {
            'ok': True,
            'transferred': len(done),
            'targets': done,
            'failed': failed,
        }

    return run_on_main(_do)


# ---------------------------------------------------------------------- #
# 8. 批量属性连接（经 utility 节点）
# ---------------------------------------------------------------------- #
def _utility_plug(node_type, role, is_vector):
    # type: (str, str, bool) -> str
    """查 utility 节点的标准插头名（gt-tools NODE_PLUGS 子集）。"""
    plugs = {
        'plusMinusAverage': {
            'scalar_input': 'input1D[0]', 'scalar_output': 'output1D',
            'vector_input': 'input3D[0]', 'vector_output': 'output3D',
        },
        'multiplyDivide': {
            'scalar_input': 'input1X', 'scalar_output': 'outputX',
            'vector_input': 'input1', 'vector_output': 'output',
        },
        'reverse': {
            'scalar_input': 'inputX', 'scalar_output': 'outputX',
            'vector_input': 'input', 'vector_output': 'output',
        },
    }
    if node_type not in plugs:
        raise ValueError(
            '不支持的 utility 节点类型: {}，可选: {}'.format(
                node_type, ', '.join(sorted(plugs)),
            ),
        )
    key = '{}_{}'.format('vector' if is_vector else 'scalar', role)
    if key not in plugs[node_type]:
        raise ValueError('{} 不支持角色 {}'.format(node_type, role))
    return plugs[node_type][key]


@tool(
    dcc=['maya'],
    description='批量连接属性，可经过 utility 节点中转或反向（GT Tools Connect Attributes 移植）。'
                '适合一次给多个目标接同一路驱动。',
    category='node',
    examples=[
        {
            'summary': '把主控制器位移取反后同时驱动左右手指',
            'args': {
                'source': 'ctrl_main.translateX',
                'targets': ['finger_L.translateX', 'finger_R.translateX'],
                'add_reverse_node': True,
            },
        },
    ],
    notes=[
        'source/target 必须是 "node.attribute" 完整格式。',
        'utility_node_type 支持 plusMinusAverage / multiplyDivide / reverse。',
        'add_reverse_node=True 时输出先经过 reverse 再连目标（做镜像驱动）。',
        'force=True 时目标已有连接会被强制覆盖。',
    ],
    returns_desc='dict {"ok": True, "connected": 数量, "created_nodes": [新建节点], "errors": [失败项]}',
    prerequisites=['源属性必须存在且可输出'],
)
def connect_gt_attributes(
    source,
    targets,
    use_utility_node=False,
    utility_node_type='multiplyDivide',
    add_reverse_node=False,
    force=False,
):
    # type: (str, Any, bool, str, bool, bool) -> Dict[str, Any]
    """批量连接属性。

    :param source: 源属性完整路径（node.attribute）
    :param targets: 目标属性列表或逗号分隔字符串（node.attribute）
    :param use_utility_node: 是否经 utility 节点中转
    :param utility_node_type: utility 节点类型
    :param add_reverse_node: 是否串联 reverse 节点取反
    :param force: 目标已有连接时是否强制重连
    :returns: dict {"ok": True, "connected": ..., "created_nodes": [...], "errors": [...]}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        if '.' not in source:
            raise ValueError('source 必须是 "node.attribute" 格式: {}'.format(source))
        src_node = source.split('.', 1)[0]
        if not cmds.objExists(src_node):
            raise ValueError('源节点不存在: {}'.format(src_node))
        tgt_list = _normalize_list(targets)
        if not tgt_list:
            raise ValueError('必须指定目标属性')

        created = []
        errors = []
        connected = 0
        _open_chunk(cmds, 'GT Connect Attributes')
        try:
            utility_node = None
            reverse_node = None
            if use_utility_node:
                utility_node = cmds.createNode(utility_node_type)
                created.append(utility_node)
            if add_reverse_node:
                reverse_node = cmds.createNode('reverse')
                created.append(reverse_node)
            for target in tgt_list:
                if '.' not in target:
                    errors.append('{}: 不是 "node.attribute" 格式'.format(target))
                    continue
                dst_node = target.split('.', 1)[0]
                if not cmds.objExists(dst_node):
                    errors.append('{}: 节点不存在'.format(target))
                    continue
                try:
                    is_vector = len(cmds.getAttr(source) or []) > 1
                except Exception:  # pylint: disable=broad-except
                    is_vector = False
                chain = source
                try:
                    if utility_node is not None:
                        in_plug = _utility_plug(utility_node_type, 'input', is_vector)
                        out_plug = _utility_plug(utility_node_type, 'output', is_vector)
                        cmds.connectAttr(chain, '{}.{}'.format(utility_node, in_plug), force=True)
                        chain = '{}.{}'.format(utility_node, out_plug)
                    if reverse_node is not None:
                        in_plug = _utility_plug('reverse', 'input', is_vector)
                        out_plug = _utility_plug('reverse', 'output', is_vector)
                        cmds.connectAttr(chain, '{}.{}'.format(reverse_node, in_plug), force=True)
                        chain = '{}.{}'.format(reverse_node, out_plug)
                    cmds.connectAttr(chain, target, force=force)
                    connected += 1
                except Exception as exc:  # pylint: disable=broad-except
                    errors.append('{}: {}'.format(target, exc))
            # 全部失败时清理已创建的 utility，避免残留空节点
            if utility_node is not None and connected == 0:
                cmds.delete(utility_node)
                created.remove(utility_node)
            if reverse_node is not None and connected == 0:
                if cmds.objExists(reverse_node):
                    cmds.delete(reverse_node)
                if reverse_node in created:
                    created.remove(reverse_node)
        finally:
            _close_chunk(cmds, 'GT Connect Attributes')
        return {
            'ok': True,
            'connected': connected,
            'created_nodes': created,
            'errors': errors,
        }

    return run_on_main(_do)


# ---------------------------------------------------------------------- #
# 9. 世界空间动画采样与重烘焙
# ---------------------------------------------------------------------- #
@tool(
    dcc=['maya'],
    description='烘焙对象的世界空间位移/旋转到自身关键帧（GT Tools World Space Baker 移植）。'
                '把约束/父子层级带来的世界运动"烙"进对象本地的 translate/rotate 通道。',
    category='animation',
    examples=[
        {
            'summary': '把武器跟随手的运动烘焙成自身关键帧',
            'args': {'objects': 'sword', 'start': 1, 'end': 48},
        },
    ],
    notes=[
        '采样帧范围内逐帧记录世界空间 translate/rotate，再写回本地通道并设 key。',
        '适合"把约束动画变成裸关键帧"以供手修。',
        '烘焙过程挂起视口刷新，长动画也较快；结束后恢复当前帧。',
        '只有可动画的 translate/rotate 通道会被处理（scale 不处理）。',
    ],
    returns_desc='dict {"ok": True, "baked": 数量, "frames": [帧范围]}',
    prerequisites=['objects 必须存在于场景；帧范围内应已有动画或约束运动'],
)
def bake_gt_world_space(objects, start=None, end=None):
    # type: (Any, int, int) -> Dict[str, Any]
    """烘焙世界空间动画。

    :param objects: 对象名列表或逗号分隔字符串
    :param start: 起始帧，None 时取播放范围起点
    :param end: 结束帧，None 时取播放范围终点
    :returns: dict {"ok": True, "baked": ..., "frames": [start, end]}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        objs = _normalize_list(objects)
        objs = [o for o in objs if cmds.objExists(o)]
        if not objs:
            raise ValueError('必须指定要烘焙的对象')
        if start is None or end is None:
            rng = cmds.playbackOptions(query=True, minTime=True, maxTime=True)
            s = int(start) if start is not None else int(rng[0])
            e = int(end) if end is not None else int(rng[1])
        else:
            s, e = int(start), int(end)
        if e < s:
            raise ValueError('end 帧不能小于 start 帧（{} < {}）'.format(e, s))

        original_time = cmds.currentTime(query=True)
        baked = 0
        _open_chunk(cmds, 'GT World Space Bake')
        try:
            cmds.refresh(suspend=True)
            for obj in objs:
                animatable = cmds.listAnimatable(obj) or []
                attrs = {a.rsplit('.', 1)[-1] for a in animatable}
                has_t = any(a.startswith('translate') for a in attrs)
                has_r = any(a.startswith('rotate') for a in attrs)
                if not has_t and not has_r:
                    continue
                samples = []
                for frame in range(s, e + 1):
                    cmds.currentTime(frame)
                    row = {'frame': frame}
                    if has_t:
                        row['t'] = list(cmds.xform(obj, query=True, worldSpace=True, translation=True))
                    if has_r:
                        row['r'] = list(cmds.xform(obj, query=True, worldSpace=True, rotation=True))
                    samples.append(row)
                for row in samples:
                    cmds.currentTime(row['frame'])
                    if 't' in row:
                        cmds.xform(obj, worldSpace=True, translation=row['t'])
                    if 'r' in row:
                        cmds.xform(obj, worldSpace=True, rotation=row['r'])
                    channels = []
                    if 't' in row:
                        channels.extend(['translateX', 'translateY', 'translateZ'])
                    if 'r' in row:
                        channels.extend(['rotateX', 'rotateY', 'rotateZ'])
                    cmds.setKeyframe(obj, time=row['frame'], attribute=channels)
                baked += 1
        finally:
            cmds.currentTime(original_time)
            cmds.refresh(suspend=False)
            _close_chunk(cmds, 'GT World Space Bake')
        return {'ok': True, 'baked': baked, 'frames': [s, e]}

    return run_on_main(_do)


# ---------------------------------------------------------------------- #
# 10. 错帧（Stagger）
# ---------------------------------------------------------------------- #
@tool(
    dcc=['maya'],
    description='按顺序给多个对象的动画错帧（GT Tools Stagger Keyframes 移植）。'
                '第 N 个对象的关键帧整体偏移 N*step 帧，一键生成交替跟随动画。',
    category='animation',
    examples=[
        {
            'summary': '手指依次弯曲的经典 stagger',
            'args': {'nodes': 'finger1,finger2,finger3,finger4', 'step': 2},
        },
    ],
    notes=[
        'nodes 顺序决定偏移顺序：第 1 个不动，第 2 个偏移 step，以此类推。',
        'step 为负时向左错帧（先动的在后）。',
        '只影响已有关键帧；无关键帧的对象自动跳过。',
        '常用于尾巴、手指、披风等链式跟随动画。',
    ],
    returns_desc='dict {"ok": True, "objects": 处理数, "key_count": 移动关键帧总数}',
    prerequisites=['各对象应已有关键帧动画'],
)
def stagger_gt_keyframes(nodes, step=1.0):
    # type: (Any, float) -> Dict[str, Any]
    """按选择顺序错帧。

    :param nodes: 对象名列表或逗号分隔字符串（顺序即偏移顺序）
    :param step: 相邻对象的帧偏移量
    :returns: dict {"ok": True, "objects": ..., "key_count": ...}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        node_list = _normalize_list(nodes)
        if not node_list:
            raise ValueError('必须指定对象')
        step_val = float(step)
        if step_val == 0:
            return {'ok': True, 'objects': 0, 'key_count': 0}
        affected = 0
        key_count = 0
        _open_chunk(cmds, 'GT Stagger Keyframes')
        try:
            for index, node in enumerate(node_list):
                if not cmds.objExists(node):
                    continue
                offset = step_val * index
                if offset == 0:
                    continue
                before = cmds.keyframe(node, query=True, keyframeCount=True) or 0
                if not before:
                    continue
                cmds.keyframe(node, edit=True, relative=True, timeChange=offset)
                after = cmds.keyframe(node, query=True, keyframeCount=True) or 0
                affected += 1
                key_count += int(after)
        finally:
            _close_chunk(cmds, 'GT Stagger Keyframes')
        return {'ok': True, 'objects': affected, 'key_count': key_count}

    return run_on_main(_do)


__all__ = [
    'rename_gt_batch',
    'match_gt_transform',
    'reset_gt_transforms',
    'orient_gt_joints',
    'make_gt_stretchy_ik',
    'add_gt_sine_attributes',
    'transfer_gt_uvs',
    'connect_gt_attributes',
    'bake_gt_world_space',
    'stagger_gt_keyframes',
]
