#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""release/ci/publish_gitee.py 单元测试。

不发起真实网络请求，全部用 monkeypatch 拦截 _gitee_request。
"""

from __future__ import absolute_import
from __future__ import print_function

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PUBLISH_DIR = _PROJECT_ROOT / 'release' / 'ci'
if str(_PUBLISH_DIR) not in sys.path:
    sys.path.insert(0, str(_PUBLISH_DIR))


@pytest.fixture
def publish_gitee_mod():
    """导入 publish_gitee 模块（按需，避免污染全局）。"""
    import publish_gitee  # type: ignore
    return publish_gitee


class TestPublishGiteeArgs:
    """验证 CLI 参数处理与早退路径。"""

    def test_help_works(self, publish_gitee_mod):
        """--help 不应崩溃。"""
        with pytest.raises(SystemExit) as exc_info:
            publish_gitee_mod.main(['--help'])
        assert exc_info.value.code == 0

    def test_missing_token_returns_1(self, publish_gitee_mod, monkeypatch):
        """缺 GITEE_TOKEN 应早退 code 1。"""
        monkeypatch.delenv('GITEE_TOKEN', raising=False)
        ret = publish_gitee_mod.main(['--version', '0.4.0'])
        assert ret == 1

    def test_missing_mzp_returns_3(self, publish_gitee_mod, monkeypatch, tmp_path):
        """token 有但 mzp 找不到应返回 3。"""
        monkeypatch.setenv('GITEE_TOKEN', 'fake_token')
        empty_glob = str(tmp_path / 'nothing-*.mzp')
        ret = publish_gitee_mod.main([
            '--version', '0.4.0',
            '--mzp-glob', empty_glob,
        ])
        assert ret == 3

    def test_missing_notes_file_returns_3(self, publish_gitee_mod, monkeypatch, tmp_path):
        """notes 文件指定但不存在应返回 3。"""
        monkeypatch.setenv('GITEE_TOKEN', 'fake_token')
        # 造一个 mzp 占位文件
        fake_mzp = tmp_path / 'maxagent-0.4.0.mzp'
        fake_mzp.write_bytes(b'PK\x03\x04fake')
        ret = publish_gitee_mod.main([
            '--version', '0.4.0',
            '--mzp-glob', str(fake_mzp),
            '--notes-file', str(tmp_path / 'nope.md'),
        ])
        assert ret == 3


class TestPublishGiteeHelpers:
    """验证内部 helper 函数。"""

    def test_read_version_from_module(self, publish_gitee_mod):
        """能正确读到 release/version.py 中的 __version__。"""
        ver = publish_gitee_mod._read_version_from_module()
        # 简单语义化检查
        parts = ver.split('.')
        assert len(parts) >= 3
        for p in parts[:3]:
            assert p.isdigit(), '版本号段非数字: {}'.format(ver)

    def test_generate_default_notes_includes_files(self, publish_gitee_mod, tmp_path):
        """生成的 Release Notes 包含每个 mzp 的文件名与大小。"""
        f1 = tmp_path / 'maxagent-0.4.0.mzp'
        f1.write_bytes(b'A' * 1024 * 100)  # 100 KB
        notes = publish_gitee_mod._generate_default_notes('0.4.0', [f1])
        assert 'MaxAgent v0.4.0' in notes
        assert 'maxagent-0.4.0.mzp' in notes
        assert '3ds Max 2023 ~ 2027' in notes
        assert 'Python 3.9' in notes

    def test_build_multipart_body_format(self, publish_gitee_mod, tmp_path):
        """multipart body 格式正确：含 boundary、文件名、文件内容。"""
        f = tmp_path / 'sample.mzp'
        content = b'PK\x03\x04test_content_payload'
        f.write_bytes(content)

        body, content_type = publish_gitee_mod._build_multipart_body(f)

        # content-type 必须含 boundary
        assert content_type.startswith('multipart/form-data; boundary=')
        boundary = content_type.split('boundary=', 1)[1]

        # body 应同时包含 boundary 标识与文件名与原始字节
        assert boundary.encode('utf-8') in body
        assert b'sample.mzp' in body
        assert content in body
        # body 应以 --boundary-- 结尾
        assert body.rstrip(b'\r\n').endswith(('--' + boundary + '--').encode('utf-8'))


class TestPublishGiteeFullFlow:
    """端到端流程（拦截网络）。"""

    def test_main_full_flow_with_mocked_api(
        self, publish_gitee_mod, monkeypatch, tmp_path,
    ):
        """模拟一次完整发布：创建 release + 上传 1 个附件，全部成功。"""
        monkeypatch.setenv('GITEE_TOKEN', 'fake_token')
        monkeypatch.setenv('GITEE_OWNER', 'cmqll')
        monkeypatch.setenv('GITEE_REPO', 'max_agent')

        # 造一个真实的 mzp 占位文件
        fake_mzp = tmp_path / 'maxagent-0.4.0.mzp'
        fake_mzp.write_bytes(b'PK\x03\x04fake_mzp_content' * 100)

        # 拦截 _gitee_request 模拟成功
        calls = []

        def fake_request(method, path, token, payload=None, query=None,
                         raw_body=None, extra_headers=None, timeout=60):
            calls.append({
                'method': method,
                'path': path,
                'has_payload': payload is not None,
                'has_raw_body': raw_body is not None,
            })
            # /releases POST -> 返回 release id
            if path.endswith('/releases'):
                return {
                    'id': 12345,
                    'tag_name': payload['tag_name'],
                    'html_url': 'https://gitee.com/cmqll/max_agent/releases/v0.4.0',
                }
            # 附件上传 -> 返回成功
            if 'attach_files' in path:
                return {'browser_download_url': 'https://gitee.com/.../sample.mzp'}
            return {}

        monkeypatch.setattr(publish_gitee_mod, '_gitee_request', fake_request)

        ret = publish_gitee_mod.main([
            '--version', '0.4.0',
            '--mzp-glob', str(fake_mzp),
        ])
        assert ret == 0
        # 至少调用了 2 次 API：创建 release + 上传附件
        assert len(calls) >= 2
        # 第一次必须是创建 release
        assert calls[0]['method'] == 'POST'
        assert calls[0]['path'].endswith('/releases')
        assert calls[0]['has_payload'] is True
        # 后面必须有附件上传
        attach_calls = [c for c in calls if 'attach_files' in c['path']]
        assert len(attach_calls) >= 1
        assert attach_calls[0]['has_raw_body'] is True
