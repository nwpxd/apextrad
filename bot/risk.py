"""Position sizing and risk rules. Conservative by default."""

from decimal import Decimal

FEE_RATE = Decimal("0.001")  # 0.1% per side (Binance taker)


def position_size(
    cash: Decimal,
    portfolio_value: Decimal,
    num_positions: int,
    max_positions: int,
    risk_per_trade: float,
) -> Decimal | None:
    """Returns USD size to allocate, or None if blocked."""
    if num_positions >= max_positions:
        return None

    # Risk-based sizing: risk_per_trade % of portfolio
    size = portfolio_value * Decimal(str(risk_per_trade))

    # Never use more than 90% of available cash
    max_cash = cash * Decimal("0.90")
    size = min(size, max_cash)

    # Include fees in cost
    size_after_fees = size / (1 + FEE_RATE)

    if size_after_fees < Decimal("10"):
        return None

    return size_after_fees


def buy_cost(quantity: Decimal, price: Decimal) -> Decimal:
    """Actual cost including fees."""
    return quantity * price * (1 + FEE_RATE)


def sell_proceeds(quantity: Decimal, price: Decimal) -> Decimal:
    """Actual proceeds after fees."""
    return quantity * price * (1 - FEE_RATE)


def round_quantity(quantity: Decimal, price: Decimal) -> Decimal:
    """Round to exchange-compatible precision."""
    if price > Decimal("1000"):
        return quantity.quantize(Decimal("0.00001"))
    elif price > Decimal("1"):
        return quantity.quantize(Decimal("0.001"))
    elif price > Decimal("0.01"):
        return quantity.quantize(Decimal("1"))
    else:
        return quantity.quantize(Decimal("1"))
