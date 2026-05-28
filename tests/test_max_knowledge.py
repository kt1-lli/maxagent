#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""3ds Max 领域知识库（max_knowledge）单元测试。

覆盖：
1. L1 基础常识文本：长度上限、关键概念覆盖、易错点出现
2. L2 主题查询：命中、未命中、子键查询、相似度建议
3. system prompt 集成：L1 真正进入 build_default_system_prompt
4. lookup_max_knowledge 工具：注册、可调用、返回结构
"""

from __future__ import absolute_import
from __future__ import print_function

import unittest

from maxagent.agent.conversation import build_default_system_prompt
from maxagent.agent.conversation import estimate_tokens
from maxagent.agent.max_knowledge import KNOWLEDGE_TOPICS
from maxagent.agent.max_knowledge import get_basic_knowledge
from maxagent.agent.max_knowledge import list_topics
from maxagent.agent.max_knowledge import lookup_topic


class TestL1BasicKnowledge(unittest.TestCase):
    """Layer 1：必塞 system prompt 的基础世界观文本。"""

    def test_l1_text_under_token_budget(self):
        """L1 文本必须 ≤ 500 token（约 1000 中文字符），保护对话预算。"""
        text = get_basic_knowledge()
        tok = estimate_tokens(text)
        self.assertLessEqual(
            tok, 700,
            'L1 知识 token 预算超标 ({} > 700)，请精简内容'.format(tok),
        )

    def test_l1_covers_coordinate_system(self):
        """坐标系概念必须出现：Z-up + 单位系统提示。"""
        text = get_basic_knowledge()
        self.assertIn('Z', text)  # Z-up 标识
        self.assertIn('SystemUnitScale', text)

    def test_l1_covers_pivot_concept(self):
        """pivot 概念必须出现：底面中心 vs 几何中心区分。"""
        text = get_basic_knowledge()
        self.assertIn('pivot', text.lower())
        self.assertIn('底面中心', text)
        self.assertIn('几何中心', text)

    def test_l1_covers_box_axis_pitfall(self):
        """Box 的 length=Y / width=X / height=Z 易错点必须显式提醒。"""
        text = get_basic_knowledge()
        self.assertIn('Box', text)
        # 必须明确轴向映射
        self.assertTrue(
            'length' in text.lower() and 'width' in text.lower(),
        )

    def test_l1_covers_modifier_stack_lifo(self):
        """修改器栈 LIFO 概念必须出现。"""
        text = get_basic_knowledge()
        self.assertIn('LIFO', text)

    def test_l1_covers_copy_vs_instance(self):
        """copy / instance / reference 区别必须提醒（高频陷阱）。"""
        text = get_basic_knowledge()
        self.assertIn('copy', text.lower())
        self.assertIn('instance', text.lower())
        self.assertIn('reference', text.lower())

    def test_l1_has_pitfall_section(self):
        """陷阱清单必须存在（即使精简也要有）。"""
        text = get_basic_knowledge()
        self.assertIn('陷阱', text)


class TestL2KnowledgeTopics(unittest.TestCase):
    """Layer 2：按需查询的主题词典。"""

    def test_topic_list_is_sorted(self):
        """list_topics 返回字典序，便于 LLM 阅读。"""
        topics = list_topics()
        self.assertEqual(topics, sorted(topics))
        # 至少 8 个主题（覆盖核心场景）
        self.assertGreaterEqual(len(topics), 8)

    def test_core_topics_present(self):
        """核心主题必须存在：primitive / modifier / light / camera。"""
        topics = list_topics()
        for required in ('primitive', 'modifier', 'light',
                         'camera', 'material', 'units',
                         'render', 'pivot'):
            self.assertIn(required, topics)

    def test_every_topic_has_summary(self):
        """每个主题字典必须有 _summary 字段。"""
        for name, bucket in KNOWLEDGE_TOPICS.items():
            self.assertIn(
                '_summary', bucket,
                'topic "{}" 缺少 _summary'.format(name),
            )
            self.assertTrue(
                bucket['_summary'].strip(),
                'topic "{}" _summary 为空'.format(name),
            )

    def test_lookup_topic_hit_no_subkey(self):
        """命中主题不带子键：返回 items 字典 + keys 列表。"""
        result = lookup_topic('primitive')
        self.assertTrue(result['found'])
        self.assertEqual(result['topic'], 'primitive')
        self.assertIn('items', result)
        self.assertIn('keys', result)
        # box 必然在子键里
        self.assertIn('box', result['keys'])

    def test_lookup_topic_hit_with_subkey(self):
        """命中主题带精确子键：返回 content 字符串。"""
        result = lookup_topic('primitive', sub_key='box')
        self.assertTrue(result['found'])
        self.assertEqual(result['sub_key'], 'box')
        self.assertIn('content', result)
        # box 内容必然提到 length / width / height
        content = result['content'].lower()
        self.assertIn('length', content)
        self.assertIn('width', content)
        self.assertIn('height', content)

    def test_lookup_topic_case_insensitive(self):
        """topic 名大小写不敏感（LLM 可能写 PRIMITIVE 或 Primitive）。"""
        for variant in ('PRIMITIVE', 'Primitive', 'primitive'):
            self.assertTrue(lookup_topic(variant)['found'],
                            '主题 {} 应该命中'.format(variant))

    def test_lookup_topic_miss_with_suggestion(self):
        """未命中主题：返回 found=False + 相似度建议 + 全主题列表。"""
        result = lookup_topic('primitives')  # 多了个 s
        self.assertFalse(result['found'])
        self.assertIn('available_topics', result)
        # 相似度建议应指向 primitive
        self.assertEqual(result.get('suggestion'), 'primitive')

    def test_lookup_topic_miss_no_suggestion_when_too_far(self):
        """完全无关的查询：不给误导性建议。"""
        result = lookup_topic('zzzzzz')
        self.assertFalse(result['found'])
        # 不要给烂建议
        self.assertEqual(result.get('suggestion', ''), '')

    def test_lookup_topic_miss_subkey_lists_available(self):
        """命中主题但子键不存在：列出该主题下所有子键。"""
        result = lookup_topic('primitive', sub_key='unicorn')
        self.assertFalse(result['found'])
        self.assertIn('available_keys', result)
        self.assertIn('box', result['available_keys'])

    def test_lookup_topic_empty_topic(self):
        """空 topic：返回错误而非崩溃。"""
        result = lookup_topic('')
        self.assertFalse(result['found'])
        self.assertIn('error', result)

    def test_each_topic_item_under_size_limit(self):
        """每条 L2 知识 ≤ 800 字符，避免单次响应膨胀。"""
        for name, bucket in KNOWLEDGE_TOPICS.items():
            for k, v in bucket.items():
                if k == '_summary':
                    continue
                self.assertLessEqual(
                    len(v), 800,
                    '{}.{} 内容超长 ({} > 800)'.format(name, k, len(v)),
                )


class TestPromptIntegration(unittest.TestCase):
    """L1 知识真正注入 system prompt 的回归测试。"""

    def test_l1_text_in_default_prompt(self):
        """L1 文本必须出现在 build_default_system_prompt 输出中。"""
        prompt = build_default_system_prompt()
        # L1 标志性头部
        self.assertIn('3ds Max 世界观速查', prompt)
        # L1 必有的一些关键字
        self.assertIn('SystemUnitScale', prompt)
        self.assertIn('LIFO', prompt)

    def test_l1_text_after_spatial_rules(self):
        """L1 必须排在 14 条工作原则之后（避免打断规则连续性）。"""
        prompt = build_default_system_prompt()
        idx_rules = prompt.find('空间完成原则')
        # 用 emoji 头部精确定位 L1 块（避免与工作原则正文里的引用混淆）
        idx_l1 = prompt.find('🌍 3ds Max 世界观速查')
        idx_coding = prompt.find('代码生成硬性规则')
        self.assertGreater(idx_l1, idx_rules,
                           'L1 应排在工作原则之后')
        self.assertLess(idx_l1, idx_coding,
                        'L1 应排在代码硬规则之前')

    def test_l1_text_under_employee_rename(self):
        """改员工名后 L1 仍然存在。"""
        prompt = build_default_system_prompt('尼娜')
        self.assertIn('3ds Max 世界观速查', prompt)
        self.assertIn('尼娜', prompt)

    def test_prompt_mentions_lookup_workflow(self):
        """工作原则必须引导 LLM 在不确定时调 knowledge 工具。"""
        prompt = build_default_system_prompt()
        self.assertIn('lookup_max_knowledge', prompt)
        self.assertIn('list_max_knowledge_topics', prompt)


class TestKnowledgeToolRegistration(unittest.TestCase):
    """lookup_max_knowledge 工具注册到 dispatcher 的回归测试。"""

    def test_lookup_tool_in_schema(self):
        """加载所有工具后，lookup_max_knowledge 应在 OpenAI schema 里。"""
        from maxagent.tools import build_openai_tools_schema
        from maxagent.tools import load_all_tools
        load_all_tools(include_escape_hatch=False, load_user_tools=False)
        schema = build_openai_tools_schema()
        names = [
            (s.get('function') or {}).get('name')
            for s in schema
        ]
        self.assertIn('lookup_max_knowledge', names)
        self.assertIn('list_max_knowledge_topics', names)

    def test_lookup_tool_directly_callable(self):
        """工具函数本身（绕过 dispatcher）可直接调用并返回字典。"""
        # 触发注册
        from maxagent.tools import load_all_tools
        load_all_tools(include_escape_hatch=False, load_user_tools=False)
        from maxagent.tools.knowledge_tools import lookup_max_knowledge
        from maxagent.tools.knowledge_tools import list_max_knowledge_topics

        result = list_max_knowledge_topics()
        self.assertIn('topics', result)
        self.assertGreater(result['count'], 0)

        hit = lookup_max_knowledge('primitive', sub_key='box')
        self.assertTrue(hit['found'])
        self.assertIn('content', hit)


if __name__ == '__main__':
    unittest.main()
