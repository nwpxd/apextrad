"""
Terminal UI for APEX Trader - Real-time dashboard using rich.
Clean, readable, color-coded. Like a Bloomberg terminal in your console.
"""

import asyncio
import logging
from collections import deque
from datetime import datetime
from decimal import Decimal

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.columns import Columns
from rich import box


class TUILogHandler(logging.Handler):
    """Captures log records into a ring buffer for TUI display."""

    def __init__(self, tui: "TUI"):
        super().__init__()
        self.tui = tui

    def emit(self, record):
        try:
            msg = self.format(record)
            self.tui.log_lines.append(msg)
        except Exception:
            pass


class TUI:
    def __init__(self, agent):
        self.agent = agent
        self.signals: deque = deque(maxlen=50)
        self.log_lines: deque = deque(maxlen=100)
        self.start_time = datetime.now()
        self.scan_count = 0

        # Register signal callback on agent
        self.agent.on_signal = self._on_signal

    def _on_signal(self, signal: dict):
        self.signals.append(signal)
        self.scan_count += 1

    def _uptime(self) -> str:
        delta = datetime.now() - self.start_time
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours > 0:
            return f"{hours}h {minutes:02d}m"
        return f"{minutes}m {seconds:02d}s"

    # ── HEADER ──────────────────────────────────────────────

    def _build_header(self) -> Panel:
        mode = "LIVE" if self.agent.settings.live_mode else "PAPER"
        mode_style = "bold red" if mode == "LIVE" else "bold yellow"
        wallet = self.agent.settings.wallet or "N/A"
        if len(wallet) > 16:
            wallet = wallet[:8] + ".." + wallet[-6:]

        t = Text(justify="center")
        t.append(" APEX TRADER ", style="bold white on dark_green")
        t.append("  ")
        t.append(f" {mode} ", style=f"{mode_style} on grey23")
        t.append("  ")
        t.append(f"{wallet}", style="white")
        t.append("  |  ", style="bright_black")
        t.append(f"${self.agent.settings.spending_limit:,.0f}", style="bold cyan")
        t.append("  |  ", style="bright_black")
        t.append(f"{self._uptime()}", style="white")
        t.append("  |  ", style="bright_black")
        t.append(f"Scans: {self.scan_count}", style="bright_black")

        return Panel(t, style="bright_green", height=3)

    # ── PORTFOLIO ───────────────────────────────────────────

    def _build_portfolio(self) -> Panel:
        portfolio = self.agent.portfolio
        cash = float(portfolio.cash_usd)
        exposure = float(portfolio.exposure_usd())
        total = cash + exposure
        initial = float(self.agent.settings.spending_limit)
        pnl = total - initial
        pnl_pct = (pnl / initial * 100) if initial > 0 else 0
        c = "green" if pnl >= 0 else "red"

        t = Table(show_header=False, box=None, padding=(0, 2), expand=True)
        t.add_column("l", style="bright_black", ratio=1)
        t.add_column("v", justify="right", ratio=1)

        t.add_row("Cash", f"[bold white]${cash:,.2f}[/]")
        t.add_row("Exposure", f"[yellow]${exposure:,.2f}[/]")
        t.add_row("[bold]Total[/]", f"[bold white]${total:,.2f}[/]")
        t.add_row("P&L", f"[bold {c}]${pnl:+,.2f}  ({pnl_pct:+.1f}%)[/]")

        return Panel(t, title="[bold white] PORTFOLIO [/]", border_style="blue", box=box.ROUNDED)

    # ── METRICS ─────────────────────────────────────────────

    def _build_metrics(self) -> Panel:
        daily_pnl = float(self.agent.daily_pnl)
        dc = "green" if daily_pnl >= 0 else "red"
        regime = self.agent.current_regime
        regime_colors = {"trending": "green", "ranging": "yellow", "volatile": "red", "mixed": "cyan", "unknown": "bright_black"}
        rc = regime_colors.get(regime, "white")

        metrics = self.agent.performance.get_metrics()
        wr = metrics.get('win_rate_pct', 0)
        wr_c = "green" if wr >= 55 else ("yellow" if wr >= 45 else "red")

        t = Table(show_header=False, box=None, padding=(0, 2), expand=True)
        t.add_column("l", style="bright_black", ratio=1)
        t.add_column("v", justify="right", ratio=1)

        t.add_row("Daily P&L", f"[bold {dc}]${daily_pnl:+,.2f}[/]")
        t.add_row("Trades", f"[white]{self.agent.trade_count_today}[/]")
        t.add_row("Win Rate", f"[{wr_c}]{wr:.1f}%[/]")
        t.add_row("Regime", f"[bold {rc}]{regime.upper()}[/]")

        return Panel(t, title="[bold white] METRICS [/]", border_style="blue", box=box.ROUNDED)

    # ── POSITIONS ───────────────────────────────────────────

    def _build_positions(self) -> Panel:
        positions = self.agent.portfolio.positions
        max_pos = self.agent.settings.max_open_positions
        title = f"[bold white] POSITIONS  {len(positions)}/{max_pos} [/]"

        if not positions:
            content = Text("  Aucune position ouverte", style="bright_black italic")
            return Panel(content, title=title, border_style="bright_black", box=box.ROUNDED, height=5)

        t = Table(box=box.SIMPLE_HEAVY, padding=(0, 1), expand=True, show_edge=False)
        t.add_column("SYMBOL", style="bold white", width=10)
        t.add_column("ENTRY", justify="right", style="bright_black", width=10)
        t.add_column("NOW", justify="right", width=10)
        t.add_column("SIZE", justify="right", style="bright_black", width=9)
        t.add_column("P&L $", justify="right", width=10)
        t.add_column("P&L %", justify="right", width=8)

        for symbol, pos in positions.items():
            entry = float(pos["entry_price"])
            current = float(pos.get("current_price", entry))
            cost = float(pos["cost_usd"])
            value = float(pos["quantity"]) * current
            pnl = value - cost
            pnl_pct = ((current - entry) / entry * 100) if entry > 0 else 0
            c = "green" if pnl >= 0 else "red"

            def fmt_price(p):
                if p > 10000: return f"${p:,.0f}"
                if p > 100: return f"${p:,.1f}"
                if p > 1: return f"${p:,.2f}"
                if p > 0.01: return f"${p:.4f}"
                return f"${p:.6f}"

            sym = symbol.replace("/USDT", "").replace("/USD", "")
            t.add_row(
                sym,
                fmt_price(entry),
                fmt_price(current),
                f"${cost:,.0f}",
                f"[{c}]${pnl:+,.2f}[/]",
                f"[bold {c}]{pnl_pct:+.1f}%[/]",
            )

        return Panel(t, title=title, border_style="yellow", box=box.ROUNDED)

    # ── SIGNALS ─────────────────────────────────────────────

    def _build_signals(self) -> Panel:
        title = "[bold white] SIGNALS [/]"

        if not self.signals:
            return Panel(
                Text("  En attente du premier scan...", style="bright_black italic"),
                title=title, border_style="bright_black", box=box.ROUNDED, height=6,
            )

        t = Table(box=None, padding=(0, 1), expand=True, show_edge=False)
        t.add_column("TIME", style="bright_black", width=6)
        t.add_column("", width=4)
        t.add_column("SYMBOL", width=10)
        t.add_column("SCORE", justify="right", width=8)
        t.add_column("REGIME", width=10)
        t.add_column("REASON", style="bright_black", ratio=1)

        # Show non-HOLD signals first, fill with HOLDs if needed
        recent = list(self.signals)[-30:]
        non_hold = [s for s in recent if s["action"] != "HOLD"]
        display = non_hold[-10:] if len(non_hold) >= 3 else recent[-10:]

        for sig in display:
            time_str = sig["time"].strftime("%H:%M")
            action = sig["action"]

            if action == "BUY":
                act = "[bold green]BUY [/]"
            elif action == "SELL":
                act = "[bold red]SELL[/]"
            else:
                act = "[bright_black]HOLD[/]"

            sym = sig["symbol"].replace("/USDT", "").replace("/USD", "")
            score = sig.get("score", 0)
            sc = "green" if score > 0.05 else ("red" if score < -0.05 else "bright_black")
            regime = sig.get("regime", "?")
            rc = {"trending": "green", "ranging": "yellow", "volatile": "red", "mixed": "cyan"}.get(regime, "bright_black")

            reason = sig.get("reason", "")
            # Truncate reason
            if len(reason) > 40:
                reason = reason[:37] + "..."

            t.add_row(
                time_str,
                act,
                f"[white]{sym}[/]",
                f"[{sc}]{score:+.3f}[/]",
                f"[{rc}]{regime}[/]",
                reason,
            )

        return Panel(t, title=title, border_style="magenta", box=box.ROUNDED)

    # ── LOGS ────────────────────────────────────────────────

    def _build_logs(self) -> Panel:
        if not self.log_lines:
            return Panel(
                Text("  Demarrage...", style="bright_black"),
                title="[bold white] LOGS [/]", border_style="bright_black", box=box.ROUNDED, height=6,
            )

        text = Text()
        lines = list(self.log_lines)[-15:]
        for line in lines:
            if "[ERROR]" in line or "[CRITICAL]" in line:
                style = "bold red"
            elif "[WARNING]" in line:
                style = "yellow"
            elif "[BUY]" in line or "[+]" in line or "[TP]" in line or "[OK]" in line:
                style = "green"
            elif "[SELL]" in line or "[-]" in line or "[SL]" in line or "[STOP]" in line:
                style = "red"
            elif "[STATUS]" in line:
                style = "cyan"
            elif "[TRADE]" in line:
                style = "bold white"
            elif "BUY SIGNAL" in line:
                style = "bold green"
            else:
                style = "bright_black"

            # Shorten log line for display
            display = line
            if len(display) > 130:
                display = display[:127] + "..."
            text.append(display + "\n", style=style)

        return Panel(text, title="[bold white] LOGS [/]", border_style="bright_black", box=box.ROUNDED)

    # ── LAYOUT ──────────────────────────────────────────────

    def build_layout(self) -> Layout:
        layout = Layout()

        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="top", size=7),
            Layout(name="positions", size=max(5, len(self.agent.portfolio.positions) + 4)),
            Layout(name="signals", size=14),
            Layout(name="logs"),
        )

        layout["top"].split_row(
            Layout(name="portfolio"),
            Layout(name="metrics"),
        )

        layout["header"].update(self._build_header())
        layout["portfolio"].update(self._build_portfolio())
        layout["metrics"].update(self._build_metrics())
        layout["positions"].update(self._build_positions())
        layout["signals"].update(self._build_signals())
        layout["logs"].update(self._build_logs())

        return layout

    async def run(self):
        """Main TUI loop - refresh every 2 seconds."""
        console = Console()
        with Live(self.build_layout(), console=console, refresh_per_second=1, screen=True) as live:
            while self.agent.running:
                try:
                    live.update(self.build_layout())
                except Exception:
                    pass
                await asyncio.sleep(2)
