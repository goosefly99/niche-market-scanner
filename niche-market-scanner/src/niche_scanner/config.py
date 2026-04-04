"""Configuration system for niche-market-scanner.

Loads Kalshi and Telegram credentials from environment variables via
pydantic-settings, and scanner/sizing/vertical settings from YAML files.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import field_validator
from pydantic_settings import BaseSettings

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_CONFIG_DIR = _PROJECT_ROOT / "config"


# ---------------------------------------------------------------------------
# Credential configs (env-driven)
# ---------------------------------------------------------------------------


class KalshiConfig(BaseSettings):
    """Kalshi API credentials loaded from environment variables."""

    KALSHI_API_KEY_ID: str = ""
    KALSHI_PRIVATE_KEY_PATH: str = ""
    KALSHI_ENV: str = "demo"

    @field_validator("KALSHI_ENV")
    @classmethod
    def _validate_env(cls, v: str) -> str:
        v = v.lower()
        if v not in ("demo", "production"):
            raise ValueError("KALSHI_ENV must be 'demo' or 'production'")
        return v

    @property
    def base_url(self) -> str:
        if self.KALSHI_ENV == "production":
            return "https://api.elections.kalshi.com/trade-api/v2"
        return "https://demo-api.kalshi.co/trade-api/v2"


class TelegramConfig(BaseSettings):
    """Telegram bot credentials loaded from environment variables."""

    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""


# ---------------------------------------------------------------------------
# YAML-driven settings
# ---------------------------------------------------------------------------


class ScannerSettings:
    """Loads scanner, sizing, and vertical settings from settings.yaml."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (_CONFIG_DIR / "settings.yaml")
        self._data: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        with open(self._path, encoding="utf-8") as fh:
            self._data = yaml.safe_load(fh) or {}

    @property
    def scanner(self) -> dict[str, Any]:
        return dict(self._data.get("scanner", {}))

    @property
    def sizing(self) -> dict[str, Any]:
        return dict(self._data.get("sizing", {}))

    @property
    def verticals(self) -> dict[str, Any]:
        return dict(self._data.get("verticals", {}))

    def is_vertical_enabled(self, name: str) -> bool:
        vertical = self._data.get("verticals", {}).get(name, {})
        return bool(vertical.get("enabled", False))


class ICAOStations:
    """Loads verified ICAO station mappings from icao_stations.yaml.

    Only returns stations that have been explicitly verified against Kalshi
    resolution rules.  An empty ``stations`` dict means no cities are verified
    and all lookups will return ``None``.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (_CONFIG_DIR / "icao_stations.yaml")
        self._stations: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        with open(self._path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        self._stations = data.get("stations", {}) or {}

    def get_icao(self, city: str) -> str | None:
        """Return the verified ICAO code for *city*, or ``None``."""
        return self._stations.get(city)

    def all_verified(self) -> dict[str, str]:
        """Return a copy of all verified city -> ICAO mappings."""
        return dict(self._stations)
