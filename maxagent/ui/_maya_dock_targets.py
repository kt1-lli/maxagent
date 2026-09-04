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

本模块只做枚举与标签翻译，不负责创建/停靠，便于在设置面板与 dock
创建两处复用。
"""

from __future__ import absolute_import

from typing import List, Optional, Tuple

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

# 刷新下拉框时的推荐项（排在列表最前，其余按字典序）
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
    :returns: control 名列表，已去重且全部通过存在性校验。
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
    return result


def _is_visible(cmds, name):
    # type: (object, str) -> bool
    """查询 workspaceControl 当前是否可见。查询失败时按可见处理。"""
    try:
        return bool(cmds.workspaceControl(name, query=True, visible=True))
    except Exception:  # pylint: disable=broad-except
        return True


def list_dock_targets_with_labels(cmds=None):
    # type: (Optional[object]) -> List[Tuple[str, str]]
    """枚举停靠目标并返回 ``(control 名, 显示标签)`` 列表。

    排序规则：推荐项按 ``_RECOMMENDED_CONTROLS`` 顺序排在最前，
    其余按标签字典序排列，保证下拉框稳定且常用的在顶部。
    """
    targets = list_dock_targets(cmds=cmds)
    recommended = []  # type: List[str]
    others = []  # type: List[str]
    rec_rank = {
        name.lower(): idx
        for idx, name in enumerate(_RECOMMENDED_CONTROLS)
    }
    for name in targets:
        if name.lower() in rec_rank:
            recommended.append(name)
        else:
            others.append(name)
    recommended.sort(key=lambda n: rec_rank[n.lower()])
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
]
