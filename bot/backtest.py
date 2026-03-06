"""
Backtesting engine.

Downloads historical data from Binance and simulates the strategy
with the same rules as live trading (fees, stops, trailing, regime).

Usage:
  python main.py --backtest --months 6
  python main.py --optimize --months 6
"""

import asyncio
import logging
import itertools
from decimal import Decimal
from datetime import datetime, timedelta
from collections import defaultdict

import numpy as np
import pandas as pd

from bot.data import Market
from bot.portfolio import Portfolio
from bot.risk import position_size_atr, CircuitBreaker
from bot import strategy

log = logging.getLogger("backtest")


class Backtester:
    def __init__(self, config: dict):
        self.config = config
        self.market = Market()

    async def run(self, months: int = 6):
        data, data_htf = await self._download(months)
        if not data:
            return

        result = self._simulate(data, data_htf, self.config)
        self._print_results(result, months)

    async def optimize(self, months: int = 6):
        """Grid search over key parameters."""
        data, data_htf = await self._download(months)
        if not data:
            return

        grid = {
            "atr_sl_mult": [1.5, 2.0, 2.5, 3.0],
            "atr_tp_mult": [3.0, 4.0, 5.0, 6.0],
            "buy_score": [0.25, 0.30, 0.35, 0.40],
            "adx_trend_threshold": [20, 25, 30],
        }

        keys = list(grid.keys())
        combos = list(itertools.product(*grid.values()))
        log.info(f"Optimizing {len(combos)} parameter combinations...")

        results = []
        for i, combo in enumerate(combos):
            params = dict(zip(keys, combo))
            cfg = dict(self.config)
            cfg["atr_sl_mult"] = params["atr_sl_mult"]
            cfg["atr_tp_mult"] = params["atr_tp_mult"]
            cfg["buy_score"] = params["buy_score"]
            cfg["sell_score"] = -params["buy_score"] + 0.05  # keep offset
            cfg["adx_trend_threshold"] = params["adx_trend_threshold"]

            result = self._simulate(data, data_htf, cfg, quiet=True)
            if result["total_trades"] > 0:
                results.append({**params, **result})

            if (i + 1) % 20 == 0:
                log.info(f"  {i + 1}/{len(combos)} done...")

        # Sort by profit factor
        results.sort(key=lambda r: r.get("profit_factor", 0), reverse=True)

        print("\n" + "=" * 90)
        print("  OPTIMIZATION RESULTS")
        print("=" * 90)

        from tabulate import tabulate
        rows = []
        for r in results[:20]:
            rows.append([
                r["atr_sl_mult"],
                r["atr_tp_mult"],
                r["buy_score"],
                r["adx_trend_threshold"],
                r["total_trades"],
                f"{r['win_rate']:.0f}%",
                f"{r['profit_factor']:.2f}",
                f"{r['sharpe']:.2f}",
                f"{r['max_dd']:.1f}%",
                f"{'+'if r['total_pnl']>=0 else ''}${r['total_pnl']:,.2f}",
            ])

        print(tabulate(
            rows,
            headers=["SL mult", "TP mult", "Buy score", "ADX thresh", "Trades", "Win%", "PF", "Sharpe", "MaxDD", "P&L"],
            tablefmt="simple",
            stralign="right",
        ))
        print()

        if results:
            best = results[0]
            print("  Best parameters:")
            print(f"    atr_sl_mult: {best['atr_sl_mult']}")
            print(f"    atr_tp_mult: {best['atr_tp_mult']}")
            print(f"    buy_score: {best['buy_score']}")
            print(f"    adx_trend_threshold: {best['adx_trend_threshold']}")
            print(f"    Profit factor: {best['profit_factor']:.2f}")
            print(f"    Sharpe: {best['sharpe']:.2f}")
            print()

    async def _download(self, months: int) -> tuple[dict, dict]:
        await self.market.connect()

        symbols = self.config["symbols"]
        tf = self.config.get("timeframe", "1h")
        htf = self.config.get("htf", "4h")

        since = datetime.utcnow() - timedelta(days=months * 30)
        since_ms = int(since.timestamp() * 1000)

        log.info(f"Downloading {months} months of data for {len(symbols)} symbols...")

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

        return data, data_htf

    def _simulate(self, data: dict, data_htf: dict, config: dict, quiet: bool = False) -> dict:
        capital = config.get("backtest_initial_capital", config.get("capital", 1000))

        all_times = set()
        for df in data.values():
            all_times.update(df.index)
        sorted_times = sorted(all_times)

        if not quiet:
            log.info(f"Simulating from {sorted_times[0]} to {sorted_times[-1]}...")

        portfolio = Portfolio(capital)
        circuit_breaker = CircuitBreaker(max_consecutive=config.get("circuit_breaker", 3))
        trades = []
        daily_equity = {}  # date -> equity value
        daily_pnl = Decimal("0")
        current_day = None
        signals_count = {"BUY": 0, "SELL": 0, "HOLD": 0}

        buy_score = config.get("buy_score", strategy.BUY_SCORE)
        sell_score = config.get("sell_score", strategy.SELL_SCORE)

        for i, ts in enumerate(sorted_times):
            day = ts.date() if hasattr(ts, "date") else ts
            if current_day != day:
                # Record daily equity for Sharpe calculation
                if current_day is not None:
                    daily_equity[current_day] = float(portfolio.value)
                current_day = day
                daily_pnl = Decimal("0")

            max_loss = Decimal(str(capital)) * Decimal(str(config.get("max_daily_loss", 0.05)))
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

                df_slice = df.iloc[:loc + 1]
                price = Decimal(str(float(df_slice["close"].iloc[-1])))

                portfolio.update_price(sym, price)

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
                        "time": str(ts), "symbol": sym, "action": "SELL",
                        "entry": entry, "exit": float(price),
                        "pnl": float(pnl), "reason": stop_reason,
                    })
                    continue

                df_htf_slice = None
                if sym in data_htf:
                    htf_df = data_htf[sym]
                    htf_mask = htf_df.index <= ts
                    if htf_mask.sum() >= 50:
                        df_htf_slice = htf_df[htf_mask]

                sig = strategy.signal(df_slice, config=config, df_htf=df_htf_slice)
                signals_count[sig["action"]] += 1

                # Override action based on configurable thresholds
                action = sig["action"]
                if sig["score"] >= buy_score:
                    action = "BUY"
                elif sig["score"] <= sell_score:
                    action = "SELL"
                else:
                    action = "HOLD"

                if action == "BUY" and sym not in portfolio.positions:
                    if portfolio.is_on_cooldown(sym):
                        continue
                    if len(portfolio.positions) >= config.get("max_positions", 5):
                        continue

                    atr_val = sig.get("atr", float(price) * 0.02)
                    size = position_size_atr(
                        cash=portfolio.cash,
                        risk_usd=config.get("risk_per_trade_usd", 15),
                        atr_value=atr_val,
                        price=float(price),
                        atr_sl_mult=config.get("atr_sl_mult", 2.0),
                    )
                    if size is None:
                        continue

                    ok = portfolio.buy(sym, price, size, atr=atr_val, config=config)
                    if ok:
                        trades.append({
                            "time": str(ts), "symbol": sym, "action": "BUY",
                            "entry": float(price), "exit": None,
                            "pnl": None, "reason": sig["reason"],
                        })

                elif action == "SELL" and sym in portfolio.positions:
                    entry = float(portfolio.positions[sym]["entry"])
                    pnl = portfolio.sell(sym, price, sig["reason"])
                    daily_pnl += pnl

                    if pnl > 0:
                        circuit_breaker.record_win()

                    trades.append({
                        "time": str(ts), "symbol": sym, "action": "SELL",
                        "entry": entry, "exit": float(price),
                        "pnl": float(pnl), "reason": sig["reason"],
                    })

            if i % 24 == 0:
                daily_equity[day] = float(portfolio.value)

        # Record final day
        if current_day is not None:
            daily_equity[current_day] = float(portfolio.value)

        # Close remaining positions
        for sym in list(portfolio.positions.keys()):
            if sym in data:
                last_price = Decimal(str(float(data[sym]["close"].iloc[-1])))
                entry = float(portfolio.positions[sym]["entry"])
                pnl = portfolio.sell(sym, last_price, "backtest_end")
                trades.append({
                    "time": str(sorted_times[-1]), "symbol": sym, "action": "SELL",
                    "entry": entry, "exit": float(last_price),
                    "pnl": float(pnl), "reason": "backtest_end",
                })

        return self._calc_metrics(trades, daily_equity, capital, signals_count, data)

    def _calc_metrics(self, trades, daily_equity, capital, signals_count, data) -> dict:
        closed = [t for t in trades if t["pnl"] is not None]

        if not closed:
            return {"total_trades": 0, "total_pnl": 0, "win_rate": 0, "sharpe": 0,
                    "max_dd": 0, "profit_factor": 0, "max_consecutive_losses": 0,
                    "trades": trades, "daily_equity": daily_equity,
                    "signals_count": signals_count, "capital": capital, "data": data}

        wins = [t for t in closed if t["pnl"] > 0]
        losses = [t for t in closed if t["pnl"] <= 0]
        total_pnl = sum(t["pnl"] for t in closed)
        win_rate = len(wins) / len(closed) * 100
        avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0
        gross_win = sum(t["pnl"] for t in wins) if wins else 0
        gross_loss = sum(t["pnl"] for t in losses) if losses else 0
        profit_factor = abs(gross_win / gross_loss) if gross_loss != 0 else float("inf")

        # Max drawdown from equity curve
        peak = capital
        max_dd = 0
        running = capital
        for t in closed:
            running += t["pnl"]
            peak = max(peak, running)
            dd = (peak - running) / peak * 100
            max_dd = max(max_dd, dd)

        # Sharpe ratio from DAILY returns (correct method)
        sorted_dates = sorted(daily_equity.keys())
        if len(sorted_dates) >= 2:
            daily_values = [daily_equity[d] for d in sorted_dates]
            daily_returns = []
            for j in range(1, len(daily_values)):
                if daily_values[j - 1] > 0:
                    daily_returns.append(daily_values[j] / daily_values[j - 1] - 1)
            if daily_returns and np.std(daily_returns) > 0:
                sharpe = np.mean(daily_returns) / np.std(daily_returns) * np.sqrt(365)
            else:
                sharpe = 0
        else:
            sharpe = 0

        # Max consecutive losses
        max_consec = 0
        current_consec = 0
        for t in closed:
            if t["pnl"] <= 0:
                current_consec += 1
                max_consec = max(max_consec, current_consec)
            else:
                current_consec = 0

        avg_rr = abs(avg_win / avg_loss) if avg_loss != 0 else 0

        return {
            "total_trades": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "total_pnl": total_pnl,
            "win_rate": win_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "avg_rr": avg_rr,
            "profit_factor": profit_factor,
            "sharpe": sharpe,
            "max_dd": max_dd,
            "max_consecutive_losses": max_consec,
            "trades": trades,
            "closed": closed,
            "daily_equity": daily_equity,
            "signals_count": signals_count,
            "capital": capital,
            "data": data,
        }

    def _print_results(self, r: dict, months: int):
        if r["total_trades"] == 0:
            log.info("No trades executed during backtest period.")
            return

        capital = r["capital"]
        final_value = capital + r["total_pnl"]
        pnl_pct = (final_value - capital) / capital * 100
        pnl_sign = "+" if r["total_pnl"] >= 0 else ""

        print("\n" + "=" * 60)
        print(f"  BACKTEST RESULTS — {months} months")
        print("=" * 60)
        print(f"  Initial capital:       ${capital:,.2f}")
        print(f"  Final value:           ${final_value:,.2f}")
        print(f"  Total P&L:             {pnl_sign}${r['total_pnl']:,.2f} ({pnl_sign}{pnl_pct:.1f}%)")
        print("-" * 60)
        print(f"  Total trades:          {r['total_trades']}")
        print(f"  Wins:                  {r['wins']}")
        print(f"  Losses:                {r['losses']}")
        print(f"  Win rate:              {r['win_rate']:.1f}%")
        print(f"  Avg win:               +${r['avg_win']:,.2f}")
        print(f"  Avg loss:              -${abs(r['avg_loss']):,.2f}")
        print(f"  Avg R/R:               {r['avg_rr']:.2f}")
        print(f"  Profit factor:         {r['profit_factor']:.2f}")
        print("-" * 60)
        print(f"  Sharpe ratio:          {r['sharpe']:.2f}")
        print(f"  Max drawdown:          {r['max_dd']:.1f}%")
        print(f"  Max consec. losses:    {r['max_consecutive_losses']}")
        print("-" * 60)

        closed = r["closed"]
        sl_trades = [t for t in closed if t["reason"] == "stop_loss"]
        tp_trades = [t for t in closed if t["reason"] == "take_profit"]
        sig_sells = [t for t in closed if t["reason"] not in ("stop_loss", "take_profit", "backtest_end")]
        symbols_traded = set(t["symbol"] for t in closed)

        print(f"  Stop-losses:           {len(sl_trades)}")
        print(f"  Take-profits:          {len(tp_trades)}")
        print(f"  Signal sells:          {len(sig_sells)}")
        print(f"  Symbols traded:        {', '.join(sorted(s.replace('/USDT','') for s in symbols_traded))}")
        print("-" * 60)
        sc = r["signals_count"]
        print(f"  Signals generated:     BUY={sc['BUY']} SELL={sc['SELL']} HOLD={sc['HOLD']}")
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

        # Bull vs Bear market breakdown
        self._print_regime_breakdown(r)
        print()

    def _print_regime_breakdown(self, r: dict):
        """Detect bull/bear periods based on BTC price and show separate metrics."""
        data = r["data"]
        closed = r["closed"]

        if "BTC/USDT" not in data or not closed:
            return

        btc = data["BTC/USDT"]["close"]

        # Classify each trade's timestamp into bull/bear
        # Bear = BTC dropped >20% from its recent high over trailing 30 days
        bull_trades = []
        bear_trades = []

        for t in closed:
            try:
                trade_time = pd.Timestamp(t["time"])
                mask = btc.index <= trade_time
                if mask.sum() < 30:
                    bull_trades.append(t)
                    continue

                recent = btc[mask].iloc[-720:]  # ~30 days of 1h candles
                peak = recent.max()
                current = recent.iloc[-1]
                drawdown = (peak - current) / peak

                if drawdown > 0.20:
                    bear_trades.append(t)
                else:
                    bull_trades.append(t)
            except Exception:
                bull_trades.append(t)

        print("\n  Bull/Bear market breakdown:")
        from tabulate import tabulate
        rows = []
        for label, subset in [("Bull", bull_trades), ("Bear", bear_trades)]:
            if not subset:
                rows.append([label, 0, "-", "-", "$0"])
                continue
            wins = sum(1 for t in subset if t["pnl"] > 0)
            total_pnl = sum(t["pnl"] for t in subset)
            wr = wins / len(subset) * 100
            gross_w = sum(t["pnl"] for t in subset if t["pnl"] > 0)
            gross_l = sum(t["pnl"] for t in subset if t["pnl"] <= 0)
            pf = abs(gross_w / gross_l) if gross_l != 0 else float("inf")
            rows.append([
                label,
                len(subset),
                f"{wr:.0f}%",
                f"{pf:.2f}",
                f"{'+'if total_pnl>=0 else ''}${total_pnl:,.2f}",
            ])

        print(tabulate(rows, headers=["Regime", "Trades", "Win%", "PF", "P&L"], tablefmt="simple", stralign="right"))
