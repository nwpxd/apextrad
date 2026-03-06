"""Portfolio state management."""

import logging
from decimal import Decimal
from typing import Dict, Optional
from config.settings import Settings

log = logging.getLogger("Portfolio")


class Portfolio:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.positions: Dict[str, dict] = {}
        self.cash_usd: Decimal = settings.spending_limit
        self.realized_pnl: Decimal = Decimal("0")

    async def sync(self, crypto_conn, stock_conn):
        """Sync real balances from exchanges."""
        try:
            if self.settings.live_mode:
                balances = await crypto_conn.get_balances()
                log.info(f"Synced balances: {balances}")
        except Exception as e:
            log.warning(f"Could not sync balances: {e}")

    async def total_value_usd(self) -> Decimal:
        """Estimate total portfolio value (cash + positions)."""
        position_value = sum(
            pos["quantity"] * pos["current_price"]
            for pos in self.positions.values()
        )
        return self.cash_usd + Decimal(str(position_value))

    def get_position(self, symbol: str) -> Optional[dict]:
        return self.positions.get(symbol)

    def record_buy(self, symbol: str, price: Decimal, quantity: Decimal, cost_usd: Decimal):
        if symbol in self.positions:
            # Average in
            pos = self.positions[symbol]
            total_qty = pos["quantity"] + quantity
            avg_price = (pos["entry_price"] * pos["quantity"] + price * quantity) / total_qty
            pos["quantity"] = total_qty
            pos["entry_price"] = avg_price
        else:
            self.positions[symbol] = {
                "quantity": quantity,
                "entry_price": price,
                "current_price": price,
                "cost_usd": cost_usd,
            }
        self.cash_usd -= cost_usd
        log.info(f"Position opened: {symbol} | qty={quantity:.6f} @ ${price:.4f}")

    def record_sell(self, symbol: str, price: Decimal) -> Decimal:
        pos = self.positions.pop(symbol, None)
        if not pos:
            return Decimal("0")
        proceeds = pos["quantity"] * price
        cost = pos["cost_usd"]
        pnl = proceeds - cost
        self.cash_usd += proceeds
        self.realized_pnl += pnl
        return pnl

    def update_price(self, symbol: str, price: Decimal):
        if symbol in self.positions:
            self.positions[symbol]["current_price"] = price

    def exposure_usd(self) -> Decimal:
        return sum(
            Decimal(str(pos["quantity"])) * pos["current_price"]
            for pos in self.positions.values()
        )
