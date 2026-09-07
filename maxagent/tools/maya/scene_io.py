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
from ._common import _ensure_in_maya, _normalize_names
from ...tools.registry import tool


@tool(
    dcc=['maya'],
    description='保存当前 Maya 场景到 .ma 或 .mb 文件。',
    category='scene_io',
    dangerous=True,
    wrap_undo=False,
    examples=[{"summary": "保存场景", "args": {"file_path": 'C:/Work/scene.ma', "allow_overwrite": True}}],
    returns_desc="dict: {\"ok\": True, \"file_path\": str}",
    notes=['file_type: mayaAscii（.ma）或 mayaBinary（.mb）。', '路径不存在时会自动创建父目录。'],
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
    returns_desc="dict: {\"ok\": True, \"file_path\": str}",
    notes=['会丢弃当前场景的未保存修改，请先自行确认已保存。'],
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
    returns_desc="List[str]: 导入的顶层节点名列表",
    notes=['支持 .ma/.mb/.fbx/.obj/.abc；命名空间避免与现有对象冲突。'],
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
    returns_desc="dict: {\"ok\": True, \"file_path\": str}",
    notes=['需先选中要导出的对象，未选中时会报错。'],
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
    returns_desc="dict: {\"ok\": True, \"reference_node\": str}",
    notes=['引用是活链接，源文件更新后本场景也会跟着变。'],
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
    returns_desc="List[dict]: 引用信息列表",
    notes=['返回场景中所有 reference 的路径与命名空间。'],
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


@tool(
    dcc=['maya'],
    description='移除文件引用（删除引用节点及其全部内容，不可撤销提示后执行）。',
    category='scene_io',
    dangerous=True,
    wrap_undo=False,
    examples=[
        {'summary': '按引用节点名移除', 'args': {'reference': 'charRN'}},
        {'summary': '按文件路径匹配移除', 'args': {'file_path': 'C:/Work/char.ma'}},
    ],
    notes=[
        'reference 与 file_path 二选一；file_path 会做唯一匹配，多命中时报错列出候选。',
        '移除后引用内容从场景中消失（区别于 unload 仅隐藏）。',
        'dangerous=True，走审批流。',
    ],
    returns_desc='dict {"ok": True, "removed": 引用节点名, "was_loaded": 移除前是否加载}',
    prerequisites=['场景中必须存在目标引用'],
)
def remove_maya_reference(reference='', file_path=''):
    # type: (str, str) -> Dict[str, Any]
    """移除文件引用。

    :param reference: 引用节点名（list_references 返回的 reference_node）
    :param file_path: 引用文件路径（唯一匹配时使用）
    :returns: dict {"ok": True, "removed": ..., "was_loaded": ...}
    """
    _ensure_in_maya()

    if not reference and not file_path:
        raise ValueError('reference 与 file_path 至少提供一个')

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    def _impl():
        ref_node = reference
        if not ref_node:
            # 按路径匹配
            norm = os.path.normpath(str(file_path))
            matches = []
            for ref in (cmds.ls(type='reference') or []):
                if ref == 'sharedReferenceNode':
                    continue
                try:
                    p = cmds.referenceQuery(ref, filename=True)
                except Exception:  # pylint: disable=broad-except
                    continue
                if os.path.normpath(p) == norm:
                    matches.append(ref)
            if not matches:
                raise ValueError('没有找到该路径的引用: {}'.format(file_path))
            if len(matches) > 1:
                raise ValueError(
                    '路径命中多个引用，请指定 reference 节点名: {}'.format(
                        ', '.join(matches),
                    ),
                )
            ref_node = matches[0]
        else:
            if ref_node not in (cmds.ls(type='reference') or []):
                raise ValueError('引用节点不存在: {}'.format(ref_node))
        was_loaded = cmds.referenceQuery(ref_node, isLoaded=True)
        cmds.file(removeReference=True, referenceNode=ref_node)
        return {
            'ok': True,
            'removed': ref_node,
            'was_loaded': bool(was_loaded),
        }

    return run_on_main(_impl)


@tool(
    dcc=['maya'],
    description='新建空白 Maya 场景（相当于 File > New Scene）。',
    category='scene_io',
    dangerous=True,
    wrap_undo=False,
    examples=[{"summary": "新建场景（丢弃未保存修改）", "args": {"force": True}}],
    returns_desc='dict: {"ok": True}',
    notes=['未保存的修改会丢失；force=False 且场景有未保存修改时会报错提示。'],
)
def new_maya_scene(force=True):
    # type: (bool) -> Dict[str, Any]
    """新建空白场景。

    :param force: 是否强制丢弃未保存修改
    """
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    def _impl():
        if not force:
            # query=modified 检查是否有未保存的修改
            if cmds.file(query=True, modified=True):
                raise RuntimeError(
                    '当前场景有未保存的修改，请先保存或传 force=True 强制新建',
                )
        cmds.file(newFile=True, force=True)
        return {'ok': True}

    return run_on_main(_impl)


@tool(
    dcc=['maya'],
    description='在 Maya 中选中指定对象（相当于点击选中）。',
    category='scene_io',
    wrap_undo=False,
    examples=[
        {"summary": "选中单个对象", "args": {"objects": 'pCube1'}},
        {"summary": "追加选中（不清除现有选择）", "args": {"objects": 'pSphere1', 'add': True}},
    ],
    returns_desc='list[str]: 实际选中的对象长名列表',
    notes=['objects 支持逗号/分号分隔的字符串或列表；add=True 时在现有选择基础上追加。'],
)
def select_maya_objects(objects, add=False):
    # type: (Any, bool) -> List[str]
    """选中 Maya 对象。

    :param objects: 要选中的对象名（str/list）
    :param add: True 时追加选择而不替换
    """
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    targets = _normalize_names(objects)

    def _impl():
        if not targets:
            raise ValueError('objects 不能为空')
        missing = [n for n in targets if not cmds.objExists(n)]
        if missing:
            raise ValueError('对象不存在: {}'.format(', '.join(missing)))
        if add:
            cmds.select(targets, add=True)
        else:
            cmds.select(targets, replace=True)
        return list(cmds.ls(selection=True, long=True) or [])

    return run_on_main(_impl)


@tool(
    dcc=['maya'],
    description='清空 Maya 当前选择。',
    category='scene_io',
    wrap_undo=False,
    examples=[{"summary": "取消所有选择", "args": {}}],
    returns_desc='dict: {"ok": True}',
    notes=['无副作用，仅清除当前选择状态；可随时安全调用。'],
)
def clear_maya_selection():
    # type: () -> Dict[str, Any]
    """清空当前选择。"""
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    def _impl():
        cmds.select(clear=True)
        return {'ok': True}

    return run_on_main(_impl)


@tool(
    dcc=['maya'],
    description='删除 Maya 中的指定对象（含其子层级）。',
    category='scene_io',
    dangerous=True,
    examples=[{"summary": "删除对象", "args": {"objects": 'pCube1'}}],
    returns_desc='dict: {"ok": True, "deleted": [...]}',
    notes=['删除 transform 会连带其所有子节点；undo 可恢复（wrap_undo 默认开启）。'],
)
def delete_maya_objects(objects):
    # type: (Any) -> Dict[str, Any]
    """删除 Maya 对象。

    :param objects: 要删除的对象名（str/list）
    """
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    targets = _normalize_names(objects)

    def _impl():
        if not targets:
            raise ValueError('objects 不能为空')
        missing = [n for n in targets if not cmds.objExists(n)]
        if missing:
            raise ValueError('对象不存在: {}'.format(', '.join(missing)))
        cmds.delete(targets)
        return {'ok': True, 'deleted': targets}

    return run_on_main(_impl)


@tool(
    dcc=['maya'],
    description='设置 Maya 对象的可见性或模板/引用状态（相当于显示/隐藏、模板化）。',
    category='scene_io',
    examples=[
        {"summary": "隐藏对象", "args": {"objects": 'pCube1', 'visible': False}},
        {"summary": "模板化对象（只显示线框不可选）", "args": {"objects": 'pCube1', 'template': True}},
    ],
    returns_desc='dict: {"ok": True, "updated": [...]}',
    notes=[
        'visible 控制_visibility 属性；template/control 控制 displayType（0 正常 1 模板 2 引用）。',
        'visibility 修改可被 undo；displayType 同样可 undo。',
    ],
)
def set_maya_visibility(objects, visible=None, template=None):
    # type: (Any, Optional[bool], Optional[bool]) -> Dict[str, Any]
    """设置 Maya 对象可见性/模板状态。

    :param objects: 对象名（str/list）
    :param visible: True 显示 / False 隐藏；None 不修改
    :param template: True 模板化 / False 取消模板；None 不修改
    """
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    targets = _normalize_names(objects)

    def _impl():
        if not targets:
            raise ValueError('objects 不能为空')
        missing = [n for n in targets if not cmds.objExists(n)]
        if missing:
            raise ValueError('对象不存在: {}'.format(', '.join(missing)))
        if visible is None and template is None:
            raise ValueError('visible 与 template 至少指定一个')
        for node in targets:
            if visible is not None:
                cmds.setAttr(node + '.visibility', bool(visible))
            if template is not None:
                # display override 属性在 shape 节点上（0=normal, 1=template, 2=reference）
                shapes = cmds.listRelatives(node, shapes=True, fullPath=True) or []
                shape_targets = shapes if shapes else [node]
                for shape in shape_targets:
                    if template:
                        cmds.setAttr(shape + '.overrideEnabled', True)
                        cmds.setAttr(shape + '.overrideDisplayType', 1)
                    else:
                        # 取消模板时关闭 override，恢复默认显示
                        cmds.setAttr(shape + '.overrideEnabled', False)
        return {'ok': True, 'updated': targets}

    return run_on_main(_impl)


@tool(
    dcc=['maya'],
    description='把 Maya 对象打组（创建空组并作为其父级，相当于 Ctrl+G）。',
    category='scene_io',
    examples=[
        {"summary": "把两个对象打到一个新组", "args": {"objects": 'pCube1,pSphere1', 'group_name': 'props_grp'}},
        {"summary": "创建空组（用于组织层级）", "args": {"objects": '', 'group_name': 'assets_grp'}},
    ],
    returns_desc='dict: {"ok": True, "group": 组节点名}',
    notes=['objects 为空时创建空组；组默认创建在世界原点。'],
)
def group_maya_objects(objects, group_name=None, parent=None):
    # type: (Any, Optional[str], Optional[str]) -> Dict[str, Any]
    """把对象打组。

    :param objects: 要打组的对象名（str/list），空则创建空组
    :param group_name: 新组名称；None 自动命名（如 group1）
    :param parent: 新组的父节点；None 挂在世界层级
    """
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    targets = _normalize_names(objects)

    def _impl():
        kwargs: Dict[str, Any] = {'empty': not targets}
        if group_name:
            kwargs['name'] = group_name
        if parent:
            if not cmds.objExists(parent):
                raise ValueError('父节点不存在: {}'.format(parent))
            kwargs['parent'] = parent
        if targets:
            missing = [n for n in targets if not cmds.objExists(n)]
            if missing:
                raise ValueError('对象不存在: {}'.format(', '.join(missing)))
        # group 命令对选中的对象打组；显式传 targets 更稳
        result = cmds.group(*targets, **kwargs) if targets else cmds.group(**kwargs)
        return {'ok': True, 'group': result}

    return run_on_main(_impl)


@tool(
    dcc=['maya'],
    description='在 Maya 中建立/解除父子关系（parent / unparent）。',
    category='scene_io',
    examples=[
        {"summary": "把 pCube1 挂到 grp_a 下", "args": {"objects": 'pCube1', 'parent': 'grp_a'}},
        {"summary": "把 pCube1 移出到世界层级", "args": {"objects": 'pCube1', 'unparent': True}},
        {"summary": "保持世界位置不动地移出", "args": {"objects": 'pCube1', 'unparent': True, 'world': True}},
    ],
    returns_desc='dict: {"ok": True, "parented": [...], "parent": str | None}',
    notes=[
        'unparent=True 时执行解除父子；world=True 配合 unparent 保持世界变换不变。',
        'parent 与 unparent 二选一；都未指定时报错。',
    ],
)
def parent_maya_objects(objects, parent=None, unparent=False, world=False):
    # type: (Any, Optional[str], bool, bool) -> Dict[str, Any]
    """建立/解除父子关系。

    :param objects: 子对象名（str/list）
    :param parent: 父节点名；unparent=False 时必填
    :param unparent: True 表示解除父子
    :param world: unparent 时是否保持世界位置（-world 标志）
    """
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    targets = _normalize_names(objects)

    def _impl():
        if not targets:
            raise ValueError('objects 不能为空')
        missing = [n for n in targets if not cmds.objExists(n)]
        if missing:
            raise ValueError('对象不存在: {}'.format(', '.join(missing)))
        if unparent:
            if parent:
                raise ValueError('unparent=True 时不要再传 parent')
            if world:
                cmds.parent(targets, world=True)
            else:
                cmds.parent(targets, remove=True)
            return {'ok': True, 'parented': targets, 'parent': None}
        if not parent:
            raise ValueError('必须指定 parent 或 unparent=True')
        if not cmds.objExists(parent):
            raise ValueError('父节点不存在: {}'.format(parent))
        for node in targets:
            if node == parent or cmds.ls(node, long=True)[0].startswith(
                cmds.ls(parent, long=True)[0] + '|',
            ):
                raise ValueError(
                    '不能把节点父化到它自己的子层级: {}'.format(node),
                )
        cmds.parent(targets, parent)
        return {'ok': True, 'parented': targets, 'parent': parent}

    return run_on_main(_impl)


@tool(
    dcc=['maya'],
    description='复制 Maya 对象（duplicate），可选实例化或复制上游连接。',
    category='scene_io',
    examples=[
        {
            'summary': '复制 pCube1 并命名为 pCube1_copy',
            'args': {'objects': 'pCube1', 'new_name': 'pCube1_copy'},
        },
        {
            'summary': '实例化复制（改一个另一个跟着变）',
            'args': {'objects': 'pCube1', 'instance': True},
        },
    ],
    returns_desc='dict: {"ok": True, "duplicates": [新对象名列表]}',
    notes=[
        'instance=True 时创建实例（instanceLeaf 控制是否只实例化叶子）。',
        'un=False（默认）不复制上游历史连接；upstreamNodes=True 时连同上游节点一起复制。',
        '多对象复制时 new_name 只对第一个对象生效，其余由 Maya 自动命名。',
    ],
)
def duplicate_maya_objects(objects, new_name=None, instance=False, un=False, upstream_nodes=False):
    # type: (Any, Optional[str], bool, bool, bool) -> Dict[str, Any]
    """复制 Maya 对象。

    :param objects: 要复制的对象名（str/list）
    :param new_name: 新对象名；None 自动命名（如 pCube2）
    :param instance: True 创建实例副本
    :param un: True 复制上游连接
    :param upstream_nodes: 同 un 的完整写法；与 un 任一为 True 即生效
    :returns: dict {"ok": True, "duplicates": [...]}
    """
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    targets = _normalize_names(objects)

    def _impl():
        if not targets:
            raise ValueError('objects 不能为空')
        missing = [n for n in targets if not cmds.objExists(n)]
        if missing:
            raise ValueError('对象不存在: {}'.format(', '.join(missing)))
        kwargs: Dict[str, Any] = {}
        if new_name:
            kwargs['name'] = new_name
        if instance:
            kwargs['instanceLeaf'] = True
        if un or upstream_nodes:
            kwargs['un'] = True
        result = cmds.duplicate(*targets, **kwargs)
        return {'ok': True, 'duplicates': list(result)}

    return run_on_main(_impl)


__all__ = [
    'save_maya_file',
    'open_maya_file',
    'import_file',
    'export_selected',
    'create_reference',
    'list_references',
    'remove_maya_reference',
    'new_maya_scene',
    'select_maya_objects',
    'clear_maya_selection',
    'delete_maya_objects',
    'set_maya_visibility',
    'group_maya_objects',
    'parent_maya_objects',
    'duplicate_maya_objects',
]
