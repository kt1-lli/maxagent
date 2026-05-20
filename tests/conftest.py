#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pytest 共享 fixtures。

把 maxagent 根目录加入 sys.path，方便测试直接 ``import maxagent``。
"""

from __future__ import absolute_import
from __future__ import print_function

import os
import sys


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
