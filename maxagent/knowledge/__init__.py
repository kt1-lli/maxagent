#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""maxagent.knowledge —— BM25 全文检索知识库。

本包提供三类检索场景：
- A：本地打包的 3ds Max Python Help（``MaxHelpSource``）
- D：用户导入的 md / txt 文档（``MarkdownFileSource`` / ``DirectorySource``）
- C：Skills / 会话历史的语义召回（第二批加）

对外主入口：
- ``get_maxhelp_index()``：Max 官方文档索引
- ``get_user_index()``：用户扩展知识库
- ``add_user_source(path)`` / ``remove_user_source(source_id)``：管理用户源
"""

from __future__ import absolute_import

from .bm25 import BM25Index
from .chunker import chunk_markdown
from .chunker import chunk_plaintext
from .index import KnowledgeIndex
from .index import add_user_source
from .index import get_maxhelp_index
from .index import get_user_index
from .index import remove_user_source
from .sources import DirectorySource
from .sources import DocSource
from .sources import MarkdownFileSource
from .sources import MaxHelpSource
from .tokenizer import tokenize


__all__ = [
    'BM25Index',
    'KnowledgeIndex',
    'DocSource',
    'MaxHelpSource',
    'MarkdownFileSource',
    'DirectorySource',
    'chunk_markdown',
    'chunk_plaintext',
    'tokenize',
    'get_maxhelp_index',
    'get_user_index',
    'add_user_source',
    'remove_user_source',
]
