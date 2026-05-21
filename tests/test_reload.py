#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""maxagent.reload 模块单元测试。

覆盖：
- _list_maxagent_modules 能正确发现包内模块
- _purge_modules 移除子模块但保留白名单 + 自身
- reload_maxagent(reshow=False) 不依赖 Qt 也能完整跑一遍
"""

from __future__ import absolute_import
from __future__ import print_function

import sys

import pytest


@pytest.fixture
def fresh_imports():
    """每个测试前后清理 sys.modules 中的 maxagent 包，保证用例独立。"""
    yield
    for name in list(sys.modules.keys()):
        if name == 'maxagent' or name.startswith('maxagent.'):
            # 仅在完全清理时移除，避免污染后续用例
            if name not in ('maxagent.qt_compat',):
                sys.modules.pop(name, None)


class TestListModules:
    def test_finds_maxagent_modules(self, fresh_imports):
        # 触发若干子模块 import
        import maxagent.config  # noqa: F401
        import maxagent.sessions  # noqa: F401
        from maxagent import reload as reload_mod
        names = reload_mod._list_maxagent_modules()
        assert 'maxagent' in names
        assert 'maxagent.config' in names
        assert 'maxagent.sessions' in names


class TestPurgeModules:
    def test_purge_removes_submodules(self, fresh_imports):
        import maxagent.config  # noqa: F401
        from maxagent import reload as reload_mod
        n = reload_mod._purge_modules(skip_self=True)
        # 至少把 maxagent.config 卸下来了
        assert n >= 1
        assert 'maxagent.config' not in sys.modules

    def test_purge_keeps_self(self, fresh_imports):
        from maxagent import reload as reload_mod
        reload_mod._purge_modules(skip_self=True)
        # 当前模块依然在 sys.modules 里，否则后续语句无法运行
        assert reload_mod.__name__ in sys.modules

    def test_purge_keeps_qt_compat(self, fresh_imports):
        # qt_compat 在测试环境可能没被 import；如果没有就跳过
        if 'maxagent.qt_compat' not in sys.modules:
            pytest.skip('qt_compat 未导入')
        from maxagent import reload as reload_mod
        reload_mod._purge_modules(skip_self=True)
        assert 'maxagent.qt_compat' in sys.modules


class TestReloadFullCycle:
    def test_reload_no_show_smoke(self, fresh_imports):
        """reshow=False 时只做 purge + 重 import，不依赖 Qt UI。"""
        import maxagent  # noqa: F401
        import maxagent.config  # noqa: F401
        from maxagent import reload as reload_mod
        # 不能抛
        result = reload_mod.reload_maxagent(reshow=False)
        assert result is None
        # purge 后 sys.modules 里应当又有最新版本的 maxagent
        assert 'maxagent' in sys.modules

    def test_reload_can_be_imported_twice(self, fresh_imports):
        """连续调用两次也不应崩。"""
        from maxagent import reload as reload_mod
        reload_mod.reload_maxagent(reshow=False)
        # 重 import 后实例已变，重新拿
        from maxagent import reload as reload_mod2
        reload_mod2.reload_maxagent(reshow=False)
