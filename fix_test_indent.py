#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修复测试文件中 import 语句丢失缩进的问题。"""

from __future__ import absolute_import
from __future__ import print_function

import os


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
            # 检查上一行是否是函数/类定义结束行
            prev = lines[i - 1].strip()
            if prev.endswith(':') and (prev.startswith('def ') or prev.startswith('class ')):
                fixed.append('    ' + stripped + '\n')
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
