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
        "price_input_per_1m": 5.0,
        "price_output_per_1m": 15.0,
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
        "price_input_per_1m": 0.27,
        "price_output_per_1m": 1.1,
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
    # Agent 工具调用循环的最大轮数。批量任务（如"测试所有工具"）
    # 可能产生大量连续工具调用，默认 40 轮；接近上限时会主动提示
    # LLM 收尾，超限后保留已完成的工作而不是直接抛弃。
    max_tool_loops: int = 40
    # 历史消息 token 预算上限。每次发请求前自动裁剪超出部分的早期消息，
    # 但严格保护 tool_call 配对、最近 4 条与 system 提示。
    # 经验值：
    #   - DeepSeek/GPT-4 (64K~128K context): 32000~48000
    #   - 本地小模型 (Qwen 7B/14B 32K): 8000~16000
    #   - 长上下文专家模型 (200K+): 64000
    max_history_tokens: int = 32000
    # 长会话自动摘要触发阈值（token）。超过该阈值时，系统会在下一轮
    # LLM 调用前后台请求模型生成摘要替换早期消息。0 表示禁用自动摘要。
    auto_summarize_threshold: int = 0
    # 单次工具结果的最大字节数。超出时 dispatcher 会自动截断后再回灌
    # 给 LLM，避免 list_scene_objects 这类大返回直接打爆上下文窗口。
    # 0 或负数 = 不截断（不推荐）。
    tool_result_max_bytes: int = 16384
    # 计费单价（USD per 1M tokens），仅用于 UI 估算成本展示。
    # 默认 0 表示不显示成本（本地模型/未知服务）。
    # 常见参考值：DeepSeek-Chat (in 0.27 / out 1.1)；
    # GPT-4o (in 5 / out 15)；GPT-4o-mini (in 0.15 / out 0.6)。
    price_input_per_1m: float = 0.0
    price_output_per_1m: float = 0.0

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
    # 启动 Max 时是否自动显示 MaxAgent 面板。False 时用户需手动调用
    # ``maxagent.startup.show_panel()`` 或 MaxScript ``g_show_max_agent()``
    auto_show_on_startup: bool = True
    # 日志级别。可选 ``DEBUG`` / ``INFO`` / ``WARNING`` / ``ERROR``。
    # 文件日志固定写 DEBUG 以上（最详细，方便事后回溯），控制台只输出
    # 这里配置的级别。出问题时调成 ``DEBUG`` 抓现场即可。
    log_level: str = "INFO"

    # ---------- 联网搜索 ---------- #
    # 联网模式三选一：
    #   off    - 全局禁用联网，主 UI 按钮置灰
    #   auto   - 全局允许，由用户在主 UI 通过 🌐 按钮按需切换本轮
    #   force  - 全局强制开启，主 UI 按钮强制亮起且不可关
    web_search_mode: str = "auto"
    # 搜索后端：duckduckgo（HTML scraping，零依赖，默认）/
    #            bing_api（需 Key）/ disabled（永不联网）
    web_search_backend: str = "duckduckgo"
    # 单次返回的最大结果数（标题 + url + 摘要）
    web_search_max_results: int = 5
    # 是否对前 N 条结果抓取网页正文摘要（默认开，否则只有标题摘要质量差）
    web_fetch_page_text: bool = True
    # Bing API Key（仅 backend=bing_api 时需要）
    bing_api_key: str = ""

    # ---------- 员工档案（纯 UI 皮肤，不影响 LLM）---------- #
    # "岗位"是 MaxAgent（写死在 system prompt，不可改）；
    # "员工"是用户自定义的对外形象——名字 + 头像。
    # 这些字段只影响对话气泡的视觉表达，LLM 完全不知情。
    # 默认 employee_name = "助手"（保持现状的视觉语义）。
    employee_name: str = "助手"
    # 头像类型："emoji" 用 emoji 字符，"image" 用上传的 PNG 图片
    employee_avatar_kind: str = "emoji"
    # emoji 模式时使用的字符（兼容 _ee() 兜底）
    employee_avatar_emoji: str = "🤖"
    # image 模式时存的相对文件名，固定为 "avatar.png"，
    # 实际路径 = config_dir + filename
    employee_avatar_image: str = ""

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
            "auto_show_on_startup": self.auto_show_on_startup,
            "log_level": self.log_level,
            "web_search_mode": self.web_search_mode,
            "web_search_backend": self.web_search_backend,
            "web_search_max_results": self.web_search_max_results,
            "web_fetch_page_text": self.web_fetch_page_text,
            "bing_api_key": self.bing_api_key,
            "employee_name": self.employee_name,
            "employee_avatar_kind": self.employee_avatar_kind,
            "employee_avatar_emoji": self.employee_avatar_emoji,
            "employee_avatar_image": self.employee_avatar_image,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "AppConfig":
        cfg = cls()
        cfg.version = int(data.get("version", CONFIG_VERSION))
        cfg.profiles = [
            LLMProfile.from_dict(p) for p in data.get("profiles", [])
        ]
        # active_profile 默认值用第一个 profile 名而不是硬编码 "Default"，
        # 避免老数据缺失字段时指向不存在的 profile
        fallback_active = cfg.profiles[0].name if cfg.profiles else "Default"
        cfg.active_profile = data.get("active_profile") or fallback_active
        cfg.allow_escape_hatch = bool(data.get("allow_escape_hatch", True))
        cfg.confirm_before_exec = bool(data.get("confirm_before_exec", True))
        cfg.wrap_undo = bool(data.get("wrap_undo", True))
        cfg.max_context_chars = int(data.get("max_context_chars", 4000))
        cfg.auto_show_on_startup = bool(
            data.get("auto_show_on_startup", True)
        )
        # log_level 兼容老配置：缺失或非法值时回落 INFO
        raw_level = str(data.get("log_level", "INFO") or "INFO").upper()
        cfg.log_level = (
            raw_level if raw_level in ("DEBUG", "INFO", "WARNING", "ERROR")
            else "INFO"
        )
        # ---- 联网搜索 ---- #
        mode = str(data.get("web_search_mode", "auto") or "auto").lower()
        cfg.web_search_mode = (
            mode if mode in ("off", "auto", "force") else "auto"
        )
        backend = str(
            data.get("web_search_backend", "duckduckgo") or "duckduckgo",
        ).lower()
        cfg.web_search_backend = (
            backend if backend in ("duckduckgo", "bing_api", "disabled")
            else "duckduckgo"
        )
        try:
            cfg.web_search_max_results = max(1, min(10, int(
                data.get("web_search_max_results", 5),
            )))
        except (TypeError, ValueError):
            cfg.web_search_max_results = 5
        cfg.web_fetch_page_text = bool(
            data.get("web_fetch_page_text", True),
        )
        cfg.bing_api_key = str(data.get("bing_api_key", "") or "")
        # ---- 员工档案（纯 UI 皮肤）---- #
        cfg.employee_name = str(
            data.get("employee_name", "助手") or "助手",
        ).strip() or "助手"
        kind = str(
            data.get("employee_avatar_kind", "emoji") or "emoji",
        ).lower()
        cfg.employee_avatar_kind = (
            kind if kind in ("emoji", "image") else "emoji"
        )
        cfg.employee_avatar_emoji = str(
            data.get("employee_avatar_emoji", "🤖") or "🤖",
        )
        cfg.employee_avatar_image = str(
            data.get("employee_avatar_image", "") or "",
        )
        return cfg


def get_config_dir() -> str:
    """获取配置目录。

    优先级：
    1. ``MAXAGENT_DATA_DIR`` 环境变量（用户显式覆盖，最高优先级，便于测试）
    2. **插件包同级的 ``_userdata`` 目录**（默认，配置跟着插件走，
       拷贝 / 拖动 ms 启动器到不同 Max 版本，配置自动跟随）

    包同级目录不可写时（极少数把插件放到 Program Files 的场景）才退到
    ``~/.maxagent``，避免插件起不来。
    """
    # 1. 环境变量覆盖
    env_dir = os.environ.get("MAXAGENT_DATA_DIR")
    if env_dir:
        try:
            os.makedirs(env_dir, exist_ok=True)
            return env_dir
        except OSError:
            pass

    # 2. 插件包同级 _userdata 目录（默认）
    #    __file__ 指向 maxagent/config.py，包目录是 dirname(__file__)，
    #    再向上一级是包的父目录（ms 启动器与 maxagent 包并列摆放）。
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(pkg_dir)
    local_dir = os.path.join(parent_dir, "_userdata")
    if _is_writable(parent_dir):
        try:
            os.makedirs(local_dir, exist_ok=True)
            return local_dir
        except OSError:
            pass

    # 3. 兜底：~/.maxagent（包目录只读时才走这里）
    fallback = os.path.join(os.path.expanduser("~"), ".maxagent")
    os.makedirs(fallback, exist_ok=True)
    print(
        "[maxagent] 包目录不可写，配置已回退到: {}".format(fallback),
    )
    return fallback


def _is_writable(path: str) -> bool:
    """判断目录是否存在且可写。不存在的尝试创建一次，仍失败视为不可写。"""
    try:
        if not os.path.exists(path):
            os.makedirs(path, exist_ok=True)
        return os.access(path, os.W_OK)
    except OSError:
        return False


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
        # 备份损坏文件，方便用户事后排查
        try:
            if os.path.exists(path):
                bak = path + ".corrupt"
                os.replace(path, bak)
                print("[maxagent] 损坏配置已备份到: {}".format(bak))
        except OSError:
            pass
        cfg = AppConfig()
        cfg.profiles = [LLMProfile.from_dict(p) for p in BUILTIN_PROFILES]
        cfg.active_profile = cfg.profiles[0].name
        # 立刻把默认值写回磁盘，避免下次启动还是损坏状态
        try:
            save_config(cfg)
        except OSError as save_exc:
            print("[maxagent] 默认配置写盘失败: {}".format(save_exc))
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
            # 备份损坏文件，避免覆盖丢失现场
            try:
                if os.path.exists(path):
                    bak = path + ".corrupt"
                    os.replace(path, bak)
                    print(
                        "[maxagent] 损坏配置已备份到: {}".format(bak),
                    )
            except OSError:
                pass
            cfg = AppConfig()
            cfg.profiles = [
                LLMProfile.from_dict(p) for p in BUILTIN_PROFILES
            ]
            cfg.active_profile = cfg.profiles[0].name
            # 立即把默认值写回磁盘，对齐正常首次启动路径行为
            try:
                self._save(cfg)
            except OSError as save_exc:
                print(
                    "[maxagent] 默认配置写盘失败: {}".format(save_exc),
                )
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
        """新增或就地更新 profile（按 name 匹配），并立即落盘。

        与 ``set_active_profile`` 行为对齐：所有变更类 API 都自动持久化，
        避免外部调用方忘记 ``save()`` 导致重启丢配置。
        """
        for i, p in enumerate(self._cfg.profiles):
            if p.name == profile.name:
                self._cfg.profiles[i] = profile
                self.save()
                return
        self._cfg.profiles.append(profile)
        self.save()

    def delete_profile(self, name: str) -> None:
        """删除 profile（不允许删除当前激活的），并立即落盘。"""
        if name == self._cfg.active_profile:
            raise ValueError("不能删除当前激活的 Profile")
        self._cfg.profiles = [
            p for p in self._cfg.profiles if p.name != name
        ]
        self.save()


if __name__ == "__main__":
    # 简单自检
    c = load_config()
    print("Config dir:", get_config_dir())
    print("Active:", c.active_profile)
    for prof in c.profiles:
        print("  -", prof.name, prof.base_url, prof.model)
