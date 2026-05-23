#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dispatch_task headless 单元测试。

通过 monkeypatch 一个 fake LLMClient，让任务循环跑两轮：
1. 第一轮 LLM 返回 tool_call，让 dispatcher 执行
2. 第二轮 LLM 返回最终文本

不依赖网络、不依赖 Max。
"""

from __future__ import absolute_import
from __future__ import print_function

import threading
import unittest
from unittest import mock

from maxagent.bridge.handlers import dispatch_task as dt_mod
from maxagent.bridge.protocol import BridgeErrorCode


class _FakeProfile(object):
    """最小 profile stub，避免依赖 LLMClient 真实构造。"""

    def __init__(self, name='fake', model='fake-model'):
        self.name = name
        self.base_url = 'http://fake'
        self.api_key = ''
        self.model = model
        self.timeout = 30.0
        self.extra_headers = ''


class _FakeConfig(object):

    def __init__(self, active_profile, all_profiles=None):
        self._active = active_profile
        self.profiles = all_profiles or [active_profile]


class _FakeConfigManager(object):

    def __init__(self, active_profile, all_profiles=None):
        self.config = _FakeConfig(active_profile, all_profiles)

    def get_active_profile(self):
        return self.config._active


class _FakeLLM(object):
    """按预设序列返回 chat() 响应。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        if not self.responses:
            return {'content': '(end)', 'tool_calls': [],
                    'finish_reason': 'stop', 'usage': {}}
        return self.responses.pop(0)


class TestDispatchInvalidPayload(unittest.TestCase):

    def test_missing_prompt(self):
        resp = dt_mod.handle_dispatch_task(
            payload={}, request_id='r1',
        )
        self.assertFalse(resp['ok'])
        self.assertEqual(
            resp['error']['code'], BridgeErrorCode.INVALID_RESPONSE,
        )

    def test_blank_prompt(self):
        resp = dt_mod.handle_dispatch_task(
            payload={'prompt': '   '}, request_id='r2',
        )
        self.assertFalse(resp['ok'])
        self.assertEqual(
            resp['error']['code'], BridgeErrorCode.INVALID_RESPONSE,
        )

    def test_no_profile_available(self):
        resp = dt_mod.handle_dispatch_task(
            payload={'prompt': 'hi'},
            request_id='r3',
            config_manager=None,
        )
        self.assertFalse(resp['ok'])
        self.assertEqual(
            resp['error']['code'], BridgeErrorCode.INTERNAL_ERROR,
        )


class TestDispatchHappyPath(unittest.TestCase):

    def test_loop_executes_tool_then_returns_final(self):
        # 第 1 轮：LLM 决定调一个工具
        # 第 2 轮：LLM 看到工具结果给最终回复
        fake_llm = _FakeLLM(responses=[
            {
                'content': '',
                'tool_calls': [{
                    'id': 'tc1',
                    'name': 'my_tool',
                    'arguments': {'x': 1},
                }],
                'finish_reason': 'tool_calls',
                'usage': {},
            },
            {
                'content': '完成',
                'tool_calls': [],
                'finish_reason': 'stop',
                'usage': {},
            },
        ])

        # ToolDispatcher.dispatch 的 stub
        fake_dispatch = mock.MagicMock(return_value={
            'ok': True, 'result': {'value': 42},
        })

        # build_openai_tools_schema 返回固定 schema 即可
        with mock.patch.object(
            dt_mod, 'build_client_from_profile', return_value=fake_llm,
        ), mock.patch.object(
            dt_mod, 'build_openai_tools_schema',
            return_value=[{'type': 'function',
                           'function': {'name': 'my_tool'}}],
        ), mock.patch.object(
            dt_mod, 'ToolDispatcher',
            return_value=mock.MagicMock(dispatch=fake_dispatch),
        ):
            cfg = _FakeConfigManager(_FakeProfile())
            resp = dt_mod.handle_dispatch_task(
                payload={'prompt': '请帮我跑个测试',
                         'max_rounds': 5,
                         'timeout_seconds': 30},
                request_id='r-happy',
                config_manager=cfg,
            )

        self.assertTrue(resp['ok'], msg=resp)
        data = resp['data']
        self.assertEqual(data['final_message'], '完成')
        self.assertEqual(data['rounds'], 2)
        self.assertEqual(len(data['tool_calls']), 1)
        self.assertEqual(data['tool_calls'][0]['name'], 'my_tool')
        self.assertTrue(data['tool_calls'][0]['ok'])
        self.assertEqual(data['profile'], 'fake')
        self.assertEqual(data['model'], 'fake-model')
        # LLM 被叫 2 次（含工具循环）
        self.assertEqual(len(fake_llm.calls), 2)
        # dispatcher 被叫 1 次
        self.assertEqual(fake_dispatch.call_count, 1)


class TestDispatchLLMError(unittest.TestCase):

    def test_llm_error_returned_as_execution_error(self):
        from maxagent.llm_client import LLMError

        class _ErrLLM(object):
            def chat(self, **kwargs):
                raise LLMError('429 rate limit')

        with mock.patch.object(
            dt_mod, 'build_client_from_profile', return_value=_ErrLLM(),
        ), mock.patch.object(
            dt_mod, 'build_openai_tools_schema', return_value=[],
        ), mock.patch.object(
            dt_mod, 'ToolDispatcher', return_value=mock.MagicMock(),
        ):
            cfg = _FakeConfigManager(_FakeProfile())
            resp = dt_mod.handle_dispatch_task(
                payload={'prompt': 'go'},
                request_id='r-err',
                config_manager=cfg,
            )

        self.assertFalse(resp['ok'])
        self.assertEqual(
            resp['error']['code'], BridgeErrorCode.EXECUTION_ERROR,
        )
        # data 仍要带回，便于 IDE 端读 trace
        self.assertIn('error', resp['data'])
        self.assertIn('429', resp['data']['error'])


class TestDispatchMaxRounds(unittest.TestCase):

    def test_loop_terminates_at_max_rounds(self):
        # LLM 一直返回 tool_call，永远不收尾 → 必须在 max_rounds 终止
        infinite_resp = {
            'content': '',
            'tool_calls': [{
                'id': 'x', 'name': 'noop', 'arguments': {},
            }],
            'finish_reason': 'tool_calls',
            'usage': {},
        }

        class _LoopLLM(object):
            def __init__(self):
                self.calls = 0

            def chat(self, **kwargs):
                self.calls += 1
                return dict(infinite_resp)

        loop_llm = _LoopLLM()
        fake_dispatch = mock.MagicMock(
            return_value={'ok': True, 'result': None},
        )
        with mock.patch.object(
            dt_mod, 'build_client_from_profile', return_value=loop_llm,
        ), mock.patch.object(
            dt_mod, 'build_openai_tools_schema', return_value=[],
        ), mock.patch.object(
            dt_mod, 'ToolDispatcher',
            return_value=mock.MagicMock(dispatch=fake_dispatch),
        ):
            cfg = _FakeConfigManager(_FakeProfile())
            resp = dt_mod.handle_dispatch_task(
                payload={'prompt': 'loop forever',
                         'max_rounds': 3,
                         'timeout_seconds': 30},
                request_id='r-loop',
                config_manager=cfg,
            )

        self.assertTrue(resp['ok'])
        data = resp['data']
        self.assertEqual(data['rounds'], 3)
        self.assertTrue(data.get('reached_max_rounds'))
        self.assertEqual(loop_llm.calls, 3)


class TestProfileResolution(unittest.TestCase):

    def test_explicit_profile_name(self):
        p1 = _FakeProfile(name='primary')
        p2 = _FakeProfile(name='backup', model='m2')
        cfg = _FakeConfigManager(p1, all_profiles=[p1, p2])
        with mock.patch.object(
            dt_mod, 'build_client_from_profile',
            return_value=_FakeLLM([
                {'content': 'ok', 'tool_calls': [],
                 'finish_reason': 'stop', 'usage': {}},
            ]),
        ), mock.patch.object(
            dt_mod, 'build_openai_tools_schema', return_value=[],
        ), mock.patch.object(
            dt_mod, 'ToolDispatcher', return_value=mock.MagicMock(),
        ):
            resp = dt_mod.handle_dispatch_task(
                payload={'prompt': 'x', 'profile': 'backup'},
                request_id='r-p',
                config_manager=cfg,
            )
        self.assertTrue(resp['ok'])
        # 显式指定的 profile 应被采用
        self.assertEqual(resp['data']['profile'], 'backup')

    def test_unknown_profile_falls_back_to_active(self):
        p1 = _FakeProfile(name='primary')
        cfg = _FakeConfigManager(p1)
        with mock.patch.object(
            dt_mod, 'build_client_from_profile',
            return_value=_FakeLLM([
                {'content': 'ok', 'tool_calls': [],
                 'finish_reason': 'stop', 'usage': {}},
            ]),
        ), mock.patch.object(
            dt_mod, 'build_openai_tools_schema', return_value=[],
        ), mock.patch.object(
            dt_mod, 'ToolDispatcher', return_value=mock.MagicMock(),
        ):
            resp = dt_mod.handle_dispatch_task(
                payload={'prompt': 'x', 'profile': 'no-such'},
                request_id='r-pf',
                config_manager=cfg,
            )
        self.assertTrue(resp['ok'])
        self.assertEqual(resp['data']['profile'], 'primary')


if __name__ == '__main__':
    unittest.main()
