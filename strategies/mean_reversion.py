"""
Mean Reversion Strategy - Bollinger Bands, Z-score, statistical edges.
Best for ranging markets. Works great as a counter-signal to momentum.
"""

import logging
import pandas as pd
import numpy as np
from config.settings import Settings

log = logging.getLogger("MeanReversion")


class MeanReversionStrategy:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def signal(self, symbol: str, ohlcv: pd.DataFrame, ticker: dict) -> dict:
        try:
            close = ohlcv["close"].astype(float)

            # Bollinger Bands (20-period, 2 std)
            sma20 = close.rolling(20).mean()
            std20 = close.rolling(20).std()
            upper_band = sma20 + 2 * std20
            lower_band = sma20 - 2 * std20

            current = close.iloc[-1]
            upper = upper_band.iloc[-1]
            lower = lower_band.iloc[-1]
            mid = sma20.iloc[-1]

            # Z-score vs 20-period mean
            z_score = (current - mid) / std20.iloc[-1] if std20.iloc[-1] > 0 else 0

            # Bandwidth (how volatile is the range?)
            bandwidth = (upper - lower) / mid if mid > 0 else 0

            # Percent B: where within the band is price?
            pct_b = (current - lower) / (upper - lower) if (upper - lower) > 0 else 0.5

            score = 0.0
            reasons = []

            # Strong reversion signals
            if pct_b < 0.05:  # Near/below lower band
                score += 0.8
                reasons.append(f"at lower BB (B%={pct_b:.2f})")
            elif pct_b < 0.2:
                score += 0.4
                reasons.append(f"near lower BB")
            elif pct_b > 0.95:  # Near/above upper band
                score -= 0.8
                reasons.append(f"at upper BB (B%={pct_b:.2f})")
            elif pct_b > 0.8:
                score -= 0.4
                reasons.append(f"near upper BB")

            # Z-score reinforcement
            if z_score < -2:
                score += 0.2
                reasons.append(f"z={z_score:.1f} low")
            elif z_score > 2:
                score -= 0.2
                reasons.append(f"z={z_score:.1f} high")

            # Trend filter: if SMA20 is strongly trending, reduce reversion signal
            if len(sma20.dropna()) > 5:
                sma_now = sma20.iloc[-1]
                sma_5ago = sma20.iloc[-5]
                if sma_5ago > 0:
                    sma_slope = (sma_now - sma_5ago) / sma_5ago
                    if abs(sma_slope) > 0.02:  # >2% move in 5 bars = trending
                        score *= 0.3
                        reasons.append(f"trending ({sma_slope:.1%})")

            # Low bandwidth = good ranging environment for this strategy
            confidence_multiplier = 1.0 if bandwidth < 0.06 else 0.6
            confidence = abs(score) * 0.7 * confidence_multiplier

            # In very high bandwidth (breakout), reduce signal
            if bandwidth > 0.12:
                score *= 0.4
                confidence *= 0.5
                reasons.append("wide bands (trending?)")

            score = max(-1.0, min(1.0, score))

            return {
                "score": score,
                "confidence": confidence,
                "reason": ", ".join(reasons[:2]),
                "strategy": "mean_reversion",
                "z_score": z_score,
                "pct_b": pct_b,
            }

        except Exception as e:
            log.debug(f"MeanReversion error for {symbol}: {e}")
            return {"score": 0.0, "confidence": 0.0, "reason": "error", "strategy": "mean_reversion"}
