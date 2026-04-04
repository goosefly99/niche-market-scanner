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

# Kalshi weather ticker city suffix -> canonical city name used in ICAO
# stations config.  Kalshi embeds the city code as a suffix on the series
# prefix, e.g. KXHIGHNY -> "NY" -> new_york, KXHIGHCHI -> "CHI" -> chicago.
# Ordered longest-first so that "MSP" matches before "SP", etc.
_TICKER_CITY_MAP: dict[str, str] = {
    "CHI": "chicago",
    "DAL": "dallas",
    "MIA": "miami",
    "DEN": "denver",
    "ATL": "atlanta",
    "AUS": "austin",
    "PHX": "phoenix",
    "SEA": "seattle",
    "BOS": "boston",
    "HOU": "houston",
    "PHL": "philadelphia",
    "SFO": "san_francisco",
    "DCA": "washington_dc",
    "SAT": "san_antonio",
    "LAS": "las_vegas",
    "MSP": "minneapolis",
    "MSY": "new_orleans",
    "OKC": "oklahoma_city",
    "NY": "new_york",
    "LA": "los_angeles",
}

_NOAA_HOURLY_URL = (
    "https://api.weather.gov/gridpoints/{office}/{grid_x},{grid_y}"
    "/forecast/hourly"
)

# Pre-compiled patterns for parsing Kalshi weather tickers.
# Series prefix: KXHIGH, KXLOW, HIGH, LOW (followed by city suffix).
_RE_SERIES_PREFIX = re.compile(r"^(?:KX)?(?:HIGH|LOW)", re.IGNORECASE)

# Strike segment (last hyphen-delimited part): T75 (threshold), B74.5 (boundary).
_RE_STRIKE = re.compile(r"^([TB])(-?\d+(?:\.\d+)?)$", re.IGNORECASE)

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

        Kalshi weather tickers embed the city code as a suffix on the series
        prefix.  Examples::

            KXHIGHNY-26APR04-T75   -> "NY"  -> new_york
            KXHIGHCHI-26APR04-B74  -> "CHI" -> chicago
            KXLOWMIA-26APR04-T60  -> "MIA" -> miami

        The method strips the known series prefix (``KXHIGH``, ``KXLOW``,
        ``HIGH``, ``LOW``), takes the remainder up to the first ``-``, and
        looks it up in ``_TICKER_CITY_MAP``.

        Falls back to a substring search across the full ticker for edge
        cases (event tickers without date/strike segments).
        """
        upper = ticker.upper()

        # Try structured parse: strip series prefix, extract city suffix.
        series_match = _RE_SERIES_PREFIX.match(upper)
        if series_match:
            after_prefix = upper[series_match.end():]
            # City suffix is everything up to the first '-' (or end of string).
            city_suffix = after_prefix.split("-", 1)[0]
            if city_suffix and city_suffix in _TICKER_CITY_MAP:
                return _TICKER_CITY_MAP[city_suffix]

        # Fallback: search for known city codes anywhere in the ticker.
        # Iterate longest-first to avoid partial matches.
        for abbr, city in _TICKER_CITY_MAP.items():
            if abbr in upper:
                return city
        return None

    @staticmethod
    def _parse_strike_from_ticker(ticker: str) -> tuple[str, float] | None:
        """Extract the strike type and value from the last segment of a ticker.

        Kalshi weather tickers encode the strike in the final
        hyphen-delimited segment::

            KXHIGHNY-26APR04-T75   -> ("above", 75.0)
            KXHIGHCHI-26APR04-B74.5 -> ("boundary", 74.5)
            KXHIGHMSP-26JAN15-T-5  -> ("above", -5.0)

        Returns:
            ``("above", value)`` for threshold (T) strikes,
            ``("boundary", value)`` for bucket boundary (B) strikes,
            or ``None`` if the ticker has no recognisable strike segment.
        """
        parts = ticker.split("-")
        if len(parts) < 2:
            return None

        # The strike is in the last segment.  For negative temperatures
        # the value follows a second '-', so rejoin the last two parts
        # when the final part is purely numeric.
        last = parts[-1].upper()

        # Check if this is a negative number: last part is digits and
        # second-to-last starts with T or B.
        if last.replace(".", "", 1).isdigit() and len(parts) >= 3:
            candidate = parts[-2].upper()
            if len(candidate) == 1 and candidate in ("T", "B"):
                # Reconstruct: e.g. parts = [..., "T", "5"] -> "T-5"
                last = candidate + "-" + last

        m = _RE_STRIKE.match(last)
        if not m:
            return None

        prefix = m.group(1).upper()
        value = float(m.group(2))

        if prefix == "T":
            return ("above", value)
        return ("boundary", value)

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

    @staticmethod
    def _resolve_bucket(market: Market) -> tuple[float, float, str] | None:
        """Determine bucket boundaries from strike fields, falling back to subtitle.

        Prefers floor_strike/cap_strike (programmatic, reliable) over subtitle
        parsing (regex-based, fragile). Returns (low, high, kind) or None.
        """
        if market.strike_type == "range":
            return (market.floor_strike, market.cap_strike, "range")
        if market.strike_type == "above":
            return (market.floor_strike, math.inf, "above")
        if market.strike_type == "below":
            return (-math.inf, market.cap_strike, "below")

        # Fallback: parse subtitle text
        subtitle = market.subtitle
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

    def _evaluate_market(
        self,
        market: Market,
        forecast: NOAAForecast,
    ) -> EdgeSignal | None:
        """Score a single market against the forecast, return signal or None."""
        bucket = self._resolve_bucket(market)
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
