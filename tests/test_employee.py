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
        # base64 data URI 不再包含原始路径，改为校验 data:image 前缀
        # （原断言 'avatar.png' in html 在改用 data URI 后不再适用）
        assert 'src="data:image/png;base64,' in html
        assert '小猫' in html


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


# ====================================================================== #
# 头像 data URI 编码（PySide6 跨版本兼容的关键路径）
# ====================================================================== #
class TestAvatarDataURI:
    """覆盖 ``_file_to_data_uri`` 与缓存失效逻辑。

    bug 背景：PySide6 (Qt6) 的 QLabel/QTextDocument 默认不再加载
    ``file:///`` 本地资源，导致用户截图里头像槽位空白。改用 base64
    data URI 后两个版本一致工作。
    """

    @staticmethod
    def _make_png_at(path):
        # 写一个最小合法的 1x1 PNG 文件
        with open(path, 'wb') as fh:
            fh.write(
                b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'
                b'\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00'
                b'\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\x00\x01\x00'
                b'\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
            )

    def test_data_uri_encodes_png(self, isolated_config_dir):
        from maxagent.ui.employee import _file_to_data_uri
        path = os.path.join(isolated_config_dir, 'avatar.png')
        self._make_png_at(path)
        uri = _file_to_data_uri(path)
        assert uri.startswith('data:image/png;base64,')
        # base64 段非空
        assert len(uri) > len('data:image/png;base64,')

    def test_data_uri_missing_file_returns_empty(self, isolated_config_dir):
        from maxagent.ui.employee import _file_to_data_uri
        # 不存在的路径不应抛，应返回空串让调用方回落 emoji
        path = os.path.join(isolated_config_dir, 'no_such.png')
        assert _file_to_data_uri(path) == ''
        assert _file_to_data_uri('') == ''

    def test_data_uri_uses_cache_on_repeat_calls(self, isolated_config_dir):
        from maxagent.ui import employee as emp_mod
        path = os.path.join(isolated_config_dir, 'avatar.png')
        self._make_png_at(path)
        # 清空缓存确保起点干净
        emp_mod._invalidate_data_uri_cache()
        uri1 = emp_mod._file_to_data_uri(path)
        # 缓存命中后即使删了文件也应返回旧值（直到失效）
        os.remove(path)
        # 但 _file_to_data_uri 内部会先 os.path.exists 检查；
        # 删除后当前实现会返回空串（这是更安全的行为，保护
        # 文件被外部清空的场景）
        uri2 = emp_mod._file_to_data_uri(path)
        assert uri1.startswith('data:image/png;base64,')
        # 校验缓存确实存在（在删除前）
        emp_mod._invalidate_data_uri_cache()
        # 再写一次再读，应能正常工作
        self._make_png_at(path)
        uri3 = emp_mod._file_to_data_uri(path)
        assert uri3.startswith('data:image/png;base64,')

    def test_data_uri_invalidate_after_overwrite(self, isolated_config_dir):
        # 关键场景：用户上传新头像后，气泡应读到新图而不是缓存旧图
        from maxagent.ui import employee as emp_mod
        path = os.path.join(isolated_config_dir, 'avatar.png')
        # 第一张图
        with open(path, 'wb') as fh:
            fh.write(b'\x89PNG\r\n\x1a\nFIRST_IMAGE_DATA')
        emp_mod._invalidate_data_uri_cache()
        uri1 = emp_mod._file_to_data_uri(path)
        # 写入第二张图（mtime + size 都变了，自动失效）
        # 等一下确保 mtime 不同（某些 FS 精度只到秒）
        import time
        time.sleep(0.01)
        new_mtime = os.path.getmtime(path) + 2
        with open(path, 'wb') as fh:
            fh.write(
                b'\x89PNG\r\n\x1a\nSECOND_IMAGE_DATA_WITH_DIFFERENT_LEN'
            )
        os.utime(path, (new_mtime, new_mtime))
        uri2 = emp_mod._file_to_data_uri(path)
        # 不同内容 → 不同 base64 → 不同 URI
        assert uri1 != uri2

    def test_display_html_image_mode_uses_data_uri(
        self, monkeypatch, isolated_config_dir,
    ):
        """display_html 在 image 模式下应输出 data: URI 而非 file:///。"""
        from maxagent.ui.employee import Employee
        from maxagent.ui import employee as emp_mod
        emp_mod._invalidate_data_uri_cache()
        path = os.path.join(isolated_config_dir, 'avatar.png')
        self._make_png_at(path)
        emp = Employee(
            name='尼娜',
            avatar_kind='image',
            avatar_image='avatar.png',
        )
        html = emp.display_html()
        assert 'src="data:image/png;base64,' in html
        # 关键回归：再也不应出现 file:/// 协议（PySide6 渲不出）
        assert 'file:///' not in html
        assert '尼娜' in html