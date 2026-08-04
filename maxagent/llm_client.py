#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OpenAI 兼容的 LLM 客户端。

设计目标：
1. 不依赖官方 openai SDK（避免 Max 内 pip 装包麻烦），用 urllib + json 直连 HTTP。
2. 同时支持本地模型（Ollama / LM Studio / vLLM）和远程 API Key 模式。
3. 支持 Function Calling（OpenAI tools 协议）+ JSON 模式（本地模型降级路径）。
4. 支持流式输出（SSE）。
"""

import json
import socket
import time
import urllib.error
import urllib.request
from typing import Any
from typing import Callable
from typing import Dict
from typing import Iterator
from typing import List
from typing import Optional

from .logger import get_logger


logger = get_logger(__name__)


# 默认触发指数退避重试的 HTTP 状态码：429 rate limit / 502 / 503 / 504 服务端过载。
_DEFAULT_RETRYABLE_STATUS_CODES = frozenset((429, 502, 503, 504))

# Windows 网络错误码 → 用户友好提示映射。
# 这些都是用户系统层面引发的 socket 中断，不是 LLM 服务端问题。
# winerror 在 ConnectionAbortedError / ConnectionResetError / OSError 上
# 都可访问（Python 在 Windows 把这些 errno 一起暴露成 winerror 属性）。
_WINERR_HINTS = {
    10053: '本地软件中止了连接（VPN/防火墙/杀毒可能拦截）',
    10054: '远端强制关闭连接（服务端异常或中转代理断流）',
    10060: '连接超时（网络不通或服务端响应慢）',
    10061: '目标主机拒绝连接（端口未监听或服务未启动）',
    11001: '主机名解析失败（base_url 拼写错误或 DNS 异常）',
}


class LLMError(Exception):
    """LLM 调用相关异常。"""


class LLMRateLimitError(LLMError):
    """触发速率限制或服务端过载，建议上层切换到备用 provider。

    :param message: 人类可读错误信息
    :param should_fallback: 是否应尝试备用 provider
    :param status_code: 原始 HTTP 状态码（若有）
    """

    def __init__(self, message, should_fallback=True, status_code=None):
        super(LLMRateLimitError, self).__init__(message)
        self.should_fallback = bool(should_fallback)
        self.status_code = status_code


def _winerror_of(exc: BaseException) -> Optional[int]:
    """从底层 socket / OSError 链路里提取 winerror（仅 Windows 有意义）。

    URLError 把真实 socket 异常包在 ``.reason`` 里；OSError 自身就有
    ``winerror`` 属性。其他平台返回 None 即可。
    """
    # URLError → 解开真实 reason
    inner = getattr(exc, 'reason', None)
    if inner is not None and isinstance(inner, BaseException):
        we = getattr(inner, 'winerror', None)
        if we:
            return int(we)
    we = getattr(exc, 'winerror', None)
    return int(we) if we else None


def _is_retryable_network_error(exc: BaseException) -> bool:
    """判断网络异常是否值得重试（仅"连接未建立或刚建立就断"的场景）。

    不能盲目重试所有 OSError——例如 ECONNREFUSED（10061）服务端没开，
    重试也是徒劳；DNS 解析失败也不该重试。这里只覆盖**瞬断**类错误。
    """
    we = _winerror_of(exc)
    # 10053 本地软件中止 / 10054 远端 reset / 10060 connect timeout
    # 这三类大概率是网络瞬时抖动，重试可能恢复
    if we in (10053, 10054, 10060):
        return True
    # 跨平台兜底：常见的"连接被对端关闭"类异常（Linux/macOS 也算）
    if isinstance(
        exc, (ConnectionAbortedError, ConnectionResetError),
    ):
        return True
    # 真实 reason 是 socket.timeout 也算可重试
    inner = getattr(exc, 'reason', None)
    if isinstance(inner, socket.timeout):
        return True
    if isinstance(exc, socket.timeout):
        return True
    return False


def _humanize_network_error(exc: BaseException) -> str:
    """把底层网络异常翻译成用户可读的中文提示。

    返回示例:
        "网络中断 [WinError 10053]：本地软件中止了连接（VPN/防火墙/
        杀毒可能拦截）。建议：检查 VPN/代理；或切换其他模型 Profile 重试"
    """
    we = _winerror_of(exc)
    if we and we in _WINERR_HINTS:
        return (
            '网络中断 [WinError {code}]：{hint}。'
            '建议：① 检查 VPN/代理/防火墙是否拦截了 LLM 端口；'
            '② 切换其他模型 Profile 重试；'
            '③ 若 base_url 是公司网关，确认网关是否限流'
        ).format(code=we, hint=_WINERR_HINTS[we])
    # 非 Windows 或未知错误码，回退到原始 reason 字符串
    inner = getattr(exc, 'reason', exc)
    return '网络错误: {}'.format(inner)


def _format_http_error(exc: "urllib.error.HTTPError", url: str) -> str:
    """把 HTTPError 格式化为人类可读的详细错误信息。

    会尽力提取响应体中的 ``error.message`` / ``error.code`` / ``request_id`` /
    关键 response headers（如 X-Request-Id / WWW-Authenticate / X-Ratelimit-*）
    和回环网关相关的头（如 X-Tencent-* / X-Auth-* / Server），方便快速定位
    "key 错"还是"网关拦截"还是"请求体被拒"。
    """
    try:
        raw = exc.read().decode("utf-8", errors="replace")
    except Exception:  # pylint: disable=broad-except
        raw = "<unable to read body>"

    # 尝试从 JSON body 提取 error.message
    pretty_msg = ""
    try:
        body_json = json.loads(raw)
        if isinstance(body_json, dict):
            err_obj = body_json.get("error") or body_json
            if isinstance(err_obj, dict):
                pretty_msg = (
                    err_obj.get("message")
                    or err_obj.get("msg")
                    or err_obj.get("detail")
                    or ""
                )
            elif isinstance(err_obj, str):
                pretty_msg = err_obj
    except (ValueError, TypeError):
        pretty_msg = ""

    # 关键 header（用于辨认是否被中间网关拦截）
    interesting_headers = []
    try:
        hdrs = exc.headers or {}
        keys_of_interest = (
            "Server",
            "Via",
            "X-Request-Id",
            "X-Trace-Id",
            "X-Tencent-Reqid",
            "X-Tencent-Auth",
            "X-Auth-Token",
            "WWW-Authenticate",
            "X-Ratelimit-Limit-Requests",
            "X-Ratelimit-Remaining-Requests",
            "Cf-Ray",
        )
        for k in keys_of_interest:
            v = hdrs.get(k)
            if v:
                interesting_headers.append("{}={}".format(k, v))
    except Exception:  # pylint: disable=broad-except
        pass

    parts = ["HTTP {}".format(exc.code)]
    if pretty_msg:
        parts.append(pretty_msg)
    # 始终保留原始 body 的前 400 字符（截断防爆）
    body_preview = raw.strip()
    if len(body_preview) > 400:
        body_preview = body_preview[:400] + "...(truncated)"
    if body_preview and body_preview != pretty_msg:
        parts.append("body=" + body_preview)
    if interesting_headers:
        parts.append("headers[{}]".format("; ".join(interesting_headers)))
    parts.append("url=" + url)
    return " | ".join(parts)


# 已知路径误填模式 -> 自动剥除的尾部
_KNOWN_PATH_TAILS = (
    "/chat/completions",
    "/completions",
    "/v1/chat/completions",
    "/v1/completions",
)


def _sanitize_base_url(url: str) -> str:
    """规范化 base_url：去掉末尾斜杠 + 自动剥除常见的误填尾部。

    用户经常把 base_url 填成完整的 endpoint
    （如 ``https://api.deepseek.com/chat/completions``），
    导致拼接后变成 ``.../chat/completions/chat/completions`` 走不通。
    这里做一次防呆处理。
    """
    if not url:
        return url
    cleaned = url.strip().rstrip("/")
    lowered = cleaned.lower()
    for tail in _KNOWN_PATH_TAILS:
        if lowered.endswith(tail):
            cleaned = cleaned[: -len(tail)]
            break
    return cleaned.rstrip("/")


def diagnose_base_url(url: str) -> Optional[str]:
    """对 base_url 做静态体检，返回提示文本（无问题返回 None）。

    仅用于 UI 给用户提示，不会影响实际请求行为。
    """
    if not url:
        return None
    raw = url.strip()
    if not raw:
        return None
    if not (raw.startswith("http://") or raw.startswith("https://")):
        return "⚠ Base URL 必须以 http:// 或 https:// 开头"
    lowered = raw.rstrip("/").lower()
    for tail in _KNOWN_PATH_TAILS:
        if lowered.endswith(tail):
            return "⚠ Base URL 末尾不应包含 {}，会被自动忽略".format(tail)
    # 已知不需要 /v1 也能正常工作的根域名白名单（按官方文档）
    _ROOT_OK_HOSTS = (
        "api.deepseek.com",  # DeepSeek：根域名即 OpenAI 兼容入口
    )
    for host in _ROOT_OK_HOSTS:
        if host in lowered:
            return None
    if "/v1" not in lowered and "/v2" not in lowered and "/api" not in lowered:
        return (
            "💡 多数 OpenAI 兼容服务需要带版本路径（如 /v1）。"
            "若遇 401/404 可尝试切换 /v1 后缀"
        )
    return None


def _exponential_backoff(base_delay, max_delay, attempt):
    """计算第 attempt 次重试的退避时间（从 0 开始计数）。

    公式：min(max_delay, base_delay * 2^attempt) + 随机 jitter（±12.5%）
    jitter 用于避免多个并发请求在同一时刻冲击备用 provider。
    """
    import random
    delay = min(float(max_delay), float(base_delay) * (2 ** attempt))
    jitter = delay * (random.uniform(-0.125, 0.125))
    return max(0.0, delay + jitter)


def _is_retryable_http_status(status_code, retryable_set=None):
    """判断 HTTP 状态码是否应触发指数退避重试。"""
    if retryable_set is None:
        retryable_set = _DEFAULT_RETRYABLE_STATUS_CODES
    return status_code in retryable_set


class LLMClient(object):
    """OpenAI 兼容的最小客户端。

    :param base_url: 形如 http://localhost:11434/v1 或 https://api.openai.com/v1
    :param api_key: API Key；本地模型可填任意非空字符串
    :param model:   模型名
    :param timeout: HTTP 超时时间（秒）
    :param extra_headers: 额外 HTTP 头
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: int = 120,
        extra_headers: Optional[Dict[str, str]] = None,
        max_retries: int = 3,
        retry_base_delay: float = 2.0,
        retry_max_delay: float = 60.0,
        retryable_status_codes: Optional[set] = None,
    ):
        self._base_url = _sanitize_base_url(base_url)
        self._api_key = api_key or ""
        self._model = model
        self._timeout = timeout
        self._extra_headers = dict(extra_headers or {})
        # 最近一次响应里携带的 usage 统计（OpenAI 兼容协议会返回
        # ``{"prompt_tokens", "completion_tokens", "total_tokens"}``）。
        # 流式模式下大多数后端会在最后一个 chunk 的 ``usage`` 字段返回。
        self._last_usage = {}  # type: Dict[str, int]
        # 指数退避重试配置：429 / 5xx / 网络瞬断时自动重试。
        self._max_retries = max(0, int(max_retries))
        self._retry_base_delay = float(retry_base_delay)
        self._retry_max_delay = float(retry_max_delay)
        # 默认触发指数退避重试的 HTTP 状态码：429 rate limit / 502 / 503 / 504 服务端过载。
        self._retryable_status_codes = retryable_status_codes or _DEFAULT_RETRYABLE_STATUS_CODES

    # ------------------------------------------------------------------ #
    # 公共方法
    # ------------------------------------------------------------------ #

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        stream: bool = False,
        on_delta: Optional[Callable[[str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        reasoning_mode: bool = False,
    ) -> Dict[str, Any]:
        """发起一次 chat completion 调用。

        :param messages: OpenAI 格式的消息列表
        :param tools:    OpenAI tools 列表（function calling）；为 None 表示不开 tools
        :param temperature: 基础温度；当 reasoning_mode=True 时自动降至 0.1
        :param stream:   是否流式
        :param on_delta: 流式模式下每收到一个文本片段时的回调
        :param cancel_check: 流式期间的取消检查回调，返回 True 时立刻关闭
            连接并抛 ``LLMError('用户取消')``，让 worker 能快速跟手停下来。
        :param reasoning_mode: 是否为"思考/规划"轮次（工具调用前）。
            True 时 temperature 锁定 0.1，提升工具参数确定性。
        :returns: 一个标准化的 dict：
                  {
                      "content": "文本内容（可能为空）",
                      "tool_calls": [
                          {"id": "...", "name": "...", "arguments": {...}},
                          ...
                      ],
                      "finish_reason": "stop"|"tool_calls"|"length"|...,
                      "usage": {"prompt_tokens": int, ...},  # 仅当后端返回时
                      "raw": <原始响应>,
                  }
        """
        # Temperature 分层：reasoning / 工具调用轮次用更低温度
        # 减少参数幻觉和过度联想；最终回复轮次保持用户设定温度
        effective_temp = 0.1 if reasoning_mode else temperature

        # 部分模型/网关（如 Moonshot kimi-k3）服务端强制 temperature=1，
        # 与 reasoning_mode 无关。先在此强制修正，后续 param_overrides
        # 仍可让用户显式覆盖。
        try:
            from .model_capabilities import requires_temperature_one
            if requires_temperature_one(self._model):
                effective_temp = 1.0
        except Exception:  # pylint: disable=broad-except
            pass

        payload: Dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": effective_temp,
            "stream": stream,
        }
        # max_tokens <= 0 表示「由模型决定」（UI 设置中的特殊值），
        # 此时不发该字段，让服务端使用其默认值。
        # 部分严苛网关（如 tokenhub vita 视觉模型）会在 max_tokens 超出
        # 模型上限时直接返回 400 invalid_params；保留 UI 的 0 语义可让
        # 用户在不知道具体上限时也能成功调用。
        if max_tokens and max_tokens > 0:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        # 通用参数覆盖：profile.param_overrides 最后生效，可覆盖
        # temperature / top_p / max_tokens 等任意字段，兼容不同模型/网关
        # 的特殊要求（如 Moonshot kimi-k3 需要 temperature=1）。
        profile = getattr(self, '_profile', None)
        overrides = getattr(profile, 'param_overrides', None)
        if overrides:
            payload.update(overrides)
        # DeepSeek 增强：本客户端已在 _chat_stream / _chat_blocking 中
        # 完整支持 reasoning_content 的收集与回传（见 reasoning_chunks
        # 处理逻辑）。对于支持 thinking 的模型（如 deepseek-reasoner），
        # 服务端会自动返回 <think>...</think>  reasoning_content；
        # 我们将其保留在 assistant message 中传给下一轮，确保思考链
        # 连续性。无需额外参数控制——模型名本身决定 thinking 行为。
        # 流式模式下额外要 usage 字段（DeepSeek/OpenAI 都支持这个 option，
        # 不支持的后端会忽略）
        if stream:
            payload["stream_options"] = {"include_usage": True}

        url = self._base_url + "/chat/completions"
        headers = self._build_headers()
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        # DEBUG 埋点：请求摘要（避免打印 messages 全文）
        if logger.isEnabledFor(10):
            logger.debug(
                'HTTP %s model=%s msgs=%d tools=%d stream=%s body=%dB',
                'POST', self._model, len(messages),
                len(tools or []), stream, len(body),
            )

        if stream:
            return self._chat_stream(url, headers, body, on_delta, cancel_check)
        return self._chat_blocking(url, headers, body)

    def get_last_usage(self) -> Dict[str, int]:
        """返回最近一次 chat() 收到的 usage 字典；若后端未返回则为空 dict。"""
        return dict(self._last_usage)

    # ------------------------------------------------------------------ #
    # 内部实现
    # ------------------------------------------------------------------ #

    def _build_headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self._api_key:
            headers["Authorization"] = "Bearer " + self._api_key
        headers.update(self._extra_headers)
        return headers

    def _open_request(
        self,
        req: urllib.request.Request,
        is_stream: bool = False,
    ) -> Any:
        """统一建立 HTTP 连接，处理重试与可重试错误。

        :param req: urllib Request 对象
        :param is_stream: 是否为流式请求（仅影响日志文案）
        :returns: urllib response 对象
        :raises LLMRateLimitError: 429 / 503 等可重试状态码耗尽重试次数
        :raises LLMError: 其他不可重试错误
        """
        max_retries = self._max_retries
        base_delay = self._retry_base_delay
        max_delay = self._retry_max_delay
        retryable = self._retryable_status_codes
        last_http_exc: Optional[urllib.error.HTTPError] = None

        mode = 'stream' if is_stream else 'blocking'
        for attempt in range(max_retries + 1):
            if attempt > 0:
                backoff = _exponential_backoff(base_delay, max_delay, attempt - 1)
                logger.info(
                    'HTTP %s retry attempt %d/%d after %.2fs backoff '
                    '(model=%s)',
                    mode, attempt, max_retries, backoff, self._model,
                )
                time.sleep(backoff)
            t0 = time.time()
            try:
                return urllib.request.urlopen(req, timeout=self._timeout)
            except urllib.error.HTTPError as exc:
                last_http_exc = exc
                logger.warning(
                    'HTTP %s %s failed in %.2fs: %s',
                    self._model, mode, time.time() - t0, exc.code,
                )
                if exc.code in retryable:
                    # 可重试状态码：继续循环；耗尽后抛 LLMRateLimitError
                    if attempt < max_retries:
                        continue
                    raise LLMRateLimitError(
                        _format_http_error(exc, req.full_url),
                        should_fallback=True,
                        status_code=exc.code,
                    )
                # 不可重试 HTTP 错误直接抛 LLMError
                raise LLMError(_format_http_error(exc, req.full_url))
            except urllib.error.URLError as exc:
                logger.warning(
                    'Network %s %s failed in %.2fs: %s',
                    self._model, mode, time.time() - t0, exc.reason,
                )
                if not _is_retryable_network_error(exc):
                    raise LLMError(_humanize_network_error(exc))
                if attempt < max_retries:
                    continue
                raise LLMError(_humanize_network_error(exc))

        # 理论上不会到达；防御性兜底
        if last_http_exc is not None:
            raise LLMRateLimitError(
                _format_http_error(last_http_exc, req.full_url),
                should_fallback=True,
                status_code=last_http_exc.code,
            )
        raise LLMError('网络错误: 未能建立连接')

    def _chat_blocking(
        self,
        url: str,
        headers: Dict[str, str],
        body: bytes,
    ) -> Dict[str, Any]:
        """非流式请求：建立连接用 _open_request（含指数退避重试），读取响应。"""
        req = urllib.request.Request(
            url, data=body, headers=headers, method="POST",
        )
        t0 = time.time()
        with self._open_request(req, is_stream=False) as resp:
            raw = resp.read().decode("utf-8")
        if logger.isEnabledFor(10):
            logger.debug(
                'HTTP blocking ok in %.2fs body=%dB',
                time.time() - t0, len(raw),
            )

        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise LLMError("响应解析失败: {}".format(exc))

        # 记录 usage（非流式时直接在 body 里）
        usage = data.get("usage") or {}
        if isinstance(usage, dict):
            self._last_usage = {
                k: int(v) for k, v in usage.items()
                if isinstance(v, (int, float))
            }

        return self._normalize_response(data)

    def _chat_stream(
        self,
        url: str,
        headers: Dict[str, str],
        body: bytes,
        on_delta: Optional[Callable[[str], None]],
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        """SSE 流式读取，按需回调文本片段，最终聚合成统一返回结构。"""
        content_chunks: List[str] = []
        # DeepSeek thinking 模式：reasoning_content 也是分片到达，
        # 必须按 chunk 累积成完整字符串供下一轮回传给 API
        reasoning_chunks: List[str] = []
        # tool_calls 流式分片需要按 index 累积
        tool_buf: Dict[int, Dict[str, Any]] = {}
        finish_reason: Optional[str] = None
        usage_buf: Dict[str, int] = {}

        req = urllib.request.Request(
            url, data=body, headers=headers, method="POST",
        )
        try:
            resp = self._open_request(req, is_stream=True)
        except LLMError:
            raise

        if logger.isEnabledFor(10):
            logger.debug('HTTP stream connected to %s', url)

        cancelled = False
        # 标志：流式正文阶段是否被网络异常中断（用于错误信息和上层处理）
        stream_aborted: Optional[BaseException] = None
        try:
            for line in self._iter_sse_lines(resp, cancel_check):
                # 每行检查一次取消
                if cancel_check is not None:
                    try:
                        if cancel_check():
                            cancelled = True
                            break
                    except Exception:  # pylint: disable=broad-except
                        pass
                if not line or not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                except ValueError:
                    continue

                # 流式 usage（DeepSeek/OpenAI 在最后一个 chunk 给出）
                u = chunk.get("usage")
                if isinstance(u, dict):
                    for k, v in u.items():
                        if isinstance(v, (int, float)):
                            usage_buf[k] = int(v)

                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}

                # 1. 文本增量
                text = delta.get("content")
                if text:
                    content_chunks.append(text)
                    if on_delta is not None:
                        try:
                            on_delta(text)
                        except Exception:  # pylint: disable=broad-except
                            # 回调异常不能影响主流程
                            pass

                # 1.5 推理过程增量（DeepSeek thinking 模式专属）
                # reasoning_content 不投递给 on_delta（不显示给用户），
                # 仅累积起来供下一轮回传 API
                rtext = delta.get("reasoning_content")
                if rtext:
                    reasoning_chunks.append(rtext)

                # 2. tool_calls 增量
                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    slot = tool_buf.setdefault(
                        idx,
                        {"id": "", "name": "", "arguments": ""},
                    )
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    func = tc.get("function") or {}
                    if func.get("name"):
                        slot["name"] = func["name"]
                    if func.get("arguments"):
                        slot["arguments"] += func["arguments"]

                fr = choices[0].get("finish_reason")
                if fr:
                    finish_reason = fr
        except (
            ConnectionAbortedError,
            ConnectionResetError,
            socket.timeout,
            urllib.error.URLError,
            OSError,
        ) as exc:
            # 流式正文阶段被网络中断：不重试（重试会让 LLM 重复输出已发的部分，
            # 而且服务端可能已扣额度）。把已收到的 content/reasoning/tool_calls
            # 当作"被截断的回复"返回，让 worker 拿到部分结果继续。
            # 内容若已有产出则按 length 截断处理，让上层把它视为正常的
            # 短回复结束（不再二次报错）；内容为空才抛错让用户感知问题。
            stream_aborted = exc
            logger.warning(
                'HTTP stream aborted mid-body: %s; '
                'collected content=%d chars reasoning=%d chars tool_buf=%d',
                exc, sum(len(c) for c in content_chunks),
                sum(len(c) for c in reasoning_chunks),
                len(tool_buf),
            )
        finally:
            try:
                resp.close()
            except Exception:  # pylint: disable=broad-except
                pass

        if cancelled:
            raise LLMError("用户取消")

        # 流被中断且未收到任何字节：抛友好错误（让用户知道 LLM 完全没回复）
        if (stream_aborted is not None
                and not content_chunks
                and not reasoning_chunks
                and not tool_buf):
            raise LLMError(_humanize_network_error(stream_aborted))

        # 把累积的 tool_calls.arguments 解析成 dict
        tool_calls: List[Dict[str, Any]] = []
        for _, slot in sorted(tool_buf.items()):
            args_raw = slot["arguments"] or "{}"
            try:
                args = json.loads(args_raw)
            except ValueError:
                args = {"_raw": args_raw}
            tool_calls.append({
                "id": slot["id"],
                "name": slot["name"],
                "arguments": args,
            })

        if usage_buf:
            self._last_usage = usage_buf

        return {
            "content": "".join(content_chunks),
            "reasoning_content": "".join(reasoning_chunks),
            "tool_calls": tool_calls,
            # 被网络中断截断的回复明确标记 length（业务上等同于"被截短"），
            # 上层 worker 可以正常处理已收文本，无需特殊路径。
            "finish_reason": (
                finish_reason
                or ('length' if stream_aborted is not None else 'stop')
            ),
            "usage": dict(usage_buf),
            "raw": None,
        }

    @staticmethod
    def _iter_sse_lines(resp, cancel_check=None) -> Iterator[str]:
        """逐行迭代 SSE 流。

        ``cancel_check`` 每次读完一个 1KB chunk 后会被调用一次；返回
        True 时立即中断并关闭流。这样在 LLM 非常啰嗦的长回复场景下，
        用户点"停止"也能在 1KB 范围内跟手生效。
        """
        buf = b""
        while True:
            if cancel_check is not None:
                try:
                    if cancel_check():
                        return
                except Exception:  # pylint: disable=broad-except
                    pass
            chunk = resp.read(1024)
            if not chunk:
                if buf:
                    yield buf.decode("utf-8", errors="replace")
                return
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                yield line.decode("utf-8", errors="replace").rstrip("\r")

    @staticmethod
    def _normalize_response(data: Dict[str, Any]) -> Dict[str, Any]:
        """把 OpenAI 标准响应展平成统一格式。"""
        choices = data.get("choices") or []
        if not choices:
            return {
                "content": "",
                "reasoning_content": "",
                "tool_calls": [],
                "finish_reason": "stop",
                "usage": data.get("usage") or {},
                "raw": data,
            }
        msg = choices[0].get("message") or {}
        tool_calls = []
        for tc in msg.get("tool_calls") or []:
            func = tc.get("function") or {}
            args_raw = func.get("arguments") or "{}"
            try:
                args = json.loads(args_raw)
            except ValueError:
                args = {"_raw": args_raw}
            tool_calls.append({
                "id": tc.get("id", ""),
                "name": func.get("name", ""),
                "arguments": args,
            })
        return {
            "content": msg.get("content") or "",
            "reasoning_content": msg.get("reasoning_content") or "",
            "tool_calls": tool_calls,
            "finish_reason": choices[0].get("finish_reason", "stop"),
            "usage": data.get("usage") or {},
            "raw": data,
        }


def build_client_from_profile(profile, app_config=None) -> LLMClient:
    """从 LLMProfile 构造客户端。

    若传入 AppConfig，则把全局重试参数注入 LLMClient，保证 profile 级
    配置和全局配置协同生效。
    """
    client_kwargs = {
        "base_url": profile.base_url,
        "api_key": profile.api_key,
        "model": profile.model,
        "timeout": profile.timeout,
        "extra_headers": profile.extra_headers,
    }
    if app_config is not None:
        client_kwargs.update({
            "max_retries": app_config.llm_max_retries,
            "retry_base_delay": app_config.llm_retry_base_delay,
            "retry_max_delay": app_config.llm_retry_max_delay,
            "retryable_status_codes": set(app_config.llm_retryable_status_codes),
        })
    client = LLMClient(**client_kwargs)
    client._profile = profile
    return client
