#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量修正 tests/ 中因工具目录重构导致的旧 import。"""

from __future__ import absolute_import
from __future__ import print_function

import os

# 精确替换映射：旧文本 -> 新文本
_REPLACEMENTS = {
    'from maxagent.tools import animation': 'from maxagent.tools.max import animation',
    'from maxagent.tools import class_tree': 'from maxagent.tools.max import class_tree',
    'from maxagent.tools import creative': 'from maxagent.tools.max import creative',
    'from maxagent.tools import geometry': 'from maxagent.tools.max import geometry',
    'from maxagent.tools import high_level': 'from maxagent.tools.max import high_level',
    'from maxagent.tools import light_camera': 'from maxagent.tools.max import light_camera',
    'from maxagent.tools import material': 'from maxagent.tools.max import material',
    'from maxagent.tools import modifier': 'from maxagent.tools.max import modifier',
    'from maxagent.tools import render': 'from maxagent.tools.max import render',
    'from maxagent.tools import scene_awareness': 'from maxagent.tools.max import scene_awareness',
    'from maxagent.tools import scene_io': 'from maxagent.tools.max import scene_io',
    'from maxagent.tools import scene_query': 'from maxagent.tools.max import scene_query',
    'from maxagent.tools import scripting': 'from maxagent.tools.max import scripting',
    'from maxagent.tools import transform': 'from maxagent.tools.max import transform',
    'from maxagent.tools import viewport_capture': 'from maxagent.tools.max import viewport_capture',
    'from maxagent.tools import learn_tools': 'from maxagent.tools.shared import learn_tools',
    'from maxagent.tools import learn_rules': 'from maxagent.tools.shared import learn_rules',
    'from maxagent.tools import todo_tools': 'from maxagent.tools.shared import todo_tools',
    'from maxagent.tools import web_tools': 'from maxagent.tools.shared import web_tools',
    'from maxagent.tools import reflection_tools': 'from maxagent.tools.shared import reflection_tools',
    'from maxagent.tools import batch': 'from maxagent.tools.shared import batch',
    'from maxagent.tools import knowledge_tools': 'from maxagent.tools.shared import knowledge_tools',
    'from maxagent.tools import memory_tools': 'from maxagent.tools.shared import memory_tools',
    'from maxagent.tools import skills_tools': 'from maxagent.tools.shared import skills_tools',
    'import maxagent.tools.material as mat_mod': 'import maxagent.tools.max.material as mat_mod',
}


def fix_file(path):
    # type: (str) -> bool
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    new_content = content
    for old, new in _REPLACEMENTS.items():
        new_content = new_content.replace(old, new)
    if new_content == content:
        return False
    with open(path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    print('已更新: {}'.format(path))
    return True


def main():
    tests_dir = '/data/workspace/tests'
    for filename in os.listdir(tests_dir):
        if not filename.endswith('.py'):
            continue
        fix_file(os.path.join(tests_dir, filename))


if __name__ == '__main__':
    main()
