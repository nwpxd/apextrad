"""
APEX TRADER v2 - Fully Automated AI Trading Agent
Usage: python main.py --wallet YOUR_WALLET --limit 1000 --mode paper
"""

import asyncio
import argparse
import logging
import sys
import os
from decimal import Decimal
from dotenv import load_dotenv
load_dotenv()

from core.agent import TradingAgent
from config.settings import Settings

# Fix Windows console encoding
if sys.platform == "win32":
    os.environ["PYTHONUTF8"] = "1"
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

os.makedirs("logs", exist_ok=True)


async def main():
    parser = argparse.ArgumentParser(description="APEX Trader v2")
    parser.add_argument("--wallet", required=True)
    parser.add_argument("--limit", type=float, required=True)
    parser.add_argument("--mode", choices=["live", "paper"], default="paper")
    parser.add_argument("--config", default="config/settings.yaml")
    parser.add_argument("--no-tui", action="store_true", help="Headless mode (no terminal UI)")
    parser.add_argument("--dashboard", action="store_true", help="Launch web dashboard on localhost:8080")
    parser.add_argument("--port", type=int, default=8080, help="Dashboard port (default: 8080)")
    args = parser.parse_args()

    # Set up logging: file always, console only in headless mode
    handlers = [logging.FileHandler("logs/apex.log", encoding="utf-8")]
    if args.no_tui:
        handlers.append(logging.StreamHandler(sys.stdout))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )
    log = logging.getLogger("APEX")

    log.info("=" * 50)
    log.info("APEX TRADER v2 starting")
    log.info(f"  Wallet : {args.wallet}")
    log.info(f"  Limit  : ${args.limit:,.2f}")
    log.info(f"  Mode   : {args.mode.upper()}")
    log.info(f"  TUI    : {'OFF' if args.no_tui else 'ON'}")
    log.info(f"  Web UI : {'http://localhost:' + str(args.port) if args.dashboard else 'OFF'}")
    log.info("=" * 50)

    settings = Settings.load(args.config)
    settings.wallet = args.wallet
    settings.spending_limit = Decimal(str(args.limit))
    settings.live_mode = args.mode == "live"

    agent = TradingAgent(settings)
    tasks = [asyncio.create_task(agent.run())]

    if args.dashboard:
        from dashboard_api import DashboardServer
        dashboard = DashboardServer(agent, port=args.port)
        tasks.append(asyncio.create_task(dashboard.run()))
        log.info(f"[OK] Dashboard will be at http://localhost:{args.port}")

    if not args.no_tui:
        from tui import TUI, TUILogHandler
        tui = TUI(agent)
        # Capture logs into TUI display
        tui_handler = TUILogHandler(tui)
        tui_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S"))
        logging.getLogger().addHandler(tui_handler)
        tasks.append(asyncio.create_task(tui.run()))

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass
    finally:
        await agent.shutdown()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
