#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""技能（Skill / Playbook）管理。

Skill 与"工具（Tool）"的区别：
- Tool 是 Python 函数，对应一次具体的 pymxs 调用（如 ``create_box``）。
- Skill 是一段"自然语言流程描述 + 触发关键词"，存为 JSON。
  当用户的输入命中触发关键词时，把 Skill 的详细 instructions
  注入到 LLM 的 system prompt，让 LLM 按照流程调用既有工具。

为什么不让 Skill 也写代码？
1. 安全：Skill 不执行任何代码，纯文本指令，没有 RCE 风险。
2. 灵活：LLM 可以根据当前场景情况调整步骤，而不是死板按脚本走。
3. 复用：Skill 直接复用已有的 ToolDispatcher，无需独立运行时。

文件位置::

    {config_dir}/skills/<skill_name>.json
    {config_dir}/skills/_index.json

数据结构::

    {
      "name": "标准导出",
      "description": "把当前选中物体烘焙后导出为 FBX，按命名规范处理",
      "trigger_keywords": ["标准导出", "导出fbx"],
      "instructions": "1. 检查选中物体...\n2. ...",
      "created_at": 1700000000.0,
      "updated_at": 1700000000.0,
      "use_count": 3,
      "source_session_sid": "abc12345"
    }
"""

from __future__ import absolute_import
from __future__ import print_function

import json
import os
import re
import time
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from .config import get_config_dir
from .dcc.runtime import current_dcc
from .logger import get_logger
from .shared_resources import ConflictResolution
from .shared_resources import get_shared_subdir
from .shared_resources import guard_shared_write
from .shared_resources import list_shared_files
from .shared_resources import SharedConflictResolver
from .shared_resources import SHARED_NAME_PREFIX


SKILLS_DIRNAME = 'skills'
INDEX_FILENAME = '_index.json'

# 技能语义索引名（复用 KnowledgeIndex，存储在 {config_dir}/knowledge/skills.idx.json）
_SEMANTIC_INDEX_NAME = 'skills'

logger = get_logger(__name__)

# Skill 名字校验：只允许中英文 + 数字 + 下划线 + 短横线 + 空格
_NAME_RE = re.compile(r'^[\w\u4e00-\u9fa5\- ]{1,32}$')

# Skill 生命周期状态
_VALID_STATUSES = ('draft', 'beta', 'stable', 'deprecated')

# 单个 instructions 最大长度（防止 LLM 写出过长指令污染 prompt）
MAX_INSTRUCTIONS_CHARS = 4000

# 在 system prompt 中注入"已学技能"摘要时每条 desc 最大字符数
MAX_BRIEF_DESC_CHARS = 80


def get_skills_dir():
    base = get_config_dir()
    path = os.path.join(base, SKILLS_DIRNAME)
    if not os.path.isdir(path):
        os.makedirs(path)
    return path


def _safe_filename(name):
    """把 skill name 转为安全的文件名片段。"""
    cleaned = re.sub(r'[^\w\u4e00-\u9fa5\-]+', '_', name).strip('_')
    return cleaned[:48] or 'skill'


def _normalize_trigger_keywords(raw):
    """标准化 trigger_keywords 输入，处理 LLM/用户常见传参错误。

    真实场景 LLM 常常把关键词以字符串形式传入而非 list，直接 ``list(str)``
    会**逐字拆分**（例如 "布置studio" -> ['布','置','s','t','u','d','i','o']），
    导致触发匹配完全失效——这是长期存在的用户反馈 bug。

    本函数按以下顺序尝试解析：
    1. None / 空 → []
    2. list / tuple → 逐项 str + strip，跳过空项
    3. 字符串 → 按 JSON 数组、逗号（含中文逗号）、分号、竖线、换行等常见
       分隔符切分；若无分隔符则整体视为单一关键词
    """
    if raw is None:
        return []
    # list / tuple：直接过滤空项
    if isinstance(raw, (list, tuple)):
        out = []
        for item in raw:
            if item is None:
                continue
            s = str(item).strip()
            if s:
                out.append(s)
        return out
    # 字符串：多种解析策略
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        # 尝试 JSON 数组（LLM 有时会传 '["a", "b"]'）
        if s.startswith('[') and s.endswith(']'):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    return _normalize_trigger_keywords(parsed)
            except (ValueError, TypeError):
                pass
        # 按常见分隔符切（中文逗号、英文逗号、分号、竖线、斜杠、换行）
        parts = re.split(r'[,，;；\|/\n\r\t]+', s)
        parts = [p.strip() for p in parts if p and p.strip()]
        if len(parts) >= 1:
            return parts
        return [s]
    # 其它类型：退化为字符串再走一次
    try:
        return _normalize_trigger_keywords(str(raw))
    except Exception:  # pylint: disable=broad-except
        return []


class Skill(object):
    """一个 Skill 实例。

    Skill 现在支持两种形态：
    - 声明式：纯 instructions + trigger_keywords，安全、可解释
    - 过程式：同目录下存在 ``{safe_name}.impl.py``，可被专家模式调用
    """

    def __init__(self, name, description='', trigger_keywords=None,
                 instructions='', created_at=None, updated_at=None,
                 use_count=0, source_session_sid='', file_path=None,
                 impl_path=None, status='stable', patches=None,
                 shared=False, readonly=False, dcc=None):
        # type: (str, str, Optional[List[str]], str, Optional[float], Optional[float], int, str, Optional[str], Optional[str], str, Optional[List[Dict]], bool, bool, Optional[List[str]]) -> None
        self.name = name
        self.description = description or ''
        self.trigger_keywords = _normalize_trigger_keywords(trigger_keywords)
        self.instructions = instructions or ''
        now = time.time()
        self.created_at = float(created_at if created_at is not None else now)
        self.updated_at = float(updated_at if updated_at is not None else now)
        self.use_count = int(use_count)
        self.source_session_sid = source_session_sid or ''
        self.file_path = file_path
        self.impl_path = impl_path
        # 生命周期状态：draft / beta / stable / deprecated
        self.status = status if status in _VALID_STATUSES else 'stable'
        # 运行统计：成功/失败次数
        self.success_count = 0
        self.fail_count = 0
        # 补丁列表：每次用户/Agent 对参数的修正建议
        self.patches = list(patches) if patches else []
        # 共享资源标记：来自共享目录 / 对当前实例只读
        self.shared = bool(shared)
        self.readonly = bool(readonly)
        # DCC 适用范围：['3dsmax'] / ['maya'] / ['3dsmax', 'maya'] / None(通用)
        self.dcc = self._normalize_dcc(dcc)

    @staticmethod
    def _normalize_dcc(dcc):
        # type: (Any) -> Optional[List[str]]
        """标准化 dcc 字段为字符串列表或 None（通用）。"""
        if dcc is None or dcc == []:
            return None
        if isinstance(dcc, str):
            return [dcc.strip()]
        if isinstance(dcc, (list, tuple)):
            out = [str(x).strip() for x in dcc if x is not None and str(x).strip()]
            return out if out else None
        return None

    @staticmethod
    def _is_compatible(skill_dcc, current):
        # type: (Optional[List[str]], str) -> bool
        """判断 skill_dcc 是否与当前 DCC 兼容。

        - skill_dcc 为 None / [] 时视为通用，任何 DCC 都兼容
        - current 为 'unknown' 时：只要 skill_dcc 包含 3dsmax/maya 中任一，
          就放行（测试/未知环境保留资产可见性）
        """
        if not skill_dcc:
            return True
        normalized = set(str(x).strip().lower() for x in skill_dcc)
        if current == 'unknown':
            return bool(normalized & {'3dsmax', 'maya'})
        return current.lower() in normalized

    def to_dict(self):
        return {
            'name': self.name,
            'description': self.description,
            'trigger_keywords': list(self.trigger_keywords),
            'instructions': self.instructions,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'use_count': self.use_count,
            'source_session_sid': self.source_session_sid,
            'file_path': self.file_path,
            'impl_path': self.impl_path,
            'status': self.status,
            'success_count': self.success_count,
            'fail_count': self.fail_count,
            'patches': list(self.patches),
            'shared': self.shared,
            'readonly': self.readonly,
            'dcc': list(self.dcc) if self.dcc else None,
        }

    @classmethod
    def from_dict(cls, data):
        s = cls(
            name=data.get('name', ''),
            description=data.get('description', ''),
            trigger_keywords=data.get('trigger_keywords') or [],
            instructions=data.get('instructions', ''),
            created_at=data.get('created_at'),
            updated_at=data.get('updated_at'),
            use_count=int(data.get('use_count', 0) or 0),
            source_session_sid=data.get('source_session_sid', ''),
            file_path=data.get('file_path'),
            impl_path=data.get('impl_path'),
            status=data.get('status', 'stable'),
            patches=data.get('patches') or [],
            shared=bool(data.get('shared', False)),
            readonly=bool(data.get('readonly', False)),
            dcc=data.get('dcc'),
        )
        s.success_count = int(data.get('success_count', 0) or 0)
        s.fail_count = int(data.get('fail_count', 0) or 0)
        return s

    def has_impl(self):
        # type: () -> bool
        """本 Skill 是否包含可执行代码实现。"""
        if not self.impl_path:
            return False
        return os.path.isfile(self.impl_path)

    def load_impl(self):
        # type: () -> Any
        """加载 impl.py 并返回入口 run 函数。

        加载规则：
        - impl.py 必须存在且与同目录 skill json 同名
        - 文件中必须定义 ``run(ctx, **kwargs)`` 函数
        - 每次调用都重新加载，支持热更新；加载失败抛出异常
        """
        if not self.has_impl():
            raise RuntimeError('Skill 没有实现文件: {}'.format(self.name))
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'maxagent_skill_impl_{}'.format(_safe_filename(self.name)),
            self.impl_path,
        )
        if spec is None or spec.loader is None:
            raise RuntimeError('无法加载 impl 文件: {}'.format(self.impl_path))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        run = getattr(module, 'run', None)
        if run is None or not callable(run):
            raise RuntimeError('impl.py 缺少 run(ctx, **kwargs) 入口')
        return run

    def dcc_tag(self):
        """返回用于 UI 展示的 DCC 标签文本。"""
        if not self.dcc:
            return '[通用]'
        return '[{}]'.format('/'.join(self.dcc))

    def brief(self):
        """简短一行描述，给 system prompt 用。"""
        desc = self.description.strip().replace('\n', ' ')
        if len(desc) > MAX_BRIEF_DESC_CHARS:
            desc = desc[:MAX_BRIEF_DESC_CHARS] + '...'
        kws = ' / '.join(self.trigger_keywords[:3]) if self.trigger_keywords else ''
        status_tag = ''
        if self.status != 'stable':
            status_tag = '[{}]'.format(self.status)
        impl_tag = '[code]' if self.has_impl() else ''
        dcc_tag = self.dcc_tag()
        tags = ' '.join(filter(None, [dcc_tag, status_tag, impl_tag]))
        if tags:
            tags = ' ' + tags
        if kws:
            return '- {name}{tags}（触发词: {kw}）：{desc}'.format(
                name=self.name, tags=tags, kw=kws, desc=desc,
            )
        return '- {name}{tags}：{desc}'.format(
            name=self.name, tags=tags, desc=desc,
        )


class SkillManager(object):
    """技能 CRUD + 触发匹配。"""

    def __init__(self, base_dir=None, config_dir=None):
        # type: (Optional[str], Optional[str]) -> None
        self._base = base_dir or get_skills_dir()
        self._config_dir = config_dir or os.path.dirname(self._base)
        if not os.path.isdir(self._base):
            os.makedirs(self._base)

    # ------------------------------------------------------------------ #
    # 路径
    # ------------------------------------------------------------------ #
    def _index_path(self):
        return os.path.join(self._base, INDEX_FILENAME)

    def _file_path_for(self, skill):
        # type: (Skill) -> str
        if skill.file_path:
            # 共享只读资源禁止通过 save 写回共享目录
            guard_shared_write(skill.file_path)
            if os.path.dirname(skill.file_path) == self._base:
                return skill.file_path
        return os.path.join(
            self._base, '{}.json'.format(_safe_filename(skill.name)),
        )

    def _impl_path_for(self, skill):
        # type: (Skill) -> str
        """根据 skill 名推断同目录 impl.py 路径。"""
        base = os.path.splitext(self._file_path_for(skill))[0]
        return base + '.impl.py'

    def _impl_path_for_shared(self, shared_json_path):
        # type: (str) -> str
        """根据共享 skill json 路径推断同目录 impl.py 路径。"""
        if not shared_json_path:
            return ''
        return os.path.splitext(shared_json_path)[0] + '.impl.py'

    def _attach_impl_path(self, skill):
        # type: (Skill) -> None
        """如果同目录存在 impl.py，则自动绑定到 skill。"""
        if not skill.file_path:
            return
        impl_path = os.path.splitext(skill.file_path)[0] + '.impl.py'
        if os.path.isfile(impl_path):
            skill.impl_path = impl_path
        else:
            skill.impl_path = None

    def _conflict_resolver(self):
        # type: () -> SharedConflictResolver
        """返回与当前 SkillManager 同 config_dir 的冲突解决器。"""
        return SharedConflictResolver(config_dir=self._config_dir)

    # ------------------------------------------------------------------ #
    # 共享资源合并
    # ------------------------------------------------------------------ #
    def _scan_shared_skills(self):
        # type: () -> List[Skill]
        """扫描共享目录中的 skill，返回未经过禁用过滤的 Skill 列表。"""
        shared_dir = get_shared_subdir('skills')
        if not shared_dir or not os.path.isdir(shared_dir):
            return []
        out = []
        resolver = self._conflict_resolver()
        for full in list_shared_files('skills', '.json'):
            fname = os.path.basename(full)
            try:
                with open(full, 'r', encoding='utf-8') as fh:
                    data = json.load(fh)
                s = Skill.from_dict(data)
                s.file_path = full
                # 共享 skill 的 impl.py 也放在共享目录
                impl_path = self._impl_path_for_shared(full)
                if os.path.isfile(impl_path):
                    s.impl_path = impl_path
                else:
                    s.impl_path = None
                # 标记为共享只读资产
                s.shared = True
                s.readonly = True
                # 同步更新冲突记录中的路径信息（用于 UI 展示和诊断）
                rec = resolver.get(s.name, 'skills')
                if rec is not None:
                    rec.shared_path = full
                    resolver.set(
                        s.name, 'skills', rec.resolution,
                        shared_path=full, local_path=rec.local_path,
                        confirmed=rec.confirmed,
                    )
                out.append(s)
            except (OSError, ValueError) as exc:
                logger.warning(
                    'skip 损坏的共享 skill 文件 %s: %s', fname, exc,
                )
        return out

    def _merge_local_and_shared(self, local_skills, shared_skills):
        # type: (List[Skill], List[Skill]) -> List[Skill]
        """合并本地与共享 skill，按冲突解决记录处理同名资产。

        处理策略：
        - use_shared（默认）：使用共享版本，本地版本对 LLM 不可见
        - use_local：使用本地版本，忽略共享版本
        - keep_both：同时保留，共享版本改名为 shared_<name>
        - overwrite_local：共享版本覆盖本地文件（写入本地目录）
        """
        if not shared_skills:
            return list(local_skills)
        resolver = self._conflict_resolver()
        local_map = {s.name: s for s in local_skills}
        shared_map = {s.name: s for s in shared_skills}
        out_map = dict(local_map)
        # 先记录所有存在本地同名资产的共享项
        for s in shared_skills:
            if s.name in local_map:
                rec = resolver.get(s.name, 'skills')
                if rec is None:
                    rec = ConflictResolution(
                        name=s.name,
                        asset_type='skills',
                        resolution='use_shared',
                        shared_path=s.file_path or '',
                        local_path=local_map[s.name].file_path or '',
                        confirmed=False,
                    )
                    resolver.set(
                        s.name, 'skills', 'use_shared',
                        shared_path=s.file_path or '',
                        local_path=local_map[s.name].file_path or '',
                        confirmed=False,
                    )
        for s in shared_skills:
            rec = resolver.get(s.name, 'skills')
            resolution = rec.resolution if rec else 'use_shared'
            if s.name not in local_map:
                # 纯共享资产：直接挂载
                out_map[s.name] = s
                continue
            local_path = local_map[s.name].file_path or ''
            shared_path = s.file_path or ''
            if resolution == 'use_local':
                # 显式保留本地，共享不可见
                continue
            if resolution == 'keep_both':
                # 保留本地，共享版本重命名后一起出现
                renamed = SHARED_NAME_PREFIX + s.name
                s.name = renamed
                s.file_path = shared_path
                out_map[renamed] = s
                continue
            if resolution == 'overwrite_local':
                # 把共享版本拷贝到本地目录（保留共享原始文件只读）
                try:
                    self._copy_shared_to_local(s, local_path)
                except (OSError, ValueError) as exc:
                    logger.warning(
                        '共享 skill 覆盖本地失败 %s: %s', s.name, exc,
                    )
                # 覆盖后仍使用本地扫描结果
                continue
            # 默认 use_shared：共享版本优先，本地版本对 LLM 不可见
            out_map[s.name] = s
        return list(out_map.values())

    def _copy_shared_to_local(self, shared_skill, local_path):
        # type: (Skill, str) -> None
        """把共享 skill 内容写入本地路径（用于 overwrite_local）。"""
        if not local_path:
            local_path = os.path.join(
                self._base, '{}.json'.format(
                    _safe_filename(shared_skill.name),
                ),
            )
        guard_shared_write(local_path)
        tmp = local_path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(shared_skill.to_dict(), fh, ensure_ascii=False, indent=2)
        if os.path.exists(local_path):
            os.replace(tmp, local_path)
        else:
            os.rename(tmp, local_path)
        # 同步拷贝 impl.py（如存在）
        src_impl = shared_skill.impl_path or ''
        if src_impl and os.path.isfile(src_impl):
            dst_impl = os.path.splitext(local_path)[0] + '.impl.py'
            guard_shared_write(dst_impl)
            import shutil
            shutil.copy2(src_impl, dst_impl)

    # ------------------------------------------------------------------ #
    # 索引（损坏时扫描重建）
    # ------------------------------------------------------------------ #
    def _filter_by_dcc(self, skills):
        # type: (List[Skill]) -> List[Skill]
        """按当前 DCC 过滤 skill 列表。"""
        current = current_dcc()
        return [s for s in skills if Skill._is_compatible(s.dcc, current)]

    def _scan(self):
        # type: () -> List[Skill]
        out = []
        if not os.path.isdir(self._base):
            return out
        # 拉取一次禁用名单（O(1) 集合查询）；模块异常时按空集处理。
        try:
            from .disabled_registry import get_disabled_skills_set
            disabled = get_disabled_skills_set()
        except Exception:  # pylint: disable=broad-except
            disabled = set()
        local_skills = []
        for fname in os.listdir(self._base):
            if fname == INDEX_FILENAME or not fname.endswith('.json'):
                continue
            full = os.path.join(self._base, fname)
            try:
                with open(full, 'r', encoding='utf-8') as fh:
                    data = json.load(fh)
                s = Skill.from_dict(data)
                # 自动绑定同目录 impl.py
                self._attach_impl_path(s)
                s.shared = False
                s.readonly = False
                local_skills.append(s)
            except (OSError, ValueError) as exc:
                logger.warning(
                    'skip 损坏的 skill 文件 %s: %s', fname, exc,
                )
        shared_skills = self._scan_shared_skills()
        merged = self._merge_local_and_shared(local_skills, shared_skills)
        # 按当前 DCC 过滤
        merged = self._filter_by_dcc(merged)
        for s in merged:
            # 禁用项对 LLM 完全不可见——既不进 system prompt，也不
            # 出现在 list_skills 工具的返回；但仍然保留磁盘文件，
            # 用户在「我的资源」面板能看到并随时启用。
            if s.name in disabled:
                continue
            if s.file_path is None:
                s.file_path = self._file_path_for(s)
            out.append(s)
        out.sort(key=lambda s: s.updated_at, reverse=True)
        return out

    # ------------------------------------------------------------------ #
    # 语义召回（BM25）
    # ------------------------------------------------------------------ #
    def _semantic_index(self):
        # type: () -> Any
        """获取或创建技能语义索引（懒加载，失败时返回 None）。"""
        try:
            from .knowledge.index import KnowledgeIndex
            idx = KnowledgeIndex(_SEMANTIC_INDEX_NAME)
            idx.load()
            return idx
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning('初始化技能语义索引失败: %s', exc)
            return None

    @staticmethod
    def _skill_to_document(skill):
        # type: (Skill) -> str
        """把 skill 序列化为 BM25 可检索文本。

        文本包含：名称、描述、触发关键词、instructions 摘要。
        不直接塞完整 instructions（太长会稀释关键词权重）。
        """
        parts = [skill.name, skill.description]
        parts.extend(skill.trigger_keywords)
        instructions = skill.instructions.strip()
        # 取 instructions 前 600 字符作为语义匹配依据
        parts.append(instructions[:600])
        return '\n'.join(parts)

    def _rebuild_semantic_index(self):
        # type: () -> None
        """全量重建技能语义索引。"""
        idx = self._semantic_index()
        if idx is None:
            return
        try:
            from .knowledge.bm25 import BM25Index
            from .knowledge.index import KnowledgeIndex
            bm = BM25Index()
            for s in self.list_all_skills():
                if not s.name:
                    continue
                bm.add_document(
                    doc_id=s.name,
                    text=self._skill_to_document(s),
                    meta={
                        'name': s.name,
                        'description': s.description,
                        'trigger_keywords': list(s.trigger_keywords),
                    },
                )
            bm.finalize()
            idx._bm25 = bm  # pylint: disable=protected-access
            idx._built_at = time.time()  # pylint: disable=protected-access
            idx._built_fingerprints = {'skills': 'all'}  # pylint: disable=protected-access
            idx.save()
            logger.info(
                '技能语义索引重建完成: docs=%d', bm.n_docs,
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning('重建技能语义索引失败: %s', exc)

    def _semantic_search(self, user_input, topk=2):
        # type: (str, int) -> List[Skill]
        """基于 BM25 语义召回相关 skill。

        仅当已存在持久化索引或需要时重建；无 skill 时返回空列表。
        """
        if not user_input or not user_input.strip():
            return []
        idx = self._semantic_index()
        if idx is None:
            return []
        # 如果没有索引或索引为空，尝试重建一次
        if idx._bm25.n_docs == 0:  # pylint: disable=protected-access
            self._rebuild_semantic_index()
        try:
            hits = idx.search(user_input, topk=topk, auto_rebuild=False)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning('技能语义检索失败: %s', exc)
            return []
        # 过滤过低相关度的结果，避免误召回
        threshold = 0.15
        names = [
            h['meta'].get('name')
            for h in hits
            if h.get('score', 0.0) >= threshold and h.get('meta', {}).get('name')
        ]
        out = []
        for name in names:
            s = self.get(name)
            if s is not None:
                out.append(s)
        return out

    # ------------------------------------------------------------------ #
    # 公共 API（语义索引维护钩子）
    # ------------------------------------------------------------------ #
    def list_skills(self):
        # type: () -> List[Skill]
        """列出当前 DCC 下可用的技能（不含禁用项）。"""
        return self._scan()

    def list_all_skills(self):
        # type: () -> List[Skill]
        """列出所有技能（**含被禁用 + 共享技能**），仅供管理 UI 使用。

        与 ``list_skills`` 的差异：``list_skills`` 走 ``_scan`` 会过滤
        掉禁用项（保证 LLM 看不到）并处理冲突；本方法绕过过滤直接读盘，
        让设置面板能展示完整清单并提供"启用"开关。
        """
        out = []
        if not os.path.isdir(self._base):
            return out
        local_skills = []
        for fname in os.listdir(self._base):
            if fname == INDEX_FILENAME or not fname.endswith('.json'):
                continue
            full = os.path.join(self._base, fname)
            try:
                with open(full, 'r', encoding='utf-8') as fh:
                    data = json.load(fh)
                s = Skill.from_dict(data)
                self._attach_impl_path(s)
                s.shared = False
                s.readonly = False
                s.file_path = full
                local_skills.append(s)
            except (OSError, ValueError) as exc:
                logger.warning(
                    'skip 损坏的 skill 文件 %s: %s', fname, exc,
                )
        shared_skills = self._scan_shared_skills()
        merged = self._merge_local_and_shared(local_skills, shared_skills)
        # 管理面板也按当前 DCC 过滤，避免用户在 Max 里看到 Maya 专用 skill
        merged = self._filter_by_dcc(merged)
        for s in merged:
            if s.file_path is None:
                s.file_path = self._file_path_for(s)
            out.append(s)
        out.sort(key=lambda s: s.updated_at, reverse=True)
        return out

    def get(self, name):
        # type: (str) -> Optional[Skill]
        for s in self._scan():
            if s.name == name:
                return s
        return None

    def save(self, skill, overwrite=True, rebuild_semantic_index=True):
        # type: (Skill, bool, bool) -> Skill
        """保存（创建或更新）一个 Skill。

        :param overwrite: 同名时是否覆盖；False 且已存在则抛 ValueError
        :param rebuild_semantic_index: 是否重建语义索引；
            use_count 等统计字段更新时可设为 False 避免无意义重建
        """
        # 校验
        if not skill.name or not _NAME_RE.match(skill.name):
            raise ValueError(
                '技能名只能包含中英文/数字/下划线/短横线/空格，'
                '长度1-32: {}'.format(skill.name),
            )
        if not skill.instructions.strip():
            raise ValueError('技能 instructions 不能为空')
        if len(skill.instructions) > MAX_INSTRUCTIONS_CHARS:
            raise ValueError(
                '技能 instructions 过长（{}字符 > 上限 {}）'.format(
                    len(skill.instructions), MAX_INSTRUCTIONS_CHARS,
                ),
            )
        if skill.status and skill.status not in _VALID_STATUSES:
            raise ValueError(
                '技能状态非法: {}，可选: {}'.format(
                    skill.status, ', '.join(_VALID_STATUSES),
                ),
            )
        if not overwrite and self.get(skill.name) is not None:
            raise ValueError('同名技能已存在: {}'.format(skill.name))

        # 未指定 dcc 时，默认绑定到当前 DCC
        if skill.dcc is None:
            current = current_dcc()
            if current in ('3dsmax', 'maya'):
                skill.dcc = [current]

        skill.updated_at = time.time()
        if not skill.created_at:
            skill.created_at = skill.updated_at
        path = self._file_path_for(skill)
        skill.file_path = path
        # 若已有同目录 impl.py 则自动关联
        self._attach_impl_path(skill)
        # 共享只读资源禁止写入
        guard_shared_write(path)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(skill.to_dict(), fh, ensure_ascii=False, indent=2)
        if os.path.exists(path):
            os.replace(tmp, path)
        else:
            os.rename(tmp, path)
        # skill 内容变化后重建语义索引；纯统计更新可跳过
        if rebuild_semantic_index:
            self._rebuild_semantic_index()
        return skill

    def delete(self, name):
        # type: (str) -> bool
        s = self.get(name)
        if s is None:
            return False
        if s.file_path and os.path.exists(s.file_path):
            try:
                guard_shared_write(s.file_path)
                os.remove(s.file_path)
            except (OSError, PermissionError) as exc:
                logger.warning('删除 skill 失败: %s', exc)
                return False
        # 删除后重建语义索引
        self._rebuild_semantic_index()
        return True

    def increment_use_count(self, name):
        # type: (str) -> None
        s = self.get(name)
        if s is None:
            return
        s.use_count += 1
        s.updated_at = time.time()
        try:
            self.save(s, rebuild_semantic_index=False)
        except (OSError, ValueError):
            pass

    # ------------------------------------------------------------------ #
    # Prompt 注入（叠加语义召回）
    # ------------------------------------------------------------------ #
    def build_system_prompt_addon(self, user_input=None):
        # type: (Optional[str]) -> str
        """根据当前已学技能生成可拼到 system prompt 的附加文本。

        策略：
        - 始终列出已学技能的 ``- name（触发词）：description`` 简介
        - 如果 user_input 命中某个 skill 的 trigger_keywords，把该 skill 的
          完整 instructions 也注入（让 LLM 按部就班执行）
        """
        skills = self._scan()
        if not skills:
            return ''
        lines = ['', '## 你已经学会的技能（Skills）']
        for s in skills:
            lines.append(s.brief())
        lines.append('')
        lines.append(
            '当用户的请求与某个技能的描述或触发词相符时，'
            '请按照该技能的 instructions 执行；'
            '如果技能标记了 [code] 且用户明确要求执行该技能代码，'
            '请使用 run_skill_code 工具调用其代码实现（该工具为 dangerous，'
            '需要用户确认）；'
            '如果没有完全匹配的技能，按用户的具体要求处理即可。'
        )

        # 命中触发词时把完整 instructions 注入
        matched = []
        matched_ids = set()  # type: Any
        if user_input:
            ui_lower = user_input.lower()
            for s in skills:
                for kw in s.trigger_keywords:
                    if not kw:
                        continue
                    if kw.lower() in ui_lower:
                        matched.append(s)
                        matched_ids.add(s.name)
                        break

            # 关键词未命中或命中不足时，叠加 BM25 语义召回
            if user_input.strip():
                semantic_hits = self._semantic_search(user_input, topk=2)
                for s in semantic_hits:
                    if s.name not in matched_ids:
                        matched.append(s)
                        matched_ids.add(s.name)

        if matched:
            lines.append('')
            lines.append('## 当前用户输入命中的技能详细流程')
            for s in matched:
                lines.append('')
                lines.append('### 技能：{}'.format(s.name))
                if s.description:
                    lines.append('描述：{}'.format(s.description))
                lines.append('')
                lines.append(s.instructions.strip())

        return '\n'.join(lines)


__all__ = [
    'Skill',
    'SkillManager',
    'get_skills_dir',
    'MAX_INSTRUCTIONS_CHARS',
]