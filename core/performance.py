"""
Performance Tracker - Real-time Sharpe, Calmar, drawdown, win rate.
Feeds data to the dashboard and helps MetaAI learn from outcomes.
"""

import json
import logging
import os
from datetime import datetime
from decimal import Decimal
from typing import List, Dict
import numpy as np
import pandas as pd

log = logging.getLogger("Performance")

PERF_FILE = "logs/performance.json"


class PerformanceTracker:
    def __init__(self, initial_capital: float):
        self.initial_capital = initial_capital
        self.equity_snapshots: List[dict] = []
        self.trades: List[dict] = []
        self.peak_equity = initial_capital
        self._load()

    def record_equity(self, equity: float):
        self.equity_snapshots.append({
            "timestamp": datetime.now().isoformat(),
            "equity": equity,
        })
        self.peak_equity = max(self.peak_equity, equity)
        self._save()

    def record_trade(self, symbol: str, entry: float, exit_price: float,
                     quantity: float, pnl: float, reason: str, strategy_hint: str = ""):
        trade = {
            "timestamp": datetime.now().isoformat(),
            "symbol": symbol,
            "entry": entry,
            "exit": exit_price,
            "quantity": quantity,
            "pnl_usd": pnl,
            "pnl_pct": (exit_price - entry) / entry if entry > 0 else 0,
            "exit_reason": reason,
            "strategy": strategy_hint,
        }
        self.trades.append(trade)
        self._save()
        return trade

    def get_metrics(self) -> dict:
        if not self.equity_snapshots:
            return self._empty_metrics()

        equities = [s["equity"] for s in self.equity_snapshots]
        current = equities[-1]

        # Returns
        total_return = (current - self.initial_capital) / self.initial_capital

        # Sharpe (annualized hourly)
        returns = pd.Series(equities).pct_change().dropna()
        sharpe = float(returns.mean() / returns.std() * np.sqrt(8760)) if len(returns) > 1 and returns.std() > 0 else 0

        # Drawdown
        eq_series = pd.Series(equities)
        rolling_max = eq_series.cummax()
        dd_series = (eq_series - rolling_max) / rolling_max
        max_dd = float(dd_series.min())
        current_dd = float(dd_series.iloc[-1])

        # Calmar
        calmar = (total_return / abs(max_dd)) if max_dd != 0 else 0

        # Trade stats
        n_trades = len(self.trades)
        if n_trades > 0:
            wins = [t for t in self.trades if t["pnl_usd"] > 0]
            losses = [t for t in self.trades if t["pnl_usd"] <= 0]
            win_rate = len(wins) / n_trades
            gross_profit = sum(t["pnl_usd"] for t in wins)
            gross_loss = abs(sum(t["pnl_usd"] for t in losses))
            profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
            avg_win = np.mean([t["pnl_pct"] for t in wins]) if wins else 0
            avg_loss = np.mean([t["pnl_pct"] for t in losses]) if losses else 0
            # Recent performance (last 20 trades)
            recent = self.trades[-20:]
            recent_wins = len([t for t in recent if t["pnl_usd"] > 0])
            recent_win_rate = recent_wins / len(recent) if recent else 0
        else:
            win_rate = profit_factor = avg_win = avg_loss = recent_win_rate = 0

        return {
            "current_equity": round(current, 2),
            "initial_capital": round(self.initial_capital, 2),
            "total_return_pct": round(total_return * 100, 2),
            "total_pnl_usd": round(current - self.initial_capital, 2),
            "sharpe_ratio": round(sharpe, 3),
            "max_drawdown_pct": round(max_dd * 100, 2),
            "current_drawdown_pct": round(current_dd * 100, 2),
            "calmar_ratio": round(calmar, 3),
            "peak_equity": round(self.peak_equity, 2),
            "n_trades": n_trades,
            "win_rate_pct": round(win_rate * 100, 1),
            "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else 999,
            "avg_win_pct": round(avg_win * 100, 2),
            "avg_loss_pct": round(avg_loss * 100, 2),
            "recent_win_rate_pct": round(recent_win_rate * 100, 1),
            "equity_curve": [{"t": s["timestamp"], "v": s["equity"]} for s in self.equity_snapshots[-200:]],
            "recent_trades": self.trades[-50:],
        }

    def get_strategy_breakdown(self) -> dict:
        """How is each strategy performing?"""
        breakdown = {}
        for trade in self.trades:
            strat = trade.get("strategy", "unknown")
            if strat not in breakdown:
                breakdown[strat] = {"trades": 0, "pnl": 0, "wins": 0}
            breakdown[strat]["trades"] += 1
            breakdown[strat]["pnl"] += trade["pnl_usd"]
            if trade["pnl_usd"] > 0:
                breakdown[strat]["wins"] += 1
        for strat in breakdown:
            n = breakdown[strat]["trades"]
            breakdown[strat]["win_rate"] = round(breakdown[strat]["wins"] / n * 100, 1) if n > 0 else 0
        return breakdown

    def _empty_metrics(self) -> dict:
        return {
            "current_equity": self.initial_capital,
            "total_return_pct": 0,
            "sharpe_ratio": 0,
            "max_drawdown_pct": 0,
            "n_trades": 0,
            "win_rate_pct": 0,
            "equity_curve": [],
            "recent_trades": [],
        }

    def _save(self):
        try:
            os.makedirs("logs", exist_ok=True)
            with open(PERF_FILE, "w") as f:
                json.dump({
                    "initial_capital": self.initial_capital,
                    "peak_equity": self.peak_equity,
                    "equity_snapshots": self.equity_snapshots[-5000:],  # Keep last 5k
                    "trades": self.trades,
                }, f)
        except Exception as e:
            log.debug(f"Save error: {e}")

    def _load(self):
        try:
            if os.path.exists(PERF_FILE):
                with open(PERF_FILE) as f:
                    data = json.load(f)
                self.equity_snapshots = data.get("equity_snapshots", [])
                self.trades = data.get("trades", [])
                self.peak_equity = data.get("peak_equity", self.initial_capital)
                log.info(f"Loaded {len(self.trades)} historical trades from disk")
        except Exception:
            pass
