#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单一版本号源。

整个发布流水线只读取这一处的 ``__version__``，避免多处维护版本号导致漂移。
``release/build.py`` 会读取此文件并注入到 mzp 元数据 / 临时副本的
``maxagent/__init__.py.__version__``。

约定:
- 主版本号  major: 不兼容 API 变更（如 PySide / pymxs 接口大改）
- 次版本号  minor: 新增向下兼容功能（功能批次发布时递增）
- 修订号    patch: 向下兼容的问题修复（hotfix 时递增）
"""

from __future__ import absolute_import


__version__ = '0.4.0'
__codename__ = 'multi-abi-release'
__release_channel__ = 'stable'

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
