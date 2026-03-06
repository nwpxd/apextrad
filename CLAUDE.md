# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Apex Trader v2 — a multi-strategy async trading bot supporting crypto (Binance via ccxt), US equities (Alpaca), and memecoins. Python 3.8+, fully async with `asyncio`. Terminal UI via `rich`.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run trading bot with TUI (paper mode)
python main.py --wallet my_wallet --limit 1000 --mode paper

# Run headless (no TUI, logs to stdout)
python main.py --wallet my_wallet --limit 1000 --mode paper --no-tui

# Run live trading
python main.py --wallet my_wallet --limit 1000 --mode live

# Additional flags: --config <path>

# Backtesting with walk-forward optimization
python backtest.py --symbol BTC/USDT --days 365 --walk-forward

# Diagnostic tool (tests connectivity, fetches data, runs strategies)
python diagnose.py
```

No test suite or linting configuration exists in this repo.

## Architecture

**Async event loop** with 4 concurrent loops managed by `TradingAgent` (core/agent.py):
- Trading loop (configurable interval) — scans signals, inline SL/TP checks, executes trades
- Sentiment loop (300s) — refreshes heuristic or LLM-powered sentiment
- Rebalance loop (60s) — periodic stop-loss/take-profit sweep
- Monitor loop (600s) — equity snapshots, status reporting

**Terminal UI** (`tui.py`) runs as a 5th async task, refreshing every 2s via `rich.live.Live`. Displays portfolio, metrics, positions, signals, and logs. Captures logs via a custom `TUILogHandler`.

**Signal flow:**
1. `MarketDataHub` (core/market_data.py) fetches OHLCV from Binance/yfinance
2. Four strategies generate scores (-1.0 to +1.0 with confidence):
   - `strategies/momentum.py` — RSI, MACD crossover, EMA crossover detection
   - `strategies/mean_reversion.py` — Bollinger Bands, Z-score, trend filter
   - `strategies/sentiment.py` — LLM or heuristic news analysis with TTL cache
   - `strategies/arbitrage.py` — spread & liquidity analysis
3. `MetaAI` (strategies/meta_ai.py) detects regime (trending/ranging/volatile/mixed) and combines via weighted averaging with regime-adjusted weights + NaN guard
4. `RiskManager` (core/risk_manager.py) enforces: aggregate exposure cap, 10% cash buffer, memecoin size cap (3%), position limits
5. `OrderBookAnalyzer` (core/orderbook.py) checks slippage before execution
6. Orders execute through `connectors/crypto_connector.py` or `connectors/stock_connector.py`

**Supporting modules:**
- `core/portfolio.py` — position state, cash, P&L tracking
- `core/performance.py` — Sharpe, drawdown, win rate, persists to `logs/performance.json`
- `core/orderbook.py` — bid/ask imbalance, depth analysis, slippage estimation
- `core/onchain.py` — TVL (DeFiLlama), whale flows, Fear & Greed index

## Key Design Decisions

- **`decimal.Decimal`** for all monetary calculations
- **Paper trading is the default** — works without any API keys (mock data)
- **Strategy weights** in `config/settings.yaml` auto-adjusted by MetaAI per regime
- **Inline SL/TP** checked during every symbol evaluation + periodic sweep
- **Settings validated** at load time (SL < TP, weights sum ~1.0, etc.)

## Configuration

- `config/settings.yaml` — risk params, strategy weights, asset lists, timing intervals
- `config/settings.py` — dataclass with `_validate()`, loads YAML + env vars
- `.env` file (not in repo) — API keys: `BINANCE_API_KEY`, `BINANCE_SECRET`, `ALPACA_API_KEY`, `ALPACA_SECRET`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`
