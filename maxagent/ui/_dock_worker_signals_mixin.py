#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MaxAgentDockWidget 的 AgentWorker 信号槽 mixin。

包含流式 chunk / 工具起讫 / 状态 / 系统通知 / TODO / 完成 / 失败
/ turn 落盘节流 / Skill 提议弹窗 等 UI 更新槽。所有 ``self.*`` 属性由
``MaxAgentDockWidget.__init__`` 初始化。

拆分自 dock_widget.py，行为完全等价。
"""

from __future__ import absolute_import
from __future__ import print_function

from ..logger import get_logger
from ..qt_compat import QtCore, QtWidgets
from .emoji_compat import ee as _ee

logger = get_logger(__name__)


class _WorkerSignalsMixin(object):
    """AgentWorker 触发的 UI 信号处理。"""

    def _on_chunk(self, chunk):
        self._renderer.add_assistant_chunk(chunk)

    def _on_text_complete(self, _text):
        self._renderer.end_turn()

    def _on_tool_started(self, name, args_str, call_id):
        from ..tools.registry import get_tool
        spec = get_tool(name)
        dangerous = bool(spec and spec.dangerous)
        block = self._renderer.add_tool_call(
            name, args_str, dangerous=dangerous,
        )
        if call_id:
            self._pending_tool_blocks[call_id] = block

    def _on_tool_finished(self, name, ok, result_str, call_id):
        # 从映射里取出对应 block 并填入结果（不再新增 widget）
        block = self._pending_tool_blocks.pop(call_id, None)
        if block is not None:
            block.set_result(ok, result_str)
        else:
            # 兜底：找不到 block 时，作为独立条目展示
            self._renderer.add_status(
                '工具 {} 完成: {}'.format(name, 'ok' if ok else 'fail')
            )
        # 工具结束后开个新气泡待 LLM 继续说话
        self._renderer.add_assistant_start()

    def _on_status(self, text):
        self.status_label.setText(text)

    def _on_system_notice(self, level, message):
        """将 worker 发出的系统通知插入对话流为持久气泡。

        level: 'info' / 'warn' / 'error'
        """
        try:
            self._renderer.add_system_notice(level, message)
        except Exception:  # pylint: disable=broad-except
            # 通知渲染失败不应中断主流程
            logger.debug('add_system_notice failed', exc_info=True)

    def _on_todo_updated(self, session_id, snapshot):
        """LLM 通过 todo_write/update_status 修改清单后触发此槽。

        每会话保持一张任务卡：首次出现时插入气泡，后续更新就地刷新，
        避免把整段对话历史被 checklist 占满。
        """
        try:
            self._renderer.add_or_update_todo_bubble(session_id, snapshot)
        except Exception:  # pylint: disable=broad-except
            logger.debug('add_or_update_todo_bubble failed', exc_info=True)

    def _on_finished(self):
        self._renderer.end_turn()
        self.status_label.setText(_ee('✅') + ' 完成')
        self._set_running(False)
        # 停止节流 timer，避免 finished 后再触发一次冗余保存
        timer = getattr(self, '_turn_save_timer', None)
        if timer is not None and timer.isActive():
            timer.stop()
        self._save_current_session()
        self._refresh_context_label()

    def _on_turn_progress(self):
        """崩溃防丢：worker 每完成一步立即落盘（带节流）。

        触发时机：worker 追加了 assistant 消息或 tool_result 之后。
        session_mgr.save 用 tmp+rename 原子写，重复触发无副作用；
        长任务中途 Max 崩溃时，已保存的步骤会在下次启动时正确加载。

        节流策略：最短 500ms 落一次盘。避免长任务连续追加大量
        tool_result 时把 IO 打满；即使崩溃丢失也最多丢 500ms 的进度。
        """
        # 复用同一个 QTimer 单发，避免重复排队
        timer = getattr(self, '_turn_save_timer', None)
        if timer is None:
            timer = QtCore.QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(500)
            timer.timeout.connect(self._do_turn_progress_save)
            self._turn_save_timer = timer
        if not timer.isActive():
            timer.start()

    def _do_turn_progress_save(self):
        """turn_progress 节流后的真正落盘动作。"""
        try:
            self._save_current_session()
        except Exception:  # pylint: disable=broad-except
            # 落盘失败不能阻塞下一步；下次触发时会重试
            logger.warning('turn_progress 落盘失败', exc_info=True)

    def _on_failed(self, err):
        self._renderer.add_error(err)
        self.status_label.setText(_ee('❌') + ' 失败')
        self._set_running(False)
        # 停止节流 timer，避免 failed 后再触发一次冗余保存
        timer = getattr(self, '_turn_save_timer', None)
        if timer is not None and timer.isActive():
            timer.stop()
        # 失败也保存：用户能在历史里看到失败原因
        self._save_current_session()
        self._refresh_context_label()

    def _on_skill_proposed(self, manifest, impl_code):
        # type: (dict, str) -> None
        """worker 提议把本轮操作沉淀为 Skill，弹出确认对话框。"""
        name = manifest.get('name', '未命名')
        text = (
            '检测到本次会话执行了一系列成功操作。\n'
            '是否把该流程保存为可复用 Skill？\n\n'
            '名称：{}\n'
            '状态：draft\n'
            '触发词：{}\n\n'
            '保存后可在设置面板的技能管理中查看和编辑。'
        ).format(
            name,
            ' / '.join(manifest.get('trigger_keywords', []) or ['（无）']),
        )
        ret = QtWidgets.QMessageBox.question(
            self, '保存为 Skill？', text,
            QtWidgets.QMessageBox.Save
            | QtWidgets.QMessageBox.Discard,
            QtWidgets.QMessageBox.Discard,
        )
        if ret != QtWidgets.QMessageBox.Save:
            return
        try:
            from ..skills import Skill, SkillManager
            skill = Skill.from_dict(manifest)
            mgr = SkillManager()
            mgr.save(skill, overwrite=True)
            if impl_code:
                impl_path = mgr._impl_path_for(skill)
                with open(impl_path, 'w', encoding='utf-8') as fh:
                    fh.write(impl_code)
            self._renderer.add_status(
                '已保存 Skill 草案：{}'.format(name),
            )
        except Exception as exc:  # pylint: disable=broad-except
            QtWidgets.QMessageBox.critical(
                self, '保存失败',
                '保存 Skill 失败：{}'.format(exc),
            )
