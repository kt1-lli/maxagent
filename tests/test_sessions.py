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


class TestSessionEmployeeInjection:
    """覆盖"岗位 / 员工分离"在 SessionManager 层的注入路径。

    bug 复现：v0.x 之前 ``create_session`` 内部直接 ``Conversation()``，
    导致用户改员工名后新建的会话仍用默认 system prompt，LLM 自我介绍
    继续说"我是 MaxAgent"。本测试类锁定该路径。
    """

    def test_create_session_default_uses_default_prompt(self, sessions_dir):
        # 不传 system_prompt 时，回落到 Conversation 默认值（向后兼容）
        from maxagent.agent.conversation import DEFAULT_SYSTEM_PROMPT
        mgr = SessionManager(base_dir=sessions_dir)
        meta = mgr.create_session()
        result = mgr.load(meta.sid)
        assert result is not None
        _, conv = result
        assert conv.system_prompt == DEFAULT_SYSTEM_PROMPT

    def test_create_session_injects_custom_prompt(self, sessions_dir):
        # 传入"员工尼娜"对应的 prompt 后，存盘和重读应保持一致
        from maxagent.agent.conversation import build_default_system_prompt
        mgr = SessionManager(base_dir=sessions_dir)
        custom = build_default_system_prompt('尼娜')
        meta = mgr.create_session(system_prompt=custom)

        # 存盘文件里 system_prompt 应是新版本（含尼娜身份铁律）
        result = mgr.load(meta.sid)
        assert result is not None
        _, conv = result
        assert conv.system_prompt == custom
        assert '我是 尼娜' in conv.system_prompt
        # 关键：bug 修复后，自定义 prompt 不应残留 'MaxAgent' 自我介绍
        assert '我是 MaxAgent，3ds Max 的智能助手' not in conv.system_prompt

    def test_persisted_prompt_survives_manager_reload(self, sessions_dir):
        # 模拟用户重启 Max：新 SessionManager 实例读盘后 prompt 仍正确
        from maxagent.agent.conversation import build_default_system_prompt
        mgr = SessionManager(base_dir=sessions_dir)
        custom = build_default_system_prompt('尼娜')
        meta = mgr.create_session(system_prompt=custom)

        mgr2 = SessionManager(base_dir=sessions_dir)
        result = mgr2.load(meta.sid)
        assert result is not None
        _, conv = result
        assert conv.system_prompt == custom

    def test_empty_session_can_have_prompt_overwritten(self, sessions_dir):
        # 模拟"用户改员工名后切回空会话"的场景：
        # dock_widget 在 _load_session 里会覆写空会话的 system_prompt。
        # 这里验证 conversation.system_prompt 是可赋值字段且能被 save 正确持久化。
        from maxagent.agent.conversation import build_default_system_prompt
        mgr = SessionManager(base_dir=sessions_dir)
        meta = mgr.create_session()  # 默认 prompt
        # 加载、改 prompt、保存
        result = mgr.load(meta.sid)
        assert result is not None
        meta2, conv = result
        assert len(conv) == 0  # 确认是空会话
        new_prompt = build_default_system_prompt('尼娜')
        conv.system_prompt = new_prompt
        mgr.save(meta2, conv)
        # 重新加载验证
        result2 = mgr.load(meta.sid)
        assert result2 is not None
        _, conv2 = result2
        assert conv2.system_prompt == new_prompt