#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多轮对话状态机。

设计目标：
1. 维护一个完整的 messages 列表，符合 OpenAI Chat Completions 协议格式。
2. 提供 add_user / add_assistant / add_tool_result 等便捷方法。
3. 支持序列化到 JSON 以便保存/恢复历史。
4. 支持 trim_to_token_limit 简单的窗口管理（按字符近似 token）。

消息角色:
- system: 系统提示词
- user: 用户输入
- assistant: 模型回复（可能含 tool_calls）
- tool: 工具执行结果
"""

from __future__ import absolute_import
from __future__ import print_function

import json
import time
from typing import Any
from typing import Dict
from typing import List
from typing import Optional


# 默认系统提示词，告诉模型自己是 Max agent，使用工具
DEFAULT_SYSTEM_PROMPT = """\
你是 3ds Max 内嵌的智能助手 MaxAgent，专门帮助美术 / TA 通过自然语言操作 \
3ds Max 场景。你可以调用提供给你的工具完成创建几何体、修改对象、添加修改器、\
设置材质灯光、渲染、保存场景等操作。

工作原则:
1. 优先使用预定义的工具完成任务，能用 create_box 就不要用 run_python。
2. 如果用户的需求复杂，预定义工具无法直接满足，再使用 run_maxscript / run_python \
   逃生舱（这两个工具会要求用户确认，是 dangerous 工具）。
3. 操作前若需要了解场景，先调用 list_scene_objects / get_object_info 等查询工具。
4. 每次只调用必要的工具，避免无意义的多余调用。
5. 工具调用失败时，根据返回的错误信息修正参数后重试，最多重试 2 次仍失败时\
   告知用户具体原因。
6. 回答使用简体中文。涉及具体数值（位置 / 尺寸）时，注明单位（Max system unit）。
"""


class Message(object):
    """单条消息。"""

    def __init__(self, role, content=None, tool_calls=None,
                 tool_call_id=None, name=None, ts=None):
        # type: (str, Optional[str], Optional[List[Dict]], Optional[str], Optional[str], Optional[float]) -> None
        self.role = role
        # OpenAI 协议允许 content 为 None（仅当 assistant 只发 tool_calls 时）
        self.content = content
        self.tool_calls = tool_calls
        self.tool_call_id = tool_call_id
        self.name = name
        self.ts = ts if ts is not None else time.time()

    def to_openai_dict(self):
        """转为 OpenAI Chat Completions 协议的 dict。"""
        out = {'role': self.role}
        if self.content is not None:
            out['content'] = self.content
        elif self.role == 'assistant' and self.tool_calls:
            # OpenAI 要求 assistant 消息必须有 content 字段，可以是 None
            out['content'] = None
        else:
            out['content'] = ''
        if self.tool_calls:
            out['tool_calls'] = self.tool_calls
        if self.tool_call_id:
            out['tool_call_id'] = self.tool_call_id
        if self.name:
            out['name'] = self.name
        return out

    def to_json(self):
        """转为持久化用的 dict（含时间戳）。"""
        d = self.to_openai_dict()
        d['ts'] = self.ts
        return d

    @classmethod
    def from_json(cls, data):
        """从持久化 dict 恢复 Message。"""
        return cls(
            role=data.get('role', 'user'),
            content=data.get('content'),
            tool_calls=data.get('tool_calls'),
            tool_call_id=data.get('tool_call_id'),
            name=data.get('name'),
            ts=data.get('ts'),
        )


class Conversation(object):
    """对话历史管理。"""

    def __init__(self, system_prompt=None):
        # type: (Optional[str]) -> None
        self.system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
        self.messages = []  # type: List[Message]

    # ------------------------------------------------------------------ #
    # 增加消息
    # ------------------------------------------------------------------ #
    def add_user(self, content):
        # type: (str) -> Message
        msg = Message(role='user', content=content)
        self.messages.append(msg)
        return msg

    def add_assistant(self, content=None, tool_calls=None):
        # type: (Optional[str], Optional[List[Dict]]) -> Message
        msg = Message(
            role='assistant',
            content=content,
            tool_calls=tool_calls,
        )
        self.messages.append(msg)
        return msg

    def add_tool_result(self, tool_call_id, name, content):
        # type: (str, str, str) -> Message
        msg = Message(
            role='tool',
            content=content,
            tool_call_id=tool_call_id,
            name=name,
        )
        self.messages.append(msg)
        return msg

    # ------------------------------------------------------------------ #
    # 序列化
    # ------------------------------------------------------------------ #
    def to_openai_messages(self):
        """转换为 OpenAI 协议消息数组（带 system 消息开头）。"""
        out = [{'role': 'system', 'content': self.system_prompt}]
        for m in self.messages:
            out.append(m.to_openai_dict())
        return out

    def to_json(self):
        """完整序列化（含 system_prompt 与时间戳）。"""
        return {
            'system_prompt': self.system_prompt,
            'messages': [m.to_json() for m in self.messages],
        }

    @classmethod
    def from_json(cls, data):
        """反序列化。"""
        c = cls(system_prompt=data.get('system_prompt'))
        for d in data.get('messages', []):
            c.messages.append(Message.from_json(d))
        return c

    def save(self, file_path):
        """保存对话到 JSON 文件。"""
        with open(file_path, 'w', encoding='utf-8') as fh:
            json.dump(self.to_json(), fh, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, file_path):
        """从 JSON 文件加载对话。"""
        with open(file_path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        return cls.from_json(data)

    # ------------------------------------------------------------------ #
    # 维护
    # ------------------------------------------------------------------ #
    def clear(self):
        """清空消息（保留 system_prompt）。"""
        self.messages = []

    def trim_to_char_budget(self, max_chars=80000):
        """简单的字符级窗口管理。

        如果消息总长度超过 max_chars（约等于 20k tokens），从最早的非 system
        消息开始裁掉，但会保留消息成对（一对 user + assistant 一起裁）。

        :param max_chars: 字符预算上限
        """
        # 估算当前总长度
        def _msg_chars(msg):
            n = len(msg.content or '')
            if msg.tool_calls:
                n += len(json.dumps(msg.tool_calls, ensure_ascii=False))
            return n

        total = len(self.system_prompt or '')
        for m in self.messages:
            total += _msg_chars(m)

        # 超出则从最早开始裁
        idx = 0
        while total > max_chars and idx < len(self.messages) - 2:
            # 跳过最近 2 条（保住当前轮的上下文）
            removed = self.messages[idx]
            total -= _msg_chars(removed)
            idx += 1
        if idx > 0:
            self.messages = self.messages[idx:]

    def __len__(self):
        return len(self.messages)
