
"""Pure fee calculations for external prediction markets."""

from decimal import Decimal, ROUND_HALF_UP, ROUND_DOWN


def calculate_taker_fee(
    *,
    price: Decimal,
    quantity: Decimal,
    fee_rate: Decimal,
    precision: Decimal,
) -> Decimal:
    """
    Calculate the taker fee in USD equivalent.

    fee = quantity * price * (1 - price) * fee_rate
    """

    if not Decimal("0") <= price <= Decimal("1"):
        raise ValueError("Price must be between 0 and 1")

    if quantity < 0:
        raise ValueError("Quantity cannot be negative")

    if fee_rate < 0:
        raise ValueError("Fee rate cannot be negative")

    if not precision.is_finite() or precision <= 0:
        raise ValueError("Invalid fee precision")

    fee = quantity * price * (Decimal("1") - price) * fee_rate

    return fee.quantize(
        precision,
        rounding=ROUND_HALF_UP,
    )


def calculate_hedge_cost(
    *,
    price: Decimal,
    quantity: Decimal,
    fee_rate: Decimal,
    precision: Decimal,
) -> Decimal:
    """
    Calculate the economic cost of buying hedge contracts.

    This returns a USD-equivalent cost.

    It does not model venue-specific token deductions yet.
    """

    fee = calculate_taker_fee(
        price=price,
        quantity=quantity,
        fee_rate=fee_rate,
        precision=precision,
    )

    return quantity * price + fee
