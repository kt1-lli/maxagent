#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复现 test_reload.py + test_skill_dcc_defaults_to_current 失败场景。"""

from __future__ import absolute_import
from __future__ import print_function

import sys

import pytest


def main():
    # 先 import 测试模块（像 pytest 一样）
    import tests.test_skills as ts_mod
    from maxagent.dcc.runtime import current_dcc

    # 执行 reload 测试
    from maxagent import reload as rm
    rm.reload_maxagent(reshow=False)

    # 模拟 test_skill_dcc_defaults_to_current
    mp = pytest.MonkeyPatch()
    mp.setattr('maxagent.dcc.runtime._DCC_NAME', None)
    import maxagent.dcc.runtime as rt_mod
    mp.setitem(rt_mod._DCC_STATE, 'name', 'maya')

    print('DEBUG rt_mod._DCC_NAME:', rt_mod._DCC_NAME)
    print('DEBUG rt_mod._DCC_STATE:', rt_mod._DCC_STATE)
    print('DEBUG current_dcc():', current_dcc())
    print('DEBUG ts_mod.SkillManager module:', ts_mod.SkillManager.__module__)
    import maxagent.skills as skills_mod
    print('DEBUG skills_mod.current_dcc():', skills_mod.current_dcc())

    m = ts_mod.SkillManager(base_dir='/tmp/repro_test_dir')
    s = ts_mod.Skill(name='maya_only', instructions='do')
    m.save(s)
    print('DEBUG saved s.dcc:', s.dcc)
    data = m.get('maya_only').to_dict()
    print('DEBUG data[dcc]:', data.get('dcc'))

    mp.undo()


if __name__ == '__main__':
    main()
