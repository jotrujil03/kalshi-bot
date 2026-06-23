"""
bot.py - Spot-gated directional scalper.
Entry only on BTC spot velocity. One side. Exit on TP or spot reversal.
"""
import logging
import threading
import time
from datetime import datetime, timezone

import strategy
import fair_value
from btc_data import current_price

try:
    import config
    from kalshi_client import KalshiClient
    LIVE_IMPORTS_SUCCESS = True
except ImportError:
    LIVE_IMPORTS_SUCCESS = False

logger = logging.getLogger(__name__)


class MarketMakerBot:
    def __init__(self, client=None, api_lock=None):
        self.ticker = None
        self.close_dt = None
        self.is_dry_run = getattr(config, "DRY_RUN", True) if LIVE_IMPORTS_SUCCESS else True
        self.api_lock = api_lock or threading.Lock()

        if client is not None:
            self.client = client
        elif LIVE_IMPORTS_SUCCESS and not self.is_dry_run:
            self.client = KalshiClient(
                api_key_id=config.KALSHI_API_KEY_ID,
                private_key_path=config.KALSHI_PRIVATE_KEY_PATH,
                base_url=config.BASE_URL,
            )
        else:
            self.client = None

        self.net_yes_inventory = 0.0
        self.MAX_INVENTORY = 1

        # Single active entry (one side at a time)
        self.active_yes_bid = 0
        self.active_no_bid = 0
        self.yes_order_id = None
        self.no_order_id = None
        self.entry_order_time = 0

        # Exit
        self.active_yes_ask = 0
        self.active_no_ask = 0
        self.yes_exit_order_id = None
        self.no_exit_order_id = None
        self.exit_order_time = 0

        self.yes_entry_price = 0
        self.no_entry_price = 0
        self.last_stop_time = 0

        # Spot injected by spot thread
        self.spot_vel = 0.0
        self.spot_price = 0.0
        self._spot_lock = threading.Lock()
        self.strike = 0.0

        self.last_inventory_sync = 0

    def set_spot(self, price: float, vel: float):
        with self._spot_lock:
            self.spot_price = price
            self.spot_vel = vel

    def get_spot(self):
        with self._spot_lock:
            return self.spot_price, self.spot_vel

    # ── Order helpers ─────────────────────────────────────────────────────────
    def _cancel_order(self, order_id: str):
        if not order_id or self.is_dry_run:
            return
        try:
            with self.api_lock:
                self.client.cancel_order(order_id)
        except Exception as e:
            if "not_found" not in str(e).lower():
                logger.debug(f"Cancel note: {e}")

    def _sync_inventory(self):
        if self.is_dry_run or not self.client or time.time() - self.last_inventory_sync < 5:
            return
        try:
            with self.api_lock:
                positions = self.client.get_positions(self.ticker)
            self.net_yes_inventory = 0.0
            for pos in positions:
                if pos.get("ticker") == self.ticker:
                    raw = pos.get("yes_position", 0) or pos.get("position", 0)
                    self.net_yes_inventory = float(raw)
            self.last_inventory_sync = time.time()
        except:
            pass

    def _is_good_orderbook(self, orderbook: dict) -> bool:
        yes = orderbook.get("yes", [])
        no = orderbook.get("no", [])
        total = sum(q for _, q in yes) + sum(q for _, q in no)
        return len(yes) > 5 and len(no) > 5 and total >= 50

    def _place_live_order(self, action: str, side: str, price: int) -> str:
        if self.is_dry_run:
            logger.info(f"[DRY_RUN] Would {action.upper()} {side.upper()} x1 @ {price}¢")
            return "DRY"
        try:
            with self.api_lock:
                resp = self.client.place_order(
                    ticker=self.ticker, side=side, action=action, count=1,
                    yes_price=price if side == "yes" else None,
                    no_price=price if side == "no" else None,
                )
            order_id = resp.get("order_id") or resp.get("order", {}).get("id") or "unknown"
            logger.info(f"Placed {action} {side} @ {price}¢ (ID: {order_id})")
            return order_id
        except Exception as e:
            logger.error(f"Place failed: {e}")
            return None

    # ── Main tick ─────────────────────────────────────────────────────────────
    def process_market_tick(self, ticker: str, orderbook: dict, close_time: datetime):
        self.ticker = ticker
        self.close_dt = close_time
        self._sync_inventory()

        minutes_left = (close_time - datetime.now(timezone.utc)).total_seconds() / 60.0
        if minutes_left <= getattr(config, "EXIT_BEFORE_CLOSE_MIN", 2) and self.net_yes_inventory != 0:
            self._flatten_inventory(orderbook, "Expiration")
            return

        if not self._is_good_orderbook(orderbook):
            self._cancel_all_entries()
            return

        self._manage(orderbook)

    def _cancel_all_entries(self):
        self._cancel_order(self.yes_order_id); self.yes_order_id = None
        self._cancel_order(self.no_order_id);  self.no_order_id = None
        self.active_yes_bid = self.active_no_bid = 0

    # ── Strategy core ─────────────────────────────────────────────────────────
    def _manage(self, orderbook: dict):
        _, spot_vel = self.get_spot()
        stopband = getattr(config, "SPOT_STOP", 0.5)        # $/sec reversal to exit

        # TP scaled to clear round-trip fees
        tp_cents = getattr(config, "TAKE_PROFIT_CENTS", 3)
        entry_px = self.yes_entry_price if self.net_yes_inventory > 0 else self.no_entry_price
        if entry_px:
            fee = strategy.kalshi_fee_cents(entry_px)
            tp_cents = max(tp_cents, 2 * fee + 1)

        # ----- FLAT: clean exit state, then consider entry -----
        if self.net_yes_inventory == 0:
            self._clear_exit_state()

            if time.time() - self.last_stop_time < getattr(config, "REENTRY_COOLDOWN", 5):
                self._cancel_all_entries()
                return

            plan = strategy.generate_quotes(orderbook, self.net_yes_inventory, self.MAX_INVENTORY)
            if plan.get("status") != "active":
                self._cancel_all_entries()
                return

            sig = self._fair_value_signal(orderbook)
            if sig is None:
                self._cancel_all_entries()
                return
            if sig == "yes":
                self._quote_entry("yes", plan["quotes"]["yes_bid"], spot_vel)
                self._cancel_side("no")
            else:
                self._quote_entry("no", plan["quotes"]["no_bid"], spot_vel)
                self._cancel_side("yes")
            return

        # ----- LONG YES -----
        if self.net_yes_inventory > 0:
            if self.yes_entry_price == 0:
                self.yes_entry_price = self.active_yes_bid
                logger.info(f"YES fill. Entry {self.yes_entry_price}¢")
            if spot_vel < -stopband:
                logger.info(f"SPOT STOP: {spot_vel:.2f}$/s, dumping YES")
                self.last_stop_time = time.time()
                self._flatten_inventory(orderbook, "SpotStop")
                return
            self._manage_exit("yes", self.yes_entry_price, tp_cents, orderbook)
            return

        # ----- LONG NO -----
        if self.net_yes_inventory < 0:
            if self.no_entry_price == 0:
                self.no_entry_price = self.active_no_bid
                logger.info(f"NO fill. Entry {self.no_entry_price}¢")
            if spot_vel > stopband:
                logger.info(f"SPOT STOP: {spot_vel:.2f}$/s, dumping NO")
                self.last_stop_time = time.time()
                self._flatten_inventory(orderbook, "SpotStop")
                return
            self._manage_exit("no", self.no_entry_price, tp_cents, orderbook)
            return

    def _implied_yes(self, orderbook: dict):
        yes = orderbook.get("yes", [])
        no = orderbook.get("no", [])
        if not yes or not no:
            return None
        best_yes = max(p for p, _ in yes)
        best_no = max(p for p, _ in no)
        implied_yes_ask = 100 - best_no
        return (best_yes + implied_yes_ask) / 200.0  # [0,1]

    def _fair_value_signal(self, orderbook: dict):
        spot, _ = self.get_spot()
        if spot <= 0 or self.strike <= 0 or not self.close_dt:
            return None
        secs_left = (self.close_dt - datetime.now(timezone.utc)).total_seconds()
        if secs_left <= getattr(config, "MIN_SECS_LEFT", 60):
            return None
        implied = self._implied_yes(orderbook)
        if implied is None:
            return None
        # round-trip fee at the implied price (cents)
        fee_rt = 2 * strategy.kalshi_fee_cents(implied * 100)
        e = fair_value.edge(spot, self.strike, secs_left, implied, fee_rt)
        logger.info(f"FV: spot={spot:.0f} K={self.strike:.0f} fair={e['fair_yes']:.3f} impl={implied:.3f} div={e['divergence']:+.3f} thr={e['threshold']:.3f}")
        if e["signal"]:
            logger.info(f"EDGE {e['signal'].upper()}: fair={e['fair_yes']:.3f} "
                        f"impl={implied:.3f} div={e['divergence']:+.3f} thr={e['threshold']:.3f} "
                        f"sig={self.spot_vel:+.2f}$/s")
        return e["signal"]

    def _quote_entry(self, side: str, price: int, spot_vel: float):
        if price <= 0:
            return
        active = self.active_yes_bid if side == "yes" else self.active_no_bid
        oid = self.yes_order_id if side == "yes" else self.no_order_id
        requote = getattr(config, "REQUOTE_DRIFT_CENTS", 1)
        max_q = getattr(config, "MAX_QUOTE_SECONDS", 30)
        drift = abs(price - active) if active else 999
        stale = self.entry_order_time and time.time() - self.entry_order_time > max_q
        if drift < requote and not stale:
            return
        self._cancel_order(oid)
        new_id = None
        logger.info(f"ENTRY {side.upper()} {price}¢ | SpotVel {spot_vel:.2f}$/s")
        new_id = self._place_live_order("buy", side, price)
        self.entry_order_time = time.time()
        if side == "yes":
            self.yes_order_id = new_id; self.active_yes_bid = price
        else:
            self.no_order_id = new_id; self.active_no_bid = price

    def _cancel_side(self, side: str):
        if side == "yes":
            if self.yes_order_id or self.active_yes_bid:
                self._cancel_order(self.yes_order_id)
                self.yes_order_id = None; self.active_yes_bid = 0
        else:
            if self.no_order_id or self.active_no_bid:
                self._cancel_order(self.no_order_id)
                self.no_order_id = None; self.active_no_bid = 0

    def _manage_exit(self, side: str, entry: int, tp_cents: int, orderbook: dict):
        ask_price = int(entry + tp_cents)
        timeout = getattr(config, "MAX_QUOTE_SECONDS", 30)
        active = self.active_yes_ask if side == "yes" else self.active_no_ask
        oid = self.yes_exit_order_id if side == "yes" else self.no_exit_order_id
        if self.exit_order_time and time.time() - self.exit_order_time > timeout:
            best_bid = max((p for p, _ in orderbook.get(side, [])), default=ask_price)
            ask_price = min(ask_price, best_bid)   # cross to fill
        if active == ask_price:
            return
        self._cancel_order(oid)
        logger.info(f"EXIT SELL {side.upper()} @ {ask_price}¢ (Entry {entry}¢)")
        new_id = self._place_live_order("sell", side, ask_price)
        self.exit_order_time = time.time()
        if side == "yes":
            self.yes_exit_order_id = new_id; self.active_yes_ask = ask_price
        else:
            self.no_exit_order_id = new_id; self.active_no_ask = ask_price

    def _clear_exit_state(self):
        if self.active_yes_ask or self.yes_exit_order_id:
            self._cancel_order(self.yes_exit_order_id)
            self.yes_exit_order_id = None; self.active_yes_ask = 0; self.yes_entry_price = 0
        if self.active_no_ask or self.no_exit_order_id:
            self._cancel_order(self.no_exit_order_id)
            self.no_exit_order_id = None; self.active_no_ask = 0; self.no_entry_price = 0
        self.exit_order_time = 0

    def _flatten_inventory(self, orderbook: dict, reason: str):
        self._cancel_order(self.yes_order_id); self.yes_order_id = None
        self._cancel_order(self.no_order_id);  self.no_order_id = None
        self._cancel_order(self.yes_exit_order_id); self.yes_exit_order_id = None
        self._cancel_order(self.no_exit_order_id);  self.no_exit_order_id = None
        self.active_yes_bid = self.active_no_bid = 0
        self.active_yes_ask = self.active_no_ask = 0
        self.yes_entry_price = self.no_entry_price = 0
        self.exit_order_time = 0

        if self.net_yes_inventory > 0:
            best = max((p for p, _ in orderbook.get("yes", [])), default=1)
            self._place_live_order("sell", "yes", best)
        elif self.net_yes_inventory < 0:
            best = max((p for p, _ in orderbook.get("no", [])), default=1)
            self._place_live_order("sell", "no", best)
        self.net_yes_inventory = 0
        logger.info(f"FLATTENED ({reason})")
