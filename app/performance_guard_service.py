import os
from typing import Any, Callable


def _csv_env(name: str) -> set[str]:
    raw = os.getenv(name, "")
    return {
        item.strip().lower()
        for item in raw.split(",")
        if item and item.strip()
    }


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def build_performance_guard(
    *,
    load_dashboard: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    dashboard = load_dashboard()
    min_sample = _int_env("PERF_GUARD_MIN_SAMPLE", 12)
    min_roi = _float_env("PERF_GUARD_MIN_ROI", -2.0)
    min_hit_rate = _float_env("PERF_GUARD_MIN_HIT_RATE", 42.0)
    min_clv_positive_pct = _float_env("PERF_GUARD_MIN_CLV_POSITIVE_PCT", 45.0)
    allowed_sports = _csv_env("PERF_GUARD_ALLOW_SPORTS")
    allowed_leagues = _csv_env("PERF_GUARD_ALLOW_LEAGUES")

    blocked_sports: dict[str, dict[str, Any]] = {}
    blocked_leagues: dict[str, dict[str, Any]] = {}

    def _evaluate_bucket(rows: list[dict[str, Any]], target: dict[str, dict[str, Any]]) -> None:
        for row in rows:
            closed = int(row.get("cerradas") or 0)
            if closed < min_sample:
                continue

            roi = float(row.get("roi") or 0)
            hit_rate = float(row.get("hit_rate") or 0)
            clv_positive_pct = row.get("clv_positivo_pct")
            reasons: list[str] = []

            if roi < min_roi:
                reasons.append(f"roi_bajo:{roi:.2f}")
            if hit_rate < min_hit_rate:
                reasons.append(f"hit_rate_bajo:{hit_rate:.2f}")
            if clv_positive_pct is not None and float(clv_positive_pct) < min_clv_positive_pct:
                reasons.append(f"clv_positivo_bajo:{float(clv_positive_pct):.2f}")

            if not reasons:
                continue

            key = str(row.get("nombre") or "").strip()
            if not key:
                continue
            target[key] = {
                "sample_closed": closed,
                "roi": roi,
                "hit_rate": hit_rate,
                "clv_positivo_pct": float(clv_positive_pct) if clv_positive_pct is not None else None,
                "reasons": reasons,
            }

    _evaluate_bucket(dashboard.get("por_deporte") or [], blocked_sports)
    _evaluate_bucket(dashboard.get("por_liga") or [], blocked_leagues)

    unblocked_sports = [
        key for key in list(blocked_sports)
        if key.strip().lower() in allowed_sports
    ]
    for key in unblocked_sports:
        blocked_sports.pop(key, None)

    unblocked_leagues = [
        key for key in list(blocked_leagues)
        if key.strip().lower() in allowed_leagues
    ]
    for key in unblocked_leagues:
        blocked_leagues.pop(key, None)

    return {
        "blocked_sports": blocked_sports,
        "blocked_leagues": blocked_leagues,
        "overrides": {
            "allowed_sports": sorted(allowed_sports),
            "allowed_leagues": sorted(allowed_leagues),
            "unblocked_sports": unblocked_sports,
            "unblocked_leagues": unblocked_leagues,
        },
        "thresholds": {
            "min_sample": min_sample,
            "min_roi": min_roi,
            "min_hit_rate": min_hit_rate,
            "min_clv_positive_pct": min_clv_positive_pct,
        },
    }


def apply_performance_guard_to_pick(
    pick: dict[str, Any],
    guard: dict[str, Any] | None,
) -> dict[str, Any]:
    adjusted = pick.copy()
    guard = guard or {}
    blocked_sports = guard.get("blocked_sports") or {}
    blocked_leagues = guard.get("blocked_leagues") or {}

    sport_label = str(adjusted.get("sport_label") or "").strip()
    league_label = str(adjusted.get("league_label") or "").strip()

    blocked_reason = None
    if league_label and league_label in blocked_leagues:
        blocked_reason = f"Liga bloqueada por rendimiento historico: {league_label}"
    elif sport_label and sport_label in blocked_sports:
        blocked_reason = f"Deporte bloqueado por rendimiento historico: {sport_label}"

    adjusted["performance_guard_blocked"] = False
    adjusted["performance_guard_reason"] = None

    if not blocked_reason:
        return adjusted

    adjusted["performance_guard_blocked"] = True
    adjusted["performance_guard_reason"] = blocked_reason
    adjusted["stake"] = 0
    adjusted["importe_sugerido"] = 0
    adjusted["stake_pct_bankroll"] = 0
    adjusted["kelly_fraccional"] = 0
    adjusted["recomendacion"] = "No apostar"
    motivo = str(adjusted.get("motivo") or "").strip()
    adjusted["motivo"] = f"{motivo} | {blocked_reason}".strip(" |")
    return adjusted
