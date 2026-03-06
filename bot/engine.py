"""Trading engine. Connects everything. Runs the loop."""

import asyncio
import logging
from collections import deque
from decimal import Decimal
from datetime import datetime, date

from bot.data import Market
from bot.portfolio import Portfolio
from bot.tracker import Tracker
from bot import strategy

log = logging.getLogger("engine")


class Engine:
    def __init__(self, config: dict):
        self.config = config
        self.market = Market()
        self.portfolio = Portfolio(config["capital"])
        self.tracker = Tracker()
        self.running = False

        # State
        self.daily_pnl = Decimal("0")
        self.trades_today = 0
        self._last_reset = date.today()

        # Signal log for dashboard
        self.signals: deque = deque(maxlen=50)

    async def start(self):
        self.running = True
        await self.market.connect()
        log.info("Engine started")

        tasks = [
            asyncio.create_task(self._loop()),
            asyncio.create_task(self._monitor()),
        ]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass

    async def stop(self):
        self.running = False
        await self.market.close()
        log.info("Engine stopped")

    async def _loop(self):
        interval = self.config.get("scan_interval", 60)
        symbols = self.config["symbols"]

        while self.running:
            self._reset_daily()

            # Check daily loss limit
            max_loss = self.portfolio.initial * Decimal(str(self.config.get("max_daily_loss", 0.05)))
            if self.daily_pnl < -max_loss:
                log.warning(f"Daily loss limit hit (${self.daily_pnl:,.2f}). Pausing.")
                await asyncio.sleep(3600)
                continue

            log.info(f"Scanning {len(symbols)} symbols...")

            for symbol in symbols:
                if not self.running:
                    break
                try:
                    await self._evaluate(symbol)
                except Exception as e:
                    log.warning(f"{symbol}: {e}")

            await asyncio.sleep(interval)

    async def _evaluate(self, symbol: str):
        # Get data
        df = await self.market.ohlcv(symbol, self.config.get("timeframe", "1h"), limit=100)
        if df is None or len(df) < 50:
            return

        price_now = await self.market.price(symbol)
        if price_now is None:
            return

        price = Decimal(str(price_now))
        self.portfolio.update_price(symbol, price)

        # Check stops first
        stop_reason = self.portfolio.check_stops(symbol, price)
        if stop_reason:
            pnl = self.portfolio.sell(symbol, price, stop_reason)
            self._record_sell(symbol, price, pnl, stop_reason)
            return

        # Run strategy
        sig = strategy.signal(df)

        # Log signal
        self.signals.append({
            "time": datetime.now(),
            "symbol": symbol,
            "action": sig["action"],
            "score": sig["score"],
            "reason": sig["reason"],
        })

        if sig["action"] == "BUY" and symbol not in self.portfolio.positions:
            self._try_buy(symbol, price, sig)

        elif sig["action"] == "SELL" and symbol in self.portfolio.positions:
            pnl = self.portfolio.sell(symbol, price, sig["reason"])
            self._record_sell(symbol, price, pnl, sig["reason"])

    def _try_buy(self, symbol: str, price: Decimal, sig: dict):
        from bot.risk import position_size

        size = position_size(
            cash=self.portfolio.cash,
            portfolio_value=self.portfolio.value,
            num_positions=len(self.portfolio.positions),
            max_positions=self.config.get("max_positions", 5),
            risk_per_trade=self.config.get("risk_per_trade", 0.05),
        )
        if size is None:
            return

        self.portfolio.buy(
            symbol, price, size,
            stop_pct=self.config.get("stop_loss", 0.02),
            tp_pct=self.config.get("take_profit", 0.04),
        )
        self.trades_today += 1

    def _record_sell(self, symbol: str, price: Decimal, pnl: Decimal, reason: str):
        self.daily_pnl += pnl
        self.trades_today += 1
        self.tracker.record(
            symbol=symbol,
            entry=0,  # already logged in portfolio
            exit_price=float(price),
            pnl=float(pnl),
            reason=reason,
        )

    def _reset_daily(self):
        today = date.today()
        if today != self._last_reset:
            log.info("New day - counters reset")
            self.daily_pnl = Decimal("0")
            self.trades_today = 0
            self._last_reset = today

    async def _monitor(self):
        while self.running:
            await asyncio.sleep(300)
            v = self.portfolio.value
            n = len(self.portfolio.positions)
            log.info(f"Value: ${v:,.2f} | Positions: {n} | Daily PnL: ${self.daily_pnl:+,.2f}")
