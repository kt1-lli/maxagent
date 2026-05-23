#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""设置对话框 v2 改造的回归测试。

覆盖：
- 5 个 Tab Page 全部成功实例化
- Profile 列表的"复制为副本""设为默认""通过 _del_profile_by_name 删除"
- DEBUG 监控字段在 worker 上的初值

注意：测试在 offscreen 平台下运行，无需图形环境；
但需要 PySide2 或 PySide6 任一可用。
"""

from __future__ import absolute_import
from __future__ import print_function

import os

import pytest


# 在导入任何 Qt 之前设置 offscreen
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

try:
    from maxagent.qt_compat import QtWidgets  # noqa: F401
    HAS_QT = True
except Exception:  # pylint: disable=broad-except
    HAS_QT = False


@pytest.fixture(scope='module')
def qapp():
    """模块级 QApplication，多个测试共享，避免反复创建。"""
    if not HAS_QT:
        pytest.skip('Qt 不可用，跳过 UI 测试')
    from maxagent.qt_compat import QtWidgets as QW
    app = QW.QApplication.instance() or QW.QApplication([])
    yield app


@pytest.fixture()
def config_mgr(tmp_path):
    """临时目录 + ConfigManager，自带 default profile。"""
    from maxagent.config import ConfigManager
    cfg_path = tmp_path / 'config.json'
    cm = ConfigManager(str(cfg_path))
    return cm


@pytest.fixture()
def dialog(qapp, config_mgr):
    """构造 SettingsDialog 实例。"""
    from maxagent.ui.settings_dialog import SettingsDialog
    d = SettingsDialog(config_mgr)
    yield d
    d.deleteLater()


def test_nav_has_six_tabs(dialog):
    """左侧导航现在有 8 个 Tab：模型/联网/应用/助手形象/我的资源/日志/IDE 接口/帮助。

    注：原"我的规则"和"工具与技能"两项已合并为单个"我的资源"主 Tab，
    内部用横向子 Tab 切换 4 个视图。这是 v3 重构后的稳定结构。
    """
    assert dialog.nav.count() == 8
    assert dialog.stack.count() == 8
    labels = [dialog.nav.item(i).text() for i in range(dialog.nav.count())]
    assert any('模型' in t for t in labels)
    assert any('联网' in t for t in labels)
    assert any('应用' in t for t in labels)
    assert any('助手形象' in t for t in labels)
    assert any('我的资源' in t for t in labels)
    assert any('日志' in t for t in labels)
    assert any('IDE 接口' in t for t in labels)
    assert any('帮助' in t for t in labels)
    # 旧两个 Tab 已被并入"我的资源"，不应再独立存在
    assert not any('我的规则' == t.split()[-1] for t in labels)
    assert not any('工具与技能' == t.split()[-1] for t in labels)


def test_resources_subtabs_have_four_pages(dialog):
    """「我的资源」内部必须有 4 个横向子 Tab：规则/技能/工具/导入导出。"""
    # 切到"我的资源"主 Tab
    res_idx = next(
        i for i, (_l, k) in enumerate(dialog._NAV_ITEMS) if k == 'resources'
    )
    dialog.nav.setCurrentRow(res_idx)
    assert hasattr(dialog, 'resources_tabs')
    assert dialog.resources_tabs.count() == 4
    sub_labels = [
        dialog.resources_tabs.tabText(i)
        for i in range(dialog.resources_tabs.count())
    ]
    assert any('规则' in t for t in sub_labels)
    assert any('技能' in t for t in sub_labels)
    assert any('工具' in t for t in sub_labels)
    assert any('导入' in t and '导出' in t for t in sub_labels)


def test_resources_subtab_lists_initialized(dialog):
    """技能/工具子页的列表 widget 必须被构建出来。"""
    # 触发主 Tab 切换以确保子页已构建（lazy 场景下也必须可用）
    res_idx = next(
        i for i, (_l, k) in enumerate(dialog._NAV_ITEMS) if k == 'resources'
    )
    dialog.nav.setCurrentRow(res_idx)
    assert hasattr(dialog, '_skills_list')
    assert hasattr(dialog, '_tools_list')


def test_nav_switch_changes_stack_index(dialog):
    """点击导航项后 stacked widget index 同步切换。"""
    dialog.nav.setCurrentRow(2)
    assert dialog.stack.currentIndex() == 2
    dialog.nav.setCurrentRow(4)
    assert dialog.stack.currentIndex() == 4


def test_duplicate_profile(dialog, config_mgr, monkeypatch):
    """右键 → 复制为副本：核心是 ``_duplicate_profile`` 路径。"""
    src_name = config_mgr.get_active_profile_name()
    new_name = src_name + '-copy-test'

    # mock QInputDialog 返回新名称
    from maxagent.qt_compat import QtWidgets

    def _fake_get_text(*_args, **_kwargs):
        return (new_name, True)

    monkeypatch.setattr(QtWidgets.QInputDialog, 'getText', _fake_get_text)

    dialog._duplicate_profile(src_name)
    # 副本应已写入配置
    assert config_mgr.get_profile(new_name) is not None
    # 列表应该刷新选中新项
    assert dialog.profile_list.currentItem().text() == new_name


def test_set_active_profile_via_menu_action(dialog, config_mgr):
    """右键 → 设为默认：直接调 ``_set_active_profile`` 验证副作用。"""
    # 准备一个非 active 的 profile
    from maxagent.config import LLMProfile
    other = LLMProfile(
        name='alt-profile',
        base_url='http://localhost:11434/v1',
        api_key='',
        model='qwen2.5:7b',
    )
    config_mgr.upsert_profile(other)
    dialog._reload_profiles()

    assert config_mgr.get_active_profile_name() != 'alt-profile'
    dialog._set_active_profile('alt-profile')
    assert config_mgr.get_active_profile_name() == 'alt-profile'


def test_del_profile_by_name_blocks_active(dialog, config_mgr, monkeypatch):
    """不允许删除当前激活的 profile，需弹警告。"""
    active = config_mgr.get_active_profile_name()
    triggered = {'warn': 0}

    from maxagent.qt_compat import QtWidgets

    def _fake_warn(*_args, **_kwargs):
        triggered['warn'] += 1

    monkeypatch.setattr(QtWidgets.QMessageBox, 'warning', _fake_warn)
    dialog._del_profile_by_name(active)
    # 没真删
    assert config_mgr.get_profile(active) is not None
    # 弹了警告
    assert triggered['warn'] == 1


def test_log_state_radio_load(dialog, config_mgr):
    """日志状态三态单选应按当前 cfg.log_level 选中对应 radio。"""
    cfg = config_mgr.config
    # DEBUG → log_radio_debug 选中
    cfg.log_level = 'DEBUG'
    dialog._load_app_settings()
    assert dialog.log_radio_debug.isChecked() is True
    assert dialog.log_radio_on.isChecked() is False
    assert dialog.log_radio_off.isChecked() is False

    # OFF → log_radio_off 选中
    cfg.log_level = 'OFF'
    dialog._load_app_settings()
    assert dialog.log_radio_off.isChecked() is True
    assert dialog.log_radio_debug.isChecked() is False

    # INFO（默认）→ log_radio_on 选中
    cfg.log_level = 'INFO'
    dialog._load_app_settings()
    assert dialog.log_radio_on.isChecked() is True
    assert dialog.log_radio_off.isChecked() is False
    assert dialog.log_radio_debug.isChecked() is False

    # 老配置 WARNING：在 _load_app_settings 里被归一化成 INFO
    cfg.log_level = 'WARNING'
    dialog._load_app_settings()
    assert dialog.log_radio_on.isChecked() is True


def test_log_state_radio_writes_back(dialog, config_mgr, monkeypatch):
    """点击 DEBUG radio 后，cfg.log_level 应被写回 'DEBUG'，
    且 logger 真实级别同步切换。"""
    cfg = config_mgr.config
    cfg.log_level = 'INFO'
    dialog._load_app_settings()
    # 触发：让 log_radio_debug 选中——QButtonGroup 互斥下会发 toggled
    dialog.log_radio_debug.setChecked(True)
    assert cfg.log_level == 'DEBUG'

    # 切到 OFF
    dialog.log_radio_off.setChecked(True)
    assert cfg.log_level == 'OFF'


def test_worker_debug_metrics_initialized():
    """AgentWorker 必须初始化 DEBUG 监控用的字段。"""
    from maxagent.agent.worker import AgentWorker
    from maxagent.agent.conversation import Conversation
    from maxagent.tools.dispatcher import ToolDispatcher

    class _FakeLLM:
        pass

    w = AgentWorker(
        llm_client=_FakeLLM(),
        conversation=Conversation(system_prompt='x'),
        dispatcher=ToolDispatcher(wrap_undo=False),
    )
    assert hasattr(w, '_chunk_count')
    assert hasattr(w, '_chunk_emit_count')
    assert hasattr(w, '_chunk_first_ts')
    assert hasattr(w, '_llm_call_started_ts')
    assert w._chunk_count == 0
    assert w._chunk_emit_count == 0
