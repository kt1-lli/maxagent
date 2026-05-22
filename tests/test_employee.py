#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试 Employee（员工档案）模块。

覆盖：
- 配置字段的默认值与持久化（含中文 / emoji 名字）
- Employee.from_config / save 的往返一致性
- display_html 对 emoji / image / 缺失图片的处理
- 名字 HTML 转义防止 XSS
- 头像图片路径解析
"""

from __future__ import absolute_import
from __future__ import print_function

import os

import pytest

from maxagent.config import AppConfig
from maxagent.config import ConfigManager


@pytest.fixture
def cfg_path(tmp_path):
    return str(tmp_path / 'config.json')


@pytest.fixture
def isolated_config_dir(tmp_path, monkeypatch):
    """让 get_config_dir() 指向 tmp_path，避免污染真实配置目录。"""
    monkeypatch.setenv('MAXAGENT_DATA_DIR', str(tmp_path))
    yield str(tmp_path)


# ====================================================================== #
# 配置字段层
# ====================================================================== #
class TestConfigFields:
    def test_default_employee_name(self):
        cfg = AppConfig()
        assert cfg.employee_name == '助手'

    def test_default_avatar_kind_emoji(self):
        cfg = AppConfig()
        assert cfg.employee_avatar_kind == 'emoji'

    def test_default_avatar_emoji(self):
        cfg = AppConfig()
        assert cfg.employee_avatar_emoji == '🤖'

    def test_default_avatar_image_empty(self):
        cfg = AppConfig()
        assert cfg.employee_avatar_image == ''

    def test_round_trip_emoji_kind(self, cfg_path):
        m = ConfigManager(config_path=cfg_path)
        m.config.employee_name = '小猫'
        m.config.employee_avatar_kind = 'emoji'
        m.config.employee_avatar_emoji = '🐱'
        m.save()

        m2 = ConfigManager(config_path=cfg_path)
        assert m2.config.employee_name == '小猫'
        assert m2.config.employee_avatar_kind == 'emoji'
        assert m2.config.employee_avatar_emoji == '🐱'

    def test_round_trip_image_kind(self, cfg_path):
        m = ConfigManager(config_path=cfg_path)
        m.config.employee_avatar_kind = 'image'
        m.config.employee_avatar_image = 'avatar.png'
        m.save()

        m2 = ConfigManager(config_path=cfg_path)
        assert m2.config.employee_avatar_kind == 'image'
        assert m2.config.employee_avatar_image == 'avatar.png'

    def test_invalid_kind_falls_back_to_emoji(self, cfg_path):
        # 直接构造一个含非法 kind 的 dict 看是否被纠正
        cfg = AppConfig.from_dict({'employee_avatar_kind': 'unknown'})
        assert cfg.employee_avatar_kind == 'emoji'

    def test_empty_name_falls_back_to_default(self):
        cfg = AppConfig.from_dict({'employee_name': ''})
        assert cfg.employee_name == '助手'

    def test_whitespace_name_falls_back(self):
        cfg = AppConfig.from_dict({'employee_name': '   '})
        assert cfg.employee_name == '助手'


# ====================================================================== #
# Employee 类
# ====================================================================== #
class TestEmployeeBasics:
    def test_default_init(self):
        from maxagent.ui.employee import Employee
        emp = Employee()
        assert emp.name == '助手'
        assert emp.avatar_kind == 'emoji'
        assert emp.avatar_emoji == '🤖'

    def test_from_config_none(self):
        from maxagent.ui.employee import Employee
        emp = Employee.from_config(None)
        # None 时回落到默认，不抛
        assert emp.name == '助手'
        assert emp.avatar_kind == 'emoji'

    def test_from_config_loads_values(self, cfg_path):
        from maxagent.ui.employee import Employee
        m = ConfigManager(config_path=cfg_path)
        m.config.employee_name = '阿福'
        m.config.employee_avatar_kind = 'emoji'
        m.config.employee_avatar_emoji = '🦊'
        m.save()
        emp = Employee.from_config(m)
        assert emp.name == '阿福'
        assert emp.avatar_emoji == '🦊'

    def test_save_persists(self, cfg_path):
        from maxagent.ui.employee import Employee
        m = ConfigManager(config_path=cfg_path)
        emp = Employee(name='张三', avatar_emoji='🐧')
        emp.save(m)

        m2 = ConfigManager(config_path=cfg_path)
        assert m2.config.employee_name == '张三'
        assert m2.config.employee_avatar_emoji == '🐧'

    def test_invalid_kind_corrected(self):
        from maxagent.ui.employee import Employee
        emp = Employee(avatar_kind='wrong')
        assert emp.avatar_kind == 'emoji'

    def test_empty_name_corrected(self):
        from maxagent.ui.employee import Employee
        emp = Employee(name='')
        assert emp.name == '助手'


# ====================================================================== #
# display_html 渲染
# ====================================================================== #
class TestDisplayHTML:
    def test_emoji_html_contains_avatar_and_name(self):
        from maxagent.ui.employee import Employee
        emp = Employee(name='测试名', avatar_emoji='🦄')
        html = emp.display_html()
        assert '测试名' in html
        # emoji 可能被 _e() 兜底，但至少非空
        assert '<span' in html

    def test_html_escape_prevents_xss(self):
        from maxagent.ui.employee import Employee
        emp = Employee(name='<script>alert(1)</script>')
        html = emp.display_html()
        # 应该是转义后的实体，不能出现原始 <script>
        assert '<script>' not in html
        assert '&lt;script&gt;' in html

    def test_image_kind_with_missing_file_falls_back_to_emoji(
        self, isolated_config_dir,
    ):
        from maxagent.ui.employee import Employee
        emp = Employee(
            name='小猫',
            avatar_kind='image',
            avatar_image='nonexistent.png',
        )
        html = emp.display_html()
        # 图片不存在时应自动回落 emoji，HTML 不含 <img
        assert '<img' not in html
        assert '小猫' in html

    def test_image_kind_with_existing_file(self, isolated_config_dir):
        from maxagent.ui.employee import Employee
        # 写一个像素 PNG（实际能否被 Qt 加载不重要，路径存在即触发分支）
        avatar_path = os.path.join(isolated_config_dir, 'avatar.png')
        with open(avatar_path, 'wb') as fh:
            # 1x1 透明 PNG 最小合法字节
            fh.write(
                b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'
                b'\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00'
                b'\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\x00\x01\x00'
                b'\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
            )
        emp = Employee(
            name='小猫',
            avatar_kind='image',
            avatar_image='avatar.png',
        )
        html = emp.display_html()
        assert '<img' in html
        assert 'avatar.png' in html


# ====================================================================== #
# 头像图片路径
# ====================================================================== #
class TestAvatarPath:
    def test_emoji_kind_returns_empty(self):
        from maxagent.ui.employee import Employee
        emp = Employee(avatar_kind='emoji')
        assert emp.avatar_image_path() == ''

    def test_image_kind_no_filename_returns_empty(self):
        from maxagent.ui.employee import Employee
        emp = Employee(avatar_kind='image', avatar_image='')
        assert emp.avatar_image_path() == ''

    def test_image_kind_missing_file_returns_empty(self, isolated_config_dir):
        from maxagent.ui.employee import Employee
        emp = Employee(avatar_kind='image', avatar_image='not_here.png')
        assert emp.avatar_image_path() == ''

    def test_image_kind_existing_file_returns_path(
        self, isolated_config_dir,
    ):
        from maxagent.ui.employee import Employee
        avatar_path = os.path.join(isolated_config_dir, 'avatar.png')
        with open(avatar_path, 'wb') as fh:
            fh.write(b'not a real png but exists')
        emp = Employee(avatar_kind='image', avatar_image='avatar.png')
        assert emp.avatar_image_path() == avatar_path


# ====================================================================== #
# 工具函数：remove_avatar_image
# ====================================================================== #
class TestRemoveAvatar:
    def test_remove_existing_file(self, isolated_config_dir):
        from maxagent.ui.employee import remove_avatar_image
        path = os.path.join(isolated_config_dir, 'avatar.png')
        with open(path, 'wb') as fh:
            fh.write(b'x')
        assert remove_avatar_image() is True
        assert not os.path.exists(path)

    def test_remove_nonexistent_returns_false(self, isolated_config_dir):
        from maxagent.ui.employee import remove_avatar_image
        # 没有 avatar.png 时调用应返回 False，不抛
        assert remove_avatar_image() is False
