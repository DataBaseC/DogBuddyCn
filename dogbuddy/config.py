# DogBuddyCn — Configuration loading & validation (L5 Infrastructure)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""YAML configuration with pydantic validation.

Sections: llm (+retry), gateway, web, panel, paths, agents.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, ClassVar, Literal

import yaml
from pydantic import BaseModel, Field


def default_data_dir() -> Path:
    """Platform default data dir: /etc/dogbuddy (POSIX) or %LOCALAPPDATA%\\dogbuddy (Windows)."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
        return Path(base) / "dogbuddy"
    return Path("/etc/dogbuddy")


class RetryConfig(BaseModel):
    """LLM rate-limit retry policy (M-H)."""

    base_interval: float = Field(default=10.0, ge=0.1, description="first backoff seconds")
    max_interval: float = Field(default=300.0, ge=1.0, description="backoff cap seconds")
    max_retries: int = Field(default=5, ge=0, le=50)
    backoff: Literal["exp", "fixed"] = "exp"


class LLMKeyConfig(BaseModel):
    api_key: str = ""
    base_url: str = ""
    model: str = ""


class LLMConfig(BaseModel):
    provider: Literal["openai_compat", "ollama"] = "openai_compat"
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    api_key: str = ""
    keys: list[str] = Field(default_factory=list, description="extra keys, kept for config only")
    temperature: float = 0.7
    max_context_tokens: int = Field(default=128000, ge=2000)
    compact_threshold: float = Field(default=0.8, ge=0.3, le=0.95)
    request_timeout: float = Field(default=120.0, ge=5.0)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    ollama_base_url: str = "http://127.0.0.1:11434"


class GatewayConfig(BaseModel):
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 8095
    access_token: str = ""
    whitelist: list[int] = Field(default_factory=list, description="allowed QQ user ids")
    # default push target for task reports: {"type": "private"|"group", "id": 123}
    push_target: dict[str, Any] = Field(default_factory=dict)


class WebConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8100
    auth_token: str = ""
    password: str = ""


class PanelConfig(BaseModel):
    enabled: bool = True
    base_url: str = "http://127.0.0.1:8080"
    agent_token: str = ""


class PathsConfig(BaseModel):
    data_dir: str = ""  # empty → platform default (/etc/dogbuddy or %LOCALAPPDATA%\dogbuddy)
    workspaces_dir: str = ""  # empty → <data_dir>/workspaces

    @property
    def data(self) -> Path:
        return Path(self.data_dir) if self.data_dir else default_data_dir()

    @property
    def workspaces(self) -> Path:
        return Path(self.workspaces_dir) if self.workspaces_dir else self.data / "workspaces"


class NotifyChannelConfig(BaseModel):
    """One outbound report channel. `type`: console | gateway | webhook."""
    type: str = "console"
    name: str = ""           # channel name for per-job routing (defaults to type)
    enabled: bool = True
    url: str = ""            # webhook target
    target_type: str = ""    # gateway: private | group (empty → config.gateway.push_target)
    target_id: str = ""


class NotifyConfig(BaseModel):
    channels: list[NotifyChannelConfig] = Field(default_factory=list)  # empty → console only


class UpdateConfig(BaseModel):
    """Self-upgrade defaults (see manage.py update)."""

    source: Literal["git", "bundle"] = "git"
    ref: str = "origin/main"           # git ref followed when source=git
    bundle_url: str = ""               # used when source=bundle
    health_tries: int = Field(default=30, ge=1, le=600)
    health_delay: float = Field(default=1.0, ge=0.1, le=60.0)


class AppConfig(BaseModel):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    llm_profiles: dict[str, LLMConfig] = Field(
        default_factory=dict, description="named LLM config sets; `llm` is the default one")
    active_llm_profile: str = Field(default="", description="profile name for new windows; empty = llm section")
    max_iterations: int = Field(default=25, ge=1, le=200,
                                description="agent tool-loop rounds per run (windows may override)")
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    panel: PanelConfig = Field(default_factory=PanelConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    notify: NotifyConfig = Field(default_factory=NotifyConfig)
    update: UpdateConfig = Field(default_factory=UpdateConfig)

    # ---- LLM profiles ----
    def llm_for(self, profile: str | None = None) -> LLMConfig:
        """Resolve a named profile; empty/None → active profile → default `llm` section."""
        name = profile or self.active_llm_profile
        if name and name in self.llm_profiles:
            return self.llm_profiles[name]
        return self.llm

    def effective_model(self, profile: str | None = None, model: str = "") -> str:
        """The model a window/run actually uses: explicit override → profile → default.

        Single source of truth for the resolution rule; the provider registry and
        the console DTOs both call it so the UI can never disagree with reality.
        """
        return model or self.llm_for(profile).model

    # ---- per-window parameter resolution (Batch 1 / ROADMAP T1.1) ----
    #: every parameter a window may override; anything else in window.params is ignored
    #: `trust` (T4.2): "normal" | "elevated" — elevated may run allowlisted danger
    #: `task_timeout`（v11）：cron 单轮等待上限。**此前漏在这个白名单里**，于是
    #: 通过 API/项目层设的 task_timeout 会被 `effective_params()` 静默丢弃，
    #: `WindowManager._wait_timeout()` 永远退回 1800s —— 用户设的 15s 形同无效。
    WINDOW_PARAM_KEYS: ClassVar[tuple[str, ...]] = (
        "max_context_tokens", "compact_threshold", "temperature",
        "request_timeout", "max_iterations", "task_timeout", "retry", "trust")

    def effective_params(self, window: Any = None, profile: str | None = None,
                         project: Any = None) -> dict[str, Any]:
        """Full parameter set a window actually runs with.

        Resolution chain (T7.2 extends T1.1 with the project layer):
          **window.params → project.params → bound profile → active profile → global defaults**

        项目是比工位**弱**的一层（项目定默认，工位可覆盖）；`retry` 逐键合并，
        所以项目只调 `max_retries`、工位只调 `base_interval` 也能叠加。
        `project` 可以是 Project 实体、dict，或 None（= 没挂项目）。
        """
        section = self.llm_for(profile or (getattr(window, "llm_profile", "") or None))
        raw_retry = getattr(section, "retry", None)
        if hasattr(raw_retry, "model_dump"):
            retry_dump = raw_retry.model_dump()
        elif isinstance(raw_retry, dict):
            retry_dump = dict(raw_retry)
        else:
            retry_dump = self.llm.retry.model_dump()
        params: dict[str, Any] = {
            "max_context_tokens": getattr(section, "max_context_tokens",
                                          self.llm.max_context_tokens),
            "compact_threshold": getattr(section, "compact_threshold",
                                         self.llm.compact_threshold),
            "temperature": getattr(section, "temperature", self.llm.temperature),
            "request_timeout": getattr(section, "request_timeout",
                                       self.llm.request_timeout),
            "max_iterations": self.max_iterations,
            # 与 WindowManager.CRON_WAIT_TIMEOUT 对齐：超时只代表"这轮不再等"，
            # 任务继续在后台跑，不会计失败。
            "task_timeout": 1800.0,
            "retry": retry_dump,
            "trust": "normal",
        }
        for layer in (project, window):
            overrides = self._layer_params(layer)
            for key, value in overrides.items():
                if key not in self.WINDOW_PARAM_KEYS or value is None:
                    continue
                if key == "retry" and isinstance(value, dict):
                    params["retry"] = {**params["retry"],
                                       **{k: v for k, v in value.items() if v is not None}}
                else:
                    params[key] = value
        return params

    @staticmethod
    def _layer_params(layer: Any) -> dict[str, Any]:
        """从 Project 实体 / dict / None 里取出参数覆盖。"""
        if layer is None:
            return {}
        if isinstance(layer, dict):
            return layer
        return getattr(layer, "params", None) or {}

    def param_sources(self, window: Any = None, profile: str | None = None,
                      project: Any = None) -> dict[str, str]:
        """每个参数来自哪一层 —— 控制台的「继承中/已覆盖」标签用它。

        取值：`window`（工位覆盖）> `project`（项目覆盖）> `inherited`（跟全局/profile）。
        """
        win_params = self._layer_params(window)
        proj_params = self._layer_params(project)
        out: dict[str, str] = {}
        for key in self.WINDOW_PARAM_KEYS:
            if key in win_params and win_params[key] is not None:
                out[key] = "window"
            elif key in proj_params and proj_params[key] is not None:
                out[key] = "project"
            else:
                out[key] = "inherited"
        return out

    def profile_names(self) -> list[str]:
        return [""] + sorted(self.llm_profiles)

    def save_profile(self, name: str, cfg: LLMConfig) -> None:
        if not name or name == "default":
            raise ValueError("profile name must be a non-empty name other than 'default'")
        self.llm_profiles[name] = cfg

    def delete_profile(self, name: str) -> bool:
        return self.llm_profiles.pop(name, None) is not None

    # ---- convenience accessors ----
    @property
    def db_path(self) -> Path:
        return self.paths.data / "dogbuddy.db"

    @property
    def memory_dir(self) -> Path:
        return self.paths.data / "memory"

    @property
    def logs_dir(self) -> Path:
        return self.paths.data / "logs"

    @property
    def checkpoints_dir(self) -> Path:
        return self.paths.data / "checkpoints"


#: 掩码前缀（见 `mask_secret`）——所有"要出接口的密钥"统一用它
SECRET_MASK = "********"


def mask_secret(value: str | None, keep_tail: int = 4, min_len: int = 12) -> str:
    """把密钥变成可以出接口的掩码：`sk-abcdef123456` → `********3456`。

    这是**唯一**的密钥掩码口径（LLM key / 面板 agent token 都走它）。
    太短的密钥连尾巴都不给看：4 位尾巴对 8 位的 key 等于泄露一半。
    """
    v = (value or "").strip()
    if not v:
        return ""
    return SECRET_MASK + (v[-keep_tail:] if len(v) >= min_len else "")


def is_masked_secret(value: str | None) -> bool:
    """前端把掩码回传时表示"没改密钥"，绝不能把掩码当成新密钥存下去。"""
    return bool(value) and str(value).strip().startswith(SECRET_MASK)


def _env_overrides(cfg: AppConfig) -> AppConfig:
    """Environment variables override YAML (secrets stay out of files)."""
    if v := os.environ.get("DOGBUDDY_LLM_API_KEY"):
        cfg.llm.api_key = v
    if v := os.environ.get("DOGBUDDY_LLM_BASE_URL"):
        cfg.llm.base_url = v
    if v := os.environ.get("DOGBUDDY_LLM_MODEL"):
        cfg.llm.model = v
    if v := os.environ.get("DOGBUDDY_PANEL_TOKEN"):
        cfg.panel.agent_token = v
    if v := os.environ.get("DOGBUDDY_WEB_TOKEN"):
        cfg.web.auth_token = v
    if v := os.environ.get("DOGBUDDY_GATEWAY_TOKEN"):
        cfg.gateway.access_token = v
    return cfg


# env var → (section, field) it overrides; used by save_config to avoid
# writing environment-supplied secrets into the YAML file.
_ENV_BINDINGS: dict[str, tuple[str, str]] = {
    "DOGBUDDY_LLM_API_KEY": ("llm", "api_key"),
    "DOGBUDDY_LLM_BASE_URL": ("llm", "base_url"),
    "DOGBUDDY_LLM_MODEL": ("llm", "model"),
    "DOGBUDDY_PANEL_TOKEN": ("panel", "agent_token"),
    "DOGBUDDY_WEB_TOKEN": ("web", "auth_token"),
    "DOGBUDDY_GATEWAY_TOKEN": ("gateway", "access_token"),
}


def config_path(path: str | Path | None = None) -> Path:
    """Resolve the config file path the same way `load_config` does."""
    if path is None:
        path = os.environ.get("DOGBUDDY_CONFIG") or str(default_data_dir() / "config.yaml")
    return Path(path)


def load_config(path: str | Path | None = None) -> AppConfig:
    """Load YAML config; missing file yields defaults. Env vars win over YAML."""
    path = config_path(path)
    data: dict[str, Any] = {}
    if path.exists():
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    cfg = AppConfig.model_validate(data)
    return _env_overrides(cfg)


def save_config(cfg: AppConfig, path: str | Path | None = None) -> Path:
    """Atomically persist `cfg` to the YAML config file.

    Guarantees:
      * temp file in the same directory + ``os.replace`` → never a half file
      * ``os.fsync`` before replace → survives power loss
      * previous file kept as ``<name>.bak``
      * permissions forced to 0600 (best effort on Windows)
      * values that came from the environment are **not** written back, so a
        token injected via `DOGBUDDY_*` never leaks into the file

    This is the single write path for configuration: every settings endpoint,
    admin tool and CLI edit must go through it, otherwise the change dies with
    the process (the v1.3 defect this replaces).
    """
    p = config_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # P2-12：目录不可写时 `mkstemp` 会按 TMP_MAX 次重试随机名，**长时间空转**
    # （审查报告实测 222s 不返回），调用方拿不到快速失败。先探一下，立刻报错。
    if not os.access(p.parent, os.W_OK):
        raise PermissionError(
            f"配置目录不可写：{p.parent}（无法保存配置；请检查权限或磁盘状态）")
    data = cfg.model_dump(mode="json")
    for env, (section, field) in _ENV_BINDINGS.items():
        if os.environ.get(env):
            data.get(section, {}).pop(field, None)

    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)

    fd, tmp_name = tempfile.mkstemp(prefix=p.name + ".", suffix=".tmp", dir=str(p.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(tmp, 0o600)
        except OSError:  # Windows / exotic filesystems
            pass
        if p.exists():
            try:
                shutil.copy2(p, Path(str(p) + ".bak"))
            except OSError:
                pass
        os.replace(tmp, p)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    return p

