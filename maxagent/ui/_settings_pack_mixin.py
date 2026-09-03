#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SettingsDialog 的「工具与技能打包」子域 mixin。

从 ``settings_dialog.py`` 抽出的第 Page 6 页面，负责把用户的
自定义工具、技能、规则打包为 ``.maxagent-pack`` 文件，或反向导入。

该 mixin 依赖主类初始化时准备好的下列 Qt/协作对象：
- ``self.pack_tool_list`` / ``self.pack_skill_list`` / ``self.pack_rule_list``
- ``self.pack_name_edit`` / ``self.pack_author_edit`` / ``self.pack_desc_edit``

除此之外没有任何主类之外的依赖，可被 ``SettingsDialog`` 通过多继承
直接混入。抽取仅为按功能拆分文件，行为与原实现完全一致。
"""

from __future__ import absolute_import
from __future__ import print_function

from ..logger import get_logger
from ..qt_compat import QtCore
from ..qt_compat import QtWidgets
from .emoji_compat import ee as _ee


logger = get_logger(__name__)


class _SettingsPackMixin(object):
    """Page 6: 工具与技能（导入 / 导出 .maxagent-pack）。"""

    def _build_page_pack(self):
        # type: () -> QtWidgets.QWidget
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setSpacing(10)

        title = QtWidgets.QLabel(_ee('📦') + '  工具与技能')
        title.setStyleSheet('font-size:16px; font-weight:bold;')
        layout.addWidget(title)

        intro = QtWidgets.QLabel(
            '把<b>自定义工具</b>、<b>技能</b>、<b>自定义规则</b>打包为 '
            '<code>.maxagent-pack</code> 文件，用于跨电脑同步、'
            '团队分享或社区交换。<br>'
            '<span style="color:#ff9090;">⚠ 不会包含 API Key / Profile / '
            '会话历史</span>，避免泄露敏感信息。',
        )
        intro.setTextFormat(QtCore.Qt.TextFormat.RichText)
        intro.setWordWrap(True)
        intro.setStyleSheet(
            'QLabel { background:#2a2a2a; color:#e8e8e8;'
            ' border:1px solid #3a3a3a; padding:8px; border-radius:4px; }',
        )
        layout.addWidget(intro)

        # 三栏勾选 + 数量统计
        body = QtWidgets.QHBoxLayout()
        body.setSpacing(10)

        self.pack_tool_list = self._make_pack_export_list(body, '🧰 自定义工具')
        self.pack_skill_list = self._make_pack_export_list(body, '🎓 技能')
        self.pack_rule_list = self._make_pack_export_list(body, '📋 自定义规则')

        layout.addLayout(body, 1)

        # 操作按钮（每栏的全选已收敛到栏顶复选框，这里仅保留刷新与导入导出）
        op_row = QtWidgets.QHBoxLayout()
        op_row.setSpacing(8)
        refresh_btn = QtWidgets.QPushButton(_ee('🔄') + ' 刷新')
        refresh_btn.clicked.connect(self._reload_pack_lists)
        op_row.addWidget(refresh_btn)
        op_row.addStretch(1)
        export_btn = QtWidgets.QPushButton(_ee('📤') + ' 导出选中…')
        export_btn.setStyleSheet(
            'QPushButton { background:#2d7d46; color:white;'
            ' border:1px solid #3a9c5a; padding:6px 12px; border-radius:3px; }'
            'QPushButton:hover { background:#3a9c5a; }'
        )
        export_btn.clicked.connect(self._on_pack_export)
        op_row.addWidget(export_btn)
        import_btn = QtWidgets.QPushButton(_ee('📥') + ' 导入资源包…')
        import_btn.clicked.connect(self._on_pack_import)
        op_row.addWidget(import_btn)
        layout.addLayout(op_row)

        # 包元信息输入（可选）
        meta_box = QtWidgets.QGroupBox('包元信息（可选）')
        meta_form = QtWidgets.QFormLayout(meta_box)
        meta_form.setLabelAlignment(QtCore.Qt.AlignLeft)
        meta_form.setFormAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        meta_form.setFieldGrowthPolicy(
            QtWidgets.QFormLayout.ExpandingFieldsGrow,
        )
        self.pack_name_edit = QtWidgets.QLineEdit()
        self.pack_name_edit.setPlaceholderText('如 "我的渲染工作流 v1"')
        meta_form.addRow('包名:', self.pack_name_edit)
        self.pack_author_edit = QtWidgets.QLineEdit()
        self.pack_author_edit.setPlaceholderText('作者署名（可空）')
        meta_form.addRow('作者:', self.pack_author_edit)
        self.pack_desc_edit = QtWidgets.QLineEdit()
        self.pack_desc_edit.setPlaceholderText('一句话说明该包的用途（可空）')
        meta_form.addRow('描述:', self.pack_desc_edit)
        layout.addWidget(meta_box)

        # 首次加载
        self._reload_pack_lists()

        return page

    def _make_pack_export_list(self, parent_layout, title):
        # type: (QtWidgets.QHBoxLayout, str) -> QtWidgets.QListWidget
        wrap = QtWidgets.QVBoxLayout()
        wrap.setSpacing(4)
        # 顶部行：标题 + 独立全选复选框
        head_row = QtWidgets.QHBoxLayout()
        head_row.setSpacing(6)
        title_lbl = QtWidgets.QLabel(title)
        title_lbl.setStyleSheet(
            'QLabel { color:#ffd166; font-weight:bold; }'
        )
        head_row.addWidget(title_lbl)
        head_row.addStretch(1)
        all_chk = QtWidgets.QCheckBox('全选')
        all_chk.setTristate(False)
        all_chk.setStyleSheet('QCheckBox { color:#cccccc; }')
        head_row.addWidget(all_chk)
        wrap.addLayout(head_row)

        lst = QtWidgets.QListWidget()
        lst.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        wrap.addWidget(lst, 1)
        parent_layout.addLayout(wrap, 1)

        # 全选 ↔ 列表 双向同步
        all_chk.stateChanged.connect(
            lambda state, _lst=lst: self._pack_toggle_section(_lst, state),
        )
        lst.itemChanged.connect(
            lambda _it, _lst=lst, _chk=all_chk:
                self._pack_sync_section_header(_lst, _chk),
        )
        # 暴露给后续刷新使用，方便重置 header
        lst._pack_select_all_chk = all_chk  # type: ignore[attr-defined]
        return lst

    def _reload_pack_lists(self):
        """从磁盘扫描已有的工具 / 技能 / 规则，刷新三栏。"""
        try:
            from .. import user_tools_loader as utl
            tools = utl.list_user_tools(include_meta=True)
        except Exception as exc:  # pylint: disable=broad-except
            tools = []
            logger.warning('扫描自定义工具失败: %s', exc)
        try:
            from .. import skills as skills_mod
            skill_objs = skills_mod.SkillManager().list_skills()
        except Exception as exc:  # pylint: disable=broad-except
            skill_objs = []
            logger.warning('扫描技能失败: %s', exc)
        try:
            from .. import user_rules_loader as url_mod
            rules = url_mod.list_rules()
        except Exception as exc:  # pylint: disable=broad-except
            rules = []
            logger.warning('扫描自定义规则失败: %s', exc)

        self.pack_tool_list.blockSignals(True)
        try:
            self.pack_tool_list.clear()
            for item in tools:
                name = item['name']
                meta = item.get('meta') or {}
                desc = (meta.get('description') or '').strip()
                dcc = meta.get('dcc')
                dcc_tag = ' [通用]' if not dcc else ' [{}]'.format('/'.join(dcc))
                label = name + dcc_tag + ('  —  ' + desc if desc else '')
                it = QtWidgets.QListWidgetItem(label)
                it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
                it.setCheckState(QtCore.Qt.Unchecked)
                it.setData(QtCore.Qt.UserRole, name)
                self.pack_tool_list.addItem(it)
            if not tools:
                placeholder = QtWidgets.QListWidgetItem('（暂无自定义工具）')
                placeholder.setFlags(QtCore.Qt.NoItemFlags)
                self.pack_tool_list.addItem(placeholder)
        finally:
            self.pack_tool_list.blockSignals(False)

        self.pack_skill_list.blockSignals(True)
        try:
            self.pack_skill_list.clear()
            for sk in skill_objs:
                desc = (sk.description or '').strip().replace('\n', ' ')
                if len(desc) > 40:
                    desc = desc[:40] + '…'
                dcc_tag = sk.dcc_tag()
                label = '[{}] '.format(dcc_tag.strip('[]')) + sk.name + ('  —  ' + desc if desc else '')
                it = QtWidgets.QListWidgetItem(label)
                it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
                it.setCheckState(QtCore.Qt.Unchecked)
                it.setData(QtCore.Qt.UserRole, sk.name)
                self.pack_skill_list.addItem(it)
            if not skill_objs:
                placeholder = QtWidgets.QListWidgetItem('（暂无技能）')
                placeholder.setFlags(QtCore.Qt.NoItemFlags)
                self.pack_skill_list.addItem(placeholder)
        finally:
            self.pack_skill_list.blockSignals(False)

        self.pack_rule_list.blockSignals(True)
        try:
            self.pack_rule_list.clear()
            for r in rules:
                rid = r.get('id') or ''
                title_txt = (r.get('title') or '').strip()
                label = rid + ('  —  ' + title_txt if title_txt else '')
                it = QtWidgets.QListWidgetItem(label)
                it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
                it.setCheckState(QtCore.Qt.Unchecked)
                it.setData(QtCore.Qt.UserRole, rid)
                self.pack_rule_list.addItem(it)
            if not rules:
                placeholder = QtWidgets.QListWidgetItem('（暂无自定义规则）')
                placeholder.setFlags(QtCore.Qt.NoItemFlags)
                self.pack_rule_list.addItem(placeholder)
        finally:
            self.pack_rule_list.blockSignals(False)

        # 刷新后所有项均为未勾选 → 栏顶全选复选框也复位
        for lst in (self.pack_tool_list, self.pack_skill_list,
                    self.pack_rule_list):
            chk = getattr(lst, '_pack_select_all_chk', None)
            if chk is not None:
                self._pack_sync_section_header(lst, chk)

    def _pack_toggle_section(self, lst, state):
        # type: (QtWidgets.QListWidget, int) -> None
        """栏顶'全选'复选框切换 → 同步该栏所有可勾选项。"""
        # 兼容 PySide2(int) / PySide6(CheckState 或 int) 两种回调签名
        try:
            state_int = int(state)
        except (TypeError, ValueError):
            state_int = 2 if state == QtCore.Qt.Checked else 0
        target = (QtCore.Qt.Checked if state_int == 2
                  else QtCore.Qt.Unchecked)
        lst.blockSignals(True)
        try:
            for i in range(lst.count()):
                it = lst.item(i)
                if not (it.flags() & QtCore.Qt.ItemIsUserCheckable):
                    continue
                it.setCheckState(target)
        finally:
            lst.blockSignals(False)
        logger.info(
            'pack 栏全选切换: %s → %s',
            getattr(lst, 'objectName', lambda: '')() or 'list',
            'all' if target == QtCore.Qt.Checked else 'none',
        )

    def _pack_sync_section_header(self, lst, header_chk):
        # type: (QtWidgets.QListWidget, QtWidgets.QCheckBox) -> None
        """列表项勾选变化 → 反向同步栏顶'全选'复选框状态。"""
        total = 0
        checked = 0
        for i in range(lst.count()):
            it = lst.item(i)
            if not (it.flags() & QtCore.Qt.ItemIsUserCheckable):
                continue
            total += 1
            if it.checkState() == QtCore.Qt.Checked:
                checked += 1
        # 阻断回环：栏顶 checkbox 状态切换不应再触发 _pack_toggle_section
        header_chk.blockSignals(True)
        try:
            if total == 0:
                header_chk.setChecked(False)
            elif checked == total:
                header_chk.setChecked(True)
            else:
                header_chk.setChecked(False)
        finally:
            header_chk.blockSignals(False)

    def _pack_select_all(self):
        """[兼容入口] 把三栏的'全选'复选框全部勾上。

        新 UI 已把全选收敛到每栏顶部独立的复选框，但保留此方法供
        老测试 / 外部脚本继续调用，避免破坏向后兼容。
        """
        for lst in (self.pack_tool_list, self.pack_skill_list,
                    self.pack_rule_list):
            chk = getattr(lst, '_pack_select_all_chk', None)
            if chk is not None:
                chk.setChecked(True)
            else:
                # 极端兜底：栏顶 checkbox 不存在时直接遍历
                self._pack_toggle_section(lst, QtCore.Qt.Checked)

    def _pack_clear(self):
        """[兼容入口] 清空三栏的所有勾选。"""
        for lst in (self.pack_tool_list, self.pack_skill_list,
                    self.pack_rule_list):
            chk = getattr(lst, '_pack_select_all_chk', None)
            if chk is not None:
                chk.setChecked(False)
            else:
                self._pack_toggle_section(lst, QtCore.Qt.Unchecked)

    @staticmethod
    def _collect_checked_data(lst):
        # type: (QtWidgets.QListWidget) -> list
        out = []
        for i in range(lst.count()):
            it = lst.item(i)
            if not (it.flags() & QtCore.Qt.ItemIsUserCheckable):
                continue
            if it.checkState() == QtCore.Qt.Checked:
                out.append(it.data(QtCore.Qt.UserRole))
        return out

    def _on_pack_export(self):
        tools = self._collect_checked_data(self.pack_tool_list)
        skills = self._collect_checked_data(self.pack_skill_list)
        rules = self._collect_checked_data(self.pack_rule_list)
        if not (tools or skills or rules):
            QtWidgets.QMessageBox.information(
                self, '未选择',
                '请先在三个列表里勾选要导出的资源（至少一个）。',
            )
            return
        # 选保存路径
        from .. import pack as pack_mod
        suggested = (self.pack_name_edit.text().strip()
                     or 'maxagent_export') + pack_mod.PACK_SUFFIX
        path, _filter = QtWidgets.QFileDialog.getSaveFileName(
            self, '保存资源包', suggested,
            'MaxAgent Pack (*{})'.format(pack_mod.PACK_SUFFIX),
        )
        if not path:
            return
        if not path.endswith(pack_mod.PACK_SUFFIX):
            path += pack_mod.PACK_SUFFIX
        try:
            res = pack_mod.export_pack(
                output_path=path,
                tool_names=tools,
                skill_names=skills,
                rule_ids=rules,
                pack_name=self.pack_name_edit.text().strip(),
                description=self.pack_desc_edit.text().strip(),
                author=self.pack_author_edit.text().strip(),
            )
        except pack_mod.PackError as exc:
            QtWidgets.QMessageBox.warning(self, '导出失败', str(exc))
            return
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception('导出资源包失败')
            QtWidgets.QMessageBox.critical(self, '导出异常', str(exc))
            return
        size_kb = max(1, res['size'] // 1024)
        QtWidgets.QMessageBox.information(
            self, '导出成功',
            '已写入: {}\n\n'
            '工具 {} 个 / 技能 {} 个 / 规则 {} 个\n大小: {} KB'.format(
                res['path'],
                len(res['tools']),
                len(res['skills']),
                len(res['rules']),
                size_kb,
            ),
        )

    def _on_pack_import(self):
        from .. import pack as pack_mod
        path, _filter = QtWidgets.QFileDialog.getOpenFileName(
            self, '选择资源包', '',
            'MaxAgent Pack (*{} *.zip)'.format(pack_mod.PACK_SUFFIX),
        )
        if not path:
            return
        try:
            parsed = pack_mod.parse_pack(path)
        except pack_mod.PackError as exc:
            QtWidgets.QMessageBox.warning(self, '解析失败', str(exc))
            return
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception('解析资源包失败')
            QtWidgets.QMessageBox.critical(self, '解析异常', str(exc))
            return

        from .pack_dialog import PackImportDialog
        dlg = PackImportDialog(parsed, path, parent=self)
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return
        sel = dlg.selection() or {}
        try:
            summary = pack_mod.import_pack(
                pack_path=path,
                selected_tools=sel.get('tools'),
                selected_skills=sel.get('skills'),
                selected_rules=sel.get('rules'),
                overwrite=bool(sel.get('overwrite', False)),
            )
        except pack_mod.PackError as exc:
            QtWidgets.QMessageBox.warning(self, '导入失败', str(exc))
            return
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception('导入资源包失败')
            QtWidgets.QMessageBox.critical(self, '导入异常', str(exc))
            return

        # 汇总结果
        def _fmt(group):
            parts = []
            for k in ('imported', 'overwritten', 'skipped'):
                v = group.get(k) or []
                if v:
                    parts.append('{}={}'.format(k, len(v)))
            errs = group.get('errors') or []
            if errs:
                parts.append('错误={}'.format(len(errs)))
            return '、'.join(parts) or '无'

        msg = (
            '工具: {}\n技能: {}\n规则: {}'.format(
                _fmt(summary['tools']),
                _fmt(summary['skills']),
                _fmt(summary['rules']),
            )
        )
        # 列出错误细节（前 5 条）
        all_errs = (
            summary['tools'].get('errors', [])
            + summary['skills'].get('errors', [])
            + summary['rules'].get('errors', [])
        )
        if all_errs:
            msg += '\n\n错误详情（前 5 条）:'
            for e in all_errs[:5]:
                key = e.get('name') or e.get('rule_id') or '?'
                msg += '\n· {}: {}'.format(key, e.get('reason', ''))
        QtWidgets.QMessageBox.information(self, '导入完成', msg)
        self._reload_pack_lists()
