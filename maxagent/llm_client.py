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
import urllib.error
import urllib.request
from typing import Any
from typing import Callable
from typing import Dict
from typing import Iterator
from typing import List
from typing import Optional


class LLMError(Exception):
    """LLM 调用相关异常。"""


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
    （如 ``https://api.deepseek.com/v1/chat/completions``），
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
    if "/v1" not in lowered and "/v2" not in lowered and "/api" not in lowered:
        return (
            "💡 多数 OpenAI 兼容服务需要带版本路径（如 /v1）。"
            "若遇 401/404 可尝试切换 /v1 后缀"
        )
    return None


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
    ) -> Dict[str, Any]:
        """发起一次 chat completion 调用。

        :param messages: OpenAI 格式的消息列表
        :param tools:    OpenAI tools 列表（function calling）；为 None 表示不开 tools
        :param stream:   是否流式
        :param on_delta: 流式模式下每收到一个文本片段时的回调
        :param cancel_check: 流式期间的取消检查回调，返回 True 时立刻关闭
            连接并抛 ``LLMError('用户取消')``，让 worker 能快速跟手停下来。
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
        payload: Dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        # 流式模式下额外要 usage 字段（DeepSeek/OpenAI 都支持这个 option，
        # 不支持的后端会忽略）
        if stream:
            payload["stream_options"] = {"include_usage": True}

        url = self._base_url + "/chat/completions"
        headers = self._build_headers()
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

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

    def _chat_blocking(
        self,
        url: str,
        headers: Dict[str, str],
        body: bytes,
    ) -> Dict[str, Any]:
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise LLMError(_format_http_error(exc, url))
        except urllib.error.URLError as exc:
            raise LLMError("网络错误: {}".format(exc.reason))

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
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        content_chunks: List[str] = []
        # tool_calls 流式分片需要按 index 累积
        tool_buf: Dict[int, Dict[str, Any]] = {}
        finish_reason: Optional[str] = None
        usage_buf: Dict[str, int] = {}

        try:
            resp = urllib.request.urlopen(req, timeout=self._timeout)
        except urllib.error.HTTPError as exc:
            raise LLMError(_format_http_error(exc, url))
        except urllib.error.URLError as exc:
            raise LLMError("网络错误: {}".format(exc.reason))

        cancelled = False
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
        finally:
            try:
                resp.close()
            except Exception:  # pylint: disable=broad-except
                pass

        if cancelled:
            raise LLMError("用户取消")

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
            "tool_calls": tool_calls,
            "finish_reason": finish_reason or "stop",
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
            "tool_calls": tool_calls,
            "finish_reason": choices[0].get("finish_reason", "stop"),
            "usage": data.get("usage") or {},
            "raw": data,
        }


def build_client_from_profile(profile) -> LLMClient:
    """从 LLMProfile 构造客户端。"""
    return LLMClient(
        base_url=profile.base_url,
        api_key=profile.api_key,
        model=profile.model,
        timeout=profile.timeout,
        extra_headers=profile.extra_headers,
    )
