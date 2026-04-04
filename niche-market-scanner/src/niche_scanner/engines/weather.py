"""Weather edge engine using NOAA forecasts for Kalshi temperature markets."""

from __future__ import annotations

import logging
import math
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx
from scipy.stats import norm

from niche_scanner.config import ICAOStations
from niche_scanner.engines.base import EdgeEngine, EdgeSignal
from niche_scanner.kalshi.models import Market, OrderBook
from niche_scanner.sizing.fees import round_trip_fee_pp

logger = logging.getLogger(__name__)

# Kalshi weather ticker city abbreviation -> canonical city name used in ICAO
# stations config.  Extend as Kalshi adds new cities.
_TICKER_CITY_MAP: dict[str, str] = {
    "NYC": "new_york",
    "CHI": "chicago",
    "DAL": "dallas",
    "MIA": "miami",
    "DEN": "denver",
    "ATL": "atlanta",
    "AUS": "austin",
    "LA": "los_angeles",
    "PHX": "phoenix",
    "SEA": "seattle",
}

_NOAA_HOURLY_URL = (
    "https://api.weather.gov/gridpoints/{office}/{grid_x},{grid_y}"
    "/forecast/hourly"
)

# Pre-compiled patterns for parsing Kalshi weather subtitles.
_RE_RANGE = re.compile(r"(\d+)\s*F?\s*to\s*(\d+)\s*F?", re.IGNORECASE)
_RE_ABOVE = re.compile(r"(\d+)\s*(?:F\s*)?or\s+above", re.IGNORECASE)
_RE_BELOW = re.compile(r"[Bb]elow\s+(\d+)")


# ---------------------------------------------------------------------------
# NOAAForecast dataclass
# ---------------------------------------------------------------------------


@dataclass
class NOAAForecast:
    """Gaussian forecast for a location derived from NOAA hourly data."""

    temperature_f: float
    uncertainty_f: float  # standard deviation in Fahrenheit

    def bucket_probability(self, low_f: float, high_f: float) -> float:
        """P(low <= T < high) under the normal model."""
        d = norm(loc=self.temperature_f, scale=self.uncertainty_f)
        return float(d.cdf(high_f) - d.cdf(low_f))

    def threshold_probability(
        self,
        threshold_f: float,
        direction: str = "above",
    ) -> float:
        """P(T >= threshold) when direction='above', else P(T < threshold)."""
        d = norm(loc=self.temperature_f, scale=self.uncertainty_f)
        if direction == "above":
            return float(1.0 - d.cdf(threshold_f))
        return float(d.cdf(threshold_f))

    @property
    def is_boundary_adjacent(self) -> bool:
        """True if the forecast mean is within 1 std dev of a 5F boundary."""
        nearest_boundary = round(self.temperature_f / 5.0) * 5.0
        return abs(self.temperature_f - nearest_boundary) <= self.uncertainty_f


# ---------------------------------------------------------------------------
# WeatherEdgeEngine
# ---------------------------------------------------------------------------


class WeatherEdgeEngine(EdgeEngine):
    """Detect edges on Kalshi temperature markets using NOAA forecasts."""

    def __init__(
        self,
        icao_stations: ICAOStations,
        min_edge_pp: float = 12.0,
        noaa_cache_ttl_sec: int = 900,
    ) -> None:
        self._stations = icao_stations
        self._min_edge_pp = min_edge_pp
        self._cache_ttl = noaa_cache_ttl_sec
        self._cache: dict[str, tuple[float, NOAAForecast]] = {}

    # -- ticker parsing -----------------------------------------------------

    @staticmethod
    def _parse_city_from_ticker(ticker: str) -> str | None:
        """Extract the canonical city key from a Kalshi weather ticker.

        Kalshi weather tickers typically follow patterns like
        ``KXHIGHNY-26APR4-T58`` or ``HIGHNY-...``. This method
        searches for known city abbreviations in the ticker.
        """
        upper = ticker.upper()
        for abbr, city in _TICKER_CITY_MAP.items():
            if abbr in upper:
                return city
        return None

    # -- subtitle bucket parsing --------------------------------------------

    @staticmethod
    def _parse_bucket(subtitle: str) -> tuple[float, float, str] | None:
        """Parse a Kalshi weather subtitle into (low, high, kind).

        Returns:
            ``(low, high, "range")`` for "55F to 60F",
            ``(threshold, inf, "above")`` for "58 or above",
            ``(-inf, threshold, "below")`` for "Below 50",
            or ``None`` if the subtitle is not recognised.
        """
        m = _RE_RANGE.search(subtitle)
        if m:
            return (float(m.group(1)), float(m.group(2)), "range")

        m = _RE_ABOVE.search(subtitle)
        if m:
            return (float(m.group(1)), math.inf, "above")

        m = _RE_BELOW.search(subtitle)
        if m:
            return (-math.inf, float(m.group(1)), "below")

        return None

    # -- NOAA API -----------------------------------------------------------

    async def _fetch_noaa_forecast(
        self,
        city: str,
    ) -> NOAAForecast | None:
        """Fetch NOAA hourly forecast for *city*, with in-memory caching.

        Estimates uncertainty from the temperature spread across the next
        6 hourly periods.
        """
        now = time.monotonic()

        # Check cache
        if city in self._cache:
            ts, cached = self._cache[city]
            if now - ts < self._cache_ttl:
                return cached

        station = self._stations.get_station(city)
        if station is None:
            return None

        office = station.get("office")
        grid_x = station.get("grid_x")
        grid_y = station.get("grid_y")

        if not all((office, grid_x is not None, grid_y is not None)):
            logger.warning("Incomplete station config for %s", city)
            return None

        url = _NOAA_HOURLY_URL.format(
            office=office,
            grid_x=grid_x,
            grid_y=grid_y,
        )

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    url,
                    headers={"User-Agent": "niche-market-scanner/0.1"},
                    timeout=10.0,
                )
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, Exception):
            logger.exception("NOAA fetch failed for %s", city)
            return None

        forecast = self._extract_forecast(data)
        if forecast is not None:
            self._cache[city] = (now, forecast)
        return forecast

    @staticmethod
    def _extract_forecast(data: dict[str, Any]) -> NOAAForecast | None:
        """Build a NOAAForecast from NOAA hourly JSON response.

        Takes the first (most imminent) temperature as the mean and
        estimates uncertainty from the standard deviation of the next
        6 hourly readings.
        """
        periods = data.get("properties", {}).get("periods", [])
        if not periods:
            return None

        temps = [
            p["temperature"]
            for p in periods[:6]
            if isinstance(p.get("temperature"), (int, float))
        ]
        if not temps:
            return None

        mean_temp = temps[0]

        if len(temps) >= 2:
            avg = sum(temps) / len(temps)
            variance = sum((t - avg) ** 2 for t in temps) / len(temps)
            std_dev = max(math.sqrt(variance), 1.0)
        else:
            std_dev = 3.0  # sensible default

        return NOAAForecast(
            temperature_f=float(mean_temp),
            uncertainty_f=std_dev,
        )

    # -- edge evaluation ----------------------------------------------------

    def _evaluate_market(
        self,
        market: Market,
        forecast: NOAAForecast,
    ) -> EdgeSignal | None:
        """Score a single market against the forecast, return signal or None."""
        bucket = self._parse_bucket(market.subtitle)
        if bucket is None:
            return None

        low, high, kind = bucket

        # Compute model probability for YES outcome
        if kind == "range":
            model_prob = forecast.bucket_probability(low, high)
        elif kind == "above":
            model_prob = forecast.threshold_probability(low, direction="above")
        else:  # below
            model_prob = forecast.threshold_probability(high, direction="below")

        # Try both YES and NO sides
        best_signal: EdgeSignal | None = None

        for side, m_prob in [("yes", model_prob), ("no", 1.0 - model_prob)]:
            if side == "yes":
                price_cents = market.yes_ask
            else:
                price_cents = market.no_ask

            if price_cents <= 0 or price_cents >= 100:
                continue

            market_prob = price_cents / 100.0
            raw_edge = (m_prob - market_prob) * 100.0  # percentage points
            fee_cost = round_trip_fee_pp(price_cents)
            adjusted_edge = raw_edge - fee_cost

            if adjusted_edge < self._min_edge_pp:
                continue

            # Confidence: higher when boundary-adjacent (richer signal)
            confidence = min(adjusted_edge / 30.0, 1.0)
            if forecast.is_boundary_adjacent:
                confidence = min(confidence * 1.2, 1.0)

            thesis = (
                f"NOAA forecast {forecast.temperature_f:.0f}F "
                f"(+/-{forecast.uncertainty_f:.1f}F) implies "
                f"{side.upper()} prob {m_prob:.1%} vs market "
                f"{market_prob:.1%} on '{market.subtitle}'"
            )

            signal = EdgeSignal(
                engine="weather",
                ticker=market.ticker,
                side=side,
                model_prob=m_prob,
                market_prob=market_prob,
                edge_pp=raw_edge,
                fee_adjusted_edge=adjusted_edge,
                confidence=confidence,
                thesis=thesis,
                metadata={
                    "forecast_temp_f": forecast.temperature_f,
                    "forecast_std_f": forecast.uncertainty_f,
                    "bucket": (low, high, kind),
                    "boundary_adjacent": forecast.is_boundary_adjacent,
                },
            )

            if best_signal is None or signal.fee_adjusted_edge > best_signal.fee_adjusted_edge:
                best_signal = signal

        return best_signal

    # -- scan ---------------------------------------------------------------

    async def scan(
        self,
        markets: list[Market],
        orderbooks: dict[str, OrderBook],
    ) -> list[EdgeSignal]:
        """Evaluate all weather markets and return detected edges."""
        # Group markets by city
        city_markets: dict[str, list[Market]] = {}
        for market in markets:
            city = self._parse_city_from_ticker(market.ticker)
            if city is None:
                continue
            city_markets.setdefault(city, []).append(market)

        signals: list[EdgeSignal] = []

        for city, city_mkts in city_markets.items():
            # Skip unverified cities
            if self._stations.get_icao(city) is None:
                logger.debug("Skipping unverified city: %s", city)
                continue

            forecast = await self._fetch_noaa_forecast(city)
            if forecast is None:
                logger.warning("No forecast available for %s", city)
                continue

            for mkt in city_mkts:
                signal = self._evaluate_market(mkt, forecast)
                if signal is not None:
                    signals.append(signal)

        return signals
