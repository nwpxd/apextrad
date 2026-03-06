"""
StockConnector - Alpaca Markets for US equities.
Alpaca offers commission-free trading with a great API.
Paper trading available at paper-api.alpaca.markets.
"""

import logging
from config.settings import Settings

log = logging.getLogger("StockConn")

ALPACA_BASE = "https://api.alpaca.markets"
ALPACA_PAPER = "https://paper-api.alpaca.markets"


class StockConnector:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = None

    async def connect(self):
        base_url = ALPACA_BASE if self.settings.live_mode else ALPACA_PAPER
        mode = "LIVE" if self.settings.live_mode else "PAPER"

        if not self.settings.alpaca_api_key:
            log.warning("[WARN]  No Alpaca API key - stock trading disabled")
            return

        try:
            import alpaca_trade_api as tradeapi
            self._client = tradeapi.REST(
                self.settings.alpaca_api_key,
                self.settings.alpaca_secret,
                base_url=base_url,
            )
            account = self._client.get_account()
            log.info(f"[OK] Stock connector: Alpaca {mode} | Cash: ${float(account.cash):,.2f}")
        except ImportError:
            log.warning("alpaca-trade-api not installed - pip install alpaca-trade-api")
        except Exception as e:
            log.error(f"Alpaca connection failed: {e}")

    async def market_buy(self, symbol: str, quantity: float) -> dict:
        clean = symbol.replace("/USD", "")
        if self._client is None:
            log.info(f"[PAPER] stock buy {quantity:.2f} shares {clean}")
            return {"id": "paper"}
        try:
            order = self._client.submit_order(
                symbol=clean,
                qty=quantity,
                side="buy",
                type="market",
                time_in_force="day",
            )
            log.info(f"[OK] Stock BUY: {order.id} - {clean}")
            return {"id": order.id, "status": order.status}
        except Exception as e:
            log.error(f"Stock buy failed {clean}: {e}")
            return None

    async def market_sell(self, symbol: str, quantity: float) -> dict:
        clean = symbol.replace("/USD", "")
        if self._client is None:
            log.info(f"[PAPER] stock sell {quantity:.2f} shares {clean}")
            return {"id": "paper"}
        try:
            order = self._client.submit_order(
                symbol=clean,
                qty=quantity,
                side="sell",
                type="market",
                time_in_force="day",
            )
            log.info(f"[OK] Stock SELL: {order.id} - {clean}")
            return {"id": order.id, "status": order.status}
        except Exception as e:
            log.error(f"Stock sell failed {clean}: {e}")
            return None

    async def disconnect(self):
        pass  # alpaca-trade-api is synchronous, no cleanup needed
