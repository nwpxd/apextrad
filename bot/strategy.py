"""
Signal engine. EMA crossover + RSI + Volume.
Simple, proven, no magic.
"""

import numpy as np
import pandas as pd

# Thresholds
BUY_SCORE = 0.30
SELL_SCORE = -0.25


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> float:
    delta = series.diff().dropna()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean().iloc[-1]
    avg_loss = loss.rolling(period).mean().iloc[-1]
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def signal(df: pd.DataFrame) -> dict:
    """
    Returns {"action": BUY/SELL/HOLD, "score": float, "reason": str}
    """
    close = df["close"]
    vol = df["volume"]

    if len(close) < 50:
        return _hold("not enough data")

    # EMAs
    ema9 = ema(close, 9)
    ema21 = ema(close, 21)
    ema50 = ema(close, 50)

    # Current values
    e9, e21, e50 = ema9.iloc[-1], ema21.iloc[-1], ema50.iloc[-1]
    e9_prev, e21_prev = ema9.iloc[-2], ema21.iloc[-2]
    price = close.iloc[-1]

    # Trend filter: only long above EMA50
    uptrend = price > e50

    # EMA crossover score
    cross_bull = e9_prev <= e21_prev and e9 > e21
    cross_bear = e9_prev >= e21_prev and e9 < e21

    if cross_bull:
        cross_score = 0.40
    elif e9 > e21:
        cross_score = 0.15
    elif cross_bear:
        cross_score = -0.40
    else:
        cross_score = -0.15

    # RSI
    r = rsi(close)
    if r < 30:
        rsi_score = 0.30
    elif r < 40:
        rsi_score = 0.15
    elif r > 70:
        rsi_score = -0.30
    elif r > 60:
        rsi_score = -0.15
    else:
        rsi_score = 0.0

    # Volume multiplier
    avg_vol = vol.rolling(20).mean().iloc[-1]
    vol_ratio = vol.iloc[-1] / avg_vol if avg_vol > 0 else 1.0
    vol_mult = min(1.5, max(0.5, 0.5 + vol_ratio * 0.5))

    # Final score
    score = (cross_score + rsi_score) * vol_mult

    # Block buys in downtrend
    if not uptrend:
        score = min(score, 0.0)

    # Build reason
    parts = []
    if cross_bull:
        parts.append("EMA cross UP")
    elif cross_bear:
        parts.append("EMA cross DOWN")
    parts.append(f"RSI={r:.0f}")
    parts.append(f"vol={vol_ratio:.1f}x")
    if not uptrend:
        parts.append("downtrend")
    reason = " | ".join(parts)

    if score >= BUY_SCORE:
        return {"action": "BUY", "score": score, "reason": reason}
    elif score <= SELL_SCORE:
        return {"action": "SELL", "score": score, "reason": reason}
    return {"action": "HOLD", "score": score, "reason": reason}


def _hold(reason: str) -> dict:
    return {"action": "HOLD", "score": 0.0, "reason": reason}
