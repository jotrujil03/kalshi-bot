import base64
import logging
import time
from typing import Optional

import requests
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

logger = logging.getLogger(__name__)


class KalshiAPIError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(f"Kalshi API error {status_code}: {message}")


class KalshiClient:
    def __init__(self, api_key_id: str, private_key_path: str, base_url: str):
        self.api_key_id = api_key_id
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json", "Accept": "application/json"})

        with open(private_key_path, "rb") as f:
            self.private_key = serialization.load_pem_private_key(
                f.read(), password=None, backend=default_backend()
            )
        logger.info("Kalshi client initialized (env: %s)", base_url)

    def _sign(self, timestamp_ms: str, method: str, path: str) -> str:
        """Critical: Kalshi requires full path including /trade-api/v2 prefix for signing"""
        # Clean path
        if '?' in path:
            path = path.split('?')[0]
        if not path.startswith('/'):
            path = '/' + path

        # Full signing path (this fixes INCORRECT_API_KEY_SIGNATURE)
        full_sign_path = "/trade-api/v2" + path if not path.startswith("/trade-api/v2") else path

        message = timestamp_ms + method.upper() + full_sign_path
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"Signing message: {message}")

        signature = self.private_key.sign(
            message.encode("utf-8"),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return base64.b64encode(signature).decode("utf-8")

    def _auth_headers(self, method: str, path: str) -> dict:
        ts = str(int(time.time() * 1000))
        signature = self._sign(ts, method, path)
        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "KALSHI-ACCESS-TIMESTAMP": ts,
        }

    def _request(self, method: str, path: str, params: dict = None, body: dict = None) -> dict:
        headers = self._auth_headers(method, path)
        url = self.base_url + path
        try:
            resp = self.session.request(
                method,
                url,
                headers=headers,
                params=params,
                json=body,
                timeout=10,
            )
            if not resp.ok:
                if method == "DELETE" and resp.status_code == 404:
                    logger.debug(f"Order already gone on DELETE {path}")
                else:
                    logger.error(f"Kalshi error {resp.status_code} on {method} {path}: {resp.text}")
                raise KalshiAPIError(resp.status_code, resp.text)

            # FIX: Ensure we never return None if the API responds with `null`
            parsed = resp.json() if resp.text else {}
            return parsed if isinstance(parsed, dict) else {}

        except requests.RequestException as exc:
            logger.error(f"Network error on {method} {path}: {exc}")
            raise

    # ── Market discovery ──────────────────────────────────────────────────────

    def get_markets(self, series_ticker: str = None, status: str = "open", limit: int = 100) -> list[dict]:
        params = {"status": status, "limit": limit}
        if series_ticker:
            params["series_ticker"] = series_ticker
        data = self._request("GET", "/markets", params=params)
        return data.get("markets", [])

    def get_market(self, ticker: str) -> dict:
        return self._request("GET", f"/markets/{ticker}")

    def get_orderbook(self, ticker: str, depth: int = 10) -> dict:
        """Returns normalized orderbook with yes/no bids in cents."""
        data = self._request("GET", f"/markets/{ticker}/orderbook", params={"depth": depth})

        # Safely fallback to {} if the API returns null for the orderbook
        ob_fp = data.get("orderbook_fp") or data.get("orderbook") or {}
        if not isinstance(ob_fp, dict):
            ob_fp = {}

        yes_raw = ob_fp.get("yes_dollars", []) or ob_fp.get("yes", [])
        no_raw = ob_fp.get("no_dollars", []) or ob_fp.get("no", [])

        yes_bids = []
        for price_str, qty_str in yes_raw:
            price_cents = int(float(price_str) * 100)
            qty = int(float(qty_str))
            yes_bids.append((price_cents, qty))

        no_bids = []
        for price_str, qty_str in no_raw:
            price_cents = int(float(price_str) * 100)
            qty = int(float(qty_str))
            no_bids.append((price_cents, qty))

        yes_bids.sort(reverse=True)
        no_bids.sort(reverse=True)

        logger.debug(f"Orderbook for {ticker}: {len(yes_bids)} YES | {len(no_bids)} NO")
        return {"yes": yes_bids, "no": no_bids}

    def get_series(self, series_ticker: str) -> dict:
        return self._request("GET", f"/series/{series_ticker}")

    # ── Orders ────────────────────────────────────────────────────────────────

    def place_order(
        self,
        ticker: str,
        side: str,
        action: str,
        count: int,
        order_type: str = "limit",
        yes_price: Optional[int] = None,
        no_price: Optional[int] = None,
    ) -> dict:
        # Map (side, action) -> YES-leg bid/ask + YES price in cents
        if side == "yes":
            price_cents = yes_price
            if price_cents is None:
                raise ValueError("yes_price required")
            book_side = "bid" if action == "buy" else "ask"
            yes_price_cents = price_cents
        elif side == "no":
            price_cents = no_price
            if price_cents is None:
                raise ValueError("no_price required")
            # buy NO == sell YES @ (100-n); sell NO == buy YES @ (100-n)
            book_side = "ask" if action == "buy" else "bid"
            yes_price_cents = 100 - price_cents
        else:
            raise ValueError(f"Invalid side: {side}")

        if not (1 <= yes_price_cents <= 99):
            raise ValueError(f"Price out of range: {yes_price_cents}¢")

        body = {
            "ticker": ticker,
            "client_order_id": f"bot_{int(time.time() * 1000)}",
            "side": book_side,
            "count": str(int(count)),
            "price": f"{yes_price_cents / 100:.2f}",
            "time_in_force": "good_till_canceled",
            "self_trade_prevention_type": "taker_at_cross",
        }

        logger.info(f"Placing V2 order: {action} {side} {ticker} x{count} @ {price_cents}¢ "
                    f"(YES {book_side} @ {yes_price_cents}¢)")
        return self._request("POST", "/portfolio/events/orders", body=body)

    def cancel_order(self, order_id: str) -> dict:
        # Changed endpoint to /portfolio/orders/{order_id}
        return self._request("DELETE", f"/portfolio/orders/{order_id}")

    def get_orders(self, ticker: str = None, status: str = None) -> list[dict]:
        params = {}
        if ticker:
            params["ticker"] = ticker
        if status:
            params["status"] = status
        data = self._request("GET", "/portfolio/orders", params=params)
        return data.get("orders", [])

    # ── Portfolio ─────────────────────────────────────────────────────────────

    def get_balance(self) -> dict:
        return self._request("GET", "/portfolio/balance")

    def get_positions(self, ticker: str = None) -> list[dict]:
        params = {}
        if ticker:
            params["ticker"] = ticker
        data = self._request("GET", "/portfolio/positions", params=params)
        return data.get("market_positions", []) or data.get("positions", [])
