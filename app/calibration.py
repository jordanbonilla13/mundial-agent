"""
Módulo premium de calibración dinámica para Mundial Agent.

Analiza el histórico de picks evaluadas y proporciona:
- Métricas de rendimiento por segmento (liga, mercado, tier)
- Detección automática de sesgos y underperformance
- Recomendaciones de ajuste de filtros
- Score de confianza dinámico por segmento
- Ajustes de penalización basados en datos reales

Este módulo es crítico para hacer el bot verdaderamente PREMIUM:
convierte el histórico observado en inteligencia operativa.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from collections import defaultdict
from tracking import (
    listar_evaluaciones_picks,
    listar_picks,
    conectar,
    DB_PATH,
)


@dataclass
class SegmentMetrics:
    """Métricas de rendimiento para un segmento (liga, mercado, tier, etc.)."""
    
    segment_name: str
    segment_type: str  # "liga", "mercado", "tier", "bookmaker", etc.
    total_picks: int
    total_recommended: int
    picks_closed: int
    picks_won: int
    picks_lost: int
    picks_push: int
    total_staked: float
    total_profit: float
    roi: float  # porcentaje
    hit_rate: float  # porcentaje
    clv: Optional[float]  # porcentaje
    clv_positive_count: int
    confidence_score: float  # 0-1, basado en muestra y rendimiento
    last_pick_date: Optional[str]
    min_sample_warning: bool
    trend: str  # "strong", "neutral", "weak"
    recommendation: str  # "confiable", "revisar", "penalizar"


@dataclass
class CalibrationSnapshot:
    """Snapshot completo de calibración del modelo en un momento dado."""
    
    timestamp: str
    total_picks_evaluated: int
    segments_by_type: dict[str, list[SegmentMetrics]]
    model_adjustments: dict[str, Any]
    alerts: list[str]


def calculate_segment_metrics(
    segment_name: str,
    segment_type: str,
    picks: list[dict[str, Any]],
) -> SegmentMetrics:
    """
    Calcula métricas premium para un segmento específico.
    
    Args:
        segment_name: Nombre del segmento (ej: "La Liga", "h2h", "elite")
        segment_type: Tipo de segmento ("liga", "mercado", "tier", etc.)
        picks: Lista de picks de tracking.py
    
    Returns:
        SegmentMetrics con análisis completo del segmento
    """
    
    total_picks = len(picks)
    total_recommended = sum(
        1
        for p in picks
        if (
            p.get("recommended_by_bot") in {True, 1, "1", "true"}
            or p.get("recomendado") in {True, 1, "1", "true"}
            or p.get("elite_pick") in {True, 1, "1", "true"}
        )
    )
    
    closed_picks = [p for p in picks if p.get("estado") == "cerrada"]
    picks_closed = len(closed_picks)
    
    picks_won = sum(1 for p in closed_picks if p.get("resultado") == "win")
    picks_lost = sum(1 for p in closed_picks if p.get("resultado") == "loss")
    picks_push = sum(1 for p in closed_picks if p.get("resultado") == "push")
    
    total_staked = sum(float(p.get("importe_sugerido") or 0) for p in closed_picks)
    total_profit = sum(float(p.get("profit_loss") or 0) for p in closed_picks)
    
    roi = (total_profit / total_staked * 100) if total_staked > 0 else 0
    hit_rate = (picks_won / picks_closed * 100) if picks_closed > 0 else 0
    
    # Calular CLV
    clv_values = []
    clv_positive_count = 0
    for p in closed_picks:
        cuota = p.get("cuota")
        closing_odds = p.get("closing_odds")
        if cuota and closing_odds:
            try:
                cuota_f = float(cuota)
                cierre_f = float(closing_odds)
                if cierre_f > 0:
                    clv_individual = (cuota_f / cierre_f - 1) * 100
                    clv_values.append(clv_individual)
                    if clv_individual > 0:
                        clv_positive_count += 1
            except (ValueError, TypeError):
                pass
    
    clv = (sum(clv_values) / len(clv_values)) if clv_values else None
    
    # Fecha del último pick
    last_pick_date = None
    for p in sorted(picks, key=lambda x: x.get("created_at") or x.get("fecha_creacion") or "", reverse=True):
        if p.get("created_at") or p.get("fecha_creacion"):
            last_pick_date = p.get("created_at") or p.get("fecha_creacion")
            break
    
    # Determinar si hay advertencia de muestra pequeña
    min_sample_warning = picks_closed < 10
    
    # Calcular score de confianza (0-1)
    # Basado en: muestra, ROI, hit_rate, CLV
    sample_score = min(picks_closed / 30, 1.0)  # Mejora hasta 30 picks cerrados
    roi_score = min(abs(roi) / 10, 1.0) if roi > 0 else 0  # ROI positivo es bueno
    hitrate_score = hit_rate / 100  # 0-1 natural
    clv_score = (clv / 10) if clv and clv > 0 else 0  # CLV positivo es bueno
    
    confidence_score = (
        sample_score * 0.3 +
        roi_score * 0.3 +
        hitrate_score * 0.2 +
        clv_score * 0.2
    )
    confidence_score = min(max(confidence_score, 0), 1)
    
    # Determinar trend
    if roi >= 5 and hit_rate >= 55 and (clv is None or clv >= 0):
        trend = "strong"
    elif roi >= -2 and hit_rate >= 50:
        trend = "neutral"
    else:
        trend = "weak"
    
    # Recomendación operativa
    if min_sample_warning:
        recommendation = "revisar"  # Muestra muy pequeña
    elif confidence_score > 0.7:
        recommendation = "confiable"
    elif confidence_score > 0.4:
        recommendation = "revisar"
    else:
        recommendation = "penalizar"
    
    return SegmentMetrics(
        segment_name=segment_name,
        segment_type=segment_type,
        total_picks=total_picks,
        total_recommended=total_recommended,
        picks_closed=picks_closed,
        picks_won=picks_won,
        picks_lost=picks_lost,
        picks_push=picks_push,
        total_staked=total_staked,
        total_profit=total_profit,
        roi=round(roi, 2),
        hit_rate=round(hit_rate, 2),
        clv=round(clv, 2) if clv is not None else None,
        clv_positive_count=clv_positive_count,
        confidence_score=round(confidence_score, 3),
        last_pick_date=last_pick_date,
        min_sample_warning=min_sample_warning,
        trend=trend,
        recommendation=recommendation,
    )


def analyze_by_league(db_path: str = DB_PATH) -> dict[str, SegmentMetrics]:
    """Analiza rendimiento por liga."""
    picks = listar_picks(limit=10000, db_path=db_path)
    
    picks_by_league: dict[str, list[dict]] = defaultdict(list)
    for pick in picks:
        league = pick.get("league_label") or pick.get("liga") or "Unknown"
        picks_by_league[league].append(pick)
    
    metrics = {}
    for league, league_picks in picks_by_league.items():
        metrics[league] = calculate_segment_metrics(
            segment_name=league,
            segment_type="liga",
            picks=league_picks,
        )
    
    return metrics


def analyze_by_market(db_path: str = DB_PATH) -> dict[str, SegmentMetrics]:
    """Analiza rendimiento por mercado."""
    picks = listar_picks(limit=10000, db_path=db_path)
    
    picks_by_market: dict[str, list[dict]] = defaultdict(list)
    for pick in picks:
        market = pick.get("mercado", "Unknown")
        picks_by_market[market].append(pick)
    
    metrics = {}
    for market, market_picks in picks_by_market.items():
        metrics[market] = calculate_segment_metrics(
            segment_name=market,
            segment_type="mercado",
            picks=market_picks,
        )
    
    return metrics


def _build_league_market_key(league: str, market: str) -> str:
    return f"{league}::{market}"


def analyze_by_league_market(db_path: str = DB_PATH) -> dict[str, SegmentMetrics]:
    """Analiza rendimiento por combinacion liga + mercado."""
    picks = listar_picks(limit=10000, db_path=db_path)

    picks_by_combo: dict[str, list[dict]] = defaultdict(list)
    for pick in picks:
        league = pick.get("league_label") or pick.get("liga") or "Unknown"
        market = pick.get("mercado", "Unknown")
        combo = _build_league_market_key(league, market)
        picks_by_combo[combo].append(pick)

    metrics = {}
    for combo, combo_picks in picks_by_combo.items():
        metrics[combo] = calculate_segment_metrics(
            segment_name=combo,
            segment_type="liga_mercado",
            picks=combo_picks,
        )

    return metrics


def analyze_by_tier(db_path: str = DB_PATH) -> dict[str, SegmentMetrics]:
    """Analiza rendimiento por tier (elite, premium, standard)."""
    picks = listar_picks(limit=10000, db_path=db_path)
    
    picks_by_tier: dict[str, list[dict]] = defaultdict(list)
    for pick in picks:
        tier = pick.get("elite_tier", "standard")
        picks_by_tier[tier].append(pick)
    
    metrics = {}
    for tier, tier_picks in picks_by_tier.items():
        metrics[tier] = calculate_segment_metrics(
            segment_name=tier,
            segment_type="tier",
            picks=tier_picks,
        )
    
    return metrics


def analyze_by_bookmaker(db_path: str = DB_PATH) -> dict[str, SegmentMetrics]:
    """Analiza rendimiento por casa de apuestas."""
    picks = listar_picks(limit=10000, db_path=db_path)
    
    picks_by_bookie: dict[str, list[dict]] = defaultdict(list)
    for pick in picks:
        bookmaker = pick.get("casa") or pick.get("casa_apuestas") or "Unknown"
        picks_by_bookie[bookmaker].append(pick)
    
    metrics = {}
    for bookie, bookie_picks in picks_by_bookie.items():
        metrics[bookie] = calculate_segment_metrics(
            segment_name=bookie,
            segment_type="casa",
            picks=bookie_picks,
        )
    
    return metrics


def generate_calibration_snapshot(db_path: str = DB_PATH) -> CalibrationSnapshot:
    """
    Genera un snapshot completo de calibración del modelo.
    
    Este es el corazón de la inteligencia premium:
    - Analiza todos los segmentos
    - Detecta underperformance
    - Genera alertas y recomendaciones
    """
    
    picks = listar_picks(limit=10000, db_path=db_path)
    evaluations = listar_evaluaciones_picks(limit=10000, db_path=db_path)
    total_evaluated = len(evaluations)
    
    segments_by_type = {
        "ligas": analyze_by_league(db_path),
        "mercados": analyze_by_market(db_path),
        "ligas_mercados": analyze_by_league_market(db_path),
        "tiers": analyze_by_tier(db_path),
        "casas": analyze_by_bookmaker(db_path),
    }
    
    # Generar alertas
    alerts = []
    
    # Alerta de underperformance por liga
    for league, metrics in segments_by_type["ligas"].items():
        if not metrics.min_sample_warning and metrics.roi < -5:
            alerts.append(
                f"🔴 LIGA EN RIESGO: {league} con ROI {metrics.roi}% y hit_rate {metrics.hit_rate}%. "
                f"Considera penalizar o revisar modelo."
            )
        elif metrics.recommendation == "penalizar":
            alerts.append(
                f"⚠️ REVISAR: {league} tiene confidence_score bajo ({metrics.confidence_score}). "
                f"{metrics.picks_closed} picks cerrados."
            )
    
    # Alerta de underperformance por mercado
    for market, metrics in segments_by_type["mercados"].items():
        if not metrics.min_sample_warning and metrics.roi < -5:
            alerts.append(
                f"🔴 MERCADO EN RIESGO: {market} con ROI {metrics.roi}%. "
                f"Consider limiting exposure."
            )
    
    # Alerta de underperformance por tier
    for tier, metrics in segments_by_type["tiers"].items():
        if not metrics.min_sample_warning and metrics.roi < -5:
            alerts.append(
                f"🔴 TIER {tier} underperforming: ROI {metrics.roi}%, CLV {metrics.clv}%"
            )
    
    for combo, metrics in segments_by_type["ligas_mercados"].items():
        if not metrics.min_sample_warning and (metrics.roi < -5 or metrics.hit_rate < 45):
            alerts.append(
                f"Segmento en riesgo: {combo} con ROI {metrics.roi}% y hit_rate {metrics.hit_rate}%. "
                f"Endurecer filtros para esta combinacion."
            )

    # Generar ajustes de modelo sugeridos
    model_adjustments = _generate_model_adjustments(segments_by_type)
    
    return CalibrationSnapshot(
        timestamp=datetime.now(timezone.utc).isoformat(),
        total_picks_evaluated=total_evaluated,
        segments_by_type=segments_by_type,
        model_adjustments=model_adjustments,
        alerts=alerts,
    )


def _generate_model_adjustments(segments_by_type: dict[str, dict[str, SegmentMetrics]]) -> dict[str, Any]:
    """
    Genera sugerencias de ajuste para el modelo basadas en datos.
    
    Esto es lo que hace el bot verdaderamente adaptivo:
    - Si una liga tiene ROI negativo consistente, penaliza
    - Si un mercado tiene hit_rate bajo, aumenta el threshold
    - Si un tier está ganando, confía más en él
    """
    
    adjustments = {
        "league_penalties": {},
        "market_thresholds": {},
        "league_market_penalties": {},
        "league_market_thresholds": {},
        "tier_boosts": {},
        "confidence_multipliers": {},
    }
    
    # Analizar ligas
    for league, metrics in segments_by_type["ligas"].items():
        if not metrics.min_sample_warning:
            if metrics.roi < -5:
                # Penalizar fuerte
                penalty = min(0.5, abs(metrics.roi) / 20)  # Hasta 50% de penalidad
                adjustments["league_penalties"][league] = round(penalty, 3)
            elif metrics.roi < 0:
                # Penalizar suavemente
                penalty = abs(metrics.roi) / 50
                adjustments["league_penalties"][league] = round(penalty, 3)
    
    # Analizar mercados
    for market, metrics in segments_by_type["mercados"].items():
        if not metrics.min_sample_warning:
            if metrics.hit_rate < 45:
                # Aumentar threshold de confianza para este mercado
                threshold_boost = 0.1  # +10% en requirements
                adjustments["market_thresholds"][market] = round(threshold_boost, 3)
            elif metrics.hit_rate > 60:
                # Reducir threshold, es confiable
                threshold_reduction = -0.05
                adjustments["market_thresholds"][market] = round(threshold_reduction, 3)

    # Analizar combinaciones liga + mercado
    for combo, metrics in segments_by_type.get("ligas_mercados", {}).items():
        if metrics.min_sample_warning:
            continue

        combo_penalty = 0.0
        combo_threshold = 0.0

        if metrics.roi <= -12 or metrics.hit_rate <= 38:
            combo_penalty = min(0.6, max(abs(metrics.roi) / 18, (45 - metrics.hit_rate) / 18))
            combo_threshold = 0.18
        elif metrics.roi <= -6 or metrics.hit_rate <= 44:
            combo_penalty = min(0.35, max(abs(metrics.roi) / 30, (47 - metrics.hit_rate) / 30))
            combo_threshold = 0.1
        elif metrics.roi >= 8 and metrics.hit_rate >= 58:
            combo_threshold = -0.04

        if combo_penalty > 0:
            adjustments["league_market_penalties"][combo] = round(combo_penalty, 3)
        if combo_threshold != 0:
            adjustments["league_market_thresholds"][combo] = round(combo_threshold, 3)
    
    # Analizar tiers
    for tier, metrics in segments_by_type["tiers"].items():
        if not metrics.min_sample_warning and metrics.confidence_score > 0.6:
            boost = metrics.confidence_score * 0.15  # Hasta 15% de boost
            adjustments["tier_boosts"][tier] = round(boost, 3)
    
    # Multiplier de confianza general
    all_roi = [m.roi for m in segments_by_type["ligas"].values() if not m.min_sample_warning]
    all_hitrate = [m.hit_rate for m in segments_by_type["ligas"].values() if not m.min_sample_warning]
    
    if all_roi:
        avg_roi = sum(all_roi) / len(all_roi)
        if avg_roi > 5:
            adjustments["confidence_multipliers"]["model_general"] = 1.1  # +10% confianza
        elif avg_roi < -5:
            adjustments["confidence_multipliers"]["model_general"] = 0.85  # -15% confianza
    
    return adjustments


def get_penalty_factor_for_league(
    league: str,
    calibration: CalibrationSnapshot,
) -> float:
    """
    Retorna el factor de penalidad para una liga.
    
    1.0 = sin penalidad
    0.8 = 20% de penalidad
    0.5 = 50% de penalidad (muy severamente)
    
    Se usa en forecasting.py para ajustar execution_score
    """
    
    penalties = calibration.model_adjustments.get("league_penalties", {})
    penalty = penalties.get(league, 0)
    return 1.0 - penalty


def get_market_threshold_adjustment(
    market: str,
    calibration: CalibrationSnapshot,
) -> float:
    """
    Retorna el ajuste de threshold para un mercado.
    
    0 = sin ajuste
    +0.1 = requerir 10% más confianza
    -0.05 = aceptar 5% menos confianza
    """
    
    adjustments = calibration.model_adjustments.get("market_thresholds", {})
    return adjustments.get(market, 0.0)


def get_penalty_factor_for_league_market(
    league: str,
    market: str,
    calibration: CalibrationSnapshot,
) -> float:
    """Retorna el factor de penalidad para una combinacion liga + mercado."""

    penalties = calibration.model_adjustments.get("league_market_penalties", {})
    combo_key = _build_league_market_key(league, market)
    penalty = penalties.get(combo_key, 0)
    return 1.0 - penalty


def get_league_market_threshold_adjustment(
    league: str,
    market: str,
    calibration: CalibrationSnapshot,
) -> float:
    """Retorna el ajuste de threshold para una combinacion liga + mercado."""

    adjustments = calibration.model_adjustments.get("league_market_thresholds", {})
    combo_key = _build_league_market_key(league, market)
    return adjustments.get(combo_key, 0.0)


def get_tier_boost(
    tier: str,
    calibration: CalibrationSnapshot,
) -> float:
    """
    Retorna el boost de score para un tier.
    
    0 = sin boost
    +0.1 = +10% al ranking_score
    """
    
    boosts = calibration.model_adjustments.get("tier_boosts", {})
    return boosts.get(tier, 0.0)


def get_model_confidence_multiplier(calibration: CalibrationSnapshot) -> float:
    """
    Retorna el multiplicador general de confianza del modelo.
    
    1.0 = confianza normal
    1.1 = +10% más confianza en los picks
    0.85 = -15% menos confianza (más crítico)
    """
    
    multipliers = calibration.model_adjustments.get("confidence_multipliers", {})
    return multipliers.get("model_general", 1.0)


def format_calibration_report(calibration: CalibrationSnapshot) -> str:
    """
    Formatea el snapshot de calibración como reporte legible premium.
    """
    
    report = []
    report.append("=" * 80)
    report.append("📊 REPORTE PREMIUM DE CALIBRACIÓN DEL MODELO")
    report.append(f"⏱️  {calibration.timestamp}")
    report.append(f"📈 Total de picks evaluadas: {calibration.total_picks_evaluated}")
    report.append("=" * 80)
    
    # Ligas
    report.append("\n🏆 RENDIMIENTO POR LIGA:")
    for league, metrics in sorted(
        calibration.segments_by_type.get("ligas", {}).items(),
        key=lambda x: x[1].confidence_score,
        reverse=True
    ):
        icon = "✅" if metrics.recommendation == "confiable" else "⚠️" if metrics.recommendation == "revisar" else "❌"
        report.append(
            f"  {icon} {league}: ROI {metrics.roi}% | Hit {metrics.hit_rate}% | "
            f"CLV {metrics.clv}% | Conf {metrics.confidence_score} | Picks {metrics.picks_closed}"
        )
    
    # Mercados
    report.append("\n🎯 RENDIMIENTO POR MERCADO:")
    for market, metrics in sorted(
        calibration.segments_by_type.get("mercados", {}).items(),
        key=lambda x: x[1].confidence_score,
        reverse=True
    ):
        icon = "✅" if metrics.recommendation == "confiable" else "⚠️" if metrics.recommendation == "revisar" else "❌"
        report.append(
            f"  {icon} {market}: ROI {metrics.roi}% | Hit {metrics.hit_rate}% | "
            f"Conf {metrics.confidence_score} | Picks {metrics.picks_closed}"
        )

    league_market_segments = calibration.segments_by_type.get("ligas_mercados", {})
    if league_market_segments:
        report.append("\n🧠 RENDIMIENTO POR NICHO LIGA + MERCADO:")
        for combo, metrics in sorted(
            league_market_segments.items(),
            key=lambda x: x[1].confidence_score,
            reverse=True
        ):
            icon = "✅" if metrics.recommendation == "confiable" else "⚠️" if metrics.recommendation == "revisar" else "❌"
            report.append(
                f"  {icon} {combo}: ROI {metrics.roi}% | Hit {metrics.hit_rate}% | "
                f"Conf {metrics.confidence_score} | Picks {metrics.picks_closed}"
            )
    
    # Tiers
    report.append("\n⭐ RENDIMIENTO POR TIER:")
    for tier, metrics in calibration.segments_by_type.get("tiers", {}).items():
        icon = "✅" if metrics.recommendation == "confiable" else "⚠️" if metrics.recommendation == "revisar" else "❌"
        report.append(
            f"  {icon} {tier.upper()}: ROI {metrics.roi}% | Hit {metrics.hit_rate}% | "
            f"CLV {metrics.clv}% | Conf {metrics.confidence_score} | Picks {metrics.picks_closed}"
        )
    
    # Alertas
    if calibration.alerts:
        report.append("\n🚨 ALERTAS CRÍTICAS:")
        for alert in calibration.alerts:
            report.append(f"  {alert}")
    else:
        report.append("\n✅ Sin alertas críticas.")
    
    # Ajustes recomendados
    adjustments = calibration.model_adjustments
    if any(adjustments.values()):
        report.append("\n🔧 AJUSTES SUGERIDOS DEL MODELO:")
        
        if adjustments.get("league_penalties"):
            report.append("  Penalidades por liga:")
            for league, penalty in adjustments["league_penalties"].items():
                report.append(f"    - {league}: -{penalty*100:.1f}%")
        
        if adjustments.get("market_thresholds"):
            report.append("  Ajustes de threshold por mercado:")
            for market, adj in adjustments["market_thresholds"].items():
                direction = "↑ requerir más" if adj > 0 else "↓ aceptar menos"
                report.append(f"    - {market}: {direction} ({adj*100:+.1f}%)")

        if adjustments.get("league_market_penalties"):
            report.append("  Penalidades por nicho liga + mercado:")
            for combo, penalty in adjustments["league_market_penalties"].items():
                report.append(f"    - {combo}: -{penalty*100:.1f}%")

        if adjustments.get("league_market_thresholds"):
            report.append("  Ajustes de threshold por nicho liga + mercado:")
            for combo, adj in adjustments["league_market_thresholds"].items():
                direction = "↑ requerir más" if adj > 0 else "↓ aceptar menos"
                report.append(f"    - {combo}: {direction} ({adj*100:+.1f}%)")
        
        if adjustments.get("tier_boosts"):
            report.append("  Boosts por tier:")
            for tier, boost in adjustments["tier_boosts"].items():
                report.append(f"    - {tier}: +{boost*100:.1f}%")
        
        if adjustments.get("confidence_multipliers"):
            multiplier = adjustments["confidence_multipliers"].get("model_general", 1.0)
            report.append(f"  Multiplicador de confianza general: {multiplier:+.1%}")
    
    report.append("\n" + "=" * 80)
    
    return "\n".join(report)
