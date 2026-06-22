"""
15-minute BTC directional strategy.

Signal logic (score from -1 to +1):
  RSI(14)        → oversold/overbought component
  EMA(9/21)      → trend + crossover component
  MACD(12,26,9)  → momentum component
  Price momentum → short-term rate-of-change

Positive score → expect BTC to rise → trade YES on "BTC above X"
Negative score → expect BTC to fall → trade NO  on "BTC above X"
"""

import logging
from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class Direction(Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


@dataclass
class Signal:
    direction: Direction
    confidence: float          # 0.0 – 1.0
    price: float
    indicators: dict = field(default_factory=dict)
    score: float = 0.0


# ── Indicator helpers ─────────────────────────────────────────────────────────

def _rsi(prices: pd.Series, period: int = 14) -> pd.Series:
    delta = prices.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _ema(prices: pd.Series, span: int) -> pd.Series:
    return prices.ewm(span=span, adjust=False).mean()


def _macd(prices: pd.Series):
    fast = _ema(prices, 12)
    slow = _ema(prices, 26)
    line = fast - slow
    signal = _ema(line, 9)
    hist = line - signal
    return line, signal, hist


def _roc(prices: pd.Series, period: int = 5) -> float:
    """Rate of change over last `period` bars (%)."""
    if len(prices) < period + 1:
        return 0.0
    return (prices.iloc[-1] / prices.iloc[-period] - 1) * 100


# ── Main signal generator ─────────────────────────────────────────────────────

def generate_signal(df: pd.DataFrame) -> Signal:
    """
    df must have at minimum a 'close' column (and preferably 30+ rows).
    Returns a Signal with direction, confidence, and key indicator values.
    """
    if len(df) < 30:
        logger.warning("Only %d candles available; signal may be unreliable", len(df))

    close = df["close"]
    price = close.iloc[-1]

    rsi = _rsi(close)
    ema9 = _ema(close, 9)
    ema21 = _ema(close, 21)
    _, _, hist = _macd(close)
    roc = _roc(close, 5)

    cur_rsi = rsi.iloc[-1]
    ema_diff = ema9.iloc[-1] - ema21.iloc[-1]
    ema_diff_prev = ema9.iloc[-2] - ema21.iloc[-2]
    hist_cur = hist.iloc[-1]
    hist_prev = hist.iloc[-2]

    score = 0.0

    # RSI component (weight 0.25)
    if cur_rsi < 30:
        score += 0.25
    elif cur_rsi < 40:
        score += 0.12
    elif cur_rsi > 70:
        score -= 0.25
    elif cur_rsi > 60:
        score -= 0.12

    # EMA crossover (weight 0.35)
    if ema_diff_prev <= 0 and ema_diff > 0:      # just crossed up
        score += 0.35
    elif ema_diff_prev >= 0 and ema_diff < 0:    # just crossed down
        score -= 0.35
    elif ema_diff > 0:
        score += 0.15
    else:
        score -= 0.15

    # MACD histogram (weight 0.25)
    if hist_prev <= 0 and hist_cur > 0:
        score += 0.25
    elif hist_prev >= 0 and hist_cur < 0:
        score -= 0.25
    elif hist_cur > 0:
        score += 0.10
    else:
        score -= 0.10

    # Short-term momentum (weight 0.15)
    if roc > 0.3:
        score += 0.15
    elif roc > 0.1:
        score += 0.07
    elif roc < -0.3:
        score -= 0.15
    elif roc < -0.1:
        score -= 0.07

    score = max(-1.0, min(1.0, score))

    indicators = {
        "rsi": round(cur_rsi, 2),
        "ema9": round(ema9.iloc[-1], 2),
        "ema21": round(ema21.iloc[-1], 2),
        "macd_hist": round(hist_cur, 4),
        "roc_5": round(roc, 4),
        "score": round(score, 4),
    }

    THRESHOLD = 0.20
    if score > THRESHOLD:
        direction = Direction.BULLISH
        confidence = min(score / 1.0, 1.0)
    elif score < -THRESHOLD:
        direction = Direction.BEARISH
        confidence = min(abs(score) / 1.0, 1.0)
    else:
        direction = Direction.NEUTRAL
        confidence = 0.0

    sig = Signal(direction=direction, confidence=confidence, price=price,
                 indicators=indicators, score=score)

    logger.info(
        "Signal: %s (confidence=%.2f, score=%.3f) | RSI=%.1f EMA_diff=%.2f MACD_hist=%.4f RoC=%.3f%%",
        direction.value, confidence, score, cur_rsi, ema_diff, hist_cur, roc,
    )
    return sig
