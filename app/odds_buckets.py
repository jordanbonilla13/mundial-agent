from __future__ import annotations

from typing import Iterable


ODDS_BUCKETS: list[tuple[float, float | None, str]] = [
    (1.01, 1.39, "1.01-1.39"),
    (1.40, 1.59, "1.40-1.59"),
    (1.60, 1.79, "1.60-1.79"),
    (1.80, 1.99, "1.80-1.99"),
    (2.00, 2.29, "2.00-2.29"),
    (2.30, 2.79, "2.30-2.79"),
    (2.80, None, "2.80+"),
]


def odds_bucket_for_value(value: float | int | str | None) -> str:
    try:
        cuota = float(value)
    except (TypeError, ValueError):
        return "sin_cuota"

    if cuota <= 0:
        return "sin_cuota"

    for lower, upper, label in ODDS_BUCKETS:
        if cuota < lower:
            continue
        if upper is None or cuota <= upper:
            return label

    return ODDS_BUCKETS[-1][2]


def odds_bucket_sort_key(label: str) -> tuple[int, float]:
    normalized = str(label or "").strip()
    for index, (lower, _upper, bucket_label) in enumerate(ODDS_BUCKETS):
        if normalized == bucket_label:
            return (0, float(index))
    if normalized == "sin_cuota":
        return (1, 999.0)
    return (2, 999.0)


def ordered_odds_buckets(labels: Iterable[str]) -> list[str]:
    return sorted({str(label or "") for label in labels}, key=odds_bucket_sort_key)
