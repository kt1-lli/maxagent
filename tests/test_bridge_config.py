#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bridge 配置字段持久化测试。"""

from __future__ import absolute_import
from __future__ import print_function

import unittest

from maxagent.config import AppConfig


class TestBridgeConfigDefaults(unittest.TestCase):

    def test_default_disabled_with_safe_defaults(self):
        cfg = AppConfig()
        self.assertFalse(cfg.bridge_enabled)
        self.assertEqual(cfg.bridge_host, '127.0.0.1')
        self.assertEqual(cfg.bridge_port, 7003)
        self.assertEqual(cfg.bridge_token, '')
        self.assertTrue(cfg.bridge_dispatch_enabled)
        self.assertEqual(cfg.bridge_dispatch_max_rounds, 20)
        self.assertEqual(cfg.bridge_dispatch_timeout_sec, 300)


class TestBridgeConfigRoundtrip(unittest.TestCase):

    def test_to_dict_and_from_dict(self):
        cfg = AppConfig()
        cfg.bridge_enabled = True
        cfg.bridge_host = '0.0.0.0'
        cfg.bridge_port = 17003
        cfg.bridge_token = 'my-secret'
        cfg.bridge_dispatch_enabled = False
        cfg.bridge_dispatch_max_rounds = 5
        cfg.bridge_dispatch_timeout_sec = 60
        d = cfg.to_dict()
        self.assertTrue(d['bridge_enabled'])
        self.assertEqual(d['bridge_port'], 17003)
        cfg2 = AppConfig.from_dict(d)
        self.assertEqual(cfg2.bridge_enabled, True)
        self.assertEqual(cfg2.bridge_host, '0.0.0.0')
        self.assertEqual(cfg2.bridge_port, 17003)
        self.assertEqual(cfg2.bridge_token, 'my-secret')
        self.assertFalse(cfg2.bridge_dispatch_enabled)
        self.assertEqual(cfg2.bridge_dispatch_max_rounds, 5)
        self.assertEqual(cfg2.bridge_dispatch_timeout_sec, 60)


class TestBridgeConfigSanitization(unittest.TestCase):

    def test_invalid_port_falls_back_to_default(self):
        cfg = AppConfig.from_dict({'bridge_port': 99999})
        self.assertEqual(cfg.bridge_port, 7003)

    def test_negative_port_falls_back(self):
        cfg = AppConfig.from_dict({'bridge_port': -1})
        self.assertEqual(cfg.bridge_port, 7003)

    def test_max_rounds_clamped(self):
        cfg = AppConfig.from_dict({'bridge_dispatch_max_rounds': 999})
        self.assertEqual(cfg.bridge_dispatch_max_rounds, 100)
        # 0 / None 视为"未提供"，回落默认值 20
        cfg2 = AppConfig.from_dict({'bridge_dispatch_max_rounds': 0})
        self.assertEqual(cfg2.bridge_dispatch_max_rounds, 20)
        # 负数被夹紧到下界 1
        cfg3 = AppConfig.from_dict({'bridge_dispatch_max_rounds': -5})
        self.assertEqual(cfg3.bridge_dispatch_max_rounds, 1)

    def test_timeout_clamped(self):
        cfg = AppConfig.from_dict({'bridge_dispatch_timeout_sec': 999999})
        self.assertEqual(cfg.bridge_dispatch_timeout_sec, 3600)
        # 0 视为"未提供"，回落默认 300
        cfg2 = AppConfig.from_dict({'bridge_dispatch_timeout_sec': 0})
        self.assertEqual(cfg2.bridge_dispatch_timeout_sec, 300)
        # 1 被夹紧到下界 10
        cfg3 = AppConfig.from_dict({'bridge_dispatch_timeout_sec': 1})
        self.assertEqual(cfg3.bridge_dispatch_timeout_sec, 10)

    def test_bad_types_fallback(self):
        cfg = AppConfig.from_dict({
            'bridge_port': 'abc',
            'bridge_dispatch_max_rounds': 'lots',
            'bridge_dispatch_timeout_sec': None,
        })
        self.assertEqual(cfg.bridge_port, 7003)
        self.assertEqual(cfg.bridge_dispatch_max_rounds, 20)
        self.assertEqual(cfg.bridge_dispatch_timeout_sec, 300)

    def test_old_config_without_bridge_fields_still_loads(self):
        # 老版本配置文件没有 bridge_* 字段：必须降级到默认值
        cfg = AppConfig.from_dict({
            'version': 1,
            'profiles': [],
            'active_profile': '',
        })
        self.assertFalse(cfg.bridge_enabled)
        self.assertEqual(cfg.bridge_port, 7003)


if __name__ == '__main__':
    unittest.main()
