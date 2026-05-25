#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gitee Release 自动发布脚本。

被 CI（GitHub Actions / 工蜂蓝盾）的 publish 阶段调用，
把构建产出的 ``.mzp`` 文件上传到 Gitee 仓库的 Release 页面。

零外部依赖
==========
仅使用标准库 ``urllib`` + ``json``，避免在 CI 节点装 ``requests``
等额外包，与项目"零外部依赖"原则一致。

环境变量
========
- ``GITEE_TOKEN``  : 必填，Gitee 个人访问令牌（建议作 CI secret 注入）
- ``GITEE_OWNER``  : 仓库 owner（默认 ``cmqll``）
- ``GITEE_REPO``   : 仓库名（默认 ``max_agent``）

CLI 参数
========
- ``--version``    : 版本号（不含 v 前缀），未提供时从 release/version.py 读取
- ``--mzp-glob``   : mzp 产物 glob 模式（默认 ``release/dist/*.mzp``）
- ``--notes-file`` : Release Notes 文件路径（可选，默认自动生成简短描述）
- ``--draft``      : 创建草稿（默认 False）
- ``--prerelease`` : 标记为预发布（默认 False）

退出码
======
- 0 : 成功
- 1 : 参数缺失或环境变量缺失
- 2 : 网络 / API 错误
- 3 : 文件未找到

参考: https://gitee.com/api/v5/swagger
"""

from __future__ import absolute_import
from __future__ import print_function

import argparse
import glob
import json
import logging
import mimetypes
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

LOG = logging.getLogger('publish_gitee')

GITEE_API_BASE = 'https://gitee.com/api/v5'
DEFAULT_OWNER = 'cmqll'
DEFAULT_REPO = 'max_agent'


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format='[%(levelname)s] %(message)s',
    )


def _read_version_from_module() -> str:
    """从 release/version.py 读取 __version__。"""
    repo_root = Path(__file__).resolve().parent.parent.parent
    version_file = repo_root / 'release' / 'version.py'
    if not version_file.is_file():
        raise FileNotFoundError('release/version.py 不存在: {}'.format(version_file))
    namespace: Dict[str, Any] = {}
    exec(compile(version_file.read_text(encoding='utf-8'), str(version_file), 'exec'), namespace)
    return namespace['__version__']


def _gitee_request(
    method: str,
    path: str,
    token: str,
    payload: Optional[Dict[str, Any]] = None,
    query: Optional[Dict[str, str]] = None,
    raw_body: Optional[bytes] = None,
    extra_headers: Optional[Dict[str, str]] = None,
    timeout: int = 60,
) -> Dict[str, Any]:
    """统一的 Gitee API 请求工具。

    :param method: HTTP 方法
    :param path:   API 路径片段（不含 base url）
    :param token:  Gitee access_token（作为 query 参数传，符合 Gitee 习惯）
    :param payload: JSON body（与 raw_body 互斥）
    :param query:  额外的 query 参数
    :param raw_body: 原始 body 字节（用于 multipart 上传）
    :param extra_headers: 额外的 HTTP headers
    :return: 解析后的 JSON dict（无 body 时返回空 dict）
    """
    qs = dict(query or {})
    qs['access_token'] = token
    full_url = '{}{}?{}'.format(GITEE_API_BASE, path, urllib.parse.urlencode(qs))

    headers = {'User-Agent': 'maxagent-publish-gitee/1.0'}
    if extra_headers:
        headers.update(extra_headers)

    body: Optional[bytes] = None
    if raw_body is not None:
        body = raw_body
    elif payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        headers['Content-Type'] = 'application/json; charset=utf-8'

    req = urllib.request.Request(full_url, data=body, method=method, headers=headers)
    LOG.debug('%s %s', method, full_url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            if not data:
                return {}
            return json.loads(data.decode('utf-8'))
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode('utf-8', errors='replace') if exc.fp else ''
        LOG.error('Gitee API %s %s 失败: HTTP %s\n响应: %s',
                  method, path, exc.code, err_body)
        raise


def _create_release(
    owner: str, repo: str, token: str,
    tag_name: str, name: str, body: str,
    draft: bool, prerelease: bool,
) -> Dict[str, Any]:
    """创建 Release。"""
    LOG.info('创建 Gitee Release: %s/%s @ %s', owner, repo, tag_name)
    payload = {
        'tag_name': tag_name,
        'name': name,
        'body': body,
        'prerelease': prerelease,
        'target_commitish': 'master',
    }
    # Gitee API 没有 draft 字段，先记录
    if draft:
        LOG.warning('Gitee API 不支持 draft 标志，将创建为正式 Release')

    return _gitee_request(
        'POST',
        '/repos/{}/{}/releases'.format(owner, repo),
        token,
        payload=payload,
    )


def _build_multipart_body(
    file_path: Path,
    field_name: str = 'file',
) -> tuple:
    """手工构造 multipart/form-data body。

    :return: (body_bytes, content_type_header)
    """
    boundary = '----maxagent-{}'.format(uuid.uuid4().hex)
    mime, _ = mimetypes.guess_type(str(file_path))
    if mime is None:
        mime = 'application/octet-stream'

    chunks: List[bytes] = []
    chunks.append(('--{}\r\n'.format(boundary)).encode('utf-8'))
    chunks.append((
        'Content-Disposition: form-data; name="{}"; filename="{}"\r\n'
        'Content-Type: {}\r\n\r\n'
    ).format(field_name, file_path.name, mime).encode('utf-8'))
    chunks.append(file_path.read_bytes())
    chunks.append('\r\n--{}--\r\n'.format(boundary).encode('utf-8'))

    body = b''.join(chunks)
    content_type = 'multipart/form-data; boundary={}'.format(boundary)
    return body, content_type


def _upload_attachment(
    owner: str, repo: str, token: str,
    release_id: int, file_path: Path,
) -> Dict[str, Any]:
    """上传附件到指定 Release。"""
    LOG.info('上传附件 %s (%.2f MB)',
             file_path.name, file_path.stat().st_size / 1024 / 1024)
    body, content_type = _build_multipart_body(file_path)
    return _gitee_request(
        'POST',
        '/repos/{}/{}/releases/{}/attach_files'.format(owner, repo, release_id),
        token,
        raw_body=body,
        extra_headers={'Content-Type': content_type},
        timeout=300,  # 大文件上传放宽超时
    )


def _generate_default_notes(version: str, mzp_files: List[Path]) -> str:
    """生成默认 Release Notes（无外部 notes 文件时使用）。"""
    lines = [
        '# MaxAgent v{}'.format(version),
        '',
        '## 兼容性',
        '',
        '- 3ds Max 2022 ~ 2027',
        '- Python 3.7 / 3.9 / 3.10 / 3.11 / 3.13（5 套 ABI 内置于单包）',
        '',
        '## 安装',
        '',
        '将下方 `.mzp` 文件直接拖入 3ds Max 视口即可自动安装。',
        '',
        '## 产物',
        '',
    ]
    for f in mzp_files:
        size_mb = f.stat().st_size / 1024 / 1024
        lines.append('- `{}` ({:.2f} MB)'.format(f.name, size_mb))
    return '\n'.join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    _setup_logging()

    parser = argparse.ArgumentParser(description='Gitee Release 自动发布')
    parser.add_argument('--version', default=None,
                        help='版本号（不含 v 前缀），默认从 release/version.py 读取')
    parser.add_argument('--mzp-glob', default='release/dist/*.mzp',
                        help='mzp 产物 glob 模式')
    parser.add_argument('--notes-file', default=None,
                        help='Release Notes Markdown 文件路径（可选）')
    parser.add_argument('--draft', action='store_true', help='创建草稿（Gitee 实际不支持）')
    parser.add_argument('--prerelease', action='store_true', help='标记为预发布')
    args = parser.parse_args(argv)

    # 读 token
    token = os.environ.get('GITEE_TOKEN')
    if not token:
        LOG.error('环境变量 GITEE_TOKEN 未设置')
        return 1

    owner = os.environ.get('GITEE_OWNER', DEFAULT_OWNER)
    repo = os.environ.get('GITEE_REPO', DEFAULT_REPO)

    # 读版本号
    try:
        version = args.version or _read_version_from_module()
    except FileNotFoundError as exc:
        LOG.error('%s', exc)
        return 3

    tag_name = 'v{}'.format(version)
    LOG.info('目标: %s/%s @ %s', owner, repo, tag_name)

    # 找 mzp
    mzp_files = sorted(Path(p) for p in glob.glob(args.mzp_glob))
    if not mzp_files:
        LOG.error('未找到 mzp 文件: %s', args.mzp_glob)
        return 3
    LOG.info('找到 %d 个 mzp 文件', len(mzp_files))

    # Notes
    if args.notes_file:
        notes_path = Path(args.notes_file)
        if not notes_path.is_file():
            LOG.error('notes 文件不存在: %s', notes_path)
            return 3
        body = notes_path.read_text(encoding='utf-8')
    else:
        body = _generate_default_notes(version, mzp_files)

    # 创建 Release
    try:
        release = _create_release(
            owner, repo, token,
            tag_name=tag_name,
            name='MaxAgent {}'.format(tag_name),
            body=body,
            draft=args.draft,
            prerelease=args.prerelease,
        )
    except urllib.error.HTTPError:
        LOG.error('创建 Release 失败')
        return 2

    release_id = release.get('id')
    if not release_id:
        LOG.error('Release 创建响应缺 id 字段: %s', release)
        return 2
    LOG.info('Release 已创建: id=%s, html_url=%s',
             release_id, release.get('html_url', ''))

    # 上传附件
    failed: List[str] = []
    for mzp in mzp_files:
        try:
            _upload_attachment(owner, repo, token, release_id, mzp)
            LOG.info('✅ %s 上传成功', mzp.name)
        except urllib.error.HTTPError:
            failed.append(mzp.name)
            LOG.error('❌ %s 上传失败', mzp.name)

    if failed:
        LOG.error('部分附件上传失败: %s', failed)
        return 2

    LOG.info('全部完成 ✅ Release: %s', release.get('html_url', ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
