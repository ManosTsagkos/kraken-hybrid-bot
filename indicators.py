"""
indicators.py
-------------
Pure functions for the 4H technical substrate:
  - EMA Trend Matrix (9 / 21 / 55)
  - RSI Momentum Filter (14)
  - Rate of Change (10 bars) with simple "buyer exhaustion" detection

No network calls in this file - everything operates on a pandas DataFrame
of OHLC candles, so it can be unit-tested with synthetic data.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


def kraken_ohlc_to_dataframe(ohlc_result: dict, pair_key: str) -> pd.DataFrame:
    """
    Converts Kraken's raw OHLC result (list of
    [time, open, high, low, close, vwap, volume, count]) into a DataFrame.
    Drops the final row, which is the current/still-forming candle.
    """
    raw = ohlc_result[pair_key]
    df = pd.DataFrame(raw, columns=["time", "open", "high", "low", "close",
                                     "vwap", "volume", "count"])
    for col in ("open", "high", "low", "close", "vwap", "volume"):
        df[col] = df[col].astype(float)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df.set_index("time")
    if len(df) > 1:
        df = df.iloc[:-1]  # drop the not-yet-closed candle
    return df


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    # Wilder's smoothing (standard RSI definition)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    out = out.fillna(100)  # if avg_loss is 0, RSI -> 100
    return out


def roc(series: pd.Series, period: int = 10) -> pd.Series:
    return (series - series.shift(period)) / series.shift(period) * 100


@dataclass
class TechnicalSnapshot:
    trend: str              # "LONG", "SHORT", or "NEUTRAL"
    close: float
    ema_fast: float
    ema_mid: float
    ema_slow: float
    rsi: float
    roc: float
    roc_prev: float
    buyer_exhaustion: bool
    seller_exhaustion: bool
    reasons: list[str]


def compute_technical_snapshot(df: pd.DataFrame, ema_fast: int = 9, ema_mid: int = 21,
                                ema_slow: int = 55, rsi_period: int = 14,
                                rsi_bull_threshold: float = 60, rsi_bear_threshold: float = 40,
                                roc_period: int = 10,
                                roc_exhaustion_lookback: int = 3) -> TechnicalSnapshot:
    """
    Implements:
      - EMA Trend Matrix: LONG needs close > EMA_fast > EMA_mid > EMA_slow (and inverse for SHORT)
      - RSI Momentum Filter: confirm LONG only if RSI > rsi_bull_threshold,
                              confirm SHORT only if RSI < rsi_bear_threshold
      - ROC exhaustion: if price is rising but ROC is falling vs N bars ago, flag
        "buyer exhaustion" (and the mirror for sellers) - the bot should stand aside.
    """
    if len(df) < max(ema_slow, rsi_period, roc_period) + roc_exhaustion_lookback + 1:
        raise ValueError(
            f"Not enough candles ({len(df)}) to compute indicators reliably. "
            f"Need at least {ema_slow + roc_exhaustion_lookback + 1}."
        )

    close = df["close"]
    ema_f = ema(close, ema_fast)
    ema_m = ema(close, ema_mid)
    ema_s = ema(close, ema_slow)
    rsi_series = rsi(close, rsi_period)
    roc_series = roc(close, roc_period)

    last = -1
    prev = -1 - roc_exhaustion_lookback

    c = float(close.iloc[last])
    f = float(ema_f.iloc[last])
    m = float(ema_m.iloc[last])
    s = float(ema_s.iloc[last])
    r = float(rsi_series.iloc[last])
    roc_now = float(roc_series.iloc[last])
    roc_then = float(roc_series.iloc[prev])

    reasons = []
    ema_long_aligned = c > f > m > s
    ema_short_aligned = c < f < m < s

    price_rising = c > float(close.iloc[prev])
    price_falling = c < float(close.iloc[prev])
    buyer_exhaustion = price_rising and roc_now < roc_then
    seller_exhaustion = price_falling and roc_now > roc_then

    trend = "NEUTRAL"
    if ema_long_aligned:
        reasons.append(f"EMA aligned bullish (close {c:.2f} > EMA9 {f:.2f} > EMA21 {m:.2f} > EMA55 {s:.2f})")
        if r > rsi_bull_threshold:
            reasons.append(f"RSI {r:.1f} > {rsi_bull_threshold} confirms bullish momentum")
            if buyer_exhaustion:
                reasons.append(f"ROC exhaustion detected (now {roc_now:.2f} < {roc_exhaustion_lookback} bars ago {roc_then:.2f}) -> stand aside")
                trend = "NEUTRAL"
            else:
                trend = "LONG"
        else:
            reasons.append(f"RSI {r:.1f} does not confirm (need > {rsi_bull_threshold})")
    elif ema_short_aligned:
        reasons.append(f"EMA aligned bearish (close {c:.2f} < EMA9 {f:.2f} < EMA21 {m:.2f} < EMA55 {s:.2f})")
        if r < rsi_bear_threshold:
            reasons.append(f"RSI {r:.1f} < {rsi_bear_threshold} confirms bearish momentum")
            if seller_exhaustion:
                reasons.append(f"ROC exhaustion detected (now {roc_now:.2f} > {roc_exhaustion_lookback} bars ago {roc_then:.2f}) -> stand aside")
                trend = "NEUTRAL"
            else:
                trend = "SHORT"
        else:
            reasons.append(f"RSI {r:.1f} does not confirm (need < {rsi_bear_threshold})")
    else:
        reasons.append("EMAs not aligned (9/21/55 not in clean order) -> NEUTRAL")

    return TechnicalSnapshot(
        trend=trend, close=c, ema_fast=f, ema_mid=m, ema_slow=s, rsi=r,
        roc=roc_now, roc_prev=roc_then,
        buyer_exhaustion=buyer_exhaustion, seller_exhaustion=seller_exhaustion,
        reasons=reasons,
    )
