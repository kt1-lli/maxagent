#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用户显式意图 → INSTRUCTIONS.md 自动写入。

在 ``send_message`` / ``AgentWorker._current_user_input`` 拿到用户输入后，
先经此模块检查是否包含"记住/以后/默认/总是/必须/不要/以后都..."等触发词。
命中则把当前用户消息（或抽取出的具体规则）追加到 INSTRUCTIONS.md，并把
这个动作作为一个 ``memory_write`` 事件写入事件日志。

同时把相对时间（今天/明天/本周六/下周...）在写入前转换为绝对日期。
"""

from __future__ import absolute_import
from __future__ import print_function

import re
import time
from typing import Optional
from typing import Tuple

from ..logger import get_logger
from .events import get_event_logger
from .store import get_memory_store

logger = get_logger(__name__)

# 强意图触发词（命中即写入 INSTRUCTIONS.md）
_STRONG_TRIGGERS = (
    '记住', '记下', '请记住', '帮我记住',
    '以后都', '以后请', '以后你', '以后要',
    '默认', '总是', '始终',
    '不要再', '不要每次', '禁止', '严禁',
    '必须', '一律',
)

# 弱意图（结合上下文可能是长期指令，本模块保守不自动写，交给 LLM 通过工具写）
_WEAK_TRIGGERS = ('习惯', '喜欢', '偏好')


def detect_explicit_memory_intent(user_input):
    # type: (str) -> bool
    """判断用户消息是否包含"显式长期记忆"触发词。"""
    if not user_input:
        return False
    text = str(user_input).strip()
    if not text:
        return False
    for kw in _STRONG_TRIGGERS:
        if kw in text:
            return True
    return False


def _resolve_relative_dates(text):
    # type: (str) -> str
    """把常见相对时间转成 ``绝对日期(周X)`` 格式，保留原表达在括号内。

    覆盖：今天 / 明天 / 后天 / 本周X / 下周 / 下周X / 这个月 / N 天后
    未识别项原样返回。
    """
    if not text:
        return text
    now = time.localtime()
    today = time.mktime(time.struct_time((
        now.tm_year, now.tm_mon, now.tm_mday, 0, 0, 0, 0, 0, -1,
    )))

    def _fmt(ts):
        lt = time.localtime(ts)
        weekday = '一二三四五六日'[lt.tm_wday]
        return '{}(周{})'.format(time.strftime('%Y-%m-%d', lt), weekday)

    replacements = []  # type: list

    replacements.append(('今天', _fmt(today)))
    replacements.append(('明天', _fmt(today + 86400)))
    replacements.append(('后天', _fmt(today + 86400 * 2)))

    # N 天后
    m = re.search(r'(\d+)\s*天后', text)
    if m:
        try:
            n = int(m.group(1))
            replacements.append((m.group(0), _fmt(today + 86400 * n)))
        except ValueError:
            pass

    # 本周 X / 下周 X
    week_map = {'一': 0, '二': 1, '三': 2, '四': 3, '五': 4, '六': 5, '日': 6, '天': 6}
    for prefix_kw, base_offset in (('本周', 0), ('下周', 7)):
        m = re.search(prefix_kw + r'([一二三四五六日天])', text)
        if m:
            wday = week_map.get(m.group(1))
            if wday is not None:
                cur_wday = now.tm_wday
                delta = (wday - cur_wday) % 7 + base_offset
                if base_offset == 0 and delta == 0:
                    delta = 0  # 本周今天保持今天
                replacements.append((m.group(0), _fmt(today + 86400 * delta)))

    result = text
    for src, dst in replacements:
        if src in result and dst not in result:
            result = result.replace(src, '{}（{}）'.format(dst, src), 1)
    return result


def write_instruction_from_user_message(user_input, session_id=''):
    # type: (str, str) -> Tuple[bool, str]
    """检测触发词并写入 INSTRUCTIONS.md。

    :returns: ``(written, message)``
        - written=True 表示已追加；
        - message 是给上层的可选提示（比如 "已记入长期规则: ..."）
    """
    if not detect_explicit_memory_intent(user_input):
        return False, ''
    text = _resolve_relative_dates(str(user_input).strip())
    # 过长：保留前 300 字，避免大段对话被塞进 INSTRUCTIONS.md
    if len(text) > 300:
        text = text[:300] + '...'
    try:
        result = get_memory_store().append_instruction(text)
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning('写入 INSTRUCTIONS.md 失败: %s', exc)
        return False, ''
    if result == 'appended':
        try:
            get_event_logger().log(
                'memory_write',
                payload={'file': 'INSTRUCTIONS.md', 'rule': text},
                session_id=session_id or '',
            )
        except Exception:  # pylint: disable=broad-except
            pass
        logger.info('已把用户显式指令写入 INSTRUCTIONS.md')
        return True, '已记入长期规则'
    return False, ''


__all__ = [
    'detect_explicit_memory_intent',
    'write_instruction_from_user_message',
]
