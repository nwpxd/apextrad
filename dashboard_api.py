"""
Dashboard API - serves real-time data to the HTML dashboard via WebSocket.
Uses aiohttp (already in requirements) - no extra dependency needed.
"""

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from aiohttp import web

log = logging.getLogger("Dashboard")


class DashboardServer:
    def __init__(self, agent, host="0.0.0.0", port=8080):
        self.agent = agent
        self.host = host
        self.port = port
        self.app = web.Application()
        self.clients: list[web.WebSocketResponse] = []
        self._setup_routes()

    def _setup_routes(self):
        self.app.router.add_get("/", self._serve_index)
        self.app.router.add_get("/ws", self._websocket_handler)
        self.app.router.add_get("/api/status", self._api_status)

    async def _serve_index(self, request):
        html_path = Path(__file__).parent / "dashboard" / "index.html"
        return web.FileResponse(html_path)

    async def _api_status(self, request):
        return web.json_response(self._build_state())

    async def _websocket_handler(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.clients.append(ws)
        log.info(f"Dashboard client connected ({len(self.clients)} total)")

        try:
            await ws.send_json(self._build_state())
            async for msg in ws:
                pass  # We only push, no client messages needed
        finally:
            self.clients.remove(ws)
            log.info(f"Dashboard client disconnected ({len(self.clients)} total)")

        return ws

    def _build_state(self) -> dict:
        portfolio = self.agent.portfolio
        cash = float(portfolio.cash_usd)
        exposure = float(portfolio.exposure_usd())
        total = cash + exposure
        initial = float(self.agent.settings.spending_limit)
        pnl = total - initial
        pnl_pct = (pnl / initial * 100) if initial > 0 else 0

        positions = []
        for symbol, pos in portfolio.positions.items():
            entry = float(pos["entry_price"])
            current = float(pos.get("current_price", entry))
            cost = float(pos["cost_usd"])
            qty = float(pos["quantity"])
            value = qty * current
            pos_pnl = value - cost
            pos_pnl_pct = ((current - entry) / entry * 100) if entry > 0 else 0
            positions.append({
                "symbol": symbol,
                "entry": entry,
                "current": current,
                "cost": cost,
                "value": value,
                "quantity": qty,
                "pnl": pos_pnl,
                "pnl_pct": pos_pnl_pct,
            })

        metrics = self.agent.performance.get_metrics()

        # Get recent signals from TUI callback or agent
        signals = []
        if hasattr(self.agent, '_dashboard_signals'):
            for sig in list(self.agent._dashboard_signals)[-20:]:
                signals.append({
                    "time": sig["time"].strftime("%H:%M:%S"),
                    "action": sig["action"],
                    "symbol": sig["symbol"],
                    "score": sig.get("score", 0),
                    "regime": sig.get("regime", "unknown"),
                    "reason": sig.get("reason", ""),
                })

        return {
            "timestamp": datetime.now().isoformat(),
            "mode": "LIVE" if self.agent.settings.live_mode else "PAPER",
            "wallet": self.agent.settings.wallet or "N/A",
            "portfolio": {
                "cash": cash,
                "exposure": exposure,
                "total": total,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "initial": initial,
            },
            "metrics": {
                "daily_pnl": float(self.agent.daily_pnl),
                "trades_today": self.agent.trade_count_today,
                "win_rate": metrics.get("win_rate_pct", 0),
                "regime": self.agent.current_regime,
                "total_trades": metrics.get("total_trades", 0),
                "max_drawdown": metrics.get("max_drawdown_pct", 0),
            },
            "positions": positions,
            "signals": signals,
            "max_positions": self.agent.settings.max_open_positions,
        }

    async def broadcast(self):
        """Push state to all connected WebSocket clients."""
        if not self.clients:
            return
        state = self._build_state()
        dead = []
        for ws in self.clients:
            try:
                await ws.send_json(state)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.remove(ws)

    async def run(self):
        """Start the web server + broadcast loop."""
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()
        log.info(f"[OK] Dashboard running at http://localhost:{self.port}")

        # Broadcast state every 2 seconds
        while self.agent.running:
            await self.broadcast()
            await asyncio.sleep(2)

        await runner.cleanup()
