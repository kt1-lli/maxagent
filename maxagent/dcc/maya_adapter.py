#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Maya 适配器（占位实现）。

当前 Phase 1 仅保留结构骨架，所有方法都会抛出 NotImplementedError。
后续 Phase 2 实现 Maya 工具集时再填充具体实现。
"""

from __future__ import absolute_import
from __future__ import print_function

from .adapter import DCCAdapter


class MayaAdapter(DCCAdapter):
    """Maya DCC 适配器占位。"""

    @property
    def name(self):
        return 'maya'

    def is_available(self):
        try:
            import maya.cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
            return maya.cmds is not None
        except ImportError:
            return False

    def get_main_window(self):
        raise NotImplementedError('MayaAdapter.get_main_window 尚未实现')

    def run_on_main(self, fn, *args, **kwargs):
        raise NotImplementedError('MayaAdapter.run_on_main 尚未实现')

    def undo_block(self, label="agent op"):
        raise NotImplementedError('MayaAdapter.undo_block 尚未实现')

    def get_selection(self):
        raise NotImplementedError('MayaAdapter.get_selection 尚未实现')

    def get_node_by_name(self, name):
        raise NotImplementedError('MayaAdapter.get_node_by_name 尚未实现')

    def create_primitive(self, typename, **kwargs):
        raise NotImplementedError('MayaAdapter.create_primitive 尚未实现')

    def execute_script(self, code, language="python"):
        raise NotImplementedError('MayaAdapter.execute_script 尚未实现')
