"""
kraken_client.py
-----------------
Thin wrapper around Kraken's Spot REST API.

Implements the exact authentication scheme published by Kraken:
https://docs.kraken.com/api/docs/guides/spot-rest-auth/

Public endpoints (no auth): Time, OHLC, Ticker, AssetPairs, ...
Private endpoints (signed): Balance, TradeBalance, OpenPositions, AddOrder, ...

Every private request that places/cancels orders goes through `add_order()`,
which honours the global DRY_RUN flag by sending Kraken's own `validate`
parameter (the order is checked by Kraken's engine for errors but is NEVER
actually executed and NEVER returns a real order id). This is documented at:
https://support.kraken.com/articles/advanced-api-faq
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
import urllib.parse
from typing import Any

import requests


class KrakenAPIError(Exception):
    """Raised when Kraken's API returns one or more errors."""


class KrakenClient:
    BASE_URL = "https://api.kraken.com"
    API_VERSION = "0"

    def __init__(self, api_key: str = "", api_secret: str = "", dry_run: bool = True,
                 timeout: int = 15, max_retries: int = 3, retry_backoff: float = 1.5):
        self.api_key = api_key
        self.api_secret = api_secret
        self.dry_run = dry_run
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "institutional-hybrid-engine/1.0"})

    # ------------------------------------------------------------------ #
    # Low-level request plumbing
    # ------------------------------------------------------------------ #
    def _nonce(self) -> str:
        return str(int(time.time() * 1000))

    def _sign(self, urlpath: str, data: dict) -> str:
        postdata = urllib.parse.urlencode(data)
        encoded = (str(data["nonce"]) + postdata).encode()
        message = urlpath.encode() + hashlib.sha256(encoded).digest()
        mac = hmac.new(base64.b64decode(self.api_secret), message, hashlib.sha512)
        return base64.b64encode(mac.digest()).decode()

    def _request(self, method: str, urlpath: str, data: dict | None = None,
                  private: bool = False) -> dict:
        data = data or {}
        url = self.BASE_URL + urlpath
        headers = {}

        if private:
            if not self.api_key or not self.api_secret:
                raise KrakenAPIError(
                    "Private endpoint called but no API key/secret configured. "
                    "Set KRAKEN_API_KEY / KRAKEN_API_SECRET in your .env file."
                )
            data["nonce"] = self._nonce()
            headers["API-Key"] = self.api_key
            headers["API-Sign"] = self._sign(urlpath, data)

        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                if method == "POST":
                    resp = self._session.post(url, data=data, headers=headers, timeout=self.timeout)
                else:
                    resp = self._session.get(url, params=data, headers=headers, timeout=self.timeout)
                resp.raise_for_status()
                payload = resp.json()
                errors = payload.get("error", [])
                if errors:
                    # Kraken returns transient errors (e.g. EService:Busy, EAPI:Rate limit
                    # exceeded) that are worth retrying, and permanent ones that are not.
                    transient = any(e.startswith(("EService", "EGeneral:Temporary",
                                                    "EAPI:Rate limit")) for e in errors)
                    if transient and attempt < self.max_retries:
                        time.sleep(self.retry_backoff * attempt)
                        continue
                    raise KrakenAPIError(f"Kraken API error(s): {errors}")
                return payload.get("result", {})
            except (requests.RequestException, ValueError) as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    time.sleep(self.retry_backoff * attempt)
                    continue
        raise KrakenAPIError(f"Request to {urlpath} failed after {self.max_retries} attempts: {last_exc}")

    # ------------------------------------------------------------------ #
    # Public endpoints
    # ------------------------------------------------------------------ #
    def get_server_time(self) -> dict:
        return self._request("GET", f"/{self.API_VERSION}/public/Time")

    def get_ohlc(self, pair: str, interval: int = 240, since: int | None = None) -> dict:
        """
        interval is in minutes (1, 5, 15, 30, 60, 240, 1440, 10080, 21600).
        Returns up to 720 of the most recent candles. The last candle is the
        current, not-yet-closed one - callers should usually drop it when
        computing indicators that assume closed candles.
        """
        params = {"pair": pair, "interval": interval}
        if since is not None:
            params["since"] = since
        return self._request("GET", f"/{self.API_VERSION}/public/OHLC", params)

    def get_ticker(self, pair: str) -> dict:
        return self._request("GET", f"/{self.API_VERSION}/public/Ticker", {"pair": pair})

    # ------------------------------------------------------------------ #
    # Private endpoints
    # ------------------------------------------------------------------ #
    def get_balance(self) -> dict:
        return self._request("POST", f"/{self.API_VERSION}/private/Balance", private=True)

    def get_trade_balance(self, asset: str = "ZUSD") -> dict:
        return self._request("POST", f"/{self.API_VERSION}/private/TradeBalance",
                              {"asset": asset}, private=True)

    def get_open_positions(self) -> dict:
        return self._request("POST", f"/{self.API_VERSION}/private/OpenPositions",
                              {"docalcs": True}, private=True)

    def get_open_orders(self) -> dict:
        return self._request("POST", f"/{self.API_VERSION}/private/OpenOrders", private=True)

    def add_order(self, pair: str, side: str, ordertype: str, volume: str,
                  price: str | None = None, leverage: str | None = None,
                  reduce_only: bool = False, close_ordertype: str | None = None,
                  close_price: str | None = None, force_dry_run: bool | None = None) -> dict:
        """
        Places (or, if dry_run, validates-only) an order.

        side: "buy" or "sell"
        ordertype: "market", "limit", "stop-loss", "stop-loss-limit", "trailing-stop", ...
        leverage: e.g. "2:1" - only relevant for margin (long or short) orders
        reduce_only: True when this order should only ever reduce/close an existing position
        close_ordertype/close_price: optional attached "close" order (e.g. a stop-loss
            that fires automatically when this order opens a position)

        Honours global self.dry_run by adding Kraken's native `validate=true` flag,
        unless force_dry_run is explicitly passed to override.
        """
        data: dict[str, Any] = {
            "pair": pair,
            "type": side,
            "ordertype": ordertype,
            "volume": volume,
        }
        if price is not None:
            data["price"] = price
        if leverage is not None:
            data["leverage"] = leverage
        if reduce_only:
            data["reduce_only"] = True
        if close_ordertype is not None:
            data["close[ordertype]"] = close_ordertype
            if close_price is not None:
                data["close[price]"] = close_price

        dry = self.dry_run if force_dry_run is None else force_dry_run
        if dry:
            data["validate"] = True

        return self._request("POST", f"/{self.API_VERSION}/private/AddOrder", data, private=True)

    def cancel_order(self, txid: str) -> dict:
        return self._request("POST", f"/{self.API_VERSION}/private/CancelOrder",
                              {"txid": txid}, private=True)

    def cancel_all(self) -> dict:
        return self._request("POST", f"/{self.API_VERSION}/private/CancelAll", private=True)
