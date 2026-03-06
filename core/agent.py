"""
TradingAgent - The central AI brain that runs the show.
Orchestrates all strategies, manages risk, executes trades.
"""

import asyncio
import logging
from decimal import Decimal
from datetime import datetime, date
from typing import Dict, List, Optional, Callable

from config.settings import Settings
from core.portfolio import Portfolio
from core.risk_manager import RiskManager
from core.market_data import MarketDataHub
from core.performance import PerformanceTracker
from core.orderbook import OrderBookAnalyzer
from core.onchain import OnChainAnalyzer
from strategies.momentum import MomentumStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.sentiment import SentimentStrategy
from strategies.arbitrage import ArbitrageStrategy
from strategies.meta_ai import MetaAI
from connectors.crypto_connector import CryptoConnector
from connectors.stock_connector import StockConnector

log = logging.getLogger("Agent")


class TradingAgent:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.running = False

        # Core systems
        self.portfolio = Portfolio(settings)
        self.risk = RiskManager(settings, self.portfolio)
        self.market_data = MarketDataHub(settings)
        self.performance = PerformanceTracker(float(settings.spending_limit))
        self.orderbook = OrderBookAnalyzer(settings)
        self.onchain = OnChainAnalyzer(settings)

        # Connectors (exchanges / brokers)
        self.crypto_conn = CryptoConnector(settings)
        self.stock_conn = StockConnector(settings)

        # Strategies
        self.momentum = MomentumStrategy(settings)
        self.mean_reversion = MeanReversionStrategy(settings)
        self.sentiment = SentimentStrategy(settings)
        self.arbitrage = ArbitrageStrategy(settings)
        self.meta_ai = MetaAI(settings)

        # Daily tracking
        self.daily_pnl: Decimal = Decimal("0")
        self.trade_count_today: int = 0
        self.last_reset: date = date.today()

        # TUI callback for signals (set by TUI if active)
        self.on_signal: Optional[Callable] = None
        # Last regime detected (for TUI display)
        self.current_regime: str = "unknown"

    async def run(self):
        """Main event loop."""
        self.running = True
        log.info("Agent online - initializing systems...")

        await self._init_all()
        log.info("[OK] All systems initialized. Trading loop starting.")

        tasks = [
            asyncio.create_task(self._trading_loop()),
            asyncio.create_task(self._sentiment_loop()),
            asyncio.create_task(self._rebalance_loop()),
            asyncio.create_task(self._monitor_loop()),
        ]

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            log.info("Agent tasks cancelled.")

    async def _init_all(self):
        await self.market_data.connect()
        await self.crypto_conn.connect()
        await self.stock_conn.connect()
        await self.onchain.connect()
        await self.orderbook.connect(self.market_data._exchange)
        await self.portfolio.sync(self.crypto_conn, self.stock_conn)
        log.info(f"Portfolio value: ${await self.portfolio.total_value_usd():,.2f}")
        log.info(f"Spending limit: ${self.settings.spending_limit:,.2f}")

    async def _trading_loop(self):
        """Main loop - scan markets and act on signals."""
        while self.running:
            try:
                self._reset_daily_if_needed()
                await self._check_daily_loss_limit()
                await self._scan_and_trade()
            except Exception as e:
                log.error(f"Trading loop error: {e}", exc_info=True)
            await asyncio.sleep(self.settings.scan_interval_sec)

    async def _scan_and_trade(self):
        symbols = self._get_all_symbols()
        log.info(f"Scanning {len(symbols)} symbols...")

        for symbol in symbols:
            if not self.running:
                break
            try:
                await self._evaluate_symbol(symbol)
            except Exception as e:
                log.warning(f"Error evaluating {symbol}: {e}")

    async def _evaluate_symbol(self, symbol: str):
        """Run all strategies on a symbol, let meta-AI decide action."""
        # Fetch market data
        ohlcv = await self.market_data.get_ohlcv(symbol, "15m", limit=100)
        if ohlcv is None:
            return
        if len(ohlcv) < 20:
            return

        ticker = await self.market_data.get_ticker(symbol)
        if ticker is None:
            return

        current_price = Decimal(str(ticker["last"]))

        # Update position price if we hold it
        self.portfolio.update_price(symbol, current_price)

        # Inline stop-loss / take-profit check BEFORE running strategies
        if symbol in self.portfolio.positions:
            pos = self.portfolio.positions[symbol]
            entry = pos["entry_price"]
            pct_change = (current_price - entry) / entry
            if pct_change <= -Decimal(str(self.settings.stop_loss_pct)):
                log.warning(f"[SL] Stop-loss on {symbol} ({pct_change:.1%})")
                await self._execute_sell(symbol, current_price, {"reason": "stop_loss"})
                return
            if pct_change >= Decimal(str(self.settings.take_profit_pct)):
                log.info(f"[TP] Take-profit on {symbol} ({pct_change:.1%})")
                await self._execute_sell(symbol, current_price, {"reason": "take_profit"})
                return

        # Check liquidity
        vol = ticker.get("quoteVolume") or ticker.get("baseVolume", 0) or 0
        if vol < self.settings.min_liquidity_usd:
            return

        # Gather signals from all strategies
        signals = {}
        signals["momentum"] = await self.momentum.signal(symbol, ohlcv, ticker)
        signals["mean_reversion"] = await self.mean_reversion.signal(symbol, ohlcv, ticker)
        signals["sentiment"] = self.sentiment.get_cached_signal(symbol)
        signals["arbitrage"] = await self.arbitrage.signal(symbol, ticker)

        # Log signal scores
        mom_s = signals.get('momentum', {}).get('score', 'N/A')
        mr_s = signals.get('mean_reversion', {}).get('score', 'N/A')
        log.info(f"  {symbol}: mom={mom_s} mr={mr_s}")

        # Meta-AI combines signals into a final decision
        decision = await self.meta_ai.decide(symbol, signals, self.portfolio, ticker)
        self.current_regime = decision.get("regime", self.current_regime)
        log.info(f"  {symbol}: decision={decision['action']} reason={decision['reason']}")

        # Notify TUI of signal
        if self.on_signal:
            self.on_signal({
                "time": datetime.now(),
                "action": decision["action"],
                "symbol": symbol,
                "score": decision.get("score", 0),
                "regime": self.current_regime,
                "reason": decision.get("reason", ""),
            })

        if decision["action"] == "BUY":
            await self._execute_buy(symbol, current_price, decision)
        elif decision["action"] == "SELL":
            await self._execute_sell(symbol, current_price, decision)
        elif decision["action"] == "CLOSE":
            await self._execute_sell(symbol, current_price, decision)

    async def _execute_buy(self, symbol: str, price: Decimal, decision: dict):
        """Risk-check then buy."""
        size_usd = self.risk.calculate_position_size(symbol, price, decision["confidence"])
        if size_usd is None:
            log.debug(f"Risk blocked BUY {symbol}")
            return

        if not self.risk.within_spending_limit(size_usd):
            log.warning(f"Spending limit reached - skipping {symbol}")
            return

        # Check execution quality via orderbook
        is_stock = self._is_stock(symbol)
        if not is_stock:
            exec_quality = await self.orderbook.get_execution_quality(symbol, float(size_usd))
            if not exec_quality.get("executable", True):
                log.warning(f"Slippage too high for {symbol}: {exec_quality.get('slippage_pct', '?')}%")
                return

        quantity = size_usd / price

        # Round quantity for exchange minimums
        if not is_stock:
            if price > Decimal("1000"):
                quantity = quantity.quantize(Decimal("0.00001"))
            elif price > Decimal("1"):
                quantity = quantity.quantize(Decimal("0.001"))
            else:
                quantity = quantity.quantize(Decimal("1"))
        else:
            quantity = quantity.quantize(Decimal("1"))

        if quantity <= 0:
            return

        log.info(f"[BUY] {symbol} @ ${price:,.4f} | ${size_usd:,.2f} | {decision['reason']}")

        if self.settings.live_mode or is_stock:
            connector = self._get_connector(symbol)
            order = await connector.market_buy(symbol, float(quantity))
            if order:
                self.portfolio.record_buy(symbol, price, quantity, size_usd)
                self.trade_count_today += 1
                if is_stock and not self.settings.live_mode:
                    log.info(f"[ALPACA PAPER] Stock order: {symbol}")
            self._log_trade("BUY", symbol, float(price), float(size_usd))
        else:
            log.info(f"[PAPER] Simulated BUY {quantity:.6f} {symbol}")
            self.portfolio.record_buy(symbol, price, quantity, size_usd)
            self.trade_count_today += 1
            self._log_trade("BUY", symbol, float(price), float(size_usd))

    async def _execute_sell(self, symbol: str, price: Decimal, decision: dict):
        """Sell if we hold a position."""
        position = self.portfolio.get_position(symbol)
        if not position:
            return

        log.info(f"[SELL] {symbol} @ ${price:,.4f} | {decision['reason']}")

        if self.settings.live_mode:
            connector = self._get_connector(symbol)
            await connector.market_sell(symbol, float(position["quantity"]))

        entry_price = float(position["entry_price"])
        qty = float(position["quantity"])
        pnl = self.portfolio.record_sell(symbol, price)
        self.daily_pnl += pnl
        self.trade_count_today += 1

        # Record in performance tracker
        self.performance.record_trade(
            symbol=symbol,
            entry=entry_price,
            exit_price=float(price),
            quantity=qty,
            pnl=float(pnl),
            reason=decision.get("reason", ""),
        )

        pnl_tag = "[+]" if pnl > 0 else "[-]"
        log.info(f"{pnl_tag} PnL on {symbol}: ${pnl:+,.2f} | Daily: ${self.daily_pnl:+,.2f}")

    async def _check_daily_loss_limit(self):
        max_loss = self.settings.spending_limit * Decimal(str(self.settings.max_daily_loss_pct))
        if self.daily_pnl < -max_loss:
            log.critical(f"[STOP] Daily loss limit hit (${self.daily_pnl:,.2f}). Halting.")
            await self._emergency_close_all()
            self.running = False

    async def _emergency_close_all(self):
        log.warning("[ALERT] Emergency close all positions")
        for symbol, pos in list(self.portfolio.positions.items()):
            ticker = await self.market_data.get_ticker(symbol)
            if ticker:
                price = Decimal(str(ticker["last"]))
                await self._execute_sell(symbol, price, {"reason": "emergency_close"})

    async def _sentiment_loop(self):
        """Refresh sentiment analysis periodically."""
        while self.running:
            try:
                await self.sentiment.refresh(self._get_all_symbols())
            except Exception as e:
                log.warning(f"Sentiment refresh error: {e}")
            await asyncio.sleep(self.settings.sentiment_interval_sec)

    async def _rebalance_loop(self):
        """Periodically check stop-losses and take-profits."""
        while self.running:
            try:
                await self._check_stops()
            except Exception as e:
                log.warning(f"Rebalance error: {e}")
            await asyncio.sleep(self.settings.rebalance_interval_sec)

    async def _check_stops(self):
        """Check every open position for SL/TP."""
        for symbol, pos in list(self.portfolio.positions.items()):
            ticker = await self.market_data.get_ticker(symbol)
            if not ticker:
                continue
            current = Decimal(str(ticker["last"]))
            entry = pos["entry_price"]
            pct_change = (current - entry) / entry

            if pct_change <= -Decimal(str(self.settings.stop_loss_pct)):
                log.warning(f"[SL] Stop-loss on {symbol} ({pct_change:.1%})")
                await self._execute_sell(symbol, current, {"reason": "stop_loss"})
            elif pct_change >= Decimal(str(self.settings.take_profit_pct)):
                log.info(f"[TP] Take-profit on {symbol} ({pct_change:.1%})")
                await self._execute_sell(symbol, current, {"reason": "take_profit"})

    async def _monitor_loop(self):
        """Record equity and print status report every 10 minutes."""
        while self.running:
            await asyncio.sleep(600)
            try:
                value = await self.portfolio.total_value_usd()
                self.performance.record_equity(float(value))
                positions = len(self.portfolio.positions)
                log.info(
                    f"[STATUS] Value: ${value:,.2f} | "
                    f"Positions: {positions} | "
                    f"Daily PnL: ${self.daily_pnl:+,.2f} | "
                    f"Trades: {self.trade_count_today}"
                )
            except Exception:
                pass

    def _reset_daily_if_needed(self):
        today = date.today()
        if today != self.last_reset:
            self.daily_pnl = Decimal("0")
            self.trade_count_today = 0
            self.last_reset = today
            log.info("New trading day - counters reset")

    def _get_all_symbols(self) -> List[str]:
        symbols = []
        if self.settings.crypto_enabled:
            symbols += self.settings.crypto_pairs
        if self.settings.memecoins_enabled:
            symbols += self.settings.memecoin_pairs
        if self.settings.stocks_enabled:
            symbols += [f"{s}/USD" for s in self.settings.stock_watchlist]
        return symbols

    def _is_stock(self, symbol: str) -> bool:
        return symbol.replace("/USD", "") in self.settings.stock_watchlist

    def _get_connector(self, symbol: str):
        if "/" in symbol and not symbol.endswith("/USD"):
            return self.crypto_conn
        if symbol.replace("/USD", "") in self.settings.stock_watchlist:
            return self.stock_conn
        return self.crypto_conn

    def _log_trade(self, action: str, symbol: str, price: float, size_usd: float):
        log.info(f"[TRADE] {action} {symbol} @ ${price:,.4f} size=${size_usd:,.2f}")

    async def shutdown(self):
        log.info("Shutting down gracefully...")
        self.running = False
        for name, cleanup in [
            ("market_data", self.market_data.disconnect()),
            ("crypto", self.crypto_conn.disconnect()),
            ("stock", self.stock_conn.disconnect()),
            ("onchain", self.onchain.disconnect()),
        ]:
            try:
                await asyncio.wait_for(cleanup, timeout=5.0)
            except Exception as e:
                log.warning(f"Shutdown error ({name}): {e}")
        try:
            self.performance._save()
        except Exception:
            pass
        log.info("APEX Trader offline.")
