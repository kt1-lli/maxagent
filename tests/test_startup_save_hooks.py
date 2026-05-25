#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""startup._connect_qdock_save_hooks 防抖与稳定期回归测试。

历史上线后用户报告：图1（嵌入态）会被保存覆盖成图2（被推到右侧的奇怪
布局）。根因是保存钩子在以下场景把 dirty 中间状态当成最终态写盘：

    1. 启动期 Qt 还在做布局适应，``visibilityChanged`` 等信号触发了
       一次"启动后立即落盘"，几何还没稳定。
    2. ``saveState() / saveGeometry()`` 在 hide / 短瞬态下返回空 / 极短
       二进制，覆盖掉用户上次保存的好布局。
    3. 用户拖动过程中信号高频触发，最后一次抓到的是中间帧。

修复策略：
    - 1.2 秒启动稳定期内屏蔽自动保存
    - hide 时不保存
    - 二进制过短（< 50 字节）视为无效快照，跳过
    - 300ms 防抖合并高频信号
    - 暴露 ``pin_current_layout`` 作为"用户确认"的强意图入口

本测试通过 stub 出 QDockWidget / 主窗口的最小 API，并直接调用
``dock_widget`` 上挂载的 ``_flush_qdock_state`` / ``_arm_qdock_save``
两个 hook 来验证内部保存路径的语义；不直接操作 QTimer 全局，避免
干扰真实 Qt 模块导致测试进程崩溃。
"""

from __future__ import absolute_import
from __future__ import print_function

import os

# 必须在 import maxagent.startup 之前设置：startup 模块顶层会自动调
# _auto_register() → show_panel()，如果跑下去会直接创建 QWidget，
# 在没有 QApplication 的测试进程里会导致进程 abort
os.environ['MAXAGENT_NO_AUTOSTART'] = '1'

from typing import List

import pytest


class _FakeByteArray(object):
    """模拟 ``QByteArray``：``bytes(ba)`` / ``ba.data()`` 都能拿到 raw。"""

    def __init__(self, raw):
        self._raw = bytes(raw)

    def __bytes__(self):
        return self._raw

    def data(self):
        return self._raw


class _FakeSignal(object):
    """模拟 Qt signal：connect 收集 slot，emit 触发所有 slot。"""

    def __init__(self):
        self._slots = []  # type: List

    def connect(self, slot):
        self._slots.append(slot)

    def emit(self, *args):
        for slot in list(self._slots):
            slot(*args)


class _FakeMainWin(object):
    """模拟 Max 主窗口：只暴露保存所需 API。"""

    def __init__(self, area=2, state_bytes=None):
        self._area = area
        # 默认给一段 100 字节的"合法" state
        self._state_bytes = state_bytes if state_bytes is not None else (
            b'\x01' * 100
        )

    def dockWidgetArea(self, _qdock):  # noqa: N802 (Qt API)
        return self._area

    def saveState(self):  # noqa: N802 (Qt API)
        return _FakeByteArray(self._state_bytes)


class _FakeQDock(object):
    """模拟 QDockWidget：仅必要 API + 三个 signal。"""

    def __init__(self, geo_bytes=None, floating=False, parent=None):
        # 默认给一段 80 字节的"合法" geometry（> 50 阈值）
        self._geo = geo_bytes if geo_bytes is not None else b'\xab' * 80
        self._floating = floating
        self._parent = parent
        self.topLevelChanged = _FakeSignal()
        self.dockLocationChanged = _FakeSignal()
        self.visibilityChanged = _FakeSignal()

    def saveGeometry(self):  # noqa: N802 (Qt API)
        return _FakeByteArray(self._geo)

    def isFloating(self):  # noqa: N802 (Qt API)
        return self._floating

    def parent(self):
        return self._parent


class _SaveCallRecorder(object):
    """模拟 dock_widget.save_ui_state，记录每次调用入参。"""

    def __init__(self):
        self.calls = []  # type: List[dict]

    def save_ui_state(self, **kwargs):
        self.calls.append(dict(kwargs))


# ---------------------------------------------------------------------- #
# 测试 1：直接调用 _flush_qdock_state（模拟用户主动 pin），应正常落盘
# ---------------------------------------------------------------------- #

def test_flush_writes_full_payload():
    """``_flush_qdock_state`` 同步保存路径应完整写入 4 个核心字段。"""
    from maxagent import startup as startup_mod

    main_win = _FakeMainWin()
    qdock = _FakeQDock(parent=main_win)
    recorder = _SaveCallRecorder()
    startup_mod._connect_qdock_save_hooks(qdock, recorder)

    flusher = recorder._flush_qdock_state  # type: ignore[attr-defined]
    flusher()

    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert call.get('floating') is False
    assert call.get('dock_area') == 2
    assert call.get('embedded_ok') is True
    assert call.get('geometry_b64')  # 非空
    assert call.get('main_state_b64')  # 非空


# ---------------------------------------------------------------------- #
# 测试 2：过短的 saveGeometry 二进制被丢弃 —— 整次保存放弃
# ---------------------------------------------------------------------- #

def test_short_geometry_bytes_rejected():
    """saveGeometry 返回 < 50 字节时认定为无效快照，整次保存放弃。

    避免启动初期 / 隐藏瞬间拿到的"半成品"几何覆盖好状态。
    """
    from maxagent import startup as startup_mod

    main_win = _FakeMainWin()
    qdock = _FakeQDock(geo_bytes=b'\x00' * 10, parent=main_win)  # 仅 10 字节
    recorder = _SaveCallRecorder()
    startup_mod._connect_qdock_save_hooks(qdock, recorder)

    flusher = recorder._flush_qdock_state  # type: ignore[attr-defined]
    flusher()

    assert recorder.calls == [], (
        '过短几何应被丢弃, 实际写入: {}'.format(recorder.calls)
    )


# ---------------------------------------------------------------------- #
# 测试 3：过短 main_state_b64 不覆盖旧值（传 None 让消费方沿用旧值）
# ---------------------------------------------------------------------- #

def test_short_main_state_passes_none():
    """saveState 返回 < 20 字节时，main_state_b64 应传 None 而不是 ''。

    这样 ``save_ui_state`` 内 "None 表示沿用旧值" 的语义会保留好布局。
    """
    from maxagent import startup as startup_mod

    main_win = _FakeMainWin(state_bytes=b'\x00' * 5)  # 短到不可信
    qdock = _FakeQDock(parent=main_win)
    recorder = _SaveCallRecorder()
    startup_mod._connect_qdock_save_hooks(qdock, recorder)

    recorder._flush_qdock_state()  # type: ignore[attr-defined]

    assert len(recorder.calls) == 1
    # 关键断言：main_state_b64 必须是 None（沿用旧值），而不是空字符串
    assert recorder.calls[0].get('main_state_b64') is None


# ---------------------------------------------------------------------- #
# 测试 4：hooks 都被正确注册到对应 signal 上
# ---------------------------------------------------------------------- #

def test_signals_are_connected():
    """3 个 signal 都应被订阅 1 个 slot（防抖入口）。"""
    from maxagent import startup as startup_mod

    main_win = _FakeMainWin()
    qdock = _FakeQDock(parent=main_win)
    recorder = _SaveCallRecorder()
    startup_mod._connect_qdock_save_hooks(qdock, recorder)

    # pylint: disable=protected-access
    assert len(qdock.topLevelChanged._slots) == 1
    assert len(qdock.dockLocationChanged._slots) == 1
    assert len(qdock.visibilityChanged._slots) == 1


# ---------------------------------------------------------------------- #
# 测试 5：visibilityChanged(False) 永远不触发保存（hide 过滤）
# ---------------------------------------------------------------------- #

def test_hide_signal_does_not_save():
    """hide 事件应被过滤掉（visibilityChanged(False) 永远不进保存）。

    历史上线后图1→图2现象的元凶之一是 ``visibilityChanged(False)``
    时 ``saveGeometry`` 拿到的几何不可靠（屏外 / 0 大小），覆盖了
    用户上次保存的好布局。修复后该信号在 lambda 层就被过滤掉。

    本测试只验证 hide 不会触发任何保存路径——通过先 patch
    ``_schedule_save`` 行为不可能，所以改成等价断言：
    ``visibilityChanged(False)`` 后立刻同步 flush 一次，得到的
    保存次数应严格等于 1（只有 flush 那一次，没有 hide 引入的）。
    """
    from maxagent import startup as startup_mod

    main_win = _FakeMainWin()
    qdock = _FakeQDock(parent=main_win)
    recorder = _SaveCallRecorder()
    startup_mod._connect_qdock_save_hooks(qdock, recorder)

    qdock.visibilityChanged.emit(False)

    # 立即同步 flush；如果上一行 hide 错误地触发了保存（即便走防抖
    # 队列），它至迟也会在 flush 时已经被加入队列——但因为我们的
    # lambda 直接 ``return None``，hide 根本不会进入 _schedule_save，
    # 所以 flush 后总次数应为 1，而不是 2
    flusher = recorder._flush_qdock_state  # type: ignore[attr-defined]
    flusher()

    assert len(recorder.calls) == 1, (
        'hide 不应触发保存，flush 后只该有 1 次, 实际 {} 次'.format(
            len(recorder.calls),
        )
    )


# ---------------------------------------------------------------------- #
# 测试 6：手动 _arm_qdock_save() 后再 flush，能正常落盘
# ---------------------------------------------------------------------- #

def test_arm_then_flush_saves():
    """显式 arm 后直接 flush 同步保存路径必须能写盘。

    模拟 ``pin_current_layout`` 的内部流程：先 arm 解除稳定期，再
    调 ``_flush_qdock_state``。
    """
    from maxagent import startup as startup_mod

    main_win = _FakeMainWin()
    qdock = _FakeQDock(parent=main_win)
    recorder = _SaveCallRecorder()
    startup_mod._connect_qdock_save_hooks(qdock, recorder)

    arm = recorder._arm_qdock_save  # type: ignore[attr-defined]
    flusher = recorder._flush_qdock_state  # type: ignore[attr-defined]

    arm()
    flusher()

    assert len(recorder.calls) == 1


# ---------------------------------------------------------------------- #
# 测试 7：pin_current_layout 在没有 dock 单例时安全返回 False
# ---------------------------------------------------------------------- #

def test_pin_current_layout_no_dock_returns_false(monkeypatch):
    """没有 dock 单例时 ``pin_current_layout`` 应安全返回 False。"""
    from maxagent import startup as startup_mod

    monkeypatch.setattr(startup_mod, '_DOCK_WIDGET', None)
    assert startup_mod.pin_current_layout() is False


# ---------------------------------------------------------------------- #
# 测试 8：pin_current_layout 在有 dock 单例时立即写盘
# ---------------------------------------------------------------------- #

def test_pin_current_layout_writes_immediately(monkeypatch):
    """``pin_current_layout`` 应主动 arm 并同步 flush。"""
    from maxagent import startup as startup_mod

    main_win = _FakeMainWin()
    qdock = _FakeQDock(parent=main_win)
    recorder = _SaveCallRecorder()
    startup_mod._connect_qdock_save_hooks(qdock, recorder)

    monkeypatch.setattr(startup_mod, '_DOCK_WIDGET', recorder)

    ok = startup_mod.pin_current_layout()
    assert ok is True
    assert len(recorder.calls) == 1, (
        'pin 应当立即写一次盘, 实际 {} 次'.format(len(recorder.calls))
    )
