"""
order_executor.py
-------------------
Translates a Decision (from decision_engine.py) + RiskCheckResult (from
risk_manager.py) into actual Kraken orders, via KrakenClient.

Every order placed here respects KrakenClient.dry_run: while dry_run is True,
Kraken itself validates the order but never executes it (see kraken_client.py
docstring). This module never overrides that - flipping to live trading is a
deliberate, explicit step the operator takes in .env, not something any code
path here can silently do.
"""

from __future__ import annotations

import logging

from decision_engine import ActionType, Decision
from kraken_client import KrakenClient, KrakenAPIError
from risk_manager import RiskCheckResult
from state import BotState


class OrderExecutor:
    def __init__(self, client: KrakenClient, pair: str, logger: logging.Logger,
                 default_stop_loss_pct: float):
        self.client = client
        self.pair = pair
        self.logger = logger
        self.default_stop_loss_pct = default_stop_loss_pct

    def get_equity_usd(self) -> float:
        """Reads account equity (USD) via TradeBalance. Returns 0.0 on failure
        rather than raising, so a transient API hiccup can't crash the loop -
        callers should treat 0.0 defensively (e.g. skip opening new risk)."""
        try:
            tb = self.client.get_trade_balance(asset="ZUSD")
            return float(tb.get("eb", 0.0))  # 'eb' = equivalent balance
        except (KrakenAPIError, ValueError, TypeError) as exc:
            self.logger.error(f"Failed to fetch trade balance: {exc}")
            return 0.0

    def get_last_price(self) -> float | None:
        try:
            ticker = self.client.get_ticker(self.pair)
            pair_data = next(iter(ticker.values()))
            return float(pair_data["c"][0])  # last trade closed price
        except (KrakenAPIError, KeyError, StopIteration, ValueError, TypeError) as exc:
            self.logger.error(f"Failed to fetch last price: {exc}")
            return None

    def _volume_for_usd_amount(self, usd_amount: float, price: float) -> str:
        if price <= 0:
            return "0"
        volume = usd_amount / price
        return f"{volume:.8f}"

    def execute(self, decision: Decision, risk: RiskCheckResult, state: BotState) -> BotState:
        action = decision.action
        self.logger.info(f"Decision: {action.value} | leverage={decision.leverage} "
                          f"size%={decision.position_size_pct_of_max} | reasons: {decision.reasons}")

        if not risk.allowed:
            self.logger.warning(f"Risk manager blocked new risk: {risk.reasons}")
            if action in (ActionType.OPEN_LONG, ActionType.OPEN_SHORT,
                          ActionType.INCREASE_CONVICTION, ActionType.STRATEGY_FLIP):
                self.logger.warning("Skipping order - daily circuit breaker or risk cap active.")
                return state

        price = self.get_last_price()
        equity = self.get_equity_usd()
        if price is None or equity <= 0:
            self.logger.error("Cannot size/place orders without a valid price and equity figure - skipping this cycle.")
            return state

        usd_at_risk = equity * (risk.position_size_pct_of_equity / 100.0)
        volume = self._volume_for_usd_amount(usd_at_risk, price)
        leverage_str = f"{int(risk.leverage)}:1" if risk.leverage and risk.leverage > 1 else None

        try:
            if action == ActionType.EMERGENCY_EXIT:
                self._close_position(state)
                state.position_side = None
                state.entry_price = None
                state.stop_price = None

            elif action == ActionType.STRATEGY_FLIP:
                if state.position_side is not None:
                    self._close_position(state)
                self._open_position("SHORT", volume, price, leverage_str, decision.trailing_stop_pct)
                state.position_side = "SHORT"
                state.entry_price = price
                state.leverage = risk.leverage

            elif action == ActionType.OPEN_LONG:
                self._open_position("LONG", volume, price, leverage_str, decision.trailing_stop_pct)
                state.position_side = "LONG"
                state.entry_price = price
                state.leverage = risk.leverage

            elif action == ActionType.OPEN_SHORT:
                self._open_position("SHORT", volume, price, leverage_str, decision.trailing_stop_pct)
                state.position_side = "SHORT"
                state.entry_price = price
                state.leverage = risk.leverage

            elif action == ActionType.REDUCE_POSITION and state.position_side:
                self._reduce_position(state, reduce_to_pct=decision.position_size_pct_of_max)

            elif action == ActionType.INCREASE_CONVICTION and state.position_side:
                self.logger.info(
                    f"INCREASE_CONVICTION: would raise leverage to {risk.leverage}x on existing "
                    f"{state.position_side} position. Increasing leverage on an OPEN position requires "
                    f"closing and re-opening on Kraken Spot Margin - implement to your own risk "
                    f"tolerance before enabling this in live trading. Logging only for now."
                )
                state.leverage = risk.leverage

            elif action in (ActionType.TIGHTEN_STOP, ActionType.HOLD_LONG, ActionType.HOLD_SHORT):
                state.stop_price = self._compute_stop_price(state, decision.trailing_stop_pct, price)
                self.logger.info(f"Updated logical stop reference to {state.stop_price} "
                                  f"(trailing {decision.trailing_stop_pct}%). Implement EditOrder/"
                                  f"cancel-replace against Kraken's open conditional order if you "
                                  f"want this enforced exchange-side rather than just tracked locally.")

            elif action == ActionType.STAND_ASIDE:
                pass  # nothing to do

            state.last_action = action.value

        except KrakenAPIError as exc:
            self.logger.error(f"Kraken API error while executing {action.value}: {exc}")

        return state

    # ------------------------------------------------------------------ #
    def _open_position(self, side: str, volume: str, price: float,
                       leverage_str: str | None, trailing_stop_pct: float):
        order_side = "buy" if side == "LONG" else "sell"
        result = self.client.add_order(
            pair=self.pair, side=order_side, ordertype="market", volume=volume,
            leverage=leverage_str,
            close_ordertype="trailing-stop",
            close_price=f"-{trailing_stop_pct}%",
        )
        self.logger.info(f"OPEN {side} order result: {result}")

    def _close_position(self, state: BotState):
        if not state.position_side:
            return
        order_side = "sell" if state.position_side == "LONG" else "buy"
        # NOTE: volume "0" with reduce_only is not valid on Kraken - in a real
        # deployment, read the exact open volume from get_open_positions()
        # before closing. This is a known simplification - see README.
        positions = self.client.get_open_positions()
        total_vol = sum(float(p.get("vol", 0)) - float(p.get("vol_closed", 0))
                        for p in positions.values()) if positions else 0.0
        if total_vol <= 0:
            self.logger.warning("No open position volume found on Kraken to close - skipping.")
            return
        result = self.client.add_order(
            pair=self.pair, side=order_side, ordertype="market",
            volume=f"{total_vol:.8f}", reduce_only=True,
        )
        self.logger.info(f"CLOSE {state.position_side} order result: {result}")

    def _reduce_position(self, state: BotState, reduce_to_pct: float):
        positions = self.client.get_open_positions()
        total_vol = sum(float(p.get("vol", 0)) - float(p.get("vol_closed", 0))
                        for p in positions.values()) if positions else 0.0
        if total_vol <= 0:
            self.logger.warning("No open position volume found on Kraken to reduce - skipping.")
            return
        reduce_vol = total_vol * (1 - reduce_to_pct / 100.0)
        if reduce_vol <= 0:
            return
        order_side = "sell" if state.position_side == "LONG" else "buy"
        result = self.client.add_order(
            pair=self.pair, side=order_side, ordertype="market",
            volume=f"{reduce_vol:.8f}", reduce_only=True,
        )
        self.logger.info(f"REDUCE {state.position_side} by {100 - reduce_to_pct:.0f}% order result: {result}")

    def _compute_stop_price(self, state: BotState, trailing_pct: float, current_price: float) -> float:
        if state.position_side == "LONG":
            return round(current_price * (1 - trailing_pct / 100.0), 2)
        if state.position_side == "SHORT":
            return round(current_price * (1 + trailing_pct / 100.0), 2)
        return current_price
