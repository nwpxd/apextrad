"""
Risk Manager - the bot's financial immune system.
Never trades beyond the spending limit. Ever.
"""

import logging
from decimal import Decimal
from typing import Optional
from config.settings import Settings
from core.portfolio import Portfolio

log = logging.getLogger("RiskManager")


class RiskManager:
    def __init__(self, settings: Settings, portfolio: Portfolio):
        self.settings = settings
        self.portfolio = portfolio

    def calculate_position_size(
        self, symbol: str, price: Decimal, confidence: float
    ) -> Optional[Decimal]:
        """
        Returns USD size to buy, or None if risk checks fail.
        Scales by signal confidence.
        """
        # Max allowed positions check
        if len(self.portfolio.positions) >= self.settings.max_open_positions:
            log.debug(f"Max positions ({self.settings.max_open_positions}) reached")
            return None

        # Don't double-up on existing positions
        if symbol in self.portfolio.positions:
            return None

        # Base size: max_position_pct of spending limit
        base_size = self.settings.spending_limit * Decimal(str(self.settings.max_position_pct))

        # Scale by confidence (0.5-1.0 range mapped to 0.4-1.0 of base)
        confidence_scalar = Decimal(str(0.4 + 0.6 * max(0.0, min(1.0, confidence))))
        size = base_size * confidence_scalar

        # Memecoin size cap (max 3% per memecoin)
        if self.is_memecoin(symbol):
            size = self.memecoin_size_cap(size)

        # Never go below $10 (dust)
        if size < Decimal("10"):
            return None

        # Aggregate exposure check: don't exceed spending limit
        current_exposure = self.portfolio.exposure_usd()
        if current_exposure + size > self.settings.spending_limit:
            size = self.settings.spending_limit - current_exposure
            if size < Decimal("10"):
                log.debug("Aggregate exposure would exceed spending limit")
                return None

        # Keep 10% cash buffer
        max_cash_use = self.portfolio.cash_usd * Decimal("0.90")
        size = min(size, max_cash_use)

        if size < Decimal("10"):
            return None

        log.debug(f"Position size for {symbol}: ${size:.2f} (confidence={confidence:.2f})")
        return size

    def within_spending_limit(self, additional_usd: Decimal) -> bool:
        """Check if buying more would breach the hard spending limit."""
        total_exposure = self.portfolio.exposure_usd() + additional_usd
        within = total_exposure <= self.settings.spending_limit
        if not within:
            log.warning(
                f"Spending limit guard: exposure ${total_exposure:.2f} "
                f"> limit ${self.settings.spending_limit:.2f}"
            )
        return within

    def max_drawdown_ok(self, current_value: Decimal, peak_value: Decimal) -> bool:
        if peak_value == 0:
            return True
        drawdown = (peak_value - current_value) / peak_value
        return drawdown < Decimal("0.20")  # Hard 20% max drawdown

    def is_memecoin(self, symbol: str) -> bool:
        return symbol in self.settings.memecoin_pairs

    def memecoin_size_cap(self, size: Decimal) -> Decimal:
        """Memecoins get smaller allocations - high risk, high reward."""
        return min(size, self.settings.spending_limit * Decimal("0.03"))  # Max 3% per memecoin
