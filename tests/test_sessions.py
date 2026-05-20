#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试 SessionManager：创建/保存/加载/重命名/删除 + 索引重建。"""

from __future__ import absolute_import
from __future__ import print_function

import os

import pytest

from maxagent.agent.conversation import Conversation
from maxagent.sessions import (
    INDEX_FILENAME,
    SessionManager,
    SessionMeta,
)


@pytest.fixture
def sessions_dir(tmp_path):
    d = tmp_path / 'sessions'
    d.mkdir()
    return str(d)


class TestSessionLifecycle:
    def test_create_session_writes_file(self, sessions_dir):
        mgr = SessionManager(base_dir=sessions_dir)
        meta = mgr.create_session()
        assert meta.sid
        assert os.path.exists(meta.file_path)
        # 索引也应包含它
        assert mgr.list_sessions()[0].sid == meta.sid

    def test_save_then_load_roundtrip(self, sessions_dir):
        mgr = SessionManager(base_dir=sessions_dir)
        meta = mgr.create_session()
        conv = Conversation()
        conv.add_user('hello')
        conv.add_assistant(content='hi')
        mgr.save(meta, conv)

        result = mgr.load(meta.sid)
        assert result is not None
        meta2, conv2 = result
        assert meta2.sid == meta.sid
        assert len(conv2) == 2
        assert conv2.messages[0].content == 'hello'

    def test_auto_title_from_first_user_msg(self, sessions_dir):
        mgr = SessionManager(base_dir=sessions_dir)
        meta = mgr.create_session()
        conv = Conversation()
        conv.add_user('请创建一个红色的茶壶并加上修改器')
        mgr.save(meta, conv)
        assert '请创建一个红色的茶壶' in meta.title

    def test_delete_session(self, sessions_dir):
        mgr = SessionManager(base_dir=sessions_dir)
        m1 = mgr.create_session()
        m2 = mgr.create_session()
        assert mgr.delete(m1.sid) is True
        names = [s.sid for s in mgr.list_sessions()]
        assert m1.sid not in names
        assert m2.sid in names

    def test_delete_nonexistent(self, sessions_dir):
        mgr = SessionManager(base_dir=sessions_dir)
        assert mgr.delete('not_a_real_sid') is False

    def test_rename_persists(self, sessions_dir):
        mgr = SessionManager(base_dir=sessions_dir)
        meta = mgr.create_session()
        assert mgr.rename(meta.sid, '我的项目') is True
        # reload 后标题应变
        mgr2 = SessionManager(base_dir=sessions_dir)
        loaded = mgr2.list_sessions()
        assert loaded[0].title == '我的项目'

    def test_list_sorted_by_updated_at(self, sessions_dir):
        mgr = SessionManager(base_dir=sessions_dir)
        m1 = mgr.create_session()
        m2 = mgr.create_session()
        # 触发 m1 的 updated_at 后置
        conv = Conversation()
        conv.add_user('x')
        mgr.save(m1, conv)
        names = [s.sid for s in mgr.list_sessions()]
        # m1 现在应在最前
        assert names[0] == m1.sid


class TestSessionIndexRebuild:
    def test_missing_index_rebuilt_from_files(self, sessions_dir):
        mgr = SessionManager(base_dir=sessions_dir)
        m1 = mgr.create_session()
        m2 = mgr.create_session()
        # 删掉索引
        idx_path = os.path.join(sessions_dir, INDEX_FILENAME)
        os.remove(idx_path)
        # 新 manager 重新扫
        mgr2 = SessionManager(base_dir=sessions_dir)
        sids = {s.sid for s in mgr2.list_sessions()}
        assert m1.sid in sids and m2.sid in sids

    def test_corrupt_index_rebuilt(self, sessions_dir):
        mgr = SessionManager(base_dir=sessions_dir)
        m1 = mgr.create_session()
        # 把索引写成垃圾
        idx_path = os.path.join(sessions_dir, INDEX_FILENAME)
        with open(idx_path, 'w', encoding='utf-8') as fh:
            fh.write('{this is not json')
        mgr2 = SessionManager(base_dir=sessions_dir)
        sids = {s.sid for s in mgr2.list_sessions()}
        assert m1.sid in sids

    def test_skip_corrupt_session_file(self, sessions_dir):
        mgr = SessionManager(base_dir=sessions_dir)
        m1 = mgr.create_session()
        # 写一个坏掉的会话文件
        bad = os.path.join(sessions_dir, 'bad.json')
        with open(bad, 'w', encoding='utf-8') as fh:
            fh.write('{nope')
        os.remove(os.path.join(sessions_dir, INDEX_FILENAME))
        mgr2 = SessionManager(base_dir=sessions_dir)
        sids = {s.sid for s in mgr2.list_sessions()}
        # 坏文件被跳过，但好的还在
        assert m1.sid in sids

    def test_index_filters_missing_files(self, sessions_dir):
        mgr = SessionManager(base_dir=sessions_dir)
        m1 = mgr.create_session()
        m2 = mgr.create_session()
        # 直接删掉 m1 的文件（不通过 mgr.delete）
        os.remove(m1.file_path)
        # mgr 重新加载，应过滤掉 m1
        mgr2 = SessionManager(base_dir=sessions_dir)
        sids = {s.sid for s in mgr2.list_sessions()}
        assert m1.sid not in sids
        assert m2.sid in sids
