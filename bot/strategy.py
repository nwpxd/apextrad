"""
Signal engine with market regime detection.

Indicators:
  - EMA crossover (9/21) + trend filter (EMA50)
  - RSI(14) with divergence detection
  - MACD histogram momentum
  - ADX(14) for regime detection (trending vs ranging)
  - Bollinger Bands squeeze for breakout anticipation
  - Volume confirmation
  - Higher timeframe trend filter
"""

import numpy as np
import pandas as pd

# Scoring thresholds
BUY_SCORE = 0.35
SELL_SCORE = -0.30


# ── Indicator functions ──

def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period).mean()


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


def rsi_series(series: pd.Series, period: int = 14) -> pd.Series:
    """Full RSI series for divergence detection."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def macd(series: pd.Series) -> tuple[float, float, float]:
    fast = ema(series, 12)
    slow = ema(series, 26)
    macd_line = fast - slow
    signal_line = ema(macd_line, 9)
    hist = macd_line - signal_line
    return float(macd_line.iloc[-1]), float(signal_line.iloc[-1]), float(hist.iloc[-1])


def atr(df: pd.DataFrame, period: int = 14) -> float:
    high, low, close = df["high"], df["low"], df["close"]
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])


def adx(df: pd.DataFrame, period: int = 14) -> float:
    """Average Directional Index - measures trend strength."""
    high = df["high"]
    low = df["low"]
    close = df["close"]

    plus_dm = high.diff()
    minus_dm = -low.diff()

    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr_smooth = tr.rolling(period).mean()
    plus_di = 100 * (plus_dm.rolling(period).mean() / atr_smooth)
    minus_di = 100 * (minus_dm.rolling(period).mean() / atr_smooth)

    dx = abs(plus_di - minus_di) / (plus_di + minus_di) * 100
    dx = dx.replace([np.inf, -np.inf], 0).fillna(0)
    adx_val = dx.rolling(period).mean().iloc[-1]
    return float(adx_val) if not np.isnan(adx_val) else 0.0


def bollinger_bands(series: pd.Series, period: int = 20, std: float = 2.0) -> dict:
    """Returns BB values and squeeze detection."""
    mid = sma(series, period)
    rolling_std = series.rolling(period).std()
    upper = mid + rolling_std * std
    lower = mid - rolling_std * std

    bandwidth = (upper - lower) / mid
    bw_current = float(bandwidth.iloc[-1])
    bw_avg = float(bandwidth.rolling(50).mean().iloc[-1]) if len(bandwidth) >= 50 else bw_current

    price = float(series.iloc[-1])
    pct_b = (price - float(lower.iloc[-1])) / (float(upper.iloc[-1]) - float(lower.iloc[-1])) if float(upper.iloc[-1]) != float(lower.iloc[-1]) else 0.5

    squeeze = bw_current < bw_avg * 0.75

    return {
        "upper": float(upper.iloc[-1]),
        "lower": float(lower.iloc[-1]),
        "mid": float(mid.iloc[-1]),
        "pct_b": pct_b,
        "squeeze": squeeze,
        "bandwidth": bw_current,
    }


def detect_rsi_divergence(close: pd.Series, lookback: int = 14) -> str:
    """Detect bullish or bearish RSI divergence."""
    if len(close) < lookback * 3:
        return "none"

    rsi_vals = rsi_series(close)
    prices = close.iloc[-lookback * 2:]
    rsi_v = rsi_vals.iloc[-lookback * 2:]

    mid = len(prices) // 2

    price_low1 = prices.iloc[:mid].min()
    price_low2 = prices.iloc[mid:].min()
    rsi_low1 = rsi_v.iloc[:mid].min()
    rsi_low2 = rsi_v.iloc[mid:].min()

    # Bullish divergence: lower price low, higher RSI low
    if price_low2 < price_low1 and rsi_low2 > rsi_low1:
        return "bullish"

    price_high1 = prices.iloc[:mid].max()
    price_high2 = prices.iloc[mid:].max()
    rsi_high1 = rsi_v.iloc[:mid].max()
    rsi_high2 = rsi_v.iloc[mid:].max()

    # Bearish divergence: higher price high, lower RSI high
    if price_high2 > price_high1 and rsi_high2 < rsi_high1:
        return "bearish"

    return "none"


def detect_regime(adx_val: float, config: dict) -> str:
    """Classify market regime based on ADX."""
    if adx_val >= config.get("adx_trend_threshold", 25):
        return "trending"
    elif adx_val <= config.get("adx_range_threshold", 20):
        return "ranging"
    return "transition"


def htf_trend(df_htf: pd.DataFrame | None) -> str:
    """Determine higher timeframe trend direction."""
    if df_htf is None or len(df_htf) < 50:
        return "neutral"

    close = df_htf["close"]
    e21 = ema(close, 21)
    e50 = ema(close, 50)

    if float(e21.iloc[-1]) > float(e50.iloc[-1]) and float(close.iloc[-1]) > float(e50.iloc[-1]):
        return "bullish"
    elif float(e21.iloc[-1]) < float(e50.iloc[-1]) and float(close.iloc[-1]) < float(e50.iloc[-1]):
        return "bearish"
    return "neutral"


# ── Main signal function ──

def signal(df: pd.DataFrame, config: dict | None = None, df_htf: pd.DataFrame | None = None) -> dict:
    """
    Generate trading signal with regime awareness.
    Returns {action, score, reason, atr, regime, htf_trend}.
    """
    if config is None:
        config = {}

    close = df["close"]
    vol = df["volume"]

    if len(close) < 50:
        return _hold("not enough data", 0, "unknown", "neutral")

    current_atr = atr(df)
    price = float(close.iloc[-1])

    # ── Market regime ──
    adx_val = adx(df, config.get("adx_period", 14))
    regime = detect_regime(adx_val, config)

    # ── Higher timeframe trend ──
    htf = htf_trend(df_htf)

    # ── Bollinger Bands ──
    bb = bollinger_bands(
        close,
        config.get("bb_period", 20),
        config.get("bb_std", 2.0),
    )

    # ── EMAs ──
    ema9 = ema(close, 9)
    ema21 = ema(close, 21)
    ema50 = ema(close, 50)

    e9, e21_v, e50_v = ema9.iloc[-1], ema21.iloc[-1], ema50.iloc[-1]
    e9_prev, e21_prev = ema9.iloc[-2], ema21.iloc[-2]

    uptrend = price > e50_v
    strong_uptrend = e9 > e21_v > e50_v

    # ── EMA crossover ── (0.40 max)
    cross_bull = e9_prev <= e21_prev and e9 > e21_v
    cross_bear = e9_prev >= e21_prev and e9 < e21_v

    if cross_bull:
        cross_score = 0.40
    elif e9 > e21_v:
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

    # RSI divergence bonus
    divergence = detect_rsi_divergence(close)
    if divergence == "bullish":
        rsi_score += 0.10
    elif divergence == "bearish":
        rsi_score -= 0.10

    # ── MACD ── (0.20 max)
    _, _, hist = macd(close)
    macd_line = ema(close, 12) - ema(close, 26)
    hist_prev = float(macd_line.iloc[-2] - ema(macd_line, 9).iloc[-2])

    if hist > 0 and hist > hist_prev:
        macd_score = 0.20
    elif hist > 0:
        macd_score = 0.08
    elif hist < 0 and hist < hist_prev:
        macd_score = -0.20
    elif hist < 0:
        macd_score = -0.08
    else:
        macd_score = 0.0

    # ── Volume ── (multiplier 0.6 - 1.4)
    avg_vol = vol.rolling(20).mean().iloc[-1]
    vol_ratio = vol.iloc[-1] / avg_vol if avg_vol > 0 else 1.0
    vol_mult = min(1.4, max(0.6, 0.4 + vol_ratio * 0.5))

    # ── Bollinger Bands bonus ── (0.15 max)
    bb_score = 0.0
    if bb["squeeze"]:
        # Squeeze = volatility compression, breakout incoming
        if cross_bull or (e9 > e21_v and hist > 0):
            bb_score = 0.15  # expect upside breakout
        elif cross_bear or (e9 < e21_v and hist < 0):
            bb_score = -0.15  # expect downside breakout
    elif bb["pct_b"] < 0.05:
        bb_score = 0.10  # price touching lower band (potential bounce)
    elif bb["pct_b"] > 0.95:
        bb_score = -0.10  # price touching upper band (potential reversal)

    # ── Composite score ──
    raw_score = cross_score + rsi_score + macd_score + bb_score
    score = raw_score * vol_mult

    # ── Regime-based adjustments ──
    if regime == "ranging":
        # In range: reduce trend signals, favor mean-reversion
        if abs(score) < 0.5:
            score *= 0.6  # weaken marginal signals
        # But allow strong mean-reversion signals (RSI extremes + BB touch)
        if r < 25 and bb["pct_b"] < 0.1:
            score = max(score, 0.35)  # force buy near lower BB in range
        elif r > 75 and bb["pct_b"] > 0.9:
            score = min(score, -0.30)  # force sell near upper BB in range

    elif regime == "trending":
        # In trend: boost aligned signals
        if strong_uptrend and score > 0:
            score *= 1.20

    # ── Higher timeframe filter ──
    if htf == "bearish" and score > 0:
        score *= 0.5  # halve buy signals against HTF trend
    elif htf == "bullish" and score < 0:
        score *= 0.5  # halve sell signals against HTF trend
    elif htf == "bullish" and score > 0:
        score *= 1.10  # slight boost when aligned with HTF

    # Block buys in downtrend (unless strong mean-reversion in range)
    if not uptrend and regime != "ranging":
        score = min(score, 0.0)

    # ── Build reason ──
    parts = []
    if cross_bull:
        parts.append("EMA cross UP")
    elif cross_bear:
        parts.append("EMA cross DOWN")
    elif e9 > e21_v:
        parts.append("EMA bullish")
    else:
        parts.append("EMA bearish")

    parts.append(f"RSI={r:.0f}")
    if divergence != "none":
        parts.append(f"div={divergence}")
    parts.append(f"MACD={'up' if hist > 0 else 'dn'}")
    parts.append(f"ADX={adx_val:.0f}")
    parts.append(f"regime={regime}")
    parts.append(f"vol={vol_ratio:.1f}x")

    if bb["squeeze"]:
        parts.append("BB squeeze")
    if htf != "neutral":
        parts.append(f"HTF={htf}")

    reason = " | ".join(parts)

    if score >= BUY_SCORE:
        return {"action": "BUY", "score": score, "reason": reason, "atr": current_atr, "regime": regime, "htf_trend": htf}
    elif score <= SELL_SCORE:
        return {"action": "SELL", "score": score, "reason": reason, "atr": current_atr, "regime": regime, "htf_trend": htf}
    return {"action": "HOLD", "score": score, "reason": reason, "atr": current_atr, "regime": regime, "htf_trend": htf}


def _hold(reason: str, atr_val: float, regime: str = "unknown", htf: str = "neutral") -> dict:
    return {"action": "HOLD", "score": 0.0, "reason": reason, "atr": atr_val, "regime": regime, "htf_trend": htf}
