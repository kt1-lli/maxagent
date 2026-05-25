#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MaxAgent 一键打包入口。

工作流程
========

1. 解析参数 / 读取 ``release/version.py`` / 读取 ``cython_modules.txt``
2. 对每个目标 ABI（cp39/cp310/cp311/cp313）：
   a. 创建 ``build_cache/cpXX/`` 干净工作区
   b. 把 ``maxagent/`` 整个包复制到 ``build_cache/cpXX/maxagent/`` 作临时副本
      （**绝不修改源码目录**）
   c. 对白名单文件运行 Cython → 产出 ``.pyd`` / ``.so``，删除原 .py
   d. 对剩余 .py 运行 PyArmor RFT → 产出加密 .pyc，删除原 .py
   e. 校验：必须保留 __init__.py / reload.py / qt_compat.py 明文
3. 把 5 个 ``cpXX/`` 子目录、shared/、mzp_install.ms 一起打成
   ``dist/maxagent-X.Y.Z.mzp``（实质 zip）
4. 输出最终路径供分发

设计取舍
========
- 只读取，**绝不修改 ``maxagent/`` 源码**：所有变换都在 ``build_cache/``。
- ABI 缺失策略：``--quick`` 模式允许跳过；正式发布要求齐全。
- 在 Linux 上能跑通完整流程（产出 ``.so`` 替代 ``.pyd``），便于 CI 与本地预演；
  Windows .pyd 由跑 Windows 节点的 CI 步骤产出。

用法
====
::

    python release/build.py                # 完整 5 ABI 构建（要求当前解释器能编全部）
    python release/build.py --quick        # 仅当前 ABI（开发期）
    python release/build.py --all-abis     # ⭐ 一键全 ABI：通过 uv 调度 4 个 Python 子进程并聚合
    python release/build.py --version X.Y  # 同时更新 version.py
    python release/build.py --abis cp311   # 指定单 ABI
    python release/build.py --skip-pyarmor # 调试期跳过 PyArmor
    python release/build.py --dry-run      # 仅打印计划不执行
"""

from __future__ import absolute_import

import argparse
import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import time
import zipfile
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple


# 本脚本所在目录 = release/
RELEASE_DIR = Path(__file__).resolve().parent
# 仓库根 = release/ 的父目录
REPO_ROOT = RELEASE_DIR.parent
# 源包目录（绝不修改）
SOURCE_PKG_DIR = REPO_ROOT / 'maxagent'
# 中间产物 / 最终产物
BUILD_CACHE_DIR = RELEASE_DIR / 'build_cache'
DIST_DIR = RELEASE_DIR / 'dist'
SHARED_DIR = RELEASE_DIR / 'shared'

# 必须保留 .py 明文的文件（相对 maxagent/ 包根）
KEEP_PLAINTEXT_FILES = (
    '__init__.py',
    'reload.py',
    'qt_compat.py',
)

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


def _read_cython_whitelist() -> List[str]:
    """读取 cython_modules.txt，返回相对 maxagent/ 包的路径列表。"""
    listfile = RELEASE_DIR / 'cython_modules.txt'
    items: List[str] = []
    for line in listfile.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        items.append(line)
    LOG.debug('Cython 白名单含 %d 个文件', len(items))
    return items


def _current_abi() -> str:
    """返回当前 Python 解释器对应的 ABI 标签（如 'cp311'）。"""
    info = sys.version_info
    return 'cp{}{}'.format(info.major, info.minor)


def _ext_suffix() -> str:
    """返回当前平台 C 扩展模块后缀（Windows 上是 .pyd，Linux 是 .so）。"""
    suffix = sysconfig.get_config_var('EXT_SUFFIX')
    if suffix:
        return suffix
    # 兜底
    return '.pyd' if sys.platform.startswith('win') else '.so'


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
        raise ValueError('版本号格式必须为 SemVer，如 0.4.0 或 0.4.0-rc1，得到: {}'.format(new_version))
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


# -------------------------- Cython 编译 --------------------------


def _has_cython() -> bool:
    """探测是否安装了 cython。"""
    try:
        import Cython  # noqa: F401
        return True
    except ImportError:
        return False


def _cythonize_files(
    snapshot_pkg_dir: Path,
    whitelist: Sequence[str],
    abi: str,
    dry_run: bool,
) -> List[Path]:
    """对 snapshot_pkg_dir 内的白名单文件执行 Cython 编译。

    :param snapshot_pkg_dir: 临时副本的 maxagent/ 目录
    :param whitelist: 相对包根的源文件路径列表
    :param abi: 目标 ABI（仅记日志，实际编译用当前解释器）
    :param dry_run: 仅打印不执行
    :return: 已生成的扩展模块文件列表（绝对路径）
    """
    if not whitelist:
        return []
    if dry_run:
        LOG.info('[dry-run] 将 Cython 编译 %d 个文件 (ABI=%s)', len(whitelist), abi)
        return []
    if not _has_cython():
        raise RuntimeError(
            'Cython 未安装。请先 `uv pip install cython>=3.0.11` 或 `pip install cython`'
        )

    from Cython.Build import cythonize
    from setuptools import Extension

    extensions: List[Extension] = []
    sources_to_remove: List[Path] = []
    for rel in whitelist:
        src = snapshot_pkg_dir / rel
        if not src.exists():
            LOG.warning('白名单文件不存在 (跳过): %s', rel)
            continue
        # Cython 模块名 = maxagent.子模块.子模块
        module_name = 'maxagent.' + rel[:-3].replace('/', '.').replace('\\', '.')
        ext = Extension(
            name=module_name,
            sources=[str(src)],
        )
        extensions.append(ext)
        sources_to_remove.append(src)

    if not extensions:
        return []

    compiler_directives = {
        'language_level': 3,
        'binding': True,
        'embedsignature': True,
        'boundscheck': False,
        'wraparound': False,
        'cdivision': True,
    }
    LOG.info('[%s] Cython 编译 %d 个模块...', abi, len(extensions))

    # 用 build_ext 命令编译 .pyx/.py → .c → .pyd/.so
    # 我们在原地编译（inplace）生成 .pyd/.so 与源 .py 同目录
    build_dir = BUILD_CACHE_DIR / abi / '_cython_build'
    _clean_dir(build_dir)

    # 切到包根目录调用 setuptools，需要构造一个临时 setup
    cmd = [
        sys.executable, '-c',
        (
            'import sys; sys.argv=["setup.py","build_ext","--inplace",'
            '"--build-temp",{build_temp!r},"--build-lib",{build_lib!r}];'
            'from setuptools import setup;'
            'from Cython.Build import cythonize;'
            'from setuptools import Extension;'
            'exts=[Extension(n, [s]) for n,s in {ext_pairs!r}];'
            'setup(name="maxagent_cython", ext_modules=cythonize(exts, '
            'compiler_directives={directives!r}))'
        ).format(
            build_temp=str(build_dir / 'temp'),
            build_lib=str(build_dir / 'lib'),
            ext_pairs=[(e.name, e.sources[0]) for e in extensions],
            directives=compiler_directives,
        ),
    ]

    proc = subprocess.run(
        cmd,
        cwd=str(snapshot_pkg_dir.parent),  # cwd = build_cache/cpXX/
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
    )
    if proc.returncode != 0:
        LOG.error('Cython 编译失败:\n--- stdout ---\n%s\n--- stderr ---\n%s',
                  proc.stdout, proc.stderr)
        raise RuntimeError('Cython 编译失败 (ABI={})'.format(abi))
    LOG.debug('cython stdout: %s', proc.stdout[-500:])

    # 删除白名单原始 .py 与生成的中间 .c 文件
    suffix = _ext_suffix()
    produced: List[Path] = []
    for src in sources_to_remove:
        # 校验扩展模块产出
        ext_path = src.with_suffix(suffix)
        # 实际可能命名为 module.cpXX-win_amd64.pyd 这种含 ABI 的全名
        candidates = list(src.parent.glob(src.stem + '*' + suffix.split('.', 1)[-1]))
        if ext_path.exists():
            produced.append(ext_path)
        elif candidates:
            produced.append(candidates[0])
        else:
            LOG.warning('未找到 %s 的编译产物', src.name)
            continue
        # 删除原 .py
        src.unlink()
        # 删除 cython 生成的 .c
        c_file = src.with_suffix('.c')
        if c_file.exists():
            c_file.unlink()

    LOG.info('[%s] Cython 完成，产出 %d 个扩展模块', abi, len(produced))
    return produced


# -------------------------- PyArmor 加密 --------------------------


def _has_pyarmor() -> bool:
    """探测是否安装了 pyarmor。"""
    try:
        proc = subprocess.run(
            ['pyarmor', '--version'],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _pyarmor_encrypt(
    snapshot_pkg_dir: Path,
    excludes: Sequence[str],
    abi: str,
    dry_run: bool,
    skip: bool,
) -> int:
    """对 snapshot_pkg_dir 内剩余 .py 文件运行 PyArmor。

    :param excludes: 不加密的文件名集合（相对包根的路径，也可以是裸文件名）
    :return: 已加密文件数
    """
    remaining = sorted(snapshot_pkg_dir.rglob('*.py'))
    # 过滤掉明文保留文件（精确匹配 + 裸文件名匹配）
    keepset = set()
    for keep in excludes:
        keep_path = (snapshot_pkg_dir / keep).resolve()
        keepset.add(keep_path)
    keep_basenames = {Path(k).name for k in excludes}
    targets = [
        p for p in remaining
        if p.resolve() not in keepset and p.name not in keep_basenames
    ]

    if dry_run:
        LOG.info('[dry-run][%s] 将 PyArmor 加密 %d 个 .py 文件', abi, len(targets))
        return 0
    if skip:
        LOG.info('[%s] --skip-pyarmor 跳过加密，%d 个 .py 保留明文', abi, len(targets))
        return 0
    if not targets:
        return 0
    if not _has_pyarmor():
        LOG.warning(
            '[%s] PyArmor 未安装，回退到 compileall 字节码（保护强度仅 L1）。\n'
            '    安装：uv pip install pyarmor>=8.5.11',
            abi,
        )
        return _compileall_fallback(snapshot_pkg_dir, targets, abi)

    LOG.info('[%s] PyArmor 加密 %d 个文件...', abi, len(targets))
    out_dir = BUILD_CACHE_DIR / abi / '_pyarmor_out'
    _clean_dir(out_dir)
    # 把 keep 列表传给 pyarmor 的 --exclude（每个 keep 一个参数）
    cmd: List[str] = ['pyarmor', 'gen', '-O', str(out_dir), '-r', '-i']
    for keep in excludes:
        cmd += ['--exclude', keep]
    cmd.append(str(snapshot_pkg_dir))
    LOG.debug('pyarmor cmd: %s', ' '.join(cmd))

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
    )
    if proc.returncode != 0:
        # 软退化：trial 版常见的 "out of license" 限制
        combined = (proc.stdout or '') + (proc.stderr or '')
        if 'out of license' in combined.lower():
            LOG.warning(
                '[%s] PyArmor trial 试用版受限（out of license）。\n'
                '    自动回退到 compileall 字节码兜底。\n'
                '    正式发布请使用 PyArmor 商业 license（避免此回退）。',
                abi,
            )
            return _compileall_fallback(snapshot_pkg_dir, targets, abi)
        LOG.error('PyArmor 失败:\n--- stdout ---\n%s\n--- stderr ---\n%s',
                  proc.stdout, proc.stderr)
        raise RuntimeError('PyArmor 加密失败 (ABI={})'.format(abi))

    # 把加密产物逐文件覆盖回 snapshot_pkg_dir，保留明文白名单不动
    encrypted_pkg = out_dir / SOURCE_PKG_DIR.name
    if not encrypted_pkg.exists():
        LOG.warning('[%s] pyarmor 输出目录缺 maxagent/，跳过回拷', abi)
        return 0

    encrypted_count = 0
    for src_file in encrypted_pkg.rglob('*'):
        if not src_file.is_file():
            continue
        rel = src_file.relative_to(encrypted_pkg)
        dest_file = snapshot_pkg_dir / rel
        # 如果目标在 keepset，跳过覆盖（保留明文）
        if dest_file.resolve() in keepset or dest_file.name in keep_basenames:
            continue
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        if dest_file.exists():
            dest_file.unlink()
        shutil.copy2(str(src_file), str(dest_file))
        encrypted_count += 1

    LOG.info('[%s] PyArmor 完成，回写 %d 个文件', abi, encrypted_count)
    return len(targets)


def _compileall_fallback(
    snapshot_pkg_dir: Path,
    targets: Sequence[Path],
    abi: str,
) -> int:
    """PyArmor 不可用 / trial 受限时的兜底：用 compileall 把 .py 编译为 .pyc。

    保护强度仅 L1（标准字节码反编译工具如 uncompyle6 可逆向），
    但至少不再以源码明文形式分发。

    :return: 已编译的文件数
    """
    import py_compile

    LOG.info('[%s] py_compile 编译 %d 个 .py → .pyc', abi, len(targets))
    success_count = 0
    for src in targets:
        try:
            # cfile 与源 .py 同目录、同名（仅扩展名换为 .pyc），不走 __pycache__
            pyc_path = src.with_suffix('.pyc')
            py_compile.compile(
                str(src),
                cfile=str(pyc_path),
                doraise=True,
            )
            if pyc_path.exists():
                src.unlink()
                success_count += 1
        except py_compile.PyCompileError as exc:
            LOG.warning('[%s] py_compile 失败 %s: %s', abi, src.name, exc)
        except Exception as exc:  # pylint: disable=broad-except
            LOG.warning('[%s] py_compile 跳过 %s: %s', abi, src.name, exc)

    LOG.info('[%s] py_compile 完成，%d 个文件已转 .pyc', abi, success_count)
    return success_count



# -------------------------- 单 ABI 打包 --------------------------


def _build_one_abi(
    abi: str,
    version: str,
    whitelist: Sequence[str],
    dry_run: bool,
    skip_pyarmor: bool,
) -> Path:
    """构建单个 ABI 子目录，返回其根（含 maxagent/ 子目录）。"""
    LOG.info('=' * 60)
    LOG.info('开始构建 ABI: %s (version=%s)', abi, version)
    LOG.info('=' * 60)
    abi_root = BUILD_CACHE_DIR / abi
    _clean_dir(abi_root)
    snapshot_pkg = abi_root / 'maxagent'
    _copy_pkg_snapshot(snapshot_pkg)
    _sync_pkg_init_version(snapshot_pkg, version)

    # 1) Cython
    _cythonize_files(snapshot_pkg, whitelist, abi, dry_run)

    # 2) PyArmor（处理剩余 .py，但保留明文白名单）
    excludes = list(KEEP_PLAINTEXT_FILES)
    _pyarmor_encrypt(snapshot_pkg, excludes, abi, dry_run, skip_pyarmor)

    # 3) 校验：明文文件必须存在
    if not dry_run:
        for keep in KEEP_PLAINTEXT_FILES:
            kp = snapshot_pkg / keep
            if not kp.exists():
                raise RuntimeError('打包后明文文件缺失: {} (ABI={})'.format(keep, abi))

    LOG.info('[%s] 完成，产物在 %s', abi, snapshot_pkg)
    return abi_root


# -------------------------- mzp 打包 --------------------------


def _make_mzp(
    version: str,
    available_abis: Sequence[str],
    dry_run: bool,
) -> Path:
    """将 build_cache/cpXX/ × N + shared/ + mzp_install.ms 打成 mzp。"""
    _ensure_dir(DIST_DIR)
    out_path = DIST_DIR / 'maxagent-{}.mzp'.format(version)

    if dry_run:
        LOG.info('[dry-run] 将生成 %s，含 ABIs: %s', out_path, list(available_abis))
        return out_path

    if out_path.exists():
        out_path.unlink()

    LOG.info('打包 mzp: %s', out_path.name)
    with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        # 写入元数据
        meta = {
            'name': 'maxagent',
            'version': version,
            'abis': list(available_abis),
            'built_at': time.strftime('%Y-%m-%d %H:%M:%S %z'),
        }
        zf.writestr(
            'maxagent_release.json',
            json.dumps(meta, ensure_ascii=False, indent=2),
        )

        # 安装钩子
        install_ms = RELEASE_DIR / 'mzp_install.ms'
        if install_ms.exists():
            zf.write(install_ms, arcname='mzp_install.ms')
        else:
            LOG.warning('mzp_install.ms 缺失，mzp 将无法自动安装')

        # 各 ABI 产物
        # 排除中间构建目录（仅匹配目录段以 _ 开头，不匹配最终文件名如 __init__.py）
        intermediate_prefixes = ('_cython_build', '_pyarmor_out')
        for abi in available_abis:
            abi_root = BUILD_CACHE_DIR / abi
            for f in abi_root.rglob('*'):
                if not f.is_file():
                    continue
                rel_parts = f.relative_to(abi_root).parts
                # 仅检查目录段（去掉最末段文件名）是否落在中间目录里
                dir_parts = rel_parts[:-1]
                if any(seg in intermediate_prefixes for seg in dir_parts):
                    continue
                arc = Path('runtime') / abi / f.relative_to(abi_root)
                zf.write(f, arcname=str(arc).replace('\\', '/'))

        # 共享资源
        if SHARED_DIR.exists():
            for f in SHARED_DIR.rglob('*'):
                if f.is_file():
                    arc = Path('shared') / f.relative_to(SHARED_DIR)
                    zf.write(f, arcname=str(arc).replace('\\', '/'))

    size_mb = out_path.stat().st_size / 1024 / 1024
    LOG.info('mzp 完成: %s (%.1f MB)', out_path, size_mb)
    return out_path


# -------------------------- 多 ABI 一键调度（uv 矩阵） --------------------------


def _has_uv() -> bool:
    """探测 uv 是否安装并可执行。"""
    try:
        proc = subprocess.run(
            ['uv', '--version'],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _list_uv_pythons() -> List[str]:
    """枚举本机 uv 已安装的 Python 版本号字符串列表（如 ['3.9.7', '3.11.13']）。"""
    try:
        proc = subprocess.run(
            ['uv', 'python', 'list', '--only-installed'],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    versions: List[str] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        # uv python list 格式：cpython-3.11.13-windows-x86_64-none  C:\path\to\python.exe
        m = re.match(r'(?:cpython|pypy)-(\d+\.\d+\.\d+)[\w\-]*\s', line)
        if m:
            versions.append(m.group(1))
    return versions


def _ensure_uv_pythons(targets: Sequence[str], dry_run: bool, auto_install: bool) -> List[str]:
    """确保 uv 已安装目标 Python 版本列表。

    :param targets: 期望的 Python 完整版本号（如 ['3.9.7', '3.10.8', ...]）
    :param dry_run: 仅打印不执行
    :param auto_install: True 时自动调 ``uv python install`` 补齐缺失版本
    :return: 当前已可用的 Python 版本列表（可能少于 targets）
    """
    installed = set(_list_uv_pythons())
    missing = [v for v in targets if v not in installed]
    if not missing:
        LOG.info('uv 已安装全部目标 Python: %s', list(targets))
        return list(targets)

    LOG.warning('uv 缺少以下 Python 版本: %s', missing)
    if dry_run:
        LOG.info('[dry-run] 将通过 uv 安装上述版本')
        return list(targets)

    if not auto_install:
        LOG.error(
            '请先安装缺失的 Python（任选其一）:\n'
            '  uv python install %s\n'
            '或在本命令上加 --auto-install-pythons 自动安装。',
            ' '.join(missing),
        )
        return [v for v in targets if v in installed]

    LOG.info('自动安装缺失的 Python: %s', missing)
    proc = subprocess.run(
        ['uv', 'python', 'install'] + list(missing),
        text=True,
        encoding='utf-8',
        errors='replace',
    )
    if proc.returncode != 0:
        LOG.error('uv python install 失败，退出码 %s', proc.returncode)
        return [v for v in targets if v in installed]
    return list(targets)


def _abi_already_built(abi: str) -> bool:
    """判断 build_cache/cpXX/maxagent/ 是否已存在编译产物。

    判断条件：maxagent/ 包目录存在 + 至少一个扩展模块（.pyd/.so）。
    """
    pkg_dir = BUILD_CACHE_DIR / abi / 'maxagent'
    if not pkg_dir.is_dir():
        return False
    for ext in ('*.pyd', '*.so'):
        if any(pkg_dir.rglob(ext)):
            return True
    # 没有扩展模块也算"未构建"，避免把上一次失败的半成品当作有效产物
    return False


def _orchestrate_all_abis(
    abis: Sequence[str],
    abi_to_python: dict,
    version: str,
    skip_pyarmor: bool,
    skip_existing: bool,
    auto_install_pythons: bool,
    dry_run: bool,
    verbose: bool,
) -> Tuple[List[str], List[str]]:
    """通过 uv 多 Python 子进程，串行触发每个 ABI 的本机编译。

    最终由调用方再跑 ``--pack-only`` 聚合 mzp。

    :return: (成功列表, 失败列表)
    """
    if not _has_uv():
        raise RuntimeError(
            '--all-abis 模式需要 uv（https://docs.astral.sh/uv/）。\n'
            '请先安装 uv 或退回 --quick / 单 --abis 单 ABI 构建。'
        )

    py_targets = [abi_to_python[abi] for abi in abis if abi in abi_to_python]
    available_pys = set(_ensure_uv_pythons(py_targets, dry_run, auto_install_pythons))

    succeeded: List[str] = []
    failed: List[str] = []

    for abi in abis:
        py_ver = abi_to_python.get(abi)
        if py_ver is None:
            LOG.error('[%s] version.py 未提供 ABI_TO_PYTHON 映射，跳过', abi)
            failed.append(abi)
            continue

        if py_ver not in available_pys and not dry_run:
            LOG.error('[%s] 缺少 Python %s（uv 未安装），跳过', abi, py_ver)
            failed.append(abi)
            continue

        if skip_existing and _abi_already_built(abi):
            LOG.info('[%s] 已有产物，--skip-existing 跳过（如需重建请去除该参数）', abi)
            succeeded.append(abi)
            continue

        # 拼装子命令：uv run --python <ver> python release/build.py --abis cpXX
        sub_cmd: List[str] = [
            'uv', 'run', '--python', py_ver,
            '--no-project',  # 避免 uv 把 release/ 当成 editable 项目反复装
            'python', str(Path(__file__).resolve()),
            '--abis', abi,
        ]
        if skip_pyarmor:
            sub_cmd.append('--skip-pyarmor')
        if dry_run:
            sub_cmd.append('--dry-run')
        if verbose:
            sub_cmd.append('--verbose')

        LOG.info('=' * 60)
        LOG.info('[%s] 调度子进程: Python %s', abi, py_ver)
        LOG.info('=' * 60)
        LOG.debug('cmd: %s', ' '.join(sub_cmd))

        if dry_run:
            LOG.info('[dry-run] 将执行: %s', ' '.join(sub_cmd))
            succeeded.append(abi)
            continue

        # 不捕获子进程输出，让用户实时看到进度（Cython 编译可能耗时几十秒）
        proc = subprocess.run(sub_cmd)
        if proc.returncode == 0:
            LOG.info('[%s] ✅ 构建成功', abi)
            succeeded.append(abi)
        else:
            LOG.error('[%s] ❌ 构建失败（退出码 %s），其它 ABI 继续',
                      abi, proc.returncode)
            failed.append(abi)

    LOG.info('=' * 60)
    LOG.info('多 ABI 调度结束。成功 %d 个: %s', len(succeeded), succeeded)
    if failed:
        LOG.warning('失败 %d 个: %s', len(failed), failed)
    LOG.info('=' * 60)

    # 若至少有一个 ABI 成功，就尝试聚合 mzp（即便不全也允许出包，给用户选择权）
    if succeeded and not dry_run:
        LOG.info('开始聚合 mzp（仅含成功 ABI: %s）...', succeeded)
        # 直接复用 _make_mzp，绕过命令行再开一进程
        _make_mzp(version, succeeded, dry_run=False)

    return succeeded, failed


# -------------------------- 主入口 --------------------------


def _resolve_abis(args_abis: Optional[List[str]], quick: bool, supported: Sequence[str],
                  quick_default: str) -> List[str]:
    """解析最终要构建的 ABI 列表。"""
    if args_abis:
        unknown = [a for a in args_abis if a not in supported]
        if unknown:
            raise ValueError('未知 ABI: {}，支持: {}'.format(unknown, list(supported)))
        return list(args_abis)
    if quick:
        return [quick_default]
    return list(supported)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description='MaxAgent 一键打包')
    parser.add_argument('--version', help='覆盖 release/version.py 中的版本号', default=None)
    parser.add_argument('--quick', action='store_true', help='仅构建当前本地 ABI（开发期）')
    parser.add_argument('--abis', nargs='+', help='指定 ABI 列表（如 cp311 cp313）')
    parser.add_argument('--all-abis', action='store_true',
                        help='一键全 ABI 构建：通过 uv 调度 5 个 Python 子进程并聚合。'
                             '需要本机已装 uv，缺失的 Python 可加 --auto-install-pythons '
                             '让 uv 自动下载。')
    parser.add_argument('--auto-install-pythons', action='store_true',
                        help='[配合 --all-abis] 自动通过 uv 下载缺失的 Python 版本。')
    parser.add_argument('--skip-existing', action='store_true',
                        help='[配合 --all-abis] 跳过 build_cache/ 中已有产物的 ABI，'
                             '只构建缺失的。便于失败重试。')
    parser.add_argument('--skip-pyarmor', action='store_true',
                        help='跳过 PyArmor 加密（调试用，发布禁止）')
    parser.add_argument('--pack-only', action='store_true',
                        help='仅打包：跳过 Cython/PyArmor 步骤，'
                             '直接用 build_cache/ 现有产物聚合 mzp。'
                             'CI 矩阵作业完成后，pack 作业用此参数。')
    parser.add_argument('--allow-cross-abi', action='store_true',
                        help='[高级] 允许目标 ABI 与当前解释器 ABI 不一致。'
                             '默认禁止，避免 .pyc 字节码版本不匹配引发 '
                             'SystemError: unknown opcode。仅在 PyArmor '
                             '跨 ABI 模式或你确认自己清楚后果时启用。')
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
    abis = _resolve_abis(args.abis, args.quick, supported_abis, _current_abi())
    LOG.info('目标 ABIs: %s', abis)

    # 2.05) --all-abis 一键多 ABI：调度 uv 子进程矩阵，主进程不参与编译
    # ----------------------------------------------------------------
    # 设计：本机若装了 uv，可用 5 个 Python 各跑一次单 ABI 构建，再由主进程
    # 复用 _make_mzp() 聚合产物。每个子进程内的 _current_abi() 等于自己的目标
    # ABI，自然绕过 ABI 一致性校验。失败的 ABI 不阻塞其它 ABI（fail-soft）。
    if args.all_abis:
        if args.pack_only:
            LOG.error('--all-abis 与 --pack-only 互斥（前者会自动调用聚合）')
            return 7
        if args.quick:
            LOG.error('--all-abis 与 --quick 互斥（前者目标是全 ABI，后者是单 ABI）')
            return 7
        # 强制把目标拉回完整 supported_abis，忽略 --abis（避免半全半快混用）
        if args.abis:
            LOG.warning('--all-abis 模式忽略 --abis %s，目标为全部 supported_abis',
                        args.abis)
        target_abis = list(supported_abis)
        LOG.info('--all-abis 模式：目标 = %s', target_abis)
        try:
            succeeded, failed = _orchestrate_all_abis(
                abis=target_abis,
                abi_to_python=getattr(version_mod, 'ABI_TO_PYTHON', {}),
                version=version,
                skip_pyarmor=args.skip_pyarmor,
                skip_existing=args.skip_existing,
                auto_install_pythons=args.auto_install_pythons,
                dry_run=args.dry_run,
                verbose=args.verbose,
            )
        except RuntimeError as exc:
            # 典型场景：本机未装 uv。给用户一句话说明，避免抛 traceback。
            LOG.error('%s', exc)
            return 9
        if failed:
            # 部分失败：mzp 已含成功的 ABI，但仍以非零状态码反馈
            return 8
        LOG.info('--all-abis 全部完成 ✅')
        return 0

    # 2.1) ABI 一致性自检
    # ----------------------------------------------------------------
    # 核心约束：当前 Python 解释器只能产出与之 minor 版本完全一致的字节码 / Cython
    # 扩展。如果用户在 cp310 解释器下尝试构建 cp311 产物，结果会是
    # "目录命名 cp311 但内容是 cp310 字节码"，运行时报 SystemError: unknown opcode。
    # 历史教训：曾因此造成用户 .venv=3.10 但 quick_abi=cp311 的产物被加载到 Max
    # 3.11.12 时炸 unknown opcode，定位耗时不少。这里强校验避免再踩。
    #
    # 例外：--pack-only / --dry-run 不实际编译，无需校验解释器；
    #       --allow-cross-abi 是给未来跨 ABI 加密管线（如 PyArmor super mode）
    #       预留的逃生口，目前不应使用。
    current_abi = _current_abi()
    needs_compile = not args.pack_only and not args.dry_run
    cross_abi = [a for a in abis if a != current_abi]
    if needs_compile and cross_abi and not args.allow_cross_abi:
        LOG.error(
            'ABI 不匹配：当前解释器是 %s（%s），但目标 ABIs 包含 %s。\n'
            '    单个 Python 解释器无法编译其它 minor 版本的 .pyc / .pyd。\n'
            '    解决办法二选一：\n'
            '      A) 用对应版本的 Python 重新跑：例如目标 cp311 时\n'
            '         "C:\\Path\\To\\Python311\\python.exe" build.py --quick\n'
            '      B) 在 CI 矩阵中为每个 ABI 各起一台对应版本的 runner。\n'
            '    若你确认知道自己在做什么（如 PyArmor 跨 ABI 模式），\n'
            '    可加 --allow-cross-abi 旁路本检查。',
            current_abi, sys.version.split()[0], cross_abi,
        )
        return 6

    # 2.2) quick 模式信息提示
    # quick 模式总是按当前解释器 ABI 构建，给用户一个醒目提示，避免他们
    # 误以为产物 ABI 来自 pyproject.toml 的 quick_abi 配置项。
    if args.quick and needs_compile:
        LOG.warning(
            '【quick 模式】将按当前解释器 ABI=%s（Python %s）构建。\n'
            '    若你的 3ds Max 使用的不是 Python %s.x，此产物在 Max 中加载会报\n'
            '    SystemError: unknown opcode。请用 Max 对应版本的 Python 重跑。',
            current_abi, sys.version.split()[0],
            '{}.{}'.format(sys.version_info.major, sys.version_info.minor),
        )

    # 3) 检查源包目录
    if not SOURCE_PKG_DIR.exists():
        LOG.error('源包目录不存在: %s', SOURCE_PKG_DIR)
        return 2

    # 4) 加载 Cython 白名单
    whitelist = _read_cython_whitelist()

    # 5) 准备 build_cache / dist
    _ensure_dir(BUILD_CACHE_DIR)
    _ensure_dir(DIST_DIR)

    # --pack-only 短路：跳过编译，直接走第 7 步
    # CI 场景下，矩阵作业已把各 ABI 产物下载回 build_cache/，pack 作业只需聚合
    if args.pack_only:
        LOG.info('--pack-only 模式：跳过 Cython/PyArmor，直接打包')
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

    # 6) 逐 ABI 构建
    built_abis: List[str] = []
    current_abi = _current_abi()
    for abi in abis:
        # 简化策略：当 ABI != 当前 Python 时，第一版仅在本机产出占位（不交叉编译）
        if abi != current_abi and not args.dry_run:
            LOG.warning(
                '[%s] 当前 Python 是 %s，无法本机编译此 ABI 的扩展模块。\n'
                '    第一版策略：跳过该 ABI 的 Cython 编译，'
                '由 CI 在对应 Python 版本节点完成。',
                abi, current_abi,
            )
            # 仍创建占位目录，以便文档/调试看出预期结构
            _clean_dir(BUILD_CACHE_DIR / abi)
            (BUILD_CACHE_DIR / abi / 'PLACEHOLDER.txt').write_text(
                'ABI {} placeholder. Build on a Python {} runner.\n'.format(
                    abi, version_mod.ABI_TO_PYTHON.get(abi, '?'),
                ),
                encoding='utf-8',
            )
            continue
        try:
            _build_one_abi(abi, version, whitelist, args.dry_run, args.skip_pyarmor)
            built_abis.append(abi)
        except Exception as exc:
            LOG.exception('[%s] 构建失败: %s', abi, exc)
            return 3

    # 7) 打包 mzp
    if not built_abis and not args.dry_run:
        LOG.error('没有任何 ABI 构建成功，跳过 mzp 打包')
        return 4
    _make_mzp(version, built_abis or list(abis), args.dry_run)

    LOG.info('全部完成 ✅')
    return 0


if __name__ == '__main__':
    sys.exit(main())
