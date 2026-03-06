"""
Position sizing and risk management.

- ATR-based position sizing: risk a fixed $ amount per trade
- Correlation limits: max altcoins when BTC is in position
- Circuit breaker: pause after consecutive stop-losses
- Fee accounting on every trade
"""

from decimal import Decimal
from datetime import datetime, timedelta

FEE_RATE = Decimal("0.001")  # 0.1% per side (Binance taker)


def position_size_atr(
    cash: Decimal,
    risk_usd: float,
    atr_value: float,
    price: float,
    atr_sl_mult: float = 2.0,
) -> Decimal | None:
    """
    ATR-based sizing: risk a fixed $ amount per trade.
    size = risk_usd / (atr_sl_mult * ATR) * price
    This way, if the stop-loss triggers, you lose exactly risk_usd.
    """
    if atr_value <= 0 or price <= 0:
        return None

    stop_distance = atr_value * atr_sl_mult
    quantity = risk_usd / stop_distance
    size_usd = Decimal(str(quantity * price))

    # Never use more than 90% of available cash
    max_cash = cash * Decimal("0.90")
    size_usd = min(size_usd, max_cash)

    # Include fees
    size_after_fees = size_usd / (1 + FEE_RATE)

    if size_after_fees < Decimal("10"):
        return None

    return size_after_fees


def check_correlation(
    symbol: str,
    positions: dict,
    max_correlated: int = 2,
) -> bool:
    """
    Block new altcoin positions if BTC is held and we have too many alts.
    Returns True if the trade is allowed.
    """
    if symbol == "BTC/USDT":
        return True

    btc_held = "BTC/USDT" in positions
    if not btc_held:
        return True

    alt_count = sum(1 for s in positions if s != "BTC/USDT")
    return alt_count < max_correlated


class CircuitBreaker:
    """Pause trading after consecutive stop-losses."""

    def __init__(self, max_consecutive: int = 3, pause_hours: int = 4):
        self.max_consecutive = max_consecutive
        self.pause_hours = pause_hours
        self.consecutive_stops = 0
        self._paused_until: datetime | None = None

    def record_stop_loss(self):
        self.consecutive_stops += 1
        if self.consecutive_stops >= self.max_consecutive:
            self._paused_until = datetime.now() + timedelta(hours=self.pause_hours)

    def record_win(self):
        self.consecutive_stops = 0

    def is_paused(self) -> bool:
        if self._paused_until is None:
            return False
        if datetime.now() >= self._paused_until:
            self._paused_until = None
            self.consecutive_stops = 0
            return False
        return True

    @property
    def remaining_pause(self) -> str:
        if self._paused_until is None:
            return ""
        delta = self._paused_until - datetime.now()
        if delta.total_seconds() <= 0:
            return ""
        mins = int(delta.total_seconds() / 60)
        return f"{mins // 60}h{mins % 60:02d}m"


def buy_cost(quantity: Decimal, price: Decimal) -> Decimal:
    return quantity * price * (1 + FEE_RATE)


def sell_proceeds(quantity: Decimal, price: Decimal) -> Decimal:
    return quantity * price * (1 - FEE_RATE)


def round_quantity(quantity: Decimal, price: Decimal) -> Decimal:
    if price > Decimal("1000"):
        return quantity.quantize(Decimal("0.00001"))
    elif price > Decimal("1"):
        return quantity.quantize(Decimal("0.001"))
    elif price > Decimal("0.01"):
        return quantity.quantize(Decimal("1"))
    else:
        return quantity.quantize(Decimal("1"))
