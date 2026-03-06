# trader-bot

Crypto trading bot. Real Binance data, no API key needed for paper trading.

## Strategy

- **EMA crossover** (9/21) with trend filter (EMA50)
- **RSI**(14) with divergence detection
- **MACD** histogram for direction strength
- **ADX**(14) market regime detection (trending vs ranging)
- **Bollinger Bands** squeeze for breakout anticipation
- **Multi-timeframe** filter (4h confirms 1h signals)
- **ATR-based stops** that adapt to volatility
- **Progressive trailing stop** (3 phases: breakeven → 1x ATR → 0.75x ATR)
- **Circuit breaker** pauses after 3 consecutive stop-losses
- **Correlation limit** caps altcoin exposure when BTC is held
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

Dashboard includes:
- TradingView candlestick charts with volume
- Equity curve
- Trade history with filters
- Market regime indicator
- Circuit breaker status
- Progressive trailing stop phases

## Backtesting

Test the strategy on historical data before risking capital:

```bash
python main.py --backtest --months 6 --limit 1000
```

Output includes: win rate, profit factor, Sharpe ratio, max drawdown, per-symbol breakdown.

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
htf: "4h"                     # higher timeframe confirmation
scan_interval: 60
max_positions: 5
risk_per_trade_usd: 15        # fixed $ risk per trade (ATR-based sizing)
max_correlated: 2             # max altcoins when BTC is held
circuit_breaker: 3            # consecutive SLs before 4h pause

# Regime detection
adx_trend_threshold: 25       # ADX > 25 = trending
adx_range_threshold: 20       # ADX < 20 = ranging
```

Stops are dynamic (ATR-based):
- **SL** = entry - 2x ATR
- **TP** = entry + 4x ATR (2:1 R/R)
- **Trail Phase 1**: at 1R profit → stop to breakeven
- **Trail Phase 2**: at 2R profit → trail at 1x ATR
- **Trail Phase 3**: at 4R profit → trail tight at 0.75x ATR

## Structure

```
trader-bot/
  main.py           # entry point (paper/live/backtest)
  config.yaml       # settings
  .env              # API keys (live mode only)
  server.py         # dashboard API + WebSocket
  dashboard/
    index.html      # web UI (TradingView charts)
  bot/
    data.py         # Binance connection + orders + retry
    strategy.py     # signal engine (EMA+RSI+MACD+ADX+BB)
    risk.py         # ATR sizing + correlation + circuit breaker
    portfolio.py    # positions, progressive trailing stops
    tracker.py      # trade history
    engine.py       # main loop + multi-TF + reconciliation
    backtest.py     # backtesting engine
```

## Disclaimer

No guaranteed profits. Always backtest and paper trade first. Don't risk money you can't lose.
