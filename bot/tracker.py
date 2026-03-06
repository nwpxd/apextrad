"""Trade history and performance metrics."""

import json
import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger("tracker")
TRADES_FILE = Path("logs/trades.json")


class Tracker:
    def __init__(self):
        self.trades: list[dict] = []
        self._load()

    def record(self, symbol: str, entry: float, exit_price: float, pnl: float, reason: str):
        trade = {
            "time": datetime.now().isoformat(),
            "symbol": symbol,
            "entry": entry,
            "exit": exit_price,
            "pnl": round(pnl, 4),
            "reason": reason,
        }
        self.trades.append(trade)
        self._save()

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        wins = sum(1 for t in self.trades if t["pnl"] > 0)
        return wins / len(self.trades) * 100

    @property
    def total_pnl(self) -> float:
        return sum(t["pnl"] for t in self.trades)

    @property
    def avg_win(self) -> float:
        wins = [t["pnl"] for t in self.trades if t["pnl"] > 0]
        return sum(wins) / len(wins) if wins else 0.0

    @property
    def avg_loss(self) -> float:
        losses = [t["pnl"] for t in self.trades if t["pnl"] <= 0]
        return sum(losses) / len(losses) if losses else 0.0

    def _save(self):
        try:
            TRADES_FILE.parent.mkdir(exist_ok=True)
            TRADES_FILE.write_text(json.dumps(self.trades, indent=2))
        except Exception:
            pass

    def _load(self):
        try:
            if TRADES_FILE.exists():
                self.trades = json.loads(TRADES_FILE.read_text())
        except Exception:
            self.trades = []
