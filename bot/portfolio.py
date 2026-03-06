"""
Portfolio state with progressive trailing stops and crash recovery.

Trailing stop phases:
  Phase 1 (0 to 1R profit): move stop to breakeven
  Phase 2 (1R to 2R profit): trail at 1x ATR behind high
  Phase 3 (>2R profit):      trail tight at 0.75x ATR behind high

State is persisted to logs/state.json after every trade for crash recovery.
"""

import json
import logging
from decimal import Decimal
from datetime import datetime
from pathlib import Path
from bot import risk

log = logging.getLogger("portfolio")
STATE_FILE = Path("logs/state.json")


class Portfolio:
    def __init__(self, capital: float):
        self.initial = Decimal(str(capital))
        self.cash = Decimal(str(capital))
        self.positions: dict[str, dict] = {}
        self.cooldowns: dict[str, datetime] = {}
        self.equity_curve: list[dict] = []

    def buy(self, symbol: str, price: Decimal, size_usd: Decimal, atr: float, config: dict | None = None):
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
        risk_per_unit = atr_d * sl_mult

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
        self.save_state()
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

        self.save_state()
        return pnl

    def update_price(self, symbol: str, price: Decimal):
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

        if pos["trail_phase"] < 1 and r_multiple >= pos["trail_phase1_r"]:
            pos["trail_phase"] = 1
            new_stop = entry + atr_d * Decimal("0.1")
            if new_stop > pos["stop_loss"]:
                pos["stop_loss"] = new_stop
                log.info(f"TRAIL P1 {symbol} | stop -> breakeven ${new_stop:,.2f}")

        if pos["trail_phase"] < 2 and r_multiple >= pos["trail_phase2_r"]:
            pos["trail_phase"] = 2
            log.info(f"TRAIL P2 {symbol} | trailing at 1x ATR")

        if pos["trail_phase"] >= 2:
            if r_multiple >= pos["trail_phase2_r"] * 2:
                trail_distance = atr_d * pos["trail_phase3_atr"]
                if pos["trail_phase"] < 3:
                    pos["trail_phase"] = 3
                    log.info(f"TRAIL P3 {symbol} | tight trail at {pos['trail_phase3_atr']}x ATR")
            else:
                trail_distance = atr_d

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
        if len(self.equity_curve) > 10000:
            self.equity_curve = self.equity_curve[-10000:]

    # ── State persistence ──

    def save_state(self):
        """Save portfolio state for crash recovery."""
        try:
            state = {
                "cash": str(self.cash),
                "initial": str(self.initial),
                "positions": {},
                "cooldowns": {s: t.isoformat() for s, t in self.cooldowns.items()},
                "saved_at": datetime.now().isoformat(),
            }
            for sym, pos in self.positions.items():
                state["positions"][sym] = {
                    "entry": str(pos["entry"]),
                    "quantity": str(pos["quantity"]),
                    "cost": str(pos["cost"]),
                    "current": str(pos["current"]),
                    "high_since_entry": str(pos["high_since_entry"]),
                    "stop_loss": str(pos["stop_loss"]),
                    "take_profit": str(pos["take_profit"]),
                    "atr": str(pos["atr"]),
                    "risk_1r": str(pos["risk_1r"]),
                    "trail_phase": pos["trail_phase"],
                    "trail_phase1_r": str(pos["trail_phase1_r"]),
                    "trail_phase2_r": str(pos["trail_phase2_r"]),
                    "trail_phase3_atr": str(pos["trail_phase3_atr"]),
                    "opened": pos["opened"].isoformat(),
                }

            STATE_FILE.parent.mkdir(exist_ok=True)
            STATE_FILE.write_text(json.dumps(state, indent=2))
        except Exception as e:
            log.warning(f"Failed to save state: {e}")

    def load_state(self) -> bool:
        """Load portfolio state from disk. Returns True if state was loaded."""
        try:
            if not STATE_FILE.exists():
                return False

            raw = json.loads(STATE_FILE.read_text())
            self.cash = Decimal(raw["cash"])
            self.initial = Decimal(raw["initial"])

            for sym, pos_data in raw.get("positions", {}).items():
                self.positions[sym] = {
                    "entry": Decimal(pos_data["entry"]),
                    "quantity": Decimal(pos_data["quantity"]),
                    "cost": Decimal(pos_data["cost"]),
                    "current": Decimal(pos_data["current"]),
                    "high_since_entry": Decimal(pos_data["high_since_entry"]),
                    "stop_loss": Decimal(pos_data["stop_loss"]),
                    "take_profit": Decimal(pos_data["take_profit"]),
                    "atr": Decimal(pos_data["atr"]),
                    "risk_1r": Decimal(pos_data["risk_1r"]),
                    "trail_phase": pos_data["trail_phase"],
                    "trail_phase1_r": Decimal(pos_data["trail_phase1_r"]),
                    "trail_phase2_r": Decimal(pos_data["trail_phase2_r"]),
                    "trail_phase3_atr": Decimal(pos_data["trail_phase3_atr"]),
                    "opened": datetime.fromisoformat(pos_data["opened"]),
                }

            for sym, ts in raw.get("cooldowns", {}).items():
                self.cooldowns[sym] = datetime.fromisoformat(ts)

            n = len(self.positions)
            log.info(f"State loaded: ${self.cash:,.2f} cash, {n} positions")
            return True

        except Exception as e:
            log.warning(f"Failed to load state: {e}")
            return False

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
