#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MaxAgent 打包入口（开源版：源码直接打包）。

去掉了 Cython / PyArmor / py_compile 保护，直接把 ``maxagent/``
以纯源码形式复制到 ``release/build_cache/`` 下，最终打包成
``release/dist/maxagent-X.Y.Z.mzp``。

用法
====
::

    python release/build.py                     # 完整流程（复制源码 + 打包）
    python release/build.py --version 1.2.3     # 同时更新 version.py
    python release/build.py --pack-only         # 仅重新打包（复用已有 build_cache）
    python release/build.py --dry-run           # 只打印计划
    python release/build.py --verbose           # 详细日志

产物结构
========
::

    dist/maxagent-X.Y.Z.mzp
      ├── mzp.run
      ├── mzp_install.ms
      ├── macros/MaxAgent-Macros.mcr
      └── runtime/
          ├── cp37/maxagent/...    # 各 ABI 目录内容完全一致（纯 .py）
          ├── cp39/maxagent/...
          ├── cp310/maxagent/...
          ├── cp311/maxagent/...
          └── cp313/maxagent/...

之所以仍然按 ABI 分目录，是为了兼容 ``mzp_install.ms`` 的既有查找路径
``runtime\\cpXX\\maxagent\\``；纯源码分发场景下多份副本几乎不占空间
（zip 压缩后重复内容会被高效压缩）。
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
from typing import List, Optional, Sequence, Tuple


# 本脚本所在目录 = release/
RELEASE_DIR = Path(__file__).resolve().parent
# 仓库根 = release/ 的父目录
REPO_ROOT = RELEASE_DIR.parent
# 源包目录（绝不修改）
SOURCE_PKG_DIR = REPO_ROOT / 'maxagent'
# 中间产物 / 最终产物
BUILD_CACHE_DIR = RELEASE_DIR / 'build_cache'
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


def _clean_dir(path: Path) -> Path:
    """如果存在先删除再创建（保证全新目录）。"""
    if path.exists():
        shutil.rmtree(path)
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


# -------------------------- 单 ABI 目录填充（纯拷贝） --------------------------


def _build_one_abi(abi: str, version: str, dry_run: bool) -> Path:
    """在 build_cache/<abi>/maxagent/ 下放一份源码副本。

    开源版不再做任何编译或加密，所有 ABI 目录内容一致。之所以仍
    保留 ABI 目录分层，是为了兼容 ``mzp_install.ms`` 从
    ``runtime\\cpXX\\maxagent\\`` 定位产物的历史约定，同时给未来
    如果重新引入 ABI 相关产物留出扩展位。
    """
    LOG.info('=' * 60)
    LOG.info('准备 ABI 目录: %s (version=%s)', abi, version)
    LOG.info('=' * 60)
    abi_root = BUILD_CACHE_DIR / abi
    if dry_run:
        LOG.info('[dry-run][%s] 将复制 %s -> %s/maxagent', abi, SOURCE_PKG_DIR, abi_root)
        return abi_root
    _clean_dir(abi_root)
    snapshot_pkg = abi_root / 'maxagent'
    _copy_pkg_snapshot(snapshot_pkg)
    _sync_pkg_init_version(snapshot_pkg, version)
    LOG.info('[%s] 完成，产物在 %s', abi, snapshot_pkg)
    return abi_root


# -------------------------- mzp 打包 --------------------------


def _make_mzp(
    version: str,
    available_abis: Sequence[str],
    dry_run: bool,
) -> Path:
    """将 build_cache/cpXX/ × N + mzp_install.ms 打成 mzp。"""
    _ensure_dir(DIST_DIR)
    out_path = DIST_DIR / 'maxagent-{}.mzp'.format(version)

    if dry_run:
        LOG.info('[dry-run] 将生成 %s，含 ABIs: %s', out_path, list(available_abis))
        return out_path

    if out_path.exists():
        out_path.unlink()

    LOG.info('打包 mzp: %s', out_path.name)
    with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        # 安装钩子（按 Autodesk mzp 协议组织）
        #
        #   * mzp.run        : 拖入清单文件（指令序列，非 INI），告诉 Max
        #                       拖入时如何解压、执行哪个 dropScript。
        #   * mzp_install.ms : 唯一的 dropScript——拷贝产物 / 注册宏 /
        #                      注册菜单 / 最后拉起 AI 面板，全部一次完成。
        #
        # 关键约束：
        #   * mzp.run 必须用 Windows CRLF 换行（INI 风格被 Max 严格按行
        #     解析，Unix LF 会让 Max 把整个文件当作一行解释失败 → 退化到
        #     "运行第一个文件"启发式 → 进而失败）。即使源仓库里的 mzp.run
        #     是 LF，打包时也要在 zip 字节流里强制改成 CRLF。
        #
        # 参考: https://help.autodesk.com/view/MAXDEV/2027/ENU/?guid=GUID-35559C6A
        mzp_run_src = RELEASE_DIR / 'mzp.run'
        if mzp_run_src.exists():
            raw = mzp_run_src.read_bytes()
            normalized = raw.replace(b'\r\n', b'\n').replace(b'\r', b'\n')
            crlf_bytes = normalized.replace(b'\n', b'\r\n')
            zf.writestr('mzp.run', crlf_bytes)
        else:
            LOG.warning('mzp.run 缺失 -> Max 会退化到启发式解析，行为不可控')

        install_src = RELEASE_DIR / 'mzp_install.ms'
        if install_src.exists():
            zf.write(install_src, arcname='mzp_install.ms')
        else:
            LOG.warning('mzp_install.ms 缺失 -> mzp 完全无法自动安装')

        # 预打包的 macroScript 文件（UTF-8 BOM .mcr）
        macros_src = RELEASE_DIR / 'macros'
        if macros_src.is_dir():
            for f in macros_src.rglob('*'):
                if f.is_file():
                    arc = Path('macros') / f.relative_to(macros_src)
                    zf.write(f, arcname=str(arc).replace('\\', '/'))
        else:
            LOG.warning('release/macros/ 目录缺失，mzp 内将无 .mcr 文件，宏注册会失败')

        # 各 ABI 产物（纯 .py 源码）
        for abi in available_abis:
            abi_root = BUILD_CACHE_DIR / abi
            for f in abi_root.rglob('*'):
                if not f.is_file():
                    continue
                arc = Path('runtime') / abi / f.relative_to(abi_root)
                zf.write(f, arcname=str(arc).replace('\\', '/'))

    size_mb = out_path.stat().st_size / 1024 / 1024
    LOG.info('mzp 完成: %s (%.1f MB)', out_path, size_mb)
    return out_path


# -------------------------- 主入口 --------------------------


def _resolve_abis(
    args_abis: Optional[List[str]], supported: Sequence[str],
) -> List[str]:
    """解析最终要构建的 ABI 列表。"""
    if args_abis:
        unknown = [a for a in args_abis if a not in supported]
        if unknown:
            raise ValueError(
                '未知 ABI: {}，支持: {}'.format(unknown, list(supported))
            )
        return list(args_abis)
    return list(supported)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description='MaxAgent 一键打包（开源版）')
    parser.add_argument('--version', help='覆盖 release/version.py 中的版本号', default=None)
    parser.add_argument('--abis', nargs='+',
                        help='指定 ABI 列表（如 cp311 cp313），默认全部 supported ABI')
    parser.add_argument('--pack-only', action='store_true',
                        help='仅打包：跳过复制源码，直接用 build_cache/ 现有产物聚合 mzp')
    parser.add_argument('--dry-run', action='store_true', help='仅打印计划不执行')
    parser.add_argument('--verbose', '-v', action='store_true')
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)

    # 1) 加载版本号
    if args.version:
        _bump_version(args.version)
    version_mod = _load_version_module()
    version: str = version_mod.__version__
    supported_abis: Tuple[str, ...] = version_mod.SUPPORTED_ABIS
    LOG.info('版本号: %s', version)

    # 2) 决定要构建的 ABI
    abis = _resolve_abis(args.abis, supported_abis)
    LOG.info('目标 ABIs: %s', abis)

    # 3) 检查源包目录
    if not SOURCE_PKG_DIR.exists():
        LOG.error('源包目录不存在: %s', SOURCE_PKG_DIR)
        return 2

    # 4) 准备 build_cache / dist
    _ensure_dir(BUILD_CACHE_DIR)
    _ensure_dir(DIST_DIR)

    # --pack-only 短路：跳过复制，直接打包
    if args.pack_only:
        LOG.info('--pack-only 模式：跳过复制源码，直接打包')
        existing_abis: List[str] = []
        for abi in abis:
            abi_dir = BUILD_CACHE_DIR / abi
            pkg_dir = abi_dir / 'maxagent'
            if pkg_dir.is_dir():
                existing_abis.append(abi)
                LOG.info('[%s] 检测到已有产物: %s', abi, pkg_dir)
            else:
                LOG.warning('[%s] build_cache 中未找到 maxagent 包，跳过', abi)
        if not existing_abis and not args.dry_run:
            LOG.error('--pack-only 模式下 build_cache/ 内没有任何 ABI 产物')
            return 5
        _make_mzp(version, existing_abis, args.dry_run)
        LOG.info('打包完成 ✅')
        return 0

    # 5) 逐 ABI 复制源码
    built_abis: List[str] = []
    for abi in abis:
        try:
            _build_one_abi(abi, version, args.dry_run)
            built_abis.append(abi)
        except Exception as exc:  # pylint: disable=broad-except
            LOG.exception('[%s] 复制失败: %s', abi, exc)
            return 3

    # 6) 打包 mzp
    if not built_abis and not args.dry_run:
        LOG.error('没有任何 ABI 产物，跳过 mzp 打包')
        return 4
    _make_mzp(version, built_abis or list(abis), args.dry_run)

    LOG.info('全部完成 ✅')
    return 0


if __name__ == '__main__':
    sys.exit(main())
