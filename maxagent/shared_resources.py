#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""共享只读资源目录管理。

本模块提供统一的共享资源目录扫描、冲突解决和只读保护能力。
共享目录是团队外挂资产库，通过 Git 同步，对当前运行实例只读。

支持的资产类型：
    - skills
    - user_tools
    - user_rules
    - reflections
    - knowledge/user_sources

冲突解决策略：
    - use_shared     使用共享版本（默认）
    - use_local      使用本地版本
    - keep_both      保留两者，共享版本加 shared_ 前缀
    - overwrite_local 用共享版本覆盖本地版本
"""

from __future__ import absolute_import
from __future__ import print_function

import json
import os
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from .config import get_config_dir
from .config import load_config
from .logger import get_logger


logger = get_logger(__name__)


# 共享资产子目录映射（相对于共享根目录）
ASSET_SUBDIRS = {
    'skills': 'skills',
    'user_tools': 'user_tools',
    'user_rules': 'user_rules',
    'reflections': 'reflections',
    'knowledge_sources': os.path.join('knowledge', 'user_sources'),
}

# 冲突解决选项
CONFLICT_RESOLUTIONS = (
    'use_shared',
    'use_local',
    'keep_both',
    'overwrite_local',
)

# 共享版本重命名前缀
SHARED_NAME_PREFIX = 'shared_'


def get_shared_resources_dir() -> Optional[str]:
    """获取共享资源目录路径。

    优先级：
    1. ``MAXAGENT_SHARED_DIR`` 环境变量
    2. ``config.json`` 中的 ``shared_resources_dir`` 字段
    3. 未启用返回 None

    返回 None 或存在的目录绝对路径。
    """
    env_dir = os.environ.get('MAXAGENT_SHARED_DIR', '').strip()
    if env_dir:
        if os.path.isdir(env_dir):
            return os.path.abspath(env_dir)
        logger.warning('环境变量 MAXAGENT_SHARED_DIR 指向的目录不存在: %s', env_dir)
        return None

    try:
        cfg = load_config()
        cfg_dir = (cfg.shared_resources_dir or '').strip()
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning('加载配置失败，无法获取共享资源目录: %s', exc)
        return None

    if cfg_dir and os.path.isdir(cfg_dir):
        return os.path.abspath(cfg_dir)
    return None


def is_shared_resources_enabled() -> bool:
    """共享资源目录是否已启用。"""
    return get_shared_resources_dir() is not None


def get_shared_subdir(asset_type: str) -> Optional[str]:
    """获取某类资产在共享目录中的绝对路径；未启用时返回 None。"""
    root = get_shared_resources_dir()
    if not root:
        return None
    sub = ASSET_SUBDIRS.get(asset_type)
    if not sub:
        return None
    return os.path.join(root, sub)


def list_shared_files(asset_type: str, pattern: Optional[str] = None) -> List[str]:
    """列出共享目录下某类资产的文件绝对路径。

    :param asset_type: skills / user_tools / user_rules / reflections
    :param pattern: 可选扩展名过滤，如 '.json'
    :returns: 按文件名排序的绝对路径列表
    """
    subdir = get_shared_subdir(asset_type)
    if not subdir or not os.path.isdir(subdir):
        return []
    out = []
    try:
        for fname in sorted(os.listdir(subdir)):
            full = os.path.join(subdir, fname)
            if not os.path.isfile(full):
                continue
            if pattern is not None and not fname.lower().endswith(pattern.lower()):
                continue
            out.append(full)
    except OSError as exc:
        logger.warning('扫描共享目录 %s 失败: %s', subdir, exc)
    return out


def list_shared_knowledge_sources() -> List[Dict[str, Any]]:
    """列出共享目录下的知识源。

    知识源是目录型资产，直接返回目录项信息。
    """
    subdir = get_shared_subdir('knowledge_sources')
    if not subdir or not os.path.isdir(subdir):
        return []
    out = []
    try:
        for name in sorted(os.listdir(subdir)):
            full = os.path.join(subdir, name)
            if not os.path.isdir(full):
                continue
            out.append({
                'source_id': 'shared_' + name,
                'name': name,
                'path': full,
                'kind': 'dir',
                'shared': True,
            })
    except OSError as exc:
        logger.warning('扫描共享知识源目录 %s 失败: %s', subdir, exc)
    return out


@dataclass
class ConflictResolution:
    """单条同名资产的冲突解决记录。"""

    name: str
    asset_type: str
    resolution: str = 'use_shared'
    shared_path: str = ''
    local_path: str = ''
    # confirmed 表示该记录是否经过用户/调用方显式确认；
    # False 时代表由系统按默认策略自动生成的记录，UI 应继续提示用户选择。
    confirmed: bool = False
    resolved_at: float = field(default_factory=lambda: 0.0)

    def __post_init__(self):
        if self.resolution not in CONFLICT_RESOLUTIONS:
            self.resolution = 'use_shared'

    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'asset_type': self.asset_type,
            'resolution': self.resolution,
            'shared_path': self.shared_path,
            'local_path': self.local_path,
            'confirmed': self.confirmed,
            'resolved_at': self.resolved_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ConflictResolution':
        return cls(
            name=data.get('name', ''),
            asset_type=data.get('asset_type', ''),
            resolution=data.get('resolution', 'use_shared'),
            shared_path=data.get('shared_path', ''),
            local_path=data.get('local_path', ''),
            confirmed=bool(data.get('confirmed', False)),
            resolved_at=float(data.get('resolved_at') or 0.0),
        )


class SharedConflictResolver:
    """管理本地与共享资产的同名冲突解决记录。"""

    FILENAME = 'shared_conflict_resolutions.json'

    def __init__(self, config_dir=None):
        # type: (Optional[str]) -> None
        self._config_dir = config_dir or get_config_dir()
        self._path = os.path.join(self._config_dir, self.FILENAME)
        self._records = {}  # type: Dict[str, ConflictResolution]
        self._load()

    def _key(self, name: str, asset_type: str) -> str:
        return '{}:{}'.format(asset_type, name)

    def _load(self):
        # type: () -> None
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
            for item in (data.get('resolutions') or []):
                rec = ConflictResolution.from_dict(item)
                self._records[self._key(rec.name, rec.asset_type)] = rec
        except (OSError, ValueError) as exc:
            logger.warning('加载冲突解决记录失败: %s', exc)

    def save(self):
        # type: () -> None
        try:
            os.makedirs(self._config_dir, exist_ok=True)
            tmp = self._path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as fh:
                json.dump(
                    {'resolutions': [r.to_dict() for r in self._records.values()]},
                    fh,
                    ensure_ascii=False,
                    indent=2,
                )
            if os.path.exists(self._path):
                os.replace(tmp, self._path)
            else:
                os.rename(tmp, self._path)
        except OSError as exc:
            logger.warning('保存冲突解决记录失败: %s', exc)

    def get(self, name: str, asset_type: str) -> Optional[ConflictResolution]:
        return self._records.get(self._key(name, asset_type))

    def set(self, name: str, asset_type: str, resolution: str,
            shared_path: str = '', local_path: str = '',
            confirmed: bool = False):
        # type: (str, str, str, str, str, bool) -> None
        if resolution not in CONFLICT_RESOLUTIONS:
            resolution = 'use_shared'
        rec = ConflictResolution(
            name=name,
            asset_type=asset_type,
            resolution=resolution,
            shared_path=shared_path,
            local_path=local_path,
            confirmed=confirmed,
            resolved_at=time_now(),
        )
        self._records[self._key(name, asset_type)] = rec
        self.save()

    def remove(self, name: str, asset_type: str):
        # type: (str, str) -> bool
        key = self._key(name, asset_type)
        if key in self._records:
            del self._records[key]
            self.save()
            return True
        return False

    def list_all(self):
        # type: () -> List[ConflictResolution]
        return list(self._records.values())


def time_now() -> float:
    """当前时间戳。"""
    import time
    return time.time()


def is_under_shared_path(path: str) -> bool:
    """判断给定路径是否位于共享资源目录下。"""
    shared = get_shared_resources_dir()
    if not shared or not path:
        return False
    abs_shared = os.path.abspath(shared)
    abs_path = os.path.abspath(path)
    # 防止 shared=/a 误匹配 /abc
    if abs_path == abs_shared:
        return True
    return abs_path.startswith(abs_shared + os.sep)


def guard_shared_write(path: str) -> None:
    """写操作前的只读保护。若目标位于共享目录则抛 PermissionError。"""
    if is_under_shared_path(path):
        raise PermissionError(
            '共享资源只读，禁止写入: {}'.format(path),
        )


class SharedAssetStats:
    """共享目录资产统计。"""

    def __init__(self):
        # type: () -> None
        self.skills = 0
        self.user_tools = 0
        self.user_rules = 0
        self.reflections = 0
        self.knowledge_sources = 0

    def to_dict(self):
        # type: () -> Dict[str, int]
        return {
            'skills': self.skills,
            'user_tools': self.user_tools,
            'user_rules': self.user_rules,
            'reflections': self.reflections,
            'knowledge_sources': self.knowledge_sources,
        }


def scan_shared_stats() -> SharedAssetStats:
    """扫描共享目录并返回各类资产数量统计。"""
    stats = SharedAssetStats()
    if not is_shared_resources_enabled():
        return stats

    stats.skills = len(list_shared_files('skills', '.json'))
    stats.user_tools = len([
        p for p in list_shared_files('user_tools', '.py')
        if not os.path.basename(p).startswith('__')
    ])
    stats.user_rules = len(list_shared_files('user_rules', '.json'))
    stats.reflections = len(list_shared_files('reflections', '.json'))
    stats.knowledge_sources = len(list_shared_knowledge_sources())
    return stats


__all__ = [
    'ASSET_SUBDIRS',
    'CONFLICT_RESOLUTIONS',
    'SHARED_NAME_PREFIX',
    'ConflictResolution',
    'SharedAssetStats',
    'SharedConflictResolver',
    'get_shared_resources_dir',
    'get_shared_subdir',
    'guard_shared_write',
    'is_shared_resources_enabled',
    'is_under_shared_path',
    'list_shared_files',
    'list_shared_knowledge_sources',
    'scan_shared_stats',
]
