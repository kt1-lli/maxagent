#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DCC 适配层入口。

提供运行时 DCC 探测、统一适配器接口和主线程调度函数。
"""

from __future__ import absolute_import
from __future__ import print_function

from .adapter import DCCAdapter
from .runtime import current_dcc
from .runtime import run_on_main

__all__ = [
    'DCCAdapter',
    'current_dcc',
    'run_on_main',
]
