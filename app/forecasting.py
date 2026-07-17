from typing import Any


def source_strength_for_context(catalog_key: str, supports_elo: bool) -> str:
    source_strength = {
        "worldcup": "market+model",
        "futbol": "market+model",
        "tenis": "tennis_model",
        "baloncesto": "basketball_model",
    }.get(catalog_key, "market_only")

    if source_strength not in {"market+model", "tennis_model", "basketball_model"} and supports_elo:
        return "market+model"

    if source_strength not in {"market+model", "tennis_model", "basketball_model"}:
        return "market_only"

    return source_strength


def stake_limit_text(perfil: str) -> str:
    return {
        "conservador": "1.5% del bankroll",
        "moderado": "3% del bankroll",
        "agresivo": "8% del bankroll",
        "alto_riesgo": "50% del bankroll",
    }.get(perfil, "3% del bankroll")


def standard_risk_disclaimer() -> str:
    return "No garantiza beneficio. El stake esta limitado y debe subirse solo con historico, ROI y CLV positivo."


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(value, maximum))


def market_signal_label(
    market_support_count: int | None,
    market_width_pct: float | None,
    market_edge_vs_consensus: float | None,
) -> str:
    if market_support_count is None and market_width_pct is None and market_edge_vs_consensus is None:
        return "consenso_desconocido"

    support = int(market_support_count or 0)
    width = float(market_width_pct or 0)
    edge = float(market_edge_vs_consensus or 0)

    if support >= 5 and width <= 0.04 and edge >= 0.03:
        return "consenso_fuerte"
    if support >= 3 and width <= 0.07 and edge >= 0.015:
        return "consenso_sano"
    if support <= 1 or width >= 0.12:
        return "mercado_fragil"
    return "consenso_medio"


def _is_over_pick(pick: dict[str, Any]) -> bool:
    candidates = (
        pick.get("equipo"),
        pick.get("equipo_raw"),
        pick.get("tipo_resultado"),
        pick.get("tipo_resultado_raw"),
    )
    normalized = " ".join(str(value or "").strip().lower() for value in candidates)
    return any(token in normalized for token in ("over", "mas de", "más de"))


def apply_market_regime_guard(pick: dict[str, Any]) -> dict[str, Any]:
    adjusted = pick.copy()
    sport_key = str(adjusted.get("sport_key") or "").lower()
    market = str(adjusted.get("mercado") or "").lower()

    adjusted["market_guard_penalty_score"] = 0
    adjusted["market_guard_level"] = "none"
    adjusted["market_guard_blocked"] = False
    adjusted["market_guard_reasons"] = []

    if not sport_key.startswith("basketball_") or market not in {"totals", "alternate_totals"}:
        return adjusted

    support = int(adjusted.get("market_support_count") or 0)
    width = float(adjusted.get("market_width_pct") or 0)
    edge = float(adjusted.get("market_edge_vs_consensus") or 0)
    margin = float(adjusted.get("margen_cuota") or 0)
    value = float(adjusted.get("valor_esperado") or 0)

    is_wnba = "wnba" in sport_key
    is_over = _is_over_pick(adjusted)
    reasons: list[str] = []
    penalty = 0

    min_support = 4 if is_wnba else 3
    if support < min_support:
        penalty += 10 if is_wnba else 8
        reasons.append(f"support_bajo:{support}")

    fragile_width = 0.065 if is_wnba else 0.08
    caution_width = 0.04 if is_wnba else 0.05
    if width >= fragile_width:
        penalty += 9 if is_wnba else 7
        reasons.append(f"width_alta:{width:.3f}")
    elif width >= caution_width:
        penalty += 5 if is_wnba else 4
        reasons.append(f"width_media:{width:.3f}")

    min_edge = 0.025 if is_wnba else 0.015
    if edge < min_edge:
        penalty += 10 if is_wnba else 8
        reasons.append(f"edge_bajo:{edge:.3f}")
    elif edge < (0.035 if is_wnba else 0.025):
        penalty += 5 if is_wnba else 4
        reasons.append(f"edge_justo:{edge:.3f}")

    if margin < (1.04 if is_wnba else 1.03):
        penalty += 6
        reasons.append(f"margen_justo:{margin:.3f}")

    if value < (0.035 if is_wnba else 0.03):
        penalty += 6
        reasons.append(f"value_bajo:{value:.3f}")

    if is_wnba and is_over:
        penalty += 6
        reasons.append("wnba_over_regla_estricta")
        if support < 5:
            penalty += 4
            reasons.append("wnba_over_soporte_insuficiente")
        if edge < 0.03:
            penalty += 4
            reasons.append("wnba_over_edge_insuficiente")

    adjusted["market_guard_penalty_score"] = penalty
    adjusted["market_guard_reasons"] = reasons

    if penalty >= 26 or (is_wnba and is_over and (support < 4 or edge < 0.02 or width >= 0.07)):
        adjusted["market_guard_level"] = "block"
        adjusted["market_guard_blocked"] = True
    elif penalty >= 16:
        adjusted["market_guard_level"] = "high"
    elif penalty >= 8:
        adjusted["market_guard_level"] = "medium"
    elif penalty > 0:
        adjusted["market_guard_level"] = "low"

    return adjusted


def execution_score_for_pick(pick: dict[str, Any]) -> int:
    support = int(pick.get("market_support_count") or 0)
    width = float(pick.get("market_width_pct") or 0)
    edge = float(pick.get("market_edge_vs_consensus") or 0)
    stake = float(pick.get("stake") or 0)
    margin = float(pick.get("margen_cuota") or 0)
    market_guard_penalty = float(pick.get("market_guard_penalty_score") or 0)

    score = 45.0

    if support >= 6:
        score += 18
    elif support >= 4:
        score += 12
    elif support >= 2:
        score += 6
    elif support == 1:
        score -= 12

    if width <= 0.02:
        score += 14
    elif width <= 0.04:
        score += 10
    elif width <= 0.07:
        score += 4
    elif width >= 0.12:
        score -= 12
    elif width >= 0.08:
        score -= 7

    if edge >= 0.05:
        score += 14
    elif edge >= 0.03:
        score += 10
    elif edge >= 0.015:
        score += 6
    elif edge <= -0.02:
        score -= 10

    if stake >= 2:
        score += 6
    elif stake >= 1:
        score += 3

    if margin >= 1.06:
        score += 4
    elif margin < 1.01:
        score -= 4

    score -= market_guard_penalty * 0.85

    return int(round(_clamp(score, 0, 100)))


def ranking_score_for_pick(pick: dict[str, Any]) -> int:
    tier_bonus = {
        "stakazo": 24,
        "elite": 16,
        "premium": 8,
        "seguimiento": 0,
        "descartable": -12,
    }.get(str(pick.get("elite_tier") or "").lower(), 0)

    quality = float(pick.get("quality_score") or 0)
    reliability = float(pick.get("reliability_score") or 0)
    confidence = float(pick.get("puntuacion_confianza") or 0)
    expected_value = min(20.0, max(0.0, float(pick.get("valor_esperado") or 0) * 200))
    margin = min(15.0, max(0.0, (float(pick.get("margen_cuota") or 0) - 1.0) * 250))
    execution = float(pick.get("execution_score") or execution_score_for_pick(pick))
    historical_penalty = float(pick.get("historical_penalty_score") or 0)
    market_guard_penalty = float(pick.get("market_guard_penalty_score") or 0)
    stake = float(pick.get("stake") or 0)

    score = 0.0
    score += quality * 0.25
    score += reliability * 0.22
    score += confidence * 0.18
    score += execution * 0.20
    score += expected_value
    score += margin
    score += tier_bonus
    score += min(6.0, stake * 2.0)
    score -= historical_penalty * 1.35
    score -= market_guard_penalty * 1.15

    return int(round(_clamp(score, 0, 100)))


def enrich_pick_ranking(pick: dict[str, Any]) -> dict[str, Any]:
    """
    Enriquece un pick con scores de ranking y ejecución.
    
    Si está disponible el módulo de calibración, usa scores mejorados
    con penalizaciones dinámicas. Si no, usa los scores base.
    """
    enriched = apply_market_regime_guard(pick)
    enriched["market_signal"] = market_signal_label(
        enriched.get("market_support_count"),
        enriched.get("market_width_pct"),
        enriched.get("market_edge_vs_consensus"),
    )
    
    # Intentar usar scoring calibrado
    try:
        from app.calibrated_scoring import (
            calibrated_execution_score,
            calibrated_ranking_score,
            get_calibration_metadata,
        )
        enriched["execution_score"] = calibrated_execution_score(enriched)
        enriched["ranking_score"] = calibrated_ranking_score(enriched)
        enriched["calibration"] = get_calibration_metadata(enriched)
    except Exception:
        # Fallback a scores base si falla calibración
        enriched["execution_score"] = execution_score_for_pick(enriched)
        enriched["ranking_score"] = ranking_score_for_pick(enriched)
    
    return enriched


def attach_context_to_pick(
    recomendacion: dict[str, Any],
    perfil: str,
    perfil_label: str,
    modo: str,
    modo_label: str,
    filtro_mercados: str,
    contexto_deporte: dict[str, Any],
) -> dict[str, Any]:
    recomendacion["perfil"] = perfil
    recomendacion["perfil_es"] = perfil_label
    recomendacion["modo"] = modo
    recomendacion["modo_es"] = modo_label
    recomendacion["filtro_mercados"] = filtro_mercados
    recomendacion["sport_key"] = contexto_deporte["sport_key"]
    recomendacion["sport_label"] = contexto_deporte["sport_label"]
    recomendacion["league_key"] = contexto_deporte["league_key"]
    recomendacion["league_label"] = contexto_deporte["league_label"]
    return recomendacion
