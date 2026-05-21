#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试 Conversation：增删消息、token 估算、tool 配对裁剪、重启注入、摘要替换。"""

from __future__ import absolute_import
from __future__ import print_function

from maxagent.agent.conversation import (
    CHARS_PER_TOKEN,
    Conversation,
    Message,
    estimate_tokens,
)


class TestEstimateTokens:
    def test_empty(self):
        assert estimate_tokens('') == 0
        assert estimate_tokens(None) == 0

    def test_simple(self):
        # 10 字符 / 2.5 + 1 = 5
        n = estimate_tokens('a' * 10)
        assert n >= 4

    def test_long_text(self):
        n = estimate_tokens('x' * 1000)
        assert n > 100


class TestConversationBasic:
    def test_add_user_message(self):
        c = Conversation()
        c.add_user('hello')
        assert len(c) == 1
        assert c.messages[0].role == 'user'
        assert c.messages[0].content == 'hello'

    def test_add_assistant_with_tool_calls(self):
        c = Conversation()
        c.add_assistant(
            content=None,
            tool_calls=[{
                'id': 'call_1',
                'type': 'function',
                'function': {'name': 't', 'arguments': '{}'},
            }],
        )
        assert c.messages[0].tool_calls[0]['id'] == 'call_1'

    def test_add_tool_result(self):
        c = Conversation()
        c.add_tool_result(
            tool_call_id='call_1',
            name='t',
            content='{"ok": true}',
        )
        m = c.messages[0]
        assert m.role == 'tool'
        assert m.tool_call_id == 'call_1'

    def test_clear(self):
        c = Conversation()
        c.add_user('a')
        c.add_user('b')
        c.clear()
        assert len(c) == 0

    def test_to_openai_messages_includes_system(self):
        c = Conversation()
        c.add_user('hi')
        msgs = c.to_openai_messages()
        assert msgs[0]['role'] == 'system'
        assert msgs[1]['role'] == 'user'

    def test_serialize_roundtrip(self):
        c = Conversation()
        c.add_user('hi')
        c.add_assistant(content='hello')
        data = c.to_json()
        c2 = Conversation.from_json(data)
        assert len(c2) == len(c)
        assert c2.messages[0].content == 'hi'
        assert c2.messages[1].content == 'hello'


class TestTrimToTokenBudget:
    def _build_conv_with_size(self, msg_count, content_len=200):
        c = Conversation()
        for i in range(msg_count):
            c.add_user('U{}: {}'.format(i, 'x' * content_len))
            c.add_assistant(content='A{}: {}'.format(i, 'y' * content_len))
        return c

    def test_no_trim_under_budget(self):
        c = self._build_conv_with_size(2, 50)
        cut = c.trim_to_token_budget(max_tokens=100000, keep_recent=4)
        assert cut == 0
        assert len(c) == 4

    def test_trim_over_budget(self):
        c = self._build_conv_with_size(20, 200)
        before = len(c)
        cut = c.trim_to_token_budget(max_tokens=500, keep_recent=4)
        assert cut > 0
        assert len(c) < before
        # 至少保护最近 4 条
        assert len(c) >= 4

    def test_protects_recent(self):
        c = self._build_conv_with_size(20, 100)
        # 最后一条的内容
        last_content = c.messages[-1].content
        c.trim_to_token_budget(max_tokens=300, keep_recent=4)
        assert c.messages[-1].content == last_content

    def test_tool_pair_integrity(self):
        """裁剪不能把 assistant(tool_calls) 和它的 tool 结果拆开。"""
        c = Conversation()
        # 制造大量配对
        for i in range(15):
            c.add_user('U{}'.format(i))
            c.add_assistant(
                content=None,
                tool_calls=[{
                    'id': 'call_{}'.format(i),
                    'type': 'function',
                    'function': {
                        'name': 'noop',
                        'arguments': 'X' * 200,  # 让消息变重
                    },
                }],
            )
            c.add_tool_result(
                tool_call_id='call_{}'.format(i),
                name='noop',
                content='Y' * 200,
            )
            c.add_assistant(content='final {}'.format(i))

        # 裁剪到一个相对小的预算
        c.trim_to_token_budget(max_tokens=2000, keep_recent=4)

        # 任何剩余的 tool 消息都必须能找到对应的 assistant tool_calls
        seen_call_ids = set()
        for m in c.messages:
            if m.role == 'assistant' and m.tool_calls:
                for tc in m.tool_calls:
                    seen_call_ids.add(tc.get('id'))
        for m in c.messages:
            if m.role == 'tool':
                assert m.tool_call_id in seen_call_ids, (
                    'tool {} 的 assistant 配对被裁掉了'.format(m.tool_call_id)
                )


class TestRestoredNotice:
    def test_inject_into_non_empty(self):
        c = Conversation()
        c.add_user('hi')
        injected = c.inject_restored_notice()
        assert injected is True
        assert c.has_restored_marker()
        assert c.messages[0].role == 'system'
        assert '__maxagent_restored__' in c.messages[0].content

    def test_idempotent(self):
        c = Conversation()
        c.add_user('hi')
        assert c.inject_restored_notice() is True
        # 再注入应该被识别并跳过
        assert c.inject_restored_notice() is False
        # 总条数：原 1 条 + 1 条 system = 2
        assert len(c) == 2

    def test_empty_conv_no_inject(self):
        c = Conversation()
        assert c.inject_restored_notice() is False
        assert not c.has_restored_marker()


class TestReplaceWithSummary:
    def test_basic_compression(self):
        c = Conversation()
        for i in range(8):
            c.add_user('msg{}'.format(i))
            c.add_assistant(content='reply{}'.format(i))
        ok, removed = c.replace_with_summary(
            'Summary of early talk.', keep_recent=2,
        )
        assert ok is True
        assert removed > 0
        # 第一条应是 summary system note
        assert c.messages[0].role == 'system'
        assert '__maxagent_summary__' in c.messages[0].content
        # 最后 keep_recent 条保留
        assert c.messages[-1].content == 'reply7'

    def test_too_short_no_compress(self):
        c = Conversation()
        c.add_user('hi')
        ok, removed = c.replace_with_summary('s', keep_recent=2)
        assert ok is False
        assert removed == 0

    def test_protects_tool_pair_at_boundary(self):
        """如果保留区起点正好落在 tool_call 配对中间，应往前扩展保护整组。"""
        c = Conversation()
        # 早期普通消息
        for i in range(4):
            c.add_user('U{}'.format(i))
            c.add_assistant(content='A{}'.format(i))
        # 边界处插一个 tool 配对
        c.add_assistant(
            content=None,
            tool_calls=[{
                'id': 'cx',
                'type': 'function',
                'function': {'name': 'n', 'arguments': '{}'},
            }],
        )
        c.add_tool_result(tool_call_id='cx', name='n', content='ok')
        c.add_assistant(content='wrap up')

        ok, _removed = c.replace_with_summary('S', keep_recent=2)
        assert ok is True
        # 任何保留的 tool 必须有配对的 assistant
        seen = set()
        for m in c.messages:
            if m.role == 'assistant' and m.tool_calls:
                for tc in m.tool_calls:
                    seen.add(tc.get('id'))
        for m in c.messages:
            if m.role == 'tool':
                assert m.tool_call_id in seen


class TestSystemPromptRules:
    """验证硬规则与身份提示已正确写入 DEFAULT_SYSTEM_PROMPT。

    每条规则都对应用户线上反馈过的具体问题，回归时一旦被人误删能立刻发现。
    """

    def _prompt(self):
        from maxagent.agent.conversation import DEFAULT_SYSTEM_PROMPT
        return DEFAULT_SYSTEM_PROMPT

    def test_identity_lockdown(self):
        # #4：问"你是谁"必须答 MaxAgent，不能透露底层模型
        prompt = self._prompt()
        assert 'MaxAgent' in prompt
        assert '身份铁律' in prompt
        # 必须明确禁止透露底层 LLM 厂商
        assert '严禁透露' in prompt or '严禁说出' in prompt

    def test_anti_hallucination_rule(self):
        # #1：禁止捏造 MaxScript / pymxs API
        prompt = self._prompt()
        assert '反幻觉' in prompt
        assert '严禁捏造' in prompt

    def test_local_scope_rule(self):
        # #2：local 只在所属括号内生效
        prompt = self._prompt()
        assert 'local 作用域铁律' in prompt
        assert '括号' in prompt

    def test_if_then_else_rule(self):
        # #3：else 必须配 then；if-do 不能带 else
        prompt = self._prompt()
        assert 'then' in prompt and 'else' in prompt
        # 应当含有"then 关键字不可省略"或等价禁止文案
        assert '不可省略' in prompt or '禁止' in prompt

    def test_rules_are_appended_after_workflow(self):
        # 确认硬规则确实被拼接到 prompt 末尾，而不是被截断
        from maxagent.agent.coding_rules import CODING_RULES
        prompt = self._prompt()
        # 抽 CODING_RULES 的标志性子串验证
        assert '代码生成硬性规则' in prompt
        assert CODING_RULES.strip() in prompt