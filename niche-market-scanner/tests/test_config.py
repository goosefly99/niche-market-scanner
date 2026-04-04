"""Tests for configuration system (config.py)."""

from __future__ import annotations

from pathlib import Path

import yaml


class TestScannerSettings:
    """Tests for YAML-based scanner settings."""

    def test_loads_from_yaml(self, tmp_path: Path) -> None:
        from niche_scanner.config import ScannerSettings

        yaml_path = tmp_path / "settings.yaml"
        yaml_path.write_text(yaml.dump({
            "scanner": {"weather_interval_min": 10},
            "sizing": {"kelly_fraction": 0.3, "paper_trade": True},
            "verticals": {"weather": {"enabled": True}, "sports": {"enabled": False}},
        }))
        settings = ScannerSettings(path=yaml_path)
        assert settings.scanner["weather_interval_min"] == 10
        assert settings.sizing["kelly_fraction"] == 0.3
        assert settings.is_vertical_enabled("weather") is True
        assert settings.is_vertical_enabled("sports") is False

    def test_missing_vertical_returns_false(self, tmp_path: Path) -> None:
        from niche_scanner.config import ScannerSettings

        yaml_path = tmp_path / "settings.yaml"
        yaml_path.write_text(yaml.dump({"verticals": {}}))
        settings = ScannerSettings(path=yaml_path)
        assert settings.is_vertical_enabled("nonexistent") is False

    def test_default_settings_load(self) -> None:
        """The shipped config/settings.yaml loads without error."""
        from niche_scanner.config import ScannerSettings

        settings = ScannerSettings()  # Uses default path
        assert settings.sizing.get("paper_trade") is True
        assert settings.is_vertical_enabled("weather") is True
        assert settings.is_vertical_enabled("economics") is True


class TestICAOStations:
    """Tests for ICAO station loading and lookup."""

    def test_get_icao_verified(self, tmp_path: Path) -> None:
        from niche_scanner.config import ICAOStations

        yaml_path = tmp_path / "icao.yaml"
        yaml_path.write_text(yaml.dump({
            "stations": {
                "new_york": {
                    "icao": "KNYC", "station": "NYC",
                    "office": "OKX", "grid_x": 34, "grid_y": 38,
                    "verified": True,
                },
            },
        }))
        stations = ICAOStations(path=yaml_path)
        assert stations.get_icao("new_york") == "KNYC"

    def test_get_icao_unverified_returns_none(self, tmp_path: Path) -> None:
        from niche_scanner.config import ICAOStations

        yaml_path = tmp_path / "icao.yaml"
        yaml_path.write_text(yaml.dump({
            "stations": {
                "test_city": {"icao": "KXXX", "verified": False},
            },
        }))
        stations = ICAOStations(path=yaml_path)
        assert stations.get_icao("test_city") is None

    def test_get_icao_missing_city_returns_none(self, tmp_path: Path) -> None:
        from niche_scanner.config import ICAOStations

        yaml_path = tmp_path / "icao.yaml"
        yaml_path.write_text(yaml.dump({"stations": {}}))
        stations = ICAOStations(path=yaml_path)
        assert stations.get_icao("nowhere") is None

    def test_all_verified(self, tmp_path: Path) -> None:
        from niche_scanner.config import ICAOStations

        yaml_path = tmp_path / "icao.yaml"
        yaml_path.write_text(yaml.dump({
            "stations": {
                "a": {"icao": "KA", "verified": True},
                "b": {"icao": "KB", "verified": False},
                "c": {"icao": "KC", "verified": True},
            },
        }))
        stations = ICAOStations(path=yaml_path)
        verified = stations.all_verified()
        assert verified == {"a": "KA", "c": "KC"}

    def test_all_series_tickers(self, tmp_path: Path) -> None:
        from niche_scanner.config import ICAOStations

        yaml_path = tmp_path / "icao.yaml"
        yaml_path.write_text(yaml.dump({
            "stations": {
                "city1": {
                    "icao": "K1", "verified": True,
                    "series_tickers": ["KXHIGH1", "KXLOW1"],
                },
                "city2": {
                    "icao": "K2", "verified": True,
                    "series_tickers": ["KXHIGH2"],
                },
                "city3": {"icao": "K3", "verified": False, "series_tickers": ["SKIP"]},
            },
        }))
        stations = ICAOStations(path=yaml_path)
        tickers = stations.all_series_tickers()
        assert set(tickers) == {"KXHIGH1", "KXLOW1", "KXHIGH2"}

    def test_get_station_returns_full_dict(self, tmp_path: Path) -> None:
        from niche_scanner.config import ICAOStations

        yaml_path = tmp_path / "icao.yaml"
        yaml_path.write_text(yaml.dump({
            "stations": {
                "nyc": {
                    "icao": "KNYC", "station": "NYC",
                    "office": "OKX", "grid_x": 34, "grid_y": 38,
                    "verified": True,
                },
            },
        }))
        stations = ICAOStations(path=yaml_path)
        station = stations.get_station("nyc")
        assert station is not None
        assert station["office"] == "OKX"
        assert station["grid_x"] == 34

    def test_default_icao_loads_20_cities(self) -> None:
        """The shipped icao_stations.yaml has 20 verified cities."""
        from niche_scanner.config import ICAOStations

        stations = ICAOStations()  # Uses default path
        verified = stations.all_verified()
        assert len(verified) == 20

    def test_plain_string_format(self, tmp_path: Path) -> None:
        """Plain string entries (city: 'ICAO') are treated as verified."""
        from niche_scanner.config import ICAOStations

        yaml_path = tmp_path / "icao.yaml"
        yaml_path.write_text(yaml.dump({
            "stations": {"simple_city": "KSMP"},
        }))
        stations = ICAOStations(path=yaml_path)
        assert stations.get_icao("simple_city") == "KSMP"
        assert stations.all_verified() == {"simple_city": "KSMP"}


class TestKalshiConfig:
    """Tests for Kalshi API configuration."""

    def test_demo_base_url(self, monkeypatch) -> None:
        from niche_scanner.config import KalshiConfig

        monkeypatch.setenv("KALSHI_API_KEY_ID", "test")
        monkeypatch.setenv("KALSHI_PRIVATE_KEY", "/tmp/key.pem")
        monkeypatch.setenv("KALSHI_ENV", "demo")
        cfg = KalshiConfig()
        assert "demo" in cfg.base_url

    def test_production_base_url(self, monkeypatch) -> None:
        from niche_scanner.config import KalshiConfig

        monkeypatch.setenv("KALSHI_API_KEY_ID", "test")
        monkeypatch.setenv("KALSHI_PRIVATE_KEY", "/tmp/key.pem")
        monkeypatch.setenv("KALSHI_ENV", "production")
        cfg = KalshiConfig()
        assert "elections.kalshi.com" in cfg.base_url
