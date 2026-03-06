# — Fully Automated AI Trading System

Multi-strategy, multi-market AI trading agent with backtesting, on-chain analysis,
order book depth signals, real-time performance tracking, and a live dashboard.

---

## ⚡ Quick Start

```bash
# 1. Install
pip install -r requirements.txt

# 2. Set API keys (edit .env)
export BINANCE_API_KEY="..."
export BINANCE_SECRET="..."
export ALPACA_API_KEY="..."
export ALPACA_SECRET="..."
export ANTHROPIC_API_KEY="..."   # For AI sentiment

# 3. ALWAYS backtest first
python backtest.py --symbol BTC/USDT --days 365 --walk-forward

# 4. Paper trade
python main.py --wallet my_wallet --limit 1000 --mode paper

# 5. Open live dashboard (separate terminal)
python dashboard_api.py
# → Open http://localhost:8080

# 6. Go live when satisfied with paper performance
python main.py --wallet my_wallet --limit 1000 --mode live
```

---

## 🏗️ Full Architecture

```
APEX Trader v2
├── main.py                      ← Entry point
├── backtest.py                  ← Backtesting + walk-forward optimizer
├── dashboard_api.py             ← Real-time WebSocket + REST API
├── dashboard/index.html         ← Live monitoring dashboard
├── config/
│   ├── settings.py
│   └── settings.yaml
├── core/
│   ├── agent.py                 ← Main orchestrator
│   ├── portfolio.py             ← Position + P&L tracking
│   ├── risk_manager.py          ← Kelly sizing + hard limits
│   ├── market_data.py           ← Crypto + stock data hub
│   ├── orderbook.py             ← Order book depth analysis  [NEW]
│   ├── onchain.py               ← TVL, flows, Fear & Greed   [NEW]
│   └── performance.py           ← Sharpe/Calmar/drawdown     [NEW]
├── strategies/
│   ├── meta_ai.py               ← Regime-aware master brain
│   ├── momentum.py              ← RSI + MACD + EMA
│   ├── mean_reversion.py        ← Bollinger Bands + Z-score
│   ├── sentiment.py             ← LLM news analysis
│   └── arbitrage.py             ← Spread signals
└── connectors/
    ├── crypto_connector.py      ← Binance
    └── stock_connector.py       ← Alpaca
```

---

## 🧠 Signal Stack (7 layers)

| Layer | Signal | What it detects |
|---|---|---|
| 1 | Momentum | RSI, MACD, EMA crossovers |
| 2 | Mean Reversion | Bollinger Bands, Z-score |
| 3 | Sentiment AI | LLM-analyzed headlines & social |
| 4 | Arbitrage | Spread analysis, execution efficiency |
| 5 | Order Book | Bid/ask imbalance, walls, liquidity depth |
| 6 | On-Chain | TVL trends, DeFi metrics, Fear & Greed Index |
| 7 | MetaAI | Regime detection + adaptive weight synthesis |

---

## 📊 Backtesting + Walk-Forward Optimization

Walk-forward optimization tests your strategy on 5 independent out-of-sample windows,
preventing overfitting. A strategy that works on unseen data in 4 of 5 folds is
genuinely robust.

```bash
python backtest.py --symbol BTC/USDT --days 365 --walk-forward
```

Target metrics before going live:
- Sharpe Ratio > 1.0
- Max Drawdown < 20%
- Win Rate > 50%
- Profit Factor > 1.5
- Consistency: 4+ of 5 folds profitable

---

## 🛡️ Risk Controls

| Control | Value | Notes |
|---|---|---|
| Spending limit | Your --limit | Hard ceiling, never breached |
| Max position | 10% | Kelly-scaled by confidence |
| Stop loss | 5% | Per position |
| Take profit | 15% | Per position |
| Daily loss halt | 10% | Halts all trading |
| Memecoin cap | 3% | High risk = small size |
| Liquidity filter | $50k | Skips illiquid pairs |

---

## 📈 Dashboard (http://localhost:8080)

- Live equity curve with P&L coloring
- Sharpe ratio, drawdown, win rate, profit factor
- Open positions with live P&L
- Real-time signal feed (BUY/SELL/HOLD)
- Fear & Greed index with gauge
- MetaAI strategy weight bars
- Full trade history
- System log terminal

---

## ⚠️ Disclaimer

No system produces guaranteed profits. Always backtest, always paper trade first,
never risk money you can't afford to lose.
