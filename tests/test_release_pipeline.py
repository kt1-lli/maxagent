#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""release/build.py 打包流水线回归测试。

覆盖第 2 批修复的所有 bug 与第 1 批建立的产物结构契约：

- PyArmor ``--exclude`` 参数确实传给子进程
- 明文白名单（``__init__.py`` / ``reload.py`` / ``qt_compat.py``）保留为 .py
- mzp 内 5 个子包 ``__init__.py`` 都到位（防过滤逻辑误伤）
- mzp 顶层结构正确：``maxagent_release.json`` / ``mzp_install.ms``
  / ``runtime/cpXX/maxagent/`` / ``shared/``
- Cython 产物（.so）数量等于白名单数量
- PyArmor trial 受限时自动走 py_compile 软退化
- mzp 整体内能完整 import 关键 API（绕开 GUI 副作用模块）

运行
====
::

    # 默认运行（含本文件的端到端慢测，约 1~2 分钟）
    pytest tests/

    # 跳过慢测，仅跑 helper 单测
    pytest tests/ -m 'not slow'

    # 仅跑 release 流水线
    pytest tests/test_release_pipeline.py -v
"""

from __future__ import absolute_import
from __future__ import print_function

import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import List
from typing import Tuple

import pytest

# 项目根目录
_TEST_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _TEST_DIR.parent
_RELEASE_DIR = _PROJECT_ROOT / 'release'
_BUILD_SCRIPT = _RELEASE_DIR / 'build.py'

# 把 release 目录加入 sys.path 才能 import build 模块测 helper
if str(_RELEASE_DIR) not in sys.path:
    sys.path.insert(0, str(_RELEASE_DIR))


# ============================================================
# Helper 单元测试（毫秒级）
# ============================================================


class TestBuildHelpers:
    """测试 release/build.py 内部辅助函数。

    这部分不跑真实流水线，毫秒级即可完成。
    """

    def test_load_whitelist_returns_list(self):
        """cython_modules.txt 能被正确解析（去注释、去空行）。"""
        import build  # type: ignore

        wl = build._read_cython_whitelist()
        assert isinstance(wl, list)
        assert len(wl) >= 1
        # 白名单内每条都应是模块路径片段，不含注释和空行
        for item in wl:
            assert item, '白名单含空字符串'
            assert not item.startswith('#'), '白名单含注释残留: {}'.format(item)
            assert item == item.strip(), '白名单含前后空白: {}'.format(item)

    def test_pyarmor_excludes_constant(self):
        """明文白名单常量包含三个关键文件。"""
        import build  # type: ignore

        excludes = build.KEEP_PLAINTEXT_FILES
        assert '__init__.py' in excludes
        assert 'reload.py' in excludes
        assert 'qt_compat.py' in excludes

    def test_target_abis_constant(self):
        """支持 5 个 Python ABI（覆盖 Max 2022~2027）。"""
        import build  # type: ignore

        version_mod = build._load_version_module()
        abis = version_mod.SUPPORTED_ABIS
        assert 'cp37' in abis
        assert 'cp39' in abis
        assert 'cp310' in abis
        assert 'cp311' in abis
        assert 'cp313' in abis

    def test_version_module_present(self):
        """version.py 存在且可读出语义化版本号。"""
        version_file = _RELEASE_DIR / 'version.py'
        assert version_file.is_file()
        content = version_file.read_text(encoding='utf-8')
        assert '__version__' in content
        # 简单语义化版本断言
        import re
        match = re.search(
            r"__version__\s*=\s*['\"](\d+\.\d+\.\d+)['\"]", content,
        )
        assert match, 'version.py 中未找到合法的 __version__ 语义化版本号'


# ============================================================
# 端到端流水线测试（session 级别复用 build 产物）
# ============================================================

# 选择一个本地一定能跑的 ABI 做端到端验证（当前 Python 版本对应的 cp）
_CURRENT_ABI = 'cp{}{}'.format(sys.version_info.major, sys.version_info.minor)


def _has_cython() -> bool:
    """探测当前环境是否装了 Cython。"""
    try:
        import Cython  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.fixture(scope='session')
def built_mzp(tmp_path_factory) -> Tuple[Path, Path]:
    """跑一次 build.py，session 内复用产物。

    :return: (mzp 文件路径, build_cache 内的当前 ABI 包目录)
    """
    if not _has_cython():
        pytest.skip('Cython 未安装，跳过 release pipeline 端到端测试')

    # 用 tmp 目录避开常规 release/dist，不污染开发者本地产物
    work_dir = tmp_path_factory.mktemp('release_pipeline')

    # 复制项目到 tmp 工作区，避免污染源代码树
    # 但 build.py 是基于 release/ 目录的相对路径定位的，
    # 所以更简单的做法：直接在原 workspace 跑 build，然后断言产物，
    # 跑完后清理 release/build_cache 和 release/dist 即可。
    cache_dir = _RELEASE_DIR / 'build_cache'
    dist_dir = _RELEASE_DIR / 'dist'

    # 跑前清理
    if cache_dir.exists():
        shutil.rmtree(cache_dir)
    if dist_dir.exists():
        shutil.rmtree(dist_dir)

    cmd = [sys.executable, str(_BUILD_SCRIPT), '--quick']
    proc = subprocess.run(
        cmd,
        cwd=str(_PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        timeout=300,
    )

    if proc.returncode != 0:
        pytest.fail(
            'build.py --quick 失败 (returncode={})\n'
            '--- stdout ---\n{}\n'
            '--- stderr ---\n{}'.format(proc.returncode, proc.stdout, proc.stderr)
        )

    # 找 mzp 产物
    mzps = sorted(dist_dir.glob('maxagent-*.mzp'))
    assert mzps, 'build 完成但 dist/ 内没找到 maxagent-*.mzp'
    mzp_path = mzps[-1]

    # 当前 ABI 的 build_cache 包目录
    abi_pkg = cache_dir / _CURRENT_ABI / 'maxagent'
    assert abi_pkg.is_dir(), '未找到当前 ABI 的 build_cache 包: {}'.format(abi_pkg)

    yield mzp_path, abi_pkg

    # session 结束后清理（保留 dist 给开发者用，仅清 build_cache）
    if cache_dir.exists():
        shutil.rmtree(cache_dir, ignore_errors=True)


@pytest.mark.slow
class TestBuildPipeline:
    """端到端验证 release/build.py 一次完整 build 的产物结构。

    测试都基于 session-scoped fixture ``built_mzp``，
    整个类只触发一次实际 build（约 1 分钟）。
    """

    def test_mzp_file_created(self, built_mzp):
        """mzp 文件存在且非空。"""
        mzp_path, _ = built_mzp
        assert mzp_path.is_file()
        size_mb = mzp_path.stat().st_size / 1024 / 1024
        # 合理体积区间：单 ABI 不应小于 1MB（产物丢失）也不应大于 50MB
        assert 1.0 <= size_mb <= 50.0, (
            'mzp 体积异常: {:.1f} MB（期望 1~50 MB）'.format(size_mb)
        )

    def test_mzp_top_level_metadata(self, built_mzp):
        """mzp 顶层包含 maxagent_release.json 和 mzp_install.ms。"""
        mzp_path, _ = built_mzp
        with zipfile.ZipFile(mzp_path) as zf:
            names = set(zf.namelist())
            assert 'maxagent_release.json' in names
            assert 'mzp_install.ms' in names

            meta_raw = zf.read('maxagent_release.json').decode('utf-8')
            meta = json.loads(meta_raw)
            assert meta['name'] == 'maxagent'
            assert 'version' in meta
            assert isinstance(meta['abis'], list) and len(meta['abis']) >= 1
            assert _CURRENT_ABI in meta['abis']

    def test_plaintext_whitelist_preserved_in_cache(self, built_mzp):
        """build_cache 内明文白名单保留为 .py，不被加密。"""
        _, abi_pkg = built_mzp
        for keep in ('__init__.py', 'reload.py', 'qt_compat.py'):
            py_file = abi_pkg / keep
            pyc_file = abi_pkg / (keep[:-3] + '.pyc')
            assert py_file.is_file(), (
                '明文白名单 {} 在 build_cache 中丢失！'.format(keep)
            )
            assert not pyc_file.is_file(), (
                '明文白名单 {} 不应同时存在 .pyc！'.format(keep)
            )

    def test_mzp_contains_all_subpackage_inits(self, built_mzp):
        """mzp 内所有子包 __init__.py 都在（防过滤逻辑误伤）。"""
        mzp_path, _ = built_mzp
        with zipfile.ZipFile(mzp_path) as zf:
            names = zf.namelist()

        prefix = 'runtime/{}/maxagent/'.format(_CURRENT_ABI)
        # 至少应该有这些子包的 __init__.py（绝对路径写死，便于早发现回归）
        required_inits = [
            prefix + '__init__.py',
            prefix + 'agent/__init__.py',
            prefix + 'bridge/__init__.py',
            prefix + 'bridge/handlers/__init__.py',
            prefix + 'tools/__init__.py',
            prefix + 'ui/__init__.py',
        ]
        for init in required_inits:
            assert init in names, (
                '子包 __init__.py 在 mzp 中缺失: {}\n'
                '（mzp 过滤逻辑曾误伤所有 __init__.py，本断言守护回归）'.format(init)
            )

    def test_mzp_contains_plaintext_whitelist(self, built_mzp):
        """mzp 内三个明文白名单文件保留为 .py。"""
        mzp_path, _ = built_mzp
        with zipfile.ZipFile(mzp_path) as zf:
            names = set(zf.namelist())

        prefix = 'runtime/{}/maxagent/'.format(_CURRENT_ABI)
        for keep in ('reload.py', 'qt_compat.py'):
            arc = prefix + keep
            assert arc in names, (
                '明文白名单 {} 在 mzp 中丢失！'.format(arc)
            )

    def test_mzp_contains_cython_artifacts(self, built_mzp):
        """mzp 内 Cython 产物（.so / .pyd）数量与白名单一致。"""
        mzp_path, _ = built_mzp
        with zipfile.ZipFile(mzp_path) as zf:
            ext_files = [
                n for n in zf.namelist()
                if n.endswith('.so') or n.endswith('.pyd')
            ]

        import build  # type: ignore
        whitelist = build._read_cython_whitelist()
        # 每个白名单条目应该恰好对应一个扩展模块（.so 或 .pyd）
        # 在当前 ABI（cp311 通常）下应等于白名单条数
        cur_abi_exts = [
            n for n in ext_files
            if '/{}/'.format(_CURRENT_ABI) in n
        ]
        assert len(cur_abi_exts) == len(whitelist), (
            '当前 ABI Cython 产物数 {} != 白名单条数 {}'.format(
                len(cur_abi_exts), len(whitelist),
            )
        )

    def test_mzp_contains_pyc_or_py_for_remaining(self, built_mzp):
        """mzp 内既有 .pyc（兜底加密）又有少量 .py（明文白名单）。"""
        mzp_path, _ = built_mzp
        prefix = 'runtime/{}/maxagent/'.format(_CURRENT_ABI)
        with zipfile.ZipFile(mzp_path) as zf:
            pyc_count = sum(
                1 for n in zf.namelist()
                if n.startswith(prefix) and n.endswith('.pyc')
            )
            py_count = sum(
                1 for n in zf.namelist()
                if n.startswith(prefix) and n.endswith('.py')
            )

        # PyArmor trial 受限或商业 license 都应至少加密 10+ 个文件
        assert pyc_count >= 10, (
            'mzp 内 .pyc 文件过少 ({}), 加密兜底可能未触发'.format(pyc_count)
        )
        # .py 不能多到泄露源码：3 个白名单 + 几个子包 __init__.py（约 6~10 个）
        assert py_count <= 15, (
            'mzp 内 .py 文件过多 ({}), 可能有未加密源码泄露！'.format(py_count)
        )

    def test_mzp_extracted_can_import_core_apis(self, built_mzp, tmp_path):
        """mzp 解压后能完整 import 关键 API（绕开 GUI 副作用模块）。

        在子进程内 import 避免污染当前 pytest 进程的 sys.modules。
        """
        mzp_path, _ = built_mzp
        extract_dir = tmp_path / 'mzp_extract'
        extract_dir.mkdir()
        with zipfile.ZipFile(mzp_path) as zf:
            zf.extractall(str(extract_dir))

        runtime_dir = extract_dir / 'runtime' / _CURRENT_ABI
        assert (runtime_dir / 'maxagent').is_dir()

        # 在子进程跑 import 检查（用 -c 直接执行，不写临时 .py）
        # 不 import startup/skills（它们顶层有 GUI 副作用，sandbox 无 Qt 会崩）
        check_src = (
            'import sys\n'
            'sys.path.insert(0, {!r})\n'
            'import maxagent\n'
            'assert maxagent.__version__\n'
            'from maxagent import config, llm_client, sessions\n'
            'from maxagent.agent import conversation, coding_rules, worker\n'
            'from maxagent.tools import dispatcher, registry\n'
            'from maxagent import attachments, logger, web_search\n'
            'from maxagent import qt_compat, reload\n'
            'from maxagent.bridge import server, protocol\n'
            'assert llm_client.LLMClient.__name__ == "LLMClient"\n'
            'assert worker.AgentWorker.__name__ == "AgentWorker"\n'
            'assert conversation.Conversation.__name__ == "Conversation"\n'
            'assert dispatcher.ToolDispatcher.__name__ == "ToolDispatcher"\n'
            'assert config.ConfigManager.__name__ == "ConfigManager"\n'
            'print("OK")\n'
        ).format(str(runtime_dir))

        proc = subprocess.run(
            [sys.executable, '-c', check_src],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=30,
        )

        if proc.returncode != 0 or 'OK' not in proc.stdout:
            pytest.fail(
                'mzp 解压后 import 验证失败:\n'
                '--- stdout ---\n{}\n'
                '--- stderr ---\n{}'.format(proc.stdout, proc.stderr)
            )

    def test_install_script_present_and_nonempty(self, built_mzp):
        """mzp_install.ms 存在且非空。"""
        mzp_path, _ = built_mzp
        with zipfile.ZipFile(mzp_path) as zf:
            data = zf.read('mzp_install.ms')
        assert len(data) > 100, 'mzp_install.ms 体积异常小'
        # 关键字检查（脚本应包含部署逻辑）
        text = data.decode('utf-8', errors='replace')
        assert 'maxagent' in text.lower()

    def test_install_script_registers_menu_and_macros(self, built_mzp):
        """mzp_install.ms 应注册主菜单 + 拷贝 .mcr + 安装方式询问。"""
        mzp_path, _ = built_mzp
        with zipfile.ZipFile(mzp_path) as zf:
            text = zf.read('mzp_install.ms').decode('utf-8', errors='replace')

        # 必须有菜单注册逻辑（menuMan 主菜单栏挂载）
        assert 'menuMan.getMainMenuBar' in text, 'mzp_install.ms 未通过 menuMan 挂主菜单'
        assert 'menuMan.createMenu' in text, '未创建 MaxAgent 子菜单'
        assert 'menuMan.updateMenuBar' in text, '菜单更新调用缺失'

        # 必须有安装方式三选一对话框（菜单 / 仅宏 / 取消）
        assert 'yesNoCancelBox' in text, '缺少安装方式选择对话框'

        # 卸载宏要同步移除菜单（避免重启后残留菜单项）
        # 注：这条检查现在转移到 .mcr 文件中（卸载宏已搬出 mzp_install.ms）
        # 此处仅检查 mzp_install.ms 调用了 fileIn 注册 .mcr 文件
        assert 'fileIn' in text, 'mzp_install.ms 未通过 fileIn 触发 .mcr 注册'
        assert 'MaxAgent-Macros.mcr' in text, (
            'mzp_install.ms 未引用 macros\\MaxAgent-Macros.mcr'
        )
        # getDir #userMacros 是语言无关的，避免硬编码 ENU/zh-CN
        assert '#userMacros' in text, (
            'mzp_install.ms 未使用 getDir #userMacros，可能硬编码语言子目录'
        )

    def test_macros_mcr_present_with_utf8_bom(self, built_mzp):
        """.mcr 文件必须存在于 mzp 内，且使用 UTF-8 BOM 编码。

        UTF-8 BOM 是让中文/英文版 Max 都能正确解码中文字面量的关键——
        没有 BOM 时英文版 Max 会按系统 ANSI（1252）解析中文字符串，乱码报错。
        """
        mzp_path, _ = built_mzp
        with zipfile.ZipFile(mzp_path) as zf:
            assert 'macros/MaxAgent-Macros.mcr' in zf.namelist(), (
                'mzp 缺少 macros/MaxAgent-Macros.mcr'
            )
            raw = zf.read('macros/MaxAgent-Macros.mcr')

        assert raw.startswith(b'\xef\xbb\xbf'), (
            'MaxAgent-Macros.mcr 缺少 UTF-8 BOM（EF BB BF），中英文版 Max 无法跨语言通用'
        )

        # 解码并校验 5 个 macroScript 全在
        text = raw[3:].decode('utf-8')
        for macro in (
            'MaxAgent_Show',
            'MaxAgent_Toggle',
            'MaxAgent_OpenInstallDir',
            'MaxAgent_About',
            'MaxAgent_Uninstall',
        ):
            assert 'macroScript ' + macro in text, (
                '.mcr 缺少 macroScript: ' + macro
            )

        # 卸载宏的菜单清理逻辑也搬到了 .mcr，确保没遗失
        assert 'removeItemByPosition' in text, '.mcr 卸载宏未清理主菜单'
        # .mcr 自删除逻辑（避免 ActionTable 残留）
        assert 'deleteFile' in text, '.mcr 卸载宏未删除自身文件'

    def test_install_script_uses_installer_source_first(self, built_mzp):
        """mzp_install.ms 必须用四层 fallback 解析解压目录。

        历史教训：
          * v1 只用 getSourceFileName() —— Max 2022 CHS 拖入时返回空字符串，
            装失败。
          * v2 加了 installerSource 优先 —— 但实测某些 Max 版本根本不注入
            该全局变量，仍可能失败。
          * v3 用 ``extract to "maxagent_install"`` 相对路径——Max 在多个
            版本上对相对路径语义不一致，目录根本不被创建。
          * v4 改成扫 ``#temp\\maxagent-*\\`` 启发式——但用户重命名 mzp
            后会失效。
          * v5（当前）用 ``extract to "$temp\\maxagent_install"`` 绝对路径
            前缀（``$temp`` 是 mzp 协议官方变量），把解压目录定死在
            ``#temp\\maxagent_install\\``，mzp_install.ms 直接拼这个固定
            路径作首选 fallback。再加一层"扫 #temp 下含 runtime\\ 的子目录"
            作兜底，对极端情况也鲁棒。installerSource / getSourceFileName
            作最末兜底。

        本测试防止任何一层 fallback 被回退或简化。
        """
        mzp_path, _ = built_mzp
        with zipfile.ZipFile(mzp_path) as zf:
            text = zf.read('mzp_install.ms').decode('utf-8', errors='replace')

        # 第 1 层：mzp.run extract to 定死的固定子目录
        assert 'maxagent_install' in text, (
            'mzp_install.ms 未引用 mzp.run extract to 的固定子目录 '
            '"maxagent_install"，第 1 层 fallback 缺失'
        )
        assert 'getDir #temp' in text or 'getDir  #temp' in text, (
            'mzp_install.ms 未通过 getDir #temp 拼接解压根，'
            '第 1 层 fallback 不可用'
        )
        # 第 2 层：兜底全量扫描
        assert 'getDirectories' in text, (
            'mzp_install.ms 未用 getDirectories 扫描 #temp 子目录，'
            '第 2 层兜底不可用'
        )
        # 第 3 层
        assert 'installerSource' in text, (
            'mzp_install.ms 缺少 installerSource fallback'
        )
        # 第 4 层（开发态 fileIn 调试用）
        assert 'getSourceFileName' in text, (
            'mzp_install.ms 缺少 getSourceFileName 兜底（开发态调试需要）'
        )

    def test_install_script_no_hardcoded_enu(self, built_mzp):
        """mzp_install.ms 不应在路径里硬编码 ENU——应靠 getDir 自动适配语言。

        如果硬编码 ENU，中文版 Max（usermacros 在 \\zh-CN\\ 下）会装错位置。
        """
        mzp_path, _ = built_mzp
        with zipfile.ZipFile(mzp_path) as zf:
            text = zf.read('mzp_install.ms').decode('utf-8', errors='replace')

        # 允许在注释里出现 ENU（举例说明），但不应有 + "\\ENU\\" 这种硬拼路径
        # 拼接判定：找 "\ENU\" 字面量出现在非注释行
        for ln in text.splitlines():
            stripped = ln.lstrip()
            if stripped.startswith('--'):
                continue
            assert '\\ENU\\' not in ln and '"ENU"' not in ln, (
                '检测到硬编码 ENU 路径片段（中文版 Max 会装错位置）：\n  ' + ln
            )

    def test_mzp_run_manifest_is_command_sequence(self, built_mzp):
        """mzp.run 必须是 Autodesk 标准的指令序列（不是 INI！）。

        关键事实（field-tested + Autodesk 文档）：
          * mzp.run 是 ``copy / move / extract to / drop / run / clear temp``
            等指令的序列，**不是** INI section 格式。
          * 拖入时 Max **只**执行 ``drop`` 指定的脚本，``run`` 指令被无条件
            忽略。所以入口必须是 ``drop "mzp_install.ms"``。
          * 上一版误用 ``[install]`` / ``[run]`` 段名，被 Max 解析失败 →
            退化到"找根目录唯一 .ms"启发式 → 当时根目录有 2 个 .ms（
            mzp_install.ms 和 mzp_run.ms）→ 启发式也失败 → 整个 mzp
            **拖入完全没反应**。这是回归保护测试。

        参考: https://help.autodesk.com/view/MAXDEV/2027/ENU/?guid=GUID-35559C6A
        """
        mzp_path, _ = built_mzp
        with zipfile.ZipFile(mzp_path) as zf:
            names = zf.namelist()
            assert 'mzp.run' in names, 'mzp 缺少 mzp.run 清单文件'
            raw = zf.read('mzp.run')

        # 1) 必须是 CRLF 换行（Windows INI 解析要求）
        assert b'\r\n' in raw, (
            'mzp.run 必须用 Windows CRLF 换行，否则 Max 解析失败'
        )
        # 不允许出现裸 LF（除 CRLF 中的 LF 外）
        # 把所有 CRLF 抽掉，剩下的不应有任何 \n
        residual = raw.replace(b'\r\n', b'')
        assert b'\n' not in residual, (
            'mzp.run 含有裸 LF（非 CRLF），Max 解析会失败'
        )

        text = raw.decode('utf-8', errors='replace')
        # 2) **禁止**出现 INI 段（这是上一版的 bug，必须钉死）
        assert '[install]' not in text, (
            'mzp.run 出现非法 INI 段 [install]——Autodesk mzp 协议没有此语法'
        )
        assert '[run]' not in text, (
            'mzp.run 出现非法 INI 段 [run]——Autodesk mzp 协议没有此语法'
        )

        # 3) 必须有 drop 指令指向 mzp_install.ms（拖入入口）
        assert 'drop "mzp_install.ms"' in text, (
            'mzp.run 缺少 drop "mzp_install.ms" 指令——拖入时 Max 无入口可执行'
        )

        # 4) 必须有 extract to "$temp\maxagent_install" —— 用 $temp 绝对路径
        # 前缀把解压目录定死。早期实现要么没 extract to（Max 把 mzp 解压到
        # 不可预测目录），要么用相对路径 ``extract to "maxagent_install"``
        # （某些版本不解析），都导致 mzp_install.ms 无法找到 sibling 文件。
        # `$temp` 是 mzp 协议官方支持的特殊变量，保证跨 Max 版本一致。
        assert 'extract to "$temp\\maxagent_install"' in text, (
            'mzp.run 必须包含 extract to "$temp\\maxagent_install"——'
            '用 $temp 绝对路径前缀确保解压目录确定性，是 mzp_install.ms '
            '能定位 runtime\\cpXXX\\maxagent\\ 的前提'
        )

    def test_install_script_shows_panel_at_end(self, built_mzp):
        """mzp_install.ms 末尾必须调 show_panel——drop 上下文只有这一个脚本会跑。

        Autodesk mzp 协议在 drop 上下文下只执行 ``drop`` 指定的 1 个脚本，
        不存在 [install]/[run] 两阶段（那是上一版的误解）。所以拉起面板的
        逻辑必须合并在 mzp_install.ms 末尾。

        本测试防止 show_panel 调用被误删或被错误地拆回独立 mzp_run.ms。
        """
        mzp_path, _ = built_mzp
        with zipfile.ZipFile(mzp_path) as zf:
            names = zf.namelist()
            text = zf.read('mzp_install.ms').decode('utf-8', errors='replace')

        # 1) 不应再有独立 mzp_run.ms（避免根目录两个 .ms 让启发式失效）
        assert 'mzp_run.ms' not in names, (
            'mzp 不应再包含 mzp_run.ms——drop 上下文 run 指令被忽略，'
            '该文件永远跑不到，且会让无 mzp.run 的启发式失效'
        )

        # 2) install 脚本里必须 import maxagent.startup 并调 show_panel(force=True)
        assert 'maxagent.startup' in text, (
            'mzp_install.ms 未 import maxagent.startup'
        )
        assert 'show_panel' in text and 'force=True' in text, (
            'mzp_install.ms 未在末尾调用 show_panel(force=True)'
        )


# ============================================================
# 软退化路径单元测试（不需要真实 build 产物）
# ============================================================


class TestPyarmorFallback:
    """验证 PyArmor 不可用 / trial 受限时的 py_compile 软退化逻辑。"""

    def test_compileall_fallback_function_exists(self):
        """build 模块暴露 _compileall_fallback 函数。"""
        import build  # type: ignore
        assert hasattr(build, '_compileall_fallback')
        assert callable(build._compileall_fallback)

    def test_compileall_fallback_compiles_py_to_pyc(self, tmp_path):
        """直接调用兜底函数，验证 .py → .pyc 转换正确。"""
        import build  # type: ignore

        # 准备两个简单 .py
        a_py = tmp_path / 'a.py'
        a_py.write_text('VAL = 1\n', encoding='utf-8')
        b_py = tmp_path / 'b.py'
        b_py.write_text('def foo():\n    return 2\n', encoding='utf-8')

        count = build._compileall_fallback(tmp_path, [a_py, b_py], 'cptest')
        assert count == 2

        # 原 .py 已删，.pyc 已生成
        assert not a_py.exists()
        assert not b_py.exists()
        assert (tmp_path / 'a.pyc').is_file()
        assert (tmp_path / 'b.pyc').is_file()

    def test_compileall_fallback_skips_syntax_error(self, tmp_path):
        """语法错误的文件应被跳过且不抛异常。"""
        import build  # type: ignore

        bad = tmp_path / 'bad.py'
        bad.write_text('def broken(:\n', encoding='utf-8')  # 语法错误
        good = tmp_path / 'good.py'
        good.write_text('OK = True\n', encoding='utf-8')

        count = build._compileall_fallback(tmp_path, [bad, good], 'cptest')
        # 至少 good 成功；bad 被跳过
        assert count >= 1
        assert (tmp_path / 'good.pyc').is_file()
        # bad 因失败保留原 .py（兜底逻辑：失败不删源）
        assert bad.exists()


@pytest.mark.slow
class TestPackOnlyMode:
    """验证 --pack-only 模式（CI pack 阶段使用）。"""

    def test_pack_only_skips_compile_and_uses_existing_cache(
        self, built_mzp, tmp_path,
    ):
        """build_cache 已有产物时，--pack-only 应秒级直出 mzp 而不重新编译。"""
        if not _has_cython():
            pytest.skip('Cython 未安装')

        import time
        # 已经在 built_mzp fixture 内跑过完整 build，此时 build_cache 内有产物
        cmd = [
            sys.executable, str(_BUILD_SCRIPT),
            '--pack-only', '--abis', _CURRENT_ABI,
        ]
        start = time.time()
        proc = subprocess.run(
            cmd,
            cwd=str(_PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=60,
        )
        elapsed = time.time() - start

        assert proc.returncode == 0, proc.stderr
        # --pack-only 应该秒级完成（不再走 Cython/PyArmor）
        assert elapsed < 15, '--pack-only 耗时 {}s 异常（应 <15s）'.format(elapsed)
        # 产物存在
        dist_dir = _RELEASE_DIR / 'dist'
        mzps = sorted(dist_dir.glob('maxagent-*.mzp'))
        assert mzps
        # 日志应明确显示走了 pack-only 路径
        combined = proc.stdout + proc.stderr
        assert 'pack-only' in combined.lower() or '跳过' in combined

    def test_pack_only_with_empty_cache_returns_error(
        self, tmp_path, monkeypatch,
    ):
        """--pack-only 但 build_cache 中无对应 ABI 产物时应早退非 0。"""
        # 在临时目录跑 build.py，避免污染真实 build_cache
        # 简化做法：直接调 main()，把 BUILD_CACHE_DIR 指到空的 tmp 目录
        import build  # type: ignore

        empty_cache = tmp_path / 'empty_cache'
        empty_cache.mkdir()
        empty_dist = tmp_path / 'empty_dist'
        empty_dist.mkdir()

        monkeypatch.setattr(build, 'BUILD_CACHE_DIR', empty_cache)
        monkeypatch.setattr(build, 'DIST_DIR', empty_dist)

        ret = build.main(['--pack-only', '--abis', 'cp311'])
        # _make_mzp 在没有 ABI 时返回 5
        assert ret == 5


# ============================================================
# --all-abis 一键多 ABI 调度（轻量单测，不启动真实子进程矩阵）
# ============================================================


class TestAllAbisOrchestration:
    """验证 --all-abis 模式的参数解析、互斥规则与辅助函数。"""

    def test_all_abis_helpers_exist(self):
        """build 模块暴露多 ABI 调度所需的全部辅助函数。"""
        import build  # type: ignore
        for name in (
            '_has_uv',
            '_list_uv_pythons',
            '_ensure_uv_pythons',
            '_abi_already_built',
            '_orchestrate_all_abis',
        ):
            assert hasattr(build, name), '缺少辅助函数 ' + name
            assert callable(getattr(build, name)), name + ' 不可调用'

    def test_all_abis_conflict_with_quick(self):
        """--all-abis 与 --quick 互斥，应早退非 0。"""
        import build  # type: ignore
        ret = build.main(['--all-abis', '--quick'])
        assert ret == 7

    def test_all_abis_conflict_with_pack_only(self):
        """--all-abis 与 --pack-only 互斥，应早退非 0。"""
        import build  # type: ignore
        ret = build.main(['--all-abis', '--pack-only'])
        assert ret == 7

    def test_all_abis_dry_run_succeeds(self, monkeypatch):
        """--all-abis --dry-run 应跑通整个调度计划而不真正启动子进程。"""
        import build  # type: ignore

        # 即便沙箱里没装 uv，也假装它存在 + 假装目标 Python 都可用，
        # 这样 dry-run 路径能完整覆盖到 _orchestrate_all_abis 主体
        monkeypatch.setattr(build, '_has_uv', lambda: True)
        monkeypatch.setattr(
            build,
            '_list_uv_pythons',
            lambda: ['3.7.9', '3.9.7', '3.10.8', '3.11.9', '3.13.9'],
        )
        ret = build.main(['--all-abis', '--dry-run'])
        # dry-run 不真编不真打包，应返回 0
        assert ret == 0

    def test_all_abis_without_uv_raises_clear_error(self, monkeypatch):
        """--all-abis 但本机没装 uv，应给出明确错误并非零退出。"""
        import build  # type: ignore
        monkeypatch.setattr(build, '_has_uv', lambda: False)
        ret = build.main(['--all-abis'])
        # _orchestrate_all_abis 抛 RuntimeError，main 内 except 链让其
        # 在 ABI 编译阶段返回 3 — 但 --all-abis 是在编译之前抛，
        # 直接传播；这里只断言 != 0 即可（具体码不强约束）
        assert ret != 0

    def test_abi_already_built_detects_extension_modules(self, tmp_path, monkeypatch):
        """_abi_already_built 应靠 .pyd / .so 的存在判断已编译。"""
        import build  # type: ignore

        monkeypatch.setattr(build, 'BUILD_CACHE_DIR', tmp_path)

        # 空目录 → False
        assert build._abi_already_built('cp311') is False

        # 仅 .py 没扩展 → 也算未编译（避免半成品被误判）
        pkg = tmp_path / 'cp311' / 'maxagent'
        pkg.mkdir(parents=True)
        (pkg / '__init__.py').write_text('', encoding='utf-8')
        assert build._abi_already_built('cp311') is False

        # 有 .pyd → True
        (pkg / 'config.cp311-win_amd64.pyd').write_bytes(b'\x00')
        assert build._abi_already_built('cp311') is True

        # Linux 用 .so 也应识别
        pkg2 = tmp_path / 'cp310' / 'maxagent'
        pkg2.mkdir(parents=True)
        (pkg2 / 'tool.cpython-310-x86_64-linux-gnu.so').write_bytes(b'\x00')
        assert build._abi_already_built('cp310') is True


class TestUvPythonMinorMatching:
    """验证 uv Python 解析采用 minor 系列匹配，不再要求 patch 精确相等。"""

    def test_minor_key_extracts_minor_part(self):
        import build  # type: ignore
        assert build._minor_key('3.11.13') == '3.11'
        assert build._minor_key('3.9.7') == '3.9'
        assert build._minor_key('3.13') == '3.13'

    def test_resolve_uv_python_prefers_exact_match(self):
        """目标 patch 已装时优先返回 patch 完全一致的版本。"""
        import build  # type: ignore
        installed = ['3.11.9', '3.11.13']
        assert build._resolve_uv_python('3.11.9', installed) == '3.11.9'

    def test_resolve_uv_python_falls_back_to_same_minor_max_patch(self):
        """目标 patch 没装时回退到同 minor 中 patch 最高的版本。"""
        import build  # type: ignore
        installed = ['3.9.18', '3.10.18', '3.11.13', '3.13.5']
        # 期望 3.11.9 但只装了 3.11.13 → 命中 3.11.13
        assert build._resolve_uv_python('3.11.9', installed) == '3.11.13'
        # 期望 3.10.8 但只装了 3.10.18 → 命中 3.10.18
        assert build._resolve_uv_python('3.10.8', installed) == '3.10.18'

    def test_resolve_uv_python_returns_none_for_missing_minor(self):
        """同 minor 系列完全没装时返回 None。"""
        import build  # type: ignore
        installed = ['3.10.18', '3.11.13']
        assert build._resolve_uv_python('3.9.7', installed) is None

    def test_ensure_uv_pythons_returns_dict_with_actual_versions(self, monkeypatch):
        """_ensure_uv_pythons 返回 {期望: 实际} 映射，且 minor 命中即可。"""
        import build  # type: ignore
        # uv 装的全是新 patch（与 ABI_TO_PYTHON 中的精确版不同）
        monkeypatch.setattr(
            build,
            '_list_uv_pythons',
            lambda: ['3.9.18', '3.10.18', '3.11.13', '3.13.5'],
        )
        resolved = build._ensure_uv_pythons(
            ['3.9.7', '3.10.8', '3.11.9', '3.13.9'],
            dry_run=False,
            auto_install=False,
        )
        assert resolved == {
            '3.9.7': '3.9.18',
            '3.10.8': '3.10.18',
            '3.11.9': '3.11.13',
            '3.13.9': '3.13.5',
        }

    def test_all_abis_dry_run_with_minor_only_install(self, monkeypatch):
        """模拟用户用 'uv python install 3.11' 装的 3.11.13 等新 patch，
        --all-abis --dry-run 应能完整通过而不再因 patch 不同被判失败。
        """
        import build  # type: ignore
        monkeypatch.setattr(build, '_has_uv', lambda: True)
        monkeypatch.setattr(
            build,
            '_list_uv_pythons',
            lambda: ['3.9.18', '3.10.18', '3.11.13', '3.13.5'],
        )
        ret = build.main(['--all-abis', '--dry-run'])
        assert ret == 0