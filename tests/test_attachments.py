#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试图片附件 / 多模态消息组装。

覆盖：
- save_image_bytes / save_image_file 落盘 + 元数据
- Attachment.to_data_uri 编码
- model_supports_vision 白名单匹配
- build_user_content 视觉/降级两条路径
- Conversation 持久化 round-trip 保留 attachments
- AgentWorker._apply_attachments 视觉 + 降级
"""

from __future__ import absolute_import
from __future__ import print_function

import os

import pytest


@pytest.fixture
def tmp_config_dir(monkeypatch, tmp_path):
    """把 config_dir 重定向到临时目录，避免污染真实路径。"""
    # 直接打补丁覆盖 get_config_dir 的所有引用点
    import maxagent.config as cfg_mod
    import maxagent.attachments as att_mod
    fake_dir = str(tmp_path)
    monkeypatch.setattr(cfg_mod, 'get_config_dir', lambda: fake_dir)
    monkeypatch.setattr(att_mod, 'get_config_dir', lambda: fake_dir)
    return tmp_path


def _png_bytes():
    """构造一个最小合法 PNG（1x1 像素），用于测试落盘。"""
    # 16 字节 PNG 不合法，这里用真实 1x1 透明 PNG 的最短字节流
    return bytes([
        0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A,
        0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52,
        0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,
        0x08, 0x06, 0x00, 0x00, 0x00, 0x1F, 0x15, 0xC4,
        0x89, 0x00, 0x00, 0x00, 0x0D, 0x49, 0x44, 0x41,
        0x54, 0x78, 0x9C, 0x62, 0x00, 0x01, 0x00, 0x00,
        0x05, 0x00, 0x01, 0x0D, 0x0A, 0x2D, 0xB4, 0x00,
        0x00, 0x00, 0x00, 0x49, 0x45, 0x4E, 0x44, 0xAE,
        0x42, 0x60, 0x82,
    ])


# ---------------------------------------------------------------------- #
# Attachment 落盘 / 编码
# ---------------------------------------------------------------------- #
class TestSaveAndLoad(object):

    def test_save_image_bytes_creates_file(self, tmp_config_dir):
        from maxagent import attachments as att_mod
        att = att_mod.save_image_bytes(_png_bytes(), mime='image/png',
                                       name='hello')
        assert att is not None
        assert att.exists()
        assert att.size == len(_png_bytes())
        assert att.mime == 'image/png'
        assert att.name == 'hello'
        # 落盘路径在 attachments 目录下
        assert 'attachments' in att.path

    def test_save_image_file_copies_source(self, tmp_config_dir, tmp_path):
        from maxagent import attachments as att_mod
        src = tmp_path / 'src.png'
        src.write_bytes(_png_bytes())
        att = att_mod.save_image_file(str(src))
        assert att is not None
        # 不应该等于源路径——必须复制
        assert att.path != str(src)
        assert att.exists()
        # 删除源文件后副本仍然可读
        os.remove(str(src))
        assert att.exists()

    def test_save_oversize_rejected(self, tmp_config_dir):
        from maxagent import attachments as att_mod
        # 构造超限数据
        big = b'x' * (att_mod.MAX_IMAGE_BYTES + 1)
        result = att_mod.save_image_bytes(big)
        assert result is None

    def test_to_data_uri_round_trip(self, tmp_config_dir):
        from maxagent import attachments as att_mod
        att = att_mod.save_image_bytes(_png_bytes(), mime='image/png')
        uri = att.to_data_uri()
        assert uri is not None
        assert uri.startswith('data:image/png;base64,')
        # base64 内容非空
        b64 = uri.split('base64,', 1)[1]
        assert len(b64) > 10

    def test_to_data_uri_missing_file_returns_none(self, tmp_config_dir):
        from maxagent.attachments import Attachment
        att = Attachment(
            kind='image',
            path='/nonexistent/path.png',
            mime='image/png',
            size=100,
        )
        assert att.to_data_uri() is None


# ---------------------------------------------------------------------- #
# 视觉能力探测
# ---------------------------------------------------------------------- #
class TestVisionDetection(object):

    def test_whitelist_substring_match(self):
        from maxagent.attachments import model_supports_vision
        wl = ['gpt-4o', 'claude-3', 'qwen-vl']
        assert model_supports_vision('gpt-4o', wl)
        assert model_supports_vision('GPT-4O', wl)  # 大小写不敏感
        assert model_supports_vision('gpt-4o-2024-08-06', wl)  # 子串
        assert model_supports_vision('claude-3-sonnet', wl)
        assert not model_supports_vision('deepseek-chat', wl)
        assert not model_supports_vision('', wl)

    def test_empty_whitelist_disables_all(self):
        from maxagent.attachments import model_supports_vision
        assert not model_supports_vision('gpt-4o', [])
        assert not model_supports_vision('gpt-4o', None)


# ---------------------------------------------------------------------- #
# build_user_content：多模态打包 / 纯文本降级
# ---------------------------------------------------------------------- #
class TestBuildUserContent(object):

    def test_no_attachments_returns_plain_text(self, tmp_config_dir):
        from maxagent.attachments import build_user_content
        out = build_user_content('hello', None, can_vision=True)
        assert out == 'hello'
        out2 = build_user_content('hello', [], can_vision=False)
        assert out2 == 'hello'

    def test_vision_on_returns_list(self, tmp_config_dir):
        from maxagent import attachments as att_mod
        att = att_mod.save_image_bytes(_png_bytes())
        out = att_mod.build_user_content(
            '看这张图', [att], can_vision=True,
        )
        assert isinstance(out, list)
        # 至少含一个 text 段和一个 image_url 段
        kinds = [p.get('type') for p in out]
        assert 'text' in kinds
        assert 'image_url' in kinds
        # image_url.url 必须是 data URI
        for p in out:
            if p.get('type') == 'image_url':
                assert p['image_url']['url'].startswith(
                    'data:image/png;base64,',
                )

    def test_vision_off_degrades_to_text_with_notice(self, tmp_config_dir):
        from maxagent import attachments as att_mod
        att = att_mod.save_image_bytes(_png_bytes())
        out = att_mod.build_user_content(
            '看图', [att], can_vision=False,
        )
        assert isinstance(out, str)
        assert '看图' in out
        assert '图片' in out
        assert '不支持' in out

    def test_empty_text_with_image_still_has_text_segment(
            self, tmp_config_dir):
        # OpenAI 要求 user 消息至少有 text 段，否则会拒
        from maxagent import attachments as att_mod
        att = att_mod.save_image_bytes(_png_bytes())
        out = att_mod.build_user_content('', [att], can_vision=True)
        assert isinstance(out, list)
        text_segs = [p for p in out if p.get('type') == 'text']
        assert len(text_segs) >= 1


# ---------------------------------------------------------------------- #
# Conversation 持久化 round-trip
# ---------------------------------------------------------------------- #
class TestConversationAttachments(object):

    def test_add_user_with_attachments(self, tmp_config_dir):
        from maxagent.agent.conversation import Conversation
        from maxagent import attachments as att_mod
        conv = Conversation()
        att = att_mod.save_image_bytes(_png_bytes())
        msg = conv.add_user('看图', attachments=[att])
        assert msg.attachments == [att]
        assert msg.content == '看图'

    def test_round_trip_preserves_attachment_meta(self, tmp_config_dir):
        from maxagent.agent.conversation import Conversation
        from maxagent import attachments as att_mod
        conv = Conversation()
        att = att_mod.save_image_bytes(_png_bytes(), name='snip.png')
        conv.add_user('test', attachments=[att])
        data = conv.to_json()
        # JSON 里含 attachments 字段
        msg_data = data['messages'][0]
        assert 'attachments' in msg_data
        assert msg_data['attachments'][0]['name'] == 'snip.png'
        # 反序列化恢复
        conv2 = Conversation.from_json(data)
        msg2 = conv2.messages[0]
        assert len(msg2.attachments) == 1
        assert msg2.attachments[0].path == att.path

    def test_round_trip_no_attachments_is_compact(self, tmp_config_dir):
        # 无附件时不应该带 attachments 字段，保持兼容老格式
        from maxagent.agent.conversation import Conversation
        conv = Conversation()
        conv.add_user('hi')
        data = conv.to_json()
        assert 'attachments' not in data['messages'][0]


# ---------------------------------------------------------------------- #
# AgentWorker._apply_attachments
# ---------------------------------------------------------------------- #
class TestWorkerApplyAttachments(object):

    def _make_worker(self, vision=True, model='gpt-4o',
                     whitelist=('gpt-4o', 'claude-3')):
        """构造一个最小可用的 worker，用最少 mock 跑 _apply_attachments。"""
        from maxagent.agent.conversation import Conversation
        from maxagent.agent.worker import AgentWorker

        class FakeLLM(object):
            def __init__(self, m):
                self._model = m

        class FakeDispatcher(object):
            pass

        conv = Conversation()
        w = AgentWorker(
            llm_client=FakeLLM(model),
            conversation=conv,
            dispatcher=FakeDispatcher(),
            vision_enabled=vision,
            vision_whitelist=list(whitelist),
        )
        return w, conv

    def test_no_attachments_passthrough(self, tmp_config_dir):
        # 非视觉模型 + 无附件 + 无历史附件 → 直接透传
        w, conv = self._make_worker(model='deepseek-chat',
                                    whitelist=('claude-3',))
        conv.add_user('hi')
        msgs = conv.to_openai_messages()
        out = w._apply_attachments(msgs)  # noqa: SLF001
        assert out == msgs

    def test_vision_no_attachments_force_multimodal(self, tmp_config_dir):
        # 视觉模型 + 无附件 → user 仍被包成 list[text]（格式统一）
        w, conv = self._make_worker(model='gpt-4o-mini')
        conv.add_user('hi')
        msgs = conv.to_openai_messages()
        out = w._apply_attachments(msgs)  # noqa: SLF001
        user_msg = next(m for m in out if m.get('role') == 'user')
        assert isinstance(user_msg['content'], list)
        assert user_msg['content'][0]['type'] == 'text'

    def test_vision_model_rewrites_to_list(self, tmp_config_dir):
        from maxagent import attachments as att_mod
        w, conv = self._make_worker(model='gpt-4o-mini')
        att = att_mod.save_image_bytes(_png_bytes())
        conv.add_user('看图', attachments=[att])
        msgs = conv.to_openai_messages()
        out = w._apply_attachments(msgs)  # noqa: SLF001
        # user 消息的 content 已变成 list
        user_msg = next(m for m in out if m.get('role') == 'user')
        assert isinstance(user_msg['content'], list)

    def test_non_vision_model_degrades(self, tmp_config_dir):
        from maxagent import attachments as att_mod
        w, conv = self._make_worker(model='deepseek-chat')
        att = att_mod.save_image_bytes(_png_bytes())
        conv.add_user('看图', attachments=[att])
        msgs = conv.to_openai_messages()
        out = w._apply_attachments(msgs)  # noqa: SLF001
        user_msg = next(m for m in out if m.get('role') == 'user')
        assert isinstance(user_msg['content'], str)
        assert '不支持' in user_msg['content']

    def test_vision_disabled_globally(self, tmp_config_dir):
        from maxagent import attachments as att_mod
        # 即使 model 是 gpt-4o，vision_enabled=False 也降级
        w, conv = self._make_worker(vision=False, model='gpt-4o')
        att = att_mod.save_image_bytes(_png_bytes())
        conv.add_user('看图', attachments=[att])
        msgs = conv.to_openai_messages()
        out = w._apply_attachments(msgs)  # noqa: SLF001
        user_msg = next(m for m in out if m.get('role') == 'user')
        assert isinstance(user_msg['content'], str)


# ---------------------------------------------------------------------- #
# AppConfig: vision 字段 round-trip
# ---------------------------------------------------------------------- #
class TestConfigVisionFields(object):

    def test_default_values(self):
        from maxagent.config import AppConfig
        cfg = AppConfig()
        assert cfg.vision_enabled is True
        assert isinstance(cfg.vision_model_whitelist, list)
        assert any('gpt-4o' in w for w in cfg.vision_model_whitelist)

    def test_round_trip(self):
        from maxagent.config import AppConfig
        cfg = AppConfig()
        cfg.vision_enabled = False
        cfg.vision_model_whitelist = ['custom-vision']
        data = cfg.to_dict()
        assert data['vision_enabled'] is False
        assert data['vision_model_whitelist'] == ['custom-vision']
        cfg2 = AppConfig.from_dict(data)
        assert cfg2.vision_enabled is False
        assert cfg2.vision_model_whitelist == ['custom-vision']

    def test_legacy_config_uses_defaults(self):
        # 老配置文件没有 vision 字段，应当走默认值（不报错）
        from maxagent.config import AppConfig
        cfg = AppConfig.from_dict({
            'version': 1,
            'profiles': [],
            'active_profile': 'X',
        })
        assert cfg.vision_enabled is True
        assert len(cfg.vision_model_whitelist) > 0


# ---------------------------------------------------------------------- #
# build_user_content：多轮历史瘦身 + 强制 multimodal
# ---------------------------------------------------------------------- #
class TestBuildUserContentMultiTurn(object):

    def test_keep_images_false_drops_image_url(self, tmp_config_dir):
        # 视觉模型 + keep_images=False：返回 list 形态但仅保留 text 段
        from maxagent import attachments as att_mod
        att = att_mod.save_image_bytes(_png_bytes())
        out = att_mod.build_user_content(
            '识别图中人物', [att],
            can_vision=True, keep_images=False,
        )
        assert isinstance(out, list)
        kinds = [p.get('type') for p in out]
        assert 'image_url' not in kinds
        assert 'text' in kinds
        # 文本里要有占位提示，让模型知道历史曾有图
        text_seg = next(p for p in out if p.get('type') == 'text')
        assert '识别图中人物' in text_seg['text']
        assert '图片' in text_seg['text']

    def test_force_multimodal_wraps_plain_text(self, tmp_config_dir):
        # 视觉路径下，纯文本也包成 list[text]——格式严格统一
        from maxagent.attachments import build_user_content
        out = build_user_content(
            '你可以干什么', None,
            can_vision=True, force_multimodal=True,
        )
        assert isinstance(out, list)
        assert len(out) == 1
        assert out[0]['type'] == 'text'
        assert out[0]['text'] == '你可以干什么'

    def test_force_multimodal_empty_text_uses_placeholder(self, tmp_config_dir):
        # vita 等网关会拒绝空 content，空文本兜底为单空格
        from maxagent.attachments import build_user_content
        out = build_user_content(
            '', None,
            can_vision=True, force_multimodal=True,
        )
        assert isinstance(out, list)
        assert out[0]['type'] == 'text'
        assert out[0]['text']  # 非空字符串

    def test_force_multimodal_off_keeps_string(self, tmp_config_dir):
        # 默认 force_multimodal=False：纯文本仍是字符串（向后兼容）
        from maxagent.attachments import build_user_content
        out = build_user_content('hello', None, can_vision=True)
        assert out == 'hello'

    def test_keep_images_default_true_back_compat(self, tmp_config_dir):
        # 不传 keep_images 时默认保留图片，行为与原版一致
        from maxagent import attachments as att_mod
        att = att_mod.save_image_bytes(_png_bytes())
        out = att_mod.build_user_content('看图', [att], can_vision=True)
        assert isinstance(out, list)
        kinds = [p.get('type') for p in out]
        assert 'image_url' in kinds


# ---------------------------------------------------------------------- #
# AgentWorker._apply_attachments 多轮稳定性
# ---------------------------------------------------------------------- #
class TestWorkerMultiTurnStability(object):

    def _make_worker(self, vision=True, model='youtu-vita',
                     whitelist=('vita', 'gpt-4o')):
        from maxagent.agent.conversation import Conversation
        from maxagent.agent.worker import AgentWorker

        class FakeLLM(object):
            def __init__(self, m):
                self._model = m

        class FakeDispatcher(object):
            pass

        conv = Conversation()
        w = AgentWorker(
            llm_client=FakeLLM(model),
            conversation=conv,
            dispatcher=FakeDispatcher(),
            vision_enabled=vision,
            vision_whitelist=list(whitelist),
        )
        return w, conv

    def test_only_last_user_keeps_image_url(self, tmp_config_dir):
        # 模拟现实场景：第一轮发图 → assistant 回 → 第二轮纯文本
        # 期望：第一轮 user 的图片被剥离为占位文本，第二轮 user 是纯文本
        from maxagent import attachments as att_mod
        w, conv = self._make_worker()
        att = att_mod.save_image_bytes(_png_bytes())
        conv.add_user('识别图中人物', attachments=[att])
        conv.add_assistant(content='图中是佐仓千代')
        conv.add_user('你可以干什么')
        msgs = conv.to_openai_messages()
        out = w._apply_attachments(msgs)  # noqa: SLF001

        users = [m for m in out if m.get('role') == 'user']
        assert len(users) == 2
        # 第一轮 user：list 但无 image_url
        first = users[0]
        assert isinstance(first['content'], list)
        first_kinds = [p.get('type') for p in first['content']]
        assert 'image_url' not in first_kinds
        assert 'text' in first_kinds
        # 第二轮 user：list[text]（force_multimodal）
        second = users[1]
        assert isinstance(second['content'], list)
        assert second['content'][0]['type'] == 'text'
        assert second['content'][0]['text'] == '你可以干什么'

    def test_two_image_turns_only_last_keeps_full(self, tmp_config_dir):
        # 两轮都带图：只有最后一轮保留 image_url
        from maxagent import attachments as att_mod
        w, conv = self._make_worker()
        a1 = att_mod.save_image_bytes(_png_bytes())
        a2 = att_mod.save_image_bytes(_png_bytes())
        conv.add_user('图1', attachments=[a1])
        conv.add_assistant(content='ok1')
        conv.add_user('图2', attachments=[a2])
        msgs = conv.to_openai_messages()
        out = w._apply_attachments(msgs)  # noqa: SLF001

        users = [m for m in out if m.get('role') == 'user']
        # 第一轮：无 image_url
        kinds1 = [p.get('type') for p in users[0]['content']]
        assert 'image_url' not in kinds1
        # 第二轮：有 image_url
        kinds2 = [p.get('type') for p in users[1]['content']]
        assert 'image_url' in kinds2

    def test_non_vision_path_unchanged(self, tmp_config_dir):
        # 非视觉模型：应保持原本行为——图片降级为纯文本，纯文本不重写
        from maxagent import attachments as att_mod
        w, conv = self._make_worker(model='deepseek-chat',
                                    whitelist=('gpt-4o',))
        att = att_mod.save_image_bytes(_png_bytes())
        conv.add_user('看图', attachments=[att])
        conv.add_user('再问一句')
        msgs = conv.to_openai_messages()
        out = w._apply_attachments(msgs)  # noqa: SLF001
        users = [m for m in out if m.get('role') == 'user']
        # 第一条：字符串 + 占位提示
        assert isinstance(users[0]['content'], str)
        assert '不支持' in users[0]['content']
        # 第二条：原样字符串（非视觉模型不强制 multimodal）
        assert users[1]['content'] == '再问一句'

    def test_vision_pure_text_no_attachment_history(self, tmp_config_dir):
        # 全程无附件 + 视觉模型：仍走 force_multimodal 包装所有 user
        w, conv = self._make_worker()
        conv.add_user('hi')
        conv.add_assistant(content='hello')
        conv.add_user('again')
        msgs = conv.to_openai_messages()
        out = w._apply_attachments(msgs)  # noqa: SLF001
        users = [m for m in out if m.get('role') == 'user']
        for u in users:
            assert isinstance(u['content'], list)
            assert u['content'][0]['type'] == 'text'
