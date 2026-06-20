"""
macro_engine.py
----------------
Systemic Risk-Off detector (VIX + DXY), polled every ~60 seconds.

Kraken does NOT provide VIX or DXY data (those are traditional-markets
instruments), so this module talks to an external provider. The default
implementation uses `yfinance`, a free/unofficial wrapper around Yahoo
Finance - it requires outbound internet access from wherever you RUN this
bot, and Yahoo can change/rate-limit this endpoint at any time without
notice. For anything you depend on financially, consider swapping in a
paid, SLA-backed data provider (Alpha Vantage, Twelve Data, Polygon.io,
etc.) by implementing the same MacroDataProvider interface below.

Decision rule (from the strategy doc):
  - VIX > vix_risk_off_threshold  -> systemic Risk-Off
  - DXY moves more than `dxy_spike_pct_threshold`% within `dxy_spike_window_minutes`
    -> sudden flight-to-dollar -> also treated as Risk-Off
  - Otherwise: Normal regime
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field


class MacroDataProvider:
    """Interface - implement get_vix() and get_dxy() against whatever data source you have."""

    def get_vix(self) -> float | None:
        raise NotImplementedError

    def get_dxy(self) -> float | None:
        raise NotImplementedError


class YFinanceMacroProvider(MacroDataProvider):
    """Default free provider. Requires `pip install yfinance` and internet access."""

    def __init__(self):
        try:
            import yfinance as yf  # imported lazily so the rest of the app works without it
        except ImportError as exc:
            raise ImportError(
                "yfinance is not installed. Run: pip install yfinance --break-system-packages"
            ) from exc
        self._yf = yf

    def _last_price(self, ticker: str) -> float | None:
        try:
            data = self._yf.Ticker(ticker).fast_info
            price = data.get("lastPrice") or data.get("last_price")
            if price:
                return float(price)
        except Exception:
            pass
        # fallback to a 1-minute history pull
        try:
            hist = self._yf.Ticker(ticker).history(period="1d", interval="1m")
            if not hist.empty:
                return float(hist["Close"].iloc[-1])
        except Exception:
            return None
        return None

    def get_vix(self) -> float | None:
        return self._last_price("^VIX")

    def get_dxy(self) -> float | None:
        # ICE US Dollar Index futures ticker on Yahoo Finance
        return self._last_price("DX-Y.NYB")


@dataclass
class MacroState:
    vix: float | None
    dxy: float | None
    dxy_change_pct: float | None
    risk_off: bool
    reasons: list[str] = field(default_factory=list)


class MacroEngine:
    def __init__(self, provider: MacroDataProvider, vix_risk_off_threshold: float = 28,
                 vix_calm_threshold: float = 20, dxy_spike_window_minutes: int = 15,
                 dxy_spike_pct_threshold: float = 0.5, poll_seconds: int = 60):
        self.provider = provider
        self.vix_risk_off_threshold = vix_risk_off_threshold
        self.vix_calm_threshold = vix_calm_threshold
        self.dxy_spike_window_minutes = dxy_spike_window_minutes
        self.dxy_spike_pct_threshold = dxy_spike_pct_threshold
        self.poll_seconds = poll_seconds
        max_points = max(2, int((dxy_spike_window_minutes * 60) / poll_seconds) + 2)
        self._dxy_history: deque[tuple[float, float]] = deque(maxlen=max_points)  # (timestamp, value)

    def poll(self) -> MacroState:
        reasons = []
        vix = self.provider.get_vix()
        dxy = self.provider.get_dxy()
        now = time.time()

        dxy_change_pct = None
        if dxy is not None:
            self._dxy_history.append((now, dxy))
            window_start = now - self.dxy_spike_window_minutes * 60
            past_points = [v for t, v in self._dxy_history if t <= window_start + self.poll_seconds]
            if past_points:
                old = past_points[0]
                if old:
                    dxy_change_pct = (dxy - old) / old * 100

        risk_off = False
        if vix is not None and vix > self.vix_risk_off_threshold:
            risk_off = True
            reasons.append(f"VIX {vix:.1f} > risk-off threshold {self.vix_risk_off_threshold}")
        if dxy_change_pct is not None and abs(dxy_change_pct) > self.dxy_spike_pct_threshold:
            risk_off = True
            direction = "spike up" if dxy_change_pct > 0 else "spike down"
            reasons.append(
                f"DXY {direction} {dxy_change_pct:+.2f}% within {self.dxy_spike_window_minutes}min "
                f"(threshold {self.dxy_spike_pct_threshold}%)"
            )
        if not reasons:
            reasons.append("No risk-off signal (VIX and DXY within normal range)")

        return MacroState(vix=vix, dxy=dxy, dxy_change_pct=dxy_change_pct,
                           risk_off=risk_off, reasons=reasons)
