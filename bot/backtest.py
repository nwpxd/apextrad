"""
Backtesting engine.

Downloads historical data from Binance and simulates the strategy
with the same rules as live trading (fees, stops, trailing, regime).

Usage: python main.py --backtest --months 6
"""

import asyncio
import logging
from decimal import Decimal
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from bot.data import Market
from bot.portfolio import Portfolio
from bot.risk import position_size_atr, CircuitBreaker, FEE_RATE
from bot import strategy

log = logging.getLogger("backtest")


class Backtester:
    def __init__(self, config: dict):
        self.config = config
        self.market = Market()

    async def run(self, months: int = 6):
        await self.market.connect()

        symbols = self.config["symbols"]
        tf = self.config.get("timeframe", "1h")
        htf = self.config.get("htf", "4h")
        capital = self.config.get("backtest_initial_capital", self.config.get("capital", 1000))

        since = datetime.utcnow() - timedelta(days=months * 30)
        since_ms = int(since.timestamp() * 1000)

        log.info(f"Downloading {months} months of data for {len(symbols)} symbols...")

        # Download all data
        data = {}
        data_htf = {}
        for sym in symbols:
            log.info(f"  Fetching {sym} ({tf})...")
            df = await self.market.ohlcv_history(sym, tf, since_ms)
            if df is not None and len(df) >= 50:
                data[sym] = df
                log.info(f"    {len(df)} candles loaded")

            log.info(f"  Fetching {sym} ({htf})...")
            df_h = await self.market.ohlcv_history(sym, htf, since_ms)
            if df_h is not None:
                data_htf[sym] = df_h
            await asyncio.sleep(0.3)

        await self.market.close()

        if not data:
            log.error("No data downloaded. Check your internet connection.")
            return

        # Find common time range
        all_times = set()
        for df in data.values():
            all_times.update(df.index)
        sorted_times = sorted(all_times)

        log.info(f"Simulating from {sorted_times[0]} to {sorted_times[-1]}...")
        log.info(f"Total candles across all symbols: {sum(len(df) for df in data.values())}")

        # Run simulation
        portfolio = Portfolio(capital)
        circuit_breaker = CircuitBreaker(
            max_consecutive=self.config.get("circuit_breaker", 3),
        )
        trades = []
        equity_curve = []
        daily_pnl = Decimal("0")
        current_day = None
        signals_count = {"BUY": 0, "SELL": 0, "HOLD": 0}

        for i, ts in enumerate(sorted_times):
            # Daily reset
            day = ts.date() if hasattr(ts, "date") else ts
            if current_day != day:
                current_day = day
                daily_pnl = Decimal("0")

            # Daily loss check
            max_loss = Decimal(str(capital)) * Decimal(str(self.config.get("max_daily_loss", 0.05)))
            if daily_pnl < -max_loss:
                continue

            if circuit_breaker.is_paused():
                continue

            for sym in data:
                df = data[sym]
                if ts not in df.index:
                    continue

                loc = df.index.get_loc(ts)
                if loc < 50:
                    continue

                # Get slice up to current candle
                df_slice = df.iloc[:loc + 1]
                price = Decimal(str(float(df_slice["close"].iloc[-1])))

                # Update existing positions
                portfolio.update_price(sym, price)

                # Check stops
                stop_reason = portfolio.check_stops(sym, price)
                if stop_reason:
                    entry = float(portfolio.positions[sym]["entry"])
                    pnl = portfolio.sell(sym, price, stop_reason)
                    daily_pnl += pnl

                    if stop_reason == "stop_loss":
                        circuit_breaker.record_stop_loss()
                    elif pnl > 0:
                        circuit_breaker.record_win()

                    trades.append({
                        "time": str(ts),
                        "symbol": sym,
                        "action": "SELL",
                        "entry": entry,
                        "exit": float(price),
                        "pnl": float(pnl),
                        "reason": stop_reason,
                    })
                    continue

                # Get HTF data
                df_htf = None
                if sym in data_htf:
                    htf_df = data_htf[sym]
                    htf_mask = htf_df.index <= ts
                    if htf_mask.sum() >= 50:
                        df_htf = htf_df[htf_mask]

                # Strategy signal
                sig = strategy.signal(df_slice, config=self.config, df_htf=df_htf)
                signals_count[sig["action"]] += 1

                # Execute buy
                if sig["action"] == "BUY" and sym not in portfolio.positions:
                    if portfolio.is_on_cooldown(sym):
                        continue
                    if len(portfolio.positions) >= self.config.get("max_positions", 5):
                        continue

                    atr_val = sig.get("atr", float(price) * 0.02)
                    size = position_size_atr(
                        cash=portfolio.cash,
                        risk_usd=self.config.get("risk_per_trade_usd", 15),
                        atr_value=atr_val,
                        price=float(price),
                        atr_sl_mult=self.config.get("atr_sl_mult", 2.0),
                    )
                    if size is None:
                        continue

                    ok = portfolio.buy(sym, price, size, atr=atr_val, config=self.config)
                    if ok:
                        trades.append({
                            "time": str(ts),
                            "symbol": sym,
                            "action": "BUY",
                            "entry": float(price),
                            "exit": None,
                            "pnl": None,
                            "reason": sig["reason"],
                        })

                # Execute strategy sell
                elif sig["action"] == "SELL" and sym in portfolio.positions:
                    entry = float(portfolio.positions[sym]["entry"])
                    pnl = portfolio.sell(sym, price, sig["reason"])
                    daily_pnl += pnl

                    if pnl > 0:
                        circuit_breaker.record_win()

                    trades.append({
                        "time": str(ts),
                        "symbol": sym,
                        "action": "SELL",
                        "entry": entry,
                        "exit": float(price),
                        "pnl": float(pnl),
                        "reason": sig["reason"],
                    })

            # Record equity every 24 candles (1 day for 1h tf)
            if i % 24 == 0:
                equity_curve.append({
                    "time": str(ts),
                    "value": float(portfolio.value),
                })

        # Close remaining positions at last price
        for sym in list(portfolio.positions.keys()):
            if sym in data:
                last_price = Decimal(str(float(data[sym]["close"].iloc[-1])))
                entry = float(portfolio.positions[sym]["entry"])
                pnl = portfolio.sell(sym, last_price, "backtest_end")
                trades.append({
                    "time": str(sorted_times[-1]),
                    "symbol": sym,
                    "action": "SELL",
                    "entry": entry,
                    "exit": float(last_price),
                    "pnl": float(pnl),
                    "reason": "backtest_end",
                })

        # Calculate metrics
        self._print_results(trades, equity_curve, capital, months, signals_count)

    def _print_results(self, trades, equity_curve, capital, months, signals_count):
        closed = [t for t in trades if t["pnl"] is not None]
        if not closed:
            log.info("No trades executed during backtest period.")
            return

        wins = [t for t in closed if t["pnl"] > 0]
        losses = [t for t in closed if t["pnl"] <= 0]
        pnls = [t["pnl"] for t in closed]
        total_pnl = sum(pnls)
        final_value = capital + total_pnl

        win_rate = len(wins) / len(closed) * 100 if closed else 0
        avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0
        profit_factor = abs(sum(t["pnl"] for t in wins) / sum(t["pnl"] for t in losses)) if losses and sum(t["pnl"] for t in losses) != 0 else float("inf")

        # Max drawdown
        peak = capital
        max_dd = 0
        running = capital
        for t in closed:
            running += t["pnl"]
            peak = max(peak, running)
            dd = (peak - running) / peak * 100
            max_dd = max(max_dd, dd)

        # Sharpe ratio (annualized, assuming ~365 trading days for crypto)
        if len(pnls) > 1:
            returns = np.array(pnls) / capital
            sharpe = np.mean(returns) / np.std(returns) * np.sqrt(365) if np.std(returns) > 0 else 0
        else:
            sharpe = 0

        # Average R/R
        avg_rr = abs(avg_win / avg_loss) if avg_loss != 0 else 0

        # Symbols traded
        symbols_traded = set(t["symbol"] for t in closed)

        # Stop-loss breakdown
        sl_trades = [t for t in closed if t["reason"] == "stop_loss"]
        tp_trades = [t for t in closed if t["reason"] == "take_profit"]
        sig_sells = [t for t in closed if t["reason"] not in ("stop_loss", "take_profit", "backtest_end")]

        print("\n" + "=" * 60)
        print(f"  BACKTEST RESULTS — {months} months")
        print("=" * 60)
        print(f"  Initial capital:    ${capital:,.2f}")
        print(f"  Final value:        ${final_value:,.2f}")
        pnl_pct = (final_value - capital) / capital * 100
        pnl_sign = "+" if total_pnl >= 0 else ""
        print(f"  Total P&L:          {pnl_sign}${total_pnl:,.2f} ({pnl_sign}{pnl_pct:.1f}%)")
        print("-" * 60)
        print(f"  Total trades:       {len(closed)}")
        print(f"  Wins:               {len(wins)}")
        print(f"  Losses:             {len(losses)}")
        print(f"  Win rate:           {win_rate:.1f}%")
        print(f"  Avg win:            +${avg_win:,.2f}")
        print(f"  Avg loss:           -${abs(avg_loss):,.2f}")
        print(f"  Avg R/R:            {avg_rr:.2f}")
        print(f"  Profit factor:      {profit_factor:.2f}")
        print("-" * 60)
        print(f"  Sharpe ratio:       {sharpe:.2f}")
        print(f"  Max drawdown:       {max_dd:.1f}%")
        print("-" * 60)
        print(f"  Stop-losses:        {len(sl_trades)}")
        print(f"  Take-profits:       {len(tp_trades)}")
        print(f"  Signal sells:       {len(sig_sells)}")
        print(f"  Symbols traded:     {', '.join(sorted(s.replace('/USDT','') for s in symbols_traded))}")
        print("-" * 60)
        print(f"  Signals generated:  BUY={signals_count['BUY']} SELL={signals_count['SELL']} HOLD={signals_count['HOLD']}")
        print("=" * 60)

        # Per-symbol breakdown
        from tabulate import tabulate
        rows = []
        for sym in sorted(symbols_traded):
            sym_trades = [t for t in closed if t["symbol"] == sym]
            sym_wins = sum(1 for t in sym_trades if t["pnl"] > 0)
            sym_pnl = sum(t["pnl"] for t in sym_trades)
            sym_wr = sym_wins / len(sym_trades) * 100 if sym_trades else 0
            rows.append([
                sym.replace("/USDT", ""),
                len(sym_trades),
                f"{sym_wr:.0f}%",
                f"{'+'if sym_pnl>=0 else ''}${sym_pnl:,.2f}",
            ])

        print("\n  Per-symbol breakdown:")
        print(tabulate(rows, headers=["Symbol", "Trades", "Win%", "P&L"], tablefmt="simple", stralign="right"))
        print()
