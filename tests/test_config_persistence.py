#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配置链路修复回归测试。

覆盖以下修复点：
- Bug#1: ConfigManager.upsert_profile / delete_profile 自动落盘
- Bug#4/#5: 配置损坏时自动备份 + 写默认值
- Bug#6: from_dict 缺 active_profile 时回退到第一个 profile
"""

from __future__ import absolute_import
from __future__ import print_function

import json
import os

import pytest

from maxagent.config import (
    AppConfig,
    BUILTIN_PROFILES,
    ConfigManager,
    LLMProfile,
)


@pytest.fixture
def cfg_path(tmp_path):
    return str(tmp_path / 'config.json')


class TestUpsertAutoSave:
    """Bug#1：upsert_profile / delete_profile 必须自动落盘。"""

    def test_upsert_persists_without_explicit_save(self, cfg_path):
        m1 = ConfigManager(config_path=cfg_path)
        m1.upsert_profile(LLMProfile(name='AutoSave', api_key='k1'))
        # 故意不调 m1.save()
        # 重新打开，必须能读到刚写的 profile
        m2 = ConfigManager(config_path=cfg_path)
        prof = m2.get_profile('AutoSave')
        assert prof is not None, 'upsert_profile 没有自动落盘'
        assert prof.api_key == 'k1'

    def test_delete_persists_without_explicit_save(self, cfg_path):
        m1 = ConfigManager(config_path=cfg_path)
        m1.upsert_profile(LLMProfile(name='ToDelete'))
        # 这里不调 save，依赖 upsert 自动落盘
        m1.delete_profile('ToDelete')
        m2 = ConfigManager(config_path=cfg_path)
        assert m2.get_profile('ToDelete') is None, (
            'delete_profile 没有自动落盘'
        )

    def test_upsert_existing_profile_is_persisted(self, cfg_path):
        m1 = ConfigManager(config_path=cfg_path)
        m1.upsert_profile(LLMProfile(name='Same', model='v1'))
        m1.upsert_profile(LLMProfile(name='Same', model='v2'))
        m2 = ConfigManager(config_path=cfg_path)
        prof = m2.get_profile('Same')
        assert prof.model == 'v2'


class TestCorruptBackup:
    """Bug#4/#5：损坏的配置文件被备份 + 写默认值。"""

    def test_corrupt_file_is_backed_up(self, cfg_path):
        with open(cfg_path, 'w', encoding='utf-8') as fh:
            fh.write('{this is not valid json')
        ConfigManager(config_path=cfg_path)
        # 应该把损坏文件挪到 .corrupt
        assert os.path.exists(cfg_path + '.corrupt')

    def test_default_written_after_corrupt(self, cfg_path):
        with open(cfg_path, 'w', encoding='utf-8') as fh:
            fh.write('garbage')
        ConfigManager(config_path=cfg_path)
        # 默认值应当已经写回
        assert os.path.exists(cfg_path)
        with open(cfg_path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        # 应该至少有内置预设的数量
        assert len(data['profiles']) >= len(BUILTIN_PROFILES)


class TestFromDictFallback:
    """Bug#6：缺 active_profile 时不应指向不存在的 'Default'。"""

    def test_missing_active_uses_first_profile(self):
        cfg = AppConfig.from_dict({
            'profiles': [
                {'name': 'OnlyOne', 'model': 'm'},
            ],
            # 故意不传 active_profile
        })
        assert cfg.active_profile == 'OnlyOne'
        assert cfg.get_active_profile() is not None

    def test_empty_profiles_keeps_default_marker(self):
        # 没有任何 profile 时回退到 "Default"，但 get_active_profile 返回 None
        cfg = AppConfig.from_dict({})
        assert cfg.active_profile == 'Default'
        assert cfg.get_active_profile() is None

    def test_explicit_active_is_respected(self):
        cfg = AppConfig.from_dict({
            'active_profile': 'X',
            'profiles': [
                {'name': 'X'},
                {'name': 'Y'},
            ],
        })
        assert cfg.active_profile == 'X'


class TestSetActivePersists:
    """对照组：set_active_profile 一直以来就会自动 save，确保没回归。"""

    def test_set_active_writes_disk(self, cfg_path):
        m1 = ConfigManager(config_path=cfg_path)
        names = m1.list_profile_names()
        # 切换到非默认的一个
        target = names[1] if len(names) > 1 else names[0]
        m1.set_active_profile(target)
        m2 = ConfigManager(config_path=cfg_path)
        assert m2.get_active_profile_name() == target
