"""
APEX Diagnostic Tool
Usage: python diagnose.py
"""
import asyncio
import sys
import os
from dotenv import load_dotenv
load_dotenv()

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

async def diagnose():
    print("\n" + "="*60)
    print("  APEX DIAGNOSTIC")
    print("="*60)

    # Step 1: Test ccxt directly
    print("\n[1] Testing ccxt connection to Binance...")
    try:
        import ccxt.async_support as ccxt
        exchange = ccxt.binance({"enableRateLimit": True})
        ticker = await exchange.fetch_ticker("BTC/USDT")
        print(f"    OK - BTC/USDT price: ${ticker['last']:,.2f}")
        print(f"    Volume: ${ticker.get('quoteVolume',0):,.0f}")

        print("\n[2] Fetching OHLCV data...")
        ohlcv = await exchange.fetch_ohlcv("BTC/USDT", "1h", limit=100)
        print(f"    OK - got {len(ohlcv)} candles")

        print("\n[3] Running strategies on BTC/USDT...")
        import pandas as pd
        df = pd.DataFrame(ohlcv, columns=["timestamp","open","high","low","close","volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("timestamp", inplace=True)

        from config.settings import Settings
        from strategies.momentum import MomentumStrategy
        from strategies.mean_reversion import MeanReversionStrategy
        from strategies.arbitrage import ArbitrageStrategy

        settings = Settings.load("config/settings.yaml")
        mom = MomentumStrategy(settings)
        mr = MeanReversionStrategy(settings)
        arb = ArbitrageStrategy(settings)

        symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "LINK/USDT"]

        print(f"\n{'SYMBOL':<15} {'MOM':>7} {'MR':>7} {'ARB':>7} {'COMPOSITE':>11} {'ACTION'}")
        print("-"*65)

        for symbol in symbols:
            try:
                t = await exchange.fetch_ticker(symbol)
                o = await exchange.fetch_ohlcv(symbol, "1h", limit=100)
                if not o or len(o) < 20:
                    print(f"{symbol:<15} not enough data")
                    continue
                df2 = pd.DataFrame(o, columns=["timestamp","open","high","low","close","volume"])
                df2["timestamp"] = pd.to_datetime(df2["timestamp"], unit="ms")
                df2.set_index("timestamp", inplace=True)

                m = await mom.signal(symbol, df2, t)
                r = await mr.signal(symbol, df2, t)
                a = await arb.signal(symbol, t)

                ms = m.get("score", 0)
                rs = r.get("score", 0)
                as_ = a.get("score", 0)
                composite = ms * 0.58 + rs * 0.27 + as_ * 0.15
                action = "*** BUY ***" if composite > 0.18 else ("*** SELL ***" if composite < -0.18 else "hold")

                print(f"{symbol:<15} {ms:>7.3f} {rs:>7.3f} {as_:>7.3f} {composite:>11.3f} {action}")
                print(f"  mom: {m.get('reason','')}")
                print(f"  mr:  {r.get('reason','')}")

            except Exception as e:
                print(f"{symbol:<15} ERROR: {e}")

        await exchange.close()

        print("\n" + "="*60)
        print("  If composite > 0.18 = BUY signal fires")
        print("  If all composites are low, threshold needs lowering")
        print("="*60)

    except ImportError:
        print("    ERROR: ccxt not installed - run: pip install ccxt==4.4.98")
    except Exception as e:
        print(f"    ERROR: {e}")
        print("    Check your BINANCE_API_KEY in .env file")

asyncio.run(diagnose())
