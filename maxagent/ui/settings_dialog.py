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

        # 测试连接结果
        self.test_label = QtWidgets.QLabel('')
        self.test_label.setStyleSheet('color:#888;')
        right.addRow('', self.test_label)

        # 操作按钮
        op_row = QtWidgets.QHBoxLayout()
        op_row.addStretch(1)
        self.test_btn = QtWidgets.QPushButton('🧪 测试连接')
        self.test_btn.clicked.connect(self._test_connection)
        op_row.addWidget(self.test_btn)
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
        self.stream_chk.setChecked(bool(prof.stream))
        self.tools_chk.setChecked(bool(prof.use_tools))
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
                if self.max_tokens_spin.value() > 0 else None
            ),
            timeout=int(self.timeout_spin.value()),
            stream=bool(self.stream_chk.isChecked()),
            use_tools=bool(self.tools_chk.isChecked()),
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
            client = LLMClient(profile=prof)
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
