#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""事件日志（Layer 1）：原始对话 / 工具调用按时间落盘。

设计原则
========
- **只追加不修改**：JSONL 格式，每行一个事件；线程安全的追加写。
- **按天分片**：``events/YYYY-MM-DD.jsonl``，避免单文件无限膨胀。
- **零外部依赖**：只用 stdlib，可在 3ds Max 内嵌 Python 直接跑。
- **可检索**：``search_events`` 支持 keyword / query / 时间范围 / topk。

事件 kind 枚举（约定俗成，非强约束）
====================================
- ``session_start`` / ``session_end``：会话生命周期
- ``user_input``：用户输入原文
- ``assistant_reply``：助手完整回复文本
- ``tool_call``：工具被调用（含参数）
- ``tool_result``：工具执行结果（可能被截断）
- ``memory_write``：长期记忆被修改
- 用户/上层可自定义任何 ``kind``

事件字段
========
- ``ts``：Unix 时间戳（float）
- ``iso``：ISO8601 本地时间字符串（人类友好）
- ``kind``：事件类型
- ``session_id``：可选，关联会话
- ``payload``：事件正文，任意 JSON 可序列化对象
"""

from __future__ import absolute_import
from __future__ import print_function

import json
import math
import os
import threading
import time
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ..logger import get_logger

logger = get_logger(__name__)

# 单事件 payload 序列化后的最大字节数：超过则截断，保留前缀 + 尾部标记。
# 事件日志的定位是"轻量索引"，不是"完整存档"（完整对话由 sessions/*.json 承担）
_MAX_EVENT_PAYLOAD_BYTES = 8 * 1024

_TRUNCATION_MARKER = '... [truncated]'


def _now():
    # type: () -> float
    return time.time()


def _iso_local(ts):
    # type: (float) -> str
    lt = time.localtime(ts)
    return time.strftime('%Y-%m-%dT%H:%M:%S', lt)


def _day_of(ts):
    # type: (float) -> str
    lt = time.localtime(ts)
    return time.strftime('%Y-%m-%d', lt)


def _safe_dump(payload):
    # type: (Any) -> str
    """把任意对象安全地序列化为 JSON 字符串。

    - 不可序列化对象降级为 ``repr``；
    - 长度超上限时保留前缀并追加截断标记。
    """
    try:
        text = json.dumps(payload, ensure_ascii=False, default=repr)
    except (TypeError, ValueError):
        text = json.dumps(repr(payload), ensure_ascii=False)
    if len(text.encode('utf-8')) > _MAX_EVENT_PAYLOAD_BYTES:
        # 粗略按字符截断，避免复杂的字节边界处理
        cut = _MAX_EVENT_PAYLOAD_BYTES // 4
        text = text[:cut] + _TRUNCATION_MARKER
    return text


class EventLogger(object):
    """按天分片的追加日志。

    实例是线程安全的：内部锁保护单次 append 调用。跨进程并发写没有做
    强锁（3ds Max 多进程场景极少），实践中一台机器只有一个 Max 主进程。
    """

    def __init__(self, root_dir):
        # type: (str) -> None
        self._root = root_dir
        self._events_dir = os.path.join(root_dir, 'events')
        self._lock = threading.Lock()
        try:
            os.makedirs(self._events_dir, exist_ok=True)
        except OSError as exc:
            logger.warning('创建事件日志目录失败: %s', exc)

    # ------------------------------------------------------------------ #
    # 写入
    # ------------------------------------------------------------------ #
    def log(self, kind, payload=None, session_id=''):
        # type: (str, Any, str) -> None
        """追加一条事件。异常一律吞掉，不影响主流程。"""
        if not kind:
            return
        ts = _now()
        record = {
            'ts': ts,
            'iso': _iso_local(ts),
            'kind': str(kind),
            'session_id': str(session_id or ''),
            'payload': payload if payload is not None else {},
        }
        line = _safe_dump(record) + '\n'
        path = os.path.join(self._events_dir, _day_of(ts) + '.jsonl')
        try:
            with self._lock:
                with open(path, 'a', encoding='utf-8') as fh:
                    fh.write(line)
        except OSError as exc:
            logger.debug('写入事件日志失败: %s', exc)

    # ------------------------------------------------------------------ #
    # 遍历（内部）
    # ------------------------------------------------------------------ #
    def _iter_files_between(self, start_ts, end_ts):
        # type: (Optional[float], Optional[float]) -> List[str]
        """列出 [start_ts, end_ts] 覆盖到的分片文件（按日期排序）。"""
        if not os.path.isdir(self._events_dir):
            return []
        files = []  # type: List[str]
        for fname in os.listdir(self._events_dir):
            if not fname.endswith('.jsonl'):
                continue
            files.append(fname)
        files.sort()  # 按 YYYY-MM-DD 字典序 = 时间序
        # 用文件名日期粗筛（保守：命中的都收，逐条再精筛）
        if start_ts is not None:
            start_day = _day_of(start_ts)
            files = [f for f in files if f.rsplit('.', 1)[0] >= start_day]
        if end_ts is not None:
            end_day = _day_of(end_ts)
            files = [f for f in files if f.rsplit('.', 1)[0] <= end_day]
        return [os.path.join(self._events_dir, f) for f in files]

    def _iter_events(self, start_ts, end_ts):
        # type: (Optional[float], Optional[float]) -> List[Dict[str, Any]]
        """流式读取覆盖到的所有事件，按时间升序返回。"""
        events = []  # type: List[Dict[str, Any]]
        for path in self._iter_files_between(start_ts, end_ts):
            try:
                with open(path, 'r', encoding='utf-8') as fh:
                    for raw in fh:
                        raw = raw.strip()
                        if not raw:
                            continue
                        try:
                            obj = json.loads(raw)
                        except ValueError:
                            continue
                        ts = obj.get('ts')
                        if start_ts is not None and (ts or 0) < start_ts:
                            continue
                        if end_ts is not None and (ts or 0) > end_ts:
                            continue
                        events.append(obj)
            except OSError:
                continue
        return events

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #
    def search(self, keyword='', query='', start_ts=None, end_ts=None,
               kind=None, topk=10):
        # type: (str, str, Optional[float], Optional[float], Optional[str], int) -> List[Dict[str, Any]]
        """按关键词/时间范围/kind 过滤事件。

        - ``keyword``：空格分隔的多个必要关键词（AND 语义，大小写不敏感）
        - ``query``：语义查询串；本地无 embedding，退化为按空格分词后的
          "任意匹配 + 匹配数排序"（OR 语义，命中越多越靠前）
        - ``kind``：只保留指定 kind 的事件
        - ``topk``：最多返回条数，按时间倒序（新→旧）
        """
        events = self._iter_events(start_ts, end_ts)
        if kind:
            events = [e for e in events if e.get('kind') == kind]

        # keyword AND 过滤
        keywords = _tokenize(keyword)
        if keywords:
            events = [
                e for e in events
                if all(kw in _event_text(e).lower() for kw in keywords)
            ]

        # query 打分（越大越相关）
        if query:
            query_tokens = _tokenize(query)
            if query_tokens:
                events = _rank_events_by_tfidf(events, query_tokens)
            else:
                events.sort(key=lambda e: e.get('ts', 0.0), reverse=True)
        else:
            events.sort(key=lambda e: e.get('ts', 0.0), reverse=True)

        if topk and topk > 0:
            events = events[:topk]
        return events


def _event_text(event):
    # type: (Dict[str, Any]) -> str
    """把事件展开成一段可搜索的纯文本。"""
    parts = [
        str(event.get('kind') or ''),
        str(event.get('session_id') or ''),
        str(event.get('iso') or ''),
    ]
    payload = event.get('payload')
    if payload is not None:
        try:
            parts.append(json.dumps(payload, ensure_ascii=False, default=repr))
        except (TypeError, ValueError):
            parts.append(repr(payload))
    return ' '.join(parts)


def _tokenize(text):
    # type: (str) -> List[str]
    """把 keyword / query 拆成小写 token 列表，去空。"""
    if not text:
        return []
    # 允许中文原样匹配（不分词），空格分隔多个必要项
    return [t for t in str(text).lower().split() if t]


def _rank_events_by_tfidf(events, query_tokens):
    # type: (List[Dict[str, Any]], List[str]) -> List[Dict[str, Any]]
    """使用 BM25/TF-IDF 风格打分对事件排序。

    实现说明：
    - 对每个 query token 计算其在每个事件中的词频 TF；
    - 计算 IDF = log(N / df)，N 为事件总数，df 为包含该 token 的事件数；
    - 得分 = sum(TF * IDF)；
    - 命中越多、越稀有 token 命中越多，得分越高。
    当前为全量遍历实现，事件数量大时可能较慢；未来可增量索引优化。

    :param events: 已通过 keyword/kind 过滤后的事件列表
    :param query_tokens: 小写 query token 列表
    :returns: 按得分降序 + 时间倒序排序后的事件列表
    """
    if not events or not query_tokens:
        return events

    n_events = len(events)
    event_texts = [_event_text(e).lower() for e in events]

    # 计算每个 token 的文档频率 df
    df_map = {}  # type: Dict[str, int]
    for token in query_tokens:
        df = 0
        for text in event_texts:
            if token in text:
                df += 1
        df_map[token] = df

    scored = []
    for idx, e in enumerate(events):
        text = event_texts[idx]
        score = 0.0
        for token in query_tokens:
            df = df_map.get(token, 0)
            if df == 0:
                continue
            # TF：token 在事件中出现的次数
            tf = text.count(token)
            if tf == 0:
                continue
            # IDF：平滑处理避免 log(1)=0
            idf = math.log(float(n_events) / float(df))
            score += float(tf) * idf
        if score > 0.0:
            scored.append((score, e))

    # 按得分降序 + 时间倒序（后发生的更新）
    scored.sort(
        key=lambda pair: (pair[0], pair[1].get('ts', 0.0)),
        reverse=True,
    )
    return [e for _, e in scored]


# ---------------------------------------------------------------------- #
# 单例（外部一律通过 get_event_logger 获取）
# ---------------------------------------------------------------------- #

_singleton_lock = threading.Lock()
_singleton = None  # type: Optional[EventLogger]


def get_event_logger():
    # type: () -> EventLogger
    """按 memory root 获取全局事件记录器单例。"""
    global _singleton  # pylint: disable=global-statement
    with _singleton_lock:
        if _singleton is not None:
            return _singleton
        # 延迟 import，避免循环依赖
        from .store import get_memory_root
        _singleton = EventLogger(get_memory_root())
        return _singleton


def reset_event_logger():
    # type: () -> None
    """测试用：重置单例。"""
    global _singleton  # pylint: disable=global-statement
    with _singleton_lock:
        _singleton = None


def search_events(keyword='', query='', start_ts=None, end_ts=None,
                  kind=None, topk=10):
    # type: (str, str, Optional[float], Optional[float], Optional[str], int) -> List[Dict[str, Any]]
    """便捷查询接口。"""
    return get_event_logger().search(
        keyword=keyword, query=query,
        start_ts=start_ts, end_ts=end_ts,
        kind=kind, topk=topk,
    )


__all__ = [
    'EventLogger',
    'get_event_logger',
    'reset_event_logger',
    'search_events',
]
