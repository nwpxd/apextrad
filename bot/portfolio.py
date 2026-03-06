"""
Portfolio state with progressive trailing stops.

Trailing stop phases:
  Phase 1 (0 to 1R profit): move stop to breakeven
  Phase 2 (1R to 2R profit): trail at 1x ATR behind high
  Phase 3 (>2R profit):      trail tight at 0.75x ATR behind high
"""

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
        self.cooldowns: dict[str, datetime] = {}
        self.equity_curve: list[dict] = []  # {time, value}

    def buy(self, symbol: str, price: Decimal, size_usd: Decimal, atr: float, config: dict | None = None):
        """Buy with ATR-based dynamic stops and progressive trailing config."""
        if config is None:
            config = {}

        quantity = size_usd / price
        quantity = risk.round_quantity(quantity, price)
        if quantity <= 0:
            return False

        cost = risk.buy_cost(quantity, price)
        if cost > self.cash:
            return False

        atr_d = Decimal(str(atr))
        sl_mult = Decimal(str(config.get("atr_sl_mult", 2.0)))
        tp_mult = Decimal(str(config.get("atr_tp_mult", 4.0)))

        stop_loss = price - atr_d * sl_mult
        take_profit = price + atr_d * tp_mult
        risk_per_unit = atr_d * sl_mult  # 1R = this distance

        self.cash -= cost
        self.positions[symbol] = {
            "entry": price,
            "quantity": quantity,
            "cost": cost,
            "current": price,
            "high_since_entry": price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "atr": atr_d,
            "risk_1r": risk_per_unit,
            "trail_phase": 0,
            "trail_phase1_r": Decimal(str(config.get("trail_phase1_r", 1.0))),
            "trail_phase2_r": Decimal(str(config.get("trail_phase2_r", 2.0))),
            "trail_phase3_atr": Decimal(str(config.get("trail_phase3_atr", 0.75))),
            "opened": datetime.now(),
        }
        log.info(
            f"BUY {symbol} | {quantity} @ ${price:,.2f} | "
            f"SL ${stop_loss:,.2f} | TP ${take_profit:,.2f} | "
            f"1R = ${risk_per_unit:,.2f}"
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

        if reason == "stop_loss":
            self.cooldowns[symbol] = datetime.now()

        return pnl

    def update_price(self, symbol: str, price: Decimal):
        """Update price and manage progressive trailing stop."""
        pos = self.positions.get(symbol)
        if not pos:
            return

        pos["current"] = price

        if price > pos["high_since_entry"]:
            pos["high_since_entry"] = price

        entry = pos["entry"]
        atr_d = pos["atr"]
        risk_1r = pos["risk_1r"]
        profit = price - entry
        r_multiple = profit / risk_1r if risk_1r > 0 else Decimal("0")

        # Phase 1: profit reached 1R → move stop to breakeven
        if pos["trail_phase"] < 1 and r_multiple >= pos["trail_phase1_r"]:
            pos["trail_phase"] = 1
            new_stop = entry + atr_d * Decimal("0.1")  # breakeven + tiny buffer
            if new_stop > pos["stop_loss"]:
                pos["stop_loss"] = new_stop
                log.info(f"TRAIL P1 {symbol} | stop → breakeven ${new_stop:,.2f}")

        # Phase 2: profit reached 2R → trail at 1x ATR
        if pos["trail_phase"] < 2 and r_multiple >= pos["trail_phase2_r"]:
            pos["trail_phase"] = 2
            log.info(f"TRAIL P2 {symbol} | trailing at 1x ATR")

        # Apply trailing based on current phase
        if pos["trail_phase"] >= 2:
            if r_multiple >= pos["trail_phase2_r"] * 2:
                # Phase 3: tight trail at 0.75x ATR
                trail_distance = atr_d * pos["trail_phase3_atr"]
                if pos["trail_phase"] < 3:
                    pos["trail_phase"] = 3
                    log.info(f"TRAIL P3 {symbol} | tight trail at {pos['trail_phase3_atr']}x ATR")
            else:
                trail_distance = atr_d  # 1x ATR

            trail_stop = pos["high_since_entry"] - trail_distance
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
        cd = self.cooldowns.get(symbol)
        if not cd:
            return False
        elapsed = (datetime.now() - cd).total_seconds()
        if elapsed > 7200:
            del self.cooldowns[symbol]
            return False
        return True

    def record_equity(self):
        self.equity_curve.append({
            "time": datetime.now().isoformat(),
            "value": float(self.value),
        })
        # Keep last 10000 points
        if len(self.equity_curve) > 10000:
            self.equity_curve = self.equity_curve[-10000:]

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
