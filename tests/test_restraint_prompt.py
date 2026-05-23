#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""字面理解铁律 + 工具完成软提示回归测试。

针对用户反馈：「优化提示词工程，现在会乱用工具，例如我创建一个球，
但是创建了球和摄像机和灯光」。

修复策略：
1. system prompt 工作原则增加 8/9/10 三条字面理解铁律
2. worker 在第一批工具执行成功后注入"完成即停"软提示，每轮仅一次
"""

from __future__ import absolute_import
from __future__ import print_function

import unittest
from unittest import mock

from maxagent.agent.conversation import build_default_system_prompt
from maxagent.agent.conversation import Conversation
from maxagent.agent.worker import AgentWorker


class TestRestraintPrompt(unittest.TestCase):
    """system prompt 字面理解铁律相关断言。"""

    def test_prompt_contains_literal_principle_keyword(self):
        """工作原则应明确强调"按字面要求行事，不主动扩展"。"""
        prompt = build_default_system_prompt()
        self.assertIn('字面', prompt)
        # 防止过度联想
        self.assertIn('扩展', prompt)

    def test_prompt_uses_sphere_example(self):
        """应包含具体反例（创建一个球只调 create_sphere）。"""
        prompt = build_default_system_prompt()
        # 反例：球不应触发灯/相机
        self.assertIn('create_sphere', prompt)
        self.assertIn('create_light', prompt)
        self.assertIn('create_camera', prompt)

    def test_prompt_mentions_complete_scene_keywords(self):
        """应明确什么情况才允许多工具组合。"""
        prompt = build_default_system_prompt()
        # 必须列举一些"组合需求"的关键词作为豁免条件
        self.assertTrue(
            '完整场景' in prompt or '打光' in prompt or '产品展示' in prompt,
            'prompt 应说明只有用户明确要求"完整场景/打光"等才允许多工具组合',
        )

    def test_prompt_has_parameter_minimization_rule(self):
        """参数最小化原则：不要主动填可选参数。"""
        prompt = build_default_system_prompt()
        self.assertIn('最小化', prompt)
        # 应明确点名常见可选参数
        self.assertTrue(
            'position' in prompt or 'radius' in prompt or 'wirecolor' in prompt,
            'prompt 应明确不要主动填 position/radius/wirecolor',
        )

    def test_prompt_has_completion_stop_rule(self):
        """完成即停原则：单一明确请求被满足后立即给出确认。"""
        prompt = build_default_system_prompt()
        # 应包含"完成即停"或类似措辞
        self.assertTrue(
            '完成即停' in prompt or '立即给出' in prompt
            or '已为你创建' in prompt,
            'prompt 应明确：单一请求满足后立即给出简短确认回复',
        )

    def test_prompt_employee_name_still_works(self):
        """改名后铁律仍然存在，未被覆盖。"""
        prompt = build_default_system_prompt('尼娜')
        self.assertIn('字面', prompt)
        self.assertIn('完成即停', prompt)
        # 同时身份铁律也保留
        self.assertIn('尼娜', prompt)


class TestHelpDocumentation(unittest.TestCase):
    """帮助页面覆盖新增功能（splitter 锚点 + 字面理解铁律）。"""

    def _help_html(self):
        from maxagent.ui.settings_dialog import SettingsDialog
        return SettingsDialog._help_html()

    def test_help_mentions_splitter_drag_behavior(self):
        html = self._help_html()
        # 必须解释向上/向下拖的区别
        self.assertIn('向下拖', html)
        self.assertIn('向上拖', html)
        # 必须说明翻历史不被打扰
        self.assertTrue(
            '翻历史' in html or '原视点' in html or '原位置' in html,
            '帮助页应说明翻历史时不会被打扰',
        )

    def test_help_mentions_literal_principle(self):
        html = self._help_html()
        # 必须告诉用户"字面理解"特性
        self.assertTrue(
            '字面' in html or '智能克制' in html,
            '帮助页应让用户知道助手会按字面理解，不会过度联想',
        )
        # 必须举例
        self.assertIn('球', html)


class TestRestraintHintInjection(unittest.TestCase):
    """工具完成后软提示注入的单元测试。

    通过 mock LLMClient + Dispatcher，构造一轮"含 tool_calls"的响应，
    验证 worker 在工具执行完成后会向 Conversation 注入一条 system note。
    """

    def _make_worker(self, llm_responses):
        """构造 worker，注入伪 LLM。

        :param llm_responses: list of dict，按顺序作为每次 chat() 的返回
        """
        llm = mock.MagicMock()

        def _chat(**kwargs):
            if not llm_responses:
                return {
                    'content': '',
                    'tool_calls': [],
                    'finish_reason': 'stop',
                    'usage': {},
                }
            return llm_responses.pop(0)

        llm.chat.side_effect = _chat

        dispatcher = mock.MagicMock()
        # 工具执行直接返回 ok
        dispatcher.dispatch.return_value = {'ok': True, 'name': 'Sphere001'}

        conv = Conversation()
        worker = AgentWorker(
            llm_client=llm,
            conversation=conv,
            dispatcher=dispatcher,
            max_tool_loops=5,
            max_history_tokens=0,
        )
        # 注入主线程同步执行器（直接走 dispatcher.dispatch）
        worker.set_sync_tool_runner(
            lambda name, args: dispatcher.dispatch(name, args),
        )
        return worker, conv

    def test_hint_injected_after_first_tool_batch(self):
        """第一批工具执行完成后，conv 中应出现"完成即停"软提示。"""
        # 第一轮：LLM 调用 create_sphere
        # 第二轮：LLM 给出确认回复结束
        responses = [
            {
                'content': '',
                'tool_calls': [
                    {
                        'id': 'call_1',
                        'name': 'create_sphere',
                        'arguments': {'name': 'Sphere001'},
                    },
                ],
                'finish_reason': 'tool_calls',
                'usage': {},
            },
            {
                'content': '已为你创建一个球。',
                'tool_calls': [],
                'finish_reason': 'stop',
                'usage': {},
            },
        ]
        worker, conv = self._make_worker(responses)
        # 同步跑 _run_loop（不开线程，直接调）
        worker._current_user_input = '创建一个球'
        conv.add_user('创建一个球')
        worker._run_loop()

        # 检查 system note 是否被注入
        system_notes = [
            m for m in conv.messages
            if m.role == 'system' and m.content
        ]
        joined = '\n'.join(n.content for n in system_notes)
        self.assertIn('已执行完毕', joined)
        # 必须明确约束扩展行为
        self.assertTrue(
            '不要' in joined and (
                '灯光' in joined or '相机' in joined
                or '未被显式要求' in joined
            ),
            '软提示应约束 LLM 不要追加灯/相机/材质等未被要求的操作',
        )

    def test_hint_injected_only_once_per_turn(self):
        """同一轮多次工具调用，软提示只注入一次。"""
        # 三轮工具调用：球 → 球 → 球（极端模拟过度联想）
        responses = [
            {
                'content': '',
                'tool_calls': [{
                    'id': 'c1', 'name': 'create_sphere', 'arguments': {},
                }],
                'finish_reason': 'tool_calls',
                'usage': {},
            },
            {
                'content': '',
                'tool_calls': [{
                    'id': 'c2', 'name': 'create_light', 'arguments': {},
                }],
                'finish_reason': 'tool_calls',
                'usage': {},
            },
            {
                'content': '',
                'tool_calls': [{
                    'id': 'c3', 'name': 'create_camera', 'arguments': {},
                }],
                'finish_reason': 'tool_calls',
                'usage': {},
            },
            {
                'content': '完成。',
                'tool_calls': [],
                'finish_reason': 'stop',
                'usage': {},
            },
        ]
        worker, conv = self._make_worker(responses)
        conv.add_user('创建一个球')
        worker._run_loop()

        # 数 system note 中含"已执行完毕"的条数
        hint_count = sum(
            1 for m in conv.messages
            if m.role == 'system' and m.content
            and '已执行完毕' in m.content
        )
        self.assertEqual(
            hint_count, 1,
            '同一轮 user_input 中软提示应只注入 1 次，实际 {}'.format(hint_count),
        )

    def test_hint_not_injected_when_no_tool_call(self):
        """如果 LLM 直接回复不调工具，则不应注入软提示。"""
        responses = [
            {
                'content': '你好，请告诉我具体要做什么？',
                'tool_calls': [],
                'finish_reason': 'stop',
                'usage': {},
            },
        ]
        worker, conv = self._make_worker(responses)
        conv.add_user('你好')
        worker._run_loop()

        hint_count = sum(
            1 for m in conv.messages
            if m.role == 'system' and m.content
            and '已执行完毕' in m.content
        )
        self.assertEqual(hint_count, 0)


if __name__ == '__main__':
    unittest.main()
