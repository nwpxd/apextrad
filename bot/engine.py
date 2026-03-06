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
        self.market = Market(
            api_key=config.get("binance_api_key", ""),
            secret=config.get("binance_secret", ""),
            live=config.get("live", False),
        )
        self.portfolio = Portfolio(config["capital"])
        self.tracker = Tracker()
        self.running = False
        self.live = config.get("live", False)

        # State
        self.daily_pnl = Decimal("0")
        self.trades_today = 0
        self._last_reset = date.today()

        # Signal log for dashboard
        self.signals: deque = deque(maxlen=50)

    async def start(self):
        self.running = True
        await self.market.connect()

        mode = "LIVE" if self.live else "PAPER"
        log.info(f"Engine started [{mode}]")

        if self.live and not self.config.get("binance_api_key"):
            log.error("LIVE mode requires BINANCE_API_KEY and BINANCE_SECRET in .env")
            self.running = False
            return

        tasks = [
            asyncio.create_task(self._loop()),
            asyncio.create_task(self._stop_checker()),
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
        """Main trading loop."""
        interval = self.config.get("scan_interval", 60)
        symbols = self.config["symbols"]

        while self.running:
            self._reset_daily()

            # Daily loss limit
            max_loss = self.portfolio.initial * Decimal(str(self.config.get("max_daily_loss", 0.05)))
            if self.daily_pnl < -max_loss:
                log.warning(f"Daily loss limit hit (${self.daily_pnl:,.2f}). Pausing 1h.")
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

    async def _stop_checker(self):
        """Check stop-losses every 15 seconds (faster than main loop)."""
        while self.running:
            for symbol in list(self.portfolio.positions.keys()):
                try:
                    price_now = await self.market.price(symbol)
                    if price_now is None:
                        continue
                    price = Decimal(str(price_now))
                    self.portfolio.update_price(symbol, price)

                    stop_reason = self.portfolio.check_stops(symbol, price)
                    if stop_reason:
                        await self._execute_sell(symbol, price, stop_reason)
                except Exception as e:
                    log.debug(f"Stop check error {symbol}: {e}")
            await asyncio.sleep(15)

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

        # BUY
        if sig["action"] == "BUY" and symbol not in self.portfolio.positions:
            if self.portfolio.is_on_cooldown(symbol):
                return
            await self._execute_buy(symbol, price, sig)

        # SELL (strategy says sell)
        elif sig["action"] == "SELL" and symbol in self.portfolio.positions:
            await self._execute_sell(symbol, price, sig["reason"])

    async def _execute_buy(self, symbol: str, price: Decimal, sig: dict):
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

        atr_val = sig.get("atr", float(price) * 0.02)

        ok = self.portfolio.buy(symbol, price, size, atr=atr_val)
        if not ok:
            return

        self.trades_today += 1

        # Execute real order in live mode
        if self.live:
            pos = self.portfolio.positions.get(symbol)
            if pos:
                await self.market.market_buy(symbol, float(pos["quantity"]))

    async def _execute_sell(self, symbol: str, price: Decimal, reason: str):
        pos = self.portfolio.positions.get(symbol)
        if not pos:
            return

        quantity = float(pos["quantity"])
        entry = float(pos["entry"])

        pnl = self.portfolio.sell(symbol, price, reason)
        self.daily_pnl += pnl
        self.trades_today += 1

        self.tracker.record(
            symbol=symbol,
            entry=entry,
            exit_price=float(price),
            pnl=float(pnl),
            reason=reason,
        )

        # Execute real order in live mode
        if self.live:
            await self.market.market_sell(symbol, quantity)

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
