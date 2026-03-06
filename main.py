"""
Trader Bot
  Paper:     python main.py --limit 1000
  Live:      python main.py --limit 1000 --live
  Dashboard: python main.py --limit 1000 --dashboard
  Backtest:  python main.py --backtest --months 6
"""

import asyncio
import argparse
import logging
import sys
import os
import yaml

from dotenv import load_dotenv
load_dotenv()

os.makedirs("logs", exist_ok=True)

DEFAULT_CONFIG = {
    "capital": 1000,
    "symbols": [
        "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "AVAX/USDT",
        "ADA/USDT", "DOGE/USDT", "LINK/USDT",
    ],
    "timeframe": "1h",
    "htf": "4h",
    "scan_interval": 60,
    "max_positions": 5,
    "risk_per_trade_usd": 15,
    "max_correlated": 2,
    "max_daily_loss": 0.05,
    "circuit_breaker": 3,
    "adx_period": 14,
    "adx_trend_threshold": 25,
    "adx_range_threshold": 20,
    "bb_period": 20,
    "bb_std": 2.0,
    "atr_sl_mult": 2.0,
    "atr_tp_mult": 4.0,
    "trail_phase1_r": 1.0,
    "trail_phase2_r": 2.0,
    "trail_phase3_atr": 0.75,
    "use_limit_orders": False,
    "limit_order_offset_pct": 0.05,
    "reconcile_interval": 3600,
    "retry_max": 3,
    "retry_base_delay": 2,
    "backtest_months": 6,
    "backtest_initial_capital": 1000,
}


def load_config(path: str) -> dict:
    config = dict(DEFAULT_CONFIG)
    if os.path.exists(path):
        with open(path) as f:
            user = yaml.safe_load(f) or {}
        config.update(user)
    return config


async def run_backtest(config: dict, months: int):
    from bot.backtest import Backtester
    bt = Backtester(config)
    await bt.run(months)


async def main():
    parser = argparse.ArgumentParser(description="Trader Bot")
    parser.add_argument("--limit", type=float, default=1000, help="Capital in USD")
    parser.add_argument("--config", default="config.yaml", help="Config file")
    parser.add_argument("--live", action="store_true", help="Live trading (requires .env)")
    parser.add_argument("--dashboard", action="store_true", help="Web dashboard")
    parser.add_argument("--port", type=int, default=8080, help="Dashboard port")
    parser.add_argument("--backtest", action="store_true", help="Run backtester")
    parser.add_argument("--months", type=int, default=6, help="Backtest months (default: 6)")
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
    config["live"] = args.live

    config["binance_api_key"] = os.getenv("BINANCE_API_KEY", "")
    config["binance_secret"] = os.getenv("BINANCE_SECRET", "")

    # Backtest mode
    if args.backtest:
        config["backtest_initial_capital"] = args.limit
        log.info("=" * 45)
        log.info(f"  BACKTEST MODE")
        log.info(f"  Capital:  ${args.limit:,.0f}")
        log.info(f"  Months:   {args.months}")
        log.info(f"  Symbols:  {len(config['symbols'])} pairs")
        log.info(f"  Strategy: EMA + RSI + MACD + ADX + BB")
        log.info("=" * 45)
        await run_backtest(config, args.months)
        return

    # Live/Paper mode
    mode = "LIVE" if args.live else "PAPER"
    log.info("=" * 45)
    log.info(f"  Mode:     {mode}")
    log.info(f"  Capital:  ${args.limit:,.0f}")
    log.info(f"  Symbols:  {len(config['symbols'])} pairs")
    log.info(f"  Strategy: EMA + RSI + MACD + ADX + BB")
    log.info(f"  Risk:     ${config['risk_per_trade_usd']} per trade (ATR-based)")
    log.info(f"  API key:  {'loaded' if config['binance_api_key'] else 'none'}")
    log.info("=" * 45)

    if args.live and not config["binance_api_key"]:
        log.error("Live mode requires BINANCE_API_KEY and BINANCE_SECRET in .env file")
        log.error("Create a .env file with:")
        log.error("  BINANCE_API_KEY=your_key")
        log.error("  BINANCE_SECRET=your_secret")
        return

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
