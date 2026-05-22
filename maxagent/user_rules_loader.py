#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用户学习规则加载器（C1 半自动 / 文档自进化）。

设计参照 ``user_tools_loader.py``，但这里管理的是"规则文本片段"而非
可执行 Python 代码，因此存储格式更简单：

目录结构::

    {config_dir}/user_rules/<rule_id>.json    # 单条规则的完整数据

每条规则的 JSON 文件结构::

    {
      "id": "R001_color_uppercase",
      "title": "rt.Color 必须大写",
      "content": "pymxs 颜色构造器必须大写 rt.Color()...",
      "good_example": "mat.diffuse = rt.Color(255, 0, 0)",
      "bad_example": "mat.diffuse = rt.color(255, 0, 0)",
      "tags": ["material", "pymxs", "color"],
      "source_session_sid": "abc12345",
      "created_at": 1700000000.0,
      "approved_by_user": true,
      "enabled": true
    }

为什么不直接用一个大 ``user_rules.md``？
- 单条文件便于增删、导出、社区交换；
- 不易因合并冲突损坏全部规则；
- 元数据天然持久化。

设计要点：
- ``MAX_TOTAL_BYTES``: 全部已启用规则注入 system prompt 的硬上限，
  默认 4KB，超过时 ``build_system_prompt_addon`` 会按"最近创建优先"
  截断并附加省略提示。
- ``enabled=False`` 的规则保留磁盘但不注入 prompt，便于用户暂时禁用
  而不删除。
- 规则 ID 必须满足 ``[a-z0-9_]{1,40}``，由调用方提供（通常 LLM
  生成），便于人类阅读。
"""

from __future__ import absolute_import
from __future__ import print_function

import json
import os
import re
import time
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from .config import get_config_dir
from .logger import get_logger


logger = get_logger(__name__)


USER_RULES_DIRNAME = 'user_rules'

# 规则 ID 校验：小写字母+数字+下划线，2-41 字符
_ID_RE = re.compile(r'^[a-z][a-z0-9_]{1,40}$')

# 单条规则 JSON 最大字节数（防止 LLM 写出超长内容）
MAX_RULE_BYTES = 4 * 1024

# 全部启用规则注入 system prompt 时的硬上限
# 与 coding_rules.CODING_RULES (~1.9KB) 错开，避免双 addon 加起来撑爆 prompt
MAX_TOTAL_BYTES = 4 * 1024

# 测试期可以临时把 base 目录指过去
_OVERRIDE_BASE_DIR = None  # type: Optional[str]


def set_user_rules_dir_override(path):
    # type: (Optional[str]) -> None
    """把用户规则目录临时切换到指定路径（仅用于单元测试）。"""
    global _OVERRIDE_BASE_DIR  # pylint: disable=global-statement
    _OVERRIDE_BASE_DIR = path


def get_user_rules_dir():
    # type: () -> str
    """获取规则目录绝对路径，必要时创建。"""
    if _OVERRIDE_BASE_DIR:
        path = _OVERRIDE_BASE_DIR
    else:
        base = get_config_dir()
        path = os.path.join(base, USER_RULES_DIRNAME)
    if not os.path.isdir(path):
        os.makedirs(path)
    return path


def _rule_path(rule_id, base_dir=None):
    # type: (str, Optional[str]) -> str
    base = base_dir or get_user_rules_dir()
    return os.path.join(base, rule_id + '.json')


def validate_rule_id(rule_id):
    # type: (str) -> None
    """校验规则 ID，非法时抛 ValueError。"""
    if not rule_id or not _ID_RE.match(rule_id):
        raise ValueError(
            '规则 ID 只能小写字母 + 数字 + 下划线，必须以字母开头，'
            '长度 2-41，收到: {!r}'.format(rule_id),
        )


def validate_rule_content(content):
    # type: (str) -> None
    """对规则正文做基础校验。"""
    if not content or not content.strip():
        raise ValueError('规则内容不能为空')
    if len(content.encode('utf-8')) > MAX_RULE_BYTES:
        raise ValueError(
            '规则内容超出最大长度 {} 字节'.format(MAX_RULE_BYTES),
        )


def write_rule(rule_id, data):
    # type: (str, Dict[str, Any]) -> str
    """落盘单条规则，返回 .json 路径。

    :param rule_id: 规则 ID
    :param data: 规则字段字典，至少包含 title 和 content
    """
    validate_rule_id(rule_id)
    title = (data.get('title') or '').strip()
    content = (data.get('content') or '').strip()
    if not title:
        raise ValueError('规则标题不能为空')
    validate_rule_content(content)

    full = {
        'id': rule_id,
        'title': title,
        'content': content,
        'good_example': (data.get('good_example') or '').strip(),
        'bad_example': (data.get('bad_example') or '').strip(),
        'tags': list(data.get('tags') or []),
        'source_session_sid': data.get('source_session_sid') or '',
        'rationale': (data.get('rationale') or '').strip(),
        'created_at': data.get('created_at') or time.time(),
        'approved_by_user': bool(data.get('approved_by_user', True)),
        'enabled': bool(data.get('enabled', True)),
    }

    path = _rule_path(rule_id)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(full, fh, ensure_ascii=False, indent=2)
    if os.path.exists(path):
        os.replace(tmp, path)
    else:
        os.rename(tmp, path)
    return path


def list_rules(only_enabled=False):
    # type: (bool) -> List[Dict[str, Any]]
    """列出所有用户规则（按 created_at 升序）。"""
    base = get_user_rules_dir()
    out = []
    for fname in sorted(os.listdir(base)):
        if not fname.endswith('.json'):
            continue
        path = os.path.join(base, fname)
        try:
            with open(path, 'r', encoding='utf-8') as fh:
                rule = json.load(fh)
        except (OSError, ValueError) as exc:
            logger.warning('加载规则 %s 失败: %s', fname, exc)
            continue
        if only_enabled and not rule.get('enabled', True):
            continue
        out.append(rule)
    out.sort(key=lambda r: r.get('created_at') or 0)
    return out


def get_rule(rule_id):
    # type: (str) -> Optional[Dict[str, Any]]
    """读取单条规则，不存在返回 None。"""
    path = _rule_path(rule_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        logger.warning('读取规则 %s 失败: %s', rule_id, exc)
        return None


def delete_rule(rule_id):
    # type: (str) -> bool
    """删除单条规则。"""
    path = _rule_path(rule_id)
    if not os.path.exists(path):
        return False
    try:
        os.remove(path)
        return True
    except OSError as exc:
        logger.warning('删除规则 %s 失败: %s', rule_id, exc)
        return False


def set_rule_enabled(rule_id, enabled):
    # type: (str, bool) -> bool
    """启用/禁用单条规则（不删除磁盘文件）。"""
    rule = get_rule(rule_id)
    if rule is None:
        return False
    rule['enabled'] = bool(enabled)
    write_rule(rule_id, rule)
    return True


# ---------------------------------------------------------------------- #
# system prompt 注入
# ---------------------------------------------------------------------- #

def _format_rule_for_prompt(rule):
    # type: (Dict[str, Any]) -> str
    """把单条规则格式化为 system prompt 中的一段文本。"""
    lines = ['### {}'.format(rule.get('title') or rule.get('id'))]
    content = (rule.get('content') or '').strip()
    if content:
        lines.append(content)
    bad = (rule.get('bad_example') or '').strip()
    good = (rule.get('good_example') or '').strip()
    if bad:
        lines.append('反例: {}'.format(bad))
    if good:
        lines.append('正例: {}'.format(good))
    return '\n'.join(lines)


def build_system_prompt_addon(user_input=None, max_total_bytes=None):
    # type: (Optional[str], Optional[int]) -> str
    """生成可拼接到 system prompt 的用户规则段。

    :param user_input: 当前用户消息（保留参数，便于未来按标签触发）
    :param max_total_bytes: 总字节上限，None 表示用 ``MAX_TOTAL_BYTES``
    :returns: 多行文本，无规则或全禁用时返回空字符串
    """
    del user_input  # 当前版本不做标签触发，全量注入
    limit = MAX_TOTAL_BYTES if max_total_bytes is None else max_total_bytes
    rules = list_rules(only_enabled=True)
    if not rules:
        return ''

    # 按"最近创建优先"排序，便于截断时优先保留新规则
    rules.sort(key=lambda r: r.get('created_at') or 0, reverse=True)

    header = (
        '\n'
        '## 你从与用户的协作中学到的规则\n'
        '（这些规则来自历次对话沉淀，已被用户批准。'
        '与官方规则冲突时以官方规则为准。）'
    )
    parts = [header]
    used = len(header.encode('utf-8'))
    truncated_count = 0

    for rule in rules:
        chunk = '\n\n' + _format_rule_for_prompt(rule)
        chunk_bytes = len(chunk.encode('utf-8'))
        if used + chunk_bytes > limit:
            truncated_count += 1
            continue
        parts.append(chunk)
        used += chunk_bytes

    if truncated_count:
        note = (
            '\n\n（还有 {} 条规则因总长度超限未注入，'
            '可在设置面板 → 我的规则中删除部分旧规则后释放空间。）'
        ).format(truncated_count)
        # 即使 note 也超限，也优先保留 note（截断恒比静默丢失安全）
        parts.append(note)

    return '\n'.join([p.lstrip('\n') for p in parts]).rstrip()


def total_enabled_bytes():
    # type: () -> int
    """统计当前已启用规则注入 prompt 后占用的字节数（含 header）。"""
    addon = build_system_prompt_addon(max_total_bytes=10 * 1024 * 1024)
    return len(addon.encode('utf-8'))


__all__ = [
    'get_user_rules_dir',
    'set_user_rules_dir_override',
    'validate_rule_id',
    'validate_rule_content',
    'write_rule',
    'list_rules',
    'get_rule',
    'delete_rule',
    'set_rule_enabled',
    'build_system_prompt_addon',
    'total_enabled_bytes',
    'MAX_RULE_BYTES',
    'MAX_TOTAL_BYTES',
]
