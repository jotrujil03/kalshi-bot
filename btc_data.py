import logging
from datetime import datetime, timezone

import pandas as pd
import requests

import config

logger = logging.getLogger(__name__)

_session = requests.Session()


def fetch_ohlcv(interval: str = "1m", limit: int = 60) -> pd.DataFrame:
    """Return a DataFrame with columns [open, high, low, close, volume] for BTCUSDT."""
    url = f"{config.BINANCE_BASE_URL}/klines"
    params = {"symbol": config.BTC_SYMBOL, "interval": interval, "limit": limit}

    try:
        resp = _session.get(url, params=params, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.error("Failed to fetch BTC data from Binance: %s", exc)
        raise

    raw = resp.json()
    df = pd.DataFrame(raw, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore",
    ])
    df = df[["open_time", "open", "high", "low", "close", "volume"]].copy()
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df.set_index("open_time", inplace=True)

    logger.debug("Fetched %d %s candles; latest close=%.2f", len(df), interval, df["close"].iloc[-1])
    return df


def current_price() -> float:
    """Return the latest BTC/USDT spot price."""
    url = f"{config.BINANCE_BASE_URL}/ticker/price"
    try:
        resp = _session.get(url, params={"symbol": config.BTC_SYMBOL}, timeout=5)
        resp.raise_for_status()
        return float(resp.json()["price"])
    except requests.RequestException as exc:
        logger.error("Failed to fetch BTC spot price: %s", exc)
        raise
