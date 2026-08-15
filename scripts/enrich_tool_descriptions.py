#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量补齐 @tool 装饰器的结构化说明字段。

用法:
    python scripts/enrich_tool_descriptions.py maxagent/tools/scene_io.py
"""

from __future__ import absolute_import
from __future__ import print_function

import ast
import re
import sys


# 常见参数的示例值
_SAMPLE_VALUES = {
    'name': 'Box01',
    'names': ['Box01', 'Box02'],
    'file_path': 'C:/Work/scene.max',
    'output_path': 'C:/Work/render.png',
    'output_dir': 'C:/Work/frames',
    'file_basename': 'frame',
    'file_ext': 'png',
    'width': 1920,
    'height': 1080,
    'length': 10.0,
    'width_param': 10.0,
    'height_param': 10.0,
    'radius': 5.0,
    'frame': 30,
    'start_frame': 0,
    'end_frame': 100,
    'time': 30,
    'duration': 1.0,
    'position': '[50, 0, 0]',
    'rotation_euler': '[0, 45, 0]',
    'scale': '[1.5, 1.5, 1.5]',
    'offset': '[10, 0, 0]',
    'color': '[255, 128, 0]',
    'color_hex': '#FF8000',
    'material_name': 'RedMaterial',
    'texture_path': 'C:/Work/textures/diffuse.png',
    'camera': 'Camera01',
    'light_type': 'omni',
    'camera_type': 'free',
    'controller': 'position',
    'modifier_name': 'Turbosmooth',
    'modifier_class': 'Turbosmooth',
    'query': 'Box.position',
    'keyword': '材质',
    'keywords': ['材质', '贴图'],
    'search_term': '渲染设置',
    'code': 'print("hello")',
    'script': 'print("hello")',
    'old_name': 'Box01',
    'new_name': 'Box02',
    'group_name': 'AgentGroup',
    'url': 'https://example.com',
    'project_id': '12345',
    'limit': 10,
    'topk': 5,
}


_TYPE_DEFAULTS = {
    'string': 'value',
    'integer': 1,
    'number': 1.0,
    'boolean': True,
    'array': [],
    'object': {},
}


def _is_simple_default(value):
    return isinstance(value, (str, int, float, bool, list, dict, type(None)))


def _literal_repr(value):
    if isinstance(value, str):
        return repr(value)
    if isinstance(value, bool):
        return repr(value)
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, list):
        return '[{}]'.format(', '.join(_literal_repr(v) for v in value))
    if value is None:
        return 'None'
    if isinstance(value, dict):
        return repr(value)
    return repr(value)


def _infer_example_args(decorator_node, func_node):
    """根据 @tool 和函数签名生成示例参数字典。"""
    args = {}
    for idx, kw in enumerate(decorator_node.keywords):
        if kw.arg == 'parameters' and isinstance(kw.value, ast.Dict):
            props = None
            for k, v in zip(kw.value.keys, kw.value.values):
                if isinstance(k, ast.Constant) and k.value == 'properties':
                    props = v
                    break
            if props and isinstance(props, ast.Dict):
                for k, v in zip(props.keys, props.values):
                    if not isinstance(k, ast.Constant):
                        continue
                    pname = k.value
                    # 解析 schema
                    schema = _schema_from_ast(v)
                    if pname in _SAMPLE_VALUES:
                        args[pname] = _SAMPLE_VALUES[pname]
                    else:
                        args[pname] = _default_for_schema(schema)
            return args

    # 没有显式 parameters，从函数签名推导
    defaults_start = len(func_node.args.args) - len(func_node.args.defaults)
    for idx, arg in enumerate(func_node.args.args):
        pname = arg.arg
        default = None
        if idx >= defaults_start:
            d = func_node.args.defaults[idx - defaults_start]
            try:
                default = ast.literal_eval(d)
            except Exception:
                default = None
        if pname in _SAMPLE_VALUES:
            args[pname] = _SAMPLE_VALUES[pname]
        elif default is not None and _is_simple_default(default):
            args[pname] = default
        else:
            args[pname] = 'value'
    return args


def _schema_from_ast(node):
    """从 ast.Dict 提取 schema 类型。"""
    if not isinstance(node, ast.Dict):
        return {}
    schema = {}
    for k, v in zip(node.keys, node.values):
        if not isinstance(k, ast.Constant):
            continue
        key = k.value
        if key == 'type' and isinstance(v, ast.Constant):
            schema['type'] = v.value
        elif key == 'enum' and isinstance(v, ast.List):
            schema['enum'] = [e.value for e in v.elts if isinstance(e, ast.Constant)]
        elif key == 'items' and isinstance(v, ast.Dict):
            schema['items'] = _schema_from_ast(v)
        elif key == 'nullable' and isinstance(v, ast.Constant):
            schema['nullable'] = v.value
    return schema


def _default_for_schema(schema):
    t = schema.get('type', 'string')
    if isinstance(t, list):
        t = [x for x in t if x != 'null'][0] if t else 'string'
    if t == 'array' and 'items' in schema:
        return []
    return _TYPE_DEFAULTS.get(t, 'value')


def _generate_notes(func_name, args, schema_props):
    """生成 2 条通用 notes。"""
    notes = []
    if any(k in args for k in ('position', 'rotation_euler', 'offset', 'scale')):
        notes.append('坐标/旋转类参数优先使用 JSON 字符串 "[x,y,z]" 格式。')
    if 'file_path' in args:
        notes.append('file_path 建议使用绝对路径，目录不存在会自动创建。')
    if 'names' in args:
        notes.append('names 支持对象名列表或逗号分隔字符串。')
    if 'name' in args and func_name not in ('create_box', 'create_sphere', 'create_cylinder'):
        notes.append('调用前请确认 name 对应的对象已存在于场景中。')
    if not notes:
        notes.append('参数必须严格符合 JSON Schema 声明的类型。')
    if len(notes) < 2:
        notes.append('调用失败时应先检查对象/文件是否存在。')
    return notes[:3]


def _generate_returns_desc(func_name):
    return "dict {\\\"ok\\\": True, ...}"


def _generate_prerequisites(func_name, args):
    pre = []
    if 'name' in args and func_name.startswith(('set_', 'delete_', 'get_', 'add_', 'move_', 'rotate_', 'scale_')):
        pre.append('对象 name 必须已存在于场景中')
    if 'file_path' in args and func_name.startswith(('load_', 'import_', 'merge_')):
        pre.append('file_path 指向的文件必须存在')
    return pre


def _format_decorator_lines(decorator_src, func_name, args, notes, returns_desc, prerequisites, description_text):
    """重新格式化 @tool(...) 装饰器源代码。"""
    # 保留原有显式关键字（除结构化字段外）
    preserved = {}
    # 简单解析原装饰器中的关键字
    kw_re = re.compile(r'([A-Za-z_]+)\s*=\s*')
    # 这里用 ast 更可靠，但为了最小改动，直接基于 ast 节点重建
    pass


def _find_func_node(tree, decorator_end_lineno):
    """找到指定装饰器对应的函数定义节点。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for dec in node.decorator_list:
                if dec.end_lineno == decorator_end_lineno:
                    return node
    return None


def enrich_file(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        source = f.read()

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        print('SKIP {}: {}'.format(file_path, e))
        return False

    # 收集需要修改的位置
    edits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Name) or func.id != 'tool':
            continue

        # 检查是否已完整
        has_examples = any(kw.arg == 'examples' for kw in node.keywords)
        has_notes = any(kw.arg == 'notes' for kw in node.keywords)
        has_returns = any(kw.arg == 'returns_desc' for kw in node.keywords)
        if has_examples and has_notes and has_returns:
            continue

        func_node = _find_func_node(tree, node.end_lineno)
        if func_node is None:
            continue

        func_name = func_node.name
        args = _infer_example_args(node, func_node)
        notes = _generate_notes(func_name, args, {})
        returns_desc = _generate_returns_desc(func_name)
        prerequisites = _generate_prerequisites(func_name, args)

        # 找到装饰器在源码中的起止位置
        start_line = node.lineno - 1
        end_line = node.end_lineno
        old_text = '\n'.join(source.splitlines()[start_line:end_line])

        # 构造新关键字
        new_keywords = []
        if not has_examples:
            example_args = {k: v for k, v in args.items() if v is not None}
            if example_args:
                args_text = ', '.join('"{}": {}'.format(k, _literal_repr(v)) for k, v in example_args.items())
                new_keywords.append('examples=[{"summary": "典型调用", "args": {' + args_text + '}}]')
        if not has_notes:
            notes_text = ', '.join(_literal_repr(n) for n in notes)
            new_keywords.append('notes=[' + notes_text + ']')
        if not has_returns:
            new_keywords.append('returns_desc="{}"'.format(returns_desc))
        if prerequisites and not any(kw.arg == 'prerequisites' for kw in node.keywords):
            pre_text = ', '.join(_literal_repr(p) for p in prerequisites)
            new_keywords.append('prerequisites=[' + pre_text + ']')

        if not new_keywords:
            continue

        # 在原装饰器末尾追加新关键字
        # 去掉原装饰器末尾的 )，插入新关键字后再加 )
        if old_text.rstrip().endswith(','):
            suffix = ''
            trimmed = old_text.rstrip()
        elif old_text.rstrip().endswith(')'):
            suffix = ''
            trimmed = old_text.rstrip()[:-1]
            # 如果前面没有逗号，补一个
            if not trimmed.rstrip().endswith(','):
                trimmed = trimmed.rstrip() + ','
        else:
            continue

        indent = '    '
        additions = ',\n'.join(new_keywords)
        new_text = trimmed.rstrip() + '\n' + indent + additions + '\n)'

        edits.append((start_line, end_line, old_text, new_text))

    if not edits:
        print('OK {}: 无需修改'.format(file_path))
        return True

    # 从后往前替换，避免行号漂移
    lines = source.splitlines()
    for start_line, end_line, old_text, new_text in reversed(edits):
        block = '\n'.join(lines[start_line:end_line])
        if block != old_text:
            print('WARN: text mismatch at lines {}-{} in {}'.format(start_line + 1, end_line, file_path))
            continue
        lines[start_line:end_line] = new_text.splitlines()

    new_source = '\n'.join(lines)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(new_source)
    print('UPDATED {}: {} decorators'.format(file_path, len(edits)))
    return True


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python scripts/enrich_tool_descriptions.py <file1> [file2 ...]')
        sys.exit(1)
    for fp in sys.argv[1:]:
        enrich_file(fp)
