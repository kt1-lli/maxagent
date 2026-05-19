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
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key or ""
        self._model = model
        self._timeout = timeout
        self._extra_headers = dict(extra_headers or {})

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
    ) -> Dict[str, Any]:
        """发起一次 chat completion 调用。

        :param messages: OpenAI 格式的消息列表
        :param tools:    OpenAI tools 列表（function calling）；为 None 表示不开 tools
        :param stream:   是否流式
        :param on_delta: 流式模式下每收到一个文本片段时的回调
        :returns: 一个标准化的 dict：
                  {
                      "content": "文本内容（可能为空）",
                      "tool_calls": [
                          {"id": "...", "name": "...", "arguments": {...}},
                          ...
                      ],
                      "finish_reason": "stop"|"tool_calls"|"length"|...,
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

        url = self._base_url + "/chat/completions"
        headers = self._build_headers()
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        if stream:
            return self._chat_stream(url, headers, body, on_delta)
        return self._chat_blocking(url, headers, body)

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
            detail = exc.read().decode("utf-8", errors="replace")
            raise LLMError("HTTP {}: {}".format(exc.code, detail))
        except urllib.error.URLError as exc:
            raise LLMError("网络错误: {}".format(exc.reason))

        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise LLMError("响应解析失败: {}".format(exc))

        return self._normalize_response(data)

    def _chat_stream(
        self,
        url: str,
        headers: Dict[str, str],
        body: bytes,
        on_delta: Optional[Callable[[str], None]],
    ) -> Dict[str, Any]:
        """SSE 流式读取，按需回调文本片段，最终聚合成统一返回结构。"""
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        content_chunks: List[str] = []
        # tool_calls 流式分片需要按 index 累积
        tool_buf: Dict[int, Dict[str, Any]] = {}
        finish_reason: Optional[str] = None

        try:
            resp = urllib.request.urlopen(req, timeout=self._timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise LLMError("HTTP {}: {}".format(exc.code, detail))
        except urllib.error.URLError as exc:
            raise LLMError("网络错误: {}".format(exc.reason))

        try:
            for line in self._iter_sse_lines(resp):
                if not line or not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                except ValueError:
                    continue

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
            resp.close()

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

        return {
            "content": "".join(content_chunks),
            "tool_calls": tool_calls,
            "finish_reason": finish_reason or "stop",
            "raw": None,
        }

    @staticmethod
    def _iter_sse_lines(resp) -> Iterator[str]:
        """逐行迭代 SSE 流。"""
        buf = b""
        while True:
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
