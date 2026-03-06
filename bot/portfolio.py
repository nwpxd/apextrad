"""Portfolio state. Tracks cash, positions, P&L, trailing stops. Includes fees."""

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
        self.cooldowns: dict[str, datetime] = {}  # symbol -> last stop-loss time

    def buy(self, symbol: str, price: Decimal, size_usd: Decimal, atr: float):
        """Buy with ATR-based dynamic stops."""
        quantity = size_usd / price
        quantity = risk.round_quantity(quantity, price)
        if quantity <= 0:
            return False

        cost = risk.buy_cost(quantity, price)
        if cost > self.cash:
            return False

        atr_d = Decimal(str(atr))

        self.cash -= cost
        self.positions[symbol] = {
            "entry": price,
            "quantity": quantity,
            "cost": cost,
            "current": price,
            "high_since_entry": price,  # for trailing stop
            "stop_loss": price - atr_d * 2,  # 2x ATR below entry
            "take_profit": price + atr_d * 4,  # 4x ATR above entry (2:1 R/R)
            "atr": atr_d,
            "trailing_active": False,
            "opened": datetime.now(),
        }
        log.info(
            f"BUY {symbol} | {quantity} @ ${price:,.2f} | "
            f"SL ${price - atr_d * 2:,.2f} | TP ${price + atr_d * 4:,.2f}"
        )
        return True

    def sell(self, symbol: str, price: Decimal, reason: str = "") -> Decimal:
        pos = self.positions.pop(symbol, None)
        if not pos:
            return Decimal("0")

        proceeds = risk.sell_proceeds(pos["quantity"], price)
        self.cash += proceeds
        pnl = proceeds - pos["cost"]
        pnl_pct = float((price - pos["entry"]) / pos["entry"] * 100)
        tag = "WIN" if pnl >= 0 else "LOSS"
        log.info(f"SELL {symbol} | @ ${price:,.2f} | {tag} ${pnl:+,.2f} ({pnl_pct:+.1f}%) | {reason}")

        # Set cooldown on stop-loss (don't re-enter for 2h)
        if reason == "stop_loss":
            self.cooldowns[symbol] = datetime.now()

        return pnl

    def update_price(self, symbol: str, price: Decimal):
        """Update current price and manage trailing stop."""
        pos = self.positions.get(symbol)
        if not pos:
            return

        pos["current"] = price

        # Track highest price since entry
        if price > pos["high_since_entry"]:
            pos["high_since_entry"] = price

        entry = pos["entry"]
        atr_d = pos["atr"]

        # Activate trailing stop after 2x ATR profit
        if not pos["trailing_active"] and price >= entry + atr_d * 2:
            pos["trailing_active"] = True
            # Move stop to breakeven + small buffer
            new_stop = entry + atr_d * Decimal("0.5")
            if new_stop > pos["stop_loss"]:
                pos["stop_loss"] = new_stop
                log.info(f"TRAIL {symbol} | stop moved to breakeven ${new_stop:,.2f}")

        # Trail the stop as price rises
        if pos["trailing_active"]:
            trail_stop = pos["high_since_entry"] - atr_d * Decimal("1.5")
            if trail_stop > pos["stop_loss"]:
                pos["stop_loss"] = trail_stop

    def check_stops(self, symbol: str, price: Decimal) -> str | None:
        pos = self.positions.get(symbol)
        if not pos:
            return None
        if price <= pos["stop_loss"]:
            return "stop_loss"
        if price >= pos["take_profit"]:
            return "take_profit"
        return None

    def is_on_cooldown(self, symbol: str) -> bool:
        """Don't re-enter a symbol within 2 hours of a stop-loss."""
        cd = self.cooldowns.get(symbol)
        if not cd:
            return False
        elapsed = (datetime.now() - cd).total_seconds()
        if elapsed > 7200:  # 2 hours
            del self.cooldowns[symbol]
            return False
        return True

    @property
    def exposure(self) -> Decimal:
        if not self.positions:
            return Decimal("0")
        return sum(p["quantity"] * p["current"] for p in self.positions.values())

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
