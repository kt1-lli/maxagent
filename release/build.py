#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MaxAgent 打包入口（开源版：源码直出 mzp / zip）。

支持三种产物目标（--target）：
- max：仅 3ds Max 相关代码（含 shared + max + Max 专属 UI/资源）
- maya：仅 Maya 相关代码（含 shared + maya + Maya 启动入口）
- full：两者全部（默认）

不做 Cython / PyArmor / py_compile，产物结构::

    dist/maxagent-{target}-X.Y.Z.mzp
      ├── mzp.run
      ├── mzp_install.ms
      ├── macros/MaxAgent-Macros.mcr        # 仅 max / full 目标
      └── runtime/maxagent/...             # 按 target 过滤后的源码

Maya 目标输出为 .zip（拖拽脚本 + 源码），不含 mzp 安装清单。

用法::

    python release/build.py                              # full + mzp
    python release/build.py --target max                 # 仅 Max
    python release/build.py --target maya                # 仅 Maya（zip）
    python release/build.py --version 1.2.3              # 同时更新 version.py
    python release/build.py --pack-only                 # 仅重新打包（复用 build_cache/）
    python release/build.py --dry-run                   # 只打印计划
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
from typing import List, Optional, Sequence, Set


# 本脚本所在目录 = release/
RELEASE_DIR = Path(__file__).resolve().parent
# 仓库根 = release/ 的父目录
REPO_ROOT = RELEASE_DIR.parent
# 源包目录（绝不修改）
SOURCE_PKG_DIR = REPO_ROOT / 'maxagent'
# 仓库根的入口文件（按目标复制）
ROOT_ENTRY_FILES = {
    'maya': ['maya_entry.py'],
    'max': [],
    'full': ['maya_entry.py'],
}
# 中间产物 / 最终产物
BUILD_CACHE_DIR = RELEASE_DIR / 'build_cache'
STAGE_PKG_DIR = BUILD_CACHE_DIR / 'maxagent'
DIST_DIR = RELEASE_DIR / 'dist'


LOG = logging.getLogger('maxagent.release.build')


# -------------------------- 目标过滤规则 --------------------------

# 各 target 包含的 tools 子包
_TARGET_TOOLS_SUBDIRS = {
    'max': {'shared', 'max'},
    'maya': {'shared', 'maya'},
    'full': {'shared', 'max', 'maya'},
}

# 各 target 包含的 DCC 专用 UI / 资源
_TARGET_DCC_DIRS = {
    'max': {'dcc/max_adapter.py', 'ui/dock_widget.py'},
    'maya': {'dcc/maya_adapter.py', 'ui/maya_startup.py'},
    'full': {
        'dcc/max_adapter.py', 'dcc/maya_adapter.py',
        'ui/dock_widget.py', 'ui/maya_startup.py',
    },
}

# 各 target 包含的仓库根入口文件
_TARGET_ROOT_FILES = {
    'max': set(),
    'maya': {'maya_entry.py'},
    'full': {'maya_entry.py'},
}

# 各 target 打包后的产物后缀
_TARGET_SUFFIX = {
    'max': 'max',
    'maya': 'maya',
    'full': 'full',
}


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


def _copy_pkg_snapshot(dest_pkg_dir: Path, target: str) -> None:
    """把 maxagent/ 整个复制到 dest_pkg_dir，按 target 过滤并按 DCC 裁剪。

    :param target: max / maya / full
    """
    if dest_pkg_dir.exists():
        shutil.rmtree(dest_pkg_dir)

    # 计算需要保留的相对路径集合
    keep_rel: Set[str] = set()

    def _collect_rel(base: Path) -> None:
        for f in base.rglob('*'):
            if not f.is_file():
                continue
            rel = str(f.relative_to(SOURCE_PKG_DIR).as_posix())
            # 排除 Python 字节码与隐藏文件
            if '/__pycache__/' in rel or rel.startswith('__pycache__/'):
                continue
            if f.name.endswith('.pyc') or f.name.endswith('.pyo'):
                continue
            if f.name.startswith('.') and f.name != '.gitkeep':
                continue
            keep_rel.add(rel)

    _collect_rel(SOURCE_PKG_DIR)

    # 过滤 tools 子包
    tools_dir = SOURCE_PKG_DIR / 'tools'
    allowed_tool_sub = _TARGET_TOOLS_SUBDIRS[target]
    for f in tools_dir.rglob('*'):
        if not f.is_file():
            continue
        rel = str(f.relative_to(SOURCE_PKG_DIR).as_posix())
        # tools/<sub>/... 只在 allowed 集合内保留
        parts = rel.split('/')
        if parts[0] == 'tools' and parts[1] not in allowed_tool_sub:
            keep_rel.discard(rel)

    # 过滤 DCC 专用目录
    allowed_dcc = _TARGET_DCC_DIRS[target]
    # 任何带 dcc 标识的副作用文件（如 dcc/maya_adapter.py）已在上表，其余保留
    for f in SOURCE_PKG_DIR.rglob('*'):
        if not f.is_file():
            continue
        rel = str(f.relative_to(SOURCE_PKG_DIR).as_posix())
        # ui 下非目标 DCC 的 UI 文件过滤（仅保留 dock_widget / maya_startup）
        if rel.startswith('ui/') and rel not in allowed_dcc and rel != 'ui/__init__.py':
            # 保留通用 UI 文件，只剔除 DCC 专属的未在白名单里的
            if rel in ('ui/dock_widget.py', 'ui/maya_startup.py'):
                # 已通过 allowed_dcc 判断，不必额外处理
                pass
            # 其他 ui 文件（如 avatar_crop_dialog 等）为 Max 通用 UI，full/max 保留
            if target == 'maya' and rel not in allowed_dcc:
                keep_rel.discard(rel)

    # 执行复制
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

    # 用选择性 copy 而非整目录 copy，确保过滤生效
    _selective_copytree(SOURCE_PKG_DIR, dest_pkg_dir, keep_rel, _ignore)

    # 再次清理：复制过程中若触发任何 import，可能生成新的 __pycache__
    for pyc_dir in dest_pkg_dir.rglob('__pycache__'):
        if pyc_dir.is_dir():
            shutil.rmtree(pyc_dir)
    for pyc_file in dest_pkg_dir.rglob('*.pyc'):
        if pyc_file.is_file():
            pyc_file.unlink()
    for pyo_file in dest_pkg_dir.rglob('*.pyo'):
        if pyo_file.is_file():
            pyo_file.unlink()


def _selective_copytree(src_root: Path, dst_root: Path, keep_rel: Set[str], ignore):
    """只复制 keep_rel 中列出的文件，保持目录结构。"""
    for rel in sorted(keep_rel):
        s = src_root / rel
        d = dst_root / rel
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, d)


def _copy_root_entries(target: str) -> List[str]:
    """复制仓库根目录的入口文件（如 maya_entry.py）到 BUILD_CACHE 根部。

    :returns: 复制后的相对路径列表
    """
    copied: List[str] = []
    for fname in _TARGET_ROOT_FILES.get(target, set()):
        src = REPO_ROOT / fname
        if src.exists():
            dst = BUILD_CACHE_DIR / fname
            shutil.copy2(src, dst)
            copied.append(fname)
    return copied


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


def _stage_source(version: str, target: str, dry_run: bool) -> Path:
    """按 target 复制 maxagent/ 到 build_cache/maxagent/，并复制根入口。"""
    LOG.info('=' * 60)
    LOG.info('准备源码 stage: %s (version=%s, target=%s)', STAGE_PKG_DIR, version, target)
    LOG.info('=' * 60)
    if dry_run:
        LOG.info('[dry-run] 将复制 %s -> %s', SOURCE_PKG_DIR, STAGE_PKG_DIR)
        return STAGE_PKG_DIR
    _ensure_dir(BUILD_CACHE_DIR)
    _copy_pkg_snapshot(STAGE_PKG_DIR, target)
    _copy_root_entries(target)
    _sync_pkg_init_version(STAGE_PKG_DIR, version)
    LOG.info('源码 stage 完成，产物在 %s', STAGE_PKG_DIR)
    return STAGE_PKG_DIR


# -------------------------- mzp 打包（max / full） --------------------------


def _write_mzp_run(zf: zipfile.ZipFile) -> None:
    """写入 mzp.run（Windows CRLF）。"""
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


def _write_macros(zf: zipfile.ZipFile, target: str) -> None:
    if target == 'maya':
        return  # Maya 目标不包含 Max 宏
    macros_src = RELEASE_DIR / 'macros'
    if not macros_src.is_dir():
        LOG.warning('release/macros/ 缺失，mzp 内将无 .mcr 文件')
        return
    for f in macros_src.rglob('*'):
        if not f.is_file():
            continue
        arc = Path('macros') / f.relative_to(macros_src)
        zf.write(f, arcname=arc.as_posix())


def _write_runtime(zf: zipfile.ZipFile, target: str) -> int:
    """写入 runtime/maxagent/*（mzp 内），返回打入的文件数。"""
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


def _make_mzp(version: str, target: str, dry_run: bool) -> Path:
    """打包最终 mzp（max / full）。"""
    _ensure_dir(DIST_DIR)
    out_path = DIST_DIR / 'maxagent-{}-{}.mzp'.format(_TARGET_SUFFIX[target], version)

    if dry_run:
        LOG.info('[dry-run] 将生成 %s', out_path)
        return out_path

    if out_path.exists():
        out_path.unlink()

    LOG.info('打包 mzp: %s', out_path.name)
    with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        _write_mzp_run(zf)
        _write_install_script(zf)
        _write_macros(zf, target)
        n = _write_runtime(zf, target)
        LOG.info('  runtime/maxagent/ 打入 %d 个文件', n)

    size_mb = out_path.stat().st_size / 1024 / 1024
    LOG.info('mzp 完成: %s (%.2f MB)', out_path, size_mb)
    return out_path


# -------------------------- zip 打包（maya） --------------------------


def _make_zip(version: str, target: str, dry_run: bool) -> Path:
    """打包 Maya 专属 zip（含根入口 + runtime 源码）。"""
    _ensure_dir(DIST_DIR)
    out_path = DIST_DIR / 'maxagent-{}-{}.zip'.format(_TARGET_SUFFIX[target], version)

    if dry_run:
        LOG.info('[dry-run] 将生成 %s', out_path)
        return out_path

    if out_path.exists():
        out_path.unlink()

    LOG.info('打包 zip: %s', out_path.name)
    n = 0
    with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        # 仓库根入口文件
        for fname in _TARGET_ROOT_FILES.get(target, set()):
            src = BUILD_CACHE_DIR / fname
            if src.exists():
                zf.write(src, arcname=fname)
                n += 1
        # runtime 源码
        for f in STAGE_PKG_DIR.rglob('*'):
            if not f.is_file():
                continue
            arc = Path('maxagent') / f.relative_to(STAGE_PKG_DIR)
            zf.write(f, arcname=arc.as_posix())
            n += 1
    LOG.info('  zip 打入 %d 个文件', n)
    return out_path


# -------------------------- 主入口 --------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description='MaxAgent 一键打包（开源版）')
    parser.add_argument(
        '--target', choices=['max', 'maya', 'full'], default='full',
        help='打包目标：max=仅 3ds Max, maya=仅 Maya, full=两者（默认）',
    )
    parser.add_argument('--version', help='覆盖 release/version.py 中的版本号', default=None)
    parser.add_argument('--pack-only', action='store_true',
                        help='仅打包：跳过复制源码，直接用 build_cache/ 现有产物')
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
            _stage_source(version, args.target, args.dry_run)
        except Exception as exc:  # pylint: disable=broad-except
            LOG.exception('复制源码失败: %s', exc)
            return 3

    # 4) 打包
    if args.target == 'maya':
        _make_zip(version, args.target, args.dry_run)
    else:
        _make_mzp(version, args.target, args.dry_run)
    LOG.info('全部完成 ✅')
    return 0


if __name__ == '__main__':
    sys.exit(main())
