import base64
import json
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
        message = timestamp_ms + method.upper() + path
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
        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-SIGNATURE": self._sign(ts, method, path),
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
        except requests.RequestException as exc:
            logger.error("Network error calling %s %s: %s", method, path, exc)
            raise

        if not resp.ok:
            raise KalshiAPIError(resp.status_code, resp.text)

        return resp.json() if resp.text else {}

    # ── Market discovery ──────────────────────────────────────────────────────

    def get_markets(self, series_ticker: str = None, status: str = "open", limit: int = 100) -> list[dict]:
        params = {"status": status, "limit": limit}
        if series_ticker:
            params["series_ticker"] = series_ticker
        data = self._request("GET", "/markets", params=params)
        return data.get("markets", [])

    def get_market(self, ticker: str) -> dict:
        return self._request("GET", f"/markets/{ticker}")

    def get_orderbook(self, ticker: str, depth: int = 5) -> dict:
        return self._request("GET", f"/markets/{ticker}/orderbook", params={"depth": depth})

    def get_series(self, series_ticker: str) -> dict:
        return self._request("GET", f"/series/{series_ticker}")

    # ── Orders ────────────────────────────────────────────────────────────────

    def place_order(
        self,
        ticker: str,
        side: str,          # "yes" or "no"
        action: str,        # "buy" or "sell"
        count: int,
        order_type: str = "limit",
        yes_price: Optional[int] = None,   # cents (1-99)
        no_price: Optional[int] = None,
    ) -> dict:
        body = {
            "ticker": ticker,
            "side": side,
            "action": action,
            "count": count,
            "type": order_type,
            "client_order_id": f"bot_{int(time.time() * 1000)}",
        }
        if yes_price is not None:
            body["yes_price"] = yes_price
        if no_price is not None:
            body["no_price"] = no_price

        logger.info(
            "Placing order: %s %s %s x%d @ yes=%s no=%s",
            action, side, ticker, count, yes_price, no_price,
        )
        return self._request("POST", "/orders", body=body)

    def cancel_order(self, order_id: str) -> dict:
        return self._request("DELETE", f"/orders/{order_id}")

    def get_orders(self, ticker: str = None, status: str = None) -> list[dict]:
        params = {}
        if ticker:
            params["ticker"] = ticker
        if status:
            params["status"] = status
        data = self._request("GET", "/orders", params=params)
        return data.get("orders", [])

    # ── Portfolio ─────────────────────────────────────────────────────────────

    def get_balance(self) -> dict:
        return self._request("GET", "/portfolio/balance")

    def get_positions(self, ticker: str = None) -> list[dict]:
        params = {}
        if ticker:
            params["ticker"] = ticker
        data = self._request("GET", "/portfolio/positions", params=params)
        return data.get("market_positions", [])
