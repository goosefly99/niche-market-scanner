"""Economics edge engine for macro-indicator markets (CPI, Fed rate, jobs, GDP).

Integrates FRED API for CPI index data. The Cleveland Fed Inflation Nowcast
is not available via public API (site blocks automated access); FRED provides
CPI index series that can be used to calculate YoY inflation rates as a proxy.

FRED API: https://api.stlouisfed.org/fred/series/observations
Requires FRED_API_KEY environment variable.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from scipy.stats import norm

from niche_scanner.engines.base import EdgeEngine, EdgeSignal
from niche_scanner.kalshi.models import Market, OrderBook
from niche_scanner.sizing.fees import round_trip_fee_pp

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Historical volatility constants for probability estimation
# ---------------------------------------------------------------------------
# These represent the standard deviation of "surprise" (actual vs. last-known
# value) for each indicator type in its native unit.  They are used to build
# a Gaussian model: P(indicator > threshold) = 1 - Phi((threshold - value) / sigma).
#
# Sources (approximate, from published research & historical FRED data):
#   - CPI YoY monthly surprise:  ~0.10-0.20 percentage-points (pp)
#   - Core CPI YoY surprise:     ~0.05-0.15 pp (less volatile than headline)
#   - Fed Funds effective rate:   moves in 25bp increments; between meetings
#     the effective rate fluctuates ~0.05pp around the midpoint of the range
#   - Nonfarm payrolls:           ~75-100K surprise (std dev of miss)
#   - GDP quarterly annualized:   ~0.5-1.0 pp surprise
#   - Unemployment rate:          ~0.1-0.2 pp surprise
#   - Gas prices (weekly avg):    ~$0.05-0.10 per gallon weekly change
#
# The dict maps release_type -> default sigma in the indicator's native unit.
# Per-indicator overrides can be specified via INDICATOR_SIGMA below.

RELEASE_TYPE_SIGMA: dict[str, float] = {
    "cpi": 0.15,           # CPI YoY surprise std dev in percentage points
    "fed_rate": 0.05,      # Fed Funds rate fluctuation std dev in pp
    "jobs": 80.0,          # Nonfarm payrolls surprise std dev in thousands
    "gdp": 0.8,            # GDP growth surprise std dev in pp
    "unemployment": 0.15,  # Unemployment rate surprise std dev in pp
    "gas": 0.08,           # Gas price weekly change std dev in dollars
    "recession": 0.20,     # Generic; binary markets rarely have threshold
    "credit": 0.20,        # Generic; binary markets rarely have threshold
}

# Per-indicator sigma overrides (indicator_name -> sigma).
# When an IndicatorReading's name matches a key here, this sigma is used
# instead of the release_type default.
INDICATOR_SIGMA: dict[str, float] = {
    "FRED Headline CPI YoY": 0.18,   # Headline CPI is more volatile
    "FRED Core CPI YoY": 0.10,       # Core CPI is less volatile
    "FRED Effective Fed Funds Rate": 0.05,
    "10Y Breakeven Inflation Rate": 0.12,
    "Fed Funds Target Upper": 0.0,    # Exact; no uncertainty in published target
}

# FRED series IDs for CPI-related indicators
FRED_CPI_SERIES: dict[str, str] = {
    "CPIAUCSL": "CPI All Urban Consumers (headline CPI index, seasonally adjusted)",
    "CPILFESL": "CPI Less Food and Energy (core CPI index, seasonally adjusted)",
    "PCEPI": "PCE Price Index (headline PCE, seasonally adjusted)",
    "PCEPILFE": "PCE Less Food and Energy (core PCE, seasonally adjusted)",
    "CORESTICKM159SFRBATL": "Sticky Price CPI (Atlanta Fed, inflation expectations proxy)",
}

# FRED series IDs for Fed Funds rate indicators
FRED_FED_SERIES: dict[str, str] = {
    "DFF": "Effective Federal Funds Rate (daily)",
    "DFEDTARU": "Federal Funds Target Rate Upper (current target ceiling)",
    "DFEDTARL": "Federal Funds Target Rate Lower (current target floor)",
    "T10YIE": "10-Year Breakeven Inflation Rate (market inflation expectations)",
}

# FRED series for historical backtesting and calibration
FRED_HISTORICAL_SERIES: dict[str, str] = {
    "UNRATE": "Civilian Unemployment Rate (monthly, seasonally adjusted)",
    "PAYEMS": "Total Nonfarm Payrolls (monthly, thousands, seasonally adjusted)",
    "GDP": "Gross Domestic Product (quarterly, billions, seasonally adjusted annual rate)",
    "CPIAUCSL": "CPI All Urban Consumers (monthly index, seasonally adjusted)",
    "CPILFESL": "CPI Less Food and Energy (monthly index, seasonally adjusted)",
    "PCEPI": "PCE Price Index (monthly index, seasonally adjusted)",
    "DFF": "Effective Federal Funds Rate (daily)",
    "T10YIE": "10-Year Breakeven Inflation Rate (daily)",
    "ICSA": "Initial Claims (weekly, seasonally adjusted)",
}

FRED_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"


class FREDClient:
    """Client for fetching economic data from the FRED API.

    Provides caching, historical series retrieval, and YoY calculations.
    Used by the economics engine for both live indicator fetching and
    backtesting/calibration against historical releases.

    Requires a FRED API key (free, register at https://fred.stlouisfed.org/docs/api/api_key.html).
    """

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self._cache: dict[str, list[tuple[str, float]]] = {}

    def fetch_history(
        self,
        series_id: str,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 1000,
    ) -> list[tuple[str, float]]:
        """Fetch observation history for a FRED series.

        Args:
            series_id: FRED series identifier (e.g. "CPIAUCSL").
            start_date: Start date in YYYY-MM-DD format (optional).
            end_date: End date in YYYY-MM-DD format (optional).
            limit: Maximum observations to return (default 1000).

        Returns:
            List of (date_str, value) pairs sorted chronologically.
            Cached after first fetch for the same series_id.
        """
        cache_key = f"{series_id}:{start_date}:{end_date}:{limit}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        params: dict[str, str | int] = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
            "sort_order": "asc",
            "limit": limit,
        }
        if start_date:
            params["observation_start"] = start_date
        if end_date:
            params["observation_end"] = end_date

        try:
            resp = httpx.get(FRED_BASE_URL, params=params, timeout=15.0)
            resp.raise_for_status()
            history = self._parse_history(resp.json())
            self._cache[cache_key] = history
            return history
        except httpx.HTTPError:
            logger.exception("FRED history fetch failed for %s", series_id)
            return []

    def fetch_latest(self, series_id: str) -> float | None:
        """Fetch the most recent observation value."""
        params: dict[str, str | int] = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 1,
        }
        try:
            resp = httpx.get(FRED_BASE_URL, params=params, timeout=10.0)
            resp.raise_for_status()
            values = self._parse_history(resp.json())
            return values[0][1] if values else None
        except (httpx.HTTPError, IndexError):
            return None

    @staticmethod
    def _parse_history(raw: dict) -> list[tuple[str, float]]:
        """Parse FRED JSON observations into (date, value) pairs.

        Skips entries with '.' (FRED's missing data marker).
        """
        return parse_fred_observations(raw)

    @staticmethod
    def _yoy_from_history(
        history: list[tuple[str, float]],
    ) -> float | None:
        """Calculate YoY rate from the last 12+ months of history.

        Returns None if fewer than 12 observations are available.
        """
        if len(history) < 12:
            return None
        current = history[-1][1]
        year_ago = history[-13][1] if len(history) >= 13 else history[0][1]
        return calculate_yoy_rate(current, year_ago)


def parse_fed_target_range(effective_rate: float) -> tuple[float, float]:
    """Determine the Fed Funds target range from the effective rate.

    The Fed sets rates in 25bp increments. The effective rate falls
    within the target range. Returns (lower_bound, upper_bound) as percentages.

    Examples:
        4.33 -> (4.25, 4.50) — rate is within the 4.25-4.50 range
        4.50 -> (4.50, 4.75) — at boundary, rounds to next range
    """
    # Round down to nearest 25bp for the lower bound
    lower = (effective_rate // 0.25) * 0.25
    upper = lower + 0.25
    return (lower, upper)


def parse_fred_observations(raw: dict) -> list[tuple[str, float]]:
    """Parse FRED JSON response into (date, value) pairs.

    FRED returns ``"."`` for missing observations — these are skipped.
    """
    result: list[tuple[str, float]] = []
    for obs in raw.get("observations", []):
        val_str = obs.get("value", ".")
        if val_str == "." or val_str is None:
            continue
        try:
            result.append((obs["date"], float(val_str)))
        except (ValueError, KeyError):
            continue
    return result


def calculate_yoy_rate(current: float, year_ago: float) -> float:
    """Calculate year-over-year percentage change.

    Returns the annualized rate as a percentage (e.g., 3.0 for 3%).
    """
    if year_ago == 0:
        return 0.0
    return ((current / year_ago) - 1.0) * 100.0


def estimate_threshold_probability(
    indicator_value: float,
    threshold: float,
    direction: str,
    sigma: float,
) -> float:
    """Estimate P(indicator crosses threshold) using a normal distribution.

    Models the *next* indicator reading as N(indicator_value, sigma^2),
    where sigma represents historical surprise volatility.

    Args:
        indicator_value: Current or latest known indicator value.
        threshold: Market threshold (e.g. 3.5 for "3.5% or above").
        direction: ``"above"`` for P(X >= threshold),
                   ``"below"`` for P(X < threshold).
        sigma: Standard deviation of indicator surprise/volatility.

    Returns:
        Probability in [0, 1]. Returns 0.5 when sigma is zero and
        indicator_value equals threshold (no information).
    """
    if sigma <= 0:
        # No uncertainty — deterministic comparison
        if direction == "above":
            return 1.0 if indicator_value >= threshold else 0.0
        return 1.0 if indicator_value < threshold else 0.0

    z = (threshold - indicator_value) / sigma
    # P(X >= threshold) = 1 - Phi(z)
    prob_above: float = float(1.0 - norm.cdf(z))

    if direction == "above":
        return prob_above
    return 1.0 - prob_above


@dataclass
class IndicatorReading:
    """A single macro-indicator observation used to estimate probability."""

    name: str
    value: float
    probability_above: float
    weight: float


@dataclass
class ReleaseCalendarEntry:
    """An upcoming economic data release."""

    release_type: str
    release_date: str  # YYYY-MM-DD
    description: str = ""

    @property
    def date(self) -> datetime:
        """Parse release_date string to datetime (UTC midnight)."""
        return datetime.strptime(self.release_date, "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        )


class ReleaseCalendar:
    """Tracks upcoming BLS/Fed/BEA release dates for scan intensification.

    The scanner checks ``is_within_window()`` each cycle to decide whether
    to use the normal interval (30min) or the urgent interval (5min).
    """

    def __init__(self, entries: list[ReleaseCalendarEntry] | None = None) -> None:
        self.entries = sorted(entries or [], key=lambda e: e.release_date)

    @classmethod
    def from_yaml(cls, path: Path) -> ReleaseCalendar:
        """Load release calendar from a YAML file."""
        import yaml
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        entries = []
        for raw in data.get("releases", []):
            entries.append(ReleaseCalendarEntry(
                release_type=raw["type"],
                release_date=str(raw["date"]),
                description=raw.get("description", ""),
            ))
        return cls(entries)

    @classmethod
    def default(cls) -> ReleaseCalendar:
        """Create a calendar with known 2026 economic release dates.

        Sources: BLS release schedule, FOMC meeting schedule.
        Dates are approximate — verify against official schedules.
        """
        entries = [
            # CPI releases (BLS, ~10th-15th of each month for prior month)
            ReleaseCalendarEntry("cpi", "2026-04-10", "March 2026 CPI"),
            ReleaseCalendarEntry("cpi", "2026-05-13", "April 2026 CPI"),
            ReleaseCalendarEntry("cpi", "2026-06-11", "May 2026 CPI"),
            ReleaseCalendarEntry("cpi", "2026-07-14", "June 2026 CPI"),
            ReleaseCalendarEntry("cpi", "2026-08-12", "July 2026 CPI"),
            ReleaseCalendarEntry("cpi", "2026-09-10", "August 2026 CPI"),
            ReleaseCalendarEntry("cpi", "2026-10-13", "September 2026 CPI"),
            ReleaseCalendarEntry("cpi", "2026-11-12", "October 2026 CPI"),
            ReleaseCalendarEntry("cpi", "2026-12-10", "November 2026 CPI"),
            # Jobs reports (BLS, first Friday of each month)
            ReleaseCalendarEntry("jobs", "2026-04-03", "March 2026 jobs"),
            ReleaseCalendarEntry("jobs", "2026-05-01", "April 2026 jobs"),
            ReleaseCalendarEntry("jobs", "2026-06-05", "May 2026 jobs"),
            ReleaseCalendarEntry("jobs", "2026-07-02", "June 2026 jobs"),
            ReleaseCalendarEntry("jobs", "2026-08-07", "July 2026 jobs"),
            ReleaseCalendarEntry("jobs", "2026-09-04", "August 2026 jobs"),
            ReleaseCalendarEntry("jobs", "2026-10-02", "September 2026 jobs"),
            ReleaseCalendarEntry("jobs", "2026-11-06", "October 2026 jobs"),
            ReleaseCalendarEntry("jobs", "2026-12-04", "November 2026 jobs"),
            # FOMC decisions (2-day meetings, decision on day 2)
            ReleaseCalendarEntry("fed_rate", "2026-05-06", "FOMC May decision"),
            ReleaseCalendarEntry("fed_rate", "2026-06-17", "FOMC June decision"),
            ReleaseCalendarEntry("fed_rate", "2026-07-29", "FOMC July decision"),
            ReleaseCalendarEntry("fed_rate", "2026-09-16", "FOMC September decision"),
            ReleaseCalendarEntry("fed_rate", "2026-11-04", "FOMC November decision"),
            ReleaseCalendarEntry("fed_rate", "2026-12-16", "FOMC December decision"),
            # GDP (BEA, advance estimate ~4 weeks after quarter end)
            ReleaseCalendarEntry("gdp", "2026-04-29", "Q1 2026 GDP advance"),
            ReleaseCalendarEntry("gdp", "2026-07-29", "Q2 2026 GDP advance"),
            ReleaseCalendarEntry("gdp", "2026-10-28", "Q3 2026 GDP advance"),
        ]
        return cls(entries)

    def next_release(
        self, release_type: str, now: datetime | None = None,
    ) -> ReleaseCalendarEntry | None:
        """Return the soonest upcoming release of the given type.

        Returns None if no future releases of that type exist.
        """
        if now is None:
            now = datetime.now(timezone.utc)
        for entry in self.entries:
            if entry.release_type == release_type and entry.date > now:
                return entry
        return None

    def is_within_window(
        self, release_type: str, hours: float = 48, now: datetime | None = None,
    ) -> bool:
        """True if the next release of this type is within ``hours`` hours."""
        nxt = self.next_release(release_type, now=now)
        if nxt is None:
            return False
        if now is None:
            now = datetime.now(timezone.utc)
        delta = nxt.date - now
        return 0 <= delta.total_seconds() <= hours * 3600

    def upcoming(self, days: int = 30, now: datetime | None = None) -> list[ReleaseCalendarEntry]:
        """Return all releases within the next ``days`` days."""
        if now is None:
            now = datetime.now(timezone.utc)
        cutoff = now + timedelta(days=days)
        return [e for e in self.entries if now < e.date <= cutoff]


class EconomicsEdgeEngine(EdgeEngine):
    """Detect edges on economic-indicator markets using macro data.

    Phase 1 stubs indicator fetching; the engine structure, market
    classification, and probability math are fully functional.
    """

    # Regex fallback for markets not matched by SERIES_TICKER_MAP
    SERIES_PATTERNS: dict[str, re.Pattern[str]] = {
        "CPI": re.compile(r"(?i)\bCPI\b"),
        "fed_rate": re.compile(r"(?i)\b(FED|FOMC|RATE)\b"),
        "jobs": re.compile(r"(?i)\b(JOBS|NFP|NONFARM|EMPLOY)\b"),
        "gdp": re.compile(r"(?i)\bGDP\b"),
    }

    # Verified Kalshi economics series tickers mapped to release types.
    # Surveyed 2026-04-04 from Kalshi /series endpoint (493 Economics series).
    # Only includes series with demonstrated liquidity or structural importance.
    SERIES_TICKER_MAP: dict[str, list[str]] = {
        "cpi": [
            "CPIYOY",           # Monthly CPI YoY (headline inflation)
            "CPICOREYOY",       # Core CPI YoY (ex food & energy)
            "KXACPICORE-",      # Annual core inflation
            "KXCPISHELTER",     # CPI shelter component (424.x index)
            "KXAIRFARECPI",     # CPI airfare component (vol=1700+)
            "KXSHELTERCPI",     # CPI shelter (alternate series)
            "PCECORE",          # Core PCE inflation
            "KXPCECORE",        # Core PCE (alternate ticker)
            "KXPPIVSCPI",       # PPI vs CPI comparison (vol=40K+)
        ],
        "fed_rate": [
            "KXFEDDECISION",    # Fed meeting rate decision
            "KXRATECUT",        # Next Fed rate cut
            "KXFEDHIKE",        # Next Fed rate hike
            "KXTERMINALRATE",   # Fed funds terminal rate
            "LOWESTRATE",       # Fed funds lowest rate
            "KXDOTPLOT",        # Fed dot plot projections
        ],
        "jobs": [
            "KXPROLLS",         # Monthly nonfarm payrolls
            "PAYROLLS",         # Jobs numbers (alt ticker)
            "PROLLS",           # Jobs numbers (alt ticker)
            "KXPAYROLLS",       # Jobs numbers (alt ticker)
        ],
        "gdp": [
            "GDP",              # US GDP growth quarterly
            "KXGDP",            # US GDP growth (alt ticker)
            "KXGDPYEAR",        # Annual GDP
            "KXNGDP",           # Nominal GDP growth
        ],
        "unemployment": [
            "KXUE",             # Monthly unemployment rate
            "KXU3",             # U-3 unemployment rate
            "U3",               # U-3 (alt ticker)
            "KXU3MIN",          # Unemployment floor
            "U3MAX",            # Unemployment ceiling
        ],
        "gas": [
            "KXAAAGASW",        # Weekly gas price (vol=28K+, tight spreads)
            "KXAAAGASD",        # Daily gas price
            "KXAAAGASMAX",      # Yearly gas price max (vol=3.6K+)
            "GASD",             # Daily gas (alt ticker)
        ],
        "recession": [
            "KXRECSSNBER",      # NBER recession call (vol=1.25M, OI=493K)
        ],
        "credit": [
            "KXCREDITRATING",   # US credit downgrade/default (vol=38K+)
        ],
    }

    def __init__(self, min_edge_pp: float = 12.0) -> None:
        self.min_edge_pp = min_edge_pp
        # Build reverse lookup: series_ticker -> release_type
        self._ticker_to_type: dict[str, str] = {}
        for release_type, tickers in self.SERIES_TICKER_MAP.items():
            for ticker in tickers:
                self._ticker_to_type[ticker.upper()] = release_type

    @classmethod
    def all_series_tickers(cls) -> list[str]:
        """Return flat list of all economics series tickers for scanning."""
        tickers: list[str] = []
        for series_list in cls.SERIES_TICKER_MAP.values():
            tickers.extend(series_list)
        return tickers

    # ------------------------------------------------------------------
    # Market classification
    # ------------------------------------------------------------------

    def _classify_market(self, market: Market) -> str | None:
        """Return the release type if *market* matches a known series or pattern.

        First checks the structured SERIES_TICKER_MAP (reliable), then
        falls back to regex SERIES_PATTERNS (broad catch-all).
        """
        # Check series ticker from the market's event or series prefix
        # Kalshi tickers are like CPIYOY-26APR10-T3.5, so the series
        # is everything before the first date segment
        ticker_upper = market.ticker.upper()
        event_upper = (market.event_ticker or "").upper()

        # Direct lookup in the reverse map
        for known_ticker in self._ticker_to_type:
            if ticker_upper.startswith(known_ticker) or event_upper.startswith(known_ticker):
                return self._ticker_to_type[known_ticker]

        # Regex fallback
        for release_type, pattern in self.SERIES_PATTERNS.items():
            if pattern.search(market.ticker) or pattern.search(event_upper):
                return release_type
        return None

    # ------------------------------------------------------------------
    # Threshold parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_threshold(subtitle: str) -> tuple[float, str] | None:
        """Extract a numeric threshold and direction from a market subtitle.

        Recognises forms like ``"3.5% or above"``, ``"Below 200K"``,
        ``"above 150K"``, ``"under 4.0%"``.

        Returns ``(threshold_value, direction)`` where direction is
        ``"above"`` or ``"below"``, or *None* if unparseable.
        """
        above_match = re.search(
            r"([\d.]+)\s*[%K]?\s+or\s+above|above\s+([\d.]+)\s*[%K]?",
            subtitle,
            re.IGNORECASE,
        )
        if above_match:
            value_str = above_match.group(1) or above_match.group(2)
            return float(value_str), "above"

        below_match = re.search(
            r"[Bb]elow\s+([\d.]+)\s*[%K]?|[Uu]nder\s+([\d.]+)\s*[%K]?",
            subtitle,
        )
        if below_match:
            value_str = below_match.group(1) or below_match.group(2)
            return float(value_str), "below"

        return None

    # ------------------------------------------------------------------
    # Indicator fetching (Phase 2 stubs)
    # ------------------------------------------------------------------

    def fetch_indicators(self, release_type: str) -> list[IndicatorReading]:
        """Fetch indicator readings for a release type.

        Currently implemented: CPI (via FRED API).
        Stubs: fed_rate (CME FedWatch), jobs (ADP).
        """
        if release_type == "cpi":
            return self._fetch_cpi_indicators()
        if release_type == "fed_rate":
            return self._fetch_fed_rate_indicators()
        if release_type == "jobs":
            return self._fetch_jobs_indicators()
        return []

    def _fetch_cpi_indicators(self) -> list[IndicatorReading]:
        """Fetch CPI indicator readings from FRED API.

        Uses CPIAUCSL (headline CPI) and CPILFESL (core CPI) to calculate
        YoY inflation rates. These serve as the model's CPI estimate
        until the Cleveland Fed Nowcast becomes programmatically accessible.

        Requires FRED_API_KEY environment variable.
        """
        api_key = os.environ.get("FRED_API_KEY", "")
        if not api_key:
            logger.debug("FRED_API_KEY not set; skipping CPI indicators")
            return []

        readings: list[IndicatorReading] = []

        for series_id, weight, name in [
            ("CPIAUCSL", 0.5, "FRED Headline CPI YoY"),
            ("CPILFESL", 0.5, "FRED Core CPI YoY"),
        ]:
            try:
                yoy = self._fetch_fred_yoy(api_key, series_id)
                if yoy is not None:
                    # For a CPI threshold like "3.5% or above":
                    # probability_above is estimated from how close
                    # current YoY is to the threshold. This is a rough
                    # model — refinement comes with Cleveland Fed nowcast.
                    readings.append(IndicatorReading(
                        name=name,
                        value=yoy,
                        probability_above=0.5,  # Neutral default; refined per-market
                        weight=weight,
                    ))
            except Exception:
                logger.exception("Failed to fetch FRED series %s", series_id)

        return readings

    @staticmethod
    def _fetch_fred_yoy(api_key: str, series_id: str) -> float | None:
        """Fetch latest YoY rate for a FRED series.

        Retrieves the last 13 months of data, calculates YoY from the
        most recent vs 12-months-ago observations.
        """
        try:
            resp = httpx.get(
                FRED_BASE_URL,
                params={
                    "series_id": series_id,
                    "api_key": api_key,
                    "file_type": "json",
                    "sort_order": "desc",
                    "limit": 13,
                },
                timeout=10.0,
            )
            resp.raise_for_status()
            values = parse_fred_observations(resp.json())
            if len(values) < 2:
                return None
            # Most recent and ~12 months ago
            current = values[0][1]
            year_ago = values[-1][1] if len(values) >= 12 else values[-1][1]
            return calculate_yoy_rate(current, year_ago)
        except (httpx.HTTPError, KeyError, IndexError):
            logger.exception("FRED fetch failed for %s", series_id)
            return None

    def _fetch_fed_rate_indicators(self) -> list[IndicatorReading]:
        """Fetch Fed rate indicators from FRED API.

        Uses the Effective Federal Funds Rate (DFF) and target range
        (DFEDTARU/DFEDTARL) to determine current rate positioning.
        Also fetches 10-Year Breakeven Inflation Rate as a market
        expectations signal.

        CME FedWatch probability API requires a paid subscription ($25/mo).
        FRED provides the underlying rate data for free, which we use to
        derive positioning signals. When CME FedWatch API is available,
        it can be added as an additional high-weight indicator.

        Requires FRED_API_KEY environment variable.
        """
        api_key = os.environ.get("FRED_API_KEY", "")
        if not api_key:
            logger.debug("FRED_API_KEY not set; skipping Fed rate indicators")
            return []

        readings: list[IndicatorReading] = []

        # Fetch effective Fed Funds rate
        try:
            eff_rate = self._fetch_fred_latest(api_key, "DFF")
            if eff_rate is not None:
                lower, upper = parse_fed_target_range(eff_rate)
                readings.append(IndicatorReading(
                    name="FRED Effective Fed Funds Rate",
                    value=eff_rate,
                    probability_above=0.5,  # Neutral; refined per-market
                    weight=0.4,
                ))
                logger.info(
                    "Fed rate: %.2f%% (target range %.2f-%.2f%%)",
                    eff_rate, lower, upper,
                )
        except Exception:
            logger.exception("Failed to fetch DFF")

        # Fetch breakeven inflation rate (market expectations)
        try:
            breakeven = self._fetch_fred_latest(api_key, "T10YIE")
            if breakeven is not None:
                readings.append(IndicatorReading(
                    name="10Y Breakeven Inflation Rate",
                    value=breakeven,
                    probability_above=0.5,
                    weight=0.3,
                ))
        except Exception:
            logger.exception("Failed to fetch T10YIE")

        # Fetch target range bounds
        try:
            target_upper = self._fetch_fred_latest(api_key, "DFEDTARU")
            if target_upper is not None:
                readings.append(IndicatorReading(
                    name="Fed Funds Target Upper",
                    value=target_upper,
                    probability_above=0.5,
                    weight=0.3,
                ))
        except Exception:
            logger.exception("Failed to fetch DFEDTARU")

        return readings

    @staticmethod
    def _fetch_fred_latest(api_key: str, series_id: str) -> float | None:
        """Fetch the most recent observation value for a FRED series."""
        try:
            resp = httpx.get(
                FRED_BASE_URL,
                params={
                    "series_id": series_id,
                    "api_key": api_key,
                    "file_type": "json",
                    "sort_order": "desc",
                    "limit": 1,
                },
                timeout=10.0,
            )
            resp.raise_for_status()
            values = parse_fred_observations(resp.json())
            return values[0][1] if values else None
        except (httpx.HTTPError, IndexError):
            return None

    @staticmethod
    def _fetch_jobs_indicators() -> list[IndicatorReading]:
        """Stub: ADP, jobless claims, ISM employment (future)."""
        return []

    # ------------------------------------------------------------------
    # Probability model
    # ------------------------------------------------------------------

    @staticmethod
    def _calculate_model_probability(
        readings: list[IndicatorReading],
    ) -> float | None:
        """Weighted average of indicator probability-above values.

        Returns *None* when there are no readings or total weight is
        zero, signalling that no model estimate is available.
        """
        if not readings:
            return None
        total_weight = sum(r.weight for r in readings)
        if total_weight == 0:
            return None
        return sum(r.probability_above * r.weight for r in readings) / total_weight

    @staticmethod
    def _refine_readings_for_market(
        readings: list[IndicatorReading],
        threshold: float,
        direction: str,
        release_type: str,
    ) -> list[IndicatorReading]:
        """Refine indicator readings with per-market threshold probabilities.

        For each reading, computes the probability that the underlying
        indicator will cross the market's threshold using a normal
        distribution model calibrated with historical volatility.

        Args:
            readings: Raw indicator readings (probability_above may be 0.5).
            threshold: Numeric threshold from the market subtitle.
            direction: ``"above"`` or ``"below"`` from ``_parse_threshold()``.
            release_type: Release category for default sigma lookup.

        Returns:
            New list of ``IndicatorReading`` with refined ``probability_above``.
        """
        default_sigma = RELEASE_TYPE_SIGMA.get(release_type, 0.15)
        refined: list[IndicatorReading] = []

        for reading in readings:
            sigma = INDICATOR_SIGMA.get(reading.name, default_sigma)
            prob = estimate_threshold_probability(
                indicator_value=reading.value,
                threshold=threshold,
                direction=direction,
                sigma=sigma,
            )
            refined.append(IndicatorReading(
                name=reading.name,
                value=reading.value,
                probability_above=prob,
                weight=reading.weight,
            ))

        return refined

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    def _evaluate_market(
        self,
        market: Market,
        orderbook: OrderBook | None,
        readings: list[IndicatorReading],
        release_type: str,
    ) -> EdgeSignal | None:
        """Refine probability per-market threshold, then check for edge.

        If the market subtitle contains a parseable threshold (e.g.
        "3.5% or above"), indicator readings are refined against that
        threshold using a Gaussian volatility model. Otherwise, the raw
        (unrefined) weighted probability is used as a fallback.
        """
        threshold_info = self._parse_threshold(market.subtitle)
        threshold_str = (
            f"{threshold_info[0]} ({threshold_info[1]})"
            if threshold_info
            else "unknown threshold"
        )

        # Refine readings against this market's specific threshold
        if threshold_info is not None:
            threshold_val, direction = threshold_info
            refined = self._refine_readings_for_market(
                readings, threshold_val, direction, release_type,
            )
            model_prob = self._calculate_model_probability(refined)
        else:
            # No threshold parseable — use raw readings as fallback
            model_prob = self._calculate_model_probability(readings)

        if model_prob is None:
            return None

        # Market-implied probability from last price
        market_prob_yes = market.last_price / 100.0 if market.last_price > 0 else 0.5

        candidates: list[EdgeSignal] = []

        # --- YES side ---
        yes_edge_pp = (model_prob - market_prob_yes) * 100
        price_cents_yes = max(1, min(99, round(market_prob_yes * 100)))
        fee_pp_yes = round_trip_fee_pp(price_cents_yes)
        fee_adj_yes = yes_edge_pp - fee_pp_yes

        if fee_adj_yes >= self.min_edge_pp:
            candidates.append(
                EdgeSignal(
                    engine="economics",
                    ticker=market.ticker,
                    side="yes",
                    model_prob=model_prob,
                    market_prob=market_prob_yes,
                    edge_pp=yes_edge_pp,
                    fee_adjusted_edge=fee_adj_yes,
                    confidence=0.6,
                    thesis=(
                        f"{release_type} indicators suggest YES "
                        f"({threshold_str}); "
                        f"model {model_prob:.0%} vs market {market_prob_yes:.0%}"
                    ),
                    metadata={"release_type": release_type},
                ),
            )

        # --- NO side ---
        model_prob_no = 1.0 - model_prob
        market_prob_no = 1.0 - market_prob_yes
        no_edge_pp = (model_prob_no - market_prob_no) * 100
        price_cents_no = max(1, min(99, round(market_prob_no * 100)))
        fee_pp_no = round_trip_fee_pp(price_cents_no)
        fee_adj_no = no_edge_pp - fee_pp_no

        if fee_adj_no >= self.min_edge_pp:
            candidates.append(
                EdgeSignal(
                    engine="economics",
                    ticker=market.ticker,
                    side="no",
                    model_prob=model_prob,
                    market_prob=market_prob_yes,
                    edge_pp=no_edge_pp,
                    fee_adjusted_edge=fee_adj_no,
                    confidence=0.6,
                    thesis=(
                        f"{release_type} indicators suggest NO "
                        f"({threshold_str}); "
                        f"model {model_prob:.0%} vs market {market_prob_yes:.0%}"
                    ),
                    metadata={"release_type": release_type},
                ),
            )

        if not candidates:
            return None
        return max(candidates, key=lambda s: s.fee_adjusted_edge)

    # ------------------------------------------------------------------
    # Public scan interface
    # ------------------------------------------------------------------

    async def scan(
        self,
        markets: list[Market],
        orderbooks: dict[str, OrderBook],
    ) -> list[EdgeSignal]:
        """Group markets by release type, evaluate each for edge."""
        # 1. Classify
        grouped: dict[str, list[Market]] = {}
        for market in markets:
            rt = self._classify_market(market)
            if rt is not None:
                grouped.setdefault(rt, []).append(market)

        signals: list[EdgeSignal] = []

        # 2. For each release type, fetch indicators and evaluate per-market
        for release_type, group in grouped.items():
            readings = self.fetch_indicators(release_type)
            if not readings:
                logger.debug(
                    "No indicator data for %s; skipping %d markets",
                    release_type,
                    len(group),
                )
                continue

            for market in group:
                ob = orderbooks.get(market.ticker)
                signal = self._evaluate_market(
                    market, ob, readings, release_type,
                )
                if signal is not None:
                    signals.append(signal)

        return signals
