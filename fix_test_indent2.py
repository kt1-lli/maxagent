#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修复测试文件中 import 语句丢失缩进的问题（支持多层缩进检测）。"""

from __future__ import absolute_import
from __future__ import print_function

import os
import re


def fix_file(path):
    # type: (str) -> bool
    with open(path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    fixed = []
    changed = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if (
            stripped.startswith('from maxagent.tools')
            and not stripped.startswith('#')
            and not line.startswith(' ')
            and not line.startswith('\t')
            and i > 0
        ):
            # 查找上一个非空行
            prev_idx = i - 1
            while prev_idx >= 0 and not lines[prev_idx].strip():
                prev_idx -= 1
            if prev_idx < 0:
                fixed.append(line)
                continue
            prev = lines[prev_idx].rstrip('\n')
            # 计算上一行的缩进
            indent_match = re.match(r'^(\s*)', prev)
            indent = indent_match.group(1) if indent_match else ''
            if prev.rstrip().endswith(':') and ('def ' in prev or 'class ' in prev):
                fixed.append(indent + '    ' + stripped + '\n')
                changed = True
                continue
        fixed.append(line)
    if not changed:
        return False
    with open(path, 'w', encoding='utf-8') as f:
        f.writelines(fixed)
    print('已修正缩进: {}'.format(path))
    return True


def main():
    tests_dir = '/data/workspace/tests'
    for filename in os.listdir(tests_dir):
        if not filename.endswith('.py'):
            continue
        fix_file(os.path.join(tests_dir, filename))


if __name__ == '__main__':
    main()
