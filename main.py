"""
Trader Bot - Minimalist crypto trading.
Usage: python main.py --limit 1000
"""

import asyncio
import argparse
import logging
import sys
import os
import yaml

os.makedirs("logs", exist_ok=True)

DEFAULT_CONFIG = {
    "capital": 1000,
    "symbols": ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "AVAX/USDT"],
    "timeframe": "1h",
    "scan_interval": 60,
    "max_positions": 5,
    "risk_per_trade": 0.05,
    "stop_loss": 0.02,
    "take_profit": 0.04,
    "max_daily_loss": 0.05,
}


def load_config(path: str) -> dict:
    config = dict(DEFAULT_CONFIG)
    if os.path.exists(path):
        with open(path) as f:
            user = yaml.safe_load(f) or {}
        config.update(user)
    return config


async def main():
    parser = argparse.ArgumentParser(description="Trader Bot")
    parser.add_argument("--limit", type=float, default=1000, help="Capital in USD")
    parser.add_argument("--config", default="config.yaml", help="Config file")
    parser.add_argument("--dashboard", action="store_true", help="Web dashboard on localhost")
    parser.add_argument("--port", type=int, default=8080, help="Dashboard port")
    args = parser.parse_args()

    # Logging
    handlers = [
        logging.FileHandler("logs/bot.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)-10s %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )
    log = logging.getLogger("main")

    config = load_config(args.config)
    config["capital"] = args.limit

    log.info(f"Capital: ${args.limit:,.0f}")
    log.info(f"Symbols: {', '.join(config['symbols'])}")
    log.info(f"Strategy: EMA(9/21/50) + RSI(14) + Volume")
    log.info(f"Risk: {config['risk_per_trade']*100:.0f}% per trade, SL {config['stop_loss']*100:.0f}%, TP {config['take_profit']*100:.0f}%")

    from bot.engine import Engine
    engine = Engine(config)
    tasks = [asyncio.create_task(engine.start())]

    if args.dashboard:
        from server import DashboardServer
        dashboard = DashboardServer(engine, port=args.port)
        tasks.append(asyncio.create_task(dashboard.run()))
        log.info(f"Dashboard: http://localhost:{args.port}")

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass
    finally:
        await engine.stop()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")
