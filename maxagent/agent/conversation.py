#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多轮对话状态机。

设计目标：
1. 维护一个完整的 messages 列表，符合 OpenAI Chat Completions 协议格式。
2. 提供 add_user / add_assistant / add_tool_result 等便捷方法。
3. 支持序列化到 JSON 以便保存/恢复历史。
4. 支持 token 预算窗口管理（保护 tool_call/tool_result 配对，避免半截裁剪）。
5. 提供"重启对齐"机制：从磁盘恢复后注入提醒，让 LLM 感知场景可能已变。

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

from .coding_rules import get_coding_rules


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
""" + "\n" + get_coding_rules()


# 跨语言字符 → token 的粗略系数（OpenAI tiktoken 实测均值）：
# - 中英文混合: 1 token ≈ 2.0 字符
# - 纯英文/代码: 1 token ≈ 4.0 字符
# 取中庸 2.5 作为通用估算系数（偏保守，实际可能更省）
CHARS_PER_TOKEN = 2.5


def estimate_tokens(text):
    """粗略估算字符串的 token 数。

    通用估算，不依赖任何第三方分词库，所有模型适用。

    :param text: 任意字符串
    :return: 估算 token 数（int）
    """
    if not text:
        return 0
    return int(len(text) / CHARS_PER_TOKEN) + 1


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

    def estimate_tokens(self):
        """估算该消息序列化后的 token 数。"""
        n = estimate_tokens(self.content)
        if self.tool_calls:
            n += estimate_tokens(
                json.dumps(self.tool_calls, ensure_ascii=False),
            )
        # 协议固定字段（role/name/id 等）的固定开销
        n += 4
        return n


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

    def add_system_note(self, content):
        # type: (str) -> Message
        """注入一条中途的 system 角色提示。

        用途：Agent 在循环过程中需要给 LLM 下达元指令（如"请收尾，
        不要再调用工具"），通过 role=system 的额外消息在不污染主对话
        历史的前提下传递。
        """
        msg = Message(role='system', content=content)
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

    def estimate_total_tokens(self):
        """估算当前所有消息（含 system）的 token 总数。"""
        total = estimate_tokens(self.system_prompt)
        for m in self.messages:
            total += m.estimate_tokens()
        return total

    def trim_to_token_budget(self, max_tokens=32000, keep_recent=4):
        """按 token 预算裁剪历史消息，保护 tool_call/tool_result 配对。

        裁剪规则：
        1. 永远保留 system_prompt。
        2. 永远保留最近 ``keep_recent`` 条消息（确保当前轮上下文）。
        3. 从最早的非保护消息开始裁，但**绝不**把 ``assistant(tool_calls=...)``
           和它对应的 ``tool`` 结果分开——要么一起留，要么一起删。
        4. 如果裁完仍超预算（极端情况：单条消息就爆掉），不再继续。

        :param max_tokens: token 预算上限
        :param keep_recent: 保护的最近消息条数
        :return: 实际裁掉的消息条数
        """
        total = self.estimate_total_tokens()
        if total <= max_tokens:
            return 0

        # 计算每条消息的 token 数，便于复用
        msg_tokens = [m.estimate_tokens() for m in self.messages]
        n = len(self.messages)
        if n <= keep_recent:
            return 0

        # 找出 tool_call 配对组：
        # 一个 assistant(tool_calls) 后面紧跟若干 tool 消息组成一组
        # group_end[i] 表示从 i 开始的组结尾索引（含），单条消息时等于 i
        group_end = list(range(n))
        i = 0
        while i < n:
            m = self.messages[i]
            if m.role == 'assistant' and m.tool_calls:
                j = i + 1
                while j < n and self.messages[j].role == 'tool':
                    j += 1
                # i..j-1 是一个完整组
                end = j - 1
                for k in range(i, j):
                    group_end[k] = end
                i = j
            else:
                i += 1

        # 从头开始按组裁，但保护最后 keep_recent 条
        protect_from = max(0, n - keep_recent)
        # 把 protect_from 也按组对齐：如果它落在某个组中间，整个组都要保护
        if protect_from < n:
            # 找包含 protect_from 的组的起点
            head = protect_from
            while head > 0:
                prev = self.messages[head - 1]
                if prev.role == 'assistant' and prev.tool_calls:
                    head -= 1
                    # 但还要看 prev 的前面是不是也有 tool 链 — 实际不会，
                    # 因为 tool 链的发起一定是 assistant，所以 head 已是组首
                    break
                if prev.role == 'tool':
                    head -= 1
                    continue
                break
            protect_from = head

        # 估算 system 开销固定
        sys_tokens = estimate_tokens(self.system_prompt)
        current = sys_tokens + sum(msg_tokens)
        cut_until = 0  # 裁掉 [0, cut_until) 区间

        idx = 0
        while idx < protect_from and current > max_tokens:
            end = group_end[idx]
            if end >= protect_from:
                # 会和保护区交叉，停止裁剪
                break
            # 裁掉 [idx, end] 这一组
            for k in range(idx, end + 1):
                current -= msg_tokens[k]
            cut_until = end + 1
            idx = end + 1

        if cut_until > 0:
            self.messages = self.messages[cut_until:]
        return cut_until

    def trim_to_char_budget(self, max_chars=80000):
        """字符级窗口管理（保留向后兼容，内部转调 token 接口）。

        :param max_chars: 字符预算上限（粗略 ≈ tokens * CHARS_PER_TOKEN）
        """
        max_tokens = int(max_chars / CHARS_PER_TOKEN)
        return self.trim_to_token_budget(max_tokens=max_tokens)

    # ------------------------------------------------------------------ #
    # 重启对齐 / 摘要相关
    # ------------------------------------------------------------------ #
    def has_restored_marker(self):
        """检查首条消息是否已经是"会话恢复"标记。"""
        if not self.messages:
            return False
        first = self.messages[0]
        if first.role != 'system':
            return False
        return '__maxagent_restored__' in (first.content or '')

    def inject_restored_notice(self):
        """会话从磁盘加载后注入"重启对齐"提示。

        让 LLM 感知：上次的对话历史虽然在，但 Max 场景状态可能已变。
        重复调用是幂等的（带标记防止重复注入）。
        """
        if self.has_restored_marker():
            return False
        if not self.messages:
            # 空会话不注入（首次新建场景）
            return False
        notice = (
            '__maxagent_restored__\n'
            '⚠️ 这是从历史会话恢复的对话。注意：\n'
            '1. 你之前的对话内容（包括工具调用）都在历史里，但 3ds Max 场景'
            '可能已被重启或人工修改过。\n'
            '2. 当用户的新需求依赖之前创建的对象时，请先调用 '
            'list_scene_objects 或 get_object_info 验证对象是否仍存在，'
            '不要直接假设场景未变。\n'
            '3. 历史里的 tool_call_id 是上次会话的引用，仅作上下文参考，'
            '不要尝试"撤销"或"继续"那些已完成的操作。\n'
        )
        self.messages.insert(0, Message(role='system', content=notice))
        return True

    def replace_with_summary(self, summary_text, keep_recent=2):
        """用一段摘要替换早期消息，仅保留最近 ``keep_recent`` 条。

        典型用法：长会话超过阈值时，让 LLM 自己生成摘要后调用此方法
        压缩历史。

        :param summary_text: LLM 生成的摘要文本
        :param keep_recent: 保留的最近消息条数
        :return: (compressed: bool, removed_count: int)
        """
        if not summary_text:
            return False, 0
        # 不足 keep_recent + 2 条没必要压缩
        if len(self.messages) <= keep_recent + 1:
            return False, 0

        # 同样要保护尾部 tool_call 组完整性
        protect_from = max(0, len(self.messages) - keep_recent)
        head = protect_from
        while head > 0:
            prev = self.messages[head - 1]
            if prev.role == 'tool':
                head -= 1
                continue
            if prev.role == 'assistant' and prev.tool_calls:
                head -= 1
                break
            break
        protect_from = head

        if protect_from <= 0:
            return False, 0

        removed = protect_from
        summary_msg = Message(
            role='system',
            content=(
                '__maxagent_summary__\n'
                '【历史摘要】以下是早前对话与工具调用的浓缩摘要：\n\n'
                + summary_text.strip()
            ),
        )
        self.messages = [summary_msg] + self.messages[protect_from:]
        return True, removed

    def __len__(self):
        return len(self.messages)
