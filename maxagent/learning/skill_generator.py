#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从成功会话/Macro Recorder 中生成 Skill 草稿。

P0-3 学习与进化：把一次成功执行沉淀为可复用 Skill。
生成器只做"建议"，不直接落盘——最终保存仍需用户或 LLM 调用 save_skill。
"""

from __future__ import absolute_import
from __future__ import print_function

import json
import re
import time
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ..logger import get_logger


logger = get_logger(__name__)


# 工具名 → 自然语言动作描述的轻量映射
_ACTION_VERB_MAP = {
    'create_box': '创建长方体',
    'create_sphere': '创建球体',
    'create_cylinder': '创建圆柱体',
    'create_teapot': '创建茶壶',
    'create_plane': '创建平面',
    'create_cone': '创建圆锥体',
    'create_torus': '创建圆环',
    'create_tube': '创建管状体',
    'create_text': '创建文本',
    'create_circle': '创建圆形',
    'create_rectangle': '创建矩形',
    'create_ellipse': '创建椭圆',
    'create_star': '创建星形',
    'create_arc': '创建弧',
    'create_helix': '创建螺旋线',
    'move_object': '移动对象',
    'rotate_object': '旋转对象',
    'scale_object': '缩放对象',
    'delete_object': '删除对象',
    'clone_object': '复制对象',
    'add_modifier': '添加修改器',
    'remove_modifier': '移除修改器',
    'set_modifier_param': '设置修改器参数',
    'assign_material': '分配材质',
    'create_standard_material': '创建标准材质',
    'set_material_color': '设置材质颜色',
    'create_omni_light': '创建泛光灯',
    'create_target_spot': '创建目标聚光灯',
    'create_target_direct': '创建目标平行光',
    'create_area_light': '创建面光源',
    'create_sun_light': '创建太阳光',
    'set_object_property': '设置对象属性',
    'rename_object': '重命名对象',
    'run_python': '执行 Python 代码',
    'run_maxscript': '执行 MaxScript 代码',
}


def _safe_skill_name(user_input):
    # type: (str) -> str
    """从用户首条消息提炼一个合法的 skill name 候选。"""
    text = (user_input or '').strip()
    if not text:
        return '未命名流程'
    # 去掉标点、控制字符
    text = re.sub(r'[\s\u3000]+', ' ', text)
    text = re.sub(r'[^\w\u4e00-\u9fa5\- ]', '', text)
    text = text.strip()
    if not text:
        return '未命名流程'
    # 取前 20 个字符
    if len(text) > 20:
        text = text[:20]
    return text


def _action_summary(action):
    # type: (Dict[str, Any]) -> str
    """把一条 recorded action 转成一句话描述。"""
    tool = action.get('tool', '')
    args = action.get('args', {})
    verb = _ACTION_VERB_MAP.get(tool, tool)
    name = args.get('name', '')
    if name:
        return '{}: {}'.format(verb, name)
    return verb


def generate_skill_draft(actions, user_input='', session_id=''):
    # type: (List[Dict[str, Any]], str, str) -> Optional[Dict[str, Any]]
    """根据成功会话的操作序列生成 Skill 草稿。

    :param actions: RecordedAction 的 dict 列表（来自 MacroRecorder）
    :param user_input: 用户首条消息，用于生成 skill name / trigger keywords
    :param session_id: 会话 ID，用于追溯来源
    :returns: 包含 skill manifest 和可选 impl_code 的字典；
        如果 actions 为空或全部失败则返回 None
    """
    success_actions = [
        a for a in actions
        if a.get('ok', True) and a.get('tool')
    ]
    if not success_actions:
        return None

    name = _safe_skill_name(user_input)
    # 触发词：取用户输入前 3 个词或 name
    kws = _extract_keywords(user_input)
    if not kws:
        kws = [name]

    steps = []
    for idx, act in enumerate(success_actions, start=1):
        steps.append('{}. {}'.format(idx, _action_summary(act)))

    instructions = (
        '当用户提到触发词时，按以下步骤执行：\n'
        '\n'
        + '\n'.join(steps)
        + '\n\n'
        '执行完成后用 get_object_info / list_scene_objects 复核关键结果。'
    )

    # 生成 impl.py 代码草稿：基于 MacroRecorder 的 Python 脚本映射
    impl_code = _build_impl_code(success_actions)

    return {
        'suggested_name': name,
        'manifest': {
            'name': name,
            'description': '从会话自动生成的流程：' + (user_input or '无标题'),
            'trigger_keywords': kws,
            'instructions': instructions,
            'status': 'draft',
            'source_session_sid': session_id,
        },
        'impl_code': impl_code,
    }


def _extract_keywords(user_input):
    # type: (str) -> List[str]
    """从用户输入提取 1-3 个候选触发词。"""
    text = (user_input or '').strip()
    if not text:
        return []
    # 简单按中文/英文分词
    words = re.findall(r'[\u4e00-\u9fa5]{2,}|[a-zA-Z0-9_\-]+', text)
    # 过滤过短和常见动词
    stop = {'创建', '生成', '给我', '帮忙', '帮', '做', '一个', '一下',
            '请', '把', '让', '需要', '想要', '可以', '能不能'}
    result = [w for w in words if w not in stop and len(w) >= 2]
    # 把整句也作为触发词之一
    if text and len(text) <= 16:
        result.insert(0, text)
    return result[:3]


def _build_impl_code(actions):
    # type: (List[Dict[str, Any]]) -> str
    """基于 MacroRecorder 的 Python 映射生成 impl.py 代码草稿。"""
    lines = [
        '#!/usr/bin/env python3',
        '# -*- coding: utf-8 -*-',
        '"""本文件由 MaxAgent 从成功会话自动生成，仅作草稿。"""',
        '',
        'def run(ctx, **kwargs):',
        '    # type: (dict, dict) -> dict',
        '    """执行 Skill 的代码实现。"""',
        '    dispatcher = ctx.get(\'dispatcher\')',
        '    results = []',
        '',
    ]
    for act in actions:
        tool = act.get('tool', '')
        args = act.get('args', {})
        args_json = json.dumps(args, ensure_ascii=False)
        lines.append(
            '    # {}'.format(_action_summary(act)),
        )
        lines.append(
            '    if dispatcher:',
        )
        lines.append(
            '        results.append(dispatcher.dispatch("{}", {}))'.format(
                tool, args_json,
            ),
        )
        lines.append(
            '    else:',
        )
        lines.append(
            '        results.append({"ok": False, "error": "no dispatcher"})',
        )
        lines.append('')
    lines.extend([
        '    return {',
        '        \'ok\': True,',
        '        \'results\': results,',
        '    }',
        '',
    ])
    return '\n'.join(lines)


def propose_skill_from_recorder(recorder, user_input='', session_id=''):
    # type: (Any, str, str) -> Optional[Dict[str, Any]]
    """从 MacroRecorder 实例提取 actions 并生成 Skill 建议。

    :param recorder: MacroRecorder 实例
    :param user_input: 用户首条消息
    :param session_id: 会话 ID
    """
    if recorder is None:
        return None
    try:
        session = recorder.session
        actions = session.to_dict().get('actions', [])
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning('读取 MacroRecorder 失败: %s', exc)
        return None
    return generate_skill_draft(actions, user_input, session_id)
