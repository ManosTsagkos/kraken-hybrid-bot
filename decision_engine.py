"""
decision_engine.py
-------------------
Combines the three independent layers (4H technical trend, macro risk-off
state, news/IPO/geopolitical state) into a single Action, following the
Decision Matrix from the strategy document.

This module is pure logic - no API calls, no side effects - so it is fully
unit-testable. order_executor.py is responsible for actually turning an
Action into Kraken orders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from indicators import TechnicalSnapshot
from macro_engine import MacroState
from news_engine import NewsState


class ActionType(str, Enum):
    HOLD_LONG = "HOLD_LONG"                  # maintain existing long, normal trailing stop
    HOLD_SHORT = "HOLD_SHORT"                # maintain existing short, normal trailing stop
    HOLD_FLAT = "HOLD_FLAT"                  # no position, no signal to enter
    OPEN_LONG = "OPEN_LONG"
    OPEN_SHORT = "OPEN_SHORT"
    EMERGENCY_EXIT = "EMERGENCY_EXIT"        # close everything immediately (capital protection)
    REDUCE_POSITION = "REDUCE_POSITION"      # cut position size (e.g. IPO capital drain)
    TIGHTEN_STOP = "TIGHTEN_STOP"            # Defensive Mode: tighten trailing stop only
    STRATEGY_FLIP = "STRATEGY_FLIP"          # close current side, open opposite immediately
    INCREASE_CONVICTION = "INCREASE_CONVICTION"  # macro/geo confirms technical side -> raise leverage (capped)
    STAND_ASIDE = "STAND_ASIDE"              # explicit no-trade (e.g. ROC exhaustion)


@dataclass
class Decision:
    action: ActionType
    leverage: float
    trailing_stop_pct: float
    position_size_pct_of_max: float   # 0-100, scales the configured max position size
    reasons: list[str] = field(default_factory=list)


class DecisionEngine:
    def __init__(self, risk_config: dict, macro_config: dict, news_config: dict):
        self.risk = risk_config
        self.macro_cfg = macro_config
        self.news_cfg = news_config

    def decide(self, tech: TechnicalSnapshot, macro: MacroState, news: NewsState,
               current_position_side: str | None) -> Decision:
        """
        current_position_side: "LONG", "SHORT", or None (flat)

        Implements, in priority order (matching the strategy doc's intent that
        risk protection overrides the slower technical signal):
          1. Geopolitical Strategy Flip (highest priority - overrides everything)
          2. Systemic Risk-Off + existing LONG -> Emergency Exit
          3. Systemic Risk-Off + existing SHORT + geopolitical risk-off -> Increase conviction
          4. IPO capital drain -> Reduce position
          5. Defensive tightening on risk-off with no flip/exit condition
          6. Normal technical-trend-following (open/hold/stand aside)
        """
        reasons: list[str] = []
        base_leverage = self.risk.get("base_leverage", 1)
        max_leverage = self.risk.get("max_leverage", 2)
        default_trailing = self.risk.get("trailing_stop_pct", 1.0)
        defensive_trailing = self.macro_cfg.get("defensive_trailing_stop_pct", 0.5)

        # 1. Geopolitical Strategy Flip - highest priority, bypasses the 4H candle entirely
        if news.strategy_flip:
            reasons.extend(news.reasons)
            target_side = "SHORT" if current_position_side != "SHORT" else None
            # Strategy doc example flips LONG/flat -> SHORT on geopolitical shock combined
            # with a DXY spike; if we're already SHORT, just reinforce it instead of flipping again.
            if current_position_side == "SHORT":
                reasons.append("Already SHORT - reinforcing instead of flipping again")
                return Decision(ActionType.INCREASE_CONVICTION, min(max_leverage, base_leverage + 1),
                                 defensive_trailing, 100, reasons)
            return Decision(ActionType.STRATEGY_FLIP, base_leverage, defensive_trailing, 100, reasons)

        # 2. Systemic risk-off while LONG -> capital protection first
        if macro.risk_off:
            reasons.extend(macro.reasons)
            if current_position_side == "LONG":
                reasons.append("LONG position during systemic Risk-Off -> Emergency Exit (capital protection)")
                return Decision(ActionType.EMERGENCY_EXIT, 0, defensive_trailing, 0, reasons)

            # 3. Risk-off while SHORT and technical trend agrees -> reinforce (within hard cap)
            if current_position_side == "SHORT" and tech.trend == "SHORT":
                reasons.append("SHORT position aligned with Risk-Off regime -> increase conviction (capped leverage)")
                return Decision(ActionType.INCREASE_CONVICTION, min(max_leverage, base_leverage + 1),
                                 defensive_trailing, 100, reasons)

            # Risk-off with no position, or risk-off while flat: defensive stance, don't open new risk blindly
            reasons.append("Risk-Off with no clear aligned position -> Defensive Mode (tighten stops only)")
            return Decision(ActionType.TIGHTEN_STOP, base_leverage, defensive_trailing,
                             100 if current_position_side else 0, reasons)

        # 4. IPO capital drain -> reduce position size regardless of side
        if news.capital_drain_flagged and current_position_side is not None:
            reasons.extend(news.reasons)
            cut_pct = self.news_cfg.get("ipo_position_size_cut_pct", 50)
            reasons.append(f"Mega-IPO capital drain event -> cutting position size by {cut_pct}%")
            return Decision(ActionType.REDUCE_POSITION, base_leverage, default_trailing,
                             max(0, 100 - cut_pct), reasons)

        # 5. AI bubble expansion -> mild risk-on note (doesn't override technical trend by itself)
        if news.ai_bubble_expansion_flagged:
            reasons.append("AI bubble-expansion signal noted (informational - technical trend still governs entries)")

        # 6. Normal regime - technical trend governs
        reasons.extend(tech.reasons)
        if tech.trend == "LONG":
            if current_position_side == "LONG":
                return Decision(ActionType.HOLD_LONG, base_leverage, default_trailing, 100, reasons)
            return Decision(ActionType.OPEN_LONG, base_leverage, default_trailing, 100, reasons)
        if tech.trend == "SHORT":
            if current_position_side == "SHORT":
                return Decision(ActionType.HOLD_SHORT, base_leverage, default_trailing, 100, reasons)
            return Decision(ActionType.OPEN_SHORT, base_leverage, default_trailing, 100, reasons)

        # NEUTRAL technical trend
        if current_position_side is None:
            reasons.append("No technical edge -> standing aside")
            return Decision(ActionType.STAND_ASIDE, base_leverage, default_trailing, 0, reasons)
        # Have a position but trend turned neutral -> hold with normal stop, let the stop do its job
        side_action = ActionType.HOLD_LONG if current_position_side == "LONG" else ActionType.HOLD_SHORT
        reasons.append("Technical trend turned NEUTRAL while in a position -> hold with normal trailing stop")
        return Decision(side_action, base_leverage, default_trailing, 100, reasons)
