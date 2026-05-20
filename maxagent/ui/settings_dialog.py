#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""设置对话框：管理 Profile（API Key / Base URL / 模型 / 流式 / 温度）。

UI 布局:
+-----------------------------------------------+
| [Profile 列表]  | 名称: [____________________]|
|   - default     | Base URL: [_______________] |
|   - ollama      | API Key: [____________] [👁]|
|   - lm-studio   | 模型: [___________________] |
|   - deepseek    | 温度: [0.7]                 |
| [+新建][-删除]  | [✓] 流式 [✓] 工具调用      |
|                 |                             |
|                 | [测试连接] [应用] [关闭]    |
+-----------------------------------------------+
"""

from __future__ import absolute_import
from __future__ import print_function

from typing import Any
from typing import Optional

from ..config import ConfigManager
from ..config import LLMProfile
from ..llm_client import build_client_from_profile
from ..llm_client import diagnose_base_url
from ..llm_client import LLMClient
from ..llm_client import LLMError
from ..qt_compat import QtCore
from ..qt_compat import QtGui
from ..qt_compat import QtWidgets


class SettingsDialog(QtWidgets.QDialog):
    """设置对话框。"""

    def __init__(self, config_manager, parent=None):
        # type: (ConfigManager, Optional[Any]) -> None
        super(SettingsDialog, self).__init__(parent)
        self._config = config_manager
        self.setWindowTitle('MaxAgent 设置')
        self.setMinimumSize(720, 460)
        self._build_ui()
        self._reload_profiles()
        self._dirty = False

    # ------------------------------------------------------------------ #
    # UI 构建
    # ------------------------------------------------------------------ #
    def _build_ui(self):
        outer = QtWidgets.QHBoxLayout(self)

        # 左：Profile 列表
        left = QtWidgets.QVBoxLayout()
        left.setSpacing(4)
        left.addWidget(QtWidgets.QLabel('Profile 列表'))
        self.profile_list = QtWidgets.QListWidget()
        self.profile_list.setMinimumWidth(180)
        self.profile_list.currentItemChanged.connect(
            self._on_profile_selected,
        )
        left.addWidget(self.profile_list, 1)

        btns = QtWidgets.QHBoxLayout()
        self.add_btn = QtWidgets.QPushButton('+ 新建')
        self.add_btn.clicked.connect(self._add_profile)
        btns.addWidget(self.add_btn)
        self.del_btn = QtWidgets.QPushButton('- 删除')
        self.del_btn.clicked.connect(self._del_profile)
        btns.addWidget(self.del_btn)
        left.addLayout(btns)

        outer.addLayout(left, 1)

        # 右：编辑表单
        right = QtWidgets.QFormLayout()
        right.setSpacing(8)
        right.setLabelAlignment(QtCore.Qt.AlignRight)

        self.name_edit = QtWidgets.QLineEdit()
        self.name_edit.setPlaceholderText('如: my-deepseek')
        right.addRow('名称:', self.name_edit)

        self.base_url_edit = QtWidgets.QLineEdit()
        self.base_url_edit.setPlaceholderText(
            '如: https://api.deepseek.com/v1',
        )
        right.addRow('Base URL:', self.base_url_edit)

        # base_url 静态体检提示（路径错填等常见问题）
        self.base_url_hint = QtWidgets.QLabel('')
        self.base_url_hint.setStyleSheet('color:#b8923a; font-size:11px;')
        self.base_url_hint.setWordWrap(True)
        self.base_url_hint.hide()
        right.addRow('', self.base_url_hint)
        self.base_url_edit.textChanged.connect(self._refresh_base_url_hint)

        # API Key + 显示/隐藏
        key_row = QtWidgets.QHBoxLayout()
        key_row.setSpacing(4)
        self.api_key_edit = QtWidgets.QLineEdit()
        self.api_key_edit.setEchoMode(QtWidgets.QLineEdit.Password)
        self.api_key_edit.setPlaceholderText(
            '本地模型可留空，或填 ollama / lmstudio 等占位符',
        )
        key_row.addWidget(self.api_key_edit, 1)
        self.show_key_btn = QtWidgets.QPushButton('👁')
        self.show_key_btn.setFixedWidth(28)
        self.show_key_btn.setCheckable(True)
        self.show_key_btn.toggled.connect(self._toggle_key_visible)
        key_row.addWidget(self.show_key_btn)
        key_widget = QtWidgets.QWidget()
        key_widget.setLayout(key_row)
        right.addRow('API Key:', key_widget)

        self.model_edit = QtWidgets.QLineEdit()
        self.model_edit.setPlaceholderText(
            '如: deepseek-chat / qwen2.5:7b / gpt-4o-mini',
        )
        right.addRow('模型:', self.model_edit)

        self.temperature_spin = QtWidgets.QDoubleSpinBox()
        self.temperature_spin.setRange(0.0, 2.0)
        self.temperature_spin.setSingleStep(0.1)
        self.temperature_spin.setValue(0.7)
        right.addRow('温度:', self.temperature_spin)

        self.max_tokens_spin = QtWidgets.QSpinBox()
        self.max_tokens_spin.setRange(0, 200000)
        self.max_tokens_spin.setSingleStep(256)
        self.max_tokens_spin.setSpecialValueText('(由模型决定)')
        right.addRow('最大输出 token:', self.max_tokens_spin)

        self.timeout_spin = QtWidgets.QSpinBox()
        self.timeout_spin.setRange(5, 3600)
        self.timeout_spin.setValue(120)
        self.timeout_spin.setSuffix(' 秒')
        right.addRow('请求超时:', self.timeout_spin)

        self.max_loops_spin = QtWidgets.QSpinBox()
        self.max_loops_spin.setRange(4, 200)
        self.max_loops_spin.setValue(40)
        self.max_loops_spin.setSuffix(' 轮')
        self.max_loops_spin.setToolTip(
            '一次用户输入后，Agent 最多允许的「LLM↔工具」往返轮数。\n'
            '批量任务（如"测试所有工具"）可能需要 40-80 轮。\n'
            '接近上限时会自动提示模型收尾，超限后会保留已完成结果。',
        )
        right.addRow('工具调用上限:', self.max_loops_spin)

        self.max_history_tokens_spin = QtWidgets.QSpinBox()
        self.max_history_tokens_spin.setRange(2000, 200000)
        self.max_history_tokens_spin.setSingleStep(2000)
        self.max_history_tokens_spin.setValue(32000)
        self.max_history_tokens_spin.setSuffix(' tokens')
        self.max_history_tokens_spin.setToolTip(
            '历史对话保留的 token 预算上限。\n'
            '每次发请求前自动裁剪最早消息，但严格保护：\n'
            '  • system 提示\n'
            '  • 最近 4 条消息\n'
            '  • assistant(tool_calls) 与对应 tool 结果的配对\n\n'
            '推荐值：\n'
            '  • 长上下文模型 (GPT-4 128K / Claude 200K): 64000\n'
            '  • DeepSeek / Qwen Max (32-64K): 32000 (默认)\n'
            '  • 本地小模型 (7B/14B): 8000-16000',
        )
        right.addRow('历史 token 预算:', self.max_history_tokens_spin)

        self.stream_chk = QtWidgets.QCheckBox('启用流式输出')
        self.stream_chk.setChecked(True)
        right.addRow('', self.stream_chk)

        self.tools_chk = QtWidgets.QCheckBox('启用 Function Calling')
        self.tools_chk.setChecked(True)
        right.addRow('', self.tools_chk)

        # 高级：自定义 header
        self.headers_edit = QtWidgets.QPlainTextEdit()
        self.headers_edit.setFixedHeight(60)
        self.headers_edit.setPlaceholderText(
            '可选：每行一个 KEY=VALUE，例如\nX-Org-Id=foo\n',
        )
        right.addRow('自定义 Header:', self.headers_edit)

        # === 全局应用设置（与 Profile 无关，作用于整个 MaxAgent）===
        sep_label = QtWidgets.QLabel('— 应用设置 —')
        sep_label.setStyleSheet('color:#888; padding-top:6px;')
        right.addRow('', sep_label)

        self.auto_show_chk = QtWidgets.QCheckBox(
            'Max 启动时自动显示 MaxAgent 面板',
        )
        self.auto_show_chk.setToolTip(
            '关闭后，Max 启动时不会自动弹出面板，需在 MAXScript Listener\n'
            '中执行 g_show_max_agent() 或通过菜单/快捷键手动显示。',
        )
        self.auto_show_chk.toggled.connect(self._on_app_setting_changed)
        right.addRow('', self.auto_show_chk)

        self.allow_escape_chk = QtWidgets.QCheckBox(
            '允许使用 run_maxscript / run_python 工具',
        )
        self.allow_escape_chk.setToolTip(
            '关闭后 LLM 无法执行任意脚本，仅能调用预定义工具。',
        )
        self.allow_escape_chk.toggled.connect(self._on_app_setting_changed)
        right.addRow('', self.allow_escape_chk)

        self.confirm_exec_chk = QtWidgets.QCheckBox(
            '执行脚本工具前弹窗确认',
        )
        self.confirm_exec_chk.toggled.connect(self._on_app_setting_changed)
        right.addRow('', self.confirm_exec_chk)

        self.wrap_undo_chk = QtWidgets.QCheckBox(
            '工具操作包裹 Undo（可 Ctrl+Z 回滚）',
        )
        self.wrap_undo_chk.toggled.connect(self._on_app_setting_changed)
        right.addRow('', self.wrap_undo_chk)

        # 测试连接结果
        self.test_label = QtWidgets.QLabel('')
        self.test_label.setStyleSheet('color:#888;')
        right.addRow('', self.test_label)

        # 操作按钮
        op_row = QtWidgets.QHBoxLayout()
        op_row.addStretch(1)
        self.test_btn = QtWidgets.QPushButton('🧪 测试连接')
        self.test_btn.setToolTip('发送一条最简单的非流式 ping，仅验证 base_url + key 基本可达。')
        self.test_btn.clicked.connect(self._test_connection)
        op_row.addWidget(self.test_btn)
        self.test_full_btn = QtWidgets.QPushButton('🔬 完整测试')
        self.test_full_btn.setToolTip(
            '复刻真实对话的请求：开启流式 + 携带全部工具 schema。\n'
            '当"测试连接"通过但实际对话报错时，用此按钮定位差异。'
        )
        self.test_full_btn.clicked.connect(self._test_connection_full)
        op_row.addWidget(self.test_full_btn)
        self.apply_btn = QtWidgets.QPushButton('💾 应用')
        self.apply_btn.clicked.connect(self._apply)
        op_row.addWidget(self.apply_btn)
        self.close_btn = QtWidgets.QPushButton('关闭')
        self.close_btn.clicked.connect(self.accept)
        op_row.addWidget(self.close_btn)

        right_box = QtWidgets.QVBoxLayout()
        right_box.addLayout(right, 1)
        right_box.addLayout(op_row)

        right_widget = QtWidgets.QWidget()
        right_widget.setLayout(right_box)
        outer.addWidget(right_widget, 2)

    # ------------------------------------------------------------------ #
    # Profile 加载/保存
    # ------------------------------------------------------------------ #
    def _reload_profiles(self):
        self.profile_list.blockSignals(True)
        self.profile_list.clear()
        active = self._config.get_active_profile_name()
        for name in self._config.list_profile_names():
            item = QtWidgets.QListWidgetItem(name)
            if name == active:
                f = item.font()
                f.setBold(True)
                item.setFont(f)
            self.profile_list.addItem(item)
        # 选中 active
        for i in range(self.profile_list.count()):
            if self.profile_list.item(i).text() == active:
                self.profile_list.setCurrentRow(i)
                break
        self.profile_list.blockSignals(False)
        self._load_to_form(active)
        # 全局应用设置（与具体 Profile 无关）
        self._load_app_settings()

    def _load_app_settings(self):
        """从 AppConfig 把全局开关加载到对应复选框。"""
        cfg = self._config.config
        # 阻塞信号，避免触发 _on_app_setting_changed 把 _dirty 置为 True
        for chk, val in (
            (self.auto_show_chk, cfg.auto_show_on_startup),
            (self.allow_escape_chk, cfg.allow_escape_hatch),
            (self.confirm_exec_chk, cfg.confirm_before_exec),
            (self.wrap_undo_chk, cfg.wrap_undo),
        ):
            chk.blockSignals(True)
            chk.setChecked(bool(val))
            chk.blockSignals(False)

    def _on_app_setting_changed(self, _checked):
        """任一全局开关变化时立刻保存到 AppConfig（不需要点应用）。

        全局开关数量少且独立于 Profile 编辑，立即写盘体验更直接，
        而且避免用户切换 Profile 时被"未保存提示"打断。
        """
        cfg = self._config.config
        cfg.auto_show_on_startup = bool(self.auto_show_chk.isChecked())
        cfg.allow_escape_hatch = bool(self.allow_escape_chk.isChecked())
        cfg.confirm_before_exec = bool(self.confirm_exec_chk.isChecked())
        cfg.wrap_undo = bool(self.wrap_undo_chk.isChecked())
        try:
            self._config.save()
        except Exception as exc:  # pylint: disable=broad-except
            QtWidgets.QMessageBox.warning(
                self, '保存失败', '应用设置写盘失败: {}'.format(exc),
            )

    def _load_to_form(self, profile_name):
        prof = self._config.get_profile(profile_name)
        if prof is None:
            return
        self.name_edit.setText(prof.name)
        self.base_url_edit.setText(prof.base_url)
        self.api_key_edit.setText(prof.api_key or '')
        self.model_edit.setText(prof.model)
        self.temperature_spin.setValue(float(prof.temperature))
        self.max_tokens_spin.setValue(int(prof.max_tokens or 0))
        self.timeout_spin.setValue(int(prof.timeout))
        self.max_loops_spin.setValue(int(getattr(prof, 'max_tool_loops', 40) or 40))
        self.max_history_tokens_spin.setValue(
            int(getattr(prof, 'max_history_tokens', 32000) or 32000),
        )
        self.stream_chk.setChecked(bool(prof.stream))
        self.tools_chk.setChecked(bool(prof.supports_tools))
        # headers
        if prof.extra_headers:
            text = '\n'.join(
                '{}={}'.format(k, v)
                for k, v in prof.extra_headers.items()
            )
        else:
            text = ''
        self.headers_edit.setPlainText(text)
        self.test_label.setText('')

    def _read_form(self):
        """从表单读出一个 LLMProfile 对象。"""
        # 解析 headers
        headers = {}
        for line in self.headers_edit.toPlainText().splitlines():
            line = line.strip()
            if not line or '=' not in line:
                continue
            k, v = line.split('=', 1)
            headers[k.strip()] = v.strip()

        return LLMProfile(
            name=self.name_edit.text().strip(),
            base_url=self.base_url_edit.text().strip(),
            api_key=self.api_key_edit.text(),
            model=self.model_edit.text().strip(),
            temperature=float(self.temperature_spin.value()),
            max_tokens=(
                int(self.max_tokens_spin.value())
                if self.max_tokens_spin.value() > 0 else 4096
            ),
            timeout=int(self.timeout_spin.value()),
            max_tool_loops=int(self.max_loops_spin.value()),
            max_history_tokens=int(self.max_history_tokens_spin.value()),
            stream=bool(self.stream_chk.isChecked()),
            supports_tools=bool(self.tools_chk.isChecked()),
            extra_headers=headers,
        )

    def _on_profile_selected(self, cur, prev):
        if prev is not None and self._dirty:
            ret = QtWidgets.QMessageBox.question(
                self, '未保存',
                '当前 Profile 已修改但未保存，是否丢弃修改？',
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            )
            if ret != QtWidgets.QMessageBox.Yes:
                # 回退选中
                self.profile_list.blockSignals(True)
                self.profile_list.setCurrentItem(prev)
                self.profile_list.blockSignals(False)
                return
        if cur is not None:
            self._load_to_form(cur.text())
            self._dirty = False

    # ------------------------------------------------------------------ #
    # 槽：按钮
    # ------------------------------------------------------------------ #
    def _add_profile(self):
        text, ok = QtWidgets.QInputDialog.getText(
            self, '新建 Profile', 'Profile 名称（仅英文/数字/连字符）:',
        )
        if not ok or not text.strip():
            return
        name = text.strip()
        if self._config.get_profile(name) is not None:
            QtWidgets.QMessageBox.warning(
                self, '已存在', '同名 Profile 已存在: {}'.format(name),
            )
            return
        prof = LLMProfile(
            name=name,
            base_url='https://api.openai.com/v1',
            api_key='',
            model='gpt-4o-mini',
        )
        self._config.upsert_profile(prof)
        self._config.save()
        self._reload_profiles()
        # 选中新建项
        for i in range(self.profile_list.count()):
            if self.profile_list.item(i).text() == name:
                self.profile_list.setCurrentRow(i)
                break

    def _del_profile(self):
        item = self.profile_list.currentItem()
        if item is None:
            return
        name = item.text()
        if name == self._config.get_active_profile_name():
            QtWidgets.QMessageBox.warning(
                self, '禁止删除', '不能删除当前激活的 Profile，请先切换。',
            )
            return
        ret = QtWidgets.QMessageBox.question(
            self, '确认', '确定删除 Profile "{}"？'.format(name),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if ret != QtWidgets.QMessageBox.Yes:
            return
        self._config.delete_profile(name)
        self._config.save()
        self._reload_profiles()

    def _toggle_key_visible(self, checked):
        if checked:
            self.api_key_edit.setEchoMode(QtWidgets.QLineEdit.Normal)
        else:
            self.api_key_edit.setEchoMode(QtWidgets.QLineEdit.Password)

    def _apply(self):
        try:
            prof = self._read_form()
        except Exception as exc:  # pylint: disable=broad-except
            QtWidgets.QMessageBox.warning(
                self, '表单错误', '读取表单失败: {}'.format(exc),
            )
            return
        if not prof.name:
            QtWidgets.QMessageBox.warning(self, '缺少名称', '请填写 Profile 名称')
            return
        if not prof.base_url:
            QtWidgets.QMessageBox.warning(self, '缺少 URL', '请填写 Base URL')
            return
        if not prof.model:
            QtWidgets.QMessageBox.warning(self, '缺少模型', '请填写模型名')
            return
        # 保存
        self._config.upsert_profile(prof)
        # 如果是当前 active，无需切换；否则保留原 active
        self._config.save()
        self._dirty = False
        self.test_label.setText('✓ 已保存')
        self.test_label.setStyleSheet('color:#8fce8f;')
        self._reload_profiles()

    def _test_connection(self):
        try:
            prof = self._read_form()
        except Exception as exc:  # pylint: disable=broad-except
            self.test_label.setText('✗ 表单错误: {}'.format(exc))
            self.test_label.setStyleSheet('color:#e57373;')
            return
        self.test_label.setText('⋯ 测试中...')
        self.test_label.setStyleSheet('color:#888;')
        QtWidgets.QApplication.processEvents()
        try:
            client = build_client_from_profile(prof)
            # 发一个最简单的 ping
            resp = client.chat(
                messages=[
                    {'role': 'user', 'content': '回复一个字: ok'},
                ],
                stream=False,
                tools=None,
            )
            content = (resp.get('content') or '').strip()
            if content:
                self.test_label.setText(
                    '✓ 连接成功，模型回复: "{}"'.format(content[:40]),
                )
                self.test_label.setStyleSheet('color:#8fce8f;')
            else:
                self.test_label.setText('✓ 连接成功（响应为空）')
                self.test_label.setStyleSheet('color:#8fce8f;')
        except LLMError as exc:
            self.test_label.setText('✗ 连接失败: {}'.format(exc))
            self.test_label.setStyleSheet('color:#e57373;')
        except Exception as exc:  # pylint: disable=broad-except
            self.test_label.setText('✗ 异常: {}'.format(exc))
            self.test_label.setStyleSheet('color:#e57373;')

    def _test_connection_full(self):
        """复刻真实对话的请求形式：流式 + tools schema。

        用于诊断"测试连接通过但实际对话 401 / 400"这类网关/路由问题。
        """
        try:
            prof = self._read_form()
        except Exception as exc:  # pylint: disable=broad-except
            self.test_label.setText('✗ 表单错误: {}'.format(exc))
            self.test_label.setStyleSheet('color:#e57373;')
            return
        self.test_label.setText('⋯ 完整测试中（流式+tools）...')
        self.test_label.setStyleSheet('color:#888;')
        QtWidgets.QApplication.processEvents()

        # 尽量复刻 worker 的真实 payload：带 tools schema + 流式
        try:
            from ..tools import build_openai_tools_schema
            tools_schema = build_openai_tools_schema()
        except Exception as exc:  # pylint: disable=broad-except
            self.test_label.setText('✗ 加载工具 schema 失败: {}'.format(exc))
            self.test_label.setStyleSheet('color:#e57373;')
            return

        chunks = []

        def _on_delta(text):
            chunks.append(text)

        try:
            client = build_client_from_profile(prof)
            resp = client.chat(
                messages=[
                    {'role': 'system', 'content': 'You are a helpful assistant.'},
                    {'role': 'user', 'content': '回复一个字: ok'},
                ],
                tools=tools_schema,
                stream=True,
                on_delta=_on_delta,
            )
            content = (resp.get('content') or ''.join(chunks)).strip()
            if content:
                self.test_label.setText(
                    '✓ 完整测试通过，模型回复: "{}"'.format(content[:40]),
                )
                self.test_label.setStyleSheet('color:#8fce8f;')
            else:
                self.test_label.setText(
                    '✓ 完整测试通过（响应为空，但握手成功）',
                )
                self.test_label.setStyleSheet('color:#8fce8f;')
        except LLMError as exc:
            self.test_label.setText('✗ 完整测试失败: {}'.format(exc))
            self.test_label.setStyleSheet('color:#e57373;')
        except Exception as exc:  # pylint: disable=broad-except
            self.test_label.setText('✗ 异常: {}'.format(exc))
            self.test_label.setStyleSheet('color:#e57373;')

    def _refresh_base_url_hint(self, text):
        """Base URL 输入框内容变化时刷新静态体检提示。"""
        hint = diagnose_base_url(text)
        if hint:
            self.base_url_hint.setText(hint)
            self.base_url_hint.show()
        else:
            self.base_url_hint.clear()
            self.base_url_hint.hide()
