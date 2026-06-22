"""
Core bot orchestrator for Kalshi 15-minute BTC trading.

Flow (runs every ~1 minute):
  1. Discover the open BTC 15-min market whose close is 3-13 min away.
  2. Fetch BTC 1-min candles and generate a directional signal.
  3. If signal confidence >= MIN_CONFIDENCE, size and place a limit order.
  4. Log position after fill or timeout.
"""

import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import btc_data
import config
import strategy
from kalshi_client import KalshiClient, KalshiAPIError
from strategy import Direction

logger = logging.getLogger(__name__)


def _parse_close_time(market: dict) -> Optional[datetime]:
    raw = market.get("close_time") or market.get("expiration_time")
    if not raw:
        return None
    try:
        # Kalshi returns ISO-8601 strings like "2024-01-01T15:00:00Z"
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def find_btc_15min_market(client: KalshiClient) -> Optional[dict]:
    """Return the most suitable open BTC 15-min market, or None."""
    now = datetime.now(timezone.utc)
    best: Optional[dict] = None
    best_minutes: float = float("inf")

    for series in config.BTC_SERIES_TICKERS:
        try:
            markets = client.get_markets(series_ticker=series, status="open", limit=50)
        except KalshiAPIError as exc:
            logger.debug("Series %s lookup failed: %s", series, exc)
            continue
        except Exception as exc:
            logger.warning("Unexpected error fetching series %s: %s", series, exc)
            continue

        for m in markets:
            close_dt = _parse_close_time(m)
            if close_dt is None:
                continue
            minutes_left = (close_dt - now).total_seconds() / 60
            if config.MIN_MINUTES_TO_CLOSE <= minutes_left <= config.MAX_MINUTES_TO_CLOSE:
                if minutes_left < best_minutes:
                    best_minutes = minutes_left
                    best = m

    if best:
        logger.info(
            "Found BTC market: %s — closes in %.1f min (title: %s)",
            best.get("ticker"), best_minutes, best.get("title", "")[:80],
        )
    else:
        logger.debug("No suitable BTC 15-min market found right now.")
    return best


def _yes_price_from_orderbook(client: KalshiClient, ticker: str, side: str) -> Optional[int]:
    """Return a competitive limit price in cents for the given side."""
    try:
        ob = client.get_orderbook(ticker, depth=3)
        if side == "yes":
            asks = ob.get("orderbook", {}).get("yes", [])
            if asks:
                return asks[0][0]   # best ask price in cents
        else:
            bids = ob.get("orderbook", {}).get("no", [])
            if bids:
                return bids[0][0]
    except Exception as exc:
        logger.warning("Could not fetch orderbook for %s: %s", ticker, exc)
    return None


def _calc_contracts(yes_price_cents: int, max_usd: float, max_contracts: int) -> int:
    """How many contracts to buy given a price and USD cap."""
    if yes_price_cents <= 0:
        return 0
    cost_per = yes_price_cents / 100.0
    affordable = int(max_usd / cost_per)
    return max(1, min(affordable, max_contracts))


class TradingBot:
    def __init__(self):
        self.client = KalshiClient(
            api_key_id=config.KALSHI_API_KEY_ID,
            private_key_path=config.KALSHI_PRIVATE_KEY_PATH,
            base_url=config.BASE_URL,
        )
        self.active_ticker: Optional[str] = None   # avoid double-entering same market

    def run_cycle(self):
        """Execute one decision cycle. Called by the scheduler every minute."""
        logger.info("=== Cycle start ===")

        market = find_btc_15min_market(self.client)
        if market is None:
            return

        ticker = market.get("ticker")
        if ticker == self.active_ticker:
            logger.info("Already entered %s this window — skipping.", ticker)
            return

        # Fetch BTC candles and generate signal
        try:
            df = btc_data.fetch_ohlcv(interval="1m", limit=60)
        except Exception as exc:
            logger.error("BTC data fetch failed: %s", exc)
            return

        sig = strategy.generate_signal(df)

        if sig.direction == Direction.NEUTRAL or sig.confidence < config.MIN_CONFIDENCE:
            logger.info(
                "Signal too weak (%s, conf=%.2f) — no trade.", sig.direction.value, sig.confidence
            )
            return

        # Determine Kalshi side
        # "yes" = BTC will be above strike; "no" = BTC will be at or below strike
        side = "yes" if sig.direction == Direction.BULLISH else "no"

        yes_price = _yes_price_from_orderbook(self.client, ticker, side)
        if yes_price is None:
            # Fall back to a conservative mid-market guess
            yes_price = 55 if side == "yes" else 45
            logger.warning("Orderbook unavailable — using fallback price %d¢", yes_price)

        no_price = 100 - yes_price
        price_cents = yes_price if side == "yes" else no_price
        contracts = _calc_contracts(price_cents, config.MAX_TRADE_USD, config.MAX_POSITION_SIZE)

        logger.info(
            "Placing %s %s x%d @ %d¢ | BTC=%.2f | conf=%.2f",
            side.upper(), ticker, contracts, price_cents, sig.price, sig.confidence,
        )

        try:
            order = self.client.place_order(
                ticker=ticker,
                side=side,
                action="buy",
                count=contracts,
                order_type="limit",
                yes_price=yes_price if side == "yes" else None,
                no_price=no_price if side == "no" else None,
            )
            logger.info("Order placed: %s", order)
            self.active_ticker = ticker
        except KalshiAPIError as exc:
            logger.error("Order failed: %s", exc)
        except Exception as exc:
            logger.exception("Unexpected error placing order: %s", exc)

    def reset_active_ticker_if_expired(self):
        """Clear the active ticker guard when the market it refers to is no longer open."""
        if self.active_ticker is None:
            return
        try:
            m = self.client.get_market(self.active_ticker)
            if m.get("status") != "open":
                logger.info("Market %s closed — resetting active ticker.", self.active_ticker)
                self.active_ticker = None
        except Exception:
            self.active_ticker = None
