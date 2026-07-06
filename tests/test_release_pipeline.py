#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""release/build.py 打包流水线回归测试（开源版）。

开源化后打包流程被大幅简化：不再有 Cython / PyArmor / py_compile，
只剩下"复制源码 + 打包 mzp"两步。这里只回归以下核心契约：

- helper 函数的边界行为（版本号 bump / ABI 解析）
- 端到端产物结构：mzp 顶层含 ``mzp.run`` / ``mzp_install.ms`` /
  ``macros/*.mcr`` / ``runtime/cpXX/maxagent/*.py``
- 每个 ABI 目录内容一致，且都是纯源码（无 .pyd / .so / .pyc）
- ``--pack-only`` 可以复用已有 build_cache
"""

from __future__ import absolute_import
from __future__ import print_function

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

_TEST_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _TEST_DIR.parent
_RELEASE_DIR = _PROJECT_ROOT / 'release'
_BUILD_SCRIPT = _RELEASE_DIR / 'build.py'

# 把 release 目录加入 sys.path 才能 import build 模块
if str(_RELEASE_DIR) not in sys.path:
    sys.path.insert(0, str(_RELEASE_DIR))


# ============================================================
# Helper 单元测试
# ============================================================


class TestBuildHelpers:
    """测试 release/build.py 内部辅助函数。"""

    def test_load_version_module(self):
        import build  # type: ignore

        mod = build._load_version_module()
        assert isinstance(mod.__version__, str)
        assert len(mod.SUPPORTED_ABIS) >= 1

    def test_resolve_abis_default(self):
        import build  # type: ignore

        supported = ('cp37', 'cp39', 'cp311')
        assert build._resolve_abis(None, supported) == list(supported)

    def test_resolve_abis_specific(self):
        import build  # type: ignore

        supported = ('cp37', 'cp39', 'cp311')
        assert build._resolve_abis(['cp311'], supported) == ['cp311']

    def test_resolve_abis_unknown_raises(self):
        import build  # type: ignore

        with pytest.raises(ValueError):
            build._resolve_abis(['cp99'], ('cp37', 'cp311'))

    def test_bump_version_valid(self, tmp_path, monkeypatch):
        import build  # type: ignore

        fake_version = tmp_path / 'version.py'
        fake_version.write_text(
            "__version__ = '0.0.1'\nSUPPORTED_ABIS = ('cp311',)\n",
            encoding='utf-8',
        )
        monkeypatch.setattr(build, 'RELEASE_DIR', tmp_path)
        build._bump_version('1.2.3')
        assert "__version__ = '1.2.3'" in fake_version.read_text(encoding='utf-8')

    def test_bump_version_bad_format(self, tmp_path, monkeypatch):
        import build  # type: ignore

        monkeypatch.setattr(build, 'RELEASE_DIR', tmp_path)
        with pytest.raises(ValueError):
            build._bump_version('not-a-version')


# ============================================================
# 端到端打包（真实执行 build.py 主入口）
# ============================================================


@pytest.mark.slow
class TestBuildPipeline:
    """走一遍完整的 build.py，校验 mzp 产物结构。"""

    @pytest.fixture(autouse=True)
    def _clean(self):
        # 每个用例开始前清空 build_cache / dist，用完不再清（便于人工排查）
        for name in ('build_cache', 'dist'):
            d = _RELEASE_DIR / name
            if d.exists():
                shutil.rmtree(d)
        yield

    def _run_build(self, *args):
        cmd = [sys.executable, str(_BUILD_SCRIPT)] + list(args)
        proc = subprocess.run(
            cmd,
            cwd=str(_PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=300,
        )
        return proc

    def test_full_build_produces_mzp(self):
        proc = self._run_build('--verbose')
        assert proc.returncode == 0, (
            'build.py 失败:\n--- stdout ---\n{}\n--- stderr ---\n{}'.format(
                proc.stdout, proc.stderr,
            )
        )
        dist_files = list((_RELEASE_DIR / 'dist').glob('*.mzp'))
        assert len(dist_files) == 1, 'dist/ 下应恰好有一个 mzp 产物'
        mzp = dist_files[0]
        assert mzp.stat().st_size > 0, 'mzp 是空文件'

        # 校验 mzp 顶层结构
        with zipfile.ZipFile(str(mzp)) as zf:
            names = set(zf.namelist())
        assert 'mzp.run' in names, 'mzp 顶层缺 mzp.run'
        assert 'mzp_install.ms' in names, 'mzp 顶层缺 mzp_install.ms'
        assert any(n.startswith('macros/') and n.endswith('.mcr') for n in names), (
            'mzp 内缺少 macros/*.mcr'
        )

        # 校验每个 supported ABI 都有 maxagent/__init__.py
        import build  # type: ignore
        version_mod = build._load_version_module()
        for abi in version_mod.SUPPORTED_ABIS:
            init_arc = 'runtime/{}/maxagent/__init__.py'.format(abi)
            assert init_arc in names, '缺少 {}'.format(init_arc)

    def test_build_produces_pure_source(self):
        proc = self._run_build('--verbose')
        assert proc.returncode == 0, proc.stderr

        # 断言：build_cache / mzp 内不应出现任何 .pyd / .so / .pyc
        forbidden_suffixes = ('.pyd', '.so', '.pyc', '.pyo')
        for f in (_RELEASE_DIR / 'build_cache').rglob('*'):
            if f.is_file():
                assert not f.name.endswith(forbidden_suffixes), (
                    'build_cache 出现被禁止的产物: {}'.format(f)
                )

        mzp = next((_RELEASE_DIR / 'dist').glob('*.mzp'))
        with zipfile.ZipFile(str(mzp)) as zf:
            for n in zf.namelist():
                if n.startswith('runtime/'):
                    assert not n.endswith(forbidden_suffixes), (
                        'mzp 内出现被禁止的产物: {}'.format(n)
                    )

    def test_pack_only_reuses_build_cache(self):
        # 先跑一次完整构建
        assert self._run_build('--verbose').returncode == 0
        # 记录首次 mzp 大小
        mzp1 = next((_RELEASE_DIR / 'dist').glob('*.mzp'))
        first_size = mzp1.stat().st_size
        mzp1.unlink()

        # 再跑 --pack-only；应能仅凭 build_cache 重建 mzp
        proc = self._run_build('--pack-only', '--verbose')
        assert proc.returncode == 0, proc.stderr
        mzp2 = next((_RELEASE_DIR / 'dist').glob('*.mzp'))
        assert mzp2.stat().st_size == first_size

    def test_dry_run_produces_nothing(self):
        proc = self._run_build('--dry-run')
        assert proc.returncode == 0
        assert not (_RELEASE_DIR / 'dist').exists() or not list(
            (_RELEASE_DIR / 'dist').glob('*.mzp')
        ), 'dry-run 不应产出真实 mzp'
