#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""禁用名单（disabled_registry）的端到端回归。

覆盖三层：
1. **存档层**：set_xxx_disabled / list_xxx / 缓存 / 持久化
2. **schema 层**：build_openai_tools_schema 过滤禁用工具
3. **dispatcher 层**：dispatch 拦截禁用工具
4. **prompt 层**：SkillManager 不向 LLM 暴露禁用技能
"""

from __future__ import absolute_import
from __future__ import print_function

import os

import pytest


# ---------------------------------------------------------------------- #
# 1. 存档基础
# ---------------------------------------------------------------------- #
class TestDisabledRegistryBasic:
    def setup_method(self, _method):
        from maxagent import disabled_registry as dr
        # 用例间隔离：每个测试一个临时文件
        import tempfile
        self._tmp = tempfile.mkdtemp(prefix='dr-test-')
        self._path = os.path.join(self._tmp, 'disabled.json')
        dr.set_disabled_path_override(self._path)

    def teardown_method(self, _method):
        from maxagent import disabled_registry as dr
        dr.set_disabled_path_override(None)

    def test_initial_state_empty(self):
        from maxagent import disabled_registry as dr
        assert dr.list_disabled_tools() == []
        assert dr.list_disabled_skills() == []
        assert dr.is_tool_disabled('foo') is False
        assert dr.is_skill_disabled('bar') is False

    def test_set_tool_disabled_persists(self):
        from maxagent import disabled_registry as dr
        dr.set_tool_disabled('foo', True)
        dr.set_tool_disabled('bar', True)
        # 写盘
        assert os.path.isfile(self._path)
        # 缓存有效
        assert dr.is_tool_disabled('foo') is True
        # 重置缓存后从盘读取
        dr.invalidate_cache()
        assert dr.is_tool_disabled('foo') is True
        assert sorted(dr.list_disabled_tools()) == ['bar', 'foo']

    def test_set_disabled_idempotent(self):
        from maxagent import disabled_registry as dr
        dr.set_tool_disabled('foo', True)
        dr.set_tool_disabled('foo', True)  # 第二次不应报错
        assert dr.list_disabled_tools() == ['foo']

    def test_unset_removes_from_list(self):
        from maxagent import disabled_registry as dr
        dr.set_tool_disabled('foo', True)
        dr.set_tool_disabled('foo', False)
        assert dr.list_disabled_tools() == []
        assert dr.is_tool_disabled('foo') is False

    def test_skills_separate_namespace(self):
        """工具和技能是两个独立命名空间，互不干扰。"""
        from maxagent import disabled_registry as dr
        dr.set_tool_disabled('same_name', True)
        assert dr.is_tool_disabled('same_name') is True
        assert dr.is_skill_disabled('same_name') is False
        dr.set_skill_disabled('same_name', True)
        assert dr.is_skill_disabled('same_name') is True

    def test_corrupt_json_returns_empty(self, tmp_path):
        """坏 JSON 不应让 list_xxx 抛异常，按"无禁用项"处理。"""
        from maxagent import disabled_registry as dr
        bad = tmp_path / 'bad.json'
        bad.write_text('{not json', encoding='utf-8')
        dr.set_disabled_path_override(str(bad))
        dr.invalidate_cache()
        assert dr.list_disabled_tools() == []
        assert dr.list_disabled_skills() == []

    def test_clear_all(self):
        from maxagent import disabled_registry as dr
        dr.set_tool_disabled('a', True)
        dr.set_skill_disabled('b', True)
        dr.clear_all()
        assert dr.list_disabled_tools() == []
        assert dr.list_disabled_skills() == []

    def test_empty_name_ignored(self):
        from maxagent import disabled_registry as dr
        dr.set_tool_disabled('', True)
        dr.set_tool_disabled('   ', True)
        assert dr.list_disabled_tools() == []


# ---------------------------------------------------------------------- #
# 2. schema 过滤：LLM 看不到被禁用的工具
# ---------------------------------------------------------------------- #
class TestSchemaFilter:
    def setup_method(self, _method):
        from maxagent import disabled_registry as dr
        from maxagent.tools import registry
        import tempfile
        self._tmp = tempfile.mkdtemp(prefix='schema-test-')
        dr.set_disabled_path_override(
            os.path.join(self._tmp, 'disabled.json'),
        )
        # 注册一个临时工具用于本组测试
        # 避免污染全局 _REGISTRY，这里测完手工撤回
        @registry.tool(
            name='_test_disabled_tool',
            description='only-for-disabled-test',
            parameters={'type': 'object', 'properties': {}},
        )
        def _f():
            return 'ok'

        self._tool_name = '_test_disabled_tool'

    def teardown_method(self, _method):
        from maxagent import disabled_registry as dr
        from maxagent.tools import registry
        dr.set_disabled_path_override(None)
        # 移除测试工具
        registry._REGISTRY.pop(self._tool_name, None)

    def test_enabled_tool_appears_in_schema(self):
        from maxagent.tools import build_openai_tools_schema
        names = [
            s['function']['name'] for s in build_openai_tools_schema()
        ]
        assert self._tool_name in names

    def test_disabled_tool_filtered_from_schema(self):
        from maxagent import disabled_registry as dr
        from maxagent.tools import build_openai_tools_schema
        dr.set_tool_disabled(self._tool_name, True)
        names = [
            s['function']['name'] for s in build_openai_tools_schema()
        ]
        assert self._tool_name not in names

    def test_re_enable_brings_tool_back(self):
        from maxagent import disabled_registry as dr
        from maxagent.tools import build_openai_tools_schema
        dr.set_tool_disabled(self._tool_name, True)
        dr.set_tool_disabled(self._tool_name, False)
        names = [
            s['function']['name'] for s in build_openai_tools_schema()
        ]
        assert self._tool_name in names


# ---------------------------------------------------------------------- #
# 3. dispatcher 拦截：即便强行调用也被拒绝（防御 history 残留）
# ---------------------------------------------------------------------- #
class TestDispatcherIntercept:
    def setup_method(self, _method):
        from maxagent import disabled_registry as dr
        from maxagent.tools import registry
        import tempfile
        self._tmp = tempfile.mkdtemp(prefix='disp-test-')
        dr.set_disabled_path_override(
            os.path.join(self._tmp, 'disabled.json'),
        )

        @registry.tool(
            name='_test_intercept_tool',
            description='intercept',
            parameters={'type': 'object', 'properties': {}},
            run_on_main_thread=False,
        )
        def _f():
            return 'should-not-run'

        self._tool_name = '_test_intercept_tool'

    def teardown_method(self, _method):
        from maxagent import disabled_registry as dr
        from maxagent.tools import registry
        dr.set_disabled_path_override(None)
        registry._REGISTRY.pop(self._tool_name, None)

    def test_disabled_tool_returns_error(self):
        from maxagent import disabled_registry as dr
        from maxagent.tools.dispatcher import ToolDispatcher
        dr.set_tool_disabled(self._tool_name, True)
        d = ToolDispatcher(wrap_undo=False)
        res = d.dispatch(self._tool_name, {})
        assert res['ok'] is False
        assert res.get('type') == 'tool_disabled'

    def test_enabled_tool_runs_normally(self):
        from maxagent.tools.dispatcher import ToolDispatcher
        d = ToolDispatcher(wrap_undo=False)
        res = d.dispatch(self._tool_name, {})
        assert res['ok'] is True
        assert res['result'] == 'should-not-run'


# ---------------------------------------------------------------------- #
# 4. SkillManager 过滤：被禁用技能不进 system prompt 也不出现在 list_skills
# ---------------------------------------------------------------------- #
class TestSkillManagerFilter:
    def setup_method(self, _method):
        from maxagent import disabled_registry as dr
        from maxagent import skills as skills_mod
        import tempfile
        self._tmp = tempfile.mkdtemp(prefix='sk-test-')
        dr.set_disabled_path_override(
            os.path.join(self._tmp, 'disabled.json'),
        )

        # 用临时 skills 目录
        self._skills_dir = os.path.join(self._tmp, 'skills')
        os.makedirs(self._skills_dir)
        # SkillManager 的目录由 get_config_dir() 决定，这里 monkeypatch
        # 不方便（fixtures），改成直接 inject _base
        self._mgr = skills_mod.SkillManager()
        self._mgr._base = self._skills_dir  # type: ignore[attr-defined]
        # 写两个测试技能
        self._mgr.save(skills_mod.Skill(
            name='enabled_skill',
            description='visible',
            trigger_keywords=['触发A'],
            instructions='step1',
        ))
        self._mgr.save(skills_mod.Skill(
            name='disabled_skill',
            description='hidden',
            trigger_keywords=['触发B'],
            instructions='step2',
        ))

    def teardown_method(self, _method):
        from maxagent import disabled_registry as dr
        dr.set_disabled_path_override(None)

    def test_list_skills_excludes_disabled(self):
        from maxagent import disabled_registry as dr
        names_all = [s.name for s in self._mgr.list_skills()]
        assert 'enabled_skill' in names_all
        assert 'disabled_skill' in names_all

        dr.set_skill_disabled('disabled_skill', True)
        names_after = [s.name for s in self._mgr.list_skills()]
        assert 'enabled_skill' in names_after
        assert 'disabled_skill' not in names_after

    def test_list_all_skills_includes_disabled(self):
        """管理 UI 用的 list_all_skills 必须能看到全部（含禁用）。"""
        from maxagent import disabled_registry as dr
        dr.set_skill_disabled('disabled_skill', True)
        names = [s.name for s in self._mgr.list_all_skills()]
        assert 'enabled_skill' in names
        assert 'disabled_skill' in names

    def test_system_prompt_addon_omits_disabled(self):
        from maxagent import disabled_registry as dr
        addon = self._mgr.build_system_prompt_addon()
        assert 'enabled_skill' in addon
        assert 'disabled_skill' in addon

        dr.set_skill_disabled('disabled_skill', True)
        addon2 = self._mgr.build_system_prompt_addon()
        assert 'enabled_skill' in addon2
        assert 'disabled_skill' not in addon2

    def test_disabled_keyword_not_triggered(self):
        """触发关键词命中也不会注入完整 instructions（被过滤）。"""
        from maxagent import disabled_registry as dr
        dr.set_skill_disabled('disabled_skill', True)
        addon = self._mgr.build_system_prompt_addon(user_input='触发B')
        # disabled_skill 完全不可见
        assert 'disabled_skill' not in addon
        assert 'step2' not in addon


# ---------------------------------------------------------------------- #
# 5. 设置面板 UI 集成（仅头部冒烟，避免重复 dialog 测试中已有的覆盖）
# ---------------------------------------------------------------------- #
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

try:
    from maxagent.qt_compat import QtWidgets  # noqa: F401
    HAS_QT = True
except Exception:  # pylint: disable=broad-except
    HAS_QT = False


@pytest.fixture(scope='module')
def qapp():
    if not HAS_QT:
        pytest.skip('Qt 不可用')
    from maxagent.qt_compat import QtWidgets as QW
    app = QW.QApplication.instance() or QW.QApplication([])
    yield app


@pytest.fixture()
def isolated_disabled(tmp_path):
    from maxagent import disabled_registry as dr
    p = tmp_path / 'disabled.json'
    dr.set_disabled_path_override(str(p))
    yield str(p)
    dr.set_disabled_path_override(None)


@pytest.fixture()
def isolated_resources(tmp_path, monkeypatch):
    """工具/技能/规则三类资源用临时目录。"""
    from maxagent import user_tools_loader as utl
    from maxagent import user_rules_loader as url_mod
    from maxagent import skills as skills_mod

    tools_dir = tmp_path / 'user_tools'
    rules_dir = tmp_path / 'user_rules'
    skills_dir = tmp_path / 'skills'
    tools_dir.mkdir()
    rules_dir.mkdir()
    skills_dir.mkdir()

    utl.set_user_tools_dir_override(str(tools_dir))
    url_mod.set_user_rules_dir_override(str(rules_dir))

    cfg_root = tmp_path / 'cfg'
    cfg_root.mkdir()
    monkeypatch.setattr(skills_mod, 'get_config_dir', lambda: str(cfg_root))
    (cfg_root / 'skills').mkdir()

    yield {
        'tools': str(tools_dir),
        'rules': str(rules_dir),
        'skills': str(cfg_root / 'skills'),
    }

    utl.set_user_tools_dir_override(None)
    url_mod.set_user_rules_dir_override(None)


@pytest.fixture()
def cfg_mgr(tmp_path):
    from maxagent.config import ConfigManager
    return ConfigManager(str(tmp_path / 'config.json'))


@pytest.fixture()
def settings_dlg(qapp, cfg_mgr, isolated_disabled, isolated_resources):
    from maxagent.ui.settings_dialog import SettingsDialog
    d = SettingsDialog(cfg_mgr)
    yield d
    d.deleteLater()


class TestUiIntegration:
    def test_resources_tab_has_4_subtabs(self, settings_dlg):
        # 找到"我的资源"主 Tab 索引
        idx = next(
            i for i, (_l, k) in enumerate(settings_dlg._NAV_ITEMS)
            if k == 'resources'
        )
        settings_dlg.nav.setCurrentRow(idx)
        assert settings_dlg.resources_tabs.count() == 4

    def test_skill_check_writes_disabled(self, settings_dlg, isolated_disabled):
        from maxagent import skills as skills_mod
        from maxagent import disabled_registry as dr
        from maxagent.qt_compat import QtCore

        # 写入测试技能
        mgr = skills_mod.SkillManager()
        mgr.save(skills_mod.Skill(
            name='ui_test_skill',
            description='d',
            trigger_keywords=['x'],
            instructions='do this',
        ))

        # 切到资源 Tab 触发构建 → 刷新列表
        idx = next(
            i for i, (_l, k) in enumerate(settings_dlg._NAV_ITEMS)
            if k == 'resources'
        )
        settings_dlg.nav.setCurrentRow(idx)
        settings_dlg._refresh_skills_list()

        # 找到 ui_test_skill 行
        target = None
        for i in range(settings_dlg._skills_list.count()):
            it = settings_dlg._skills_list.item(i)
            if it.data(QtCore.Qt.UserRole) == 'ui_test_skill':
                target = it
                break
        assert target is not None
        # 默认应是启用 → Checked
        assert target.checkState() == QtCore.Qt.Checked

        # 取消勾选 → 应被写入 disabled
        target.setCheckState(QtCore.Qt.Unchecked)
        assert dr.is_skill_disabled('ui_test_skill') is True

        # 再勾选 → 应被移除
        target.setCheckState(QtCore.Qt.Checked)
        assert dr.is_skill_disabled('ui_test_skill') is False

    def test_tool_check_writes_disabled(self, settings_dlg, isolated_disabled):
        from maxagent import user_tools_loader as utl
        from maxagent import disabled_registry as dr
        from maxagent.qt_compat import QtCore

        utl.write_tool(
            'ui_test_tool',
            'from maxagent.tools.registry import tool\n'
            '@tool(name="ui_test_tool", description="d")\n'
            'def _f(): return 1\n',
            {'description': 'd'},
        )

        idx = next(
            i for i, (_l, k) in enumerate(settings_dlg._NAV_ITEMS)
            if k == 'resources'
        )
        settings_dlg.nav.setCurrentRow(idx)
        settings_dlg._refresh_tools_list()

        target = None
        for i in range(settings_dlg._tools_list.count()):
            it = settings_dlg._tools_list.item(i)
            if it.data(QtCore.Qt.UserRole) == 'ui_test_tool':
                target = it
                break
        assert target is not None
        assert target.checkState() == QtCore.Qt.Checked

        target.setCheckState(QtCore.Qt.Unchecked)
        assert dr.is_tool_disabled('ui_test_tool') is True
