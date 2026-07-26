"""Private value coercions shared by provider catalogs and backends."""

from __future__ import annotations

from typing import Any


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _int_or_zero(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float_or_zero(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _float_or_none(value: Any) -> float | None:
    """Money-safe coercion: an absent or malformed number stays UNKNOWN.

    Never use ``_float_or_zero`` for a price. A missing ``price_hourly`` coerced
    to ``0.0`` reads downstream as "the provider quoted free", which sails past
    every USD ceiling and spends a budget blind (audit SAN-04). ``None`` makes
    the cost policy fail closed; a provider's own explicit ``0`` still comes
    back as a known zero.

    A NEGATIVE number is not a price either. It sorts to the front of the
    agent's cheapest-first menu, passes any positive USD ceiling (admission
    only rejects prices ABOVE the limit), and then SUBTRACTS from recorded
    spend — one garbled feed value would hand a tenant an unbounded budget.
    Unknown is the only safe reading.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    if price != price or price < 0:  # NaN and negatives are not prices
        return None
    return price


def price_sort_key(option: dict[str, Any]) -> tuple[bool, float, str]:
    """Cheapest-first ordering that parks unpriced SKUs at the END.

    An unknown price is not a cheap one — sorting ``None`` as ``0.0`` would put
    every unpriced option at the top of the agent's menu.
    """
    price = option.get("price_usd_per_hour")
    return (
        price is None,
        float(price if price is not None else 0.0),
        str(option.get("instance_type") or ""),
    )


def find_option(
    options: list[dict[str, Any]], *, instance_type: str
) -> dict[str, Any] | None:
    wanted = _norm(instance_type)
    for option in options:
        if _norm(option.get("instance_type")) == wanted:
            return option
    return None
