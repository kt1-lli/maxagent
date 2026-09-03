#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AgentWorker 会话反思/技能推荐/历史压缩的 mixin。

本模块仅提供方法，所有 ``self.*`` 属性由 ``AgentWorker.__init__``
初始化（如 ``_conv`` / ``_llm`` / ``_event_logger`` /
``_macro_recorder`` / ``_config_manager`` / ``_session_id`` 等）。

拆分自 worker.py，行为完全等价。
"""

from __future__ import absolute_import
from __future__ import print_function

import json
import time
from typing import Any
from typing import Dict
from typing import List

from ..learning.skill_generator import propose_skill_from_recorder
from ..llm_client import LLMError
from ..logger import get_logger

logger = get_logger(__name__)


class _ReflectionMixin(object):
    """会话结束时的历史压缩、反思生成、Skill 建议、telemetry 更新。"""

    # ------------------------------------------------------------------ #
    # 对话压缩（方案 B 手动 + 方案 D 自动）
    # ------------------------------------------------------------------ #
    def compress_history(self, keep_recent=2):
        """同步请求 LLM 生成历史摘要并压缩对话。

        通用化做法：发一个独立的非工具、非流式 LLM 调用，让模型读完
        当前所有 messages，输出一段 200-400 字的摘要，再用摘要替换
        早期消息。所有 OpenAI 兼容模型都能完成。

        :param keep_recent: 保留的最近消息条数
        :return: dict 形如 {'ok': bool, 'removed': int, 'summary': str, 'error': str?}
        """
        if len(self._conv) <= keep_recent + 1:
            return {
                'ok': False,
                'removed': 0,
                'summary': '',
                'error': '对话太短，无需压缩',
            }

        # 构造摘要提示词：把当前历史作为输入，要求模型输出纯文本摘要
        summary_instruction = (
            '你正在为一个 DCC AI 助手压缩对话历史。请阅读以下完整对话，'
            '输出一段 200~400 字的中文摘要，要求：\n'
            '1. 保留用户的核心目标和已确立的偏好；\n'
            '2. 保留已成功创建/修改的关键场景对象（名称、关键属性）；\n'
            '3. 保留尚未完成、需要后续跟进的事项；\n'
            '4. 用要点形式列出，不要客套；\n'
            '5. 仅输出摘要正文，不要包含"以下是摘要"等元描述。'
        )

        # 把历史以 user 角色塞给摘要模型，避免它误以为自己就是那个 agent
        history_dump = self._dump_history_for_summary()
        summary_msgs = [
            {'role': 'system', 'content': summary_instruction},
            {
                'role': 'user',
                'content': '【需要压缩的对话历史】\n' + history_dump,
            },
        ]

        try:
            resp = self._llm.chat(
                messages=summary_msgs,
                tools=None,
                stream=False,
            )
        except LLMError as exc:
            return {
                'ok': False, 'removed': 0, 'summary': '',
                'error': '生成摘要失败: {}'.format(exc),
            }
        except Exception as exc:  # pylint: disable=broad-except
            logger.debug('compress_history llm.chat 异常', exc_info=True)
            return {
                'ok': False, 'removed': 0, 'summary': '',
                'error': '生成摘要异常: {}'.format(exc),
            }

        summary = (resp.get('content') or '').strip()
        if not summary:
            return {
                'ok': False, 'removed': 0, 'summary': '',
                'error': '模型未返回摘要内容',
            }

        ok, removed = self._conv.replace_with_summary(
            summary, keep_recent=keep_recent,
        )
        return {
            'ok': ok,
            'removed': removed,
            'summary': summary,
            'error': '' if ok else '可压缩内容不足',
        }

    def _reflect_session(self):
        # type: () -> None
        """会话结束时的最佳努力自动反思。

        收集本轮会话的关键信息，调用 LLM 生成反思建议，并将结果写入
        事件日志。不直接写入长期记忆，而是作为 memory_proposal 等待
        用户确认。所有异常都会被吞掉，不能影响主路径关闭会话。
        """
        session_id = getattr(self, '_session_id', '') or ''
        try:
            # 1. 判断是否有必要反思：过短且无工具调用则跳过
            user_msgs = [
                m for m in self._conv.messages if m.role == 'user'
            ]
            tool_calls = [
                m for m in self._conv.messages
                if m.role == 'assistant' and m.tool_calls
            ]
            if len(user_msgs) <= 1 and not tool_calls:
                logger.debug('会话过短且无工具调用，跳过自动反思')
                return

            # 2. 收集最近 3 条用户输入
            recent_user_inputs = [
                (m.content or '') for m in user_msgs[-3:]
            ]

            # 3. 统计工具调用成功/失败比例
            tool_stats = self._collect_tool_stats()

            # 4. 检测是否使用了 Skill 或收到用户纠正
            has_skill = self._detect_skill_usage()
            has_correction = self._detect_user_correction()

            # 5. 构造反思 prompt
            reflection_prompt = self._build_reflection_prompt(
                recent_user_inputs=recent_user_inputs,
                tool_stats=tool_stats,
                has_skill=has_skill,
                has_correction=has_correction,
            )

            # 6. 调用 LLM 生成反思结果（子线程调用，不阻塞主线程）
            resp = self._llm.chat(
                messages=reflection_prompt,
                tools=None,
                stream=False,
                temperature=0.3,
            )
            reflection_text = ''
            if isinstance(resp, dict):
                reflection_text = resp.get('content', '') or ''
            elif isinstance(resp, str):
                reflection_text = resp

            if not reflection_text:
                return

            # 7. 尝试解析 JSON；如果解析失败，按整段文本作为 summary
            reflection = self._parse_reflection(reflection_text)

            # 8. 更新本轮命中 Skill 的 telemetry
            self._update_skill_telemetry(tool_stats)

            # 9. 写入事件日志
            if self._event_logger is not None:
                self._event_logger.log(
                    'session_reflection',
                    payload=reflection,
                    session_id=session_id,
                )
            logger.info(
                '会话反思完成: summary=%s confidence=%s topic=%s',
                reflection.get('summary', '')[:30],
                reflection.get('confidence', 0),
                reflection.get('topic', ''),
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.debug('自动反思失败（已忽略）: %s', exc)

    def _collect_tool_stats(self):
        # type: () -> Dict[str, Any]
        """统计本轮会话中的工具调用成功/失败情况。"""
        total = 0
        success = 0
        failed = 0
        tool_names = []
        for m in self._conv.messages:
            if m.role != 'tool':
                continue
            total += 1
            tool_names.append(m.name or '')
            try:
                payload = json.loads(m.content or '{}')
                if payload.get('ok'):
                    success += 1
                else:
                    failed += 1
            except (TypeError, ValueError):
                failed += 1
        return {
            'total': total,
            'success': success,
            'failed': failed,
            'tool_names': tool_names,
        }

    def _propose_skill_from_session(self):
        # type: () -> None
        """从本轮 Macro Recorder 生成 Skill 建议并发射信号。

        触发门槛（满足任一即跳过）：
        1. 全局开关 ``enable_skill_proposal`` 未启用（默认关闭）
        2. Macro Recorder 为空
        3. 成功动作数 < ``skill_proposal_min_actions``（默认 3）
        4. 所有动作都是只读/查询类工具（get_* / list_* / find_* 等）
        5. 已存在同名 Skill，或触发词与已有 Skill 高度重叠
        生成结果通过 skill_proposed 信号交给 UI 层确认保存。
        """
        try:
            # 门槛 1：全局开关
            cfg = None
            if self._config_manager is not None:
                cfg = getattr(self._config_manager, 'config', None)
            if cfg is None or not getattr(cfg, 'enable_skill_proposal', False):
                return

            # 门槛 2：recorder 非空
            if self._macro_recorder is None or self._macro_recorder.is_empty():
                return

            # 门槛 3+4：提前统计成功动作，过滤纯查询会话
            try:
                actions = self._macro_recorder.session.to_dict().get(
                    'actions', [],
                )
            except Exception:  # pylint: disable=broad-except
                actions = []
            success_actions = [
                a for a in actions
                if a.get('ok', True) and a.get('tool')
            ]
            min_actions = int(
                getattr(cfg, 'skill_proposal_min_actions', 3) or 3,
            )
            if len(success_actions) < max(1, min_actions):
                return
            # 只读工具前缀：查询类操作不算"值得沉淀的流程"
            readonly_prefixes = (
                'get_', 'list_', 'find_', 'check_', 'query_',
                'build_scene_semantic_graph', 'diff_scene_snapshots',
            )
            has_write = False
            for a in success_actions:
                tool = (a.get('tool') or '')
                if not tool.startswith(readonly_prefixes):
                    has_write = True
                    break
            if not has_write:
                return

            session_id = getattr(self, '_session_id', '') or ''
            proposal = propose_skill_from_recorder(
                self._macro_recorder,
                user_input=self._current_user_input or '',
                session_id=session_id,
            )
            if not proposal:
                return
            manifest = proposal.get('manifest')
            impl_code = proposal.get('impl_code', '')
            if not (manifest and manifest.get('instructions')):
                return

            # 门槛 5：与已有 Skill 去重（同名或触发词交集）
            try:
                from ..skills import SkillManager
                existing = SkillManager().list_all_skills()
            except Exception:  # pylint: disable=broad-except
                existing = []
            new_name = (manifest.get('name') or '').strip().lower()
            new_kws = set(
                (kw or '').strip().lower()
                for kw in (manifest.get('trigger_keywords') or [])
                if kw
            )
            for sk in existing:
                if new_name and new_name == (sk.name or '').strip().lower():
                    logger.debug('Skill 提议已存在同名，跳过: %s', new_name)
                    return
                old_kws = set(
                    (kw or '').strip().lower()
                    for kw in (sk.trigger_keywords or [])
                    if kw
                )
                if new_kws and old_kws and (new_kws & old_kws):
                    logger.debug(
                        'Skill 提议触发词与已有 Skill 重叠，跳过: %s',
                        new_kws & old_kws,
                    )
                    return

            logger.info(
                '生成 Skill 建议: %s (动作数=%d)',
                manifest.get('name'), len(success_actions),
            )
            self.skill_proposed.emit(manifest, impl_code)
        except Exception as exc:  # pylint: disable=broad-except
            logger.debug('生成 Skill 建议失败（已忽略）: %s', exc)

    def _detect_skill_usage(self):
        # type: () -> bool
        """检测本轮会话是否涉及 Skill 调用或学习。"""
        for m in self._conv.messages:
            if m.role != 'user':
                continue
            text = (m.content or '').lower()
            if any(kw in text for kw in ['skill', '技能', '学习', '记住']):
                return True
        # 检查事件日志中是否有 skill 相关事件
        if self._event_logger is not None:
            try:
                events = self._event_logger.search(
                    kind='skill_call', topk=1,
                    start_ts=time.time() - 3600,
                )
                if events:
                    return True
            except Exception:  # pylint: disable=broad-except
                logger.debug('event_logger.search skill_call 异常', exc_info=True)
        return False

    def _update_skill_telemetry(self, tool_stats):
        # type: (Dict[str, Any]) -> None
        """根据本轮会话更新命中 Skill 的运行统计。

        命中规则：任意 user 消息包含某 enabled skill 的 trigger_keyword。
        会话成功结束则 success_count +1；存在工具失败则 fail_count +1。
        仅更新已有 skill，不创建新 skill。
        """
        try:
            from ..skills import SkillManager
            mgr = SkillManager()
            skills = mgr.list_skills()
            if not skills:
                return
            user_texts = [
                (m.content or '').lower()
                for m in self._conv.messages if m.role == 'user'
            ]
            matched = []
            for sk in skills:
                for kw in (sk.trigger_keywords or []):
                    kw_lower = kw.lower()
                    if any(kw_lower in t for t in user_texts):
                        matched.append(sk)
                        break
            if not matched:
                return
            failed = tool_stats.get('failed', 0) > 0
            for sk in matched:
                sk.use_count += 1
                if failed:
                    sk.fail_count += 1
                else:
                    sk.success_count += 1
                sk.updated_at = time.time()
                try:
                    mgr.save(sk, overwrite=True)
                except Exception:  # pylint: disable=broad-except
                    logger.debug('SkillManager.save 失败', exc_info=True)
            logger.debug(
                '更新 Skill telemetry: matched=%d failed=%s',
                len(matched), failed,
            )
        except Exception:  # pylint: disable=broad-except
            logger.debug('_update_skill_telemetry 异常', exc_info=True)

    def _detect_user_correction(self):
        # type: () -> bool
        """检测本轮是否有用户纠正助手的迹象。"""
        correction_keywords = [
            '不对', '错了', '不是', '重新', '改一下', '不是这样',
            '不要', '别', '撤销', '不是这样的', '请重新',
        ]
        for m in self._conv.messages:
            if m.role != 'user':
                continue
            text = (m.content or '').lower()
            if any(kw in text for kw in correction_keywords):
                return True
        return False

    def _build_reflection_prompt(self, recent_user_inputs, tool_stats,
                                 has_skill, has_correction):
        # type: (List[str], Dict[str, Any], bool, bool) -> List[Dict[str, str]]
        """构造生成反思建议的 LLM prompt。"""
        user_inputs_text = '\n'.join(
            '{}. {}'.format(i + 1, t)
            for i, t in enumerate(recent_user_inputs)
        )
        tool_summary = (
            '工具调用总数: {total}，成功: {success}，失败: {failed}，'
            '涉及工具: {tools}'.format(
                total=tool_stats.get('total', 0),
                success=tool_stats.get('success', 0),
                failed=tool_stats.get('failed', 0),
                tools=', '.join(tool_stats.get('tool_names', [])),
            )
        )
        system_msg = (
            '你是一名会话分析助手。请基于以下本轮 DCC AI 助手与'
            '用户的交互信息，生成一段结构化的反思建议，用于决定是否'
            '更新长期记忆。\n'
            '请严格按以下 JSON 格式输出（不要包含 markdown 代码块标记）：\n'
            '{\n'
            '  "summary": "用一句话概括本轮用户的核心需求",\n'
            '  "memory_proposal": "如果观察到值得写入长期记忆的偏好、'
            '习惯或模式，请具体描述；否则写空字符串",\n'
            '  "confidence": 0.0到1.0之间的数字，表示这个建议的可信度,\n'
            '  "topic": "建议写入的 topic 名，如 user-preferences；'
            '若无可写则写空字符串"\n'
            '}\n'
            '注意：只有稳定、跨会话可复用的偏好或工作模式才值得写入'
            '长期记忆；一次性请求或临时表达请返回空 memory_proposal。'
        )
        user_msg = (
            '最近用户输入（由新到旧）：\n{}\n\n{}\n\n'
            '是否使用了 Skill/学习相关表达: {}\n'
            '是否检测到用户纠正: {}\n\n'
            '请生成反思建议。'
        ).format(
            user_inputs_text,
            tool_summary,
            '是' if has_skill else '否',
            '是' if has_correction else '否',
        )
        return [
            {'role': 'system', 'content': system_msg},
            {'role': 'user', 'content': user_msg},
        ]

    def _parse_reflection(self, text):
        # type: (str) -> Dict[str, Any]
        """解析 LLM 返回的反思文本，失败时降级为简单结构。"""
        cleaned = text.strip()
        # 去掉可能的 markdown 代码块
        if cleaned.startswith('```'):
            lines = cleaned.split('\n')
            if lines[0].startswith('```'):
                lines = lines[1:]
            if lines and lines[-1].startswith('```'):
                lines = lines[:-1]
            cleaned = '\n'.join(lines).strip()
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                return {
                    'summary': str(parsed.get('summary', '')),
                    'memory_proposal': str(parsed.get('memory_proposal', '')),
                    'confidence': float(parsed.get('confidence', 0.0) or 0.0),
                    'topic': str(parsed.get('topic', '')),
                }
        except (TypeError, ValueError):
            pass
        # 兜底：把整段文本作为 summary
        return {
            'summary': cleaned,
            'memory_proposal': '',
            'confidence': 0.0,
            'topic': '',
        }

    def _dump_history_for_summary(self):
        """把当前 messages dump 成易读文本，供摘要 prompt 使用。"""
        lines = []
        for m in self._conv.messages:
            role = m.role
            if role == 'user':
                lines.append('[用户] ' + (m.content or ''))
            elif role == 'assistant':
                if m.tool_calls:
                    names = []
                    for tc in m.tool_calls:
                        fn = (tc.get('function') or {})
                        names.append(fn.get('name') or '?')
                    lines.append(
                        '[助手] (调用工具: {}) {}'.format(
                            ', '.join(names), m.content or '',
                        ),
                    )
                else:
                    lines.append('[助手] ' + (m.content or ''))
            elif role == 'tool':
                # 工具结果可能很长，截断一下
                content = m.content or ''
                if len(content) > 300:
                    content = content[:300] + '...(truncated)'
                lines.append(
                    '[工具结果 {}] {}'.format(m.name or '?', content),
                )
            elif role == 'system':
                # 中途 system note 也写入摘要上下文
                content = m.content or ''
                if len(content) > 200:
                    content = content[:200] + '...'
                lines.append('[系统提示] ' + content)
        return '\n'.join(lines)
