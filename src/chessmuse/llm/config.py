"""Explicit configuration loading without modifying the process environment."""

import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values


class ConfigurationError(ValueError):
    """Configuration errors whose messages never include configured values."""


@dataclass(frozen=True)
class DeepSeekSettings:
    api_key: str = field(repr=False)
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-flash"
    thinking: str = "disabled"
    max_tokens: int = 2048
    timeout_seconds: float = 60
    max_retries: int = 2

    def __post_init__(self):
        if not self.api_key or not self.api_key.isascii() or any(c.isspace() for c in self.api_key):
            raise ConfigurationError("请在 .env 或环境变量中设置有效的 DEEPSEEK_API_KEY")
        try:
            url = urlsplit(self.base_url)
            valid_url = (url.scheme == "https" and url.hostname and url.port != 0
                         and not url.username and not url.password
                         and not url.query and not url.fragment)
        except ValueError:
            valid_url = False
        if not valid_url:
            raise ConfigurationError("DEEPSEEK_BASE_URL 必须是无凭据和查询参数的 HTTPS 地址")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", self.model):
            raise ConfigurationError("DEEPSEEK_MODEL 必须是有效的模型名称")
        if self.thinking not in {"enabled", "disabled"}:
            raise ConfigurationError("DEEPSEEK_THINKING 只能是 enabled 或 disabled")
        if not 1 <= self.max_tokens <= 393216:
            raise ConfigurationError("DEEPSEEK_MAX_TOKENS 必须在 1 到 393216 之间")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ConfigurationError("DEEPSEEK_TIMEOUT_SECONDS 必须是有限的正数")
        if not 0 <= self.max_retries <= 5:
            raise ConfigurationError("DEEPSEEK_MAX_RETRIES 必须在 0 到 5 之间")

    @classmethod
    def from_env(cls, env_file: str | Path = ".env") -> "DeepSeekSettings":
        """Read a selected .env (default: current directory); environment wins.

        No parent-directory search, interpolation or import-time loading. An
        explicitly empty environment variable also overrides the .env value.
        """
        values = {**dotenv_values(env_file, encoding="utf-8-sig", interpolate=False), **os.environ}

        def value(name, default):
            raw = values.get(name, default)
            return raw.strip() if isinstance(raw, str) else ""

        def number(name, default, convert):
            try:
                return convert(value(name, default))
            except (ValueError, TypeError):
                raise ConfigurationError(f"{name} 的数值格式无效") from None

        return cls(
            api_key=value("DEEPSEEK_API_KEY", ""),
            base_url=value("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            model=value("DEEPSEEK_MODEL", "deepseek-flash"),
            thinking=value("DEEPSEEK_THINKING", "disabled"),
            max_tokens=number("DEEPSEEK_MAX_TOKENS", "2048", int),
            timeout_seconds=number("DEEPSEEK_TIMEOUT_SECONDS", "60", float),
            max_retries=number("DEEPSEEK_MAX_RETRIES", "2", int),
        )
