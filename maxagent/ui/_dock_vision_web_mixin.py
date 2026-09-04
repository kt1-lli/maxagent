#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MaxAgentDockWidget 的「Vision Hint / Web 联网 / 示例填充」子域 mixin。

从 ``dock_widget.py`` 抽出的一组围绕：
- 视觉能力提示条（``self.vision_hint`` + 附件/白名单/profile 三态联动）
- 顶部 🌐 联网按钮（``self.web_btn`` 的 auto / force / off 三态）
- 示例气泡填入输入框（``_on_example_picked``）

这些方法之间只通过 ``self._config`` / ``self.attachment_strip`` /
``self.vision_hint`` / ``self.web_btn`` / ``self.status_label`` /
``self.profile_combo`` / ``self.input_edit`` 交互，与主发送流程仅通过
``_should_use_web_this_turn`` 单点耦合，可以独立成 mixin。

抽取仅按功能拆分文件，行为与原实现一致。
"""

from __future__ import absolute_import
from __future__ import print_function

from ..logger import get_logger
from .emoji_compat import ee as _ee
from ..attachments import model_supports_vision


logger = get_logger(__name__)


class _VisionWebMixin(object):
    """Vision 提示条 / Web 按钮 / 示例填充。"""

    # ------------------------------------------------------------------ #
    # 示例气泡 → 输入框
    # ------------------------------------------------------------------ #

    def _on_example_picked(self, text):
        # 把示例文本填入输入框，让用户可以编辑后再发
        self.input_edit.setPlainText(text)
        self.input_edit.setFocus()

    # ------------------------------------------------------------------ #
    # 🌐 联网按钮
    # ------------------------------------------------------------------ #

    def refresh_web_button_state(self):
        """根据全局 ``web_search_mode`` 同步 🌐 按钮显示与可点击性。

        在以下时机调用：
        1. 主 UI 初始化（_build_ui 末尾）
        2. 设置面板 OK 后（SettingsDialog 主动回调本方法）
        3. 重新加载配置后（reload）
        """
        if not hasattr(self, 'web_btn'):
            return
        cfg = self._config.config
        mode = str(getattr(cfg, 'web_search_mode', 'auto') or 'auto').lower()
        backend = str(
            getattr(cfg, 'web_search_backend', 'duckduckgo') or 'duckduckgo',
        ).lower()

        # 解析当前激活 provider，获取展示名 + 是否真正可用
        active_name = ''
        provider_usable = True
        try:
            from ..web_providers import ProviderRegistry
            reg = ProviderRegistry()
            mapped = reg.get(backend) if backend != 'disabled' else None
            if mapped is None:
                mapped = reg.get_active()
            if mapped is not None:
                active_name = mapped.get('name') or mapped.get('id') or ''
                provider_usable = bool(mapped.get('enabled', True))
        except Exception:  # pylint: disable=broad-except
            provider_usable = (backend != 'disabled')

        # 后端为 disabled 或 provider 已禁用都视同 mode=off
        effective_off = (
            mode == 'off' or backend == 'disabled' or not provider_usable
        )
        # 阻塞 toggle 信号避免触发副作用
        self.web_btn.blockSignals(True)
        if effective_off:
            self.web_btn.setEnabled(False)
            self.web_btn.setChecked(False)
            self.web_btn.setToolTip('联网已被全局关闭（设置 → 联网）')
        elif mode == 'force':
            self.web_btn.setEnabled(False)
            self.web_btn.setChecked(True)
            self.web_btn.setToolTip(
                '联网为强制开启（设置 → 联网）；本按钮不可关闭\n'
                '当前后端：{}'.format(active_name or backend),
            )
        else:  # auto
            self.web_btn.setEnabled(True)
            self.web_btn.setToolTip(
                '本轮对话允许 LLM 联网搜索\n'
                '当前后端：{}\n'
                '点击切换：亮起=本轮联网；熄灭=本轮关闭'.format(
                    active_name or backend,
                ),
            )
        self.web_btn.blockSignals(False)

    def _on_web_btn_toggled(self, checked):
        """用户点击 🌐 切换本轮联网开关——仅 auto 模式下生效。

        force / off 模式下按钮被 setEnabled(False) 拦住，不会进入这里。
        """
        try:
            self.status_label.setText(
                (_ee('🌐') + ' 本轮联网：开启') if checked
                else (_ee('🌐') + ' 本轮联网：关闭'),
            )
        except Exception:  # pylint: disable=broad-except
            pass

    def _should_use_web_this_turn(self):
        """决策本轮是否暴露 web_* 工具。

        :returns: True 表示允许 LLM 调用 web_search / web_fetch
        """
        cfg = self._config.config
        mode = str(getattr(cfg, 'web_search_mode', 'auto') or 'auto').lower()
        backend = str(
            getattr(cfg, 'web_search_backend', 'duckduckgo') or 'duckduckgo',
        ).lower()
        if mode == 'off' or backend == 'disabled':
            return False
        if mode == 'force':
            return True
        # auto 模式：看按钮当前 checked 状态
        try:
            return bool(self.web_btn.isChecked())
        except Exception:  # pylint: disable=broad-except
            return False

    # ------------------------------------------------------------------ #
    # 视觉能力提示条
    # ------------------------------------------------------------------ #

    def _refresh_vision_hint(self):
        """根据当前 profile + 视觉开关 + 是否有附件，刷新提示条显隐。

        触发点：附件增删（``AttachmentStrip.changed``）、profile 切换、
        设置对话框保存后。提示条本身只决定文案/可见性，不阻断发送——
        让 LLM 端拿到"[图片] N 张"占位提示，与现有降级行为保持一致。
        """
        try:
            cfg = self._config.config
            has_atts = bool(self.attachment_strip.attachments())
            vision_on = bool(getattr(cfg, 'vision_enabled', True))
            whitelist = list(getattr(cfg, 'vision_model_whitelist', []))
            prof = ((getattr(self._config, "resolve_active_llm", lambda: None)() or self._config.get_active_profile()))
            model_name = ''
            prof_vision_supported = False
            if prof is not None:
                model_name = getattr(prof, 'model', '') or ''
                prof_vision_supported = bool(
                    getattr(prof, 'vision_supported', False)
                )
            supported = (
                model_supports_vision(model_name, whitelist)
                and prof_vision_supported
            )
            self.vision_hint.set_state(
                has_attachments=has_atts,
                vision_enabled=vision_on,
                vision_supported=supported,
                model_name=model_name,
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.debug('refresh_vision_hint 异常: %s', exc)

    def _on_vision_hint_switch_profile(self):
        """提示条上"切换模型"按钮：把焦点交给顶部 profile 下拉。"""
        try:
            self.profile_combo.setFocus()
            # 直接展开下拉，让用户一眼看到所有候选
            self.profile_combo.showPopup()
        except Exception:  # pylint: disable=broad-except
            pass
