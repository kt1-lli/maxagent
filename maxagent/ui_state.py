#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UI 状态持久化。

与 ``config.py`` 保存的"逻辑配置"（API Key / 模型 / Profile）分开存到
独立文件 ``ui_state.json``，原因：
1. UI 状态在每次关闭/拖动时都会写盘，单独成文避免和 LLM 配置发生写竞争。
2. 用户偶尔需要把 ``config.json`` 拷贝到别的机器（共享 API Key、Profile），
   但 UI 状态（窗口几何、停靠位置）天然是机器相关的，不应跟着走。
3. 配置损坏时可独立回退某一边，互不影响。

字段说明：
    geometry_b64
        Qt ``saveGeometry()`` 返回的 QByteArray 的 base64 字符串。
        包含窗口位置、大小、是否最大化等完整状态。
    main_state_b64
        Qt ``QMainWindow.saveState()`` 返回的状态（停靠位置、是否浮动、
        其他 dockwidget 的相对位置）。仅在 Max 主窗口可写时保存。
    floating
        QDockWidget 是否为浮动状态（saveState 已经覆盖，但单独存一份做
        早期快速判断 / 兜底）。
    dock_area
        停靠区域（Qt.LeftDockWidgetArea=1, RightDockWidgetArea=2,
        TopDockWidgetArea=4, BottomDockWidgetArea=8）。
    splitter_sizes
        QSplitter 的 sizes() 列表。
    chat_height / input_height
        分割器无法保存时的 fallback。

Maya 专用字段说明（Qt 的 geometry_b64 / main_state_b64 在 Maya 下无意义，
Maya 的停靠由 workspaceControl 自己管理，必须单独存）：
    maya_dock_target
        停靠到的 workspaceControl 名（如 ``ChannelBoxLayerEditor``）。
    maya_dock_mode
        ``tab`` = tabToControl 并入目标标签页；``dock`` = dockToControl
        停靠到目标旁；``main`` = dockToMainWindow 停靠到主窗口某侧。
    maya_dock_side
        mode=main 时的方位：left / right / top / bottom。
    maya_floating / maya_visible / maya_width / maya_height
        浮动状态、可见性与尺寸，用于启动时还原用户上次的样子。
"""

from __future__ import absolute_import
from __future__ import print_function

import json
import os
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from typing import List
from typing import Optional

from .logger import get_logger

logger = get_logger(__name__)


UI_STATE_VERSION = 1


@dataclass
class UIState:
    """UI 状态快照。所有字段均可选，缺失时使用默认值。"""

    version: int = UI_STATE_VERSION
    # Qt saveGeometry 的 base64
    geometry_b64: str = ''
    # Qt QMainWindow.saveState() 的 base64（包含 dock 布局、相对位置）
    # 仅在嵌入到 Max 主窗口时有值。少了这一份，重启后 Qt 不知道把
    # QDockWidget 放回哪一列，会回退到默认右侧。
    main_state_b64: str = ''
    # QDockWidget 浮动 / 停靠
    floating: bool = False
    # Qt.RightDockWidgetArea = 2，作为默认值
    dock_area: int = 2
    # 独立窗口模式下的 fallback 尺寸/位置（-1 = 居中）
    window_w: int = 720
    window_h: int = 800
    window_x: int = -1
    window_y: int = -1
    # 是否最大化（Qt 的 saveGeometry 已包含此信息，这里是兜底）
    maximized: bool = False
    # QSplitter.sizes() 列表（聊天区 / 输入区）
    splitter_sizes: List[int] = field(default_factory=lambda: [400, 100])

    # ---- Maya 专用停靠状态（Qt 的 geometry/saveState 在 Maya 下无意义） ---- #
    # 停靠到的 workspaceControl 名，如 'ChannelBoxLayerEditor'。
    # 空串表示走默认优先级自动选择。
    maya_dock_target: str = ''
    # 停靠方式：'tab'（tabToControl，并入目标标签页）/
    #           'dock'（dockToControl，停靠到目标旁边）/
    #           'main'（dockToMainWindow，停靠到主窗口某侧）
    maya_dock_mode: str = 'tab'
    # 停靠到主窗口时的方位：left / right / top / bottom
    maya_dock_side: str = 'right'
    # 是否浮动（脱离 Maya 布局成为独立窗口）
    maya_floating: bool = False
    # 面板当前是否可见（用户手动关掉后不应强制弹回）
    maya_visible: bool = True
    # 面板宽高（workspaceControl 的 width/height 查询结果）
    maya_width: int = 0
    maya_height: int = 0
    # 设置面板上次活跃的 tab 索引（备用）
    last_settings_tab: int = 0
    # 上次启动是否成功嵌入到 Max 主窗口（用于诊断 / 启动统计）
    last_embedded_ok: bool = False
    # 上次活跃的会话 ID，启动时优先恢复该会话
    last_session_sid: str = ''

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        # type: (dict) -> UIState
        if not isinstance(data, dict):
            return cls()
        valid = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in data.items() if k in valid}
        # 类型容错：splitter_sizes 必须是 list[int]
        sizes = clean.get('splitter_sizes')
        if isinstance(sizes, list):
            clean['splitter_sizes'] = [int(x) for x in sizes if x is not None]
        else:
            clean.pop('splitter_sizes', None)
        try:
            return cls(**clean)
        except TypeError:
            return cls()


def _ui_state_path(custom_path=None):
    # type: (Optional[str]) -> str
    """返回 ui_state.json 的绝对路径。

    复用 ``config.get_config_dir()`` 拿到与 config.json 同级目录。
    """
    if custom_path:
        return custom_path
    # 延迟 import 避免循环依赖
    from .config import get_config_dir
    return os.path.join(get_config_dir(), 'ui_state.json')


class UIStateManager:
    """UIState 的读写门面。

    用法::

        mgr = UIStateManager()
        state = mgr.load()
        state.geometry_b64 = '...'
        mgr.save(state)

    所有写操作均做原子替换（写到 .tmp 再 rename），避免 Max 崩溃时
    损坏配置。文件不存在或损坏时返回默认 UIState 而不是抛异常，
    确保插件首次启动可用。
    """

    def __init__(self, path=None):
        # type: (Optional[str]) -> None
        self._path = _ui_state_path(path)

    @property
    def path(self):
        return self._path

    def load(self):
        # type: () -> UIState
        if not os.path.exists(self._path):
            return UIState()
        try:
            with open(self._path, 'r', encoding='utf-8') as fh:
                raw = json.load(fh)
            return UIState.from_dict(raw)
        except (OSError, ValueError) as exc:
            logger.warning('UI 状态加载失败，使用默认: %s', exc)
            return UIState()

    def save(self, state):
        # type: (UIState) -> None
        os.makedirs(os.path.dirname(self._path) or '.', exist_ok=True)
        tmp = self._path + '.tmp'
        try:
            with open(tmp, 'w', encoding='utf-8') as fh:
                json.dump(
                    state.to_dict(), fh, ensure_ascii=False, indent=2,
                )
            if os.path.exists(self._path):
                os.replace(tmp, self._path)
            else:
                os.rename(tmp, self._path)
        except OSError as exc:
            # 写盘失败不影响主流程，仅打日志
            logger.warning('UI 状态保存失败: %s', exc)

    def update(self, **kwargs):
        # type: (...) -> UIState
        """加载 -> 修改字段 -> 保存，一行调用。"""
        state = self.load()
        for k, v in kwargs.items():
            if hasattr(state, k):
                setattr(state, k, v)
        self.save(state)
        return state


__all__ = ['UIState', 'UIStateManager', 'UI_STATE_VERSION']
