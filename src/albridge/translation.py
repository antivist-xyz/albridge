"""Pure probability-to-Antivist price translation."""

from dataclasses import dataclass
from decimal import Decimal


OUTCOMES = ("WIN", "DRAW", "LOSS")


@dataclass(frozen=True)
class TranslationResult:
    outcome: str
    source_price: Decimal
    entry_price: Decimal
    yes_anchor: Decimal
    no_anchor: Decimal


def calculate_binary_entry(
    *,
    probability: Decimal,
    start_anchor: Decimal,
    target_anchor: Decimal,
) -> Decimal:
    """Map a probability onto two terminal anchors."""

    if not probability.is_finite():
        raise ValueError("Probability must be finite")

    if not Decimal("0") <= probability <= Decimal("1"):
        raise ValueError("Probability must be between 0 and 1")

    return start_anchor + probability * (
        target_anchor - start_anchor
    )


def calculate_synthetic_no_anchor(
    *,
    outcome: str,
    anchors: dict[str, Decimal],
    representative_prices: dict[str, Decimal],
) -> Decimal:
    """Calculate the weighted anchor of the alternative outcomes."""

    if outcome not in OUTCOMES:
        raise ValueError(f"Invalid outcome: {outcome}")

    others = [name for name in OUTCOMES if name != outcome]

    for name in OUTCOMES:
        if name not in anchors:
            raise ValueError(f"Missing anchor: {name}")

    for name in others:
        if name not in representative_prices:
            raise ValueError(f"Missing representative price: {name}")

    weights = [representative_prices[name] for name in others]

    if any(
        not weight.is_finite()
        or weight < 0
        or weight > 1
        for weight in weights
    ):
        raise ValueError("Invalid representative price")

    denominator = sum(weights, Decimal("0"))

    if denominator <= 0:
        raise ValueError("No valid alternative outcome weights")

    return sum(
        (
            representative_prices[name] * anchors[name]
            for name in others
        ),
        Decimal("0"),
    ) / denominator


def translate_yes_price(
    *,
    outcome: str,
    price: Decimal,
    anchors: dict[str, Decimal],
    representative_prices: dict[str, Decimal],
) -> TranslationResult:
    """Translate one external YES price into Antivist price space."""

    no_anchor = calculate_synthetic_no_anchor(
        outcome=outcome,
        anchors=anchors,
        representative_prices=representative_prices,
    )

    yes_anchor = anchors[outcome]

    entry_price = calculate_binary_entry(
        probability=price,
        start_anchor=no_anchor,
        target_anchor=yes_anchor,
    )

    return TranslationResult(
        outcome=outcome,
        source_price=price,
        entry_price=entry_price,
        yes_anchor=yes_anchor,
        no_anchor=no_anchor,
    )
