#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""会话（Session）管理模块。

每个 Session 对应一次完整的对话历史，独立持久化为 JSON 文件，
位于 ``{config_dir}/sessions/<dcc>/<session_id>.json``（``<dcc>`` 为
当前 DCC 标识：``3dsmax`` / ``maya`` / ``unknown``）。

设计要点：
1. 与 ``agent.conversation.Conversation`` 解耦：Session 负责"元信息 +
   存盘路径管理"，Conversation 负责消息列表本身。Session.load() 时把
   meta 与 messages 一起读出，回填到一个 Conversation 实例。
2. 文件名使用 ``YYYYMMDD-HHMMSS-<short>`` 形式，方便目录里按时间排序，
   不依赖文件系统的 mtime。
3. 标题首选用户主动设置；缺省时取首条 user 消息前 20 字。
4. 独立索引文件 ``sessions/<dcc>/_index.json`` 用于快速列出，避免每次扫盘
   都加载所有 Conversation。索引允许丢失：丢失时按目录扫描重建。
5. **DCC 隔离**：Max 与 Maya 的会话目录互相独立，避免对话记录串 DCC。
   老版本存放在 ``sessions/`` 顶层（无 DCC 子目录）的会话，会在每个
   DCC 首次使用时按 system_prompt 特征词自动分离迁移到各自目录
   （见 ``_migrate_legacy_sessions``），无法判定归属的会话原地保留。
"""

from __future__ import absolute_import
from __future__ import print_function

import json
import os
import time
import uuid
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from .agent.conversation import Conversation
from .config import get_config_dir
from .dcc.runtime import current_dcc
from .logger import get_logger


SESSIONS_DIRNAME = 'sessions'
INDEX_FILENAME = '_index.json'

# 老版本顶层目录迁移完成标记文件（放在各 DCC 子目录内，每个 DCC 只扫一次）
_MIGRATION_MARKER = '.dcc_migrated'

# 老 system_prompt 中的 DCC 特征词（仅用于无 meta.dcc 的老会话文件判定；
# 每个特征词都在 build_default_system_prompt 中按 DCC 分支逐字出现，
# 两边互不重叠）。判定方式：两侧特征词命中计数取大者，平局视为未知。
_LEGACY_DCC_HINTS = {
    'maya': (
        'Maya 环境中',
        'list_maya_objects',
        'get_maya_object_info',
        'Maya current linear unit',
        'Maya 世界观速查',
        'list_maya_knowledge_topics',
    ),
    '3dsmax': (
        '3ds Max 环境中',
        'run_maxscript',
        'Max system unit',
        '3ds Max 世界观速查',
        'list_max_knowledge_topics',
        'list_scene_objects',
    ),
}

logger = get_logger(__name__)

# 会话标题最大长度（字符）
MAX_TITLE_LEN = 30


def get_sessions_dir():
    # type: () -> str
    """获取会话存储目录，不存在则创建。"""
    base = get_config_dir()
    path = os.path.join(base, SESSIONS_DIRNAME)
    if not os.path.isdir(path):
        os.makedirs(path)
    return path


def _now_ts():
    return time.time()


def _ts_to_filename_prefix(ts):
    # 用本地时间生成一个易读的前缀，方便目录排序
    lt = time.localtime(ts)
    return time.strftime('%Y%m%d-%H%M%S', lt)


def _short_uid():
    return uuid.uuid4().hex[:8]


def _truncate_title(text):
    # type: (str) -> str
    if not text:
        return '(空)'
    text = text.strip().replace('\n', ' ')
    if len(text) <= MAX_TITLE_LEN:
        return text
    return text[:MAX_TITLE_LEN] + '...'


class SessionMeta(object):
    """会话元信息（不含消息体，索引和列表里用这个）。"""

    def __init__(self, sid, title, created_at, updated_at, message_count=0,
                 file_path=None, dcc=''):
        self.sid = sid
        self.title = title
        self.created_at = float(created_at)
        self.updated_at = float(updated_at)
        self.message_count = int(message_count)
        # file_path 为运行时填充，序列化到索引时也会带上
        self.file_path = file_path
        # 该会话归属的 DCC 标识（'3dsmax' / 'maya' / 'unknown'）。
        # 新会话写入时记录；老会话迁移后补写，便于排查与后续按 DCC 过滤。
        self.dcc = dcc or ''

    def to_dict(self):
        return {
            'sid': self.sid,
            'title': self.title,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'message_count': self.message_count,
            'file_path': self.file_path,
            'dcc': self.dcc,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            sid=data.get('sid', ''),
            title=data.get('title', ''),
            created_at=float(data.get('created_at', 0.0) or 0.0),
            updated_at=float(data.get('updated_at', 0.0) or 0.0),
            message_count=int(data.get('message_count', 0) or 0),
            file_path=data.get('file_path'),
            dcc=data.get('dcc', ''),
        )


class SessionManager(object):
    """会话 CRUD 管理。

    使用方式::

        mgr = SessionManager()
        s = mgr.create_session()           # 新建
        mgr.save(s, conversation)          # 存盘
        metas = mgr.list_sessions()        # 列出（按 updated_at 倒序）
        conv = mgr.load(s.sid)             # 加载完整对话
        mgr.delete(s.sid)                  # 删除
        mgr.rename(s.sid, '我的项目')      # 重命名
    """

    def __init__(self, base_dir=None):
        # type: (Optional[str]) -> None
        if base_dir:
            # 显式指定 base_dir（测试 / 特殊部署）：完全按调用方意图使用，
            # 不追加 DCC 子目录、不做老版本迁移。
            self._base = base_dir
            self._dcc = current_dcc()
        else:
            # 默认路径：按当前 DCC 隔离到 sessions/<dcc>/ 子目录，
            # 并在首次使用时把老版本顶层目录里的本 DCC 会话迁移过来。
            self._dcc = current_dcc()
            root = get_sessions_dir()
            self._base = os.path.join(root, self._dcc)
            self._migrate_legacy_sessions(root, self._base, self._dcc)
        if not os.path.isdir(self._base):
            os.makedirs(self._base)

    # ------------------------------------------------------------------ #
    # 路径相关
    # ------------------------------------------------------------------ #
    def _index_path(self):
        return os.path.join(self._base, INDEX_FILENAME)

    def _file_path_for(self, meta):
        # type: (SessionMeta) -> str
        if meta.file_path and os.path.dirname(meta.file_path) == self._base:
            return meta.file_path
        prefix = _ts_to_filename_prefix(meta.created_at)
        return os.path.join(
            self._base, '{}-{}.json'.format(prefix, meta.sid),
        )

    # ------------------------------------------------------------------ #
    # 索引读写（容错：损坏 / 缺失时扫描目录重建）
    # ------------------------------------------------------------------ #
    def _load_index(self):
        # type: () -> List[SessionMeta]
        path = self._index_path()
        if not os.path.exists(path):
            return self._rebuild_index()
        try:
            with open(path, 'r', encoding='utf-8') as fh:
                raw = json.load(fh)
            metas = [SessionMeta.from_dict(d) for d in raw.get('sessions', [])]
            # 索引可能引用了已被外部删除的文件，过滤掉
            return [
                m for m in metas
                if m.file_path and os.path.exists(m.file_path)
            ]
        except (OSError, ValueError, KeyError) as exc:
            logger.warning('sessions 索引损坏，重建: %s', exc)
            return self._rebuild_index()

    def _save_index(self, metas):
        # type: (List[SessionMeta]) -> None
        path = self._index_path()
        tmp = path + '.tmp'
        data = {'sessions': [m.to_dict() for m in metas]}
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        if os.path.exists(path):
            os.replace(tmp, path)
        else:
            os.rename(tmp, path)

    def _rebuild_index(self):
        # type: () -> List[SessionMeta]
        metas = []
        for fname in os.listdir(self._base):
            if fname == INDEX_FILENAME or not fname.endswith('.json'):
                continue
            full = os.path.join(self._base, fname)
            try:
                with open(full, 'r', encoding='utf-8') as fh:
                    data = json.load(fh)
                meta_dict = data.get('meta') or {}
                # 老格式兜底：没有 meta 也尽量恢复
                if not meta_dict:
                    meta_dict = {
                        'sid': fname.rsplit('-', 1)[-1].split('.')[0],
                        'title': fname,
                        'created_at': os.path.getctime(full),
                        'updated_at': os.path.getmtime(full),
                    }
                meta = SessionMeta.from_dict(meta_dict)
                meta.file_path = full
                msgs = (data.get('conversation') or {}).get('messages', [])
                meta.message_count = len(msgs)
                metas.append(meta)
            except (OSError, ValueError) as exc:
                logger.warning(
                    '跳过损坏的会话文件 %s: %s', fname, exc,
                )
                continue
        metas.sort(key=lambda m: m.updated_at, reverse=True)
        try:
            self._save_index(metas)
        except OSError:
            pass
        return metas

    # ------------------------------------------------------------------ #
    # 公共 API
    # ------------------------------------------------------------------ #
    def list_sessions(self):
        # type: () -> List[SessionMeta]
        """按 updated_at 倒序返回所有会话元信息。"""
        metas = self._load_index()
        metas.sort(key=lambda m: m.updated_at, reverse=True)
        return metas

    def create_session(self, title=None, system_prompt=None):
        # type: (Optional[str], Optional[str]) -> SessionMeta
        """新建一个空会话并写入索引。

        :param title: 可选标题；缺省 '新对话'，后续会按首条 user 消息覆写。
        :param system_prompt: 可选 system prompt 原文。传入后写入新会话
            的 ``Conversation.system_prompt``——用于"岗位 / 员工分离"
            场景：调用方（dock_widget）可注入"当前员工身份"对应的
            prompt，让 LLM 在新会话里自我介绍跟随员工名。
            为 None 时回落到 ``Conversation`` 默认值（兼容旧调用方）。
        """
        now = _now_ts()
        sid = _short_uid()
        meta = SessionMeta(
            sid=sid,
            title=title or '新对话',
            created_at=now,
            updated_at=now,
            message_count=0,
            dcc=self._dcc,
        )
        meta.file_path = self._file_path_for(meta)
        # 写入空 conversation 文件，确保 list 时能看到。
        # system_prompt 可由调用方按"当前员工身份"动态注入，使新会话
        # 在 LLM 视角下立即生效新的对外名字。
        empty_conv = Conversation(system_prompt=system_prompt)
        self._write_session_file(meta, empty_conv)
        # 更新索引
        metas = self._load_index()
        metas.insert(0, meta)
        self._save_index(metas)
        # DEBUG 埋点：会话生命周期
        logger.debug(
            'session_create sid=%s title=%s has_prompt=%s',
            sid, meta.title, system_prompt is not None,
        )
        # 事件日志：session_start
        try:
            from .memory import get_event_logger
            get_event_logger().log(
                'session_start',
                payload={'sid': sid, 'title': meta.title},
                session_id=sid,
            )
        except Exception:  # pylint: disable=broad-except
            pass
        return meta

    def save(self, meta, conversation):
        # type: (SessionMeta, Conversation) -> None
        """保存会话。会自动更新 updated_at 与 message_count。"""
        meta.updated_at = _now_ts()
        meta.message_count = len(conversation)
        # 自动从首条 user 消息生成标题（仅在用户没主动改过时）
        if meta.title in ('', '新对话'):
            for m in conversation.messages:
                if m.role == 'user' and (m.content or '').strip():
                    meta.title = _truncate_title(m.content)
                    break
        self._write_session_file(meta, conversation)
        # 更新索引
        metas = self._load_index()
        replaced = False
        for i, m in enumerate(metas):
            if m.sid == meta.sid:
                metas[i] = meta
                replaced = True
                break
        if not replaced:
            metas.append(meta)
        metas.sort(key=lambda m: m.updated_at, reverse=True)
        self._save_index(metas)

    def load(self, sid):
        # type: (str) -> Optional[tuple]
        """加载指定会话，返回 ``(meta, Conversation)``，找不到返回 None。"""
        for m in self._load_index():
            if m.sid == sid:
                if not m.file_path or not os.path.exists(m.file_path):
                    return None
                try:
                    with open(m.file_path, 'r', encoding='utf-8') as fh:
                        data = json.load(fh)
                except (OSError, ValueError):
                    return None
                conv = Conversation.from_json(data.get('conversation', {}))
                # DEBUG 埋点：会话加载
                logger.debug(
                    'session_load sid=%s title=%s msgs=%d',
                    sid, m.title, len(conv),
                )
                return m, conv
        return None

    def delete(self, sid):
        # type: (str) -> bool
        """删除会话文件并更新索引。"""
        metas = self._load_index()
        target = None
        for m in metas:
            if m.sid == sid:
                target = m
                break
        if target is None:
            return False
        if target.file_path and os.path.exists(target.file_path):
            try:
                os.remove(target.file_path)
            except OSError as exc:
                logger.warning('删除会话文件失败: %s', exc)
        metas = [m for m in metas if m.sid != sid]
        self._save_index(metas)
        # DEBUG 埋点：会话删除
        logger.debug('session_delete sid=%s title=%s', sid, target.title)
        # 事件日志：session_end
        try:
            from .memory import get_event_logger
            get_event_logger().log(
                'session_end',
                payload={'sid': sid, 'title': target.title},
                session_id=sid,
            )
        except Exception:  # pylint: disable=broad-except
            pass
        return True

    def rename(self, sid, new_title):
        # type: (str, str) -> bool
        """重命名会话，同时更新元信息和文件内容。"""
        new_title = (new_title or '').strip() or '未命名'
        new_title = new_title[:MAX_TITLE_LEN]
        result = self.load(sid)
        if result is None:
            return False
        meta, conv = result
        meta.title = new_title
        meta.updated_at = _now_ts()
        self._write_session_file(meta, conv)
        # 更新索引
        metas = self._load_index()
        for i, m in enumerate(metas):
            if m.sid == sid:
                metas[i] = meta
                break
        self._save_index(metas)
        return True

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    @staticmethod
    def _classify_legacy_dcc(data):
        # type: (Dict[str, Any]) -> str
        """按 system_prompt 特征词判定老会话文件归属的 DCC。

        :param data: 会话 JSON 反序列化后的完整 dict
        :returns: '3dsmax' / 'maya'；无法判定（含空会话）返回 ''
        """
        conv = data.get('conversation') or {}
        prompt = conv.get('system_prompt') or ''
        if not prompt:
            return ''
        best_name = ''
        best_score = 0
        for name, hints in _LEGACY_DCC_HINTS.items():
            score = 0
            for hint in hints:
                if hint in prompt:
                    score += 1
            if score > best_score:
                best_score = score
                best_name = name
        # 命中数不足 2 视为证据不足（避免巧合词误判），平局也视为未知
        if best_score < 2:
            return ''
        return best_name

    def _migrate_legacy_sessions(self, legacy_dir, target_dir, dcc_name):
        # type: (str, str, str) -> None
        """把老版本顶层 sessions/ 目录里属于当前 DCC 的会话迁移过来。

        迁移规则（幂等，每个 DCC 子目录只执行一次）：
        - ``target_dir`` 已有 ``.dcc_migrated`` 标记则直接跳过；
        - 扫描 ``legacy_dir`` 顶层的 ``*.json``（跳过索引与临时文件）；
        - meta 里已带 ``dcc`` 字段的（新格式误落顶层）：仅当与当前 DCC
          一致时搬走；
        - 无 ``dcc`` 字段的老文件：按 system_prompt 特征词判定归属，
          仅搬走属于当前 DCC 的；无法判定的原地保留（两个 DCC 各判各的，
          都判不出的会话不丢失）；
        - 搬运 = 复制到目标目录（补写 meta.dcc）+ 删除源文件；任何单个
          文件失败不影响其余文件。
        """
        marker = os.path.join(target_dir, _MIGRATION_MARKER)
        if os.path.exists(marker):
            return
        if not os.path.isdir(legacy_dir) or legacy_dir == target_dir:
            try:
                os.makedirs(target_dir, exist_ok=True)
                with open(marker, 'w', encoding='utf-8') as fh:
                    fh.write(dcc_name)
            except OSError:
                logger.warning('无法写入 DCC 迁移标记: %s', marker)
            return
        moved = 0
        skipped = 0
        try:
            names = os.listdir(legacy_dir)
        except OSError as exc:
            logger.warning('扫描老版本会话目录失败 %s: %s', legacy_dir, exc)
            names = []
        for fname in names:
            if fname == INDEX_FILENAME or not fname.endswith('.json'):
                continue
            src = os.path.join(legacy_dir, fname)
            if not os.path.isfile(src):
                continue
            try:
                with open(src, 'r', encoding='utf-8') as fh:
                    data = json.load(fh)
            except (OSError, ValueError):
                skipped += 1
                continue
            meta_dict = data.get('meta') or {}
            owner = meta_dict.get('dcc') or ''
            if not owner:
                owner = self._classify_legacy_dcc(data)
            if owner != dcc_name:
                # 属于其他 DCC 或无法判定：留在原地
                continue
            # 目标目录补写归属字段后落盘，再删除源文件
            meta_dict['dcc'] = dcc_name
            data['meta'] = meta_dict
            dst = os.path.join(target_dir, fname)
            try:
                os.makedirs(target_dir, exist_ok=True)
                with open(dst, 'w', encoding='utf-8') as fh:
                    json.dump(data, fh, ensure_ascii=False, indent=2)
                os.remove(src)
                moved += 1
            except OSError as exc:
                logger.warning('迁移会话 %s 失败: %s', fname, exc)
                skipped += 1
        try:
            os.makedirs(target_dir, exist_ok=True)
            with open(marker, 'w', encoding='utf-8') as fh:
                fh.write(dcc_name)
        except OSError:
            logger.warning('无法写入 DCC 迁移标记: %s', marker)
        logger.info(
            'sessions DCC 迁移(%s): 迁入 %d 个会话, 保留 %d 个',
            dcc_name, moved, skipped,
        )

    def _write_session_file(self, meta, conversation):
        # type: (SessionMeta, Conversation) -> None
        path = self._file_path_for(meta)
        meta.file_path = path
        data = {
            'meta': meta.to_dict(),
            'conversation': conversation.to_json(),
        }
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        if os.path.exists(path):
            os.replace(tmp, path)
        else:
            os.rename(tmp, path)


__all__ = [
    'SessionMeta',
    'SessionManager',
    'get_sessions_dir',
    'MAX_TITLE_LEN',
]