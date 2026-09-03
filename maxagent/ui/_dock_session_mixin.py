#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MaxAgentDockWidget 的会话管理相关方法 mixin。

包含会话下拉刷新、启动引导、加载、回放、保存、新建/切换/重命名/删除。
所有 ``self.*`` 属性由 ``MaxAgentDockWidget.__init__`` 初始化，本 mixin
不引入额外状态。

拆分自 dock_widget.py，行为完全等价。
"""

from __future__ import absolute_import
from __future__ import print_function

import json

from ..logger import get_logger
from ..qt_compat import QtWidgets
from .bubbles import AssistantBubble as _AssistantBubble
from .emoji_compat import ee as _ee

logger = get_logger(__name__)


class _SessionMixin(object):
    """会话的加载/切换/持久化。"""

    def _refresh_sessions_combo(self, select_sid=None):
        """刷新会话下拉，可选择切到指定 sid。"""
        self.session_combo.blockSignals(True)
        self.session_combo.clear()
        target_idx = -1
        for i, m in enumerate(self._session_mgr.list_sessions()):
            label = '{}  ({}条)'.format(m.title or '未命名', m.message_count)
            self.session_combo.addItem(label, m.sid)
            if select_sid and m.sid == select_sid:
                target_idx = i
        if target_idx >= 0:
            self.session_combo.setCurrentIndex(target_idx)
        self.session_combo.blockSignals(False)

    def _bootstrap_session(self):
        """启动时恢复上次会话或新建一个。

        策略：UI 状态里记录了 last_session_sid 时优先恢复；否则取最近的；
        都没有就 create 一个新的。
        """
        last_sid = getattr(self._ui_state, 'last_session_sid', '') or ''
        sessions = self._session_mgr.list_sessions()
        target = None
        if last_sid:
            for m in sessions:
                if m.sid == last_sid:
                    target = m
                    break
        if target is None and sessions:
            target = sessions[0]
        if target is None:
            # 第一次启动：创建一个新的
            # 把"当前员工身份"对应的 system prompt 注入到新会话——
            # LLM 自我介绍才会跟随员工名（修复 bug：尼娜会话仍说
            # "我是 MaxAgent" 的根因即此处之前没注入）。
            target = self._session_mgr.create_session(
                system_prompt=self._build_system_prompt_for_new_conv(),
            )
        self._load_session(target.sid)
        self._refresh_sessions_combo(select_sid=target.sid)

    def _load_session(self, sid):
        # type: (str) -> bool
        """加载指定会话到当前面板，返回是否成功。"""
        result = self._session_mgr.load(sid)
        if result is None:
            return False
        meta, conv = result
        self._current_session = meta
        self._conv = conv
        self._pending_tool_blocks.clear()
        self._renderer.clear()
        # 崩溃防丢：修复上次崩溃留下的孤立 tool_calls（assistant 消息
        # 里有 tool_calls，但对应的 tool 结果消息因崩溃未落盘）。
        # 未修复的话，下次发消息 API 会返回 400（tool_call 缺少配对 tool
        # 消息）。修复即为每个孤立 call 追加一条 ok=false 占位消息。
        try:
            repaired_calls = conv.repair_incomplete_tool_calls()
            if repaired_calls > 0:
                logger.info(
                    '会话 %s 修复了 %d 个孤立 tool_call（上次崩溃残留）',
                    sid, repaired_calls,
                )
                self._save_current_session(force=True)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning('repair_incomplete_tool_calls 异常: %s', exc)
        # 方案 C：从磁盘恢复的会话注入"重启对齐"提示，
        # 让 LLM 知道场景可能已变。空会话不注入。
        try:
            if conv.messages and not conv.has_restored_marker():
                injected = conv.inject_restored_notice()
                if injected:
                    # 立刻持久化，避免下次启动重复注入
                    self._save_current_session(force=True)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning('inject_restored_notice 异常: %s', exc)
        # 持久化最近一次会话 ID 到 ui_state
        try:
            self._ui_state.last_session_sid = sid
            self._ui_state_mgr.save(self._ui_state)
        except Exception:  # pylint: disable=broad-except
            logger.debug('save ui_state last_session_sid failed', exc_info=True)
        # 回放历史消息
        if not conv.messages:
            # 空会话：把当前最新的"员工身份" system prompt 覆写进去。
            # 这样老用户改名后切回这个空会话时，LLM 自我介绍也会立刻
            # 跟随新名字（修复 bug：截图里"尼娜"会话仍说"我是
            # MaxAgent"——根因就是空会话用了老存盘 prompt）。
            # 非空会话不动，保留历史身份氛围、避免对已有对话的破坏性升级。
            conv.system_prompt = self._build_system_prompt_for_new_conv()
            self._save_current_session(force=True)
            # 欢迎屏的助手称呼跟随员工档案——员工名 'MaxAgent'（默认）
            # 时与改造前完全一致；用户改名后立即生效。
            # 用 escape_name 复用员工模块的 HTML 转义，避免名字含
            # ``<script>`` 时被当 HTML 标签注入。
            emp = self._make_employee()
            from .employee import escape_name
            safe_name = escape_name(emp.name)
            self._renderer.add_welcome(
                '{} 你好，我是 <b style="color:#a8e6a8;">{}</b>。'
                '点击下方任一示例快速开始：'.format(_ee('👋'), safe_name)
            )
        else:
            self._replay_messages(conv)
        # 刷新 token 状态显示
        self._refresh_context_label()
        # 切会话后无条件跳到最新一条（问题 4）
        self._renderer.scroll_to_bottom_force()
        return True

    def _replay_messages(self, conv):
        """把 Conversation 里的消息按气泡形式重新渲染。

        工具调用按 (assistant tool_calls -> tool result) 配对展示，
        不再实际执行。
        """
        # 建索引: tool_call_id -> tool result message
        tool_results = {}
        for m in conv.messages:
            if m.role == 'tool' and m.tool_call_id:
                tool_results[m.tool_call_id] = m

        for m in conv.messages:
            if m.role == 'user':
                if m.content or getattr(m, 'attachments', None):
                    self._renderer.add_user(
                        m.content or '',
                        attachments=getattr(m, 'attachments', None),
                    )
            elif m.role == 'assistant':
                if m.content:
                    # 直接渲染最终版（不走流式）
                    self._renderer._close_streaming_if_any()  # noqa: SLF001
                    bubble = _AssistantBubble(
                        m.content,
                        employee=self._renderer._current_employee(),  # noqa: SLF001
                    )
                    self._renderer._append(bubble)  # noqa: SLF001
                if m.tool_calls:
                    for tc in m.tool_calls:
                        try:
                            fn = tc.get('function') or {}
                            name = fn.get('name', '')
                            args_str = fn.get('arguments', '{}')
                            call_id = tc.get('id', '')
                        except AttributeError:
                            continue
                        from ..tools.registry import get_tool
                        spec = get_tool(name)
                        dangerous = bool(spec and spec.dangerous)
                        block = self._renderer.add_tool_call(
                            name, args_str, dangerous=dangerous,
                        )
                        # 回填结果
                        rmsg = tool_results.get(call_id)
                        if rmsg is not None:
                            ok = True
                            try:
                                rj = json.loads(rmsg.content or '{}')
                                ok = bool(rj.get('ok', True))
                            except (TypeError, ValueError):
                                ok = True
                            block.set_result(ok, rmsg.content or '')
            elif m.role == 'system':
                # 中途的 system note，不展示给用户（避免污染观感）
                continue

    def _save_current_session(self, force=False):
        """保存当前会话到磁盘并刷新下拉。"""
        if self._current_session is None:
            return
        # 没消息时也允许保存（force=True），用于清空后立即落盘
        if not force and len(self._conv) == 0:
            return
        try:
            self._session_mgr.save(self._current_session, self._conv)
        except OSError as exc:
            logger.warning('保存会话失败: %s', exc)
            return
        # 刷新下拉但保持当前选中
        self._refresh_sessions_combo(select_sid=self._current_session.sid)

    def _on_new_session(self):
        if self._is_running:
            self._renderer.add_status('请先停止当前对话再新建会话')
            return
        # 先把当前会话存盘
        self._save_current_session()
        meta = self._session_mgr.create_session(
            system_prompt=self._build_system_prompt_for_new_conv(),
        )
        self._load_session(meta.sid)
        self._refresh_sessions_combo(select_sid=meta.sid)

    def _on_session_combo_changed(self, idx):
        if idx < 0 or self._is_running:
            return
        sid = self.session_combo.itemData(idx)
        if not sid or (self._current_session
                       and sid == self._current_session.sid):
            return
        # 切换前保存
        self._save_current_session()
        if not self._load_session(sid):
            self._renderer.add_error('加载会话失败: {}'.format(sid))

    def _on_rename_session(self):
        if self._current_session is None:
            return
        old = self._current_session.title
        new, ok = QtWidgets.QInputDialog.getText(
            self, '重命名会话', '新标题:',
            QtWidgets.QLineEdit.EchoMode.Normal, old,
        )
        if not ok:
            return
        new = (new or '').strip()
        if not new or new == old:
            return
        if self._session_mgr.rename(self._current_session.sid, new):
            self._current_session.title = new
            self._refresh_sessions_combo(
                select_sid=self._current_session.sid,
            )

    def _on_delete_session(self):
        if self._current_session is None:
            return
        if self._is_running:
            self._renderer.add_status('请先停止当前对话再删除会话')
            return
        ret = QtWidgets.QMessageBox.question(
            self, '删除会话',
            '确定要删除会话「{}」吗？此操作不可恢复。'.format(
                self._current_session.title,
            ),
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No,
        )
        if ret != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        sid = self._current_session.sid
        self._session_mgr.delete(sid)
        # 切到下一个会话（或新建一个）
        sessions = self._session_mgr.list_sessions()
        if sessions:
            self._load_session(sessions[0].sid)
            self._refresh_sessions_combo(select_sid=sessions[0].sid)
        else:
            meta = self._session_mgr.create_session(
                system_prompt=self._build_system_prompt_for_new_conv(),
            )
            self._load_session(meta.sid)
            self._refresh_sessions_combo(select_sid=meta.sid)
