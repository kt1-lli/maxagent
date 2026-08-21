#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量给 tools/max/ 下的 @tool 装饰器添加 dcc=['3dsmax']。"""

from __future__ import absolute_import
from __future__ import print_function

import ast
import os


def add_dcc_to_tool_calls(path):
    # type: (str) -> bool
    """读取文件，给每个 @tool(...) 调用添加 dcc=['3dsmax']（如果不存在）。"""
    with open(path, 'r', encoding='utf-8') as f:
        source = f.read()

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        print('解析失败: {} - {}'.format(path, exc))
        return False

    changed = False
    # 收集需要修改的 @tool 装饰器位置（起始偏移量）
    tool_decorator_starts = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Call):
                func = decorator.func
                if isinstance(func, ast.Name) and func.id == 'tool':
                    has_dcc = any(
                        isinstance(kw, ast.keyword) and kw.arg == 'dcc'
                        for kw in decorator.keywords
                    )
                    if not has_dcc:
                        tool_decorator_starts.append(decorator.lineno - 1)

    if not tool_decorator_starts:
        return False

    lines = source.split('\n')
    for line_index in reversed(tool_decorator_starts):
        line = lines[line_index]
        # 简单处理：在右括号前插入 dcc=['3dsmax']
        stripped = line.rstrip()
        if stripped.endswith(')'):
            # 情况1：参数全在同一行，形如 @tool(name='x')
            if stripped.startswith('@tool('):
                new_line = stripped[:-1] + ', dcc=[\'3dsmax\'])'
                lines[line_index] = new_line
                changed = True
                continue
        # 情况2：@tool( 独占一行，参数在后续行
        if stripped == '@tool(' or stripped.endswith('@tool('):
            # 在下一行插入参数
            indent = ' ' * 4
            lines.insert(line_index + 1, indent + 'dcc=[\'3dsmax\'],')
            changed = True
            continue

    if not changed:
        return False

    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    print('已更新: {}'.format(path))
    return True


def main():
    target_dir = '/data/workspace/maxagent/tools/max'
    for filename in sorted(os.listdir(target_dir)):
        if not filename.endswith('.py'):
            continue
        if filename == '__init__.py':
            continue
        add_dcc_to_tool_calls(os.path.join(target_dir, filename))


if __name__ == '__main__':
    main()
