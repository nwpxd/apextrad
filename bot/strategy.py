"""
Signal engine.
EMA crossover (9/21) + trend filter (EMA50) + RSI(14) + MACD + Volume.
ATR for dynamic stop/take-profit levels.
"""

import numpy as np
import pandas as pd

BUY_SCORE = 0.35
SELL_SCORE = -0.30


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


def macd(series: pd.Series) -> tuple[float, float, float]:
    """Returns (macd_line, signal_line, histogram) current values."""
    fast = ema(series, 12)
    slow = ema(series, 26)
    macd_line = fast - slow
    signal_line = ema(macd_line, 9)
    hist = macd_line - signal_line
    return float(macd_line.iloc[-1]), float(signal_line.iloc[-1]), float(hist.iloc[-1])


def atr(df: pd.DataFrame, period: int = 14) -> float:
    """Average True Range - measures volatility."""
    high, low, close = df["high"], df["low"], df["close"]
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])


def signal(df: pd.DataFrame) -> dict:
    """
    Returns {action, score, reason, atr} with ATR for dynamic stops.
    """
    close = df["close"]
    vol = df["volume"]

    if len(close) < 50:
        return _hold("not enough data", 0)

    current_atr = atr(df)
    price = float(close.iloc[-1])

    # EMAs
    ema9 = ema(close, 9)
    ema21 = ema(close, 21)
    ema50 = ema(close, 50)

    e9, e21, e50 = ema9.iloc[-1], ema21.iloc[-1], ema50.iloc[-1]
    e9_prev, e21_prev = ema9.iloc[-2], ema21.iloc[-2]

    # ── Trend filter ──
    uptrend = price > e50
    strong_uptrend = e9 > e21 > e50

    # ── EMA crossover ── (0.40 max)
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

    # ── RSI ── (0.25 max)
    r = rsi(close)
    if r < 30:
        rsi_score = 0.25
    elif r < 40:
        rsi_score = 0.12
    elif r > 70:
        rsi_score = -0.25
    elif r > 60:
        rsi_score = -0.12
    else:
        rsi_score = 0.0

    # ── MACD ── (0.20 max)
    _, _, hist = macd(close)
    hist_prev = float((ema(close, 12) - ema(close, 26)).iloc[-2] - ema(ema(close, 12) - ema(close, 26), 9).iloc[-2])

    if hist > 0 and hist > hist_prev:
        macd_score = 0.20  # Positive and rising
    elif hist > 0:
        macd_score = 0.08  # Positive but fading
    elif hist < 0 and hist < hist_prev:
        macd_score = -0.20  # Negative and falling
    elif hist < 0:
        macd_score = -0.08  # Negative but recovering
    else:
        macd_score = 0.0

    # ── Volume ── (multiplier 0.6 - 1.4)
    avg_vol = vol.rolling(20).mean().iloc[-1]
    vol_ratio = vol.iloc[-1] / avg_vol if avg_vol > 0 else 1.0
    vol_mult = min(1.4, max(0.6, 0.4 + vol_ratio * 0.5))

    # ── Composite score ──
    raw_score = cross_score + rsi_score + macd_score
    score = raw_score * vol_mult

    # Bonus for strong alignment
    if strong_uptrend and score > 0:
        score *= 1.15

    # Block buys in downtrend
    if not uptrend:
        score = min(score, 0.0)

    # ── Build reason ──
    parts = []
    if cross_bull:
        parts.append("EMA cross UP")
    elif cross_bear:
        parts.append("EMA cross DOWN")
    elif e9 > e21:
        parts.append("EMA bullish")
    else:
        parts.append("EMA bearish")

    parts.append(f"RSI={r:.0f}")
    parts.append(f"MACD={'up' if hist > 0 else 'dn'}")
    parts.append(f"vol={vol_ratio:.1f}x")

    if strong_uptrend:
        parts.append("trend strong")
    elif not uptrend:
        parts.append("downtrend")

    reason = " | ".join(parts)

    if score >= BUY_SCORE:
        return {"action": "BUY", "score": score, "reason": reason, "atr": current_atr}
    elif score <= SELL_SCORE:
        return {"action": "SELL", "score": score, "reason": reason, "atr": current_atr}
    return {"action": "HOLD", "score": score, "reason": reason, "atr": current_atr}


def _hold(reason: str, atr_val: float) -> dict:
    return {"action": "HOLD", "score": 0.0, "reason": reason, "atr": atr_val}
