"""
Order Book Analyzer - Real-time order book depth analysis.
Order book gives you the clearest picture of immediate supply/demand.

Key signals:
- Bid/ask imbalance → directional pressure
- Large walls → potential support/resistance
- Spoofing detection → fake walls that disappear
- Market depth → liquidity for execution sizing
"""

import logging
import asyncio
from typing import Optional, Dict, List
from config.settings import Settings

log = logging.getLogger("OrderBook")


class OrderBookAnalyzer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._exchange = None
        self._cache: Dict[str, dict] = {}

    async def connect(self, exchange):
        """Accepts an existing ccxt exchange instance."""
        self._exchange = exchange

    async def get_signal(self, symbol: str) -> dict:
        """
        Analyze order book and return directional signal.
        """
        book = await self._fetch_book(symbol)
        if not book:
            return {"score": 0.0, "confidence": 0.0, "reason": "no book data", "strategy": "orderbook"}

        bids = book.get("bids", [])  # [[price, qty], ...]
        asks = book.get("asks", [])

        if not bids or not asks:
            return {"score": 0.0, "confidence": 0.0, "reason": "empty book", "strategy": "orderbook"}

        metrics = self._compute_metrics(bids, asks)
        signal = self._interpret_metrics(metrics)
        return signal

    def _compute_metrics(self, bids: List, asks: List) -> dict:
        # Top 10 levels for imbalance
        top_bid_vol = sum(b[1] for b in bids[:10])
        top_ask_vol = sum(a[1] for a in asks[:10])
        total_vol = top_bid_vol + top_ask_vol

        # Bid-ask imbalance: +1 = all bids, -1 = all asks
        imbalance = (top_bid_vol - top_ask_vol) / total_vol if total_vol > 0 else 0

        # Large wall detection (single level > 20% of top 10 total)
        bid_walls = [b for b in bids[:20] if b[1] > top_bid_vol * 0.2]
        ask_walls = [a for a in asks[:20] if a[1] > top_ask_vol * 0.2]

        # Spread as % of mid price
        mid_price = (bids[0][0] + asks[0][0]) / 2
        spread_pct = (asks[0][0] - bids[0][0]) / mid_price if mid_price > 0 else 0

        # Depth score: how much USD can we trade within 0.5% of mid?
        within_half_pct = mid_price * 0.005
        bid_depth = sum(b[1] * b[0] for b in bids if b[0] >= mid_price - within_half_pct)
        ask_depth = sum(a[1] * a[0] for a in asks if a[0] <= mid_price + within_half_pct)

        return {
            "imbalance": imbalance,
            "bid_walls": len(bid_walls),
            "ask_walls": len(ask_walls),
            "spread_pct": spread_pct,
            "bid_depth_usd": bid_depth,
            "ask_depth_usd": ask_depth,
            "mid_price": mid_price,
        }

    def _interpret_metrics(self, m: dict) -> dict:
        score = 0.0
        reasons = []

        imbalance = m["imbalance"]

        # Strong bid pressure
        if imbalance > 0.3:
            score += 0.4
            reasons.append(f"bid pressure ({imbalance:+.2f})")
        elif imbalance < -0.3:
            score -= 0.4
            reasons.append(f"ask pressure ({imbalance:+.2f})")

        # Bid walls = support (short-term bullish)
        if m["bid_walls"] > 0 and m["ask_walls"] == 0:
            score += 0.2
            reasons.append(f"{m['bid_walls']} bid wall(s)")
        elif m["ask_walls"] > 0 and m["bid_walls"] == 0:
            score -= 0.2
            reasons.append(f"{m['ask_walls']} ask wall(s)")

        # Wide spread = poor liquidity, reduce confidence
        confidence_penalty = 1.0
        if m["spread_pct"] > 0.005:
            confidence_penalty = 0.5
            reasons.append(f"wide spread ({m['spread_pct']:.3%})")

        # Low depth = dangerous for our position size
        min_depth = min(m["bid_depth_usd"], m["ask_depth_usd"])
        if min_depth < 10_000:
            score *= 0.3
            reasons.append(f"thin book (${min_depth:,.0f})")

        score = max(-1.0, min(1.0, score))
        confidence = abs(score) * 0.6 * confidence_penalty

        return {
            "score": score,
            "confidence": confidence,
            "reason": ", ".join(reasons[:2]) or "balanced book",
            "strategy": "orderbook",
            "imbalance": imbalance,
            "depth_usd": min_depth,
        }

    async def get_execution_quality(self, symbol: str, size_usd: float) -> dict:
        """
        Estimate slippage for a given trade size.
        Critical for knowing if the trade is actually profitable after fees.
        """
        book = await self._fetch_book(symbol, limit=50)
        if not book:
            return {"slippage_pct": 1.0, "executable": False}

        asks = book.get("asks", [])
        mid = (book["bids"][0][0] + asks[0][0]) / 2 if book["bids"] else asks[0][0]

        remaining = size_usd
        weighted_price = 0.0
        for price, qty in asks:
            level_usd = price * qty
            if remaining <= 0:
                break
            fill = min(remaining, level_usd)
            weighted_price += price * (fill / size_usd)
            remaining -= fill

        if remaining > 0:
            return {"slippage_pct": 5.0, "executable": False, "reason": "insufficient liquidity"}

        slippage = (weighted_price - mid) / mid if mid > 0 else 0
        fee = 0.001  # 0.1% taker fee
        total_cost = slippage + fee

        return {
            "slippage_pct": round(slippage * 100, 4),
            "fee_pct": round(fee * 100, 4),
            "total_cost_pct": round(total_cost * 100, 4),
            "executable": total_cost < 0.005,  # Only worth it if <0.5% total friction
            "mid_price": mid,
        }

    async def _fetch_book(self, symbol: str, limit: int = 20) -> Optional[dict]:
        if self._exchange is None:
            return self._mock_book(symbol)
        try:
            return await self._exchange.fetch_order_book(symbol, limit=limit)
        except Exception as e:
            log.debug(f"Order book fetch failed {symbol}: {e}")
            return None

    def _mock_book(self, symbol: str) -> dict:
        import random
        mid = random.uniform(100, 50000)
        return {
            "bids": [[mid * (1 - 0.001 * i), random.uniform(0.1, 10)] for i in range(20)],
            "asks": [[mid * (1 + 0.001 * i), random.uniform(0.1, 10)] for i in range(20)],
        }
