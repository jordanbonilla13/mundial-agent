import os
from typing import Any, Callable
from app.odds_buckets import odds_bucket_for_value


def _aggressive_performance_mode(mode: str | None) -> bool:
    return str(mode or "").strip().lower() in {"agresivo", "alto_riesgo"}


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
    operating_mode: str = "equilibrado",
) -> dict[str, Any]:
    dashboard = load_dashboard()
    mode = str(operating_mode or "equilibrado").strip().lower()
    min_sample = _int_env("PERF_GUARD_MIN_SAMPLE", 12)
    min_roi = _float_env("PERF_GUARD_MIN_ROI", -2.0)
    min_hit_rate = _float_env("PERF_GUARD_MIN_HIT_RATE", 42.0)
    min_clv_positive_pct = _float_env("PERF_GUARD_MIN_CLV_POSITIVE_PCT", 45.0)
    allowed_sports = _csv_env("PERF_GUARD_ALLOW_SPORTS")
    allowed_leagues = _csv_env("PERF_GUARD_ALLOW_LEAGUES")
    allowed_markets = _csv_env("PERF_GUARD_ALLOW_MARKETS")
    allowed_league_markets = _csv_env("PERF_GUARD_ALLOW_LEAGUE_MARKETS")
    allowed_odds_buckets = _csv_env("PERF_GUARD_ALLOW_ODDS_BUCKETS")
    severe_min_sample = _int_env("PERF_GUARD_SEVERE_MIN_SAMPLE", 6)
    severe_max_roi = _float_env("PERF_GUARD_SEVERE_MAX_ROI", -10.0)
    severe_max_hit_rate = _float_env("PERF_GUARD_SEVERE_MAX_HIT_RATE", 40.0)
    severe_max_clv_positive_pct = _float_env("PERF_GUARD_SEVERE_MAX_CLV_POSITIVE_PCT", 38.0)

    blocked_sports: dict[str, dict[str, Any]] = {}
    blocked_leagues: dict[str, dict[str, Any]] = {}
    blocked_markets: dict[str, dict[str, Any]] = {}
    blocked_league_markets: dict[str, dict[str, Any]] = {}
    blocked_odds_buckets: dict[str, dict[str, Any]] = {}

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

    def _evaluate_severe_bucket(rows: list[dict[str, Any]], target: dict[str, dict[str, Any]]) -> None:
        for row in rows:
            closed = int(row.get("cerradas") or 0)
            if closed < severe_min_sample:
                continue

            roi = float(row.get("roi") or 0)
            hit_rate = float(row.get("hit_rate") or 0)
            clv_positive_pct = row.get("clv_positivo_pct")
            clv_positive_value = float(clv_positive_pct) if clv_positive_pct is not None else None
            if roi > severe_max_roi and hit_rate > severe_max_hit_rate and (clv_positive_value is None or clv_positive_value > severe_max_clv_positive_pct):
                continue

            reasons: list[str] = []
            if roi <= severe_max_roi:
                reasons.append(f"roi_critico:{roi:.2f}")
            if hit_rate <= severe_max_hit_rate:
                reasons.append(f"hit_rate_critico:{hit_rate:.2f}")
            if clv_positive_value is not None and clv_positive_value <= severe_max_clv_positive_pct:
                reasons.append(f"clv_positivo_critico:{clv_positive_value:.2f}")
            if not reasons:
                continue

            key = str(row.get("nombre") or "").strip()
            if not key:
                continue
            target[key] = {
                "sample_closed": closed,
                "roi": roi,
                "hit_rate": hit_rate,
                "clv_positivo_pct": clv_positive_value,
                "reasons": reasons,
                "severity": "critical",
            }

    _evaluate_bucket(dashboard.get("por_deporte") or [], blocked_sports)
    _evaluate_bucket(dashboard.get("por_liga") or [], blocked_leagues)
    _evaluate_severe_bucket(dashboard.get("por_mercado") or [], blocked_markets)
    _evaluate_severe_bucket(dashboard.get("por_liga_mercado") or [], blocked_league_markets)
    _evaluate_severe_bucket(dashboard.get("por_rango_cuota") or [], blocked_odds_buckets)

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

    unblocked_markets = [
        key for key in list(blocked_markets)
        if key.strip().lower() in allowed_markets
    ]
    for key in unblocked_markets:
        blocked_markets.pop(key, None)

    unblocked_league_markets = [
        key for key in list(blocked_league_markets)
        if key.strip().lower() in allowed_league_markets
    ]
    for key in unblocked_league_markets:
        blocked_league_markets.pop(key, None)

    unblocked_odds_buckets = [
        key for key in list(blocked_odds_buckets)
        if key.strip().lower() in allowed_odds_buckets
    ]
    for key in unblocked_odds_buckets:
        blocked_odds_buckets.pop(key, None)

    if _aggressive_performance_mode(mode):
        blocked_sports = {}
        blocked_leagues = {}

    return {
        "operating_mode": mode,
        "blocked_sports": blocked_sports,
        "blocked_leagues": blocked_leagues,
        "blocked_markets": blocked_markets,
        "blocked_league_markets": blocked_league_markets,
        "blocked_odds_buckets": blocked_odds_buckets,
        "overrides": {
            "allowed_sports": sorted(allowed_sports),
            "allowed_leagues": sorted(allowed_leagues),
            "allowed_markets": sorted(allowed_markets),
            "allowed_league_markets": sorted(allowed_league_markets),
            "allowed_odds_buckets": sorted(allowed_odds_buckets),
            "unblocked_sports": unblocked_sports,
            "unblocked_leagues": unblocked_leagues,
            "unblocked_markets": unblocked_markets,
            "unblocked_league_markets": unblocked_league_markets,
            "unblocked_odds_buckets": unblocked_odds_buckets,
            "performance_guard_disabled": _aggressive_performance_mode(mode),
        },
        "thresholds": {
            "min_sample": min_sample,
            "min_roi": min_roi,
            "min_hit_rate": min_hit_rate,
            "min_clv_positive_pct": min_clv_positive_pct,
            "severe_min_sample": severe_min_sample,
            "severe_max_roi": severe_max_roi,
            "severe_max_hit_rate": severe_max_hit_rate,
            "severe_max_clv_positive_pct": severe_max_clv_positive_pct,
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
    blocked_markets = guard.get("blocked_markets") or {}
    blocked_league_markets = guard.get("blocked_league_markets") or {}
    blocked_odds_buckets = guard.get("blocked_odds_buckets") or {}

    sport_label = str(adjusted.get("sport_label") or "").strip()
    league_label = str(adjusted.get("league_label") or "").strip()
    market_label = str(adjusted.get("mercado") or "").strip()
    league_market_key = f"{league_label} :: {market_label}" if league_label and market_label else ""
    odds_bucket = odds_bucket_for_value(adjusted.get("cuota_apuesta") or adjusted.get("cuota"))

    blocked_reason = None
    if league_market_key and league_market_key in blocked_league_markets:
        blocked_reason = f"Segmento bloqueado por auditoria: {league_market_key}"
    elif market_label and market_label in blocked_markets:
        blocked_reason = f"Mercado bloqueado por auditoria: {market_label}"
    elif odds_bucket and odds_bucket in blocked_odds_buckets:
        blocked_reason = f"Rango de cuota bloqueado por auditoria: {odds_bucket}"
    elif league_label and league_label in blocked_leagues:
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
