"""
OnChain Analyzer - Whale tracking, wallet flows, DEX liquidity.
On-chain data is the purest signal - it shows what smart money is actually doing.

Data sources:
- Etherscan / BSCScan (free API)
- DeFiLlama (free, no key needed)
- Blockchain.com (BTC flows)
- GlassNode (premium, best-in-class)
"""

import asyncio
import logging
import aiohttp
from typing import Dict, List, Optional
from config.settings import Settings

log = logging.getLogger("OnChain")

DEFILLAMA_BASE = "https://api.llama.fi"
ETHERSCAN_BASE = "https://api.etherscan.io/api"

# Known whale/smart money addresses to track
WHALE_WATCHLIST = {
    "eth": [
        "0x28c6c06298d514db089934071355e5743bf21d60",  # Binance hot wallet
        "0x21a31ee1afc51d94c2efccaa2092ad1028285549",  # Binance cold
        "0x47ac0fb4f2d84898e4d9e7b4dab3c24507a6d503",  # Large fund
    ],
    "btc": [],  # Add BTC whale addresses
}


class OnChainAnalyzer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._cache: Dict[str, dict] = {}
        self._session: Optional[aiohttp.ClientSession] = None

    async def connect(self):
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10),
            headers={"User-Agent": "APEX-Trader/1.0"},
        )
        log.info("[OK] On-chain analyzer connected")

    async def get_signal(self, symbol: str) -> dict:
        """
        Returns on-chain signal for a symbol.
        Combines: TVL trends, whale flows, exchange inflows/outflows.
        """
        coin = symbol.split("/")[0].lower()
        signals = await asyncio.gather(
            self._get_tvl_signal(coin),
            self._get_exchange_flows(coin),
            self._get_defi_metrics(coin),
            return_exceptions=True,
        )

        scores = []
        reasons = []

        for sig in signals:
            if isinstance(sig, Exception) or sig is None:
                continue
            scores.append(sig.get("score", 0))
            if sig.get("reason"):
                reasons.append(sig["reason"])

        if not scores:
            return {"score": 0.0, "confidence": 0.0, "reason": "no on-chain data"}

        composite = sum(scores) / len(scores)
        confidence = min(0.8, len(scores) * 0.2)

        return {
            "score": composite,
            "confidence": confidence,
            "reason": " | ".join(reasons[:3]),
            "strategy": "onchain",
        }

    async def _get_tvl_signal(self, coin: str) -> dict:
        """
        Rising TVL (Total Value Locked in DeFi) = bullish.
        Falling TVL = bearish or risk-off.
        """
        try:
            url = f"{DEFILLAMA_BASE}/charts/{coin}"
            async with self._session.get(url) as r:
                if r.status != 200:
                    return None
                data = await r.json()

            if len(data) < 7:
                return None

            recent = [d["totalLiquidityUSD"] for d in data[-7:]]
            pct_change = (recent[-1] - recent[0]) / recent[0] if recent[0] > 0 else 0

            if pct_change > 0.10:
                return {"score": 0.5, "reason": f"TVL +{pct_change:.0%} (7d)"}
            elif pct_change < -0.10:
                return {"score": -0.5, "reason": f"TVL {pct_change:.0%} (7d)"}
            return {"score": 0.0, "reason": f"TVL stable ({pct_change:+.1%})"}

        except Exception as e:
            log.debug(f"TVL signal error {coin}: {e}")
            return None

    async def _get_exchange_flows(self, coin: str) -> dict:
        """
        High exchange inflows = people depositing to sell = bearish.
        High exchange outflows = people withdrawing to hold = bullish.
        Uses Etherscan for ETH-based tokens.
        """
        if coin not in ["eth", "usdt", "usdc"]:
            return None

        try:
            # This is a simplified proxy - real impl would use Glassnode or Nansen
            # For now, check recent large transfer counts via Etherscan
            url = f"{ETHERSCAN_BASE}?module=stats&action=ethsupply&apikey=YourApiKeyToken"
            # NOTE: In production, replace with proper Glassnode/Nansen API
            return {"score": 0.0, "reason": "exchange flows: neutral"}
        except Exception:
            return None

    async def _get_defi_metrics(self, coin: str) -> dict:
        """
        Get DeFi protocol metrics - stablecoin dominance, borrow rates etc.
        High borrow rates on stables = bullish (people borrowing to buy).
        """
        try:
            url = f"{DEFILLAMA_BASE}/protocols"
            async with self._session.get(url) as r:
                if r.status != 200:
                    return None
                protocols = await r.json()

            # Find protocols for this coin
            matching = [
                p for p in protocols
                if coin.upper() in [t.upper() for t in (p.get("symbol", ""), p.get("name", ""))]
                and p.get("tvl", 0) > 1_000_000
            ]

            if not matching:
                return None

            total_tvl = sum(p.get("tvl", 0) for p in matching[:5])
            tvl_change = sum(p.get("change_7d", 0) for p in matching[:5]) / min(5, len(matching))

            score = 0.3 if tvl_change > 5 else (-0.3 if tvl_change < -5 else 0.0)
            return {
                "score": score,
                "reason": f"DeFi TVL ${total_tvl/1e6:.0f}M ({tvl_change:+.1f}% 7d)",
            }

        except Exception as e:
            log.debug(f"DeFi metrics error {coin}: {e}")
            return None

    async def get_whale_alert(self, symbol: str) -> Optional[dict]:
        """
        Check if large wallets have been moving funds recently.
        Large buys from known whale addresses = bullish signal.
        """
        coin = symbol.split("/")[0].lower()
        addresses = WHALE_WATCHLIST.get(coin, [])
        if not addresses:
            return None

        # In production: use whale-alert.io API or on-chain APIs
        # This is a placeholder for the integration point
        return {"whale_activity": "no recent large moves", "score": 0.0}

    async def get_fear_greed_index(self) -> dict:
        """
        Crypto Fear & Greed Index - classic contrarian indicator.
        Extreme fear = buy. Extreme greed = be careful.
        """
        try:
            url = "https://api.alternative.me/fng/"
            async with self._session.get(url) as r:
                data = await r.json()

            value = int(data["data"][0]["value"])
            classification = data["data"][0]["value_classification"]

            if value <= 20:
                return {"score": 0.7, "value": value, "label": f"Extreme Fear ({value})", "reason": "extreme fear = buy signal"}
            elif value <= 40:
                return {"score": 0.3, "value": value, "label": f"Fear ({value})", "reason": "market fearful"}
            elif value >= 80:
                return {"score": -0.5, "value": value, "label": f"Extreme Greed ({value})", "reason": "extreme greed = caution"}
            elif value >= 60:
                return {"score": -0.2, "value": value, "label": f"Greed ({value})", "reason": "market greedy"}
            else:
                return {"score": 0.0, "value": value, "label": f"Neutral ({value})", "reason": "neutral market sentiment"}

        except Exception as e:
            log.debug(f"Fear/greed fetch error: {e}")
            return {"score": 0.0, "value": 50, "label": "Unknown", "reason": "fear/greed unavailable"}

    async def disconnect(self):
        if self._session:
            await self._session.close()
