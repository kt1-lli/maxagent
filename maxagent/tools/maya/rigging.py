#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Maya 绑定类工具的兼容门面。

实际实现拆分到 tools/maya/rig/ 子包。此文件仅 re-export，供外部旧路径
``maxagent.tools.maya.rigging`` 与 ``tools.__init__`` 的动态 import_module 使用。
"""

from __future__ import absolute_import

from .rig import *  # noqa: F401,F403
from .rig import __all__  # noqa: F401
