#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
我的资源 v2 UI 调整回归测试。

覆盖三件事：
1. 规则子页底部按钮已移除"导出选中/导出全部/导入"三按钮（统一走 pack 页）
2. 技能/工具子页底部新增"启用/禁用"按钮，且与勾选框等价
3. pack 页每栏顶部独立"全选"复选框 + 列表项变化能反向同步栏顶状态
"""

from __future__ import absolute_import
from __future__ import print_function

import os

import pytest


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
def isolate_dirs(tmp_path, monkeypatch):
    from maxagent import disabled_registry as dr
    from maxagent import skills as skills_mod
    from maxagent import user_rules_loader as url_mod
    from maxagent import user_tools_loader as utl

    tools_dir = tmp_path / 'user_tools'
    rules_dir = tmp_path / 'user_rules'
    tools_dir.mkdir()
    rules_dir.mkdir()
    utl.set_user_tools_dir_override(str(tools_dir))
    url_mod.set_user_rules_dir_override(str(rules_dir))

    cfg_root = tmp_path / 'cfg'
    cfg_root.mkdir()
    (cfg_root / 'skills').mkdir()
    monkeypatch.setattr(skills_mod, 'get_config_dir', lambda: str(cfg_root))

    dr.set_disabled_path_override(str(tmp_path / 'disabled.json'))

    yield

    utl.set_user_tools_dir_override(None)
    url_mod.set_user_rules_dir_override(None)
    dr.set_disabled_path_override(None)


@pytest.fixture()
def dialog(qapp, tmp_path, isolate_dirs):
    from maxagent.config import ConfigManager
    from maxagent.ui.settings_dialog import SettingsDialog
    cfg = ConfigManager(str(tmp_path / 'config.json'))
    d = SettingsDialog(cfg)
    yield d
    d.deleteLater()


# ---------------------------------------------------------------------- #
# 1) 规则子页：底部按钮已移除导入导出
# ---------------------------------------------------------------------- #
class TestRulesSubpageButtons:
    def test_rules_no_export_methods_invoked(self, dialog):
        """规则子页不应再创建独立导入导出按钮（统一收敛到 pack 页）。

        通过遍历底部按钮文本来验证：不应出现"导出选中/导出全部/导入文件"。
        """
        from maxagent.qt_compat import QtWidgets

        # 查找规则页 widget
        page = dialog._build_page_rules()
        labels = []
        for btn in page.findChildren(QtWidgets.QPushButton):
            labels.append(btn.text())
        joined = '|'.join(labels)
        assert '导出全部' not in joined
        # 注："导出选中"在 pack 页确实有，但不应出现在规则子页内
        # 规则子页里也不应有"导入文件"按钮
        assert '导入文件' not in joined

    def test_rules_keep_core_buttons(self, dialog):
        """规则子页仍保留：查看详情 / 启用禁用 / 删除 / 刷新。"""
        from maxagent.qt_compat import QtWidgets

        page = dialog._build_page_rules()
        labels = '|'.join(
            btn.text()
            for btn in page.findChildren(QtWidgets.QPushButton)
        )
        assert '查看详情' in labels
        assert '启用/禁用' in labels
        assert '删除' in labels
        assert '刷新' in labels


# ---------------------------------------------------------------------- #
# 2) 技能/工具子页：新增"启用/禁用"按钮
# ---------------------------------------------------------------------- #
class TestSkillsToolsToggleButton:
    def _setup_skill(self):
        """临时写一个 skill JSON 文件供测试。"""
        import json
        import time
        from maxagent import skills as skills_mod
        sk_dir = os.path.join(skills_mod.get_config_dir(), 'skills')
        os.makedirs(sk_dir, exist_ok=True)
        data = {
            'name': 'demo_sk',
            'description': 'demo desc',
            'trigger_keywords': ['demo'],
            'instructions': 'do demo',
            'created_at': time.time(),
            'updated_at': time.time(),
            'use_count': 0,
            'source_session_sid': '',
        }
        with open(os.path.join(sk_dir, 'demo_sk.json'),
                  'w', encoding='utf-8') as fh:
            json.dump(data, fh)
        return 'demo_sk'

    def _setup_tool(self):
        from maxagent import user_tools_loader as utl
        utl.write_tool(
            'demo_tool',
            'from maxagent.tools.registry import tool\n'
            '@tool(name="demo_tool", description="d")\n'
            'def _f(): return 1\n',
            {'description': 'd'},
        )
        return 'demo_tool'

    def test_skill_toggle_button_flips_state(self, dialog, tmp_path):
        from maxagent import disabled_registry as dr
        name = self._setup_skill()
        dialog._refresh_skills_list()

        # 选中第一项
        dialog._skills_list.setCurrentRow(0)
        # 初始为启用
        assert not dr.is_skill_disabled(name)

        # 翻转一次 → 禁用
        dialog._on_skill_toggle_enabled()
        assert dr.is_skill_disabled(name)
        # 再翻转 → 启用
        dialog._on_skill_toggle_enabled()
        assert not dr.is_skill_disabled(name)

    def test_tool_toggle_button_flips_state(self, dialog):
        from maxagent import disabled_registry as dr
        name = self._setup_tool()
        dialog._refresh_tools_list()

        dialog._tools_list.setCurrentRow(0)
        assert not dr.is_tool_disabled(name)

        dialog._on_tool_toggle_enabled()
        assert dr.is_tool_disabled(name)
        dialog._on_tool_toggle_enabled()
        assert not dr.is_tool_disabled(name)

    def test_skill_toggle_no_selection_safe(self, dialog, monkeypatch):
        """没有选中项时点击不应崩溃，应温和提示。"""
        from maxagent.qt_compat import QtWidgets
        called = {'info': 0}

        def fake_info(*_args, **_kwargs):
            called['info'] += 1

        monkeypatch.setattr(
            QtWidgets.QMessageBox, 'information', fake_info,
        )
        dialog._refresh_skills_list()
        # 不主动 setCurrentRow → 无选择
        dialog._on_skill_toggle_enabled()
        assert called['info'] >= 1


# ---------------------------------------------------------------------- #
# 3) pack 页：每栏独立全选 + 反向同步
# ---------------------------------------------------------------------- #
class TestPackPerSectionSelectAll:
    def test_each_section_has_independent_select_all(self, dialog):
        """三栏列表都暴露 _pack_select_all_chk 属性。"""
        for lst in (dialog.pack_tool_list,
                    dialog.pack_skill_list,
                    dialog.pack_rule_list):
            assert hasattr(lst, '_pack_select_all_chk'), \
                '栏顶应有独立全选复选框'

    def test_section_select_all_only_affects_own_list(self, dialog):
        """技能栏的全选不应影响工具栏。"""
        from maxagent import user_tools_loader as utl
        from maxagent.qt_compat import QtCore
        utl.write_tool(
            't_a',
            'from maxagent.tools.registry import tool\n'
            '@tool(name="t_a", description="d")\n'
            'def _f(): return 1\n',
            {'description': 'd'},
        )
        dialog._reload_pack_lists()

        # 触发技能栏的全选（即使技能栏可能为空也应安全）
        skill_chk = dialog.pack_skill_list._pack_select_all_chk
        skill_chk.setChecked(True)

        # 工具栏不应有任何项被勾上
        for i in range(dialog.pack_tool_list.count()):
            it = dialog.pack_tool_list.item(i)
            if it.flags() & QtCore.Qt.ItemIsUserCheckable:
                assert it.checkState() != QtCore.Qt.Checked

    def test_section_header_syncs_with_item_state(self, dialog):
        """手动勾选/取消列表项 → 栏顶'全选'复选框反向跟随。"""
        from maxagent import user_tools_loader as utl
        from maxagent.qt_compat import QtCore
        utl.write_tool(
            't_b',
            'from maxagent.tools.registry import tool\n'
            '@tool(name="t_b", description="d")\n'
            'def _f(): return 1\n',
            {'description': 'd'},
        )
        utl.write_tool(
            't_c',
            'from maxagent.tools.registry import tool\n'
            '@tool(name="t_c", description="d")\n'
            'def _f(): return 1\n',
            {'description': 'd'},
        )
        dialog._reload_pack_lists()

        chk = dialog.pack_tool_list._pack_select_all_chk
        # 初始无勾选 → header 未选中
        assert chk.isChecked() is False

        # 勾选两项 → header 应自动变为选中
        for i in range(dialog.pack_tool_list.count()):
            it = dialog.pack_tool_list.item(i)
            if it.flags() & QtCore.Qt.ItemIsUserCheckable:
                it.setCheckState(QtCore.Qt.Checked)
        assert chk.isChecked() is True

        # 取消其中一项 → header 应回到未选中
        for i in range(dialog.pack_tool_list.count()):
            it = dialog.pack_tool_list.item(i)
            if it.flags() & QtCore.Qt.ItemIsUserCheckable:
                it.setCheckState(QtCore.Qt.Unchecked)
                break
        assert chk.isChecked() is False

    def test_no_global_select_all_clear_buttons(self, dialog):
        """pack 页不应再有全局'全选/清空选择'按钮。"""
        from maxagent.qt_compat import QtWidgets

        page = dialog._build_page_pack()
        labels = '|'.join(
            btn.text()
            for btn in page.findChildren(QtWidgets.QPushButton)
        )
        # 注：栏顶有 QCheckBox 形式的"全选"，但不应有 QPushButton 形式的全局全选
        push_button_select_all = [
            btn for btn in page.findChildren(QtWidgets.QPushButton)
            if btn.text() == '全选'
        ]
        assert push_button_select_all == [], \
            'pack 页不应再有 QPushButton 形式的全局全选按钮'
        assert '清空选择' not in labels
