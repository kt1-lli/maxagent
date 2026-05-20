#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM 配置管理模块。

支持多 Profile 配置，覆盖本地模型（Ollama / LM Studio / vLLM 等 OpenAI 兼容服务）
和远程 API Key 模式（OpenAI / Azure / DeepSeek / 公司网关等）。

配置文件位置：
    Windows: %USERPROFILE%/Documents/3dsMax/maxagent/config.json
    其他:    ~/.maxagent/config.json
"""

from __future__ import absolute_import
from __future__ import print_function

import base64
import json
import os
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from typing import Dict
from typing import List
from typing import Optional


CONFIG_VERSION = 1

# 内置预设：用户首次启动时可一键选用
BUILTIN_PROFILES = [
    {
        "name": "Ollama (本地)",
        "base_url": "http://localhost:11434/v1",
        "api_key": "ollama",
        "model": "qwen2.5:14b",
        "kind": "local",
        "supports_tools": True,
        "stream": True,
        "timeout": 300,
    },
    {
        "name": "LM Studio (本地)",
        "base_url": "http://localhost:1234/v1",
        "api_key": "lm-studio",
        "model": "local-model",
        "kind": "local",
        "supports_tools": False,  # LM Studio 部分模型 tools 不稳，默认走 JSON 模式
        "stream": True,
        "timeout": 300,
    },
    {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "api_key": "",
        "model": "gpt-4o",
        "kind": "remote",
        "supports_tools": True,
        "stream": True,
        "timeout": 120,
    },
    {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "",
        "model": "deepseek-chat",
        "kind": "remote",
        "supports_tools": True,
        "stream": True,
        "timeout": 120,
    },
]


@dataclass
class LLMProfile:
    """单个 LLM Profile 配置。"""

    name: str = "Default"
    base_url: str = "http://localhost:11434/v1"
    api_key: str = ""
    model: str = "qwen2.5:14b"
    # kind: "local" 或 "remote"，影响默认 timeout、UI 提示等
    kind: str = "local"
    # 是否原生支持 OpenAI tools/function calling；False 时降级到 JSON 模式
    supports_tools: bool = True
    stream: bool = True
    timeout: int = 300
    # 模型采样参数
    temperature: float = 0.2
    max_tokens: int = 4096
    # 自定义 HTTP 头（如公司网关需要的鉴权头）
    extra_headers: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        data = asdict(self)
        # api_key 写盘前做简单混淆（非真正加密，仅防止肉眼直读）
        if data.get("api_key"):
            data["api_key"] = "b64:" + base64.b64encode(
                data["api_key"].encode("utf-8")
            ).decode("ascii")
        return data

    @classmethod
    def from_dict(cls, data: Dict) -> "LLMProfile":
        data = dict(data)
        api_key = data.get("api_key", "") or ""
        if api_key.startswith("b64:"):
            try:
                api_key = base64.b64decode(api_key[4:]).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                api_key = ""
        data["api_key"] = api_key
        # 过滤掉未知字段，保持向前兼容
        valid_keys = {f for f in cls.__dataclass_fields__}
        data = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**data)


@dataclass
class AppConfig:
    """全局配置。"""

    version: int = CONFIG_VERSION
    active_profile: str = "Ollama (本地)"
    profiles: List[LLMProfile] = field(default_factory=list)
    # 安全：是否允许执行 run_maxscript / run_python 等"逃生舱"工具
    allow_escape_hatch: bool = True
    # 是否每次执行逃生舱前弹窗确认
    confirm_before_exec: bool = True
    # 是否每次 agent 操作包一层 undo
    wrap_undo: bool = True
    # 自动注入的场景上下文最大长度（字符）
    max_context_chars: int = 4000

    def get_active_profile(self) -> Optional[LLMProfile]:
        for p in self.profiles:
            if p.name == self.active_profile:
                return p
        return self.profiles[0] if self.profiles else None

    def to_dict(self) -> Dict:
        return {
            "version": self.version,
            "active_profile": self.active_profile,
            "profiles": [p.to_dict() for p in self.profiles],
            "allow_escape_hatch": self.allow_escape_hatch,
            "confirm_before_exec": self.confirm_before_exec,
            "wrap_undo": self.wrap_undo,
            "max_context_chars": self.max_context_chars,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "AppConfig":
        cfg = cls()
        cfg.version = int(data.get("version", CONFIG_VERSION))
        cfg.active_profile = data.get("active_profile", "Default")
        cfg.profiles = [
            LLMProfile.from_dict(p) for p in data.get("profiles", [])
        ]
        cfg.allow_escape_hatch = bool(data.get("allow_escape_hatch", True))
        cfg.confirm_before_exec = bool(data.get("confirm_before_exec", True))
        cfg.wrap_undo = bool(data.get("wrap_undo", True))
        cfg.max_context_chars = int(data.get("max_context_chars", 4000))
        return cfg


def get_config_dir() -> str:
    """获取配置目录。优先用 Max 的 Documents/3dsMax 目录，其次 ~/.maxagent。"""
    user_profile = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    max_doc_dir = os.path.join(user_profile, "Documents", "3dsMax")
    if os.path.isdir(max_doc_dir):
        cfg_dir = os.path.join(max_doc_dir, "maxagent")
    else:
        cfg_dir = os.path.join(os.path.expanduser("~"), ".maxagent")
    os.makedirs(cfg_dir, exist_ok=True)
    return cfg_dir


def get_config_path() -> str:
    return os.path.join(get_config_dir(), "config.json")


def load_config() -> AppConfig:
    """加载配置；首次运行时写入内置预设。"""
    path = get_config_path()
    if not os.path.exists(path):
        cfg = AppConfig()
        cfg.profiles = [LLMProfile.from_dict(p) for p in BUILTIN_PROFILES]
        cfg.active_profile = cfg.profiles[0].name
        save_config(cfg)
        return cfg

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return AppConfig.from_dict(raw)
    except (OSError, ValueError, KeyError) as exc:
        # 配置损坏时回退到默认值，避免插件起不来
        print("[maxagent] 配置加载失败，使用默认: {}".format(exc))
        cfg = AppConfig()
        cfg.profiles = [LLMProfile.from_dict(p) for p in BUILTIN_PROFILES]
        cfg.active_profile = cfg.profiles[0].name
        return cfg


def save_config(cfg: AppConfig) -> None:
    """持久化配置到磁盘。"""
    path = get_config_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg.to_dict(), f, ensure_ascii=False, indent=2)
    # 原子替换，避免写一半崩溃损坏配置
    if os.path.exists(path):
        os.replace(tmp, path)
    else:
        os.rename(tmp, path)


class ConfigManager:
    """AppConfig 的 UI 友好包装器。

    封装 ``dock_widget`` / ``settings_dialog`` 需要的高层操作：profile 列表、
    切换激活 profile、增删改查 profile，并自动持久化。

    :param config_path: 自定义配置文件路径，None 时使用默认路径
                        （便于单元测试隔离）
    """

    def __init__(self, config_path=None):
        # type: (Optional[str]) -> None
        self._custom_path = config_path
        self._cfg = self._load()

    # -------- 内部 IO（支持自定义路径） --------
    def _path(self) -> str:
        return self._custom_path if self._custom_path else get_config_path()

    def _load(self) -> AppConfig:
        path = self._path()
        if not os.path.exists(path):
            cfg = AppConfig()
            cfg.profiles = [
                LLMProfile.from_dict(p) for p in BUILTIN_PROFILES
            ]
            cfg.active_profile = cfg.profiles[0].name
            self._save(cfg)
            return cfg
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            return AppConfig.from_dict(raw)
        except (OSError, ValueError, KeyError) as exc:
            print("[maxagent] 配置加载失败，使用默认: {}".format(exc))
            cfg = AppConfig()
            cfg.profiles = [
                LLMProfile.from_dict(p) for p in BUILTIN_PROFILES
            ]
            cfg.active_profile = cfg.profiles[0].name
            return cfg

    def _save(self, cfg: AppConfig) -> None:
        path = self._path()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cfg.to_dict(), fh, ensure_ascii=False, indent=2)
        if os.path.exists(path):
            os.replace(tmp, path)
        else:
            os.rename(tmp, path)

    # -------- 公共 API --------
    def save(self) -> None:
        """持久化当前配置到磁盘。"""
        self._save(self._cfg)

    @property
    def config(self) -> AppConfig:
        return self._cfg

    def list_profile_names(self) -> List[str]:
        return [p.name for p in self._cfg.profiles]

    def get_active_profile_name(self) -> str:
        return self._cfg.active_profile

    def get_active_profile(self) -> Optional[LLMProfile]:
        return self._cfg.get_active_profile()

    def get_profile(self, name: str) -> Optional[LLMProfile]:
        for p in self._cfg.profiles:
            if p.name == name:
                return p
        return None

    def set_active_profile(self, name: str) -> None:
        if self.get_profile(name) is None:
            raise ValueError("Profile 不存在: {}".format(name))
        self._cfg.active_profile = name
        self.save()

    def upsert_profile(self, profile: LLMProfile) -> None:
        """新增或就地更新 profile（按 name 匹配）。"""
        for i, p in enumerate(self._cfg.profiles):
            if p.name == profile.name:
                self._cfg.profiles[i] = profile
                return
        self._cfg.profiles.append(profile)

    def delete_profile(self, name: str) -> None:
        if name == self._cfg.active_profile:
            raise ValueError("不能删除当前激活的 Profile")
        self._cfg.profiles = [
            p for p in self._cfg.profiles if p.name != name
        ]


if __name__ == "__main__":
    # 简单自检
    c = load_config()
    print("Config dir:", get_config_dir())
    print("Active:", c.active_profile)
    for prof in c.profiles:
        print("  -", prof.name, prof.base_url, prof.model)
