"""
Sistema premium de cálculo de scores con calibración dinámica.

Este módulo integra:
- Scores base de ejecución y ranking
- Penalizaciones dinámicas por liga
- Ajustes de threshold por mercado
- Boosts por tier
- Multiplicadores generales

Convierte el histórico observado en scoring en tiempo real.
"""

from typing import Any, Optional
from app.forecasting import (
    execution_score_for_pick as base_execution_score,
    ranking_score_for_pick as base_ranking_score,
    _clamp,
)
from app.calibration import (
    generate_calibration_snapshot,
    get_penalty_factor_for_league,
    get_market_threshold_adjustment,
    get_penalty_factor_for_league_market,
    get_league_market_threshold_adjustment,
    get_tier_boost,
    get_model_confidence_multiplier,
    CalibrationSnapshot,
)


# Cache global para evitar regenerar el snapshot en cada llamada
_calibration_cache: Optional[tuple[CalibrationSnapshot, float]] = None
_cache_timestamp = 0
_cache_ttl_seconds = 300  # 5 minutos


def _get_cached_calibration() -> CalibrationSnapshot:
    """
    Retorna calibración cacheada, regenerando si es necesario.
    """
    global _calibration_cache, _cache_timestamp
    
    import time
    now = time.time()
    
    if _calibration_cache is None or (now - _cache_timestamp) > _cache_ttl_seconds:
        try:
            calibration = generate_calibration_snapshot()
            _calibration_cache = (calibration, now)
            _cache_timestamp = now
        except Exception:
            # Si falla la calibración, retornar un snapshot empty
            from datetime import datetime, timezone
            from app.calibration import CalibrationSnapshot
            return CalibrationSnapshot(
                timestamp=datetime.now(timezone.utc).isoformat(),
                total_picks_evaluated=0,
                segments_by_type={},
                model_adjustments={},
                alerts=[],
            )
    
    if _calibration_cache:
        return _calibration_cache[0]
    
    from datetime import datetime, timezone
    from app.calibration import CalibrationSnapshot
    return CalibrationSnapshot(
        timestamp=datetime.now(timezone.utc).isoformat(),
        total_picks_evaluated=0,
        segments_by_type={},
        model_adjustments={},
        alerts=[],
    )


def calibrated_execution_score(pick: dict[str, Any]) -> int:
    """
    Execution score mejorado con ajustes por mercado desde calibración.
    """
    
    base_score = float(base_execution_score(pick))
    calibration = _get_cached_calibration()
    
    # Obtener ajustes por mercado y por nicho liga + mercado
    league = pick.get("league_label") or pick.get("liga") or "Unknown"
    market = pick.get("mercado", "Unknown")
    market_adj = get_market_threshold_adjustment(market, calibration)
    league_market_adj = get_league_market_threshold_adjustment(league, market, calibration)
    
    # Si el threshold sube, el score operativo debe bajar para filtrar más.
    market_impact = market_adj * 10
    league_market_impact = league_market_adj * 12
    
    adjusted_score = base_score - market_impact - league_market_impact
    return int(round(_clamp(adjusted_score, 0, 100)))


def calibrated_ranking_score(pick: dict[str, Any]) -> int:
    """
    Ranking score mejorado con:
    - Penalizaciones por liga
    - Boosts por tier
    - Multiplicador general de confianza
    """
    
    base_score = float(base_ranking_score(pick))
    calibration = _get_cached_calibration()
    
    # Aplicar penalización por liga
    league = pick.get("league_label") or pick.get("liga") or "Unknown"
    market = pick.get("mercado", "Unknown")
    league_penalty_factor = get_penalty_factor_for_league(league, calibration)
    league_penalty_ratio = max(0.0, min(1.0, 1.0 - league_penalty_factor))
    league_market_penalty_factor = get_penalty_factor_for_league_market(league, market, calibration)
    league_market_penalty_ratio = max(0.0, min(1.0, 1.0 - league_market_penalty_factor))
    league_impact = base_score * league_penalty_ratio
    league_market_impact = base_score * league_market_penalty_ratio
    
    # Aplicar boost por tier
    tier = str(pick.get("elite_tier") or "").lower()
    tier_boost = get_tier_boost(tier, calibration)
    tier_impact = tier_boost * 10  # Convertir a puntos
    
    # Aplicar multiplicador general de confianza
    model_multiplier = get_model_confidence_multiplier(calibration)
    
    # Composición final
    adjusted_score = (base_score - league_impact - league_market_impact + tier_impact) * model_multiplier
    if calibration.total_picks_evaluated <= 0:
        adjusted_score = base_score
    elif adjusted_score < base_score * 0.55 and league_penalty_ratio <= 0.05 and league_market_penalty_ratio <= 0.05 and tier_boost == 0 and model_multiplier >= 0.95:
        adjusted_score = max(adjusted_score, base_score * 0.75)
    
    return int(round(_clamp(adjusted_score, 0, 100)))


def get_calibration_metadata(pick: dict[str, Any]) -> dict[str, Any]:
    """
    Retorna metadatos de calibración para un pick.
    """
    
    calibration = _get_cached_calibration()
    
    league = pick.get("league_label") or pick.get("liga") or "Unknown"
    market = pick.get("mercado", "Unknown")
    tier = str(pick.get("elite_tier") or "").lower()
    
    league_penalty = get_penalty_factor_for_league(league, calibration)
    market_adj = get_market_threshold_adjustment(market, calibration)
    league_market_penalty = get_penalty_factor_for_league_market(league, market, calibration)
    league_market_adj = get_league_market_threshold_adjustment(league, market, calibration)
    tier_boost = get_tier_boost(tier, calibration)
    model_mult = get_model_confidence_multiplier(calibration)
    
    return {
        "league_penalty_factor": round(league_penalty, 3),
        "market_threshold_adjustment": round(market_adj, 3),
        "league_market_penalty_factor": round(league_market_penalty, 3),
        "league_market_threshold_adjustment": round(league_market_adj, 3),
        "tier_boost": round(tier_boost, 3),
        "model_confidence_multiplier": round(model_mult, 3),
        "calibration_timestamp": calibration.timestamp,
        "total_picks_evaluated": calibration.total_picks_evaluated,
        "alerts_count": len(calibration.alerts),
    }


def enrich_pick_with_calibration(pick: dict[str, Any]) -> dict[str, Any]:
    """
    Enriquece un pick con scores calibrados y metadatos.
    """
    
    enriched = pick.copy()
    
    # Usar scores calibrados
    enriched["execution_score"] = calibrated_execution_score(enriched)
    enriched["ranking_score"] = calibrated_ranking_score(enriched)
    
    # Agregar metadatos
    calibration_metadata = get_calibration_metadata(enriched)
    enriched["calibration"] = calibration_metadata
    
    return enriched


def should_penalize_pick(pick: dict[str, Any]) -> bool:
    """
    Retorna True si el pick debe ser penalizado o descartado
    basándose en calibración.
    """
    
    calibration = _get_cached_calibration()
    league = pick.get("league_label") or pick.get("liga") or "Unknown"
    market = pick.get("mercado", "Unknown")
    
    # Si hay penalidades altas, considerar descarte
    league_penalty = 1.0 - get_penalty_factor_for_league(league, calibration)
    league_market_penalty = 1.0 - get_penalty_factor_for_league_market(league, market, calibration)
    
    if league_penalty > 0.4:  # Más del 40% de penalidad
        return True

    if league_market_penalty > 0.25:
        return True
    
    # Si el ajuste de threshold exige mucha más confianza, también penalizar
    market_adj = get_market_threshold_adjustment(market, calibration)
    league_market_adj = get_league_market_threshold_adjustment(league, market, calibration)
    if market_adj > 0.15 or league_market_adj > 0.15:
        return True
    
    return False


def get_tier_recommendation_adjustment(
    current_tier: str,
    calibration: CalibrationSnapshot,
) -> str:
    """
    Recomienda si bajar de tier basándose en calibración.
    
    Si un tier está underperforming, sugerir bajar.
    """
    
    segments = calibration.segments_by_type.get("tiers", {})
    tier_data = segments.get(current_tier)
    
    if not tier_data:
        return current_tier
    
    # Si el confidence_score es muy bajo y la muestra es suficiente, bajar
    if tier_data.confidence_score < 0.4 and not tier_data.min_sample_warning:
        tier_map = {
            "stakazo": "elite",
            "elite": "premium",
            "premium": "seguimiento",
            "seguimiento": "seguimiento",
        }
        recommended = tier_map.get(current_tier, current_tier)
        return recommended
    
    return current_tier


def clear_calibration_cache():
    """
    Limpia el cache de calibración (útil para testing).
    """
    global _calibration_cache, _cache_timestamp
    _calibration_cache = None
    _cache_timestamp = 0
