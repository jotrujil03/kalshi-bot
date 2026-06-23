"""
main.py - Threaded runner. 4 threads, 1 market.
  discovery : find active KXBTC15M ticker + close_time
  book      : poll orderbook -> snapshot
  spot      : poll BTC spot -> spot_vel
  manager   : decision loop (only mutator of bot trading state)
All KalshiClient calls serialized via api_lock.
"""
import sys
import time
import logging
import threading
import requests
from datetime import datetime, timezone

import config
from bot import MarketMakerBot
from kalshi_client import KalshiClient
from btc_data import current_price

logging.basicConfig(
    level=getattr(config, "LOG_LEVEL", "INFO"),
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("main")


class Shared:
    def __init__(self):
        self.lock = threading.Lock()
        self.ticker = None
        self.close_time = None
        self.orderbook = None
        self.ob_time = 0
        self.stop = threading.Event()

    def set_market(self, ticker, close_time):
        with self.lock:
            if ticker != self.ticker:
                self.orderbook = None      # invalidate stale book on switch
                self.ob_time = 0
            self.ticker = ticker
            self.close_time = close_time

    def get_market(self):
        with self.lock:
            return self.ticker, self.close_time

    def set_book(self, ob):
        with self.lock:
            self.orderbook = ob
            self.ob_time = time.time()

    def get_book(self):
        with self.lock:
            return self.orderbook, self.ob_time



def _extract_strike(m: dict) -> float:
    """Kalshi field names vary; try known, fallback to subtitle parse. Verify with get_market dump."""
    for k in ("floor_strike", "cap_strike", "strike", "settlement_value"):
        v = m.get(k)
        if v not in (None, 0, ""):
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    import re as _re
    text = (m.get("subtitle") or m.get("title") or m.get("yes_sub_title") or "")
    mt = _re.search(r"\$?([0-9][0-9,]*\.?[0-9]*)", text)
    if mt:
        return float(mt.group(1).replace(",", ""))
    return 0.0

def discovery_loop(shared: Shared, bot: MarketMakerBot):
    url = f"{config.BASE_URL}/markets"
    params = {"series_ticker": "KXBTC15M", "status": "open", "limit": 5}
    while not shared.stop.is_set():
        try:
            r = requests.get(url, params=params, timeout=8)
            r.raise_for_status()
            markets = r.json().get("markets", [])
            if markets:
                markets.sort(key=lambda m: m.get("close_time", ""))
                m = markets[0]
                cts = m.get("close_time", "")
                if cts.endswith("Z"):
                    cts = cts.replace("Z", "+00:00")
                ct = datetime.fromisoformat(cts)
                prev, _ = shared.get_market()
                shared.set_market(m.get("ticker"), ct)
                if m.get("ticker") != prev:
                    strike = _extract_strike(m)
                    bot.strike = strike
                    bot.close_dt = ct
                    logger.info(f"[LOCKED ON] {m.get('ticker')} | strike={strike} | Closes {ct.strftime('%H:%M:%S UTC')}")
            else:
                logger.warning("No open KXBTC15M markets")
        except Exception as e:
            logger.error(f"Discovery error: {e}")
        shared.stop.wait(getattr(config, "DISCOVERY_SEC", 30))


def book_loop(shared: Shared, client: KalshiClient, api_lock: threading.Lock):
    while not shared.stop.is_set():
        ticker, _ = shared.get_market()
        if not ticker:
            shared.stop.wait(1)
            continue
        try:
            with api_lock:
                ob = client.get_orderbook(ticker, depth=getattr(config, "BOOK_DEPTH", 20))
            shared.set_book(ob)
        except Exception as e:
            logger.error(f"Book fetch error: {e}")
        shared.stop.wait(getattr(config, "BOOK_POLL_SEC", 1))


def spot_loop(shared: Shared, bot: MarketMakerBot):
    history = []  # (ts, price), thread-local
    window = getattr(config, "SPOT_WINDOW", 30)
    while not shared.stop.is_set():
        try:
            now = time.time()
            px = current_price()
            history.append((now, px))
            history = [(t, p) for t, p in history if now - t <= window]
            vel = 0.0
            if len(history) >= 2:
                t0, p0 = history[0]
                dt = now - t0
                vel = (px - p0) / dt if dt > 0 else 0.0
            bot.set_spot(px, vel)
        except Exception as e:
            logger.debug(f"Spot poll error: {e}")
        shared.stop.wait(getattr(config, "SPOT_POLL_SEC", 2))


def manager_loop(shared: Shared, bot: MarketMakerBot):
    max_book_age = getattr(config, "MAX_BOOK_AGE_SEC", 5)
    while not shared.stop.is_set():
        ticker, close_time = shared.get_market()
        ob, ob_time = shared.get_book()
        if not ticker or ob is None:
            shared.stop.wait(getattr(config, "POLL_SECONDS", 2))
            continue
        if time.time() - ob_time > max_book_age:
            logger.warning("Stale orderbook, skipping tick")
            shared.stop.wait(getattr(config, "POLL_SECONDS", 2))
            continue
        try:
            bot.process_market_tick(ticker, ob, close_time)
        except Exception as e:
            logger.error(f"Manager tick error: {e}")
        shared.stop.wait(getattr(config, "POLL_SECONDS", 2))


def main():
    print(f"Starting threaded Kalshi BTC 15M | env={config.KALSHI_ENVIRONMENT}")
    api_lock = threading.Lock()
    client = KalshiClient(
        api_key_id=config.KALSHI_API_KEY_ID,
        private_key_path=config.KALSHI_PRIVATE_KEY_PATH,
        base_url=config.BASE_URL,
    )
    bot = MarketMakerBot(client=client, api_lock=api_lock)
    shared = Shared()

    threads = [
        threading.Thread(target=discovery_loop, args=(shared, bot), name="discovery", daemon=True),
        threading.Thread(target=book_loop, args=(shared, client, api_lock), name="book", daemon=True),
        threading.Thread(target=spot_loop, args=(shared, bot), name="spot", daemon=True),
        threading.Thread(target=manager_loop, args=(shared, bot), name="manager", daemon=True),
    ]
    for t in threads:
        t.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutdown signal. Flattening + exiting.")
        shared.stop.set()
        # best-effort flatten
        try:
            ob, _ = shared.get_book()
            if ob and bot.net_yes_inventory != 0:
                bot._flatten_inventory(ob, "Shutdown")
        except Exception as e:
            logger.error(f"Shutdown flatten error: {e}")
        for t in threads:
            t.join(timeout=3)


if __name__ == "__main__":
    main()
