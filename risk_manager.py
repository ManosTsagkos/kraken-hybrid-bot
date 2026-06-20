"""
risk_manager.py
-----------------
This module exists to protect YOU from the strategy/decision engine, not the
other way around. It enforces hard caps that are never exceeded regardless
of what decision_engine.py asks for, including:
  - max leverage ceiling
  - max % of equity in a single position
  - a daily-loss circuit breaker that halts all NEW trades (existing
    positions can still be closed) once a daily loss limit is hit

This is the layer most worth reviewing carefully and adjusting to your own
risk tolerance before ever pointing this at a funded account.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass


@dataclass
class RiskCheckResult:
    allowed: bool
    leverage: float
    position_size_pct_of_equity: float
    reasons: list[str]


class RiskManager:
    def __init__(self, max_leverage: float, max_position_pct_of_equity: float,
                 max_daily_loss_pct: float, default_stop_loss_pct: float):
        self.max_leverage = max_leverage
        self.max_position_pct_of_equity = max_position_pct_of_equity
        self.max_daily_loss_pct = max_daily_loss_pct
        self.default_stop_loss_pct = default_stop_loss_pct

        self._day_anchor: dt.date | None = None
        self._equity_at_day_start: float | None = None
        self._halted = False
        self._halt_reason: str | None = None

    def _roll_day_if_needed(self, current_equity: float, today: dt.date | None = None):
        today = today or dt.datetime.utcnow().date()
        if self._day_anchor != today:
            self._day_anchor = today
            self._equity_at_day_start = current_equity
            self._halted = False
            self._halt_reason = None

    def check_daily_circuit_breaker(self, current_equity: float) -> tuple[bool, str | None]:
        """
        Returns (halted, reason). Call this once per loop iteration with the
        latest account equity, BEFORE evaluating whether to open new risk.
        """
        self._roll_day_if_needed(current_equity)
        if self._equity_at_day_start and self._equity_at_day_start > 0:
            pnl_pct = (current_equity - self._equity_at_day_start) / self._equity_at_day_start * 100
            if pnl_pct <= -abs(self.max_daily_loss_pct) and not self._halted:
                self._halted = True
                self._halt_reason = (
                    f"Daily circuit breaker triggered: equity down {pnl_pct:.2f}% "
                    f"today (limit -{self.max_daily_loss_pct}%). No NEW positions "
                    f"will be opened until the next UTC day. Existing positions "
                    f"can still be closed/protected."
                )
        return self._halted, self._halt_reason

    def cap_leverage(self, requested_leverage: float) -> tuple[float, list[str]]:
        reasons = []
        capped = min(requested_leverage, self.max_leverage)
        if capped < requested_leverage:
            reasons.append(
                f"Requested leverage {requested_leverage}x capped to hard ceiling {self.max_leverage}x"
            )
        return capped, reasons

    def cap_position_size(self, requested_pct_of_equity: float) -> tuple[float, list[str]]:
        reasons = []
        capped = min(requested_pct_of_equity, self.max_position_pct_of_equity)
        if capped < requested_pct_of_equity:
            reasons.append(
                f"Requested position size {requested_pct_of_equity:.1f}% of equity capped "
                f"to hard ceiling {self.max_position_pct_of_equity}%"
            )
        return capped, reasons

    def evaluate(self, current_equity: float, requested_leverage: float,
                 requested_pct_of_equity: float, is_opening_new_risk: bool) -> RiskCheckResult:
        reasons: list[str] = []
        halted, halt_reason = self.check_daily_circuit_breaker(current_equity)

        if halted and is_opening_new_risk:
            reasons.append(halt_reason or "Daily circuit breaker active")
            return RiskCheckResult(allowed=False, leverage=0, position_size_pct_of_equity=0,
                                    reasons=reasons)

        lev, lev_reasons = self.cap_leverage(requested_leverage)
        size, size_reasons = self.cap_position_size(requested_pct_of_equity)
        reasons.extend(lev_reasons)
        reasons.extend(size_reasons)
        if not reasons:
            reasons.append("Within all risk limits")

        return RiskCheckResult(allowed=True, leverage=lev, position_size_pct_of_equity=size,
                                reasons=reasons)
