"""
MetaAI - The master decision engine.
Combines signals from all strategies, detects market regime,
and makes the final BUY / SELL / HOLD call.
"""

import logging
import os
from typing import Dict
from config.settings import Settings

log = logging.getLogger("MetaAI")

BUY_THRESHOLD = 0.09
SELL_THRESHOLD = -0.09
MEMECOIN_SENTIMENT_MIN = 0.10

# Detect if we have AI sentiment available
_HAS_AI_SENTIMENT = bool(
    os.getenv("ANTHROPIC_API_KEY") or os.getenv("OPENAI_API_KEY")
)


class MetaAI:
    def __init__(self, settings: Settings):
        self.settings = settings

        if _HAS_AI_SENTIMENT:
            # Full weights with sentiment
            self.weights = {
                "momentum": settings.momentum_weight,
                "mean_reversion": settings.mean_reversion_weight,
                "sentiment": settings.sentiment_weight,
                "arbitrage": settings.arbitrage_weight,
            }
            log.info("MetaAI: using 4 strategies (incl. AI sentiment)")
        else:
            # Redistribute sentiment weight to momentum + mean reversion
            extra = settings.sentiment_weight
            self.weights = {
                "momentum": settings.momentum_weight + extra * 0.6,
                "mean_reversion": settings.mean_reversion_weight + extra * 0.4,
                "sentiment": 0.0,   # excluded
                "arbitrage": settings.arbitrage_weight,
            }
            log.info(
                "MetaAI: no AI sentiment key - running 3-strategy mode "
                f"(momentum={self.weights['momentum']:.2f} "
                f"mean_rev={self.weights['mean_reversion']:.2f} "
                f"arb={self.weights['arbitrage']:.2f})"
            )

        self._scan_count = 0

    async def decide(self, symbol: str, signals: Dict[str, dict], portfolio, ticker: dict) -> dict:
        is_memecoin = self._is_memecoin(symbol)
        regime = self._detect_regime(signals)
        composite, weighted_confidence = self._composite_score(signals, regime, is_memecoin)
        has_position = symbol in portfolio.positions

        reason_parts = [f"regime={regime}", f"score={composite:.3f}"]

        # Log a sample every 20 evaluations so user can see scores
        self._scan_count += 1
        if self._scan_count % 20 == 0:
            log.info(
                f"[SIGNAL SAMPLE] {symbol} | score={composite:.3f} "
                f"(need >{BUY_THRESHOLD}) | "
                f"mom={signals.get('momentum',{}).get('score',0):.2f} "
                f"mr={signals.get('mean_reversion',{}).get('score',0):.2f} | "
                f"regime={regime}"
            )

        # Memecoins need sentiment - skip entirely if no AI key
        if is_memecoin:
            if not _HAS_AI_SENTIMENT:
                return self._hold(reason_parts + ["memecoin: no sentiment AI, skip"], regime, composite)
            sentiment_score = signals.get("sentiment", {}).get("score", 0)
            if abs(sentiment_score) < MEMECOIN_SENTIMENT_MIN:
                return self._hold(reason_parts + ["memecoin: weak sentiment"], regime, composite)

        # SELL logic
        if has_position:
            if composite < SELL_THRESHOLD:
                reason_parts.append("signal bearish")
                return {"action": "SELL", "confidence": weighted_confidence, "reason": " | ".join(reason_parts), "regime": regime, "score": composite}
            return self._hold(reason_parts, regime, composite)

        # BUY logic
        if composite > BUY_THRESHOLD:
            arb_score = signals.get("arbitrage", {}).get("score", 0)
            if arb_score < -0.15:
                return self._hold(reason_parts + ["wide spread"], regime, composite)
            reason_parts.append(f"conf={weighted_confidence:.2f}")
            log.info(f"[BUY SIGNAL] {symbol} | {' | '.join(reason_parts)}")
            return {"action": "BUY", "confidence": weighted_confidence, "reason": " | ".join(reason_parts), "regime": regime, "score": composite}

        return self._hold(reason_parts, regime, composite)

    def _composite_score(self, signals: dict, regime: str, is_memecoin: bool):
        weights = dict(self.weights)

        # Skip sentiment entirely if no AI key
        if not _HAS_AI_SENTIMENT:
            weights["sentiment"] = 0.0

        if regime == "trending":
            weights["momentum"] *= 1.4
            weights["mean_reversion"] *= 0.5
        elif regime == "ranging":
            weights["mean_reversion"] *= 1.5
            weights["momentum"] *= 0.6
        elif regime == "volatile":
            weights["momentum"] *= 0.7
            weights["mean_reversion"] *= 0.8

        if is_memecoin and _HAS_AI_SENTIMENT:
            weights["sentiment"] *= 2.0

        total_weight = sum(w for w in weights.values() if w > 0)
        if total_weight == 0:
            return 0.0, 0.0

        composite = 0.0
        confidence_sum = 0.0
        for strategy, sig in signals.items():
            if sig is None:
                continue
            w = weights.get(strategy, 0.0)
            if w <= 0:
                continue
            w_norm = w / total_weight
            score = sig.get("score", 0.0)
            if score != score:  # NaN check
                continue
            composite += w_norm * score
            confidence_sum += w_norm * sig.get("confidence", 0.0)

        return composite, confidence_sum

    def _detect_regime(self, signals: dict) -> str:
        mr = signals.get("mean_reversion", {}) or {}
        mom = signals.get("momentum", {}) or {}
        mr_z = abs(mr.get("z_score", 0))
        mom_score = abs(mom.get("score", 0))
        mom_conf = mom.get("confidence", 0)
        if mr_z > 2.5:
            return "volatile"
        if mom_score > 0.4 and mom_conf > 0.4:
            return "trending"
        if mom_score < 0.2 and mr_z < 1.0:
            return "ranging"
        return "mixed"

    def _is_memecoin(self, symbol: str) -> bool:
        return symbol in self.settings.memecoin_pairs

    def _hold(self, reason_parts, regime="unknown", score=0.0) -> dict:
        return {"action": "HOLD", "confidence": 0.0, "reason": " | ".join(reason_parts), "regime": regime, "score": score}

    def update_weights(self, strategy: str, performance_delta: float):
        if strategy not in self.weights:
            return
        self.weights[strategy] = max(0.05, min(0.6, self.weights[strategy] + performance_delta * 0.01))
