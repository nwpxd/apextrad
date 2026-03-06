"""
Momentum Strategy - Trend following using RSI, MACD, EMA crossovers.
Returns signal strength -1.0 (strong sell) to +1.0 (strong buy).
"""

import logging
import pandas as pd
import numpy as np
from typing import Optional
from config.settings import Settings

log = logging.getLogger("Momentum")


class MomentumStrategy:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def signal(self, symbol: str, ohlcv: pd.DataFrame, ticker: dict) -> dict:
        """
        Returns {"score": float, "confidence": float, "reason": str}
        score: -1.0 to +1.0
        """
        try:
            close = ohlcv["close"].astype(float)

            rsi = self._rsi(close, 14)
            macd_line, signal_line = self._macd(close)
            ema_fast = close.ewm(span=9).mean()
            ema_slow = close.ewm(span=21).mean()
            ema_200 = close.ewm(span=min(200, len(close))).mean()

            current_rsi = rsi.iloc[-1]
            macd_curr = macd_line.iloc[-1] - signal_line.iloc[-1]
            macd_prev = macd_line.iloc[-2] - signal_line.iloc[-2]
            above_200 = close.iloc[-1] > ema_200.iloc[-1]

            # Detect actual EMA crossovers (not just position)
            prev_ema_bull = ema_fast.iloc[-2] > ema_slow.iloc[-2]
            curr_ema_bull = ema_fast.iloc[-1] > ema_slow.iloc[-1]
            bullish_cross = curr_ema_bull and not prev_ema_bull
            bearish_cross = not curr_ema_bull and prev_ema_bull

            # Volume surge check
            avg_vol = ohlcv["volume"].astype(float).rolling(20).mean().iloc[-1]
            current_vol = ohlcv["volume"].astype(float).iloc[-1]
            volume_surge = current_vol > avg_vol * 1.5

            score = 0.0
            reasons = []

            # RSI signals
            if current_rsi < 30:
                score += 0.3
                reasons.append(f"RSI oversold ({current_rsi:.0f})")
            elif current_rsi > 70:
                score -= 0.3
                reasons.append(f"RSI overbought ({current_rsi:.0f})")

            # MACD crossover detection (actual cross, not just position)
            if macd_curr > 0 and macd_prev <= 0:
                score += 0.35
                reasons.append("MACD bullish cross")
            elif macd_curr < 0 and macd_prev >= 0:
                score -= 0.35
                reasons.append("MACD bearish cross")
            elif macd_curr > 0:
                score += 0.1
            else:
                score -= 0.1

            # EMA crossover (strong signal) vs just position (weak)
            if bullish_cross:
                score += 0.35
                reasons.append("EMA bullish crossover")
            elif bearish_cross:
                score -= 0.35
                reasons.append("EMA bearish crossover")
            elif curr_ema_bull:
                score += 0.1
            else:
                score -= 0.1

            # 200 EMA trend
            if above_200:
                score += 0.15
                reasons.append("above EMA200")
            else:
                score -= 0.15

            # Volume surge amplifies signal in both directions
            if volume_surge and abs(score) > 0.1:
                score = max(-1.0, min(1.0, score * 1.2))
                reasons.append("volume surge")

            score = max(-1.0, min(1.0, score))
            confidence = abs(score) * 0.8  # Momentum is fairly reliable

            return {
                "score": score,
                "confidence": confidence,
                "reason": ", ".join(reasons[:3]),
                "strategy": "momentum",
            }

        except Exception as e:
            log.debug(f"Momentum signal error for {symbol}: {e}")
            return {"score": 0.0, "confidence": 0.0, "reason": "error", "strategy": "momentum"}

    def _rsi(self, series: pd.Series, period: int = 14) -> pd.Series:
        delta = series.diff()
        gain = delta.clip(lower=0).rolling(period).mean()
        loss = (-delta.clip(upper=0)).rolling(period).mean()
        rs = gain / loss.replace(0, np.nan)
        return 100 - (100 / (1 + rs))

    def _macd(self, series: pd.Series, fast=12, slow=26, signal=9):
        ema_fast = series.ewm(span=fast).mean()
        ema_slow = series.ewm(span=slow).mean()
        macd = ema_fast - ema_slow
        signal_line = macd.ewm(span=signal).mean()
        return macd, signal_line
