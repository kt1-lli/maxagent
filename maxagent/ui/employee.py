#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""员工档案（Employee）：纯 UI 层的助手形象皮肤。

设计模型：
- 岗位（MaxAgent）：写死在 system prompt，不可改。代表"3ds Max
  智能助手"的职责与身份铁律，越狱防护由此保障。
- 员工（Employee）：用户自定义的对外形象——名字 + 头像，仅影响
  对话气泡的视觉表达。LLM 对此一无所知。

类比：MaxAgent 是"前台"岗位，员工是"上任的人"——名字和长相
随员工换，但岗位职责不变。

存储：
- 名字 / 头像类型 / emoji / image 文件名 → ``config.json``
- 头像图片本体 → ``{config_dir}/avatar.png``（PNG, 64×64）

API：
- ``Employee.from_config(cfg_mgr)`` → 加载当前配置
- ``employee.display_html(title_color)`` → 生成气泡头部 HTML
- ``employee.avatar_image_path()`` → 当前头像图片绝对路径
- ``employee.save(cfg_mgr)`` → 持久化到配置
- ``save_avatar_image(cfg_mgr, qpixmap)`` → 写盘 64×64 PNG
"""

from __future__ import absolute_import
from __future__ import print_function

import os
from typing import Any
from typing import Optional

from ..config import get_config_dir
from .emoji_compat import ee as _ee


# 头像图片固定文件名，多次上传覆盖写
AVATAR_FILENAME = 'avatar.png'

# 头像在气泡里的渲染尺寸（像素）；存盘尺寸是 2× 用于高分屏
AVATAR_DISPLAY_SIZE = 18
AVATAR_STORE_SIZE = 64

# 默认值
DEFAULT_NAME = '助手'
DEFAULT_EMOJI = '🤖'

# 推荐 emoji 快选（员工 Tab 用）
SUGGESTED_EMOJIS = [
    '🤖', '🐱', '🦊', '🐧', '🦄',
    '🔮', '🎨', '🌟', '⚡',
]


def get_avatar_image_full_path():
    # type: () -> str
    """返回头像图片在磁盘上的绝对路径（无论是否存在）。"""
    return os.path.join(get_config_dir(), AVATAR_FILENAME)


class Employee(object):
    """员工档案的内存视图。

    :param name: 员工名（显示在气泡头部）
    :param avatar_kind: ``'emoji'`` 或 ``'image'``
    :param avatar_emoji: emoji 模式下使用的字符
    :param avatar_image: image 模式下的相对文件名
    """

    def __init__(
        self,
        name=DEFAULT_NAME,
        avatar_kind='emoji',
        avatar_emoji=DEFAULT_EMOJI,
        avatar_image='',
    ):
        # type: (str, str, str, str) -> None
        self.name = (name or DEFAULT_NAME).strip() or DEFAULT_NAME
        self.avatar_kind = (
            avatar_kind if avatar_kind in ('emoji', 'image') else 'emoji'
        )
        self.avatar_emoji = avatar_emoji or DEFAULT_EMOJI
        self.avatar_image = avatar_image or ''

    # ------------------------------------------------------------------ #
    # 工厂方法
    # ------------------------------------------------------------------ #
    @classmethod
    def from_config(cls, cfg_mgr):
        # type: (Any) -> Employee
        """从 ConfigManager 加载员工档案。

        ``cfg_mgr`` 为 None 或缺字段时回落到默认值，永不抛异常。
        """
        if cfg_mgr is None:
            return cls()
        try:
            cfg = cfg_mgr.config
        except AttributeError:
            return cls()
        return cls(
            name=getattr(cfg, 'employee_name', DEFAULT_NAME),
            avatar_kind=getattr(cfg, 'employee_avatar_kind', 'emoji'),
            avatar_emoji=getattr(cfg, 'employee_avatar_emoji', DEFAULT_EMOJI),
            avatar_image=getattr(cfg, 'employee_avatar_image', ''),
        )

    # ------------------------------------------------------------------ #
    # 持久化
    # ------------------------------------------------------------------ #
    def save(self, cfg_mgr):
        # type: (Any) -> None
        """把当前员工档案写回 ConfigManager 并落盘。"""
        if cfg_mgr is None:
            return
        cfg = cfg_mgr.config
        cfg.employee_name = self.name
        cfg.employee_avatar_kind = self.avatar_kind
        cfg.employee_avatar_emoji = self.avatar_emoji
        cfg.employee_avatar_image = self.avatar_image
        cfg_mgr.save()

    # ------------------------------------------------------------------ #
    # 头像图片路径
    # ------------------------------------------------------------------ #
    def avatar_image_path(self):
        # type: () -> str
        """返回当前头像图片的绝对路径（仅当 kind=image 且文件存在）。

        ``kind=image`` 但文件丢失时返回空串，调用方应回落 emoji。
        """
        if self.avatar_kind != 'image' or not self.avatar_image:
            return ''
        path = os.path.join(get_config_dir(), self.avatar_image)
        return path if os.path.exists(path) else ''

    # ------------------------------------------------------------------ #
    # HTML 渲染
    # ------------------------------------------------------------------ #
    def display_html(self, title_color='#a8e6a8', font_size_pt=9):
        # type: (str, int) -> str
        """生成对话气泡头部的 HTML 片段。

        包含头像（emoji 文本 / 图片 <img>）+ 员工名。

        :param title_color: 名字颜色，沿用现有气泡的 ``#a8e6a8``
        :param font_size_pt: 字号（pt）
        :returns: 可直接放进 ``QLabel.setText`` 的 HTML 字符串
        """
        # 名字部分必须 HTML 转义，避免用户输入 ``<script>`` 等
        safe_name = _html_escape(self.name)
        if self.avatar_kind == 'image':
            img_path = self.avatar_image_path()
            if img_path:
                # Qt 的富文本支持 file:/// 协议 + 路径分隔符做 ``/`` 处理
                url = 'file:///' + img_path.replace('\\', '/')
                return (
                    '<img src="{url}" width="{w}" height="{w}" '
                    'style="vertical-align:middle;"> '
                    '<span style="color:{color};font-size:{sz}pt;">'
                    '{name}</span>'
                ).format(
                    url=url,
                    w=AVATAR_DISPLAY_SIZE,
                    color=title_color,
                    sz=font_size_pt,
                    name=safe_name,
                )
            # 图片丢失：自动回落到 emoji（不弹错，体验顺滑）
        # emoji 模式（默认 + image 模式但文件丢失的兜底）
        avatar = _ee(self.avatar_emoji or DEFAULT_EMOJI)
        return (
            '<span style="color:{color};font-size:{sz}pt;">'
            '{avatar} {name}</span>'
        ).format(
            color=title_color,
            sz=font_size_pt,
            avatar=avatar,
            name=safe_name,
        )


def _html_escape(text):
    # type: (str) -> str
    """最小 HTML 转义，避免员工名里写 ``<script>`` 等被当标签解析。"""
    if not text:
        return ''
    return (
        text
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('"', '&quot;')
    )


# 公开别名：dock_widget 等同包外部模块通过这个名字调用，
# 避免 import 私有函数（带下划线前缀）触发 lint 告警。
escape_name = _html_escape


def save_avatar_image(qpixmap, target_size=AVATAR_STORE_SIZE):
    # type: (Any, int) -> Optional[str]
    """把 QPixmap 缩放到 ``target_size`` 并写入 ``avatar.png``。

    :param qpixmap: 已经被裁剪的方形 QPixmap（任意尺寸）
    :param target_size: 输出边长（像素），默认 64
    :returns: 写盘成功时返回相对文件名 ``avatar.png``，失败返回 None
    """
    if qpixmap is None or qpixmap.isNull():
        return None
    # 延迟导入 Qt：保持模块在无 Qt 环境（纯 pytest）下可被 import
    from ..qt_compat import QtCore
    scaled = qpixmap.scaled(
        target_size, target_size,
        QtCore.Qt.IgnoreAspectRatio,
        QtCore.Qt.SmoothTransformation,
    )
    full_path = get_avatar_image_full_path()
    ok = scaled.save(full_path, 'PNG')
    return AVATAR_FILENAME if ok else None


def remove_avatar_image():
    # type: () -> bool
    """删除磁盘上的头像图片文件。返回是否真正删除了文件。"""
    path = get_avatar_image_full_path()
    try:
        if os.path.exists(path):
            os.remove(path)
            return True
    except OSError:
        pass
    return False
