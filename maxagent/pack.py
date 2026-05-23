#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工具 / 技能 / 规则的导入导出（.maxagent-pack 包）。

为什么单独抽一个 ``pack`` 模块？
- 工具 (``user_tools/``)、技能 (``skills/``)、规则 (``user_rules/``)
  分别由 ``user_tools_loader`` / ``skills`` / ``user_rules_loader``
  管理，各自有完整的 CRUD 与磁盘格式；UI 层不应该重复实现"打包/解包
  这三类资源"的共用逻辑。
- 全部走 zip + manifest.json 的零依赖方案，与 maxagent 其它模块的
  "不引入第三方包"硬约束一致。

包文件格式 (``*.maxagent-pack`` 实质是一个 zip)::

    manifest.json              # 元数据：作者 / 版本 / 包含资源清单
    user_tools/<name>.py       # 自定义工具源码
    user_tools/<name>.meta.json
    skills/<filename>.json     # 技能 JSON
    user_rules/<id>.json       # 规则 JSON

manifest.json 字段::

    {
        "schema_version": 1,
        "name": "包名",
        "description": "可选说明",
        "author": "可选作者署名",
        "exported_at": "2026-05-23T14:18:18+08:00",
        "exported_by": "MaxAgent x.y.z",
        "tools": ["tool_a", "tool_b"],
        "skills": ["技能名 1", ...],
        "rules":  ["rule_id_a", ...]
    }

设计要点：
- 不打包 Profile / API Key / 会话历史（避免敏感信息泄露）。
- 同名冲突由 UI 层与用户交互决定（覆盖 / 跳过 / 全部覆盖）。
- 工具是可执行 ``.py`` 代码——导入路径需要在 UI 显式提示风险并由用户
  二次确认；本模块只负责文件 I/O，不做风险拦截。
- 任何 zip 异常 / JSON 异常 / 命名冲突 都通过 ``PackError`` 反馈给上层。
"""

from __future__ import absolute_import
from __future__ import print_function

import datetime
import io
import json
import os
import time
import zipfile
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from .logger import get_logger


logger = get_logger(__name__)


SCHEMA_VERSION = 1
PACK_SUFFIX = '.maxagent-pack'

# manifest 与各资源在 zip 内的存储前缀
_MANIFEST_NAME = 'manifest.json'
_TOOLS_PREFIX = 'user_tools/'
_SKILLS_PREFIX = 'skills/'
_RULES_PREFIX = 'user_rules/'

# 单个 zip 入口最大字节数（防御性，避免恶意大文件爆内存）
_MAX_ENTRY_BYTES = 2 * 1024 * 1024  # 2 MB
# 整个 pack 解压后最大字节数
_MAX_TOTAL_BYTES = 32 * 1024 * 1024  # 32 MB


class PackError(Exception):
    """导入 / 导出过程中的错误。"""


# ---------------------------------------------------------------------- #
# 公共工具
# ---------------------------------------------------------------------- #
def _now_iso():
    # type: () -> str
    """ISO 8601（含本地时区偏移）的当前时间。"""
    try:
        # Python 3.6+
        ts = time.localtime()
        offset_sec = -time.timezone if (ts.tm_isdst <= 0) else -time.altzone
        sign = '+' if offset_sec >= 0 else '-'
        hh = abs(offset_sec) // 3600
        mm = (abs(offset_sec) % 3600) // 60
        local = datetime.datetime.now()
        return '{}{}{:02d}:{:02d}'.format(
            local.strftime('%Y-%m-%dT%H:%M:%S'),
            sign, hh, mm,
        )
    except Exception:  # pylint: disable=broad-except
        return datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S')


def _maxagent_version():
    # type: () -> str
    try:
        from . import __version__ as ver
        return str(ver)
    except Exception:  # pylint: disable=broad-except
        return 'unknown'


# ---------------------------------------------------------------------- #
# 导出
# ---------------------------------------------------------------------- #
def export_pack(
    output_path,
    tool_names=None,
    skill_names=None,
    rule_ids=None,
    pack_name='',
    description='',
    author='',
):
    # type: (str, Optional[List[str]], Optional[List[str]], Optional[List[str]], str, str, str) -> Dict[str, Any]
    """把指定的工具/技能/规则打包到 ``output_path``（zip 格式）。

    :param output_path: 目标 .maxagent-pack 文件路径
    :param tool_names: 要导出的工具名列表；None 表示空集合（不导出工具）
    :param skill_names: 要导出的技能名列表
    :param rule_ids: 要导出的规则 ID 列表
    :param pack_name: 包显示名（写入 manifest）
    :param description: 包描述
    :param author: 作者
    :returns: ``{'path': ..., 'tools': [...], 'skills': [...], 'rules': [...],
               'size': bytes}``
    """
    tool_names = list(tool_names or [])
    skill_names = list(skill_names or [])
    rule_ids = list(rule_ids or [])

    if not (tool_names or skill_names or rule_ids):
        raise PackError('导出内容为空：未选择任何工具/技能/规则')

    # 准备 manifest
    manifest = {
        'schema_version': SCHEMA_VERSION,
        'name': pack_name or os.path.basename(output_path).rsplit('.', 1)[0],
        'description': description or '',
        'author': author or '',
        'exported_at': _now_iso(),
        'exported_by': 'MaxAgent ' + _maxagent_version(),
        'tools': [],
        'skills': [],
        'rules': [],
    }

    # 用 BytesIO 先写到内存，全部成功再落盘——避免半个 zip 留在磁盘上
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        # ---- 工具 ---- #
        if tool_names:
            from . import user_tools_loader as utl
            base = utl.get_user_tools_dir()
            for name in tool_names:
                py_path = os.path.join(base, name + '.py')
                meta_path = os.path.join(base, name + utl.META_SUFFIX)
                if not os.path.exists(py_path):
                    logger.warning('导出跳过：工具 %s 源码不存在 (%s)',
                                   name, py_path)
                    continue
                with open(py_path, 'rb') as fh:
                    zf.writestr(_TOOLS_PREFIX + name + '.py', fh.read())
                if os.path.exists(meta_path):
                    with open(meta_path, 'rb') as fh:
                        zf.writestr(
                            _TOOLS_PREFIX + name + utl.META_SUFFIX,
                            fh.read(),
                        )
                manifest['tools'].append(name)

        # ---- 技能 ---- #
        if skill_names:
            from . import skills as skills_mod
            mgr = skills_mod.SkillManager()
            for name in skill_names:
                sk = mgr.get(name)
                if sk is None:
                    logger.warning('导出跳过：技能 %s 不存在', name)
                    continue
                # 直接序列化为 JSON——避免依赖磁盘文件名（不同操作系统的
                # 安全字符不同）。导入端会用同样的 SkillManager.save() 重建。
                payload = sk.to_dict()
                # 不带 file_path：这是本机绝对路径，对方机器无意义
                payload.pop('file_path', None)
                fname = _safe_filename(name) + '.json'
                zf.writestr(
                    _SKILLS_PREFIX + fname,
                    json.dumps(payload, ensure_ascii=False, indent=2),
                )
                manifest['skills'].append(name)

        # ---- 规则 ---- #
        if rule_ids:
            from . import user_rules_loader as url_mod
            for rid in rule_ids:
                rule = url_mod.get_rule(rid)
                if rule is None:
                    logger.warning('导出跳过：规则 %s 不存在', rid)
                    continue
                zf.writestr(
                    _RULES_PREFIX + rid + '.json',
                    json.dumps(rule, ensure_ascii=False, indent=2),
                )
                manifest['rules'].append(rid)

        zf.writestr(
            _MANIFEST_NAME,
            json.dumps(manifest, ensure_ascii=False, indent=2),
        )

    data = buf.getvalue()
    # 确保父目录存在
    parent = os.path.dirname(os.path.abspath(output_path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    tmp = output_path + '.tmp'
    with open(tmp, 'wb') as fh:
        fh.write(data)
    if os.path.exists(output_path):
        os.replace(tmp, output_path)
    else:
        os.rename(tmp, output_path)

    logger.info(
        '导出包成功: path=%s tools=%d skills=%d rules=%d size=%dB',
        output_path,
        len(manifest['tools']),
        len(manifest['skills']),
        len(manifest['rules']),
        len(data),
    )
    return {
        'path': output_path,
        'tools': list(manifest['tools']),
        'skills': list(manifest['skills']),
        'rules': list(manifest['rules']),
        'size': len(data),
    }


def _safe_filename(name):
    # type: (str) -> str
    """技能文件名清洗——跨平台安全字符。"""
    import re
    cleaned = re.sub(r'[^\w\u4e00-\u9fa5\-]+', '_', name).strip('_')
    return cleaned[:48] or 'skill'


# ---------------------------------------------------------------------- #
# 导入：解析 + 预览 + 实际导入
# ---------------------------------------------------------------------- #
def parse_pack(pack_path):
    # type: (str) -> Dict[str, Any]
    """解析一个 .maxagent-pack 文件，返回 manifest + 各资源条目。

    不做实际导入，仅用于 UI 显示"将要导入什么"+"哪些已存在"。

    :returns: ``{
        'manifest': {...},
        'tools':  [{'name': str, 'code': str, 'meta': dict, 'status': 'new'|'existing'}],
        'skills': [{'name': str, 'data': dict, 'status': ...}],
        'rules':  [{'rule_id': str, 'data': dict, 'status': ...}],
    }``
    """
    if not os.path.isfile(pack_path):
        raise PackError('文件不存在: ' + pack_path)
    if os.path.getsize(pack_path) > _MAX_TOTAL_BYTES:
        raise PackError('包过大（>32MB），拒绝加载')

    try:
        zf = zipfile.ZipFile(pack_path, 'r')
    except zipfile.BadZipFile as exc:
        raise PackError('不是合法的 zip 文件: {}'.format(exc))

    try:
        names = zf.namelist()
        # 防 zip slip：拒绝绝对路径 / 父目录穿越
        for nm in names:
            if nm.startswith('/') or '..' in nm.split('/'):
                raise PackError('包内含非法路径: {}'.format(nm))

        # manifest 必需
        if _MANIFEST_NAME not in names:
            raise PackError('缺少 manifest.json，不是合法的 maxagent-pack')

        # 读 manifest
        try:
            manifest_raw = zf.read(_MANIFEST_NAME)
        except Exception as exc:  # pylint: disable=broad-except
            raise PackError('manifest.json 读取失败: {}'.format(exc))
        try:
            manifest = json.loads(manifest_raw.decode('utf-8'))
        except (ValueError, UnicodeDecodeError) as exc:
            raise PackError('manifest.json 解析失败: {}'.format(exc))
        if not isinstance(manifest, dict):
            raise PackError('manifest 不是 JSON 对象')
        sv = manifest.get('schema_version')
        if isinstance(sv, int) and sv > SCHEMA_VERSION:
            logger.warning(
                '包 schema_version=%s 比当前版本 %s 新，尝试兼容读取',
                sv, SCHEMA_VERSION,
            )

        result = {
            'manifest': manifest,
            'tools': _parse_tools(zf, names),
            'skills': _parse_skills(zf, names),
            'rules': _parse_rules(zf, names),
        }
        return result
    finally:
        zf.close()


def _read_zip_text(zf, name):
    # type: (zipfile.ZipFile, str) -> str
    info = zf.getinfo(name)
    if info.file_size > _MAX_ENTRY_BYTES:
        raise PackError('包内文件 {} 过大 ({} bytes)'.format(name, info.file_size))
    raw = zf.read(name)
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise PackError('文件 {} 不是 UTF-8: {}'.format(name, exc))


def _parse_tools(zf, names):
    # type: (zipfile.ZipFile, List[str]) -> List[Dict[str, Any]]
    from . import user_tools_loader as utl
    out = []  # type: List[Dict[str, Any]]
    # 找出所有 .py
    py_files = [
        n for n in names
        if n.startswith(_TOOLS_PREFIX) and n.endswith('.py')
    ]
    existing_names = {
        item['name'] for item in utl.list_user_tools(include_meta=False)
    }
    for py_name in sorted(py_files):
        tool_name = os.path.basename(py_name)[:-3]
        # 校验工具名（不合法直接标错）
        try:
            utl.validate_name(tool_name)
            valid_name = True
            invalid_reason = ''
        except ValueError as exc:
            valid_name = False
            invalid_reason = str(exc)
        try:
            code = _read_zip_text(zf, py_name)
        except PackError as exc:
            out.append({
                'name': tool_name, 'code': '', 'meta': {},
                'status': 'invalid', 'reason': str(exc),
            })
            continue
        meta_name = _TOOLS_PREFIX + tool_name + utl.META_SUFFIX
        meta = {}
        if meta_name in names:
            try:
                meta = json.loads(_read_zip_text(zf, meta_name))
            except (ValueError, PackError) as exc:
                logger.warning('元数据 %s 解析失败: %s', meta_name, exc)
                meta = {}
        if not valid_name:
            status = 'invalid'
            reason = invalid_reason
        elif tool_name in existing_names:
            status = 'existing'
            reason = ''
        else:
            status = 'new'
            reason = ''
        out.append({
            'name': tool_name,
            'code': code,
            'meta': meta if isinstance(meta, dict) else {},
            'status': status,
            'reason': reason,
        })
    return out


def _parse_skills(zf, names):
    # type: (zipfile.ZipFile, List[str]) -> List[Dict[str, Any]]
    from . import skills as skills_mod
    mgr = skills_mod.SkillManager()
    existing = {sk.name for sk in mgr.list_skills()}
    out = []  # type: List[Dict[str, Any]]
    json_files = [
        n for n in names
        if n.startswith(_SKILLS_PREFIX) and n.endswith('.json')
    ]
    for jname in sorted(json_files):
        try:
            data = json.loads(_read_zip_text(zf, jname))
        except (ValueError, PackError) as exc:
            out.append({
                'name': os.path.basename(jname),
                'data': {}, 'status': 'invalid', 'reason': str(exc),
            })
            continue
        skill_name = (data or {}).get('name') or ''
        if not skill_name:
            out.append({
                'name': os.path.basename(jname),
                'data': data, 'status': 'invalid',
                'reason': '缺少 name 字段',
            })
            continue
        status = 'existing' if skill_name in existing else 'new'
        out.append({
            'name': skill_name,
            'data': data,
            'status': status,
            'reason': '',
        })
    return out


def _parse_rules(zf, names):
    # type: (zipfile.ZipFile, List[str]) -> List[Dict[str, Any]]
    from . import user_rules_loader as url_mod
    out = []  # type: List[Dict[str, Any]]
    rule_files = [
        n for n in names
        if n.startswith(_RULES_PREFIX) and n.endswith('.json')
    ]
    for rname in sorted(rule_files):
        try:
            data = json.loads(_read_zip_text(zf, rname))
        except (ValueError, PackError) as exc:
            out.append({
                'rule_id': os.path.basename(rname),
                'data': {}, 'status': 'invalid', 'reason': str(exc),
            })
            continue
        rid = (data or {}).get('id') or os.path.basename(rname)[:-5]
        try:
            url_mod.validate_rule_id(rid)
            url_mod.validate_rule_content(data.get('content') or '')
            valid = True
            reason = ''
        except ValueError as exc:
            valid = False
            reason = str(exc)
        if not valid:
            out.append({
                'rule_id': rid, 'data': data,
                'status': 'invalid', 'reason': reason,
            })
            continue
        existing = url_mod.get_rule(rid) is not None
        out.append({
            'rule_id': rid,
            'data': data,
            'status': 'existing' if existing else 'new',
            'reason': '',
        })
    return out


def import_pack(
    pack_path,
    selected_tools=None,
    selected_skills=None,
    selected_rules=None,
    overwrite=False,
):
    # type: (str, Optional[List[str]], Optional[List[str]], Optional[List[str]], bool) -> Dict[str, Any]
    """把 ``pack_path`` 中用户选中的资源导入到本机。

    :param selected_tools: 要导入的工具名子集；None 表示包内全部
    :param selected_skills: 要导入的技能名子集
    :param selected_rules: 要导入的规则 ID 子集
    :param overwrite: 同名时是否覆盖；False 时跳过
    :returns: 详细的导入结果字典：
        ``{'tools': {imported, overwritten, skipped, errors},
           'skills': {...}, 'rules': {...}}``
    """
    parsed = parse_pack(pack_path)

    sel_tools = (
        None if selected_tools is None else set(selected_tools)
    )
    sel_skills = (
        None if selected_skills is None else set(selected_skills)
    )
    sel_rules = (
        None if selected_rules is None else set(selected_rules)
    )

    summary = {
        'tools': {
            'imported': [], 'overwritten': [],
            'skipped': [], 'errors': [],
        },
        'skills': {
            'imported': [], 'overwritten': [],
            'skipped': [], 'errors': [],
        },
        'rules': {
            'imported': [], 'overwritten': [],
            'skipped': [], 'errors': [],
        },
    }

    # ---- 工具 ---- #
    from . import user_tools_loader as utl
    for entry in parsed['tools']:
        name = entry['name']
        if sel_tools is not None and name not in sel_tools:
            continue
        if entry['status'] == 'invalid':
            summary['tools']['errors'].append(
                {'name': name, 'reason': entry.get('reason', '非法')},
            )
            continue
        if entry['status'] == 'existing' and not overwrite:
            summary['tools']['skipped'].append(name)
            continue
        try:
            utl.write_tool(name, entry['code'], entry.get('meta') or {})
            try:
                utl.reload_user_tool(name)
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning('reload_user_tool(%s) 失败: %s', name, exc)
            if entry['status'] == 'existing':
                summary['tools']['overwritten'].append(name)
            else:
                summary['tools']['imported'].append(name)
        except Exception as exc:  # pylint: disable=broad-except
            summary['tools']['errors'].append(
                {'name': name, 'reason': str(exc)},
            )

    # ---- 技能 ---- #
    from . import skills as skills_mod
    mgr = skills_mod.SkillManager()
    for entry in parsed['skills']:
        name = entry['name']
        if sel_skills is not None and name not in sel_skills:
            continue
        if entry['status'] == 'invalid':
            summary['skills']['errors'].append(
                {'name': name, 'reason': entry.get('reason', '非法')},
            )
            continue
        if entry['status'] == 'existing' and not overwrite:
            summary['skills']['skipped'].append(name)
            continue
        try:
            sk = skills_mod.Skill.from_dict(entry['data'])
            mgr.save(sk, overwrite=True)
            if entry['status'] == 'existing':
                summary['skills']['overwritten'].append(name)
            else:
                summary['skills']['imported'].append(name)
        except Exception as exc:  # pylint: disable=broad-except
            summary['skills']['errors'].append(
                {'name': name, 'reason': str(exc)},
            )

    # ---- 规则 ---- #
    from . import user_rules_loader as url_mod
    for entry in parsed['rules']:
        rid = entry['rule_id']
        if sel_rules is not None and rid not in sel_rules:
            continue
        if entry['status'] == 'invalid':
            summary['rules']['errors'].append(
                {'rule_id': rid, 'reason': entry.get('reason', '非法')},
            )
            continue
        try:
            res = url_mod.import_rule(entry['data'], overwrite=overwrite)
            if res['status'] == 'imported':
                summary['rules']['imported'].append(rid)
            elif res['status'] == 'overwritten':
                summary['rules']['overwritten'].append(rid)
            else:
                summary['rules']['skipped'].append(rid)
        except Exception as exc:  # pylint: disable=broad-except
            summary['rules']['errors'].append(
                {'rule_id': rid, 'reason': str(exc)},
            )

    logger.info(
        '导入包完成: path=%s tools=%s skills=%s rules=%s',
        pack_path,
        '+'.join('{}:{}'.format(k, len(v))
                 for k, v in summary['tools'].items()),
        '+'.join('{}:{}'.format(k, len(v))
                 for k, v in summary['skills'].items()),
        '+'.join('{}:{}'.format(k, len(v))
                 for k, v in summary['rules'].items()),
    )
    return summary


__all__ = [
    'PackError',
    'SCHEMA_VERSION',
    'PACK_SUFFIX',
    'export_pack',
    'parse_pack',
    'import_pack',
]
