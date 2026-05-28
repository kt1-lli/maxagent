#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM 反思（self-reflection）加载器。

设计参照 ``user_rules_loader.py``，但语义截然不同：

- 规则（rule）= "以后该怎么做"，必须经过用户审批弹窗，落盘后注入
  system prompt 强约束 LLM 行为；
- 反思（reflection）= "这次为什么没做好"，无需审批（LLM 自我登记），
  仅作为短期记忆按需注入 system prompt，过多时按时间衰减自动遗忘。

目录结构::

    {config_dir}/reflections/<reflection_id>.json

每条反思的 JSON 文件结构::

    {
      "id": "rfl_1700000000_abc123",
      "task_summary": "批量重命名场景中的 100 个对象",
      "what_went_well": "正则规则匹配准确...",
      "what_went_wrong": "未处理 isDeleted 节点导致 5 个对象漏改",
      "lessons": "下次 rename 前先 isValidNode 过滤",
      "tags": ["rename", "pymxs"],
      "created_at": 1700000000.0
    }

为什么不让反思像规则一样走审批弹窗？
- 反思频次比规则高 5~10 倍（每个失败任务都可能值得一记），强行弹窗
  会严重打扰用户；
- 反思的副作用面很小——只影响 LLM 自己后续推理，不会改任何文件、
  不会执行代码；
- 用户随时可以在「我的资源」面板里查看 / 删除（本期暂未做 UI，但
  ``delete_reflection`` 工具已暴露给用户和 LLM）。

注入策略：
- 默认仅注入"最近 N 条" (``MAX_INJECT_COUNT`` = 10)；
- 总字节硬上限 ``MAX_TOTAL_BYTES`` = 2KB（远小于 user_rules 的 4KB，
  毕竟反思是辅助信息）；
- 超过 ``MAX_AGE_SECONDS`` 的反思不再注入（仅保留磁盘文件）。
"""

from __future__ import absolute_import
from __future__ import print_function

import hashlib
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


REFLECTIONS_DIRNAME = 'reflections'

# 反思 ID 校验：rfl_ 开头 + 时间戳 + 短哈希，不接受 LLM 自定义 ID
# （和 rules 不同：rules ID 由 LLM 拟语义命名，便于人类识记；
#   反思更高频，自动生成 ID 更省心，避免 LLM 反复 list_reflections
#   去查重）
_ID_RE = re.compile(r'^rfl_[a-z0-9_]{1,60}$')

# 单条反思 JSON 最大字节数（防止 LLM 长篇大论）
MAX_REFLECTION_BYTES = 2 * 1024

# 全部已注入反思的硬上限。注意：本上限只针对"反思条目正文"的累加，
# 不包括开头的"主动反思指引"段（指引固定 ~800 字节，无论如何都注入）。
MAX_TOTAL_BYTES = 2 * 1024

# 注入数量上限（即使没超字节限也只取最新 N 条）
MAX_INJECT_COUNT = 10

# 老于该阈值的反思不再注入 prompt（仅保留磁盘记录）
# 默认 30 天——再老的经验大概率失效或已被新规则取代
MAX_AGE_SECONDS = 30 * 24 * 3600

# 测试期可以临时把 base 目录指过去
_OVERRIDE_BASE_DIR = None  # type: Optional[str]


def set_reflections_dir_override(path):
    # type: (Optional[str]) -> None
    """把反思目录临时切换到指定路径（仅用于单元测试）。"""
    global _OVERRIDE_BASE_DIR  # pylint: disable=global-statement
    _OVERRIDE_BASE_DIR = path


def get_reflections_dir():
    # type: () -> str
    """获取反思目录绝对路径，必要时创建。"""
    if _OVERRIDE_BASE_DIR:
        path = _OVERRIDE_BASE_DIR
    else:
        base = get_config_dir()
        path = os.path.join(base, REFLECTIONS_DIRNAME)
    if not os.path.isdir(path):
        os.makedirs(path)
    return path


def _reflection_path(reflection_id, base_dir=None):
    # type: (str, Optional[str]) -> str
    base = base_dir or get_reflections_dir()
    return os.path.join(base, reflection_id + '.json')


def _generate_reflection_id(seed_text):
    # type: (str) -> str
    """根据 task_summary + 当前时间生成稳定的 ID。"""
    h = hashlib.md5(
        (seed_text + str(time.time())).encode('utf-8'),
    ).hexdigest()[:8]
    return 'rfl_{}_{}'.format(int(time.time()), h)


def validate_reflection_id(reflection_id):
    # type: (str) -> None
    """校验反思 ID。"""
    if not reflection_id or not _ID_RE.match(reflection_id):
        raise ValueError(
            '反思 ID 格式非法（应为 rfl_ 开头 + 字母数字下划线）: {!r}'.format(
                reflection_id,
            ),
        )


def validate_reflection_payload(data):
    # type: (Dict[str, Any]) -> None
    """对反思字段做基础校验。"""
    summary = (data.get('task_summary') or '').strip()
    lessons = (data.get('lessons') or '').strip()
    if not summary:
        raise ValueError('task_summary 不能为空')
    if not lessons:
        raise ValueError('lessons 不能为空（反思的核心是经验总结）')

    # 总字节硬上限——避免单条反思塞满 prompt 预算
    full_bytes = len(
        json.dumps(data, ensure_ascii=False).encode('utf-8'),
    )
    if full_bytes > MAX_REFLECTION_BYTES:
        raise ValueError(
            '反思内容超出最大长度 {} 字节，请精简'.format(MAX_REFLECTION_BYTES),
        )


def write_reflection(data):
    # type: (Dict[str, Any]) -> str
    """写入一条反思，返回反思 ID。

    :param data: 至少包含 ``task_summary`` 和 ``lessons``；
        其他可选字段：``what_went_well`` / ``what_went_wrong`` /
        ``tags`` / ``source_session_sid``
    """
    validate_reflection_payload(data)
    reflection_id = data.get('id') or _generate_reflection_id(
        data.get('task_summary') or '',
    )
    validate_reflection_id(reflection_id)

    full = {
        'id': reflection_id,
        'task_summary': (data.get('task_summary') or '').strip(),
        'what_went_well': (data.get('what_went_well') or '').strip(),
        'what_went_wrong': (data.get('what_went_wrong') or '').strip(),
        'lessons': (data.get('lessons') or '').strip(),
        'tags': list(data.get('tags') or []),
        'source_session_sid': data.get('source_session_sid') or '',
        'created_at': data.get('created_at') or time.time(),
    }

    path = _reflection_path(reflection_id)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(full, fh, ensure_ascii=False, indent=2)
    if os.path.exists(path):
        os.replace(tmp, path)
    else:
        os.rename(tmp, path)
    return reflection_id


def list_reflections(only_recent=False):
    # type: (bool) -> List[Dict[str, Any]]
    """列出所有反思（按 created_at 降序，新的在前）。

    :param only_recent: True 时仅返回 ``MAX_AGE_SECONDS`` 内的反思
    """
    base = get_reflections_dir()
    out = []
    cutoff = time.time() - MAX_AGE_SECONDS if only_recent else 0
    for fname in sorted(os.listdir(base)):
        if not fname.endswith('.json'):
            continue
        path = os.path.join(base, fname)
        try:
            with open(path, 'r', encoding='utf-8') as fh:
                rfl = json.load(fh)
        except (OSError, ValueError) as exc:
            logger.warning('加载反思 %s 失败: %s', fname, exc)
            continue
        if only_recent and (rfl.get('created_at') or 0) < cutoff:
            continue
        out.append(rfl)
    out.sort(key=lambda r: r.get('created_at') or 0, reverse=True)
    return out


def get_reflection(reflection_id):
    # type: (str) -> Optional[Dict[str, Any]]
    """读取单条反思，不存在返回 None。"""
    path = _reflection_path(reflection_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        logger.warning('读取反思 %s 失败: %s', reflection_id, exc)
        return None


def delete_reflection(reflection_id):
    # type: (str) -> bool
    """删除单条反思。"""
    path = _reflection_path(reflection_id)
    if not os.path.exists(path):
        return False
    try:
        os.remove(path)
        return True
    except OSError as exc:
        logger.warning('删除反思 %s 失败: %s', reflection_id, exc)
        return False


# ---------------------------------------------------------------------- #
# system prompt 注入
# ---------------------------------------------------------------------- #

def _format_reflection_for_prompt(rfl):
    # type: (Dict[str, Any]) -> str
    """把单条反思格式化成简短 prompt 段。"""
    summary = (rfl.get('task_summary') or '').strip()
    lessons = (rfl.get('lessons') or '').strip()
    wrong = (rfl.get('what_went_wrong') or '').strip()
    parts = ['- 任务：{}'.format(summary)]
    if wrong:
        parts.append('  失误：{}'.format(wrong))
    parts.append('  经验：{}'.format(lessons))
    return '\n'.join(parts)


def build_system_prompt_addon(max_total_bytes=None, max_count=None):
    # type: (Optional[int], Optional[int]) -> str
    """生成可拼接到 system prompt 的反思段。

    无论是否已有历史反思，都至少返回一段"何时反思"的主动触发指引，
    避免 LLM 看不到 ``reflect_on_outcome`` 工具的存在感（仅靠工具
    description 描述触发率不够）。

    :param max_total_bytes: 总字节上限，None 表示用 ``MAX_TOTAL_BYTES``
    :param max_count: 注入条数上限，None 表示用 ``MAX_INJECT_COUNT``
    :returns: 多行文本（即使无反思也至少返回触发指引）
    """
    limit_bytes = MAX_TOTAL_BYTES if max_total_bytes is None else max_total_bytes
    limit_count = MAX_INJECT_COUNT if max_count is None else max_count

    # === 主动反思指引（始终注入，无视有没有历史反思） ===
    # 这一段是关键：靠工具 description 触发率太低，必须在 system prompt
    # 里告诉 LLM "什么时候必须主动调 reflect_on_outcome"。
    guidance = (
        '## 主动反思机制\n'
        '你有一个 `reflect_on_outcome` 工具，用于把"任务过程中获得的'
        '可复用经验"沉淀为短期记忆，下次遇到类似任务时会自动注入到'
        'system prompt 帮你避免重蹈覆辙。\n'
        '\n'
        '### 必须主动调用 reflect_on_outcome 的场景\n'
        ' 1. **任务被用户连续纠正 ≥2 次**（说明你第一反应有偏差，'
        '需要记下"下次正确路径"）\n'
        ' 2. **某个工具调用失败后你换了路径才成功**（记录"为什么 A '
        '不行 + B 才行"，避免下次又先走 A）\n'
        ' 3. **用户说"差不多了"/"将就吧"/"还有些问题"** 等不完全'
        '满意的反馈（哪怕任务收尾，也值得反思）\n'
        ' 4. **你发现自己之前的某个判断/做法是错的**（即使用户没明说）\n'
        ' 5. **用户明确说"记下教训"/"以后注意"等**（这是显式信号）\n'
        '\n'
        '### 不要反思的场景\n'
        ' - 一次性顺利完成的小任务（无反思价值）\n'
        ' - 用户明确说"这是规则"→ 应该用 `suggest_rule_addition`\n'
        ' - 仅仅是"任务做完了"→ 不是所有完成都需要反思\n'
        '\n'
        '### 调用时机\n'
        '在你给用户的最终回复之前（或之后立即），独立调用一次本工具。'
        'lessons 字段是关键——必须写"下次怎么改进"，不能只是描述发生了什么。\n'
        '调用前可先 `list_reflections` 看是否已有同类反思，避免重复登记。'
    )

    if limit_count <= 0 or limit_bytes <= 0:
        return guidance

    rfls = list_reflections(only_recent=True)
    rfls = rfls[:limit_count] if rfls else []

    if not rfls:
        return guidance

    header = (
        '## 你最近的反思（短期记忆）\n'
        '（这些是你在以往任务中的自我复盘，用于规避同类失误。'
        '与官方规则冲突时以官方规则为准；老于 30 天的反思已自动淡出。）'
    )
    parts = [header]
    used = len(header.encode('utf-8'))

    for rfl in rfls:
        chunk = '\n\n' + _format_reflection_for_prompt(rfl)
        chunk_bytes = len(chunk.encode('utf-8'))
        if used + chunk_bytes > limit_bytes:
            break
        parts.append(chunk)
        used += chunk_bytes

    history_part = '\n'.join([p.lstrip('\n') for p in parts]).rstrip()
    return guidance + '\n\n' + history_part


__all__ = [
    'get_reflections_dir',
    'set_reflections_dir_override',
    'validate_reflection_id',
    'validate_reflection_payload',
    'write_reflection',
    'list_reflections',
    'get_reflection',
    'delete_reflection',
    'build_system_prompt_addon',
    'MAX_REFLECTION_BYTES',
    'MAX_TOTAL_BYTES',
    'MAX_INJECT_COUNT',
    'MAX_AGE_SECONDS',
]
