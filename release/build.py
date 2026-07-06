#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MaxAgent 打包入口（开源版：源码直出 mzp）。

不做 Cython / PyArmor / py_compile，也不再按 ABI 分目录。产物结构::

    dist/maxagent-X.Y.Z.mzp
      ├── mzp.run
      ├── mzp_install.ms
      ├── macros/MaxAgent-Macros.mcr
      └── runtime/maxagent/...        # 单份纯 .py 源码

用法::

    python release/build.py                     # 完整流程（复制源码 + 打包）
    python release/build.py --version 1.2.3     # 同时更新 version.py
    python release/build.py --pack-only         # 仅重新打包（复用 build_cache/）
    python release/build.py --dry-run           # 只打印计划
    python release/build.py --verbose
"""

from __future__ import absolute_import

import argparse
import importlib.util
import logging
import re
import shutil
import sys
import zipfile
from pathlib import Path
from typing import List, Optional, Sequence


# 本脚本所在目录 = release/
RELEASE_DIR = Path(__file__).resolve().parent
# 仓库根 = release/ 的父目录
REPO_ROOT = RELEASE_DIR.parent
# 源包目录（绝不修改）
SOURCE_PKG_DIR = REPO_ROOT / 'maxagent'
# 中间产物 / 最终产物
BUILD_CACHE_DIR = RELEASE_DIR / 'build_cache'
STAGE_PKG_DIR = BUILD_CACHE_DIR / 'maxagent'
DIST_DIR = RELEASE_DIR / 'dist'


LOG = logging.getLogger('maxagent.release.build')


# -------------------------- 通用工具函数 --------------------------


def _setup_logging(verbose: bool = False) -> None:
    """配置 logger。"""
    level = logging.DEBUG if verbose else logging.INFO
    fmt = '[%(asctime)s][%(levelname)s] %(message)s'
    logging.basicConfig(level=level, format=fmt, datefmt='%H:%M:%S')


def _load_version_module():
    """以独立模块形式加载 version.py，避免依赖包结构。"""
    version_py = RELEASE_DIR / 'version.py'
    spec = importlib.util.spec_from_file_location('_release_version', version_py)
    if spec is None or spec.loader is None:
        raise RuntimeError('无法加载 release/version.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ensure_dir(path: Path) -> Path:
    """确保目录存在并返回它。"""
    path.mkdir(parents=True, exist_ok=True)
    return path


def _copy_pkg_snapshot(dest_pkg_dir: Path) -> None:
    """把 maxagent/ 整个复制到 dest_pkg_dir，跳过 __pycache__ / 字节码。"""
    if dest_pkg_dir.exists():
        shutil.rmtree(dest_pkg_dir)

    def _ignore(_src: str, names: Sequence[str]) -> List[str]:
        ignored: List[str] = []
        for name in names:
            if name == '__pycache__':
                ignored.append(name)
            elif name.endswith('.pyc') or name.endswith('.pyo'):
                ignored.append(name)
            elif name.startswith('.') and name not in ('.gitkeep',):
                ignored.append(name)
        return ignored

    shutil.copytree(SOURCE_PKG_DIR, dest_pkg_dir, ignore=_ignore)


# -------------------------- 版本号同步 --------------------------


def _bump_version(new_version: str) -> None:
    """把 release/version.py 中的 __version__ 修改为 new_version。"""
    if not re.match(r'^\d+\.\d+\.\d+(?:[-+][0-9a-zA-Z\.\-]+)?$', new_version):
        raise ValueError(
            '版本号格式必须为 SemVer，如 1.0.0 或 1.0.0-rc1，得到: {}'.format(new_version)
        )
    version_py = RELEASE_DIR / 'version.py'
    text = version_py.read_text(encoding='utf-8')
    new_text, count = re.subn(
        r"__version__\s*=\s*'[^']*'",
        "__version__ = '{}'".format(new_version),
        text,
        count=1,
    )
    if count != 1:
        raise RuntimeError('version.py 内容异常，未找到 __version__ 行')
    version_py.write_text(new_text, encoding='utf-8')
    LOG.info('version.py 已更新为 %s', new_version)


def _sync_pkg_init_version(snapshot_pkg_dir: Path, version: str) -> None:
    """同步 maxagent 副本的 __init__.py 中的 __version__（不动源码）。"""
    init_py = snapshot_pkg_dir / '__init__.py'
    if not init_py.exists():
        return
    text = init_py.read_text(encoding='utf-8')
    new_text, count = re.subn(
        r"__version__\s*=\s*'[^']*'",
        "__version__ = '{}'".format(version),
        text,
        count=1,
    )
    if count == 1:
        init_py.write_text(new_text, encoding='utf-8')
        LOG.debug('snapshot __init__.py 同步版本号 -> %s', version)


# -------------------------- 源码 stage --------------------------


def _stage_source(version: str, dry_run: bool) -> Path:
    """把 maxagent/ 复制到 build_cache/maxagent/，写入版本号。"""
    LOG.info('=' * 60)
    LOG.info('准备源码 stage: %s (version=%s)', STAGE_PKG_DIR, version)
    LOG.info('=' * 60)
    if dry_run:
        LOG.info('[dry-run] 将复制 %s -> %s', SOURCE_PKG_DIR, STAGE_PKG_DIR)
        return STAGE_PKG_DIR
    _ensure_dir(BUILD_CACHE_DIR)
    _copy_pkg_snapshot(STAGE_PKG_DIR)
    _sync_pkg_init_version(STAGE_PKG_DIR, version)
    LOG.info('源码 stage 完成，产物在 %s', STAGE_PKG_DIR)
    return STAGE_PKG_DIR


# -------------------------- mzp 打包 --------------------------


def _write_mzp_run(zf: zipfile.ZipFile) -> None:
    """写入 mzp.run（Windows CRLF）。"""
    # mzp.run 是 Autodesk mzp 拖入清单，Max 严格按行解析。若为 LF
    # 会被误当作单行 → 退化到"运行第一个文件"启发式，行为不可控。
    # 无论源仓库里是 LF 还是 CRLF，打包时都强制统一为 CRLF。
    src = RELEASE_DIR / 'mzp.run'
    if not src.exists():
        LOG.warning('mzp.run 缺失 -> Max 会退化到启发式解析')
        return
    raw = src.read_bytes()
    normalized = raw.replace(b'\r\n', b'\n').replace(b'\r', b'\n')
    zf.writestr('mzp.run', normalized.replace(b'\n', b'\r\n'))


def _write_install_script(zf: zipfile.ZipFile) -> None:
    src = RELEASE_DIR / 'mzp_install.ms'
    if src.exists():
        zf.write(src, arcname='mzp_install.ms')
    else:
        LOG.warning('mzp_install.ms 缺失 -> mzp 完全无法自动安装')


def _write_macros(zf: zipfile.ZipFile) -> None:
    macros_src = RELEASE_DIR / 'macros'
    if not macros_src.is_dir():
        LOG.warning('release/macros/ 缺失，mzp 内将无 .mcr 文件')
        return
    for f in macros_src.rglob('*'):
        if not f.is_file():
            continue
        arc = Path('macros') / f.relative_to(macros_src)
        zf.write(f, arcname=arc.as_posix())


def _write_runtime(zf: zipfile.ZipFile) -> int:
    """写入 runtime/maxagent/*，返回打入的文件数。"""
    if not STAGE_PKG_DIR.is_dir():
        LOG.error('stage 目录不存在: %s', STAGE_PKG_DIR)
        return 0
    n = 0
    for f in STAGE_PKG_DIR.rglob('*'):
        if not f.is_file():
            continue
        arc = Path('runtime') / 'maxagent' / f.relative_to(STAGE_PKG_DIR)
        zf.write(f, arcname=arc.as_posix())
        n += 1
    return n


def _make_mzp(version: str, dry_run: bool) -> Path:
    """打包最终 mzp。"""
    _ensure_dir(DIST_DIR)
    out_path = DIST_DIR / 'maxagent-{}.mzp'.format(version)

    if dry_run:
        LOG.info('[dry-run] 将生成 %s', out_path)
        return out_path

    if out_path.exists():
        out_path.unlink()

    LOG.info('打包 mzp: %s', out_path.name)
    with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        _write_mzp_run(zf)
        _write_install_script(zf)
        _write_macros(zf)
        n = _write_runtime(zf)
        LOG.info('  runtime/maxagent/ 打入 %d 个文件', n)

    size_mb = out_path.stat().st_size / 1024 / 1024
    LOG.info('mzp 完成: %s (%.2f MB)', out_path, size_mb)
    return out_path


# -------------------------- 主入口 --------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description='MaxAgent 一键打包（开源版）')
    parser.add_argument('--version', help='覆盖 release/version.py 中的版本号', default=None)
    parser.add_argument('--pack-only', action='store_true',
                        help='仅打包：跳过复制源码，直接用 build_cache/maxagent/ 现有产物')
    parser.add_argument('--dry-run', action='store_true', help='仅打印计划不执行')
    parser.add_argument('--verbose', '-v', action='store_true')
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)

    # 1) 加载版本号
    if args.version:
        _bump_version(args.version)
    version_mod = _load_version_module()
    version: str = version_mod.__version__
    LOG.info('版本号: %s', version)

    # 2) 检查源包目录
    if not SOURCE_PKG_DIR.exists():
        LOG.error('源包目录不存在: %s', SOURCE_PKG_DIR)
        return 2

    _ensure_dir(BUILD_CACHE_DIR)
    _ensure_dir(DIST_DIR)

    # 3) 复制或复用 stage
    if args.pack_only:
        LOG.info('--pack-only 模式：跳过复制源码')
        if not STAGE_PKG_DIR.is_dir() and not args.dry_run:
            LOG.error('build_cache/maxagent 不存在，无法 --pack-only')
            return 5
    else:
        try:
            _stage_source(version, args.dry_run)
        except Exception as exc:  # pylint: disable=broad-except
            LOG.exception('复制源码失败: %s', exc)
            return 3

    # 4) 打包 mzp
    _make_mzp(version, args.dry_run)
    LOG.info('全部完成 ✅')
    return 0


if __name__ == '__main__':
    sys.exit(main())
