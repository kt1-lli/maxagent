#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导入 .maxagent-pack 时的预览对话框。

设计要点：
- 三栏分组（工具 / 技能 / 规则），每组显示 [复选框 | 名称 | 状态] 列表。
- 状态颜色编码：``new`` 绿色 / ``existing`` 橙色 / ``invalid`` 红色。
- 工具是可执行 .py 代码，对话框顶部展示警示横幅，提醒用户审视来源。
- 选中后点击"开始导入"，由调用方负责 ``pack.import_pack`` 调用并提示结果。
"""

from __future__ import absolute_import
from __future__ import print_function

from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ..logger import get_logger
from ..qt_compat import QtCore
from ..qt_compat import QtWidgets
from .emoji_compat import ee as _ee


logger = get_logger(__name__)


_STATUS_COLOR = {
    'new': '#8fce8f',
    'existing': '#ffb86c',
    'invalid': '#ff7575',
}
_STATUS_LABEL = {
    'new': '新增',
    'existing': '已存在（需勾选覆盖）',
    'invalid': '非法',
}


class PackImportDialog(QtWidgets.QDialog):
    """预览 + 选择要导入的资源；用户确认后通过 ``selection()`` 取结果。"""

    def __init__(self, parsed, pack_path, parent=None):
        # type: (Dict[str, Any], str, Optional[QtWidgets.QWidget]) -> None
        super(PackImportDialog, self).__init__(parent)
        self.setWindowTitle('导入 MaxAgent 资源包')
        self.resize(720, 540)

        self._parsed = parsed
        self._pack_path = pack_path
        self._tool_items = []  # type: List[QtWidgets.QListWidgetItem]
        self._skill_items = []  # type: List[QtWidgets.QListWidgetItem]
        self._rule_items = []  # type: List[QtWidgets.QListWidgetItem]
        self._accepted_selection = None  # type: Optional[Dict[str, Any]]

        self._build_ui()
        self._populate()

    # ------------------------------------------------------------------ #
    # UI
    # ------------------------------------------------------------------ #
    def _build_ui(self):
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(10)

        # 顶部：包元信息 + 安全提示
        header = QtWidgets.QLabel(self._format_header_html())
        header.setTextFormat(QtCore.Qt.TextFormat.RichText)
        header.setWordWrap(True)
        header.setStyleSheet(
            'QLabel { background:#2a2a2a; color:#e8e8e8;'
            ' border:1px solid #3a3a3a; padding:8px; border-radius:4px; }'
        )
        outer.addWidget(header)

        # 中间：三个分组
        body = QtWidgets.QHBoxLayout()
        body.setSpacing(10)
        self.tool_list = self._make_group_list(body, '🧰 自定义工具')
        self.skill_list = self._make_group_list(body, '🎓 技能')
        self.rule_list = self._make_group_list(body, '📋 自定义规则')
        outer.addLayout(body, 1)

        # 全选 / 反选 / 覆盖选项
        op_row = QtWidgets.QHBoxLayout()
        op_row.setSpacing(8)
        select_all_btn = QtWidgets.QPushButton('全选可导入')
        select_all_btn.clicked.connect(self._select_all_valid)
        op_row.addWidget(select_all_btn)
        clear_btn = QtWidgets.QPushButton('清空选择')
        clear_btn.clicked.connect(self._clear_selection)
        op_row.addWidget(clear_btn)
        op_row.addStretch(1)
        self.overwrite_chk = QtWidgets.QCheckBox(
            '同名时覆盖已有资源（默认跳过）',
        )
        op_row.addWidget(self.overwrite_chk)
        outer.addLayout(op_row)

        # 底部按钮
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch(1)
        self.cancel_btn = QtWidgets.QPushButton('取消')
        self.cancel_btn.setMinimumWidth(96)
        self.cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self.cancel_btn)
        self.import_btn = QtWidgets.QPushButton(_ee('✅') + ' 开始导入')
        self.import_btn.setMinimumWidth(120)
        self.import_btn.setStyleSheet(
            'QPushButton { background:#2d7d46; color:white;'
            ' border:1px solid #3a9c5a; padding:6px 12px; border-radius:3px; }'
            'QPushButton:hover { background:#3a9c5a; }'
        )
        self.import_btn.clicked.connect(self._on_accept)
        btn_row.addWidget(self.import_btn)
        outer.addLayout(btn_row)

    def _make_group_list(self, parent_layout, title):
        # type: (QtWidgets.QHBoxLayout, str) -> QtWidgets.QListWidget
        wrap = QtWidgets.QVBoxLayout()
        wrap.setSpacing(4)
        title_label = QtWidgets.QLabel(title)
        title_label.setStyleSheet(
            'QLabel { color:#ffd166; font-weight:bold; }'
        )
        wrap.addWidget(title_label)
        lst = QtWidgets.QListWidget()
        lst.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        wrap.addWidget(lst, 1)
        parent_layout.addLayout(wrap, 1)
        return lst

    def _format_header_html(self):
        # type: () -> str
        m = self._parsed.get('manifest') or {}
        name = (m.get('name') or '').strip() or '(未命名)'
        author = (m.get('author') or '').strip() or '(匿名)'
        ver = (m.get('exported_by') or '').strip() or 'unknown'
        when = (m.get('exported_at') or '').strip() or '?'
        desc = (m.get('description') or '').strip()
        n_tool = len(self._parsed.get('tools') or [])
        n_skill = len(self._parsed.get('skills') or [])
        n_rule = len(self._parsed.get('rules') or [])
        parts = [
            '<b style="color:#ffd166;">📦 {}</b>'.format(_html_escape(name)),
            '<span style="color:#aaa;">作者:</span> {}'.format(_html_escape(author)),
            '<span style="color:#aaa;">导出时间:</span> {}'.format(_html_escape(when)),
            '<span style="color:#aaa;">导出来源:</span> {}'.format(_html_escape(ver)),
        ]
        line1 = '&nbsp;&nbsp;|&nbsp;&nbsp;'.join(parts)
        line2 = (
            '包含: 工具 <b>{}</b> / 技能 <b>{}</b> / 规则 <b>{}</b>'.format(
                n_tool, n_skill, n_rule,
            )
        )
        warn = (
            '<span style="color:#ff9090;">⚠ 工具是可执行 Python 代码——'
            '只导入你<b>信任来源</b>的资源包，导入后会立即在 Max 内加载。</span>'
        )
        body = line1 + '<br>' + line2
        if desc:
            body += '<br><span style="color:#aaa;">说明:</span> ' + _html_escape(desc)
        body += '<br>' + warn
        return body

    # ------------------------------------------------------------------ #
    # 数据填充
    # ------------------------------------------------------------------ #
    def _populate(self):
        for entry in self._parsed.get('tools') or []:
            it = self._add_check_item(
                self.tool_list, entry['name'],
                entry['status'], entry.get('reason', ''),
            )
            it.setData(QtCore.Qt.UserRole, entry['name'])
            self._tool_items.append(it)
        for entry in self._parsed.get('skills') or []:
            it = self._add_check_item(
                self.skill_list, entry['name'],
                entry['status'], entry.get('reason', ''),
            )
            it.setData(QtCore.Qt.UserRole, entry['name'])
            self._skill_items.append(it)
        for entry in self._parsed.get('rules') or []:
            rid = entry['rule_id']
            data = entry.get('data') or {}
            label = rid
            title = (data.get('title') or '').strip()
            if title:
                label = '{}（{}）'.format(rid, title)
            it = self._add_check_item(
                self.rule_list, label,
                entry['status'], entry.get('reason', ''),
            )
            it.setData(QtCore.Qt.UserRole, rid)
            self._rule_items.append(it)

    def _add_check_item(self, list_widget, label, status, reason):
        # type: (QtWidgets.QListWidget, str, str, str) -> QtWidgets.QListWidgetItem
        color = _STATUS_COLOR.get(status, '#cccccc')
        status_text = _STATUS_LABEL.get(status, status)
        text = '{}  —  {}'.format(label, status_text)
        if reason:
            text += '  [{}]'.format(reason)
        it = QtWidgets.QListWidgetItem(text)
        if status == 'invalid':
            it.setFlags(it.flags() & ~QtCore.Qt.ItemIsEnabled)
            it.setCheckState(QtCore.Qt.Unchecked)
        else:
            it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
            it.setCheckState(
                QtCore.Qt.Checked if status == 'new'
                else QtCore.Qt.Unchecked,
            )
        try:
            from ..qt_compat import QtGui
            it.setForeground(QtGui.QBrush(QtGui.QColor(color)))
        except Exception:  # pylint: disable=broad-except
            pass
        it.setData(QtCore.Qt.UserRole + 1, status)
        list_widget.addItem(it)
        return it

    # ------------------------------------------------------------------ #
    # 操作
    # ------------------------------------------------------------------ #
    def _select_all_valid(self):
        for it in self._tool_items + self._skill_items + self._rule_items:
            status = it.data(QtCore.Qt.UserRole + 1)
            if status != 'invalid':
                it.setCheckState(QtCore.Qt.Checked)

    def _clear_selection(self):
        for it in self._tool_items + self._skill_items + self._rule_items:
            it.setCheckState(QtCore.Qt.Unchecked)

    def _collect_checked(self, items):
        # type: (List[QtWidgets.QListWidgetItem]) -> List[str]
        out = []
        for it in items:
            if it.checkState() == QtCore.Qt.Checked:
                out.append(it.data(QtCore.Qt.UserRole))
        return out

    def _on_accept(self):
        tools = self._collect_checked(self._tool_items)
        skills = self._collect_checked(self._skill_items)
        rules = self._collect_checked(self._rule_items)
        if not (tools or skills or rules):
            QtWidgets.QMessageBox.information(
                self, '未选择', '请至少勾选一项要导入的内容。',
            )
            return
        # 二次确认（工具是可执行代码）
        if tools:
            ret = QtWidgets.QMessageBox.warning(
                self, '确认导入工具',
                '即将导入 {} 个自定义工具（可执行 Python 代码）。'
                '请确认包来源可信，是否继续？'.format(len(tools)),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
            if ret != QtWidgets.QMessageBox.Yes:
                return
        self._accepted_selection = {
            'tools': tools,
            'skills': skills,
            'rules': rules,
            'overwrite': bool(self.overwrite_chk.isChecked()),
        }
        self.accept()

    def selection(self):
        # type: () -> Optional[Dict[str, Any]]
        """对话框 ``accept()`` 后调用方读取结果。"""
        return self._accepted_selection


def _html_escape(s):
    # type: (str) -> str
    if not s:
        return ''
    return (
        str(s)
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
    )


__all__ = ['PackImportDialog']
