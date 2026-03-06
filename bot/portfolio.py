"""Portfolio state. Tracks cash, positions, P&L. Includes fees."""

import logging
from decimal import Decimal
from datetime import datetime
from bot import risk

log = logging.getLogger("portfolio")


class Portfolio:
    def __init__(self, capital: float):
        self.initial = Decimal(str(capital))
        self.cash = Decimal(str(capital))
        self.positions: dict[str, dict] = {}

    def buy(self, symbol: str, price: Decimal, size_usd: Decimal, stop_pct: float, tp_pct: float):
        quantity = size_usd / price
        quantity = risk.round_quantity(quantity, price)
        if quantity <= 0:
            return

        cost = risk.buy_cost(quantity, price)
        if cost > self.cash:
            return

        self.cash -= cost
        self.positions[symbol] = {
            "entry": price,
            "quantity": quantity,
            "cost": cost,
            "current": price,
            "stop_loss": price * (1 - Decimal(str(stop_pct))),
            "take_profit": price * (1 + Decimal(str(tp_pct))),
            "opened": datetime.now(),
        }
        log.info(f"BUY {symbol} | {quantity} @ ${price:,.2f} | cost ${cost:,.2f}")

    def sell(self, symbol: str, price: Decimal, reason: str = "") -> Decimal:
        pos = self.positions.pop(symbol, None)
        if not pos:
            return Decimal("0")

        proceeds = risk.sell_proceeds(pos["quantity"], price)
        self.cash += proceeds
        pnl = proceeds - pos["cost"]
        tag = "+" if pnl >= 0 else "-"
        log.info(f"SELL {symbol} | @ ${price:,.2f} | PnL ${pnl:+,.2f} ({tag}) | {reason}")
        return pnl

    def update_price(self, symbol: str, price: Decimal):
        if symbol in self.positions:
            self.positions[symbol]["current"] = price

    def check_stops(self, symbol: str, price: Decimal) -> str | None:
        """Returns 'stop_loss' or 'take_profit' if triggered, else None."""
        pos = self.positions.get(symbol)
        if not pos:
            return None
        if price <= pos["stop_loss"]:
            return "stop_loss"
        if price >= pos["take_profit"]:
            return "take_profit"
        return None

    @property
    def exposure(self) -> Decimal:
        return sum(
            p["quantity"] * p["current"] for p in self.positions.values()
        )

    @property
    def value(self) -> Decimal:
        return self.cash + self.exposure

    @property
    def pnl(self) -> Decimal:
        return self.value - self.initial

    @property
    def pnl_pct(self) -> float:
        if self.initial == 0:
            return 0.0
        return float(self.pnl / self.initial * 100)
