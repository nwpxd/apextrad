"""Dashboard server. Serves HTML + WebSocket updates."""

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from aiohttp import web

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
            positions.append({
                "symbol": sym, "entry": entry, "current": current,
                "cost": cost, "value": value, "pnl": pnl, "pnl_pct": pnl_pct,
            })

        signals = []
        for s in list(e.signals)[-20:]:
            signals.append({
                "time": s["time"].strftime("%H:%M:%S"),
                "symbol": s["symbol"], "action": s["action"],
                "score": s["score"], "reason": s["reason"],
            })

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
            },
            "positions": positions,
            "signals": signals,
            "max_positions": e.config.get("max_positions", 5),
            "config": {
                "symbols": e.config["symbols"],
                "timeframe": e.config.get("timeframe", "1h"),
                "stop_loss": e.config.get("stop_loss", 0.02),
                "take_profit": e.config.get("take_profit", 0.04),
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
