# trader-bot

crypto trading bot. Real Binance data, no API key needed for paper trading.

**Strategy:** EMA(9/21/50) crossover + RSI(14) + Volume confirmation.
Fees included (0.1% per side). 2:1 risk/reward.

## Setup

```bash
pip install -r requirements.txt
```

## Run

```bash
# Paper trading (default $1000)
python main.py --limit 1000

# With web dashboard
python main.py --limit 1000 --dashboard

# Custom port
python main.py --limit 1000 --dashboard --port 3000
```

Dashboard: open **http://localhost:8080** in your browser.

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
risk_per_trade: 0.05    # 5% per trade
stop_loss: 0.02         # 2%
take_profit: 0.04       # 4%
max_daily_loss: 0.05    # halt at -5%
```

## Structure

```
trader-bot/
  main.py           # entry point
  config.yaml       # settings
  server.py         # dashboard API
  dashboard/
    index.html      # web UI
  bot/
    data.py         # Binance market data
    strategy.py     # signal engine
    risk.py         # position sizing + fees
    portfolio.py    # positions + P&L
    tracker.py      # trade history
    engine.py       # main loop
```

## Risk controls

- 5% max per position
- 2% stop-loss, 4% take-profit
- 0.1% fees deducted on every trade
- Max 5 concurrent positions
- Daily loss limit: halt at -5%
- 10% cash always reserved

## Disclaimer

Paper trading only. No guaranteed profits. Don't risk money you can't lose.
