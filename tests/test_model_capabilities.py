#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试 model_capabilities：模型 context window 推断 + budget 换算。"""

from __future__ import absolute_import
from __future__ import print_function

import pytest

from maxagent import model_capabilities as mc


class TestInferContextWindow(object):
    """infer_context_window 的精确性测试。"""

    @pytest.mark.parametrize('model_id, expected', [
        # OpenAI
        ('gpt-4o', 128000),
        ('gpt-4o-mini', 128000),
        ('gpt-4o-2024-11-20', 128000),
        ('gpt-4-turbo', 128000),
        ('gpt-4-turbo-2024-04-09', 128000),
        ('gpt-4', 8192),
        ('gpt-4-0613', 8192),
        ('gpt-3.5-turbo', 16000),
        ('gpt-3.5-turbo-16k', 16000),
        ('gpt-4.1', 1000000),
        ('gpt-4.1-mini', 1000000),
        ('o1-preview', 128000),
        ('o1', 200000),
        ('o3-mini', 200000),
        # DeepSeek
        ('deepseek-chat', 128000),
        ('deepseek-reasoner', 128000),
        ('deepseek-v3', 128000),
        ('deepseek-v3.1', 128000),
        ('deepseek-v4', 1000000),
        # Claude
        ('claude-3-5-sonnet-20241022', 200000),
        ('claude-3.5-sonnet', 200000),
        ('claude-3-7-sonnet', 200000),
        ('claude-sonnet-4', 1000000),
        ('claude-3-opus-20240229', 200000),
        # Qwen
        ('qwen-max', 32768),
        ('qwen-plus', 131072),
        ('qwen-long', 10000000),
        ('qwen-turbo', 1000000),
        ('qwen2.5-72b-instruct', 131072),
        # Gemini
        ('gemini-1.5-pro', 2097152),
        ('gemini-1.5-flash', 1048576),
        ('gemini-2.0-flash', 1048576),
        ('gemini-2.5-pro', 1048576),
        # Llama
        ('llama-3.1-70b', 131072),
        ('llama-3', 8192),
        # 其他
        ('moonshot-v1-32k', 32000),
        ('grok-3', 131072),
        ('mistral-large', 131072),
    ])
    def test_known_models(self, model_id, expected):
        assert mc.infer_context_window(model_id) == expected

    def test_unknown_model_returns_zero(self):
        assert mc.infer_context_window('totally-unknown-xyz-2099') == 0

    def test_empty_model_returns_zero(self):
        assert mc.infer_context_window('') == 0
        assert mc.infer_context_window(None) == 0

    def test_case_insensitive(self):
        assert mc.infer_context_window('GPT-4O') == 128000
        assert mc.infer_context_window('DeepSeek-Chat') == 128000

    def test_strips_whitespace(self):
        assert mc.infer_context_window('  gpt-4o  ') == 128000


class TestOllamaSpecialHandling(object):
    """Ollama 端点的兜底处理：底层模型再大，默认 num_ctx=2048 也撑不起来。"""

    def test_ollama_qwen_capped_at_8k(self):
        # qwen2.5 底层 128K，但 Ollama 默认 num_ctx 远低于此
        result = mc.infer_context_window(
            'qwen2.5:14b', 'http://localhost:11434/v1',
        )
        assert result == mc.OLLAMA_DEFAULT_CTX  # 8192

    def test_ollama_llama3_capped_at_8k(self):
        result = mc.infer_context_window(
            'llama3.1:8b', 'http://127.0.0.1:11434/v1',
        )
        assert result == mc.OLLAMA_DEFAULT_CTX

    def test_ollama_keyword_in_url(self):
        # 用户自建 ollama 网关也能识别
        result = mc.infer_context_window(
            'qwen2.5', 'https://my-ollama.example.com/v1',
        )
        assert result == mc.OLLAMA_DEFAULT_CTX

    def test_non_ollama_url_keeps_full_capability(self):
        # 同样的 model 走 OpenAI 兼容网关时不应被压低
        result = mc.infer_context_window(
            'qwen2.5', 'https://api.openai.com/v1',
        )
        assert result == 131072

    def test_ollama_unknown_model_still_zero(self):
        # 未识别的模型即使在 Ollama 也返回 0
        result = mc.infer_context_window(
            'random-model-xyz', 'http://localhost:11434',
        )
        assert result == 0


class TestModelIdNormalization(object):
    """Ollama 风格 ``name:tag`` 必须正确剥离 tag。"""

    def test_strips_ollama_tag(self):
        assert mc.infer_context_window('llama3.1:70b') == 131072
        assert mc.infer_context_window('qwen2.5:32b-instruct-q4_K_M') == 131072

    def test_long_prefix_priority(self):
        # gpt-4-turbo 必须比 gpt-4 优先匹配
        assert mc.infer_context_window('gpt-4-turbo-2024') == 128000
        # claude-sonnet-4 必须比 claude-4 优先
        assert mc.infer_context_window('claude-sonnet-4-20250514') == 1000000
        # qwen-long 必须比 qwen 优先
        assert mc.infer_context_window('qwen-long') == 10000000


class TestRecommendHistoryBudget(object):
    """budget 换算比例的合理性。"""

    def test_zero_returns_zero(self):
        assert mc.recommend_history_budget(0) == 0
        assert mc.recommend_history_budget(-1) == 0

    def test_tiny_window_uses_50_percent(self):
        # 4K 窗口（如 llama2）按一半给历史
        assert mc.recommend_history_budget(4096) == 2048

    def test_medium_window_uses_65_percent(self):
        # 32K 窗口
        assert mc.recommend_history_budget(32000) == int(32000 * 0.65)

    def test_large_window_uses_75_percent(self):
        # 128K 窗口
        assert mc.recommend_history_budget(128000) == int(128000 * 0.75)

    def test_huge_window_reserves_50k(self):
        # 1M 窗口固定留 50K，避免一回合塞太满
        assert mc.recommend_history_budget(1000000) == 950000
        assert mc.recommend_history_budget(2097152) == 2047152

    def test_monotonic(self):
        # budget 必须随 ctx 单调不减
        prev = 0
        for ctx in [4096, 8192, 16000, 32000, 64000, 128000, 200000,
                    500000, 1000000, 2000000]:
            cur = mc.recommend_history_budget(ctx)
            assert cur >= prev
            prev = cur


class TestDescribeModel(object):
    """describe_model 的结构化输出。"""

    def test_known_remote(self):
        info = mc.describe_model('gpt-4o', 'https://api.openai.com/v1')
        assert info['source'] == 'inferred'
        assert info['context_window'] == 128000
        assert info['history_budget'] == 96000
        assert info['is_ollama'] is False

    def test_known_ollama(self):
        info = mc.describe_model('qwen2.5:14b', 'http://localhost:11434/v1')
        assert info['source'] == 'inferred'
        assert info['context_window'] == mc.OLLAMA_DEFAULT_CTX
        assert info['is_ollama'] is True

    def test_unknown(self):
        info = mc.describe_model('foo-bar-9999', '')
        assert info['source'] == 'unknown'
        assert info['context_window'] == 0
        assert info['history_budget'] == 0
