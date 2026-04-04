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
