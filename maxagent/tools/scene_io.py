#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""场景管理类工具：保存/加载/合并/导入导出/删除/隐藏/冻结/分组/选择。

危险标记说明：
- save_max_file 会覆盖文件，标 dangerous
- delete_object 会删除节点，标 dangerous
- import_file / merge_file 会引入外部数据，标 dangerous
- 仅"修改可见性/选择/分组"等可通过撤销恢复的操作不标 dangerous
"""

from __future__ import absolute_import
from __future__ import print_function

import os
from typing import List
from typing import Optional

from ..runtime_helpers import IN_MAX
from ..runtime_helpers import rt
from .registry import tool


def _ensure_in_max():
    if not IN_MAX:
        raise RuntimeError('非 3ds Max 环境')


def _get_node(name):
    node = rt.getNodeByName(name, exact=True, all=False)
    if node is None:
        raise ValueError('对象不存在: {}'.format(name))
    return node


def _normalize_names(names):
    """把 names 归一化为 list[str]，兼容 LLM 的多种输入形式。

    支持的输入：
    - list/tuple of str: 直接使用
    - 单个 str: 按逗号/分号/中文逗号切分；若都没有则视为单元素列表
    - None / 空: 返回空列表

    防御 LLM 把 ["A", "B"] 在 JSON schema 中错误传成 "A,B" 字符串
    导致 ``for n in names`` 逐字符迭代的经典 bug。
    """
    if names is None:
        return []
    if isinstance(names, (list, tuple)):
        return [str(x).strip() for x in names if str(x).strip()]
    if isinstance(names, str):
        s = names.strip()
        if not s:
            return []
        # 优先按分隔符切分
        for sep in (',', ';', '，', '；'):
            if sep in s:
                return [p.strip() for p in s.split(sep) if p.strip()]
        return [s]
    # 其他类型尝试转 str
    return [str(names)]


def _get_nodes(names):
    items = _normalize_names(names)
    if not items:
        return []
    out = []
    missing = []
    for n in items:
        node = rt.getNodeByName(n, exact=True, all=False)
        if node is None:
            missing.append(n)
        else:
            out.append(node)
    if missing:
        raise ValueError('对象不存在: {}'.format(', '.join(missing)))
    return out


@tool(
    description='保存当前 Max 场景到 .max 文件。',
    category='scene_io',
    dangerous=True,
    wrap_undo=False,
)
def save_max_file(file_path, allow_overwrite=True):
    """保存场景。

    :param file_path: 目标 .max 文件绝对路径
    :param allow_overwrite: 文件已存在时是否覆盖
    :returns: dict {"file": ..., "ok": True}
    """
    _ensure_in_max()
    if os.path.exists(file_path) and not allow_overwrite:
        raise ValueError('文件已存在且不允许覆盖: {}'.format(file_path))
    out_dir = os.path.dirname(file_path)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    ok = rt.saveMaxFile(file_path)
    return {'file': file_path, 'ok': bool(ok)}


@tool(
    description='打开一个 .max 文件，替换当前场景（会丢弃当前未保存修改）。',
    category='scene_io',
    dangerous=True,
    wrap_undo=False,
)
def load_max_file(file_path, quiet=True):
    """加载场景。

    :param file_path: .max 文件绝对路径
    :param quiet: True 时不弹保存提示对话框
    :returns: dict {"file": ..., "ok": True}
    """
    _ensure_in_max()
    if not os.path.isfile(file_path):
        raise ValueError('文件不存在: {}'.format(file_path))
    ok = rt.loadMaxFile(file_path, quiet=bool(quiet))
    return {'file': file_path, 'ok': bool(ok)}


@tool(
    description=(
        '把另一个 .max 文件合并到当前场景中（mergeMaxFile）。'
        '常用于合并资产到主场景。'
    ),
    category='scene_io',
    dangerous=True,
)
def merge_max_file(file_path, mode='prompt'):
    """合并另一个 max 文件。

    :param file_path: 要合并的 .max 文件
    :param mode: 重名处理: 'prompt' 弹窗 / 'rename' 自动重命名 / 'merge' 合并
    :returns: dict {"file": ..., "ok": True}
    """
    _ensure_in_max()
    if not os.path.isfile(file_path):
        raise ValueError('文件不存在: {}'.format(file_path))
    mode_map = {
        'prompt': rt.Name('prompt'),
        'rename': rt.Name('autoRenameDups'),
        'merge': rt.Name('mergeDups'),
    }
    flag = mode_map.get(mode, rt.Name('prompt'))
    ok = rt.mergeMaxFile(file_path, dupMtlAction=flag)
    return {'file': file_path, 'ok': bool(ok)}


@tool(
    description=(
        '导入外部 3D 文件到当前场景（FBX / OBJ / 3DS / DAE 等，'
        '取决于已安装的 importer）。'
    ),
    category='scene_io',
    dangerous=True,
)
def import_file(file_path):
    """导入外部模型文件。

    :param file_path: 文件绝对路径
    :returns: dict {"file": ..., "ok": True}
    """
    _ensure_in_max()
    if not os.path.isfile(file_path):
        raise ValueError('文件不存在: {}'.format(file_path))
    ok = rt.importFile(file_path, rt.Name('noPrompt'))
    return {'file': file_path, 'ok': bool(ok)}


@tool(
    description=(
        '导出场景或选中对象到外部 3D 文件（FBX / OBJ 等）。'
        'selected_only=True 时只导出当前选中对象。'
    ),
    category='scene_io',
    dangerous=True,
    wrap_undo=False,
)
def export_file(file_path, selected_only=False):
    """导出文件。

    :param file_path: 输出文件绝对路径
    :param selected_only: True 时仅导出选中对象
    :returns: dict {"file": ..., "ok": True}
    """
    _ensure_in_max()
    out_dir = os.path.dirname(file_path)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    if selected_only:
        ok = rt.exportFile(
            file_path, rt.Name('noPrompt'), selectedOnly=True,
        )
    else:
        ok = rt.exportFile(file_path, rt.Name('noPrompt'))
    return {'file': file_path, 'ok': bool(ok)}


@tool(
    description=(
        '按名字删除一个或多个对象。被删除对象不可通过 Ctrl+Z 之外的方式恢复。'
        'names 接受字符串数组 ["Box01","Box02"] 或逗号分隔字符串 "Box01,Box02"。'
    ),
    category='scene_io',
    dangerous=True,
)
def delete_objects(names):
    """删除对象。

    :param names: 对象名列表（list 或逗号分隔字符串均可）
    :returns: dict {"deleted": N, "names": [...]}
    """
    _ensure_in_max()
    if not names:
        raise ValueError('names 不能为空')
    nodes = _get_nodes(names)
    deleted = []
    for n in nodes:
        nm = str(n.name)
        rt.delete(n)
        deleted.append(nm)
    return {'deleted': len(deleted), 'names': deleted}


@tool(
    description='隐藏或显示对象。',
    category='scene_io',
)
def set_object_visibility(names, hidden=True):
    """设置可见性。

    :param names: 对象名列表
    :param hidden: True 隐藏；False 显示
    :returns: dict {"affected": N}
    """
    _ensure_in_max()
    nodes = _get_nodes(names)
    for n in nodes:
        n.isHidden = bool(hidden)
    return {'affected': len(nodes), 'hidden': bool(hidden)}


@tool(
    description='冻结或解冻对象（冻结对象在视口中显示为灰色，无法选中）。',
    category='scene_io',
)
def set_object_frozen(names, frozen=True):
    """设置冻结状态。

    :param names: 对象名列表
    :param frozen: True 冻结；False 解冻
    :returns: dict {"affected": N}
    """
    _ensure_in_max()
    nodes = _get_nodes(names)
    for n in nodes:
        n.isFrozen = bool(frozen)
    return {'affected': len(nodes), 'frozen': bool(frozen)}


@tool(
    description=(
        '把指定对象设为当前选中。'
        'names 接受字符串数组 ["Box01","Box02"] 或逗号分隔字符串 "Box01,Box02"。'
    ),
    category='scene_io',
    wrap_undo=False,
)
def select_objects(names, add_to_selection=False):
    """设置选中对象。

    :param names: 对象名列表
    :param add_to_selection: True 时追加到当前选中；False 替换当前选中
    :returns: dict {"selected": N}
    """
    _ensure_in_max()
    nodes = _get_nodes(names)
    if add_to_selection:
        for n in nodes:
            rt.selectMore(n)
    else:
        rt.select(nodes)
    return {'selected': len(nodes)}


@tool(
    description='清空当前选中。',
    category='scene_io',
    wrap_undo=False,
)
def clear_selection():
    """清空选中。

    :returns: dict {"ok": True}
    """
    _ensure_in_max()
    rt.clearSelection()
    return {'ok': True}


@tool(
    description='把多个对象组合成一个 Group（可整体移动/选择）。',
    category='scene_io',
)
def group_objects(names, group_name='AgentGroup'):
    """创建分组。

    :param names: 对象名列表
    :param group_name: 组名
    :returns: dict {"group": ..., "members": N}
    """
    _ensure_in_max()
    nodes = _get_nodes(names)
    rt.group(nodes, name=group_name)
    return {'group': group_name, 'members': len(nodes)}


@tool(
    description='重命名对象。',
    category='scene_io',
)
def rename_object(old_name, new_name):
    """重命名对象。

    :param old_name: 当前对象名
    :param new_name: 新名字
    :returns: dict {"old": ..., "new": ...}
    """
    _ensure_in_max()
    node = _get_node(old_name)
    node.name = new_name
    return {'old': old_name, 'new': str(node.name)}


@tool(
    description='新建一个空场景（Reset Max）。会丢弃当前所有未保存修改。',
    category='scene_io',
    dangerous=True,
    wrap_undo=False,
)
def reset_scene(quiet=True):
    """重置场景。

    :param quiet: True 时不弹保存提示
    :returns: dict {"ok": True}
    """
    _ensure_in_max()
    rt.resetMaxFile(rt.Name('noPrompt') if quiet else rt.Name('prompt'))
    return {'ok': True}
