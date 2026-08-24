#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Maya 场景管理类工具：保存/加载/导入导出/引用。

危险标记说明：
- save_maya_file 会覆盖文件，标 dangerous
- import_file 会引入外部数据，标 dangerous
- 引用/导入等操作谨慎使用
"""

from __future__ import absolute_import
from __future__ import print_function

import os
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
    description='保存当前 Maya 场景到 .ma 或 .mb 文件。',
    category='scene_io',
    dangerous=True,
    wrap_undo=False,
    examples=[{"summary": "保存场景", "args": {"file_path": 'C:/Work/scene.ma', "allow_overwrite": True}}],
    returns_desc="dict: {\"ok\": True, \"file_path\": str}"
)
def save_maya_file(file_path, allow_overwrite=True):
    # type: (str, bool) -> Dict[str, Any]
    """保存场景。

    :param file_path: 目标路径
    :param allow_overwrite: 是否允许覆盖
    """
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    def _impl():
        path = os.path.normpath(file_path)
        if os.path.exists(path) and not allow_overwrite:
            raise ValueError('文件已存在且不允许覆盖: {}'.format(path))
        directory = os.path.dirname(path)
        if directory and not os.path.exists(directory):
            os.makedirs(directory)
        cmds.file(rename=path)
        cmds.file(save=True, type='mayaAscii' if path.endswith('.ma') else 'mayaBinary')
        return {'ok': True, 'file_path': path}

    return run_on_main(_impl)


@tool(
    dcc=['maya'],
    description='打开指定 Maya 场景文件。',
    category='scene_io',
    dangerous=True,
    wrap_undo=False,
    examples=[{"summary": "打开场景", "args": {"file_path": 'C:/Work/scene.ma'}}],
    returns_desc="dict: {\"ok\": True, \"file_path\": str}"
)
def open_maya_file(file_path, force=False):
    # type: (str, bool) -> Dict[str, Any]
    """打开场景。

    :param file_path: 文件路径
    :param force: 是否强制忽略未保存更改
    """
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    def _impl():
        path = os.path.normpath(file_path)
        if not os.path.exists(path):
            raise ValueError('文件不存在: {}'.format(path))
        cmds.file(path, open=True, force=force)
        return {'ok': True, 'file_path': path}

    return run_on_main(_impl)


@tool(
    dcc=['maya'],
    description='把外部文件导入当前场景。',
    category='scene_io',
    dangerous=True,
    wrap_undo=False,
    examples=[{"summary": "导入模型", "args": {"file_path": 'C:/Work/model.fbx'}}],
    returns_desc="List[str]: 导入的顶层节点名列表"
)
def import_file(file_path, namespace=None):
    # type: (str, Optional[str]) -> List[str]
    """导入文件。

    :param file_path: 文件路径
    :param namespace: 命名空间
    """
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    def _impl():
        path = os.path.normpath(file_path)
        if not os.path.exists(path):
            raise ValueError('文件不存在: {}'.format(path))
        kwargs = {}
        if namespace:
            kwargs['namespace'] = namespace
        result = cmds.file(path, i=True, returnNewNodes=True, **kwargs)
        # 只返回 transform 顶层节点
        transforms = cmds.ls(result, type='transform', long=True) or []
        return transforms

    return run_on_main(_impl)


@tool(
    dcc=['maya'],
    description='把指定对象导出为单独文件（.ma/.mb/.fbx/.obj）。',
    category='scene_io',
    dangerous=True,
    wrap_undo=False,
    examples=[{"summary": "导出选中对象为 FBX", "args": {"file_path": 'C:/Work/export.fbx', "objects": 'pCube1'}}],
    returns_desc="dict: {\"ok\": True, \"file_path\": str}"
)
def export_selected(file_path, objects=None):
    # type: (str, Any) -> Dict[str, Any]
    """导出对象。

    :param file_path: 目标路径
    :param objects: 要导出的对象名列表，None 表示当前选择
    """
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    targets = _normalize_names(objects)

    def _impl():
        path = os.path.normpath(file_path)
        directory = os.path.dirname(path)
        if directory and not os.path.exists(directory):
            os.makedirs(directory)

        original_selection = cmds.ls(selection=True, long=True)
        if targets:
            missing = [n for n in targets if not cmds.objExists(n)]
            if missing:
                raise ValueError('对象不存在: {}'.format(', '.join(missing)))
            cmds.select(targets)
        else:
            if not original_selection:
                raise ValueError('未选择任何对象')

        ext = os.path.splitext(path)[1].lower()
        if ext in ('.ma', '.mb'):
            cmds.file(
                path,
                exportSelected=True,
                type='mayaAscii' if ext == '.ma' else 'mayaBinary',
                force=True,
            )
        elif ext == '.obj':
            cmds.file(path, exportSelected=True, type='OBJexport', force=True)
        elif ext == '.fbx':
            # FBX 导出依赖插件，先加载
            if not cmds.pluginInfo('fbxmaya', query=True, loaded=True):
                cmds.loadPlugin('fbxmaya')
            cmds.file(path, exportSelected=True, type='FBX export', force=True)
        else:
            raise ValueError('不支持的导出格式: {}'.format(ext))

        cmds.select(clear=True)
        if original_selection:
            cmds.select(original_selection)
        return {'ok': True, 'file_path': path}

    return run_on_main(_impl)


@tool(
    dcc=['maya'],
    description='创建文件引用（Reference）。',
    category='scene_io',
    dangerous=True,
    wrap_undo=False,
    examples=[{"summary": "引用角色模型", "args": {"file_path": 'C:/Work/char.ma', "namespace": 'char'}}],
    returns_desc="dict: {\"ok\": True, \"reference_node\": str}"
)
def create_reference(file_path, namespace=None):
    # type: (str, Optional[str]) -> Dict[str, Any]
    """创建文件引用。

    :param file_path: 文件路径
    :param namespace: 命名空间
    """
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    def _impl():
        path = os.path.normpath(file_path)
        if not os.path.exists(path):
            raise ValueError('文件不存在: {}'.format(path))
        kwargs = {}
        if namespace:
            kwargs['namespace'] = namespace
        ref_node = cmds.file(path, reference=True, **kwargs)
        return {'ok': True, 'reference_node': ref_node}

    return run_on_main(_impl)


@tool(
    dcc=['maya'],
    description='列出当前场景中的引用节点。',
    category='scene_io',
    wrap_undo=False,
    examples=[{"summary": "列出引用", "args": {}}],
    returns_desc="List[dict]: 引用信息列表"
)
def list_references():
    # type: () -> List[Dict[str, Any]]
    """列出文件引用。"""
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    def _impl():
        refs = cmds.ls(type='reference') or []
        result = []
        for ref in refs:
            if ref == 'sharedReferenceNode':
                continue
            try:
                path = cmds.referenceQuery(ref, filename=True)
            except Exception:  # pylint: disable=broad-except
                path = ''
            result.append({'reference_node': ref, 'file_path': path})
        return result

    return run_on_main(_impl)
