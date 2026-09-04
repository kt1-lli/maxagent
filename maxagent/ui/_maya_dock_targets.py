#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Maya 可停靠对象（workspaceControl）枚举。

Maya 2017+ 的停靠依赖 ``workspaceControl``，用户可以把面板停靠到任意
一个已存在的 workspaceControl 上（``tabToControl`` / ``dockToControl``）。
Maya 本身没有提供"列出所有可停靠位置"的命令，需要组合三类来源：

1. ``$gUIComponentDockControlArray``：内置 dock 组件（Channel Box / Layer
   Editor、Attribute Editor、Outliner、Tool Settings 等）。
2. ``$gUIComponentToolBarArray``：内置工具栏（Shelf、Time Slider、
   Range Slider、Command Line、Help Line、Tool Box）。
3. ``cmds.lsUI(workspaceControls=True)``：当前会话里所有已实例化的
   workspaceControl，包含视口 MainPane、UV 编辑器、脚本编辑器，以及
   所有第三方/用户面板。

来源 1、2 的数组里记录的是"组件名"，未必都已在当前 workspace 实例化，
因此必须逐个用 ``workspaceControl(query=True, exists=True)`` 过滤。

来源 3 会把嵌套在别的面板内部的子控件也一起返回（实测 Maya 2022：
``ChannelBoxLayerEditor`` 内部还挂着 ``ChannelBox`` 与 ``LayerEditor``）。
这类子控件停靠后不可见，且会让下拉框出现"看起来重复"的项，需要按
完整路径做层级判定后剔除，详见 :func:`_filter_nested_controls`。

本模块只做枚举与标签翻译，不负责创建/停靠，便于在设置面板与 dock
创建两处复用。
"""

from __future__ import absolute_import

from typing import Dict, List, Optional, Tuple

from ..logger import get_logger

logger = get_logger(__name__)


# 内置组件名 -> 中文显示标签。
# key 用小写做匹配，因为部分 Maya 版本返回的名字大小写不一致。
_CONTROL_LABELS = {
    'channelboxlayereditor': 'Channel Box / Layer Editor',
    'attributeeditor': 'Attribute Editor',
    'outliner': 'Outliner',
    'toolsettings': 'Tool Settings',
    'polygons': 'Modeling Toolkit',
    'shelf': 'Shelf（工具架）',
    'timeslider': 'Time Slider（时间轴）',
    'rangeslider': 'Range Slider（范围条）',
    'commandline': 'Command Line（命令行）',
    'helpline': 'Help Line（帮助行）',
    'statusline': 'Status Line（状态行）',
    'toolbox': 'Tool Box（工具箱）',
    'mainpane': 'Main Pane（主视口）',
    'uvtoolkitdockcontrol': 'UV Toolkit',
    'polytextureplacementpanel1window': 'UV Editor',
    'scripteditorpanel1window': 'Script Editor（脚本编辑器）',
    'grapheditor1window': 'Graph Editor（曲线编辑器）',
    'dopesheetpanel1window': 'Dope Sheet',
    'hypershadepanel1window': 'Hypershade',
    'nodeeditorpanel1window': 'Node Editor',
    'renderview': 'Render View',
}

# 新建面板时的默认停靠目标优先级。
# Maya 里最常被用作停靠锚点的是 Channel Box，若用户关掉了则依次回退。
_DEFAULT_DOCK_PRIORITY = (
    'ChannelBoxLayerEditor',
    'AttributeEditor',
    'Outliner',
    'ToolSettings',
)

# 下拉框里排在最前的推荐项（其余按标签字典序，并用分隔线隔开）
_RECOMMENDED_CONTROLS = (
    'ChannelBoxLayerEditor',
    'AttributeEditor',
    'Outliner',
    'ToolSettings',
    'MainPane',
)


def _iter_mel_array(cmds, mel_source):
    # type: (object, str) -> List[str]
    """执行一段 MEL 取全局数组，返回其中的字符串元素。

    MEL 的 ``$g...`` 全局变量无法通过 ``cmds`` 直接读取，标准做法是
    ``mel.eval('$tmp = $gXXX;')``——MEL 会把数组值作为 eval 的返回值。
    不同 Maya 版本可能返回 ``None`` / 空串 / 非列表，这里统一归一化。
    """
    try:
        from maya import mel as mel_mod  # type: ignore
    except Exception:  # pylint: disable=broad-except
        try:
            mel_mod = cmds.mel  # type: ignore[attr-defined]
        except Exception:  # pylint: disable=broad-except
            return []
    try:
        raw = mel_mod.eval(mel_source)
    except Exception:  # pylint: disable=broad-except
        logger.debug('MEL 枚举失败: %s', mel_source)
        return []
    if not raw:
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, (list, tuple)):
        return [str(x) for x in raw if x]
    return []


def _control_exists(cmds, name):
    # type: (object, str) -> bool
    """判断某个 workspaceControl 当前是否可用（存在且已实例化）。"""
    if not name:
        return False
    try:
        return bool(cmds.workspaceControl(name, query=True, exists=True))
    except Exception:  # pylint: disable=broad-except
        # 部分名字不是 workspaceControl（如纯 toolbar），用 control 兜底
        try:
            return bool(cmds.control(name, query=True, exists=True))
        except Exception:  # pylint: disable=broad-except
            return False


def _control_path(cmds, name):
    # type: (object, str) -> str
    """取 workspaceControl 的完整路径名，用于判定层级。

    返回形如 ``|MayaWindow|MainWorkspaceLayout|ChannelBoxLayerEditor``
    的字符串。查询失败时返回空串，调用方需按"无法判定"保守处理。
    """
    try:
        path = cmds.workspaceControl(name, query=True, fullPathName=True)
    except Exception:  # pylint: disable=broad-except
        return ''
    return str(path) if path else ''


def _filter_nested_controls(cmds, names):
    # type: (object, List[str]) -> List[str]
    """剔除嵌套在另一个候选内部的子 control。

    ``lsUI(workspaceControls=True)`` 会把嵌套在复合面板内部的子控件也
    一并返回。实测 Maya 2022 中 ``ChannelBoxLayerEditor`` 内部挂着
    ``ChannelBox`` 与 ``LayerEditor``：它们单独停靠后不可见，且在下拉框
    里表现为"和 ChannelBoxLayerEditor 重复"的干扰项。

    判定方式：比较完整路径的层级段。若 A 的路径以 B 的路径为前缀，
    说明 A 在 B 内部，丢弃 A。用分段比较而非字符串 startswith，避免
    ``Outliner`` 与 ``OutlinerPanel2`` 这类前缀相同的名字被误判。

    拿不到路径的 control 一律保留——宁可多显示一项，也不要误删用户
    真正想停靠的面板。
    """
    paths = {}  # type: Dict[str, str]
    for name in names:
        paths[name] = _control_path(cmds, name)

    def segments_of(path):
        # type: (str) -> List[str]
        return [part for part in path.replace('|', ' ').split() if part]

    result = []  # type: List[str]
    for name in names:
        path = paths.get(name) or ''
        if not path:
            result.append(name)
            continue
        own = segments_of(path)
        is_nested = False
        for other_name, other_path in paths.items():
            if other_name == name or not other_path:
                continue
            parent = segments_of(other_path)
            if len(parent) >= len(own):
                continue
            if own[:len(parent)] == parent:
                is_nested = True
                break
        if not is_nested:
            result.append(name)
    return result


def control_label(name):
    # type: (str) -> str
    """把 workspaceControl 名翻译成人类可读标签。

    已知名字返回中文说明；未知名字（第三方/自定义面板）原样返回，
    保证下拉框里永远能看到真实 control 名，方便用户对照。
    """
    if not name:
        return ''
    return _CONTROL_LABELS.get(str(name).lower(), str(name))


def list_dock_targets(cmds=None, include_invisible=True):
    # type: (Optional[object], bool) -> List[str]
    """枚举当前 Maya 会话中所有可作为停靠目标的 workspaceControl。

    :param cmds: ``maya.cmds`` 模块；None 时内部延迟导入。
    :param include_invisible: 是否包含当前不可见（未在当前 workspace
        展开）的 control。False 时只返回已可见的，用于"停靠到用户
        当前看得见的面板"这种更保守的场景。
    :returns: control 名列表，已去重、通过存在性校验，并剔除嵌套子控件。
    """
    if cmds is None:
        try:
            import maya.cmds as cmds  # type: ignore
        except Exception:  # pylint: disable=broad-except
            logger.debug('非 Maya 环境，无法枚举停靠目标')
            return []

    if not hasattr(cmds, 'workspaceControl'):
        # Maya 2016 及更早没有 workspaceControl（用 dockControl）
        return []

    candidates = []  # type: List[str]

    # 来源 1：内置 dock 组件
    candidates.extend(
        _iter_mel_array(cmds, '$ctrl_tmp_var = $gUIComponentDockControlArray;')
    )
    # 来源 2：内置工具栏
    candidates.extend(
        _iter_mel_array(cmds, '$ctrl_tmp_var = $gUIComponentToolBarArray;')
    )
    # 来源 3：当前会话已实例化的全部 workspaceControl
    try:
        existing = cmds.lsUI(workspaceControls=True) or []
        candidates.extend([str(x) for x in existing])
    except Exception:  # pylint: disable=broad-except
        logger.debug('lsUI(workspaceControls=True) 枚举失败')

    # 兜底常量：部分版本/语言环境下 mel 数组拿不到，但这些名字是稳定的
    candidates.extend(list(_DEFAULT_DOCK_PRIORITY))
    candidates.extend(['MainPane', 'UVToolkitDockControl'])

    result = []  # type: List[str]
    seen = set()  # type: set
    for raw in candidates:
        name = str(raw).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        if not _control_exists(cmds, name):
            continue
        if not include_invisible and not _is_visible(cmds, name):
            continue
        result.append(name)
    # 最后剔除嵌套在其它候选内部的子控件
    return _filter_nested_controls(cmds, result)


def _is_visible(cmds, name):
    # type: (object, str) -> bool
    """查询 workspaceControl 当前是否可见。查询失败时按可见处理。"""
    try:
        return bool(cmds.workspaceControl(name, query=True, visible=True))
    except Exception:  # pylint: disable=broad-except
        return True


def split_recommended(names):
    # type: (List[str]) -> Tuple[List[str], List[str]]
    """把停靠目标拆成（推荐项，其它项）两组。

    推荐项按 :data:`_RECOMMENDED_CONTROLS` 的固定顺序返回，其余原样
    返回（调用方自行按标签排序）。设置面板据此在两组之间插入分隔线，
    让常用目标始终排在下拉框顶部。
    """
    rec_rank = {
        name.lower(): idx
        for idx, name in enumerate(_RECOMMENDED_CONTROLS)
    }
    recommended = []  # type: List[str]
    others = []  # type: List[str]
    for name in names:
        if name.lower() in rec_rank:
            recommended.append(name)
        else:
            others.append(name)
    recommended.sort(key=lambda n: rec_rank[n.lower()])
    return (recommended, others)


def list_dock_targets_with_labels(cmds=None):
    # type: (Optional[object]) -> List[Tuple[str, str]]
    """枚举停靠目标并返回 ``(control 名, 显示标签)`` 列表。

    排序规则：推荐项按 :data:`_RECOMMENDED_CONTROLS` 顺序排在最前，
    其余按标签字典序排列，保证下拉框稳定且常用的在顶部。
    """
    targets = list_dock_targets(cmds=cmds)
    recommended, others = split_recommended(targets)
    others.sort(key=lambda n: control_label(n).lower())
    ordered = recommended + others
    return [(name, '{}  ({})'.format(control_label(name), name))
            for name in ordered]


def resolve_dock_target(preferred=None, cmds=None):
    # type: (Optional[str], Optional[object]) -> Optional[str]
    """决定新建面板应该停靠到哪个 control。

    :param preferred: 用户/持久化配置指定的首选目标。若它当前有效则直接用。
    :returns: 可用的 control 名；一个都没有时返回 None（调用方应退化为
        ``dockToMainWindow``）。
    """
    targets = list_dock_targets(cmds=cmds)
    if not targets:
        return None
    if preferred:
        for name in targets:
            if name.lower() == str(preferred).lower():
                return name
        logger.debug(
            '配置的停靠目标 %s 当前不可用，回退到默认优先级', preferred,
        )
    lower_map = {name.lower(): name for name in targets}
    for name in _DEFAULT_DOCK_PRIORITY:
        if name.lower() in lower_map:
            return lower_map[name.lower()]
    return targets[0]


__all__ = [
    'control_label',
    'list_dock_targets',
    'list_dock_targets_with_labels',
    'resolve_dock_target',
    'split_recommended',
]
