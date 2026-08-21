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

from ...runtime_helpers import IN_MAX
from ...runtime_helpers import rt
from ...tools.registry import tool


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
        # pymxs getNodeByName 在部分场景下会返回 MXSWrapperBase
        # （不是真正的 node，例如 selection set 冲名），做类型校验
        if node is None:
            missing.append(n)
            continue
        try:
            if not bool(rt.isValidNode(node)):
                missing.append(n)
                continue
        except Exception:  # pylint: disable=broad-except
            # isValidNode 本身抛异常说明拿到的对象根本不是 node
            missing.append(n)
            continue
        out.append(node)
    if missing:
        raise ValueError('对象不存在或名称冲突: {}'.format(', '.join(missing)))
    return out


@tool(
    dcc=['3dsmax'],
    description='保存当前 Max 场景到 .max 文件。',
    category='scene_io',
    dangerous=True,
    wrap_undo=False,
    examples=[{"summary": "典型调用", "args": {"file_path": 'C:/Work/scene.max', "allow_overwrite": True}}],
notes=['file_path 建议使用绝对路径，目录不存在会自动创建。', '调用失败时应先检查对象/文件是否存在。'],
returns_desc="dict {\"ok\": True, ...}"
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
    dcc=['3dsmax'],
    description='打开一个 .max 文件，替换当前场景（会丢弃当前未保存修改）。',
    category='scene_io',
    dangerous=True,
    wrap_undo=False,
    examples=[{"summary": "典型调用", "args": {"file_path": 'C:/Work/scene.max', "quiet": True}}],
notes=['file_path 建议使用绝对路径，目录不存在会自动创建。', '调用失败时应先检查对象/文件是否存在。'],
returns_desc="dict {\"ok\": True, ...}",
prerequisites=['file_path 指向的文件必须存在']
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
    dcc=['3dsmax'],
    description=(
        '把另一个 .max 文件合并到当前场景中（mergeMaxFile）。'
        '常用于合并资产到主场景。'
    ),
    category='scene_io',
    dangerous=True,
    examples=[{"summary": "典型调用", "args": {"file_path": 'C:/Work/scene.max', "mode": 'prompt'}}],
notes=['file_path 建议使用绝对路径，目录不存在会自动创建。', '调用失败时应先检查对象/文件是否存在。'],
returns_desc="dict {\"ok\": True, ...}",
prerequisites=['file_path 指向的文件必须存在']
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
    dcc=['3dsmax'],
    description=(
        '导入外部 3D 文件到当前场景（FBX / OBJ / 3DS / DAE 等，'
        '取决于已安装的 importer）。'
    ),
    category='scene_io',
    dangerous=True,
    examples=[{"summary": "典型调用", "args": {"file_path": 'C:/Work/scene.max'}}],
notes=['file_path 建议使用绝对路径，目录不存在会自动创建。', '调用失败时应先检查对象/文件是否存在。'],
returns_desc="dict {\"ok\": True, ...}",
prerequisites=['file_path 指向的文件必须存在']
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
    dcc=['3dsmax'],
    description=(
        '导出场景或选中对象到外部 3D 文件（FBX / OBJ 等）。'
        'selected_only=True 时只导出当前选中对象。'
    ),
    category='scene_io',
    dangerous=True,
    wrap_undo=False,
    examples=[{"summary": "典型调用", "args": {"file_path": 'C:/Work/scene.max', "selected_only": False}}],
notes=['file_path 建议使用绝对路径，目录不存在会自动创建。', '调用失败时应先检查对象/文件是否存在。'],
returns_desc="dict {\"ok\": True, ...}"
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
    dcc=['3dsmax'],
    description=(
        '按名字删除一个或多个对象。被删除对象不可通过 Ctrl+Z 之外的方式恢复。'
        'names 接受字符串数组 ["Box01","Box02"] 或逗号分隔字符串 "Box01,Box02"。'
    ),
    category='scene_io',
    dangerous=True,
    examples=[{"summary": "典型调用", "args": {"names": ['Box01', 'Box02']}}],
notes=['names 支持对象名列表或逗号分隔字符串。', '调用失败时应先检查对象/文件是否存在。'],
returns_desc="dict {\"ok\": True, ...}"
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
    dcc=['3dsmax'],
    description='隐藏或显示对象。',
    category='scene_io',
    examples=[{"summary": "典型调用", "args": {"names": ['Box01', 'Box02'], "hidden": True}}],
notes=['names 支持对象名列表或逗号分隔字符串。', '调用失败时应先检查对象/文件是否存在。'],
returns_desc="dict {\"ok\": True, ...}"
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
    dcc=['3dsmax'],
    description='冻结或解冻对象（冻结对象在视口中显示为灰色，无法选中）。',
    category='scene_io',
    examples=[{"summary": "典型调用", "args": {"names": ['Box01', 'Box02'], "frozen": True}}],
notes=['names 支持对象名列表或逗号分隔字符串。', '调用失败时应先检查对象/文件是否存在。'],
returns_desc="dict {\"ok\": True, ...}"
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
    dcc=['3dsmax'],
    description=(
        '把指定对象设为当前选中。'
        'names 接受字符串数组 ["Box01","Box02"] 或逗号分隔字符串 "Box01,Box02"。'
    ),
    category='scene_io',
    wrap_undo=False,
    examples=[{"summary": "典型调用", "args": {"names": ['Box01', 'Box02'], "add_to_selection": False}}],
notes=['names 支持对象名列表或逗号分隔字符串。', '调用失败时应先检查对象/文件是否存在。'],
returns_desc="dict {\"ok\": True, ...}"
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
    dcc=['3dsmax'],
    description='清空当前选中。',
    category='scene_io',
    wrap_undo=False,
    examples=[{'summary': '取消所有对象选中', 'args': {}}],
    notes=[
        '调用后当前选择集为空。',
        '此操作不会修改场景对象本身。',
    ],
    returns_desc='dict {"ok": True}',
)
def clear_selection():
    """清空选中。

    :returns: dict {"ok": True}
    """
    _ensure_in_max()
    rt.clearSelection()
    return {'ok': True}


@tool(
    dcc=['3dsmax'],
    description='把多个对象组合成一个 Group（可整体移动/选择）。',
    category='scene_io',
    examples=[{"summary": "典型调用", "args": {"names": ['Box01', 'Box02'], "group_name": 'AgentGroup'}}],
notes=['names 支持对象名列表或逗号分隔字符串。', '调用失败时应先检查对象/文件是否存在。'],
returns_desc="dict {\"ok\": True, ...}"
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
    dcc=['3dsmax'],
    description='重命名对象。',
    category='scene_io',
    examples=[{"summary": "典型调用", "args": {"old_name": 'Box01', "new_name": 'Box02'}}],
notes=['参数必须严格符合 JSON Schema 声明的类型。', '调用失败时应先检查对象/文件是否存在。'],
returns_desc="dict {\"ok\": True, ...}"
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
    dcc=['3dsmax'],
    description='新建一个空场景（Reset Max）。会丢弃当前所有未保存修改。',
    category='scene_io',
    dangerous=True,
    wrap_undo=False,
    examples=[{"summary": "典型调用", "args": {"quiet": True}}],
notes=['参数必须严格符合 JSON Schema 声明的类型。', '调用失败时应先检查对象/文件是否存在。'],
returns_desc="dict {\"ok\": True, ...}"
)
def reset_scene(quiet=True):
    """重置场景。

    :param quiet: True 时不弹保存提示
    :returns: dict {"ok": True}
    """
    _ensure_in_max()
    rt.resetMaxFile(rt.Name('noPrompt') if quiet else rt.Name('prompt'))
    return {'ok': True}


@tool(
    dcc=['3dsmax'],
    description=(
        '导出当前场景或选中对象到 USD 文件（.usd / .usdc / .usda）。\n'
        '需要 3ds Max 2022+ 且已安装并启用 USD for 3ds Max 插件。'
    ),
    category='scene_io',
    dangerous=True,
    wrap_undo=False,
    examples=[{"summary": "典型调用", "args": {"file_path": 'C:/Work/scene.max', "selected_only": False, "export_materials": True, "up_axis": 'Y'}}],
notes=['file_path 建议使用绝对路径，目录不存在会自动创建。', '调用失败时应先检查对象/文件是否存在。'],
returns_desc="dict {\"ok\": True, ...}"
)
def export_usd(file_path, selected_only=False, export_materials=True, up_axis='Y'):
    """导出 USD 文件。

    调用 3ds Max 的 ``exportFile``，并传入 ``#noPrompt`` 避免弹窗。
    USD 插件通常在 3ds Max 2022+ 提供，旧版本或未启用插件时调用会失败。

    :param file_path: 输出 USD 文件绝对路径，扩展名决定格式
        （.usd / .usdc 二进制 / .usda ASCII）
    :param selected_only: True 时仅导出选中对象
    :param export_materials: True 时尝试导出材质（取决于 Max 版本与插件支持）
    :param up_axis: 上轴方向，'Y' 或 'Z'；Max 默认 Y-up
    :returns: dict {"file": ..., "ok": True}
    """
    _ensure_in_max()
    out_dir = os.path.dirname(file_path)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in ('.usd', '.usdc', '.usda'):
        raise ValueError('USD 导出仅支持 .usd/.usdc/.usda 扩展名: {}'.format(file_path))

    kwargs = {}
    if selected_only:
        kwargs['selectedOnly'] = True
    if export_materials:
        kwargs['exportMaterials'] = True
    if up_axis and isinstance(up_axis, str):
        kwargs['upAxis'] = rt.Name(up_axis)

    ok = rt.exportFile(file_path, rt.Name('noPrompt'), **kwargs)
    return {'file': file_path, 'ok': bool(ok)}


@tool(
    dcc=['3dsmax'],
    description=(
        '从 USD 文件导入场景（.usd / .usdc / .usda）。\n'
        '需要 3ds Max 2022+ 且已安装并启用 USD for 3ds Max 插件。'
    ),
    category='scene_io',
    dangerous=True,
    examples=[{"summary": "典型调用", "args": {"file_path": 'C:/Work/scene.max', "import_materials": True}}],
notes=['file_path 建议使用绝对路径，目录不存在会自动创建。', '调用失败时应先检查对象/文件是否存在。'],
returns_desc="dict {\"ok\": True, ...}",
prerequisites=['file_path 指向的文件必须存在']
)
def import_usd(file_path, import_materials=True):
    """导入 USD 文件。

    调用 3ds Max 的 ``importFile``，并传入 ``#noPrompt`` 避免弹窗。
    如果 USD 插件未启用，调用会抛出 MaxScript 异常。

    :param file_path: USD 文件绝对路径
    :param import_materials: True 时尝试导入 USD 中携带的材质
    :returns: dict {"file": ..., "ok": True}
    """
    _ensure_in_max()
    if not os.path.isfile(file_path):
        raise ValueError('文件不存在: {}'.format(file_path))
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in ('.usd', '.usdc', '.usda'):
        raise ValueError('USD 导入仅支持 .usd/.usdc/.usda 扩展名: {}'.format(file_path))

    kwargs = {}
    if import_materials:
        kwargs['importMaterials'] = True

    ok = rt.importFile(file_path, rt.Name('noPrompt'), **kwargs)
    return {'file': file_path, 'ok': bool(ok)}


@tool(
    dcc=['3dsmax'],
    description=(
        '导出当前场景或选中对象到 Alembic 文件（.abc）。\n'
        '需要已安装并启用 Alembic 导出器（3ds Max 2018+ 通常内置）。'
    ),
    category='scene_io',
    dangerous=True,
    wrap_undo=False,
    examples=[{"summary": "典型调用", "args": {"file_path": 'C:/Work/scene.max', "selected_only": False, "frame_range": 'value'}}],
notes=['file_path 建议使用绝对路径，目录不存在会自动创建。', '调用失败时应先检查对象/文件是否存在。'],
returns_desc="dict {\"ok\": True, ...}"
)
def export_alembic(file_path, selected_only=False, frame_range=None):
    """导出 Alembic 文件。

    调用 3ds Max 的 ``exportFile``，并传入 ``#noPrompt`` 避免弹窗。
    frame_range 会映射为 ``frameRange: [start, end]`` 关键字参数。

    :param file_path: 输出 .abc 文件绝对路径
    :param selected_only: True 时仅导出选中对象
    :param frame_range: 可选帧范围元组 (start, end)，例如 (0, 120)
    :returns: dict {"file": ..., "ok": True}
    """
    _ensure_in_max()
    out_dir = os.path.dirname(file_path)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    if os.path.splitext(file_path)[1].lower() != '.abc':
        raise ValueError('Alembic 导出仅支持 .abc 扩展名: {}'.format(file_path))

    kwargs = {}
    if selected_only:
        kwargs['selectedOnly'] = True
    if frame_range is not None:
        try:
            start, end = frame_range
            kwargs['frameRange'] = [int(start), int(end)]
        except Exception as exc:
            raise ValueError('frame_range 必须是 (start, end) 整数元组: {}'.format(exc))

    ok = rt.exportFile(file_path, rt.Name('noPrompt'), **kwargs)
    return {'file': file_path, 'ok': bool(ok)}


@tool(
    dcc=['3dsmax'],
    description=(
        '从 Alembic 文件导入几何体与动画（.abc）。\n'
        '需要已安装并启用 Alembic 导入器（3ds Max 2018+ 通常内置）。'
    ),
    category='scene_io',
    dangerous=True,
    examples=[{"summary": "典型调用", "args": {"file_path": 'C:/Work/scene.max', "import_normals": True}}],
notes=['file_path 建议使用绝对路径，目录不存在会自动创建。', '调用失败时应先检查对象/文件是否存在。'],
returns_desc="dict {\"ok\": True, ...}",
prerequisites=['file_path 指向的文件必须存在']
)
def import_alembic(file_path, import_normals=True):
    """导入 Alembic 文件。

    调用 3ds Max 的 ``importFile``，并传入 ``#noPrompt`` 避免弹窗。
    import_normals 会映射为 ``importNormals`` 关键字参数。

    :param file_path: .abc 文件绝对路径
    :param import_normals: True 时导入法线数据
    :returns: dict {"file": ..., "ok": True}
    """
    _ensure_in_max()
    if not os.path.isfile(file_path):
        raise ValueError('文件不存在: {}'.format(file_path))
    if os.path.splitext(file_path)[1].lower() != '.abc':
        raise ValueError('Alembic 导入仅支持 .abc 扩展名: {}'.format(file_path))

    kwargs = {}
    if import_normals:
        kwargs['importNormals'] = True

    ok = rt.importFile(file_path, rt.Name('noPrompt'), **kwargs)
    return {'file': file_path, 'ok': bool(ok)}
