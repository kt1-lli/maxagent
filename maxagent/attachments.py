#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""图片附件管理模块。

负责对话中"图片"这类二进制附件的：
1. 落盘存储：避免直接进 session JSON 让文件体积爆炸；
2. base64 编码：用于把图片打包成 OpenAI 视觉协议（image_url）；
3. LLM 多模态消息组装：把 user 文本 + 图片列表合并成 content 数组；
4. 视觉能力探测：根据 model 名匹配 ``vision_model_whitelist``，
   不支持视觉的模型自动降级为纯文本。

设计要点：
- 落盘路径：``{config_dir}/sessions/attachments/<uuid>.<ext>``
- session JSON 里只存 attachment 元信息（path/mime/size），不存 base64
- LLM 调用时按需 base64，发完即释放，不缓存超大字符串
- DeepSeek / 本地纯文本模型 → 自动降级到"[图片] N 张"占位文本
"""

from __future__ import absolute_import
from __future__ import print_function

import base64
import os
import time
import uuid
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from .config import get_config_dir
from .logger import get_logger


logger = get_logger(__name__)


ATTACHMENTS_DIRNAME = 'attachments'

# 单张图片硬上限（字节），超过则拒收
# 太大的图片即使是视觉模型也会超 token / 超 timeout
MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB

# 扩展名 -> MIME 类型
_MIME_BY_EXT = {
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.webp': 'image/webp',
    '.bmp': 'image/bmp',
}


def get_attachments_dir():
    # type: () -> str
    """获取附件存储目录，不存在则创建。"""
    base = get_config_dir()
    sess = os.path.join(base, 'sessions')
    if not os.path.isdir(sess):
        os.makedirs(sess)
    path = os.path.join(sess, ATTACHMENTS_DIRNAME)
    if not os.path.isdir(path):
        os.makedirs(path)
    return path


def _ext_for_mime(mime):
    # type: (str) -> str
    """MIME 反推扩展名。"""
    for ext, m in _MIME_BY_EXT.items():
        if m == mime:
            return ext
    return '.png'


def _detect_mime(path):
    # type: (str) -> str
    """根据扩展名推断 MIME（不读文件内容）。"""
    ext = os.path.splitext(path)[1].lower()
    return _MIME_BY_EXT.get(ext, 'image/png')


class Attachment(object):
    """单个附件元信息。

    实际二进制保存在 ``path`` 指向的磁盘文件，本对象只持元数据，
    可以安全序列化进 session JSON。
    """

    KIND_IMAGE = 'image'

    def __init__(self, kind, path, mime, size, created_at=None, name=''):
        # type: (str, str, str, int, Optional[float], str) -> None
        self.kind = kind
        self.path = path
        self.mime = mime
        self.size = int(size)
        self.created_at = (
            float(created_at) if created_at is not None else time.time()
        )
        self.name = name or os.path.basename(path)

    def to_json(self):
        # type: () -> Dict[str, Any]
        return {
            'kind': self.kind,
            'path': self.path,
            'mime': self.mime,
            'size': self.size,
            'created_at': self.created_at,
            'name': self.name,
        }

    @classmethod
    def from_json(cls, data):
        # type: (Dict[str, Any]) -> 'Attachment'
        return cls(
            kind=str(data.get('kind', cls.KIND_IMAGE)),
            path=str(data.get('path', '')),
            mime=str(data.get('mime', 'image/png')),
            size=int(data.get('size', 0) or 0),
            created_at=data.get('created_at'),
            name=str(data.get('name', '') or ''),
        )

    def exists(self):
        # type: () -> bool
        return bool(self.path) and os.path.isfile(self.path)

    def to_data_uri(self):
        # type: () -> Optional[str]
        """把磁盘文件读成 ``data:<mime>;base64,...`` URI。

        失败（文件不存在/读失败）返回 None，调用方自行决定是否降级。
        """
        if not self.exists():
            logger.warning('附件文件不存在: %s', self.path)
            return None
        try:
            with open(self.path, 'rb') as fh:
                raw = fh.read()
        except OSError as exc:
            logger.warning('读取附件失败 %s: %s', self.path, exc)
            return None
        b64 = base64.b64encode(raw).decode('ascii')
        return 'data:{mime};base64,{b64}'.format(mime=self.mime, b64=b64)


def save_image_bytes(data, mime='image/png', name=''):
    # type: (bytes, str, str) -> Optional[Attachment]
    """把内存里的图片字节落盘到 attachments 目录。

    :param data: 二进制内容
    :param mime: MIME 类型，决定文件扩展名
    :param name: 可选的人类可读名，仅用于 UI 展示
    :returns: ``Attachment`` 实例；超限或失败返回 None
    """
    if not data:
        return None
    if len(data) > MAX_IMAGE_BYTES:
        logger.warning(
            '图片过大被拒收：%d bytes > 上限 %d',
            len(data), MAX_IMAGE_BYTES,
        )
        return None
    ext = _ext_for_mime(mime)
    fname = '{}{}'.format(uuid.uuid4().hex, ext)
    path = os.path.join(get_attachments_dir(), fname)
    try:
        with open(path, 'wb') as fh:
            fh.write(data)
    except OSError as exc:
        logger.warning('写入附件失败: %s', exc)
        return None
    return Attachment(
        kind=Attachment.KIND_IMAGE,
        path=path,
        mime=mime,
        size=len(data),
        name=name,
    )


def save_image_file(src_path, name=''):
    # type: (str, str) -> Optional[Attachment]
    """把外部图片文件复制进 attachments 目录。

    复制而不是引用——用户原图如果被删 / 移动，会话仍能正常重放。
    """
    if not src_path or not os.path.isfile(src_path):
        return None
    try:
        size = os.path.getsize(src_path)
    except OSError:
        return None
    if size > MAX_IMAGE_BYTES:
        logger.warning(
            '图片过大被拒收：%s (%d > %d)',
            src_path, size, MAX_IMAGE_BYTES,
        )
        return None
    try:
        with open(src_path, 'rb') as fh:
            data = fh.read()
    except OSError as exc:
        logger.warning('读取源图失败 %s: %s', src_path, exc)
        return None
    mime = _detect_mime(src_path)
    return save_image_bytes(
        data, mime=mime, name=name or os.path.basename(src_path),
    )


# ---------------------------------------------------------------------- #
# 视觉能力探测 + 多模态消息组装
# ---------------------------------------------------------------------- #
def model_supports_vision(model_name, whitelist):
    # type: (str, List[str]) -> bool
    """根据 model 名 + 白名单子串匹配判定是否支持视觉。

    :param model_name: 当前 active profile 的 model 字段（如 'gpt-4o'）
    :param whitelist: 配置里 ``vision_model_whitelist`` 的子串列表
    :returns: True 表示该模型支持 OpenAI 多模态协议
    """
    if not model_name or not whitelist:
        return False
    name = str(model_name).strip().lower()
    for kw in whitelist:
        kw = str(kw).strip().lower()
        if kw and kw in name:
            return True
    return False


def build_user_content(text, attachments, can_vision,
                       keep_images=True, force_multimodal=False):
    # type: (str, List[Attachment], bool, bool, bool) -> Any
    """根据是否支持视觉把 text + attachments 打包成 OpenAI content。

    - ``can_vision=True`` 且 ``keep_images=True``：返回 list 形态的多模态 content
      ``[{type: text, text: ...}, {type: image_url, image_url: {url: ...}}, ...]``
    - ``can_vision=True`` 且 ``keep_images=False``：返回 list（仅 text 段），
      正文带"[已展示过的图片 N 张]"占位提示——用于多轮对话历史瘦身：
      只让最后一条 user 真正塞图，更早的图片 base64 不再重复发送给视觉网关。
    - ``can_vision=False`` 或 attachments 为空：返回纯字符串
      （不支持视觉时附加"[图片] N 张"占位提示，让 LLM 知道有图但看不到）
    - ``force_multimodal=True`` 且 ``can_vision=True``：即使没有图片，也把纯文本
      包成 ``[{"type":"text","text":...}]``。用途：vita 这类视觉网关对
      "历史含图、当前纯文本"的混合 content 形态支持不稳，统一成 list 最稳。

    :param keep_images: False 表示丢弃 image_url 段，仅保留占位文本（仍是 list
        形态以便统一格式）。仅在 can_vision=True 路径下有差异。
    :param force_multimodal: True 表示无图片时也强制 list 形态。
    """
    text = text or ''
    images = [a for a in (attachments or []) if a.kind == Attachment.KIND_IMAGE]

    # 无图：按 force_multimodal 决定纯文本 or list[text]
    if not images:
        if can_vision and force_multimodal:
            # 空文本时仍要给一个 text 段，避免下游 vita 网关拒绝空 content
            return [{'type': 'text', 'text': text if text else ' '}]
        return text

    # 不支持视觉：纯文本 + 附件计数提示（行为与原版一致）
    if not can_vision:
        notice = '\n\n[用户附带 {} 张图片，但当前模型不支持视觉，无法查看]'.format(
            len(images),
        )
        return (text + notice).strip()

    # 支持视觉但 keep_images=False：仅 text 段 + 占位提示
    if not keep_images:
        notice = '\n\n[此前已展示过 {} 张图片，本轮上下文不再重发]'.format(
            len(images),
        )
        merged = (text + notice).strip()
        return [{'type': 'text', 'text': merged if merged else ' '}]

    parts = []  # type: List[Dict[str, Any]]
    if text.strip():
        parts.append({'type': 'text', 'text': text})
    for att in images:
        uri = att.to_data_uri()
        if uri is None:
            continue
        parts.append({
            'type': 'image_url',
            'image_url': {'url': uri},
        })
    if not parts:
        # 全部图片读失败兜底
        return text + '\n\n[图片读取失败]'
    # 至少要有 text 段，OpenAI 规定 user 消息不能纯图
    has_text = any(p.get('type') == 'text' for p in parts)
    if not has_text:
        parts.insert(0, {'type': 'text', 'text': text or '请看图。'})
    return parts


__all__ = [
    'Attachment',
    'MAX_IMAGE_BYTES',
    'get_attachments_dir',
    'save_image_bytes',
    'save_image_file',
    'model_supports_vision',
    'build_user_content',
]
