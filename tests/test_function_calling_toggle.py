#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Function Calling 总开关（profile.supports_tools）的回归测试。

固化以下事实，防止"只写不读 → 用户关掉对话仍带 tools"的历史 bug
重新出现：

1. AgentWorker 接受 ``tools_enabled`` 参数，默认 True。
2. ``tools_enabled=False`` 时，``_run_loop`` 加载完 tools_schema 后会
   立即清空，使本轮 LLM 调用不携带任何 tools 字段。
3. dispatch_task 路径同样尊重 ``profile.supports_tools=False``，跳过
   ``build_openai_tools_schema()``，整轮请求不带 tools。
4. UI 复选框 ``tools_chk`` 的状态会写入 ``profile.supports_tools``，
   并被 dock_widget 创建 worker 时显式读取并向下传递。

这里使用 ``MagicMock`` 替代真实 LLM，只关心传给 ``llm.chat`` 的
``tools`` 参数到底是 None / [] / 非空，不去打真的 HTTP。
"""

from __future__ import absolute_import
from __future__ import print_function

import inspect

import pytest


# ---------------------------------------------------------------------- #
# 1. AgentWorker 构造参数与默认值
# ---------------------------------------------------------------------- #
class TestAgentWorkerSignature:
    def test_tools_enabled_param_present(self):
        from maxagent.agent.worker import AgentWorker
        sig = inspect.signature(AgentWorker.__init__)
        assert 'tools_enabled' in sig.parameters

    def test_tools_enabled_default_true(self):
        """默认 True 保持向后兼容（不传时整条 tools 链路正常工作）。"""
        from maxagent.agent.worker import AgentWorker
        sig = inspect.signature(AgentWorker.__init__)
        assert sig.parameters['tools_enabled'].default is True

    def test_internal_attr_recorded(self):
        from maxagent.agent.worker import AgentWorker
        from maxagent.agent.conversation import Conversation
        from maxagent.tools.dispatcher import ToolDispatcher

        # 用最小 mock 构造一个 worker（不启动线程）
        from unittest.mock import MagicMock
        llm = MagicMock()
        conv = Conversation()
        dispatcher = ToolDispatcher(wrap_undo=False)

        # tools_enabled=False → 内部属性同步
        w = AgentWorker(
            llm_client=llm,
            conversation=conv,
            dispatcher=dispatcher,
            tools_enabled=False,
        )
        assert w._tools_enabled is False

        # 默认 True
        w2 = AgentWorker(
            llm_client=llm,
            conversation=conv,
            dispatcher=dispatcher,
        )
        assert w2._tools_enabled is True


# ---------------------------------------------------------------------- #
# 2. _run_loop 行为：tools_enabled=False 时 LLM 调用不带 tools
# ---------------------------------------------------------------------- #
class TestRunLoopHonorsToolsEnabled:
    def _build_worker(self, tools_enabled):
        from maxagent.agent.worker import AgentWorker
        from maxagent.agent.conversation import Conversation
        from maxagent.tools.dispatcher import ToolDispatcher
        from unittest.mock import MagicMock

        # mock LLM：单轮返回 finish_reason=stop，避免循环
        llm = MagicMock()
        llm.chat.return_value = {
            'content': 'ok',
            'tool_calls': [],
            'finish_reason': 'stop',
            'usage': {},
        }
        llm.get_last_usage.return_value = {}

        conv = Conversation(system_prompt='test sys')
        conv.add_user('hi')
        dispatcher = ToolDispatcher(wrap_undo=False)
        w = AgentWorker(
            llm_client=llm,
            conversation=conv,
            dispatcher=dispatcher,
            tools_enabled=tools_enabled,
        )
        return w, llm

    def test_tools_disabled_passes_empty_list(self):
        """tools_enabled=False → LLM.chat 收到的 tools 必须是空列表/None。"""
        w, llm = self._build_worker(tools_enabled=False)
        # 直接同步跑 _run_loop（在测试线程里执行即可）
        w._run_loop()
        assert llm.chat.called, 'LLM.chat 必须被调用'
        kwargs = llm.chat.call_args.kwargs
        tools = kwargs.get('tools')
        # _run_loop 会把空列表传给 chat；llm_client 内部会判空再决定
        # 是否往 payload 加 tools 字段——总之这里必须不是非空 list
        assert not tools, (
            '关闭 Function Calling 时 worker 不应再往 LLM 发送 tools schema，'
            '实际收到: {!r}'.format(tools)
        )

    def test_tools_enabled_passes_nonempty_when_registry_has_tools(
        self, monkeypatch,
    ):
        """默认 True 且工具表非空 → tools 应原样透传（保留原有行为）。"""
        # 用 monkeypatch 提供一个非空的工具 schema，避免依赖测试期注册表
        from maxagent.agent import worker as worker_mod
        fake_tools = [
            {'type': 'function', 'function': {'name': 'fake_tool'}},
        ]
        monkeypatch.setattr(
            worker_mod, 'build_openai_tools_schema', lambda: list(fake_tools),
        )
        w, llm = self._build_worker(tools_enabled=True)
        w._run_loop()
        kwargs = llm.chat.call_args.kwargs
        tools = kwargs.get('tools')
        assert tools == fake_tools, (
            '默认应原样透传工具 schema，实际为: {!r}'.format(tools)
        )


# ---------------------------------------------------------------------- #
# 3. dispatch_task 路径同样尊重 profile.supports_tools
# ---------------------------------------------------------------------- #
class TestDispatchTaskHonorsSupportTools:
    def _build_profile(self, supports_tools):
        from maxagent.config import LLMProfile
        return LLMProfile(
            name='vita-test',
            base_url='https://example.invalid/v1',
            api_key='x',
            model='youtu-vita',
            supports_tools=supports_tools,
        )

    def test_supports_tools_false_skips_schema(self, monkeypatch):
        from maxagent.bridge.handlers import dispatch_task as dt

        # 拦截 build_openai_tools_schema：本路径下永远不应被调用
        called = {'cnt': 0}

        def _fake_schema(*_a, **_kw):
            called['cnt'] += 1
            return [{'type': 'function', 'function': {'name': 'x'}}]

        monkeypatch.setattr(dt, 'build_openai_tools_schema', _fake_schema)

        # 拦截 build_client_from_profile：返回一个 mock LLM
        from unittest.mock import MagicMock
        fake_llm = MagicMock()
        # 让循环立刻退出（无 tool_calls + finish_reason=stop）
        fake_llm.chat.return_value = {
            'content': 'done',
            'tool_calls': [],
            'finish_reason': 'stop',
        }
        monkeypatch.setattr(
            dt, 'build_client_from_profile', lambda _p: fake_llm,
        )

        import threading
        prof = self._build_profile(supports_tools=False)
        result = dt._run_dispatch_loop(
            prompt='hi',
            profile=prof,
            max_rounds=1,
            timeout_sec=10,
            cancel_event=threading.Event(),
        )

        assert called['cnt'] == 0, (
            'supports_tools=False 时不应调用 build_openai_tools_schema'
        )
        assert fake_llm.chat.called
        # LLM.chat 也必须没拿到 tools
        kwargs = fake_llm.chat.call_args.kwargs
        assert not kwargs.get('tools'), (
            'supports_tools=False 时 dispatch_task 不应给 LLM 传 tools'
        )
        # 函数应该正常返回结果，不报错
        assert 'final_message' in result

    def test_supports_tools_true_loads_schema(self, monkeypatch):
        from maxagent.bridge.handlers import dispatch_task as dt
        from unittest.mock import MagicMock

        called = {'cnt': 0}

        def _fake_schema(*_a, **_kw):
            called['cnt'] += 1
            return [{'type': 'function', 'function': {'name': 'x'}}]

        monkeypatch.setattr(dt, 'build_openai_tools_schema', _fake_schema)

        fake_llm = MagicMock()
        fake_llm.chat.return_value = {
            'content': 'done',
            'tool_calls': [],
            'finish_reason': 'stop',
        }
        monkeypatch.setattr(
            dt, 'build_client_from_profile', lambda _p: fake_llm,
        )

        import threading
        prof = self._build_profile(supports_tools=True)
        dt._run_dispatch_loop(
            prompt='hi',
            profile=prof,
            max_rounds=1,
            timeout_sec=10,
            cancel_event=threading.Event(),
        )
        assert called['cnt'] == 1
        kwargs = fake_llm.chat.call_args.kwargs
        assert kwargs.get('tools'), (
            'supports_tools=True 时 dispatch_task 应给 LLM 传 tools'
        )


# ---------------------------------------------------------------------- #
# 4. UI 路径：tools_chk 与 profile.supports_tools 双向同步
# ---------------------------------------------------------------------- #
import os
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
def cfg_mgr(tmp_path):
    from maxagent.config import ConfigManager
    return ConfigManager(str(tmp_path / 'config.json'))


@pytest.fixture()
def settings_dlg(qapp, cfg_mgr):
    from maxagent.ui.settings_dialog import SettingsDialog
    d = SettingsDialog(cfg_mgr)
    yield d
    d.deleteLater()


class TestUiToolsCheckboxRoundTrip:
    def test_uncheck_writes_supports_tools_false(self, settings_dlg):
        """取消勾选 → 当前 profile 的 supports_tools 必须变成 False。"""
        # 找到当前 profile 并切到 OpenAI（默认有 supports_tools=True）
        dlg = settings_dlg
        # 直接操作 UI：取消勾选 + 应用
        dlg.tools_chk.setChecked(False)
        dlg._apply()
        prof = dlg._config.get_active_profile()
        assert prof.supports_tools is False

    def test_recheck_writes_supports_tools_true(self, settings_dlg):
        dlg = settings_dlg
        dlg.tools_chk.setChecked(True)
        dlg._apply()
        prof = dlg._config.get_active_profile()
        assert prof.supports_tools is True

    def test_tooltip_present(self, settings_dlg):
        """tooltip 必须包含视觉避坑提示，避免用户碰壁。"""
        dlg = settings_dlg
        tip = dlg.tools_chk.toolTip()
        assert 'vita' in tip.lower() or '视觉' in tip
        assert '关闭' in tip
