#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单一版本号源。"""

from __future__ import absolute_import

__version__ = '1.0.1'

# 支持的 3ds Max 版本对应的 Python ABI 标签
# (参考 Autodesk 官方文档: 2027 已是 Python 3.13.9)
SUPPORTED_ABIS = (
    'cp37',   # 3ds Max 2022 / 2022.x          (Python 3.7.9)
    'cp39',   # 3ds Max 2023                    (Python 3.9.7)
    'cp310',  # 3ds Max 2024                    (Python 3.10.8)
    'cp311',  # 3ds Max 2025 / 2025.1 / 2026    (Python 3.11.x)
    'cp313',  # 3ds Max 2027                    (Python 3.13.9)
)

ABI_TO_PYTHON = {
    'cp37': '3.7.9',
    'cp39': '3.9.7',
    'cp310': '3.10.8',
    'cp311': '3.11.9',
    'cp313': '3.13.9',
}

ABI_TO_MAX_VERSIONS = {
    'cp37': ('2022', '2022.1', '2022.2', '2022.3'),
    'cp39': ('2023',),
    'cp310': ('2024',),
    'cp311': ('2025', '2025.1', '2026'),
    'cp313': ('2027',),
}
