#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跨会话持久化学习（Session-to-Session Memory）。

职责：
1. 在一次会话成功结束后，自动从对话中提取**用户偏好**和**成功模式**。
2. 将提取的记忆以加权卡片形式持久化到磁盘。
3. 新建会话时，按相关性和时效性排序注入 system prompt，
   让 LLM 在新会话中继承旧经验。

三层记忆架构：
- 🟢 事实记忆 (fact)：用户场景中的稳定事实，如"场景中有 Box01"。
- 🟡 偏好记忆 (preference)：用户的工作习惯，如"喜欢用英文命名"。
- 🔵 模式记忆 (pattern)：成功的工作流模式，如"创建后通常设置材质"。

生命周期：
- 创建：会话结束后自动提取（由 AgentWorker 调用 learn_from_session）
- 读取：新建会话时由 sys_addon_provider 注入
- 遗忘：低频/超期记忆自动淡出（90 天未使用或评分过低）
- 限制：最多 20 条，最多 5 条注入 prompt，防止污染 system prompt
"""

from __future__ import absolute_import
from __future__ import print_function

import json
import os
import time
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from .logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------- #
# 常量与配置
# ---------------------------------------------------------------------- #

_MAX_MEMORY_ITEMS = 20          # 最多保留 20 条记忆
_MAX_INJECT_COUNT = 5           # 最多注入 5 条到 prompt
_MAX_MEMORY_AGE_DAYS = 90       # 90 天未使用则淡出
_DEFAULT_MEMORY_DIR = os.path.join(
    os.path.expanduser('~'), '.maxagent', 'session_memory',
)
_MEMORY_INDEX_FILE = 'memory_index.json'


# ---------------------------------------------------------------------- #
# 数据模型
# ---------------------------------------------------------------------- #

@dataclass
class MemoryItem(object):
    """单条跨会话记忆卡片。"""

    uid: str = ''                       # 唯一标识 (uuid4 或 hash)
    type: str = ''                      # fact | preference | pattern
    content: str = ''                   # 自然语言描述
    confidence: float = 0.5             # 0.0 - 1.0
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    access_count: int = 0
    source_sessions: List[str] = field(default_factory=list)

    def score(self):
        # type: () -> float
        """综合评分。"""
        age_days = (time.time() - self.created_at) / 86400.0
        recency = 1.0 / (1.0 + age_days / 30.0)
        popularity = min(self.access_count / 10.0, 1.0)
        return self.confidence * 0.4 + recency * 0.3 + popularity * 0.3

    def to_dict(self):
        # type: () -> Dict[str, Any]
        return {
            'uid': self.uid,
            'type': self.type,
            'content': self.content,
            'confidence': self.confidence,
            'created_at': self.created_at,
            'last_accessed': self.last_accessed,
            'access_count': self.access_count,
            'source_sessions': self.source_sessions,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            uid=d.get('uid', ''),
            type=d.get('type', ''),
            content=d.get('content', ''),
            confidence=d.get('confidence', 0.5),
            created_at=d.get('created_at', 0.0),
            last_accessed=d.get('last_accessed', 0.0),
            access_count=d.get('access_count', 0),
            source_sessions=d.get('source_sessions', []),
        )


# ---------------------------------------------------------------------- #
# SessionMemoryManager
# ---------------------------------------------------------------------- #

class SessionMemoryManager(object):
    """跨会话记忆管理器。

    单例模式（通过 ``get_session_memory_mgr()`` 获取）。
    """

    _instance = None  # type: Optional[SessionMemoryManager]
    _lock = False

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(SessionMemoryManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, storage_dir=None):
        if self._initialized:
            return
        self._initialized = True
        self._dir = storage_dir or _DEFAULT_MEMORY_DIR
        self._items = []  # type: List[MemoryItem]
        self._changed = False
        try:
            os.makedirs(self._dir, exist_ok=True)
        except Exception:  # pylint: disable=broad-except
            pass
        self._load()

    # ------------------------------------------------------------------ #
    # 学习：从会话中提取
    # ------------------------------------------------------------------ #

    def learn_from_session(self, conversation, session_id=''):
        # type: (Any, str) -> int
        """从已完成的对话中提取记忆。

        :returns: 新增的记忆条数
        """
        new_items = []

        try:
            facts = self._extract_facts(conversation)
            preferences = self._extract_preferences(conversation)
            patterns = self._extract_patterns(conversation)
            new_items = facts + preferences + patterns
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning('从会话提取记忆失败: %s', exc)
            return 0

        added = 0
        for item in new_items:
            if not self._is_redundant(item):
                self._items.append(item)
                added += 1

        if added:
            logger.info('从会话 %s 提取了 %d 条新记忆', session_id, added)
            self._changed = True
            self._prune_and_save()
        return added

    def _extract_facts(self, conversation):
        # type: (Any) -> List[MemoryItem]
        """提取场景事实。"""
        items = []
        msgs = getattr(conversation, 'messages', [])
        if not isinstance(msgs, list):
            return items

        # 简单的启发式规则：检测到"创建/命名/修改"相关描述
        all_tools = []
        for msg in msgs:
            tcs = msg.get('tool_calls', [])
            if isinstance(tcs, list):
                for tc in tcs:
                    fn = tc.get('function', {})
                    all_tools.append(fn.get('name', ''))

        # 检测"创建了某物体"事实
        created_names = set()
        for msg in msgs:
            tcs = msg.get('tool_calls', [])
            if not isinstance(tcs, list):
                continue
            for tc in tcs:
                fn = tc.get('function', {})
                tname = fn.get('name', '')
                args = fn.get('arguments', {})
                if tname.startswith('create_') and 'name' in args:
                    created_names.add(args['name'])

        if created_names:
            items.append(MemoryItem(
                uid=_make_uid(),
                type='fact',
                content='场景中存在对象: {}'.format(
                    ', '.join(sorted(created_names)),
                ),
                confidence=0.9,
            ))

        return items

    def _extract_preferences(self, conversation):
        # type: (Any) -> List[MemoryItem]
        """提取用户偏好（基于工具调用模式）。"""
        items = []
        msgs = getattr(conversation, 'messages', [])

        # 统计命名语言偏好
        all_names = []
        for msg in msgs:
            tcs = msg.get('tool_calls', [])
            if not isinstance(tcs, list):
                continue
            for tc in tcs:
                fn = tc.get('function', {})
                args = fn.get('arguments', {})
                if 'name' in args:
                    all_names.append(args['name'])

        if all_names:
            ascii_names = sum([
                1 for n in all_names if n and n[0].isascii()
            ])
            ratio = ascii_names / len(all_names)
            if ratio >= 0.8:
                items.append(MemoryItem(
                    uid=_make_uid(),
                    type='preference',
                    content='用户偏好用英文/ASCII 命名对象',
                    confidence=round(ratio, 2),
                ))
            elif ratio <= 0.2:
                items.append(MemoryItem(
                    uid=_make_uid(),
                    type='preference',
                    content='用户偏好用中文命名对象',
                    confidence=round(1.0 - ratio, 2),
                ))

        # 检测颜色偏好
        color_uses = {}
        for msg in msgs:
            tcs = msg.get('tool_calls', [])
            if not isinstance(tcs, list):
                continue
            for tc in tcs:
                fn = tc.get('function', {})
                args = fn.get('arguments', {})
                color = args.get('color') or args.get('diffuse_color')
                if color and isinstance(color, list) and len(color) >= 3:
                    key = '{}-{}-{}'.format(
                        color[0], color[1], color[2],
                    )
                    color_uses[key] = color_uses.get(key, 0) + 1

        if color_uses:
            best = max(color_uses, key=color_uses.get)
            best_v = color_uses[best]
            if best_v >= 2:
                items.append(MemoryItem(
                    uid=_make_uid(),
                    type='preference',
                    content='用户频繁使用颜色 RGB{}'.format(best),
                    confidence=min(0.5 + best_v * 0.1, 0.95),
                ))

        return items

    def _extract_patterns(self, conversation):
        # type: (Any) -> List[MemoryItem]
        """提取成功工作流模式。"""
        items = []
        msgs = getattr(conversation, 'messages', [])

        # 收集所有 tool_calls
        all_tool_chains = []
        for msg in msgs:
            tcs = msg.get('tool_calls', [])
            if isinstance(tcs, list):
                chain = [tc.get('function', {}).get('name', '') for tc in tcs]
                if chain:
                    all_tool_chains.append(chain)

        if not all_tool_chains:
            return items

        # 统计常见 2-step / 3-step 模式
        two_step = {}
        for chain in all_tool_chains:
            for i in range(len(chain) - 1):
                pair = (chain[i], chain[i + 1])
                two_step[pair] = two_step.get(pair, 0) + 1

        for pair, count in two_step.items():
            if count >= 2:
                items.append(MemoryItem(
                    uid=_make_uid(),
                    type='pattern',
                    content='常用工作流两步模式: {} → {}'.format(
                        pair[0], pair[1],
                    ),
                    confidence=min(0.5 + count * 0.1, 0.9),
                ))

        # 统计 3-step 模式
        three_step = {}
        for chain in all_tool_chains:
            for i in range(len(chain) - 2):
                triple = (chain[i], chain[i + 1], chain[i + 2])
                three_step[triple] = three_step.get(triple, 0) + 1

        for triple, count in three_step.items():
            if count >= 2:
                items.append(MemoryItem(
                    uid=_make_uid(),
                    type='pattern',
                    content='常用工作流三步模式: {} → {} → {}'.format(
                        triple[0], triple[1], triple[2],
                    ),
                    confidence=min(0.5 + count * 0.1, 0.9),
                ))

        return items

    def _is_redundant(self, new_item):
        # type: (MemoryItem) -> bool
        """检查新记忆是否与已有记忆重复（内容相似）。"""
        if not new_item.content:
            return True
        for existing in self._items:
            if existing.content == new_item.content:
                # 完全重复：提升置信度和访问次数
                existing.confidence = max(
                    existing.confidence, new_item.confidence,
                )
                existing.access_count += 1
                existing.last_accessed = time.time()
                return True
            # 简单包含检测（如"创建了 Box"和"创建了 Box01"不算重复）
            if new_item.content in existing.content:
                return True
        return False

    # ------------------------------------------------------------------ #
    # 注入：构建 prompt 附加文本
    # ------------------------------------------------------------------ #

    def get_prompt_addon(self, current_input='', session_id=''):
        # type: (str, str) -> str
        """获取应注入 system prompt 的记忆片段。

        :params current_input: 当前用户输入，用于相关性排序
        :returns: 格式化后的 prompt 文本（空字符串表示无记忆）
        """
        if not self._items:
            return ''

        relevant = self._rank_by_relevance(self._items, current_input)
        top_items = relevant[:_MAX_INJECT_COUNT]
        if not top_items:
            return ''

        lines = ['【跨会话经验】以下内容来自之前对话的自动提取：']
        for item in top_items:
            tag = {'fact': '✦', 'preference': '✧', 'pattern': '▸'}.get(
                item.type, '•',
            )
            lines.append(
                '  {} [{}] {}'.format(tag, item.type, item.content),
            )
            item.access_count += 1
            item.last_accessed = time.time()

        self._changed = True
        return '\n'.join(lines)

    def _rank_by_relevance(self, items, current_input):
        # type: (List[MemoryItem], str) -> List[MemoryItem]
        """按评分排序， optionally 提升与当前输入相关的记忆。"""
        scored = [(item.score(), item) for item in items]
        # 简单关键词匹配提升
        if current_input:
            keywords = set(current_input.lower().split())
            for i, (base_score, item) in enumerate(scored):
                content_lower = item.content.lower()
                match_count = sum([
                    1 for kw in keywords if kw in content_lower
                ])
                if match_count:
                    scored[i] = (base_score + match_count * 0.1, item)

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored]

    # ------------------------------------------------------------------ #
    # 剪枝与持久化
    # ------------------------------------------------------------------ #

    def _prune_and_save(self):
        # type: () -> None
        """剪枝过期/低频记忆并保存。"""
        now = time.time()
        cutoff = now - (_MAX_MEMORY_AGE_DAYS * 86400.0)

        # 保留：未过期 + 评分 > 0.1 + 数量不超上限
        kept = [
            item for item in self._items
            if item.last_accessed >= cutoff and item.score() > 0.1
        ]
        kept.sort(key=lambda x: x.score(), reverse=True)
        if len(kept) > _MAX_MEMORY_ITEMS:
            kept = kept[:_MAX_MEMORY_ITEMS]

        removed = len(self._items) - len(kept)
        if removed > 0:
            logger.info('记忆剪枝: 移除了 %d 条过期/低频记忆', removed)
        self._items = kept
        self._save()

    def _save(self):
        # type: () -> None
        if not self._changed or not self._dir:
            return
        try:
            fpath = os.path.join(self._dir, _MEMORY_INDEX_FILE)
            data = [item.to_dict() for item in self._items]
            with open(fpath, 'w', encoding='utf-8') as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
            self._changed = False
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning('记忆持久化失败: %s', exc)

    def _load(self):
        # type: () -> None
        if not self._dir:
            return
        try:
            fpath = os.path.join(self._dir, _MEMORY_INDEX_FILE)
            if not os.path.exists(fpath):
                return
            with open(fpath, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
            self._items = [MemoryItem.from_dict(d) for d in data]
            logger.info(
                '加载了 %d 条跨会话记忆', len(self._items),
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning('记忆加载失败: %s', exc)
            self._items = []

    # ------------------------------------------------------------------ #
    # 公共接口
    # ------------------------------------------------------------------ #

    def clear(self):
        # type: () -> None
        """清空所有记忆（谨慎使用）。"""
        self._items.clear()
        self._changed = True
        self._save()

    def list_items(self):
        # type: () -> List[MemoryItem]
        """列出所有记忆（用于 UI 展示或 debug）。"""
        return list(self._items)

    def get_item_count(self):
        # type: () -> int
        return len(self._items)

    def force_save(self):
        # type: () -> None
        self._changed = True
        self._save()


# ---------------------------------------------------------------------- #
# 工具函数
# ---------------------------------------------------------------------- #

def _make_uid():
    # type: () -> str
    """生成简单位移标识符。"""
    import uuid
    return uuid.uuid4().hex[:12]


# 全局单例
_session_memory_mgr = None  # type: Optional[SessionMemoryManager]


def get_session_memory_mgr(storage_dir=None):
    # type: (Optional[str]) -> SessionMemoryManager
    """获取跨会话记忆管理器单例。"""
    global _session_memory_mgr
    if _session_memory_mgr is None:
        _session_memory_mgr = SessionMemoryManager(storage_dir)
    return _session_memory_mgr


def reset_session_memory_mgr():
    # type: () -> None
    """重置单例（主要用于测试）。"""
    global _session_memory_mgr
    SessionMemoryManager._instance = None
    SessionMemoryManager._initialized = False
    _session_memory_mgr = None
