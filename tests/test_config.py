#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试 ConfigManager：profile CRUD + base64 混淆 + 默认预设 + 容错。"""

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


class TestConfigManagerBootstrap:
    def test_first_run_creates_builtin_profiles(self, cfg_path):
        m = ConfigManager(config_path=cfg_path)
        names = m.list_profile_names()
        # 至少包含所有内置预设
        for prof in BUILTIN_PROFILES:
            assert prof['name'] in names

    def test_get_active_profile(self, cfg_path):
        m = ConfigManager(config_path=cfg_path)
        prof = m.get_active_profile()
        assert prof is not None
        assert prof.name == m.get_active_profile_name()


class TestProfileCRUD:
    def test_upsert_then_get(self, cfg_path):
        m = ConfigManager(config_path=cfg_path)
        m.upsert_profile(LLMProfile(
            name='Custom1',
            base_url='http://x',
            api_key='secret',
            model='m1',
        ))
        m.save()
        # 重新读
        m2 = ConfigManager(config_path=cfg_path)
        prof = m2.get_profile('Custom1')
        assert prof is not None
        assert prof.api_key == 'secret'
        assert prof.model == 'm1'

    def test_set_active_profile_validates(self, cfg_path):
        m = ConfigManager(config_path=cfg_path)
        with pytest.raises(ValueError):
            m.set_active_profile('not_exist')

    def test_delete_active_rejected(self, cfg_path):
        m = ConfigManager(config_path=cfg_path)
        active = m.get_active_profile_name()
        with pytest.raises(ValueError):
            m.delete_profile(active)


class TestApiKeyObfuscation:
    def test_api_key_not_plaintext_on_disk(self, cfg_path):
        m = ConfigManager(config_path=cfg_path)
        m.upsert_profile(LLMProfile(
            name='ObfTest', api_key='my_secret_key_12345',
        ))
        m.save()
        with open(cfg_path, 'r', encoding='utf-8') as fh:
            raw = fh.read()
        # 直接读盘不应看到明文 key
        assert 'my_secret_key_12345' not in raw
        assert 'b64:' in raw

    def test_api_key_decoded_on_load(self, cfg_path):
        m = ConfigManager(config_path=cfg_path)
        m.upsert_profile(LLMProfile(
            name='RT', api_key='abcdEFG',
        ))
        m.save()
        m2 = ConfigManager(config_path=cfg_path)
        prof = m2.get_profile('RT')
        assert prof.api_key == 'abcdEFG'


class TestPriceFields:
    def test_default_zero(self):
        p = LLMProfile()
        assert p.price_input_per_1m == 0.0
        assert p.price_output_per_1m == 0.0

    def test_round_trip_serialize(self, cfg_path):
        m = ConfigManager(config_path=cfg_path)
        m.upsert_profile(LLMProfile(
            name='Priced',
            price_input_per_1m=0.27,
            price_output_per_1m=1.10,
        ))
        m.save()
        m2 = ConfigManager(config_path=cfg_path)
        prof = m2.get_profile('Priced')
        assert prof.price_input_per_1m == pytest.approx(0.27)
        assert prof.price_output_per_1m == pytest.approx(1.10)


class TestCorruptConfigRecovery:
    def test_corrupt_config_falls_back(self, cfg_path):
        # 写入垃圾
        with open(cfg_path, 'w', encoding='utf-8') as fh:
            fh.write('{not json')
        m = ConfigManager(config_path=cfg_path)
        # 应回退到默认预设
        names = m.list_profile_names()
        assert len(names) >= len(BUILTIN_PROFILES)


class TestNewFields:
    def test_tool_result_max_bytes_default(self):
        p = LLMProfile()
        assert p.tool_result_max_bytes == 16384

    def test_max_tool_loops_default(self):
        p = LLMProfile()
        assert p.max_tool_loops == 40

    def test_max_history_tokens_default(self):
        p = LLMProfile()
        assert p.max_history_tokens == 32000

    def test_unknown_field_filtered(self):
        # 兼容：从字典恢复时未知字段被忽略而非抛
        p = LLMProfile.from_dict({
            'name': 'X',
            'unknown_xxx': 'should_drop',
            'model': 'm',
        })
        assert p.name == 'X'
        assert p.model == 'm'


class TestDeepSeekPreset2026:
    """DeepSeek 预设按 2026/05 官方文档刷新：根域名 + v4-flash。"""

    def test_deepseek_uses_root_domain(self):
        ds = next(p for p in BUILTIN_PROFILES if p['name'] == 'DeepSeek')
        # 官方文档首推根域名（OpenAI 兼容入口）
        assert ds['base_url'] == 'https://api.deepseek.com'
        # 不应再硬编码 /v1
        assert not ds['base_url'].endswith('/v1')

    def test_deepseek_default_model_is_v4_flash(self):
        ds = next(p for p in BUILTIN_PROFILES if p['name'] == 'DeepSeek')
        # deepseek-chat / deepseek-reasoner 将于 2026/07/24 弃用
        assert ds['model'] == 'deepseek-v4-flash'
        assert ds['model'] not in ('deepseek-chat', 'deepseek-reasoner')
