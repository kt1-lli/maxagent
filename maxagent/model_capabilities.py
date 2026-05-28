#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模型能力库：根据 model 字段推断 context window 上限。

设计目标
--------
让 UI 上的"上下文预算"能跟随 Profile 自动适配，而不需要用户为每个新
模型手填 ``max_history_tokens``。

策略
----
- ``infer_context_window(model_id, base_url)`` 接收原始 model 字符串
  （如 ``deepseek-chat``、``gpt-4o-2024-11``、``qwen2.5:14b``），返回
  推断出的 context tokens 上限；若无法识别返回 ``0``。
- 匹配优先级：精确字符串 > 长前缀 > 短前缀（确保 ``gpt-4-turbo`` 不被
  ``gpt-4`` 错误吞掉）。
- **Ollama 特殊处理**：``base_url`` 含 ``11434`` 或 ``ollama`` 时，由于
  Ollama 默认 ``num_ctx=2048``（大幅低于底层模型能力），推断结果按
  保守值返回——除非用户在 Profile 里手动调高 ``num_ctx``，否则真实
  context 不会大于 8K。
- ``recommend_history_budget(ctx_window)`` 按"留 25% 空间给当前提示 +
  完成 token"的经验比例换算成 ``max_history_tokens`` 推荐值。

注意：本库只做**推断**，不做覆盖。具体覆盖策略由调用方（dock_widget）
决定——A1 方案下，UI 显示直接用推断值；用户手填值仅在推断失败时回退。

数据来源（联网核对：2026-05）
-----------------------------
- OpenAI: GPT-4o 128K, GPT-4-turbo 128K, GPT-4 8K, GPT-3.5 16K,
  GPT-4.1 1M, GPT-5 系列 200K~400K, GPT-6 2M
- Anthropic: Claude 3.5/3.7 Sonnet 200K, Claude Sonnet 4 1M,
  Opus/Haiku 200K
- DeepSeek: V3/V3.1/V3.2 128K, V4 1M, deepseek-chat/reasoner 128K
- Qwen: qwen-max 32K, qwen-plus 131K, qwen-long 10M, qwen-turbo 1M,
  Qwen2.5-72B 128K
- Gemini: 1.5 Pro 2M, 1.5/2.0/2.5 Flash 1M, 2.5 Pro 1M+
- Ollama 底层模型：llama3 8K, llama3.1/3.2 128K, qwen2.5 128K
  （但实际有效值受 num_ctx 限制，默认仅 2048）
"""

from __future__ import absolute_import
from __future__ import print_function

# 单位：token 数
# 注：此表按"长 key 优先"原则维护——更具体的型号必须排在更宽泛的前缀之前
_MODEL_CONTEXT_TABLE = [
    # ===== OpenAI =====
    ('gpt-6', 2000000),
    ('gpt-5', 400000),
    ('gpt-4.1-mini', 1000000),
    ('gpt-4.1-nano', 1000000),
    ('gpt-4.1', 1000000),
    ('gpt-4o-mini', 128000),
    ('gpt-4o', 128000),
    ('gpt-4-turbo', 128000),
    ('gpt-4-32k', 32000),
    ('gpt-4', 8192),
    ('gpt-3.5-turbo-16k', 16000),
    ('gpt-3.5-turbo', 16000),
    ('gpt-3.5', 16000),
    ('o1-mini', 128000),
    ('o1-preview', 128000),
    ('o1', 200000),
    ('o3-mini', 200000),
    ('o3', 200000),
    ('o4-mini', 200000),
    # ===== Anthropic Claude =====
    ('claude-sonnet-4', 1000000),
    ('claude-opus-4', 200000),
    ('claude-4', 200000),
    ('claude-3-7-sonnet', 200000),
    ('claude-3.7-sonnet', 200000),
    ('claude-3-5-sonnet', 200000),
    ('claude-3.5-sonnet', 200000),
    ('claude-3-5-haiku', 200000),
    ('claude-3.5-haiku', 200000),
    ('claude-3-opus', 200000),
    ('claude-3-sonnet', 200000),
    ('claude-3-haiku', 200000),
    ('claude-2.1', 200000),
    ('claude-2', 100000),
    # ===== DeepSeek =====
    ('deepseek-v4', 1000000),
    ('deepseek-v3.2', 128000),
    ('deepseek-v3.1', 128000),
    ('deepseek-v3', 128000),
    ('deepseek-r1', 128000),
    ('deepseek-reasoner', 128000),
    ('deepseek-chat', 128000),
    ('deepseek-coder', 128000),
    ('deepseek', 128000),
    # ===== Qwen / 通义 =====
    ('qwen-long', 10000000),
    ('qwen-turbo', 1000000),
    ('qwen-plus', 131072),
    ('qwen-max', 32768),
    ('qwen3', 131072),
    ('qwen2.5-coder', 131072),
    ('qwen2.5-vl', 131072),
    ('qwen2.5', 131072),
    ('qwen2-72b', 131072),
    ('qwen2', 32768),
    ('qwen-vl', 32768),
    ('qwen', 32768),
    # ===== Google Gemini =====
    ('gemini-2.5-pro', 1048576),
    ('gemini-2.5-flash', 1048576),
    ('gemini-2.0-flash', 1048576),
    ('gemini-2.0-pro', 2097152),
    ('gemini-2.0', 1048576),
    ('gemini-1.5-pro', 2097152),
    ('gemini-1.5-flash', 1048576),
    ('gemini-1.5', 1048576),
    ('gemini-pro', 32768),
    ('gemini', 32768),
    # ===== Meta Llama =====
    ('llama-4', 1000000),
    ('llama4', 1000000),
    ('llama-3.3', 131072),
    ('llama3.3', 131072),
    ('llama-3.2', 131072),
    ('llama3.2', 131072),
    ('llama-3.1', 131072),
    ('llama3.1', 131072),
    ('llama-3', 8192),
    ('llama3', 8192),
    ('llama-2', 4096),
    ('llama2', 4096),
    # ===== Mistral =====
    ('mistral-large', 131072),
    ('mistral-medium', 32768),
    ('mistral-small', 32768),
    ('mistral-nemo', 131072),
    ('mistral-7b', 32768),
    ('mistral', 32768),
    ('mixtral-8x22b', 65536),
    ('mixtral', 32768),
    # ===== 其它常见 =====
    ('phi-4', 16384),
    ('phi-3', 128000),
    ('gemma-2', 8192),
    ('gemma', 8192),
    ('command-r-plus', 128000),
    ('command-r', 128000),
    ('yi-lightning', 16384),
    ('yi-large', 32768),
    ('yi', 32768),
    ('glm-4', 131072),
    ('chatglm', 32768),
    ('moonshot-v1-128k', 128000),
    ('moonshot-v1-32k', 32000),
    ('moonshot-v1-8k', 8000),
    ('moonshot', 128000),
    ('kimi', 128000),
    ('grok-3', 131072),
    ('grok-2', 131072),
    ('grok', 131072),
]

# Ollama 默认 num_ctx 上限。即便底层模型支持 128K，未调高 num_ctx 时
# 实际窗口仅 2048，按 8K 兜底（绝大多数用户会在 modelfile 里调到 8K~16K）。
OLLAMA_DEFAULT_CTX = 8192

# 推断失败时的兜底值
FALLBACK_CONTEXT = 0


def _is_ollama_endpoint(base_url):
    # type: (str) -> bool
    """根据 base_url 判断是否为 Ollama 本地端点。"""
    if not base_url:
        return False
    lower = base_url.lower()
    return 'ollama' in lower or ':11434' in lower or '11434/' in lower


def _normalize_model_id(model_id):
    # type: (str) -> str
    """统一归一化 model 字符串：小写 + 去掉日期后缀 + 去掉 ``:tag``。"""
    if not model_id:
        return ''
    m = model_id.strip().lower()
    # Ollama 风格的 ``qwen2.5:14b`` —— 取冒号之前部分
    if ':' in m:
        m = m.split(':', 1)[0]
    return m


def infer_context_window(model_id, base_url=''):
    # type: (str, str) -> int
    """根据 model 字段推断 context window（token 数）。

    :param model_id: 模型字符串，如 ``gpt-4o``、``deepseek-chat``、
        ``qwen2.5:14b``。
    :param base_url: 可选的 API endpoint，用于识别 Ollama 等特殊后端。
    :return: 推断出的 context token 上限；无法识别时返回 ``0``。
    """
    norm = _normalize_model_id(model_id)
    if not norm:
        return FALLBACK_CONTEXT

    is_ollama = _is_ollama_endpoint(base_url)

    # 表已按"长 key 优先"维护——遍历首个匹配即为最佳匹配
    matched = FALLBACK_CONTEXT
    for key, ctx in _MODEL_CONTEXT_TABLE:
        if key in norm:
            matched = ctx
            break

    if is_ollama and matched > 0:
        # Ollama 实际能力受 num_ctx 限制，按 8K 兜底
        return min(matched, OLLAMA_DEFAULT_CTX)
    return matched


def recommend_history_budget(ctx_window):
    # type: (int) -> int
    """把 context window 换算为推荐的 ``max_history_tokens``。

    经验比例：留出约 25% 给当前 prompt + 完成；剩 75% 给历史。
    极小窗口（<8K）按 50% 给历史，避免一句问题就超限。
    """
    if ctx_window <= 0:
        return 0
    if ctx_window < 8192:
        return int(ctx_window * 0.5)
    if ctx_window <= 32768:
        return int(ctx_window * 0.65)
    if ctx_window <= 200000:
        return int(ctx_window * 0.75)
    # 超长窗口（≥200K）固定保留 50K 给当前回合，避免一次塞太满
    return ctx_window - 50000


def describe_model(model_id, base_url=''):
    # type: (str, str) -> dict
    """返回结构化推断结果，便于 UI 展示与日志记录。

    :return: ``{'context_window': int, 'history_budget': int,
        'source': 'inferred' | 'unknown', 'is_ollama': bool}``
    """
    is_ollama = _is_ollama_endpoint(base_url)
    ctx = infer_context_window(model_id, base_url)
    if ctx <= 0:
        return {
            'context_window': 0,
            'history_budget': 0,
            'source': 'unknown',
            'is_ollama': is_ollama,
        }
    return {
        'context_window': ctx,
        'history_budget': recommend_history_budget(ctx),
        'source': 'inferred',
        'is_ollama': is_ollama,
    }
