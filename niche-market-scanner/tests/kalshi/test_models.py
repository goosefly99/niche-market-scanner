"""Tests for Kalshi API Pydantic models."""

from datetime import datetime, timezone, timedelta

from niche_scanner.kalshi.models import Market, OrderBook, Order


class TestMarketFromApiResponse:
    """Parse a raw dict resembling a Kalshi API market response."""

    def test_market_from_api_response(self) -> None:
        close = datetime.now(timezone.utc) + timedelta(hours=24)
        raw = {
            "ticker": "KXWEATHER-24NOV15-B55",
            "event_ticker": "KXWEATHER-24NOV15",
            "subtitle": "Above 55F",
            "yes_bid": 40,
            "yes_ask": 45,
            "no_bid": 56,
            "no_ask": 60,
            "last_price": 42,
            "volume": 1200,
            "open_interest": 350,
            "status": "active",
            "close_time": close.isoformat(),
            "result": "",
        }
        market = Market.model_validate(raw)

        assert market.ticker == "KXWEATHER-24NOV15-B55"
        assert market.implied_prob == 0.42
        assert market.spread == 5  # 45 - 40
        # close is ~24h away; allow 1h tolerance for test execution time
        assert 23.0 <= market.time_to_close_hours <= 25.0


class TestOrderBookParsing:
    """Parse Kalshi [[price, qty], ...] format into OrderBookRow objects."""

    def test_orderbook_parsing(self) -> None:
        raw = {
            "ticker": "KXWEATHER-24NOV15-B55",
            "yes": [[40, 100], [39, 200], [38, 50]],
            "no": [[56, 150], [57, 80]],
        }
        book = OrderBook.model_validate(raw)

        assert len(book.yes) == 3
        assert len(book.no) == 2
        assert book.yes[0].price == 40
        assert book.yes[0].quantity == 100
        assert book.best_yes_bid == 40
        assert book.best_no_bid == 57
        # mid_price = (best_yes_bid + (100 - best_no_bid)) / 2
        # = (40 + (100 - 57)) / 2 = (40 + 43) / 2 = 41.5
        assert book.mid_price == 41.5


class TestMarketEdgeCases:
    """Edge cases for Market model."""

    def test_market_implied_prob_zero_last_price(self) -> None:
        close = datetime.now(timezone.utc) + timedelta(hours=1)
        raw = {
            "ticker": "KXZERO-TEST",
            "event_ticker": "KXZERO",
            "last_price": 0,
            "close_time": close.isoformat(),
        }
        market = Market.model_validate(raw)

        assert market.implied_prob == 0.0
        assert market.last_price == 0


class TestOrderModel:
    """Parse an order from a raw dict."""

    def test_order_model(self) -> None:
        created = datetime.now(timezone.utc)
        raw = {
            "order_id": "ord-abc-123",
            "ticker": "KXWEATHER-24NOV15-B55",
            "side": "yes",
            "action": "buy",
            "type": "limit",
            "yes_price": 42,
            "no_price": 0,
            "count": 10,
            "status": "resting",
            "created_time": created.isoformat(),
        }
        order = Order.model_validate(raw)

        assert order.order_id == "ord-abc-123"
        assert order.side == "yes"
        assert order.action == "buy"
        assert order.type == "limit"
        assert order.yes_price == 42
        assert order.count == 10
        assert order.status == "resting"


class TestMarketStrikeFields:
    """Tests for floor_strike/cap_strike fields on the Market model.

    Kalshi weather/ranged markets return floor_strike and cap_strike to define
    bucket boundaries.  For example:
      - "74F to 76F" -> floor_strike=74.0, cap_strike=76.0
      - "Above 80F" -> floor_strike=80.0, cap_strike=None
      - "Below 50F" -> floor_strike=None, cap_strike=50.0
    These fields are null for non-ranged markets.
    """

    def test_range_bucket_both_strikes(self) -> None:
        """A weather range market should parse floor_strike and cap_strike."""
        close = datetime.now(timezone.utc) + timedelta(hours=12)
        raw = {
            "ticker": "KXHIGHNY-26APR04-B74",
            "event_ticker": "KXHIGHNY-26APR04",
            "subtitle": "Between 74 and 76",
            "floor_strike": 74.0,
            "cap_strike": 76.0,
            "yes_bid": 30,
            "yes_ask": 35,
            "last_price": 32,
            "close_time": close.isoformat(),
        }
        market = Market.model_validate(raw)

        assert market.floor_strike == 74.0
        assert market.cap_strike == 76.0
        assert market.strike_type == "range"
        assert market.strike_low == 74.0
        assert market.strike_high == 76.0

    def test_above_threshold_floor_only(self) -> None:
        """'Above X' markets have floor_strike but no cap_strike."""
        close = datetime.now(timezone.utc) + timedelta(hours=12)
        raw = {
            "ticker": "KXHIGHNY-26APR04-T80",
            "event_ticker": "KXHIGHNY-26APR04",
            "subtitle": "80 or above",
            "floor_strike": 80.0,
            "cap_strike": None,
            "yes_bid": 20,
            "yes_ask": 25,
            "last_price": 22,
            "close_time": close.isoformat(),
        }
        market = Market.model_validate(raw)

        assert market.floor_strike == 80.0
        assert market.cap_strike is None
        assert market.strike_type == "above"
        assert market.strike_low == 80.0
        assert market.strike_high is None

    def test_below_threshold_cap_only(self) -> None:
        """'Below X' markets have cap_strike but no floor_strike."""
        close = datetime.now(timezone.utc) + timedelta(hours=12)
        raw = {
            "ticker": "KXHIGHNY-26APR04-B50",
            "event_ticker": "KXHIGHNY-26APR04",
            "subtitle": "Below 50",
            "floor_strike": None,
            "cap_strike": 50.0,
            "yes_bid": 10,
            "yes_ask": 15,
            "last_price": 12,
            "close_time": close.isoformat(),
        }
        market = Market.model_validate(raw)

        assert market.floor_strike is None
        assert market.cap_strike == 50.0
        assert market.strike_type == "below"
        assert market.strike_low is None
        assert market.strike_high == 50.0

    def test_no_strikes_defaults_to_none(self) -> None:
        """Markets without strike fields (e.g. non-weather) default to None."""
        close = datetime.now(timezone.utc) + timedelta(hours=24)
        raw = {
            "ticker": "CPI-DEC-2026",
            "event_ticker": "CPI-DEC",
            "last_price": 50,
            "close_time": close.isoformat(),
        }
        market = Market.model_validate(raw)

        assert market.floor_strike is None
        assert market.cap_strike is None
        assert market.strike_type is None

    def test_integer_strikes_coerced_to_float(self) -> None:
        """Kalshi may return integer strikes; should coerce to float."""
        close = datetime.now(timezone.utc) + timedelta(hours=12)
        raw = {
            "ticker": "KXHIGHNY-26APR04-B60",
            "event_ticker": "KXHIGHNY-26APR04",
            "floor_strike": 60,
            "cap_strike": 62,
            "last_price": 40,
            "close_time": close.isoformat(),
        }
        market = Market.model_validate(raw)

        assert market.floor_strike == 60.0
        assert market.cap_strike == 62.0
        assert isinstance(market.floor_strike, float)
        assert isinstance(market.cap_strike, float)

    def test_half_degree_strikes(self) -> None:
        """Kalshi uses 2F buckets that can be half-degree values (e.g. 74.5)."""
        close = datetime.now(timezone.utc) + timedelta(hours=12)
        raw = {
            "ticker": "KXHIGHNY-26APR04-B74.5",
            "event_ticker": "KXHIGHNY-26APR04",
            "floor_strike": 74.5,
            "cap_strike": 76.5,
            "last_price": 35,
            "close_time": close.isoformat(),
        }
        market = Market.model_validate(raw)

        assert market.floor_strike == 74.5
        assert market.cap_strike == 76.5
        assert market.strike_type == "range"

    def test_existing_fields_unaffected_by_strikes(self) -> None:
        """Strike fields should not alter existing computed properties."""
        close = datetime.now(timezone.utc) + timedelta(hours=24)
        raw = {
            "ticker": "KXHIGHNY-26APR04-B74",
            "event_ticker": "KXHIGHNY-26APR04",
            "subtitle": "Between 74 and 76",
            "floor_strike": 74.0,
            "cap_strike": 76.0,
            "yes_bid": 40,
            "yes_ask": 45,
            "no_bid": 56,
            "no_ask": 60,
            "last_price": 42,
            "volume": 1200,
            "open_interest": 350,
            "close_time": close.isoformat(),
        }
        market = Market.model_validate(raw)

        # Existing behavior unchanged
        assert market.implied_prob == 0.42
        assert market.spread == 5
        assert 23.0 <= market.time_to_close_hours <= 25.0
