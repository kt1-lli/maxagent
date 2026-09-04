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
from typing import Any
from typing import Dict
from typing import List
from typing import Optional


CONFIG_VERSION = 1


# 内置运营商 Base URL 预设（用于 UI 下拉快速填充）
BUILTIN_PROVIDER_PRESETS = [
    {"name": "OpenAI", "base_url": "https://api.openai.com/v1"},
    {"name": "Moonshot", "base_url": "https://api.moonshot.cn/v1"},
    {"name": "DeepSeek", "base_url": "https://api.deepseek.com"},
    {"name": "智谱 AI", "base_url": "https://open.bigmodel.cn/api/paas/v4"},
    {"name": "硅基流动", "base_url": "https://api.siliconflow.cn/v1"},
    {"name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1"},
    {"name": "Together", "base_url": "https://api.together.xyz/v1"},
    {"name": "Ollama (本地)", "base_url": "http://localhost:11434/v1"},
    {"name": "LM Studio (本地)", "base_url": "http://localhost:1234/v1"},
]


def _get_logger():
    """Lazy 获取 logger。

    config 被 logger.setup_logging 反向 import（用于解析 config_dir），
    故此处不能在模块顶部 ``from .logger import get_logger``——会触发
    循环 import。所有需要日志的位置统一通过本函数获取，且把 ``ImportError``
    吞掉，保证 config 永远能加载。
    """
    try:
        from .logger import get_logger
        return get_logger(__name__)
    except ImportError:
        import logging
        return logging.getLogger(__name__)


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
        "base_url": "https://api.deepseek.com",
        "api_key": "",
        "model": "deepseek-v4-flash",
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
    # 部分模型/网关（如 Moonshot kimi-k3）服务端仅接受 temperature=1，
    # 非 1 值会直接返回 400。开启后所有发往该 profile 的请求（含
    # reasoning 轮次和自动摘要）都会强制使用 temperature=1。
    force_temperature_one: bool = False
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
    # 当前模型是否支持视觉输入（image_url / image content part）。
    # True 时 Agent 会自动在需要视觉验证的节点发送 viewport 截图。
    # False 时使用文本降级描述。
    vision_supported: bool = False
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
    # 通用参数覆盖：用于覆盖发往 LLM 的 payload 中任意字段的字典。
    # 未来任何模型/网关对 temperature / top_p / max_tokens 等参数有
    # 特殊要求时，只需在这里配置即可，无需改动代码。
    # 老配置中的 force_temperature_one=True 会在 from_dict 中自动迁移为
    # param_overrides["temperature"] = 1.0。
    param_overrides: Dict[str, Any] = field(default_factory=dict)
    # 备用 Profile 链：当前 profile 触发速率限制或不可用时，按列表顺序
    # 尝试切换到链中的其他 profile。名称必须对应 profiles 中已有的 profile。
    fallback_profile_names: List[str] = field(default_factory=list)

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
        # 兼容迁移：老配置 force_temperature_one=True 且 param_overrides 中
        # 没有 temperature 时，自动把 temperature=1.0 写入 param_overrides，
        # 保证旧配置在新版中行为不变。
        force_one = bool(data.get("force_temperature_one", False))
        overrides = data.get("param_overrides") or {}
        if not isinstance(overrides, dict):
            overrides = {}
        if force_one and "temperature" not in overrides:
            overrides = dict(overrides)
            overrides["temperature"] = 1.0
            data["param_overrides"] = overrides
        return cls(**data)


# ---------------------------------------------------------------------------
# 新数据模型：Provider + ModelEntry
# ---------------------------------------------------------------------------
# 目的：把「运营商」和「模型」分离。同一运营商（同 base_url + api_key + header）
# 可挂多个模型，用户在对话窗口切模型时不再切整套 profile。
#
# 迁移策略（保守）：老 Profile 保留不删；启动时把 base_url 相同、api_key 相同
# 的 profile 合并为一个 Provider，每个 profile 变为 Provider 下的一个
# ModelEntry。老 active_profile 通过 (provider_id, model_id) 引用继续可用。
# ---------------------------------------------------------------------------


@dataclass
class ModelEntry:
    """运营商下的单个模型条目。

    ``model`` 是发给 LLM API 的原始模型 id；``label`` 是显示名（可与 model
    相同）。``overrides`` 允许模型级别覆盖运营商默认参数（如 temperature、
    max_tokens、vision_supported、force_temperature_one 等）。
    """

    id: str = ""            # 运营商内唯一 id，通常等于 model；空时由 upsert 生成
    model: str = ""         # 发送给 API 的 model 字段
    label: str = ""         # 显示名；空时回退到 model
    overrides: Dict[str, Any] = field(default_factory=dict)
    # 该条目是否为运营商的默认模型（对话下拉里选中态）
    default: bool = False

    def to_dict(self) -> Dict:
        return {
            "id": self.id or self.model,
            "model": self.model,
            "label": self.label or self.model,
            "overrides": dict(self.overrides),
            "default": bool(self.default),
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "ModelEntry":
        data = dict(data or {})
        model = str(data.get("model", "") or "")
        ident = str(data.get("id", "") or model)
        label = str(data.get("label", "") or model)
        ov = data.get("overrides") or {}
        if not isinstance(ov, dict):
            ov = {}
        return cls(
            id=ident,
            model=model,
            label=label,
            overrides=ov,
            default=bool(data.get("default", False)),
        )


@dataclass
class Provider:
    """运营商配置。多个 ModelEntry 共享同一份 Key / URL / Header / 默认参数。"""

    id: str = ""             # 全局唯一 id；由 upsert 保证
    name: str = ""           # 显示名，如 "Moonshot"、"Ollama (本地)"
    base_url: str = ""
    api_key: str = ""
    extra_headers: Dict[str, str] = field(default_factory=dict)
    # 运营商级默认参数（同 LLMProfile 中的采样/超时/能力字段）
    # 未在 ModelEntry.overrides 中覆盖时全部沿用这里。
    defaults: Dict[str, Any] = field(default_factory=dict)
    # 该运营商下挂的模型
    models: List[ModelEntry] = field(default_factory=list)

    def to_dict(self) -> Dict:
        api_key = self.api_key or ""
        if api_key:
            api_key = "b64:" + base64.b64encode(
                api_key.encode("utf-8"),
            ).decode("ascii")
        return {
            "id": self.id,
            "name": self.name,
            "base_url": self.base_url,
            "api_key": api_key,
            "extra_headers": dict(self.extra_headers),
            "defaults": dict(self.defaults),
            "models": [m.to_dict() for m in self.models],
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "Provider":
        data = dict(data or {})
        api_key = data.get("api_key", "") or ""
        if api_key.startswith("b64:"):
            try:
                api_key = base64.b64decode(api_key[4:]).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                api_key = ""
        headers = data.get("extra_headers") or {}
        if not isinstance(headers, dict):
            headers = {}
        defaults = data.get("defaults") or {}
        if not isinstance(defaults, dict):
            defaults = {}
        raw_models = data.get("models") or []
        models = [ModelEntry.from_dict(m) for m in raw_models]
        return cls(
            id=str(data.get("id", "") or ""),
            name=str(data.get("name", "") or ""),
            base_url=str(data.get("base_url", "") or ""),
            api_key=api_key,
            extra_headers=headers,
            defaults=defaults,
            models=models,
        )

    def get_default_model(self) -> Optional[ModelEntry]:
        for m in self.models:
            if m.default:
                return m
        return self.models[0] if self.models else None


# 从 LLMProfile 复制到 Provider.defaults / ModelEntry.overrides 的字段集合。
# 保留在这里方便 UI / 迁移共享，避免多处硬编码。
_PARAM_FIELDS = (
    "kind", "supports_tools", "stream", "timeout",
    "temperature", "max_tokens", "force_temperature_one",
    "max_tool_loops", "max_history_tokens",
    "vision_supported", "auto_summarize_threshold",
    "tool_result_max_bytes",
    "price_input_per_1m", "price_output_per_1m",
    "param_overrides",
)


def _profile_defaults(profile: "LLMProfile") -> Dict[str, Any]:
    """把 LLMProfile 中的运营商级默认参数字段抽出成 dict。"""
    out = {}
    for k in _PARAM_FIELDS:
        if hasattr(profile, k):
            out[k] = getattr(profile, k)
    return out


def _build_providers_from_profiles(
    profiles: List["LLMProfile"],
    active_profile_name: str,
) -> "tuple[List[Provider], Optional[tuple[str, str]]]":
    """按 base_url 归并 profile 为 provider 列表。

    合并规则（保守，无损）：
      - 组 key = (base_url, api_key)；同 base_url 但不同 api_key 视为不同
        运营商（多账号场景不合并，命名加序号）。
      - 运营商名 = 该组第一个 profile 的 name；若多个 profile 归并到一组，
        显示名保留第一个，其它 profile 变为该 provider 下的模型条目。
      - Provider.defaults 取组内第一个 profile 的参数；组内后续 profile
        若参数不同，仅差异部分落到 ModelEntry.overrides。
      - default model = 与 active_profile_name 对应的那一条；未匹配则组内
        第一条。

    返回 (providers, active_model_ref)，active_model_ref 为
    (provider_id, model_id) 或 None（若无 profile）。
    """
    if not profiles:
        return [], None

    # 保持原始顺序进行分组
    groups: List[List["LLMProfile"]] = []
    key_to_index: Dict[tuple, int] = {}
    for prof in profiles:
        # 空 base_url 视为独立分组，避免把默认值不同的 profile 挤在一起
        key = (
            (prof.base_url or "").rstrip("/"),
            prof.api_key or "",
        )
        if not key[0]:
            groups.append([prof])
            continue
        if key not in key_to_index:
            key_to_index[key] = len(groups)
            groups.append([prof])
        else:
            groups[key_to_index[key]].append(prof)

    providers: List[Provider] = []
    active_ref: Optional[tuple] = None
    used_names: Dict[str, int] = {}

    for group in groups:
        first = group[0]
        # 命名：默认用第一个 profile 的 name；若同名冲突则加 " #2" 后缀
        base_name = (first.name or "Provider").strip() or "Provider"
        name = base_name
        n = used_names.get(base_name, 0)
        if n > 0:
            name = "{} #{}".format(base_name, n + 1)
        used_names[base_name] = n + 1

        provider_id = _slugify(name) or "provider_{}".format(len(providers) + 1)
        defaults = _profile_defaults(first)
        provider = Provider(
            id=provider_id,
            name=name,
            base_url=first.base_url,
            api_key=first.api_key,
            extra_headers=dict(first.extra_headers or {}),
            defaults=defaults,
            models=[],
        )

        # 组内每个 profile → 一个 ModelEntry
        for prof in group:
            overrides: Dict[str, Any] = {}
            for k in _PARAM_FIELDS:
                if hasattr(prof, k) and getattr(prof, k) != defaults.get(k):
                    overrides[k] = getattr(prof, k)
            entry = ModelEntry(
                id=prof.model or _slugify(prof.name),
                model=prof.model,
                label=prof.name,
                overrides=overrides,
                default=False,
            )
            provider.models.append(entry)

        # 判定 default model
        wanted = next(
            (p for p in group if p.name == active_profile_name),
            None,
        )
        if wanted is not None and provider.models:
            # 用与 wanted 对应的那条 entry 作为默认
            wanted_entry = provider.models[group.index(wanted)]
            wanted_entry.default = True
            if active_ref is None:
                active_ref = (provider.id, wanted_entry.id)
        elif provider.models:
            provider.models[0].default = True

        providers.append(provider)

    if active_ref is None and providers and providers[0].models:
        active_ref = (providers[0].id, providers[0].models[0].id)

    return providers, active_ref


def _slugify(text: str) -> str:
    """把任意字符串转为文件名/id 友好的 slug（保留中文原样，替换空白与斜杠）。"""
    if not text:
        return ""
    out = []
    for ch in text:
        if ch.isalnum() or ch in "-_":
            out.append(ch)
        elif '\u4e00' <= ch <= '\u9fff':  # CJK 汉字保留
            out.append(ch)
        else:
            out.append("_")
    slug = "".join(out).strip("_")
    return slug or ""


@dataclass
class AppConfig:
    """全局配置。"""

    version: int = CONFIG_VERSION
    active_profile: str = "Ollama (本地)"
    profiles: List[LLMProfile] = field(default_factory=list)
    # 新数据模型：运营商列表 + 当前激活模型引用 (provider_id, model_id)
    # 首次加载时若 providers 为空，会从 profiles 自动迁移生成。
    providers: List[Provider] = field(default_factory=list)
    active_model_ref: Optional[List[str]] = None
    # 安全：是否允许执行 run_maxscript / run_python 等脚本工具
    allow_script_tools: bool = True
    # 是否每次执行脚本工具前弹窗确认
    confirm_before_exec: bool = True
    # 是否每次 agent 操作包一层 undo
    wrap_undo: bool = True
    # 自动注入的场景上下文最大长度（字符）
    max_context_chars: int = 4000
    # 启动 Max 时是否自动显示 MaxAgent 面板。False 时用户需手动调用
    # ``maxagent.startup.show_panel()`` 或 MaxScript ``g_show_max_agent()``
    auto_show_on_startup: bool = True
    # 日志级别。三态：``OFF`` / ``INFO`` / ``DEBUG``。
    #   OFF   - 完全关闭日志（不写文件、不输出控制台）
    #   INFO  - 默认开启，记录关键节点（会话、错误、配置变更等）
    #   DEBUG - 详细模式，附加 LLM 请求/工具调用/线程切换等全量埋点
    # 老配置 ``WARNING`` / ``ERROR`` 会在加载时归一化到 ``INFO``。
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

    # ---------- 视觉/多模态（图片附件）---------- #
    # 主开关：是否允许把图片附件作为 image_url 多模态消息发给 LLM。
    # False 时即使用户在输入框插入图片，也只在本地气泡里显示，
    # LLM 端只会收到文本内容（兼容不支持视觉的模型）。
    vision_enabled: bool = True
    # 视觉能力白名单：模型名（小写）只要包含其中任一子串，就视为
    # 支持 OpenAI 多模态协议（content 列表 + image_url）。
    # 这是个保守白名单——不在表里的模型按"不支持"处理，避免直接
    # 把超长 base64 发给纯文本模型导致 400/超大 token 浪费。
    vision_model_whitelist: List[str] = field(default_factory=lambda: [
        "gpt-4o", "gpt-4-vision", "gpt-4-turbo",
        "claude-3", "claude-4", "claude-sonnet", "claude-opus",
        "gemini-1.5", "gemini-2", "gemini-pro-vision",
        "qwen-vl", "qwen2-vl", "qwen2.5-vl", "qwen-vl-max", "qwen-vl-plus",
        "glm-4v", "yi-vl", "internvl",
        "deepseek-vl", "pixtral", "llama-3.2-vision",
        # 腾讯 youtu 实验室的 vita 视觉理解系列（tokenhub 网关）
        "youtu-vita", "vita",
    ])

    # ---------- IDE Bridge（给外部 IDE / dcc-mcp 用的本地 TCP 端口）---------- #
    # 默认关闭：用户必须在设置面板手动启用，避免默认监听端口被误用
    bridge_enabled: bool = False
    # 监听地址（强烈建议保持 127.0.0.1 防止外网访问）
    bridge_host: str = "127.0.0.1"
    # 监听端口；与 dcc-mcp DCC_PRESETS["3dsMax"]=7003 对齐
    bridge_port: int = 7003
    # 可选访问令牌，非空时所有请求必须带匹配的 token 字段
    # 本机回环场景默认空字符串免鉴权
    bridge_token: str = ""
    # 是否暴露 dispatch_task 方法（让 IDE 把整个任务派给 maxagent 自己跑）
    # 关闭时只保留 execute_python 这个单纯执行通道
    bridge_dispatch_enabled: bool = True
    # dispatch_task 单任务最大工具循环轮数（安全护栏）
    bridge_dispatch_max_rounds: int = 20
    # dispatch_task 单任务总超时（秒）
    bridge_dispatch_timeout_sec: int = 300

    # ---------- 全局重试参数 ---------- #
    # LLM 请求最大重试次数
    llm_max_retries: int = 3
    # LLM 请求重试基础延迟（秒）
    llm_retry_base_delay: float = 1.0
    # LLM 请求重试最大延迟（秒）
    llm_retry_max_delay: float = 10.0
    # LLM 请求重试状态码白名单
    llm_retryable_status_codes: List[int] = field(default_factory=lambda: [
        429, 502, 503, 504
    ])

    # ---------- Skill 自动提议 ---------- #
    # 是否在会话结束时自动弹出"是否记为 Skill"提议（默认关闭）
    # 关闭时用户仍可通过对话或工具主动保存 Skill；开启时按下方门槛
    # 触发。默认关闭以避免打扰。
    enable_skill_proposal: bool = False
    # 触发 Skill 提议的最少成功动作数（默认 3，低于则跳过）
    # 单条 create_box 不值得沉淀为可复用流程。
    skill_proposal_min_actions: int = 3

    # ---------- 场景启动扫描（#4） ---------- #
    # 会话首轮 LLM 调用前是否自动拉一次场景快照并作为 system note 注入。
    # 效果：LLM 一上来就知道"当前场景有哪些对象/多少个 mesh/相机/灯光"，
    # 无需先反问"帮我列一下场景"。默认开启，代价是首轮多 ~200 tokens。
    enable_scene_startup_scan: bool = True

    # ---------- 操作确认清单（#10） ---------- #
    # 高风险工具批量执行前，是否先把清单交给用户批准。默认关闭。
    # 开启后：一次 LLM 回复里出现 >= approval_threshold 个写入类工具时
    # 会先弹批准对话框；批准前工具不会真正执行。适合放心大胆授权时关。
    enable_approval_queue: bool = False
    approval_threshold: int = 3

    # ---------- Cost 预算保护（#12） ---------- #
    # 单会话 token 预算（输入+输出累计，0 = 不限制）
    # 触达 80% 告警、100% 强制停止并要求用户确认继续
    session_token_budget: int = 0
    # 单会话美元预算（0 = 不限制）；按 profile 里的 price_*_per_1m 折算
    session_usd_budget: float = 0.0

    # ---------- 项目级记忆（#14） ---------- #
    # 按 .max 文件路径为 key 记录"这个场景的命名约定/单位/关键对象"等
    # 长期上下文，每次打开该场景时自动注入 system prompt。
    # 默认开启，纯本地文件，不联网。存放在 ~/.maxagent/projects/*.json
    enable_project_memory: bool = True

    # ---------- 共享资源目录 ---------- #
    # 团队共享资源库根目录。指向一个只读 Git 工作区，里面可放
    # skills / user_tools / user_rules / reflections / knowledge sources。
    # 空字符串表示未启用。
    shared_resources_dir: str = ""

    def get_active_profile(self) -> Optional[LLMProfile]:
        for p in self.profiles:
            if p.name == self.active_profile:
                return p
        return self.profiles[0] if self.profiles else None

    def to_dict(self) -> Dict:
        base = {
            "version": self.version,
            "active_profile": self.active_profile,
            "profiles": [p.to_dict() for p in self.profiles],
            "allow_script_tools": self.allow_script_tools,
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
            "vision_enabled": self.vision_enabled,
            "vision_model_whitelist": list(self.vision_model_whitelist),
            "bridge_enabled": self.bridge_enabled,
            "bridge_host": self.bridge_host,
            "bridge_port": self.bridge_port,
            "bridge_token": self.bridge_token,
            "bridge_dispatch_enabled": self.bridge_dispatch_enabled,
            "bridge_dispatch_max_rounds": self.bridge_dispatch_max_rounds,
            "bridge_dispatch_timeout_sec": self.bridge_dispatch_timeout_sec,
            "llm_max_retries": self.llm_max_retries,
            "llm_retry_base_delay": self.llm_retry_base_delay,
            "llm_retry_max_delay": self.llm_retry_max_delay,
            "llm_retryable_status_codes": list(self.llm_retryable_status_codes),
            "enable_skill_proposal": self.enable_skill_proposal,
            "skill_proposal_min_actions": self.skill_proposal_min_actions,
            "enable_scene_startup_scan": self.enable_scene_startup_scan,
            "enable_approval_queue": self.enable_approval_queue,
            "approval_threshold": self.approval_threshold,
            "session_token_budget": self.session_token_budget,
            "session_usd_budget": self.session_usd_budget,
            "enable_project_memory": self.enable_project_memory,
            "shared_resources_dir": self.shared_resources_dir,
        }
        # 新字段：providers / active_model_ref
        base["providers"] = [p.to_dict() for p in self.providers]
        base["active_model_ref"] = (
            list(self.active_model_ref) if self.active_model_ref else None
        )
        return base

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
        # allow_script_tools 是新的标准字段；allow_escape_hatch 为老配置名，兼容读取。
        cfg.allow_script_tools = bool(
            data.get("allow_script_tools", data.get("allow_escape_hatch", True))
        )
        cfg.confirm_before_exec = bool(data.get("confirm_before_exec", True))
        cfg.wrap_undo = bool(data.get("wrap_undo", True))
        cfg.max_context_chars = int(data.get("max_context_chars", 4000))
        cfg.auto_show_on_startup = bool(
            data.get("auto_show_on_startup", True)
        )
        # log_level 兼容老配置：三态化（OFF / INFO / DEBUG）。
        # 老的 WARNING / ERROR / CRITICAL 一律折算成 INFO；非法值
        # 同样回落 INFO，保证用户从历史版本升级不会拿到一个会让
        # logger 直接抛错的字段。
        raw_level = str(data.get("log_level", "INFO") or "INFO").upper()
        if raw_level in ("OFF", "INFO", "DEBUG"):
            cfg.log_level = raw_level
        else:
            # 包含 WARNING / ERROR / CRITICAL / 任意非法字符串
            cfg.log_level = "INFO"
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
        # ---- 视觉 / 多模态 ---- #
        cfg.vision_enabled = bool(data.get("vision_enabled", True))
        raw_wl = data.get("vision_model_whitelist")
        if isinstance(raw_wl, list) and raw_wl:
            cfg.vision_model_whitelist = [
                str(x).strip().lower() for x in raw_wl if str(x).strip()
            ]
        # else 走 dataclass 默认值
        # ---- IDE Bridge ---- #
        cfg.bridge_enabled = bool(data.get("bridge_enabled", False))
        cfg.bridge_host = str(
            data.get("bridge_host", "127.0.0.1") or "127.0.0.1",
        )
        try:
            port = int(data.get("bridge_port", 7003) or 7003)
            # 端口范围兜底，非法值回落默认
            cfg.bridge_port = port if 1 <= port <= 65535 else 7003
        except (TypeError, ValueError):
            cfg.bridge_port = 7003
        cfg.bridge_token = str(data.get("bridge_token", "") or "")
        cfg.bridge_dispatch_enabled = bool(
            data.get("bridge_dispatch_enabled", True),
        )
        try:
            cfg.bridge_dispatch_max_rounds = max(1, min(100, int(
                data.get("bridge_dispatch_max_rounds", 20) or 20,
            )))
        except (TypeError, ValueError):
            cfg.bridge_dispatch_max_rounds = 20
        try:
            cfg.bridge_dispatch_timeout_sec = max(10, min(3600, int(
                data.get("bridge_dispatch_timeout_sec", 300) or 300,
            )))
        except (TypeError, ValueError):
            cfg.bridge_dispatch_timeout_sec = 300
        # ---- 全局重试参数 ---- #
        try:
            cfg.llm_max_retries = max(0, min(10, int(
                data.get("llm_max_retries", 3) or 3,
            )))
        except (TypeError, ValueError):
            cfg.llm_max_retries = 3
        try:
            cfg.llm_retry_base_delay = max(0.0, min(60.0, float(
                data.get("llm_retry_base_delay", 1.0) or 1.0,
            )))
        except (TypeError, ValueError):
            cfg.llm_retry_base_delay = 1.0
        try:
            cfg.llm_retry_max_delay = max(0.0, min(60.0, float(
                data.get("llm_retry_max_delay", 10.0) or 10.0,
            )))
        except (TypeError, ValueError):
            cfg.llm_retry_max_delay = 10.0
        raw_codes = data.get("llm_retryable_status_codes")
        if isinstance(raw_codes, list) and raw_codes:
            cfg.llm_retryable_status_codes = [
                int(x) for x in raw_codes if isinstance(x, int)
            ]
        # ---- Skill 自动提议 ---- #
        cfg.enable_skill_proposal = bool(data.get("enable_skill_proposal", False))
        cfg.skill_proposal_min_actions = int(data.get("skill_proposal_min_actions", 3))
        # ---- 场景启动扫描（#4） ---- #
        cfg.enable_scene_startup_scan = bool(
            data.get("enable_scene_startup_scan", True),
        )
        # ---- 操作确认清单（#10） ---- #
        cfg.enable_approval_queue = bool(
            data.get("enable_approval_queue", False),
        )
        try:
            cfg.approval_threshold = max(1, min(50, int(
                data.get("approval_threshold", 3) or 3,
            )))
        except (TypeError, ValueError):
            cfg.approval_threshold = 3
        # ---- Cost 预算保护（#12） ---- #
        try:
            cfg.session_token_budget = max(0, int(
                data.get("session_token_budget", 0) or 0,
            ))
        except (TypeError, ValueError):
            cfg.session_token_budget = 0
        try:
            cfg.session_usd_budget = max(0.0, float(
                data.get("session_usd_budget", 0.0) or 0.0,
            ))
        except (TypeError, ValueError):
            cfg.session_usd_budget = 0.0
        # ---- 项目级记忆（#14） ---- #
        cfg.enable_project_memory = bool(
            data.get("enable_project_memory", True),
        )
        # ---- 共享资源目录 ---- #
        cfg.shared_resources_dir = str(
            data.get("shared_resources_dir", "") or "",
        )
        # ---- 运营商 / 模型引用 ---- #
        raw_providers = data.get("providers")
        if isinstance(raw_providers, list) and raw_providers:
            cfg.providers = [Provider.from_dict(p) for p in raw_providers]
        else:
            cfg.providers = []
        raw_ref = data.get("active_model_ref")
        if isinstance(raw_ref, (list, tuple)) and len(raw_ref) == 2:
            cfg.active_model_ref = [str(raw_ref[0]), str(raw_ref[1])]
        else:
            cfg.active_model_ref = None
        # 自动迁移：若无 providers 但有 profiles，则从 profiles 构建一份
        if not cfg.providers and cfg.profiles:
            providers, ref = _build_providers_from_profiles(
                cfg.profiles, cfg.active_profile,
            )
            cfg.providers = providers
            if ref is not None:
                cfg.active_model_ref = list(ref)
            # 记录迁移摘要（runtime 字段，不参与序列化）
            # 供 UI 在设置面板顶部展示迁移提示条
            cfg.migration_notice = {
                'from_profiles': len(cfg.profiles),
                'to_providers': len(providers),
                'to_models': sum(len(p.models) for p in providers),
                'legacy_profiles_snapshot': [
                    p.to_dict() for p in cfg.profiles
                ],
            }
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
    # 用 lazy logger，避免 logger.setup 过程中循环依赖
    try:
        _get_logger().warning(
            "包目录不可写，配置已回退到: %s", fallback,
        )
    except Exception:  # pylint: disable=broad-except
        # logger 自身异常时降级到 stderr，保证用户能看到提示
        print("[maxagent] 包目录不可写，配置已回退到: {}".format(fallback))
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
        # 注意：logger 由 setup_logging 反向 import config，故此处用 lazy
        _log = _get_logger()
        _log.warning("配置加载失败，使用默认: %s", exc)
        # 备份损坏文件，方便用户事后排查
        try:
            if os.path.exists(path):
                bak = path + ".corrupt"
                os.replace(path, bak)
                _log.info("损坏配置已备份到: %s", bak)
        except OSError as bak_exc:
            _log.warning("损坏配置备份失败: %s", bak_exc)
        cfg = AppConfig()
        cfg.profiles = [LLMProfile.from_dict(p) for p in BUILTIN_PROFILES]
        cfg.active_profile = cfg.profiles[0].name
        # 立刻把默认值写回磁盘，避免下次启动还是损坏状态
        try:
            save_config(cfg)
        except OSError as save_exc:
            _log.error("默认配置写盘失败: %s", save_exc)
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
    _get_logger().debug(
        "配置已保存: path=%s, profiles=%d, active=%s",
        path, len(cfg.profiles), cfg.active_profile,
    )


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
            _log = _get_logger()
            _log.warning("配置加载失败，使用默认: %s", exc)
            # 备份损坏文件，避免覆盖丢失现场
            try:
                if os.path.exists(path):
                    bak = path + ".corrupt"
                    os.replace(path, bak)
                    _log.info("损坏配置已备份到: %s", bak)
            except OSError as bak_exc:
                _log.warning("损坏配置备份失败: %s", bak_exc)
            cfg = AppConfig()
            cfg.profiles = [
                LLMProfile.from_dict(p) for p in BUILTIN_PROFILES
            ]
            cfg.active_profile = cfg.profiles[0].name
            # 立即把默认值写回磁盘，对齐正常首次启动路径行为
            try:
                self._save(cfg)
            except OSError as save_exc:
                _log.error("默认配置写盘失败: %s", save_exc)
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
        old = self._cfg.active_profile
        self._cfg.active_profile = name
        self.save()
        if old != name:
            _get_logger().info("切换激活 Profile: %s -> %s", old, name)

    def upsert_profile(self, profile: LLMProfile) -> None:
        """新增或就地更新 profile（按 name 匹配），并立即落盘。

        与 ``set_active_profile`` 行为对齐：所有变更类 API 都自动持久化，
        避免外部调用方忘记 ``save()`` 导致重启丢配置。
        """
        for i, p in enumerate(self._cfg.profiles):
            if p.name == profile.name:
                self._cfg.profiles[i] = profile
                self.save()
                _get_logger().info(
                    "Profile 已更新: name=%s, model=%s",
                    profile.name, profile.model,
                )
                return
        self._cfg.profiles.append(profile)
        self.save()
        _get_logger().info(
            "Profile 已新增: name=%s, model=%s, base_url=%s",
            profile.name, profile.model, profile.base_url,
        )

    def delete_profile(self, name: str) -> None:
        """删除 profile（不允许删除当前激活的），并立即落盘。"""
        if name == self._cfg.active_profile:
            raise ValueError("不能删除当前激活的 Profile")
        before = len(self._cfg.profiles)
        self._cfg.profiles = [
            p for p in self._cfg.profiles if p.name != name
        ]
        self.save()
        if len(self._cfg.profiles) < before:
            _get_logger().info("Profile 已删除: %s", name)

    # -------- 运营商 / 模型引用 --------
    def list_providers(self) -> List[Provider]:
        return list(self._cfg.providers)

    def get_provider(self, provider_id: str) -> Optional[Provider]:
        for p in self._cfg.providers:
            if p.id == provider_id:
                return p
        return None

    def get_active_model_ref(self) -> Optional[tuple]:
        ref = self._cfg.active_model_ref
        if ref and len(ref) == 2:
            return (ref[0], ref[1])
        return None

    def set_active_model_ref(self, provider_id: str, model_id: str) -> None:
        provider = self.get_provider(provider_id)
        if provider is None:
            raise ValueError("Provider 不存在: {}".format(provider_id))
        entry = next((m for m in provider.models if m.id == model_id), None)
        if entry is None:
            raise ValueError(
                "Provider {} 下不存在模型 {}".format(provider_id, model_id),
            )
        old = self._cfg.active_model_ref
        self._cfg.active_model_ref = [provider_id, model_id]
        # 同步 legacy active_profile：优先匹配同名 profile，用于兼容尚未
        # 切换到 provider 视图的老代码路径
        for prof in self._cfg.profiles:
            if prof.model == entry.model and prof.base_url == provider.base_url:
                self._cfg.active_profile = prof.name
                break
        self.save()
        if old != self._cfg.active_model_ref:
            _get_logger().info(
                "切换激活模型: %s -> (%s, %s)",
                old, provider_id, model_id,
            )

    def resolve_active_llm(self) -> Optional[LLMProfile]:
        """把 active_model_ref 解析为一个可用于发请求的 LLMProfile。

        运营商级 defaults + 模型级 overrides 合并后填入 LLMProfile。
        当 providers 为空或 active_model_ref 无效时回退到 legacy
        get_active_profile() 保证兼容。
        """
        ref = self.get_active_model_ref()
        if not ref:
            return self.get_active_profile()
        provider = self.get_provider(ref[0])
        if provider is None:
            return self.get_active_profile()
        entry = next(
            (m for m in provider.models if m.id == ref[1]),
            provider.get_default_model(),
        )
        if entry is None:
            return self.get_active_profile()
        merged = dict(provider.defaults or {})
        merged.update(entry.overrides or {})
        merged["name"] = "{} / {}".format(provider.name, entry.label or entry.model)
        merged["base_url"] = provider.base_url
        merged["api_key"] = provider.api_key
        merged["model"] = entry.model
        if provider.extra_headers:
            merged["extra_headers"] = dict(provider.extra_headers)
        # 只保留 LLMProfile 认识的字段
        valid = set(LLMProfile.__dataclass_fields__.keys())
        clean = {k: v for k, v in merged.items() if k in valid}
        return LLMProfile(**clean)


if __name__ == "__main__":
    # 简单自检
    c = load_config()
    print("Config dir:", get_config_dir())
    print("Active:", c.active_profile)
    for prof in c.profiles:
        print("  -", prof.name, prof.base_url, prof.model)
