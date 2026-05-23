#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""设置面板"工具与技能"Tab + 完整测试链路的回归。"""

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
    from maxagent import user_tools_loader as utl
    from maxagent import user_rules_loader as url_mod
    from maxagent import skills as skills_mod

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

    yield

    utl.set_user_tools_dir_override(None)
    url_mod.set_user_rules_dir_override(None)


@pytest.fixture()
def config_mgr(tmp_path):
    from maxagent.config import ConfigManager
    cfg_path = tmp_path / 'config.json'
    return ConfigManager(str(cfg_path))


@pytest.fixture()
def dialog(qapp, config_mgr, isolate_dirs):
    from maxagent.ui.settings_dialog import SettingsDialog
    d = SettingsDialog(config_mgr)
    yield d
    d.deleteLater()


# ---------------------------------------------------------------------- #
# 工具与技能 Tab
# ---------------------------------------------------------------------- #
class TestPackTab:
    def test_pack_tab_present(self, dialog):
        labels = [
            dialog.nav.item(i).text() for i in range(dialog.nav.count())
        ]
        assert any('工具与技能' in t for t in labels)
        assert hasattr(dialog, 'pack_tool_list')
        assert hasattr(dialog, 'pack_skill_list')
        assert hasattr(dialog, 'pack_rule_list')

    def test_pack_tab_empty_state(self, dialog):
        """没有任何工具/技能/规则时，三栏都显示占位项且不可勾选。"""
        # 查找占位项（_reload_pack_lists 在创建时会执行一次）
        for lst in (dialog.pack_tool_list, dialog.pack_skill_list,
                    dialog.pack_rule_list):
            assert lst.count() == 1
            it = lst.item(0)
            from maxagent.qt_compat import QtCore
            assert not (it.flags() & QtCore.Qt.ItemIsUserCheckable)

    def test_pack_export_select_all(self, dialog):
        """添加内容后刷新 → 全选按钮覆盖三栏所有可选项。"""
        from maxagent import user_tools_loader as utl
        from maxagent import user_rules_loader as url_mod
        utl.write_tool(
            't_one',
            'from maxagent.tools.registry import tool\n'
            '@tool(name="t_one", description="d")\n'
            'def _f(): return 1\n',
            {'description': 'd'},
        )
        url_mod.write_rule('r_one', {'title': 't', 'content': 'c'})

        dialog._reload_pack_lists()
        dialog._pack_select_all()

        # 工具栏应有 1 个勾选
        from maxagent.qt_compat import QtCore
        checked_tools = sum(
            1 for i in range(dialog.pack_tool_list.count())
            if dialog.pack_tool_list.item(i).checkState()
            == QtCore.Qt.Checked
        )
        assert checked_tools == 1
        checked_rules = sum(
            1 for i in range(dialog.pack_rule_list.count())
            if dialog.pack_rule_list.item(i).checkState()
            == QtCore.Qt.Checked
        )
        assert checked_rules == 1

        # 清空后无勾选
        dialog._pack_clear()
        for lst in (dialog.pack_tool_list, dialog.pack_rule_list):
            for i in range(lst.count()):
                assert lst.item(i).checkState() != QtCore.Qt.Checked

    def test_pack_export_then_import_roundtrip(self, dialog, tmp_path,
                                               monkeypatch):
        """完整端到端：填表 → 导出 → import_pack 解析 → 数据一致。"""
        from maxagent import user_tools_loader as utl
        from maxagent import user_rules_loader as url_mod
        utl.write_tool(
            't_round',
            'from maxagent.tools.registry import tool\n'
            '@tool(name="t_round", description="x")\n'
            'def _f(): return 1\n',
            {'description': 'x'},
        )
        url_mod.write_rule('r_round', {'title': 'X', 'content': 'C'})
        dialog._reload_pack_lists()
        dialog._pack_select_all()

        # mock 文件保存对话框，自动给路径
        out = tmp_path / 'roundtrip.maxagent-pack'
        from maxagent.qt_compat import QtWidgets

        def _fake_save(*_args, **_kwargs):
            return (str(out), '*.maxagent-pack')

        def _fake_info(*_args, **_kwargs):
            pass  # 静默"导出成功"对话框

        monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName', _fake_save)
        monkeypatch.setattr(
            QtWidgets.QMessageBox, 'information', _fake_info,
        )

        dialog.pack_name_edit.setText('TestPack')
        dialog.pack_author_edit.setText('alice')
        dialog._on_pack_export()

        assert out.exists()
        # 用 pack 模块再次解析，验证内容
        from maxagent import pack
        parsed = pack.parse_pack(str(out))
        assert parsed['manifest']['name'] == 'TestPack'
        assert parsed['manifest']['author'] == 'alice'
        names_t = [t['name'] for t in parsed['tools']]
        names_r = [r['rule_id'] for r in parsed['rules']]
        assert 't_round' in names_t
        assert 'r_round' in names_r


# ---------------------------------------------------------------------- #
# 完整测试链路（覆盖 system prompt 真实化）
# ---------------------------------------------------------------------- #
class TestFullConnectionTest:
    def test_full_test_uses_real_system_prompt(self, dialog, monkeypatch):
        """完整测试路径必须用真实 build_default_system_prompt（>= 200 字符）
        + 真实 tools schema（>= 1 条）。

        通过 stub LLMClient.chat，断言它收到的 messages[0]['content']
        不再是简单的 "You are a helpful assistant."。
        """
        # 确保所有内置工具已加载（生产环境由 startup.py 触发，这里手动）
        from maxagent.tools import load_all_tools
        load_all_tools()

        captured = {}

        class _StubClient(object):
            def chat(self, messages, tools=None, stream=False,
                     on_delta=None, **_kw):
                captured['messages'] = messages
                captured['tools'] = tools
                captured['stream'] = stream
                if on_delta:
                    on_delta('ok')
                return {
                    'content': 'ok',
                    'tool_calls': [],
                    'finish_reason': 'stop',
                    'usage': {},
                }

        from maxagent.ui import settings_dialog as sd_mod
        monkeypatch.setattr(
            sd_mod, 'build_client_from_profile',
            lambda _prof: _StubClient(),
        )
        # 静默 messagebox
        from maxagent.qt_compat import QtWidgets
        monkeypatch.setattr(
            QtWidgets.QApplication, 'processEvents', lambda *_a, **_k: None,
        )

        dialog._test_connection_full()

        # 必须收到流式
        assert captured.get('stream') is True
        # 必须带 tools
        tools = captured.get('tools') or []
        assert len(tools) >= 1
        # system prompt 必须是真实长版本（不再是简单 8 字符 hello）
        msgs = captured.get('messages') or []
        assert msgs and msgs[0]['role'] == 'system'
        sys_prompt = msgs[0]['content']
        assert len(sys_prompt) > 200, (
            '完整测试应使用真实 system prompt '
            '({}字符过短)'.format(len(sys_prompt))
        )
        # 测试结果应已写入 test_label
        text = dialog.test_label.text()
        assert '完整测试通过' in text or '通过' in text
