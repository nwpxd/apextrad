"""
Arbitrage Strategy - Detect price discrepancies across exchanges/pairs.
Mostly statistical arb and triangular arb signals for crypto.
"""

import logging
from config.settings import Settings

log = logging.getLogger("Arbitrage")


class ArbitrageStrategy:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def signal(self, symbol: str, ticker: dict) -> dict:
        """
        Returns arbitrage signal. In a full build this would:
        - Compare prices across Binance, Coinbase, Kraken etc.
        - Detect triangular arb (BTC→ETH→USDT→BTC)
        - Detect funding rate arb on futures
        
        Here we use spread analysis as a proxy signal.
        """
        try:
            bid = ticker.get("bid", 0)
            ask = ticker.get("ask", 0)

            if bid == 0 or ask == 0:
                return self._neutral()

            spread_pct = (ask - bid) / bid

            # Tight spread = liquid = arb less likely but also less slippage
            # Wide spread = possible inefficiency or low liquidity (risky)
            score = 0.0
            confidence = 0.0
            reason = ""

            if spread_pct < 0.001:
                # Very liquid, good for any strategy
                score = 0.1
                confidence = 0.3
                reason = f"tight spread ({spread_pct:.4%})"
            elif spread_pct > 0.02:
                # Wide spread - penalize, hard to profit after fees
                score = -0.2
                confidence = 0.4
                reason = f"wide spread ({spread_pct:.4%}), avoid"
            else:
                score = 0.0
                confidence = 0.1
                reason = f"normal spread ({spread_pct:.4%})"

            return {
                "score": score,
                "confidence": confidence,
                "reason": reason,
                "strategy": "arbitrage",
                "spread_pct": spread_pct,
            }

        except Exception as e:
            log.debug(f"Arbitrage signal error {symbol}: {e}")
            return self._neutral()

    def _neutral(self) -> dict:
        return {"score": 0.0, "confidence": 0.0, "reason": "no data", "strategy": "arbitrage"}
