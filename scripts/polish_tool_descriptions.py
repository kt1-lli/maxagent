#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""美化和补齐 @tool 装饰器的结构化说明字段。

与 enrich_tool_descriptions.py 不同，本脚本会：
1. 重新格式化装饰器关键字（统一 4 空格缩进、尾部逗号）。
2. 为无参数工具生成空 args 示例。
3. 根据函数名/签名生成更贴切的 notes 和 returns_desc。

用法:
    python scripts/polish_tool_descriptions.py maxagent/tools/scene_io.py
"""

from __future__ import absolute_import
from __future__ import print_function

import ast
import re
import sys


_TOOL_SPECIFIC_NOTES = {
    'clear_selection': [
        '调用后当前选择集为空。',
        '此操作不会修改场景对象本身。',
    ],
    'list_skills': [
        '返回每个技能的名称、描述、触发关键词和使用次数。',
        '如需查看某个技能的完整 instructions，请调用 show_skill。',
    ],
    'list_learned_tools': [
        '返回已学习工具的名称、触发关键词和简短描述。',
        '如需运行某个工具，请使用 run_learned_tool（如存在）。',
    ],
    'list_learned_rules': [
        '返回已学习规则列表。',
        '规则通常来自用户显式教导或自动反思沉淀。',
    ],
    'list_reflections': [
        '返回反思记录的摘要。',
        '反思用于总结工具调用成功/失败经验，帮助后续决策。',
    ],
    'list_max_knowledge_topics': [
        '返回 Max 官方文档知识库中的主题列表。',
        '如需检索具体内容，请使用 search_max_docs。',
    ],
    'list_knowledge_sources': [
        '返回当前已配置的知识源（文件/代码库）列表。',
        '可通过 add_knowledge_source 新增知识源。',
    ],
    'build_scene_semantic_graph': [
        '构建场景语义图，用于理解对象之间的关系。',
        '返回结果通常包含对象名、类型、父子关系、材质等。',
    ],
    'todo_read': [
        '返回当前任务列表及每个任务的状态。',
        '可用于确认下一步该做什么。',
    ],
}


_TOOL_SPECIFIC_RETURNS = {
    'clear_selection': 'dict {"ok": True}',
    'list_skills': 'dict {"count": 技能数量, "skills": [...]}',
    'list_learned_tools': 'dict {"count": 工具数量, "tools": [...]}',
    'list_learned_rules': 'dict {"count": 规则数量, "rules": [...]}',
    'list_reflections': 'dict {"count": 反思数量, "reflections": [...]}',
    'list_max_knowledge_topics': 'dict {"count": 主题数量, "topics": [...]}',
    'list_knowledge_sources': 'dict {"count": 数量, "sources": [...]}',
    'build_scene_semantic_graph': 'dict {"nodes": [...], "edges": [...]}',
    'todo_read': 'dict {"todos": [...], "completed": 已完成数}',
}


def _infer_description(func_name, func_doc):
    """基于函数名和 docstring 推断更准确的 returns_desc 描述。"""
    doc_first = (func_doc or '').split('\n')[0].strip()
    if doc_first:
        return doc_first
    return func_name


def _build_decorator_text(original_lines, new_keywords):
    """重建 @tool(...) 装饰器文本，保持统一格式。"""
    # 解析原装饰器中的显式关键字
    old_text = '\n'.join(original_lines)
    # 用正则简单提取已有的关键字赋值
    kw_pattern = re.compile(r'^\s*([A-Za-z_]+)\s*=\s*(.+)$', re.MULTILINE)
    preserved = {}
    for m in kw_pattern.finditer(old_text):
        k, v = m.group(1), m.group(2)
        if k in ('description', 'category', 'dangerous', 'wrap_undo', 'run_on_main_thread', 'name'):
            preserved[k] = v.strip()

    # 如果原装饰器是单行，改成多行
    lines = ['@tool(']
    indent = '    '

    ordered_keys = ['name', 'description', 'category', 'dangerous', 'wrap_undo', 'run_on_main_thread']
    for k in ordered_keys:
        if k in preserved:
            lines.append(indent + '{}={},'.format(k, preserved[k]))

    # 结构化字段
    for k in ('examples', 'notes', 'returns_desc', 'prerequisites'):
        if k in new_keywords:
            value_lines = new_keywords[k].splitlines()
            if len(value_lines) == 1:
                lines.append(indent + '{}={},'.format(k, value_lines[0]))
            else:
                lines.append(indent + '{}={}'.format(k, value_lines[0]))
                for vl in value_lines[1:]:
                    lines.append(vl)
                lines[-1] = lines[-1] + ','

    lines.append(')')
    return '\n'.join(lines)


def _format_examples(examples):
    if not examples:
        return ''
    lines = ['[']
    for ex in examples:
        lines.append('        {')
        lines.append('            "summary": "{}",'.format(ex.get('summary', '典型调用')))
        args = ex.get('args', {})
        args_items = list(args.items())
        if args_items:
            lines.append('            "args": {')
            for idx, (k, v) in enumerate(args_items):
                comma = ',' if idx < len(args_items) - 1 else ''
                lines.append('                "{}": {}{}'.format(k, _repr_value(v), comma))
            lines.append('            },')
        else:
            lines.append('            "args": {},')
        lines.append('        },')
    lines.append('    ]')
    return '\n'.join(lines)


def _format_string_list(items):
    if not items:
        return ''
    lines = ['[']
    for item in items:
        lines.append('        "{}",'.format(item.replace('"', '\\"')))
    lines.append('    ]')
    return '\n'.join(lines)


def _repr_value(value):
    if isinstance(value, bool):
        return 'True' if value else 'False'
    if isinstance(value, str):
        return repr(value)
    if isinstance(value, (int, float)):
        return repr(value)
    if value is None:
        return 'None'
    if isinstance(value, list):
        return '[{}]'.format(', '.join(_repr_value(v) for v in value))
    if isinstance(value, dict):
        return repr(value)
    return repr(value)


def _extract_existing_keywords(decorator_src):
    """从装饰器源码字符串中提取显式关键字。"""
    result = {}
    # 删除 @tool( 和 ) 外壳
    inner = decorator_src.strip()
    if inner.startswith('@tool('):
        inner = inner[6:]
    if inner.endswith(')'):
        inner = inner[:-1]

    # 用 ast 解析内部表达式更安全
    try:
        expr = ast.parse('dict(' + inner + ')', mode='eval')
        call = expr.body
        if isinstance(call, ast.Call):
            for kw in call.keywords:
                if kw.arg:
                    result[kw.arg] = decorator_src
    except Exception:
        pass
    return result


def polish_file(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        source = f.read()

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        print('SKIP {}: {}'.format(file_path, e))
        return False

    edits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Name) or func.id != 'tool':
            continue

        # 找到对应函数定义
        func_node = None
        for n in ast.walk(tree):
            if isinstance(n, ast.FunctionDef):
                if node in n.decorator_list or node.end_lineno in [d.end_lineno for d in n.decorator_list]:
                    func_node = n
                    break
        if func_node is None:
            continue

        func_name = func_node.name
        if func_name.startswith('_') or func_name in ('t_one',):
            continue

        # 检查当前状态
        has_examples = any(kw.arg == 'examples' for kw in node.keywords)
        has_notes = any(kw.arg == 'notes' for kw in node.keywords)
        has_returns = any(kw.arg == 'returns_desc' for kw in node.keywords)

        if has_examples and has_notes and has_returns:
            # 已有完整字段，检查格式是否杂乱
            pass

        start_line = node.lineno - 1
        end_line = node.end_lineno
        old_text = '\n'.join(source.splitlines()[start_line:end_line])

        # 收集参数用于生成示例
        sig = func_node.args
        defaults_start = len(sig.args) - len(sig.defaults)
        params = []
        for idx, arg in enumerate(sig.args):
            pname = arg.arg
            default = None
            if idx >= defaults_start:
                try:
                    default = ast.literal_eval(sig.defaults[idx - defaults_start])
                except Exception:
                    default = None
            params.append((pname, default))

        # 生成示例
        examples = []
        if not has_examples:
            example_args = {}
            for pname, default in params:
                if pname == 'self':
                    continue
                if pname in ('name', 'names', 'file_path', 'output_path', 'output_dir'):
                    example_args[pname] = 'Box01' if pname == 'name' else ['Box01', 'Box02'] if pname == 'names' else 'C:/Work/scene.max'
                elif pname == 'frame':
                    example_args[pname] = 30
                elif pname == 'start_frame' or pname == 'start':
                    example_args[pname] = 0
                elif pname == 'end_frame' or pname == 'end':
                    example_args[pname] = 100
                elif pname == 'play':
                    example_args[pname] = True
                elif pname == 'hidden':
                    example_args[pname] = True
                elif pname == 'frozen':
                    example_args[pname] = True
                elif pname == 'selected_only':
                    example_args[pname] = False
                elif pname == 'allow_overwrite':
                    example_args[pname] = True
                elif pname == 'add_to_selection':
                    example_args[pname] = False
                elif pname == 'quiet':
                    example_args[pname] = True
                elif pname == 'camera':
                    example_args[pname] = 'Camera01'
                elif pname == 'controller':
                    example_args[pname] = 'position'
                elif pname == 'position':
                    example_args[pname] = '[50, 0, 0]'
                elif pname == 'rotation_euler':
                    example_args[pname] = '[0, 45, 0]'
                elif pname == 'scale':
                    example_args[pname] = '[1.5, 1.5, 1.5]'
                elif pname == 'color':
                    example_args[pname] = '[255, 128, 0]'
                elif pname == 'color_hex':
                    example_args[pname] = '#FF8000'
                elif pname == 'width':
                    example_args[pname] = 1920
                elif pname == 'height':
                    example_args[pname] = 1080
                elif isinstance(default, (str, int, float, bool)):
                    example_args[pname] = default
                else:
                    example_args[pname] = 'value'

            if example_args or not params:
                examples.append({'summary': '典型调用', 'args': example_args})

        # notes 和 returns_desc
        notes = _TOOL_SPECIFIC_NOTES.get(func_name)
        returns_desc = _TOOL_SPECIFIC_RETURNS.get(func_name)

        if not notes:
            notes = ['参数必须严格符合 JSON Schema 声明的类型。', '调用失败时应先检查对象/文件是否存在。']
        if not returns_desc:
            returns_desc = 'dict {"ok": True, ...}'

        new_keywords = {}
        if not has_examples:
            new_keywords['examples'] = _format_examples(examples)
        if not has_notes:
            new_keywords['notes'] = _format_string_list(notes)
        if not has_returns:
            new_keywords['returns_desc'] = '"{}"'.format(returns_desc)

        if not new_keywords:
            continue

        # 最简单方式：在原有 decorator 源码上追加新字段
        # 重新格式化整个装饰器
        new_text = _rebuild_decorator(old_text, new_keywords)
        if new_text:
            edits.append((start_line, end_line, old_text, new_text))

    if not edits:
        print('OK {}: 无需修改'.format(file_path))
        return True

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
    print('POLISHED {}: {} decorators'.format(file_path, len(edits)))
    return True


def _rebuild_decorator(old_text, new_keywords):
    """基于原装饰器文本，追加新关键字并统一格式。"""
    # 解析已有关键字（简单正则方式）
    lines = old_text.splitlines()
    
    # 找到 @tool( 行和最后的 ) 行
    if not lines[0].startswith('@tool('):
        return None
    
    # 提取所有已有字段行
    existing = []
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == ')':
            break
        existing.append(line)

    indent = '    '
    out_lines = ['@tool(']
    
    # 保留原有字段
    for line in existing:
        # 去掉可能的尾部逗号，后面统一加
        s = line.rstrip()
        if s.endswith(','):
            s = s[:-1]
        out_lines.append(s + ',')
    
    # 追加新字段
    for k in ('examples', 'notes', 'returns_desc', 'prerequisites'):
        if k not in new_keywords:
            continue
        v = new_keywords[k]
        v_lines = v.splitlines()
        if len(v_lines) == 1:
            out_lines.append(indent + '{}={},'.format(k, v_lines[0]))
        else:
            out_lines.append(indent + '{}={}'.format(k, v_lines[0]))
            for vl in v_lines[1:]:
                out_lines.append(vl + ',')
    
    out_lines.append(')')
    return '\n'.join(out_lines)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python scripts/polish_tool_descriptions.py <file1> [file2 ...]')
        sys.exit(1)
    for fp in sys.argv[1:]:
        polish_file(fp)
