# If you update this file, you must consult sandbox.md to see whether sandbox.md needs to be updated. sandbox.md must not exceed 100 lines.
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
    """Keep missing, malformed, negative, and NaN prices unknown.

    Coercing any of them to zero would bypass spend ceilings; explicit provider
    zero remains a known price.
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
    """Cheapest first, with unknown prices last."""
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
