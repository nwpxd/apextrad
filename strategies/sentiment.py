"""
Sentiment Strategy - LLM-powered news + social analysis.
Uses Claude/GPT to score news headlines and Reddit/Twitter sentiment.
Critical for memecoins which are 80% narrative-driven.
"""

import asyncio
import logging
import re
from typing import Dict, List, Optional
from config.settings import Settings

log = logging.getLogger("Sentiment")

SENTIMENT_SYSTEM_PROMPT = """You are a crypto/stock market sentiment analyzer.
Given news headlines or social posts about an asset, output ONLY a JSON object:
{"score": <float -1.0 to 1.0>, "confidence": <float 0.0 to 1.0>, "summary": "<10 words max>"}

score: -1.0 = extremely bearish, 0 = neutral, +1.0 = extremely bullish
confidence: how strong/clear the sentiment signal is
Do not output anything else. Only valid JSON."""


CACHE_TTL_SEC = 600  # 10 minutes


class SentimentStrategy:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._cache: Dict[str, dict] = {}
        self._cache_times: Dict[str, float] = {}
        self._client = None
        self._init_client()

    def _init_client(self):
        if self.settings.anthropic_api_key:
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=self.settings.anthropic_api_key)
                log.info("[OK] Sentiment: using Claude (Anthropic)")
            except ImportError:
                log.warning("anthropic not installed - pip install anthropic")
        elif self.settings.openai_api_key:
            try:
                import openai
                self._client = openai.OpenAI(api_key=self.settings.openai_api_key)
                log.info("[OK] Sentiment: using GPT (OpenAI)")
            except ImportError:
                log.warning("openai not installed - pip install openai")
        else:
            log.warning("[WARN]  No AI API key set - sentiment will use basic heuristics")

    async def refresh(self, symbols: List[str]):
        """Fetch and analyze sentiment for all symbols."""
        tasks = [self._analyze_symbol(s) for s in symbols]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _analyze_symbol(self, symbol: str):
        try:
            headlines = await self._fetch_headlines(symbol)
            if not headlines:
                return

            text = "\n".join(headlines[:10])
            import time as _time
            signal = await self._score_with_ai(symbol, text)
            self._cache[symbol] = signal
            self._cache_times[symbol] = _time.time()
            log.debug(f"Sentiment {symbol}: score={signal['score']:.2f} - {signal.get('summary','')}")
        except Exception as e:
            log.debug(f"Sentiment error for {symbol}: {e}")

    async def _fetch_headlines(self, symbol: str) -> List[str]:
        """Fetch recent news. Uses multiple free sources."""
        clean = symbol.split("/")[0].upper()
        headlines = []

        # Try RSS / CryptoPanic (free tier)
        try:
            import aiohttp
            url = f"https://cryptopanic.com/api/v1/posts/?auth_token=free&currencies={clean}&public=true"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
                    if r.status == 200:
                        data = await r.json()
                        headlines = [p["title"] for p in data.get("results", [])[:10]]
        except Exception:
            pass

        # Fallback: basic heuristic keywords from coin name
        if not headlines:
            headlines = [f"No recent news found for {clean}"]

        return headlines

    async def _score_with_ai(self, symbol: str, text: str) -> dict:
        """Use LLM to score sentiment, fallback to heuristics."""
        if self._client is None:
            return self._heuristic_score(text)

        prompt = f"Asset: {symbol}\nHeadlines:\n{text}"

        try:
            import anthropic
            if isinstance(self._client, anthropic.Anthropic):
                msg = self._client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=100,
                    system=SENTIMENT_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                )
                raw = msg.content[0].text
            else:
                # OpenAI
                resp = self._client.chat.completions.create(
                    model="gpt-4o-mini",
                    max_tokens=100,
                    messages=[
                        {"role": "system", "content": SENTIMENT_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                )
                raw = resp.choices[0].message.content

            import json
            # LLM may wrap JSON in markdown or add extra text
            raw = raw.strip()
            json_match = re.search(r'\{.*\}', raw, re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group())
                return {
                    "score": float(parsed.get("score", 0)),
                    "confidence": float(parsed.get("confidence", 0)),
                    "summary": str(parsed.get("summary", "")),
                    "strategy": "sentiment",
                }
            return self._heuristic_score(text)

        except Exception as e:
            log.debug(f"AI scoring failed: {e}")
            return self._heuristic_score(text)

    def _heuristic_score(self, text: str) -> dict:
        """Simple keyword-based scoring when no AI available."""
        text_lower = text.lower()
        bullish = ["surge", "rally", "moon", "breakout", "adoption", "partnership", "ath", "pump", "bullish", "buy"]
        bearish = ["crash", "dump", "hack", "ban", "lawsuit", "bearish", "sell", "fear", "plunge", "collapse"]

        bull_count = sum(1 for w in bullish if w in text_lower)
        bear_count = sum(1 for w in bearish if w in text_lower)

        total = bull_count + bear_count
        if total == 0:
            return {"score": 0.0, "confidence": 0.1, "summary": "neutral/no signal"}

        score = (bull_count - bear_count) / total
        confidence = min(0.6, total * 0.1)
        return {"score": score, "confidence": confidence, "summary": f"heuristic: {bull_count}B/{bear_count}S"}

    def get_cached_signal(self, symbol: str) -> dict:
        import time as _time
        if symbol in self._cache:
            age = _time.time() - self._cache_times.get(symbol, 0)
            if age < CACHE_TTL_SEC:
                return self._cache[symbol]
        return {
            "score": 0.0,
            "confidence": 0.05,
            "reason": "no sentiment data",
            "strategy": "sentiment",
        }
