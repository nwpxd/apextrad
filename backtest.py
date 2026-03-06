"""
Backtesting Engine - Test strategies against historical data.
Supports walk-forward optimization to avoid overfitting.

Usage:
    python backtest.py --symbol BTC/USDT --start 2021-01-01 --end 2024-01-01
"""

import asyncio
import argparse
import logging
import json
from decimal import Decimal
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional
import pandas as pd
import numpy as np

from config.settings import Settings
from strategies.momentum import MomentumStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.sentiment import SentimentStrategy
from strategies.arbitrage import ArbitrageStrategy
from strategies.meta_ai import MetaAI

log = logging.getLogger("Backtest")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


class BacktestEngine:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.momentum = MomentumStrategy(settings)
        self.mean_reversion = MeanReversionStrategy(settings)
        self.arbitrage = ArbitrageStrategy(settings)
        self.meta_ai = MetaAI(settings)

    async def run(
        self,
        symbol: str,
        ohlcv: pd.DataFrame,
        initial_capital: float = 10_000.0,
    ) -> "BacktestResult":
        """
        Simulate trading on historical data.
        Returns BacktestResult with full trade log and metrics.
        """
        capital = Decimal(str(initial_capital))
        position = None
        trades = []
        equity_curve = []
        peak_equity = capital

        log.info(f"Backtesting {symbol} | {len(ohlcv)} candles | ${initial_capital:,.0f} capital")

        for i in range(50, len(ohlcv)):
            window = ohlcv.iloc[:i]
            current_row = ohlcv.iloc[i]
            current_price = Decimal(str(current_row["close"]))
            timestamp = ohlcv.index[i]

            # Build mock ticker
            ticker = {
                "last": float(current_price),
                "bid": float(current_price) * 0.999,
                "ask": float(current_price) * 1.001,
                "quoteVolume": float(current_row.get("volume", 0)) * float(current_price),
            }

            # Get signals
            signals = {}
            signals["momentum"] = await self.momentum.signal(symbol, window, ticker)
            signals["mean_reversion"] = await self.mean_reversion.signal(symbol, window, ticker)
            signals["sentiment"] = {"score": 0.0, "confidence": 0.05, "strategy": "sentiment"}  # No live sentiment in backtest
            signals["arbitrage"] = await self.arbitrage.signal(symbol, ticker)

            # Mock portfolio for meta_ai
            class MockPortfolio:
                positions = {symbol: position} if position else {}

            decision = await self.meta_ai.decide(symbol, signals, MockPortfolio(), ticker)

            # Execute trades
            if decision["action"] == "BUY" and position is None:
                size_usd = capital * Decimal(str(self.settings.max_position_pct))
                size_usd = min(size_usd, capital * Decimal("0.95"))
                quantity = size_usd / current_price
                fee = size_usd * Decimal("0.001")  # 0.1% fee
                cost = size_usd + fee
                if cost <= capital:
                    capital -= cost
                    position = {
                        "symbol": symbol,
                        "entry_price": current_price,
                        "quantity": quantity,
                        "cost_usd": size_usd,
                        "entry_time": timestamp,
                        "entry_reason": decision["reason"],
                    }

            elif position and (
                decision["action"] in ("SELL", "CLOSE")
                or self._stop_loss_hit(position, current_price)
                or self._take_profit_hit(position, current_price)
            ):
                proceeds = position["quantity"] * current_price
                fee = proceeds * Decimal("0.001")
                net_proceeds = proceeds - fee
                pnl = net_proceeds - position["cost_usd"]
                pnl_pct = float(pnl / position["cost_usd"])
                capital += net_proceeds

                exit_reason = decision["reason"]
                if self._stop_loss_hit(position, current_price):
                    exit_reason = "stop_loss"
                elif self._take_profit_hit(position, current_price):
                    exit_reason = "take_profit"

                trades.append({
                    "symbol": symbol,
                    "entry_time": position["entry_time"],
                    "exit_time": timestamp,
                    "entry_price": float(position["entry_price"]),
                    "exit_price": float(current_price),
                    "quantity": float(position["quantity"]),
                    "pnl_usd": float(pnl),
                    "pnl_pct": pnl_pct,
                    "entry_reason": position["entry_reason"],
                    "exit_reason": exit_reason,
                })
                position = None

            # Track equity curve
            position_value = (position["quantity"] * current_price) if position else Decimal("0")
            total_equity = capital + position_value
            equity_curve.append({"timestamp": timestamp, "equity": float(total_equity)})
            peak_equity = max(peak_equity, total_equity)

        # Close any open position at end
        if position:
            final_price = Decimal(str(ohlcv.iloc[-1]["close"]))
            proceeds = position["quantity"] * final_price
            capital += proceeds
            trades.append({
                "symbol": symbol,
                "entry_time": position["entry_time"],
                "exit_time": ohlcv.index[-1],
                "entry_price": float(position["entry_price"]),
                "exit_price": float(final_price),
                "quantity": float(position["quantity"]),
                "pnl_usd": float(proceeds - position["cost_usd"]),
                "pnl_pct": float((proceeds - position["cost_usd"]) / position["cost_usd"]),
                "exit_reason": "end_of_backtest",
                "entry_reason": position["entry_reason"],
            })

        return BacktestResult(
            symbol=symbol,
            initial_capital=initial_capital,
            final_capital=float(capital),
            trades=trades,
            equity_curve=equity_curve,
            settings=self.settings,
        )

    def _stop_loss_hit(self, position: dict, current_price: Decimal) -> bool:
        change = (current_price - position["entry_price"]) / position["entry_price"]
        return change <= -Decimal(str(self.settings.stop_loss_pct))

    def _take_profit_hit(self, position: dict, current_price: Decimal) -> bool:
        change = (current_price - position["entry_price"]) / position["entry_price"]
        return change >= Decimal(str(self.settings.take_profit_pct))


class BacktestResult:
    def __init__(self, symbol, initial_capital, final_capital, trades, equity_curve, settings):
        self.symbol = symbol
        self.initial_capital = initial_capital
        self.final_capital = final_capital
        self.trades = trades
        self.equity_curve = equity_curve

        df = pd.DataFrame(trades) if trades else pd.DataFrame()
        self.metrics = self._compute_metrics(df, equity_curve, initial_capital, final_capital)

    def _compute_metrics(self, df, equity_curve, initial, final) -> dict:
        total_return = (final - initial) / initial
        n_trades = len(df)

        if n_trades == 0:
            return {"total_return_pct": 0, "n_trades": 0, "message": "No trades executed"}

        wins = df[df["pnl_usd"] > 0]
        losses = df[df["pnl_usd"] <= 0]
        win_rate = len(wins) / n_trades

        avg_win = wins["pnl_pct"].mean() if len(wins) > 0 else 0
        avg_loss = losses["pnl_pct"].mean() if len(losses) > 0 else 0
        profit_factor = (wins["pnl_usd"].sum() / abs(losses["pnl_usd"].sum())) if len(losses) > 0 and losses["pnl_usd"].sum() != 0 else float("inf")

        # Sharpe ratio (annualized, assumes hourly candles)
        equity_vals = [e["equity"] for e in equity_curve]
        returns = pd.Series(equity_vals).pct_change().dropna()
        sharpe = (returns.mean() / returns.std() * np.sqrt(8760)) if returns.std() > 0 else 0

        # Max drawdown
        eq_series = pd.Series(equity_vals)
        rolling_max = eq_series.cummax()
        drawdown = (eq_series - rolling_max) / rolling_max
        max_drawdown = drawdown.min()

        # Calmar ratio
        calmar = (total_return / abs(max_drawdown)) if max_drawdown != 0 else 0

        return {
            "total_return_pct": round(total_return * 100, 2),
            "final_capital": round(final, 2),
            "n_trades": n_trades,
            "win_rate": round(win_rate * 100, 2),
            "avg_win_pct": round(avg_win * 100, 2),
            "avg_loss_pct": round(avg_loss * 100, 2),
            "profit_factor": round(profit_factor, 2),
            "sharpe_ratio": round(sharpe, 3),
            "max_drawdown_pct": round(max_drawdown * 100, 2),
            "calmar_ratio": round(calmar, 3),
            "best_trade_pct": round(df["pnl_pct"].max() * 100, 2),
            "worst_trade_pct": round(df["pnl_pct"].min() * 100, 2),
        }

    def print_report(self):
        m = self.metrics
        print("\n" + "=" * 55)
        print(f"  BACKTEST REPORT - {self.symbol}")
        print("=" * 55)
        print(f"  Total Return:    {m.get('total_return_pct', 0):+.2f}%")
        print(f"  Final Capital:   ${m.get('final_capital', 0):,.2f}")
        print(f"  Sharpe Ratio:    {m.get('sharpe_ratio', 0):.3f}")
        print(f"  Max Drawdown:    {m.get('max_drawdown_pct', 0):.2f}%")
        print(f"  Calmar Ratio:    {m.get('calmar_ratio', 0):.3f}")
        print(f"  Win Rate:        {m.get('win_rate', 0):.1f}%")
        print(f"  Profit Factor:   {m.get('profit_factor', 0):.2f}")
        print(f"  # Trades:        {m.get('n_trades', 0)}")
        print(f"  Avg Win:         {m.get('avg_win_pct', 0):+.2f}%")
        print(f"  Avg Loss:        {m.get('avg_loss_pct', 0):+.2f}%")
        print(f"  Best Trade:      {m.get('best_trade_pct', 0):+.2f}%")
        print(f"  Worst Trade:     {m.get('worst_trade_pct', 0):+.2f}%")
        print("=" * 55)

    def to_json(self, path: str):
        out = {
            "symbol": self.symbol,
            "metrics": self.metrics,
            "trades": [
                {**t, "entry_time": str(t["entry_time"]), "exit_time": str(t["exit_time"])}
                for t in self.trades
            ],
            "equity_curve": [
                {"timestamp": str(e["timestamp"]), "equity": e["equity"]}
                for e in self.equity_curve
            ],
        }
        with open(path, "w") as f:
            json.dump(out, f, indent=2)
        log.info(f"Results saved to {path}")


class WalkForwardOptimizer:
    """
    Splits data into in-sample (train) and out-of-sample (test) windows.
    Prevents overfitting by always testing on unseen data.
    Standard in professional quant research.
    """

    def __init__(self, engine: BacktestEngine, n_splits: int = 5):
        self.engine = engine
        self.n_splits = n_splits

    async def run(self, symbol: str, ohlcv: pd.DataFrame, capital: float = 10_000) -> dict:
        n = len(ohlcv)
        split_size = n // (self.n_splits + 1)
        results = []

        log.info(f"Walk-forward optimization: {self.n_splits} splits on {symbol}")

        for i in range(self.n_splits):
            train_end = split_size * (i + 2)
            test_start = train_end
            test_end = min(test_start + split_size, n)

            if test_end <= test_start:
                break

            test_window = ohlcv.iloc[test_start:test_end]
            result = await self.engine.run(symbol, test_window, capital)
            results.append({
                "fold": i + 1,
                "test_start": str(ohlcv.index[test_start]),
                "test_end": str(ohlcv.index[test_end - 1]),
                "metrics": result.metrics,
            })

            log.info(
                f"Fold {i+1}: return={result.metrics.get('total_return_pct',0):+.1f}% "
                f"sharpe={result.metrics.get('sharpe_ratio',0):.2f} "
                f"dd={result.metrics.get('max_drawdown_pct',0):.1f}%"
            )

        # Aggregate
        all_returns = [r["metrics"].get("total_return_pct", 0) for r in results]
        all_sharpes = [r["metrics"].get("sharpe_ratio", 0) for r in results]
        all_dds = [r["metrics"].get("max_drawdown_pct", 0) for r in results]

        summary = {
            "symbol": symbol,
            "n_folds": len(results),
            "avg_return_pct": round(np.mean(all_returns), 2),
            "std_return_pct": round(np.std(all_returns), 2),
            "avg_sharpe": round(np.mean(all_sharpes), 3),
            "avg_max_drawdown_pct": round(np.mean(all_dds), 2),
            "consistency_score": round(len([r for r in all_returns if r > 0]) / len(results), 2),
            "folds": results,
        }

        print(f"\n{'='*55}")
        print(f"  WALK-FORWARD SUMMARY - {symbol}")
        print(f"{'='*55}")
        print(f"  Avg Return:    {summary['avg_return_pct']:+.2f}% +/- {summary['std_return_pct']:.2f}%")
        print(f"  Avg Sharpe:    {summary['avg_sharpe']:.3f}")
        print(f"  Avg Drawdown:  {summary['avg_max_drawdown_pct']:.2f}%")
        print(f"  Consistency:   {summary['consistency_score']*100:.0f}% profitable folds")
        print(f"{'='*55}\n")

        return summary


async def fetch_historical(symbol: str, days: int = 365) -> pd.DataFrame:
    """Fetch historical OHLCV using ccxt or yfinance."""
    try:
        import ccxt.async_support as ccxt
        exchange = ccxt.binance({"enableRateLimit": True})
        since = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)
        all_candles = []
        while True:
            candles = await exchange.fetch_ohlcv(symbol, "1h", since=since, limit=1000)
            if not candles:
                break
            all_candles.extend(candles)
            since = candles[-1][0] + 1
            if len(candles) < 1000:
                break
        await exchange.close()

        df = pd.DataFrame(all_candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("timestamp", inplace=True)
        log.info(f"Fetched {len(df)} hourly candles for {symbol}")
        return df

    except Exception as e:
        log.warning(f"Historical fetch failed: {e} - using synthetic data")
        # Generate synthetic data for testing without API
        import numpy as np
        n = days * 24
        np.random.seed(42)
        closes = 30000 * np.cumprod(1 + np.random.randn(n) * 0.008)
        dates = pd.date_range(end=pd.Timestamp.now(), periods=n, freq="1h")
        return pd.DataFrame({
            "open": closes * 0.999,
            "high": closes * 1.012,
            "low": closes * 0.988,
            "close": closes,
            "volume": np.random.randint(1_000_000, 10_000_000, n).astype(float),
        }, index=dates)


async def main():
    parser = argparse.ArgumentParser(description="APEX Backtester")
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--capital", type=float, default=10_000)
    parser.add_argument("--walk-forward", action="store_true")
    parser.add_argument("--output", default="backtest_results.json")
    args = parser.parse_args()

    settings = Settings.load("config/settings.yaml")
    engine = BacktestEngine(settings)

    ohlcv = await fetch_historical(args.symbol, args.days)

    if args.walk_forward:
        optimizer = WalkForwardOptimizer(engine, n_splits=5)
        await optimizer.run(args.symbol, ohlcv, args.capital)
    else:
        result = await engine.run(args.symbol, ohlcv, args.capital)
        result.print_report()
        result.to_json(args.output)


if __name__ == "__main__":
    asyncio.run(main())
