"""
fair_value.py - Diffusion fair value for KXBTC15M binaries.
YES settles if reference price >= strike at close.
"""
import math
import time
import logging

import config
from btc_data import fetch_ohlcv

logger = logging.getLogger(__name__)

_SECS_PER_YEAR = 365.0 * 24 * 3600
_MIN_PER_YEAR = 365.0 * 24 * 60

_vol_cache = {"ts": 0, "sigma": None}


def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def realized_vol_annual(limit: int = 60, ttl: int = 30) -> float:
    """Annualized vol from 1m log returns. Cached ttl seconds."""
    now = time.time()
    if _vol_cache["sigma"] is not None and now - _vol_cache["ts"] < ttl:
        return _vol_cache["sigma"]
    df = fetch_ohlcv(interval="1m", limit=limit)
    closes = df["close"].values
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]
    if len(rets) < 5:
        return _vol_cache["sigma"] or 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    sigma_1m = math.sqrt(var)
    sigma_annual = sigma_1m * math.sqrt(_MIN_PER_YEAR)
    _vol_cache.update(ts=now, sigma=sigma_annual)
    return sigma_annual


def fair_yes_prob(spot: float, strike: float, secs_left: float, sigma_annual: float) -> float:
    """P(spot_T >= strike) under zero-drift GBM."""
    if secs_left <= 0 or sigma_annual <= 0 or spot <= 0 or strike <= 0:
        return 1.0 if spot >= strike else 0.0
    T = secs_left / _SECS_PER_YEAR
    vol_sqrt_T = sigma_annual * math.sqrt(T)
    if vol_sqrt_T <= 1e-9:
        return 1.0 if spot >= strike else 0.0
    d = math.log(spot / strike) / vol_sqrt_T - 0.5 * vol_sqrt_T
    return _phi(d)


def edge(spot: float, strike: float, secs_left: float,
         implied_yes: float, fee_cents_roundtrip: float) -> dict:
    """
    implied_yes: Kalshi YES mid in [0,1].
    Returns dict: fair_yes, signal ('yes'|'no'|None), divergence (prob units).
    """
    sigma = realized_vol_annual()
    fair = fair_yes_prob(spot, strike, secs_left, sigma)
    margin = getattr(config, "EDGE_MARGIN", 0.02)        # extra prob cushion
    thr = fee_cents_roundtrip / 100.0 + margin
    div = fair - implied_yes
    signal = None
    if div > thr:
        signal = "yes"
    elif -div > thr:
        signal = "no"
    return {"fair_yes": fair, "implied_yes": implied_yes, "sigma": sigma,
            "divergence": div, "threshold": thr, "signal": signal}
