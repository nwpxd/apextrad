# trader-bot

Crypto trading bot. Real Binance data, no API key needed for paper trading.

## Strategy

- **EMA crossover** (9/21) with trend filter (EMA50)
- **RSI**(14) for momentum confirmation
- **MACD** histogram for direction strength
- **ATR-based stops** that adapt to volatility (not fixed %)
- **Trailing stop** locks profits as price moves in your favor
- **Cooldown** prevents re-entering after a stop-loss

Fees (0.1% per side) are deducted from every trade.

## Setup

```bash
pip install -r requirements.txt
```

## Paper trading (no API key needed)

```bash
python main.py --limit 1000
```

## With web dashboard

```bash
python main.py --limit 1000 --dashboard
```
Open **http://localhost:8080**

## Live trading

1. Create API key on Binance (Spot Trading only, never enable Withdrawals)
2. Copy `.env.example` to `.env` and fill your keys:
```
BINANCE_API_KEY=your_key_here
BINANCE_SECRET=your_secret_here
```
3. Run:
```bash
python main.py --limit 1000 --live --dashboard
```

## Config

Edit `config.yaml`:

```yaml
symbols:
  - BTC/USDT
  - ETH/USDT
  - SOL/USDT

timeframe: "1h"
scan_interval: 60
max_positions: 5
risk_per_trade: 0.05      # 5% per trade
max_daily_loss: 0.05      # halt at -5%
```

Stops are dynamic (ATR-based):
- **SL** = entry - 2x ATR
- **TP** = entry + 4x ATR (2:1 R/R)
- **Trail** activates after +2x ATR, follows at 1.5x ATR

## Structure

```
trader-bot/
  main.py           # entry point
  config.yaml       # settings
  .env              # API keys (live mode only)
  server.py         # dashboard API
  dashboard/
    index.html      # web UI
  bot/
    data.py         # Binance connection + orders
    strategy.py     # signal engine (EMA + RSI + MACD + ATR)
    risk.py         # position sizing + fees
    portfolio.py    # positions, trailing stops, cooldowns
    tracker.py      # trade history
    engine.py       # main loop + stop checker
```

## Disclaimer

No guaranteed profits. Always paper trade first. Don't risk money you can't lose.
