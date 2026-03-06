"""
Configuration - edit settings.yaml or pass env vars.
"""

import os
import yaml
from decimal import Decimal
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Settings:
    # Identity
    wallet: str = ""
    spending_limit: Decimal = Decimal("1000")
    live_mode: bool = False

    # Risk management
    max_position_pct: float = 0.10      # Max 10% of portfolio per position
    stop_loss_pct: float = 0.05         # 5% stop-loss
    take_profit_pct: float = 0.15       # 15% take-profit
    max_open_positions: int = 8
    max_daily_loss_pct: float = 0.10    # Halt if down 10% in a day
    min_liquidity_usd: float = 50_000   # Skip illiquid pairs

    # Strategy weights (auto-adjusted by meta-AI)
    momentum_weight: float = 0.35
    mean_reversion_weight: float = 0.25
    sentiment_weight: float = 0.25
    arbitrage_weight: float = 0.15

    # Markets
    crypto_enabled: bool = True
    stocks_enabled: bool = True
    memecoins_enabled: bool = True
    crypto_pairs: List[str] = field(default_factory=lambda: [
        "BTC/USDT", "ETH/USDT", "SOL/USDT", "AVAX/USDT", "BNB/USDT"
    ])
    memecoin_pairs: List[str] = field(default_factory=lambda: [
        "DOGE/USDT", "SHIB/USDT", "PEPE/USDT", "WIF/USDT", "BONK/USDT"
    ])
    stock_watchlist: List[str] = field(default_factory=lambda: [
        "AAPL", "NVDA", "TSLA", "META", "MSFT", "AMD", "GOOGL"
    ])

    # Connectors (set via env or yaml)
    binance_api_key: str = field(default_factory=lambda: os.getenv("BINANCE_API_KEY", ""))
    binance_secret: str = field(default_factory=lambda: os.getenv("BINANCE_SECRET", ""))
    alpaca_api_key: str = field(default_factory=lambda: os.getenv("ALPACA_API_KEY", ""))
    alpaca_secret: str = field(default_factory=lambda: os.getenv("ALPACA_SECRET", ""))
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    lunarcrush_api_key: str = field(default_factory=lambda: os.getenv("LUNARCRUSH_API_KEY", ""))

    # Timing
    scan_interval_sec: int = 60        # How often to scan markets
    sentiment_interval_sec: int = 300  # How often to refresh sentiment
    rebalance_interval_sec: int = 3600 # Portfolio rebalance check

    @classmethod
    def load(cls, path: str) -> "Settings":
        s = cls()
        if os.path.exists(path):
            with open(path) as f:
                data = yaml.safe_load(f) or {}
            for k, v in data.items():
                if hasattr(s, k):
                    setattr(s, k, v)

        # Always re-read env vars here so dotenv has time to load them
        s.binance_api_key = os.getenv("BINANCE_API_KEY", s.binance_api_key)
        s.binance_secret = os.getenv("BINANCE_SECRET", s.binance_secret)
        s.alpaca_api_key = os.getenv("ALPACA_API_KEY", s.alpaca_api_key)
        s.alpaca_secret = os.getenv("ALPACA_SECRET", s.alpaca_secret)
        s.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY", s.anthropic_api_key)
        s.openai_api_key = os.getenv("OPENAI_API_KEY", s.openai_api_key)

        # Validate settings
        s._validate()

        # Log key status (masked)
        import logging
        log = logging.getLogger("Settings")
        log.info(f"BINANCE key loaded: {'YES' if s.binance_api_key else 'NO'}")
        log.info(f"ALPACA key loaded:  {'YES' if s.alpaca_api_key else 'NO'}")

        return s

    def _validate(self):
        import logging
        log = logging.getLogger("Settings")
        assert 0 < self.stop_loss_pct < 1, f"stop_loss_pct must be 0-1, got {self.stop_loss_pct}"
        assert 0 < self.take_profit_pct < 1, f"take_profit_pct must be 0-1, got {self.take_profit_pct}"
        assert self.take_profit_pct > self.stop_loss_pct, "take_profit_pct must be > stop_loss_pct"
        assert self.max_open_positions > 0, "max_open_positions must be > 0"
        assert 0 < self.max_position_pct < 1, f"max_position_pct must be 0-1, got {self.max_position_pct}"
        total_w = self.momentum_weight + self.mean_reversion_weight + self.sentiment_weight + self.arbitrage_weight
        if abs(total_w - 1.0) > 0.05:
            log.warning(f"Strategy weights sum to {total_w:.2f}, not 1.0")
