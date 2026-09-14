#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SVG 图标加载器（Bootstrap Icons 素材，MIT 许可）。

为什么需要这个文件？
====================
PySide2 (Max 2022~2024) 上 emoji 存在字体回退缺陷（见
:mod:`maxagent.ui.emoji_compat`），此前用 BMP 字符兜底，但视觉
质量有限。Max 内嵌 Qt 环境实测（2026-09-11 探针）确认：

- PySide2 (Qt 5.15.1) 与 PySide6 (Qt 6.5.3) 的 ``QtSvg`` 模块均可用；
- ``qsvg`` 图像格式插件齐全（甚至支持 ``QIcon('x.svg')`` 直连）。

因此以 SVG 单色线性图标作为首选视觉方案，emoji/BMP 文本降级为
加载失败时的兜底，三层方案共存互不冲突。

设计要点
========
- 只依赖 ``QtSvg.QSvgRenderer`` 手动渲染成 QPixmap，不依赖 QIcon
  直连，规避插件路径的不确定性；
- 绑定选择严格跟随 :mod:`maxagent.qt_compat` 已解析的结果
  （IS_PYSIDE6 / IS_PYSIDE2），绝不引入第二套 Qt 绑定；
- Bootstrap Icons 使用 ``fill="currentColor"``，Qt 渲染器对根节点
  继承支持不可靠，故渲染前把 fill 显式注入每个图形元素（见
  :func:`_apply_fill`），既适配明暗主题，也规避颜色丢失；
- 以 2x 尺寸渲染并设置 DevicePixelRatio，保证高分屏下清晰；
- 任何失败（文件缺失 / 模块缺失 / 渲染失败）一律返回 None，
  由调用方走 ``btn_label`` 文本兜底，图标属于纯装饰不影响功能；
- 进程级缓存渲染结果，同一图标多处使用零重复开销；
  ``clear_cache()`` 供热重载场景使用。

用法示例::

    from maxagent.ui.icon_loader import set_btn_icon

    btn = QtWidgets.QPushButton(_btn_label('📌', '立即重新停靠'))
    set_btn_icon(btn, 'pin', '立即重新停靠')
    # 成功：按钮变为 SVG 图标 + 纯文本
    # 失败：按钮保持 btn_label 生成的 emoji/BMP 文本兜底
"""

from __future__ import absolute_import
from __future__ import print_function

import os
import re
import uuid
from typing import Optional
from typing import Tuple

from ..qt_compat import QtGui
from ..qt_compat import IS_PYSIDE6
from ..qt_compat import IS_PYSIDE2

# 日志延迟导入：icon_loader 可能先于 logger 初始化被 import，
# 不能在模块加载期触发日志系统初始化
_logger = None  # type: Optional[object]


def _get_logger():
    # type: () -> object
    """按需获取 maxagent.ui.icon_loader 的 logger（惰性单例）。"""
    global _logger
    if _logger is None:
        try:
            from ..logger import get_logger
            _logger = get_logger(__name__)
        except Exception:  # pylint: disable=broad-except
            # 日志系统不可用时退化为空操作，绝不能影响图标加载主流程
            class _NullLogger(object):
                """静默空日志：吸收所有级别调用。"""

                def _noop(self, *args, **kwargs):
                    """吞掉任意日志调用参数。"""

                debug = _noop
                info = _noop
                warning = _noop
                error = _noop
            _logger = _NullLogger()
    return _logger


# 图标资源目录：maxagent/ui/icons/
# 用 __file__ 推导绝对路径，Max 启动 CWD 不定时依然可靠
_ICONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icons')

# 默认图标颜色：深色 UI 下的浅灰，保证在 Max/Maya 深色主题上可见
DEFAULT_ICON_COLOR = '#e0e0e0'

# 图标语义色映射表（图标名 -> CSS 颜色）。
# 按钮类图标原先靠 emoji 自带颜色（🔌💾🔄 等），换成线性 SVG 后
# 若全部落在默认灰，视觉上等于"没有颜色"；这里按语义给常用图标
# 自动配色，调用点不传 color 时优先查表。
ICON_SEMANTIC_COLORS = {
    # 操作结果 / 确认类：绿
    'success': '#8fce8f',
    'confirm': '#8fce8f',
    'checkbox_on': '#8fce8f',
    'checkbox_off': '#888888',
    # 危险 / 失败 / 停止类：红
    'fail': '#e57373',
    'close': '#e57373',
    'delete': '#e57373',
    'trash': '#e57373',
    'stop': '#e57373',
    # 连接 / 测试 / 保存 / 发送 / 会话类：蓝
    'plug': '#6fb1ff',
    'save': '#6fb1ff',
    'edit': '#6fb1ff',
    'send': '#6fb1ff',
    'chat': '#6fb1ff',
    'globe': '#6fb1ff',
    # 刷新 / 重置 / 拉取类：青
    'refresh': '#5bc8d5',
    'refresh2': '#5bc8d5',
    'download': '#5bc8d5',
    'export': '#5bc8d5',
    'import': '#5bc8d5',
    # 收藏 / 星标 / 设置类：黄
    'star': '#e0c26a',
    'pin': '#e0c26a',
    'tool': '#e0c26a',
    'gear': '#e0c26a',
    # 状态标记 / 切换类：橙
    'tag': '#f0a35e',
    # 查看 / 信息类：紫
    'eye': '#b39ddb',
    'hide': '#b39ddb',
    # 设置导航专属图标
    'robot': '#b39ddb',
    'palette': '#f48fb1',
    'person': '#f0a35e',
    'box': '#a1887f',
    'toolbox': '#90a4ae',
    'journal-text': '#5bc8d5',
    'question-circle': '#e0c26a',
}

# 页面标题图标渲染失败时的 BMP 兜底字符（不依赖富文本）
_TITLE_FALLBACK_GLYPHS = {
    'robot': '?',
    'globe': '@',
    'palette': '*',
    'person': '@',
    'box': '#',
    'toolbox': '#',
    'plug': '=',
    'journal-text': '=',
    'question-circle': '?',
    'tag': '#',
    'star': '*',
    'tool': '#',
    'export': '>',
}

# 渲染成功的缓存：键为 (图标名, 颜色)，值为 QIcon
_ICON_CACHE = {}  # type: dict

# 渲染失败的图标名集合：记录后不再重复尝试 IO 与解析
_MISSING_ICONS = set()  # type: set


def icons_dir():
    # type: () -> str
    """返回图标资源目录的绝对路径。"""
    return _ICONS_DIR


def clear_cache():
    # type: () -> None
    """清空渲染缓存（热重载 / 换肤场景使用）。"""
    _ICON_CACHE.clear()
    _MISSING_ICONS.clear()


def load_icon(name, color=None, size=16):
    # type: (str, Optional[str], int) -> Optional[object]
    """按名称加载 SVG 图标，渲染为 QIcon。

    :param name: 图标名，对应 ``icons/<name>.svg``，如 ``'refresh'``
    :param color: 图标颜色（CSS 颜色串）；不传用 :data:`DEFAULT_ICON_COLOR`
    :param size: 逻辑尺寸（px），实际按 2x 渲染保证 HiDPI 清晰
    :returns: QIcon；任何一步失败返回 None（调用方走文本兜底）
    """
    fill = color or DEFAULT_ICON_COLOR
    cache_key = (name, fill)  # type: Tuple[str, str]
    if cache_key in _ICON_CACHE:
        return _ICON_CACHE[cache_key]
    if name in _MISSING_ICONS:
        _get_logger().debug('icon [%s] 此前已失败，继续走文本兜底', name)
        return None
    path = os.path.join(_ICONS_DIR, name + '.svg')
    if not os.path.isfile(path):
        _get_logger().warning(
            'icon [%s] SVG 文件不存在: %s（走 emoji/文本兜底）', name, path,
        )
        _MISSING_ICONS.add(name)
        return None
    icon = _render_svg_file(path, fill, size)
    if icon is None:
        _get_logger().warning(
            'icon [%s] SVG 渲染失败（QtSvg 模块缺失或 renderer 无效，走兜底）',
            name,
        )
        _MISSING_ICONS.add(name)
        return None
    _ICON_CACHE[cache_key] = icon
    _get_logger().debug('icon [%s] SVG 加载成功 (fill=%s)', name, fill)
    return icon


def icon_pixmap(name, color=None, size=16):
    # type: (str, Optional[str], int) -> Optional[object]
    """按名称渲染 SVG 图标，返回 QPixmap（供 QLabel/paint 场景使用）。

    :param name: 图标名，对应 ``icons/<name>.svg``
    :param color: 图标颜色；不传用 :data:`DEFAULT_ICON_COLOR`
    :param size: 逻辑尺寸（px），实际按 2x 渲染保证 HiDPI 清晰
    :returns: QPixmap；失败返回 None
    """
    fill = color or DEFAULT_ICON_COLOR
    cache_key = ('pixmap', name, fill, size)  # type: Tuple[str, str, str, int]
    if cache_key in _ICON_CACHE:
        return _ICON_CACHE[cache_key]
    path = os.path.join(_ICONS_DIR, name + '.svg')
    if not os.path.isfile(path):
        _MISSING_ICONS.add(name)
        return None
    pixmap = _render_pixmap(path, fill, size)
    if pixmap is None:
        _MISSING_ICONS.add(name)
        return None
    _ICON_CACHE[cache_key] = pixmap
    return pixmap


def make_page_title(name, text, color=None, size=18):
    # type: (str, str, Optional[str], int) -> object
    """构建「图标 + 标题文字」的组合控件，用于页面大标题。

    挂了样式表的 QLabel 会禁用富文本渲染，``<img>`` 直接不显示
    （badcase：帮助页标题只剩"使用帮助"或整体空白），因此标题类
    场景不走 ``rich_icon`` HTML 路线，改用 QPixmap + QLabel 组合，
    任何 Qt 环境下行为一致。

    :param name: 图标名
    :param text: 标题文本
    :param color: 图标颜色；不传用页面标题色 #4fc3f7
    :param size: 图标逻辑尺寸（px）
    :returns: 装好图和文字的 QWidget
    """
    from ..qt_compat import QtCore, QtWidgets
    wrap = QtWidgets.QWidget()
    row = QtWidgets.QHBoxLayout(wrap)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(7)
    pixmap = icon_pixmap(name, color or '#4fc3f7', size)
    icon_label = QtWidgets.QLabel()
    if pixmap is not None:
        icon_label.setPixmap(pixmap)
        icon_label.setFixedSize(size + 4, size + 4)
        icon_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
    else:
        # 兜底：图标渲染失败时显示 BMP 字符（不依赖富文本）
        icon_label.setText(_TITLE_FALLBACK_GLYPHS.get(name, ''))
    icon_label.setStyleSheet('font-size:14px;')
    row.addWidget(icon_label)
    text_label = QtWidgets.QLabel(text)
    text_label.setStyleSheet('font-size:16px; font-weight:bold;')
    row.addWidget(text_label)
    row.addStretch(1)
    return wrap


# 富文本内嵌图标的 PNG 缓存：键为 (图标名, 颜色, 尺寸)，值为文件路径
_RICH_PNG_CACHE = {}  # type: dict

# 富文本 <img> 缓存目录（进程临时目录下，随进程生命周期清理）
_RICH_PNG_DIR = None  # type: Optional[str]


def _rich_png_dir():
    # type: () -> str
    """返回富文本图标 PNG 缓存目录（惰性创建）。"""
    global _RICH_PNG_DIR
    if _RICH_PNG_DIR is None:
        import tempfile
        _RICH_PNG_DIR = os.path.join(
            tempfile.gettempdir(), 'maxagent_icon_cache',
        )
        if not os.path.isdir(_RICH_PNG_DIR):
            os.makedirs(_RICH_PNG_DIR, exist_ok=True)
    return _RICH_PNG_DIR


def rich_icon(name, color=None, size=13):
    # type: (str, Optional[str], int) -> Optional[str]
    """把 SVG 图标渲染成着色 PNG，返回富文本 ``<img>`` 片段。

    QLabel / 富文本场景无法直接用 QIcon，这里把图标落盘为 PNG
    后内嵌 ``<img src=...>``，颜色、尺寸完全可控；QLabel 对
    ``<img>`` 的支持在 PySide2/6 均稳定，无字体回退问题。

    :param name: 图标名（对应 icons/<name>.svg）
    :param color: 图标颜色；不传用 :data:`DEFAULT_ICON_COLOR`
    :param size: 内嵌显示尺寸（px）
    :returns: ``<img>`` HTML 片段；失败返回空串（调用方拼接文本即可）
    """
    fill = color or DEFAULT_ICON_COLOR
    cache_key = (name, fill, size)
    if cache_key in _RICH_PNG_CACHE:
        return _RICH_PNG_CACHE[cache_key]
    try:
        path = os.path.join(_ICONS_DIR, name + '.svg')
        if not os.path.isfile(path):
            _get_logger().warning('rich_icon [%s] SVG 不存在，返回空串', name)
            return ''
        pixmap = _render_pixmap(path, fill, size)
        if pixmap is None:
            return ''
        png_path = os.path.join(
            _rich_png_dir(),
            '{}_{}_{}.png'.format(name, fill.strip('#'), uuid.uuid4().hex[:8]),
        )
        pixmap.save(png_path, 'PNG')
        html = '<img src="{}" width="{}" height="{}">'.format(
            png_path.replace('\\', '/'), size, size,
        )
        _RICH_PNG_CACHE[cache_key] = html
        return html
    except Exception as exc:  # pylint: disable=broad-except
        # 图标是纯装饰，任何异常都不能影响 UI 构建
        _get_logger().warning('rich_icon [%s] 生成失败 (%s)，返回空串', name, exc)
        return ''


def avatar_icon_html(name, color, size):
    # type: (str, str, int) -> str
    """把 SVG 图标渲染成 data URI 内嵌 ``<img>``，专供头像场景。

    与 :func:`rich_icon` 的区别：产物直接编码 base64 内嵌（不落盘
    临时 PNG）。原因：Qt6 的 QLabel 默认不读 ``file:///`` 资源，
    落盘路径在 PySide6 下头像会空白；``data:`` URI 由 QTextDocument
    直接解析，PySide2/6 双版本一致（与图片头像的 data URI 链同源）。

    :param name: 图标名（对应 icons/<name>.svg）
    :param color: 图标颜色
    :param size: 显示尺寸（px），实际按 2x 渲染保证高分屏清晰
    :returns: ``<img>`` HTML 片段；失败返回空串（调用方走文本兜底）
    """
    from ..qt_compat import QtCore
    cache_key = ('avatar_html', name, color, size)
    if cache_key in _ICON_CACHE:
        return _ICON_CACHE[cache_key]
    try:
        path = os.path.join(_ICONS_DIR, name + '.svg')
        if not os.path.isfile(path):
            _get_logger().warning(
                'avatar_icon [%s] SVG 不存在，返回空串', name,
            )
            return ''
        pixmap = _render_pixmap(path, color, size)
        if pixmap is None or pixmap.isNull():
            return ''
        buf = QtCore.QBuffer()
        buf.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
        if not pixmap.save(buf, 'PNG'):
            return ''
        raw = bytes(buf.data())
        data_uri = 'data:image/png;base64,{}'.format(
            __import__('base64').b64encode(raw).decode('ascii'),
        )
        html = '<img src="{}" width="{}" height="{}" '.format(
            data_uri, size, size,
        ) + 'style="vertical-align:middle;">'
        _ICON_CACHE[cache_key] = html
        return html
    except Exception as exc:  # pylint: disable=broad-except
        # 头像是纯装饰，任何异常都不能影响气泡渲染
        _get_logger().warning(
            'avatar_icon [%s] 生成失败 (%s)，返回空串', name, exc,
        )
        return ''


# emoji 离屏渲染缓存：键为 (字符, 尺寸)，值为 QPixmap
_EMOJI_PIX_CACHE = {}  # type: dict


def emoji_pixmap(emoji_char, size=18):
    # type: (str, int) -> Optional[object]
    """把 emoji 字符离屏渲染为透明底 QPixmap。

    QPushButton 的默认字体链在 Max/Maya 的 Windows 环境下渲染不出
    彩色 emoji（快选按钮整排空白），而气泡里的 emoji 显示正常——
    那条路走的是 QTextDocument 富文本管线。这里用同样的管线把
    emoji 离屏渲染成 QPixmap 交给 setIcon，所见即所得。

    渲染结果若完全透明（当前字体缺该字形，典型表现为按钮空白），
    返回 None 让调用方回落文字兜底。

    :param emoji_char: 单个 emoji 字符
    :param size: 逻辑尺寸（px）
    :returns: QPixmap；失败或空白渲染返回 None
    """
    from ..qt_compat import QtCore
    cache_key = (emoji_char, size)
    if cache_key in _EMOJI_PIX_CACHE:
        return _EMOJI_PIX_CACHE[cache_key]
    try:
        scale = 2.0
        font = QtGui.QFont()
        font.setPixelSize(int(size * scale))
        # 复用 emoji_compat 的字体回退族链（含 Segoe UI Emoji 等
        # 彩色 emoji 字体），与气泡头像渲染效果同源
        try:
            from .emoji_compat import _DEFAULT_FAMILIES
            font.setFamilies(list(_DEFAULT_FAMILIES))
        except Exception:  # pylint: disable=broad-except
            # 老 Qt 没有 setFamilies 时保持默认字体链
            pass
        doc = QtGui.QTextDocument()
        doc.setDefaultFont(font)
        # emoji 字符不含 HTML 特殊字符，直接内嵌
        doc.setHtml(emoji_char)
        doc.setTextWidth(-1)
        doc_size = doc.documentLayout().documentSize()
        width = int(doc_size.width())
        height = int(doc_size.height())
        if width <= 0 or height <= 0:
            return None
        pixmap = QtGui.QPixmap(width, height)
        pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(pixmap)
        try:
            # 文档内容居中绘制，避免不同字体基线偏差裁掉字形
            painter.translate(
                (width - doc_size.width()) / 2.0,
                (height - doc_size.height()) / 2.0,
            )
            doc.drawContents(painter)
        finally:
            painter.end()
        if pixmap.isNull():
            return None
        # 空白渲染检测：字体缺字形时整个位图全透明，判失败让
        # 按钮回落文字兜底，而不是显示一个空白按钮
        image = pixmap.toImage()
        has_ink = False
        for y in range(0, image.height(), 2):
            for x in range(0, image.width(), 2):
                if QtGui.QColor(image.pixel(x, y)).alpha() > 0:
                    has_ink = True
                    break
            if has_ink:
                break
        if not has_ink:
            _get_logger().debug(
                'emoji_pixmap [%s] 渲染全透明（字体缺字形），判失败',
                emoji_char,
            )
            return None
        pixmap.setDevicePixelRatio(scale)
        _EMOJI_PIX_CACHE[cache_key] = pixmap
        return pixmap
    except Exception as exc:  # pylint: disable=broad-except
        # 图标是纯装饰，任何异常都不能影响 UI 构建
        _get_logger().debug('emoji_pixmap [%s] 渲染异常 (%s)', emoji_char, exc)
        return None


def set_btn_icon(widget, name, text, color=None):
    # type: (object, str, str, Optional[str]) -> bool
    """给按钮设置 SVG 图标并把标签改为纯文本。

    成功：``setIcon`` + ``setText(text)``（去掉 emoji 前缀），返回 True。
    失败：不做任何修改——按钮保持 ``btn_label`` 生成的 emoji/BMP
    文本兜底，返回 False。调用方无需 if/else 分支。

    :param widget: QPushButton 等 AbstractButton 控件
    :param name: 图标名（对应 icons/<name>.svg）
    :param text: 成功后按钮显示的纯文本
    :param color: 可选图标颜色；不传则按图标名查
        :data:`ICON_SEMANTIC_COLORS` 语义色表，仍未命中用默认灰
    :returns: 是否成功应用了 SVG 图标
    """
    if widget is None:
        return False
    # 颜色优先级：显式传入 > 语义色表 > 默认灰
    icon = load_icon(name, color=color or ICON_SEMANTIC_COLORS.get(name))
    if icon is None:
        # 失败原因已由 load_icon 记录，这里保持按钮原样（文本兜底）
        return False
    try:
        widget.setIcon(icon)
        widget.setText(text)
        _get_logger().debug('icon [%s] 已应用到按钮 "%s"', name, text)
        return True
    except Exception as exc:  # pylint: disable=broad-except
        _get_logger().warning(
            'icon [%s] setIcon/setText 失败 (%s)，保持文本兜底', name, exc,
        )
        return False


def _apply_fill(svg_text, fill):
    # type: (str, str) -> str
    """把目标颜色显式注入 SVG 的每个图形元素。

    Qt (SVG Tiny 1.2) 渲染器对根节点 ``fill="currentColor"`` 的
    继承支持不可靠，path 上拿不到颜色会退化成默认黑色。
    因此不能只做字符串替换，必须：

    1. 全局替换 ``currentColor``（覆盖根节点与 style 写法）；
    2. 元素上已写 fill 的（如手工图标写死 ``fill="#000"``），替换其值；
    3. 元素没写 fill 的，逐个注入 ``fill="目标色"``，
       不依赖根节点继承，保证任意素材都渲染出预期颜色。

    :param svg_text: SVG 文件原始文本
    :param fill: 目标颜色（CSS 颜色串）
    :returns: 注入颜色后的 SVG 文本
    """
    # 步骤 1：currentColor 全局替换（属性值与 CSS 均覆盖）
    svg_text = svg_text.replace('currentColor', fill)

    # 步骤 2：替换元素上已有的 fill 值（不匹配 fill-rule/fill-opacity）
    has_fill_re = re.compile(
        r'(<(?:path|circle|rect|polygon|ellipse|line|polyline)\b'
        r'[^>]*?\sfill=")[^"]*(")',
    )
    svg_text = has_fill_re.sub(
        lambda m: m.group(1) + fill + m.group(2), svg_text,
    )

    # 步骤 3：给没有 fill 的图形元素注入 fill
    # 负向前瞻 (?![^>]*\bfill=) 避免对已有 fill 的元素重复注入
    no_fill_re = re.compile(
        r'(<(?:path|circle|rect|polygon|ellipse|line|polyline)\b'
        r'(?![^>]*\sfill=")[^>]*?)(/?>)',
    )
    return no_fill_re.sub(
        lambda m: m.group(1) + ' fill="{}"'.format(fill) + m.group(2),
        svg_text,
    )


def _render_pixmap_via_reader(path, fill, size):
    # type: (str, str, int) -> Optional[object]
    """QtSvg 模块缺失时的兜底渲染：QImageReader + 源染色。

    Qt 内置 qsvg 插件（用户探针实测可用）走 QImageReader 也能把
    SVG 光栅化为 QImage，只是没有 QSvgRenderer 灵活。颜色处理：
    先把 SVG 源文本按 _apply_fill 染色，写入临时 svgz 文件后再读，
    保证颜色与主链路一致。

    :param path: SVG 文件绝对路径
    :param fill: 目标颜色
    :param size: 逻辑尺寸（px）
    :returns: QPixmap；失败返回 None
    """
    from ..qt_compat import QtCore
    try:
        with open(path, 'r', encoding='utf-8') as f:
            svg_text = _apply_fill(f.read(), fill)
    except OSError as exc:
        _get_logger().warning('icon SVG 读取失败 (%s): %s', exc, path)
        return None

    # QImageReader 需要文件路径（内存格式 svgz 依赖压缩头），
    # 染色后的内容写到临时文件再读
    tmp = os.path.join(
        _rich_png_dir(), 'reader_tmp_{}.svg'.format(uuid.uuid4().hex[:8]),
    )
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(svg_text)
        reader = QtGui.QImageReader(tmp)
        reader.setDecideFormatFromContent(True)
        # 目标尺寸按 2x 设定，HiDPI 下依然清晰
        scale = 2.0
        reader.setSize(QtGui.QSize(int(size * scale), int(size * scale)))
        image = reader.read()
    except Exception as exc:  # pylint: disable=broad-except
        _get_logger().warning('icon QImageReader 渲染异常 (%s): %s', exc, path)
        return None
    finally:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass

    if image is None or image.isNull():
        _get_logger().warning('icon QImageReader 渲染为空: %s', path)
        return None
    pixmap = QtGui.QPixmap.fromImage(image)
    if pixmap.isNull():
        return None
    pixmap.setDevicePixelRatio(scale)
    return pixmap


def _render_pixmap(path, fill, size):
    # type: (str, str, int) -> Optional[object]
    """把 SVG 文件渲染为指定逻辑尺寸的透明底 QPixmap（2x 抗模糊）。

    :param path: SVG 文件绝对路径
    :param fill: 目标颜色
    :param size: 逻辑尺寸（px）
    :returns: QPixmap；失败返回 None
    """
    from ..qt_compat import QtCore
    try:
        if IS_PYSIDE6:
            from PySide6.QtSvg import QSvgRenderer  # type: ignore  # pylint: disable=import-error,no-name-in-module
        elif IS_PYSIDE2:
            from PySide2.QtSvg import QSvgRenderer  # type: ignore  # pylint: disable=import-error,no-name-in-module
        else:
            _get_logger().warning(
                '无可用 Qt 绑定（IS_PYSIDE2/6 均为 False），'
                'icon 降级 QImageReader 链',
            )
            return _render_pixmap_via_reader(path, fill, size)
    except ImportError as exc:
        # 部分 Max 版本的 PySide2 缺 QtSvg Python 模块（用户探针实测），
        # 降级到 QImageReader 链而不是直接失败
        _get_logger().warning(
            'QtSvg 模块不可用 (%s)，icon 降级 QImageReader 链', exc,
        )
        return _render_pixmap_via_reader(path, fill, size)

    try:
        with open(path, 'r', encoding='utf-8') as f:
            svg_text = f.read()
    except OSError as exc:
        _get_logger().warning('icon SVG 读取失败 (%s): %s', exc, path)
        return None

    # 把 fill 显式注入每个图形元素（Qt 渲染器不认根节点继承）
    svg_text = _apply_fill(svg_text, fill)

    renderer = QSvgRenderer(QtCore.QByteArray(svg_text.encode('utf-8')))
    if not renderer.isValid():
        _get_logger().warning('icon SVG 解析失败 (renderer invalid): %s', path)
        return None

    # 2x 尺寸渲染 + DevicePixelRatio，HiDPI 下依然清晰
    scale = 2.0
    pixmap = QtGui.QPixmap(int(size * scale), int(size * scale))
    pixmap.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(pixmap)
    try:
        renderer.render(painter)
    finally:
        painter.end()
    if pixmap.isNull():
        _get_logger().warning('icon SVG 渲染为空 pixmap: %s', path)
        return None
    pixmap.setDevicePixelRatio(scale)
    return pixmap


def _render_svg_file(path, fill, size):
    # type: (str, str, int) -> Optional[object]
    """读取并渲染单个 SVG 文件为 QIcon，失败返回 None。

    绑定跟随 qt_compat 的解析结果：PySide6 环境只 import
    PySide6.QtSvg，PySide2 环境只 import PySide2.QtSvg，
    避免双绑定同时加载导致 Max 崩溃。
    """
    try:
        pixmap = _render_pixmap(path, fill, size)
        if pixmap is None:
            return None
        return QtGui.QIcon(pixmap)
    except ImportError as exc:
        _get_logger().warning(
            'QtSvg 模块不可用 (%s)，SVG 图标整体降级为文本兜底', exc,
        )
        return None
    except Exception as exc:  # pylint: disable=broad-except
        # 图标是纯装饰，任何异常都不能影响 UI 构建
        _get_logger().warning('icon SVG 渲染异常 (%s): %s', exc, path)
        return None


__all__ = [
    'DEFAULT_ICON_COLOR',
    'clear_cache',
    'emoji_pixmap',
    'icon_pixmap',
    'icons_dir',
    'load_icon',
    'make_page_title',
    'rich_icon',
    'set_btn_icon',
]
