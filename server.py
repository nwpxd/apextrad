"""
Dashboard server. Serves HTML + WebSocket updates.
Includes equity curve, trade history, OHLCV with indicators, live logs.
"""

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from aiohttp import web

from bot import strategy as strat

log = logging.getLogger("dashboard")


class DashboardServer:
    def __init__(self, engine, host="0.0.0.0", port=8080):
        self.engine = engine
        self.host = host
        self.port = port
        self.app = web.Application()
        self.clients: list[web.WebSocketResponse] = []
        self.app.router.add_get("/", self._index)
        self.app.router.add_get("/ws", self._ws)
        self.app.router.add_get("/api/equity", self._equity)
        self.app.router.add_get("/api/trades", self._trades)
        self.app.router.add_get("/api/ohlcv/{symbol}", self._ohlcv)

    async def _index(self, request):
        return web.FileResponse(Path(__file__).parent / "dashboard" / "index.html")

    async def _ws(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.clients.append(ws)
        try:
            await ws.send_json(self._state())
            async for _ in ws:
                pass
        finally:
            self.clients.remove(ws)
        return ws

    async def _equity(self, request):
        curve = self.engine.portfolio.equity_curve
        return web.json_response(curve)

    async def _trades(self, request):
        trades = self.engine.tracker.trades
        return web.json_response(trades)

    async def _ohlcv(self, request):
        """Return OHLCV data with EMA and Bollinger Bands overlay data."""
        symbol = request.match_info["symbol"].replace("-", "/")
        tf = self.engine.config.get("timeframe", "1h")

        df = await self.engine.market.ohlcv(symbol, tf, limit=200)
        if df is None:
            return web.json_response([])

        # Calculate indicators for overlay
        close = df["close"]
        ema9 = strat.ema(close, 9)
        ema21 = strat.ema(close, 21)
        bb_period = self.engine.config.get("bb_period", 20)
        bb_std = self.engine.config.get("bb_std", 2.0)
        bb_mid = strat.sma(close, bb_period)
        bb_rolling_std = close.rolling(bb_period).std()
        bb_upper = bb_mid + bb_rolling_std * bb_std
        bb_lower = bb_mid - bb_rolling_std * bb_std

        candles = []
        for idx, (ts, row) in enumerate(df.iterrows()):
            entry = {
                "time": int(ts.timestamp()),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
            }
            # Add indicators (skip NaN at start)
            if idx >= 20:
                entry["ema9"] = float(ema9.iloc[idx])
                entry["ema21"] = float(ema21.iloc[idx])
                entry["bb_upper"] = float(bb_upper.iloc[idx])
                entry["bb_lower"] = float(bb_lower.iloc[idx])
                entry["bb_mid"] = float(bb_mid.iloc[idx])
            candles.append(entry)

        return web.json_response(candles)

    def _state(self) -> dict:
        e = self.engine
        p = e.portfolio

        positions = []
        for sym, pos in p.positions.items():
            entry = float(pos["entry"])
            current = float(pos["current"])
            cost = float(pos["cost"])
            value = float(pos["quantity"]) * current
            pnl = value - cost
            pnl_pct = ((current - entry) / entry * 100) if entry > 0 else 0
            trail_phase = pos.get("trail_phase", 0)
            positions.append({
                "symbol": sym, "entry": entry, "current": current,
                "cost": cost, "value": value, "pnl": pnl, "pnl_pct": pnl_pct,
                "stop_loss": float(pos["stop_loss"]),
                "take_profit": float(pos["take_profit"]),
                "trail_phase": trail_phase,
            })

        signals = []
        for s in list(e.signals)[-20:]:
            signals.append({
                "time": s["time"].strftime("%H:%M:%S"),
                "symbol": s["symbol"], "action": s["action"],
                "score": s["score"], "reason": s["reason"],
                "regime": s.get("regime", ""),
            })

        cb_active = e.circuit_breaker.is_paused()
        cb_remaining = e.circuit_breaker.remaining_pause if cb_active else ""
        cb_consecutive = e.circuit_breaker.consecutive_stops

        # Live log lines
        live_logs = list(e.log_capture.buffer)

        return {
            "portfolio": {
                "cash": float(p.cash), "exposure": float(p.exposure),
                "value": float(p.value), "pnl": float(p.pnl), "pnl_pct": p.pnl_pct,
                "initial": float(p.initial),
            },
            "metrics": {
                "daily_pnl": float(e.daily_pnl), "trades_today": e.trades_today,
                "total_trades": e.tracker.total_trades, "win_rate": e.tracker.win_rate,
                "avg_win": e.tracker.avg_win, "avg_loss": e.tracker.avg_loss,
                "regime": e.current_regime,
            },
            "circuit_breaker": {
                "active": cb_active,
                "remaining": cb_remaining,
                "consecutive_stops": cb_consecutive,
            },
            "positions": positions,
            "signals": signals,
            "logs": live_logs,
            "max_positions": e.config.get("max_positions", 5),
            "config": {
                "symbols": e.config["symbols"],
                "timeframe": e.config.get("timeframe", "1h"),
                "htf": e.config.get("htf", "4h"),
                "risk_per_trade": e.config.get("risk_per_trade_usd", 15),
                "atr_sl": e.config.get("atr_sl_mult", 2.0),
                "atr_tp": e.config.get("atr_tp_mult", 4.0),
                "mode": "DRY-RUN" if e.dry_run else ("LIVE" if e.live else "PAPER"),
            },
        }

    async def _broadcast(self):
        if not self.clients:
            return
        state = self._state()
        dead = []
        for ws in self.clients:
            try:
                await ws.send_json(state)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.remove(ws)

    async def run(self):
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()
        log.info(f"http://localhost:{self.port}")

        while self.engine.running:
            await self._broadcast()
            await asyncio.sleep(2)

        await runner.cleanup()
