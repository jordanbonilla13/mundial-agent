"""
Sistema de auditoría diaria premium.

Genera reportes completos de:
- Picks recomendadas vs picks ejecutadas
- Resultados del día
- ROI intraday
- Alertas críticas
- Estado del modelo

Se envía diariamente por Telegram para máxima transparencia.
"""

from datetime import datetime, timedelta, timezone
from typing import Any
from collections import defaultdict

from tracking import (
    _bool_pick_flag,
    _pick_field,
    listar_picks,
    listar_publicaciones_telegram,
    dashboard_data,
    DB_PATH,
)
from app.ai_service import generate_audit_ai_brief, openai_available
from app.calibration import generate_calibration_snapshot

SUMMARY_ALERTS_EXCLUDED_LEAGUES = {
    "fifa world cup",
}

DEFAULT_AUDIT_LOOKBACK_HOURS = 24
DEFAULT_AUDIT_PUBLICATIONS_LIMIT = 12


def result_triplet(won: int, lost: int, push: int) -> str:
    return f"✅{won} ❌{lost} ➖{push}"


def _published_model_metrics(
    picks: list[dict[str, Any]],
    *,
    target_date: datetime,
    lookback_hours: int,
) -> dict[str, Any]:
    published = [p for p in picks if _bool_pick_flag(p, "telegram_publicada", False)]
    published_window = []
    window_start = target_date.astimezone(timezone.utc) - timedelta(hours=max(1, int(lookback_hours or 24)))

    for pick in published:
        try:
            created_str = (
                pick.get("created_at")
                or pick.get("fecha_creacion")
                or _pick_field(pick, "created_at", "")
            )
            if not created_str:
                continue
            created_date = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
            if created_date.tzinfo is None:
                created_date = created_date.replace(tzinfo=timezone.utc)
            else:
                created_date = created_date.astimezone(timezone.utc)
            if window_start <= created_date <= target_date.astimezone(timezone.utc):
                published_window.append(pick)
        except (ValueError, AttributeError):
            continue

    closed = [p for p in published if p.get("estado") == "cerrada"]
    closed_window = [p for p in published_window if p.get("estado") == "cerrada"]

    def _stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
        staked = sum(float(p.get("importe_sugerido") or 0) for p in rows)
        profit = sum(float(p.get("profit_loss") or 0) for p in rows)
        won = sum(1 for p in rows if p.get("resultado") == "win")
        lost = sum(1 for p in rows if p.get("resultado") == "loss")
        push = sum(1 for p in rows if p.get("resultado") == "push")
        return {
            "closed": len(rows),
            "won": won,
            "lost": lost,
            "push": push,
            "roi": round((profit / staked) * 100, 2) if staked > 0 else 0,
            "profit": round(profit, 2),
            "hit_rate": round((won / len(rows)) * 100, 2) if rows else 0,
        }

    return {
        "today": {
            "published": len(published_window),
            "pending": sum(1 for p in published_window if p.get("estado") != "cerrada"),
            **_stats(closed_window),
        },
        "all_time": {
            "published": len(published),
            "pending": sum(1 for p in published if p.get("estado") != "cerrada"),
            **_stats(closed),
        },
    }


def _latest_publications(limit: int = 5) -> list[dict[str, Any]]:
    publications = listar_publicaciones_telegram(limit=limit)
    latest: list[dict[str, Any]] = []

    for publication in publications:
        summary = publication.get("resultado_resumen") or {}
        pick_items = [item for item in publication.get("items", []) if item.get("message_kind") == "pick"]
        if not pick_items:
            continue
        picks_preview = []
        picks_preview_items = []

        for item in pick_items[:3]:
            match_label = str(item.get("partido") or "Partido").strip()
            team_label = str(item.get("equipo") or "Seleccion").strip()
            result = str(item.get("resultado") or "").strip().lower()
            state = str(item.get("estado") or "").strip().lower()
            was_bet = _bool_pick_flag(item, "apuesta_real", False)
            if result == "win":
                outcome = "ganada"
            elif result == "loss":
                outcome = "perdida"
            elif result == "push":
                outcome = "nula"
            elif state == "cerrada":
                outcome = "cerrada"
            else:
                outcome = "pendiente"
            picks_preview.append(f"{match_label} | {team_label} | {outcome}")
            picks_preview_items.append(
                {
                    "match_label": match_label,
                    "team_label": team_label,
                    "outcome": outcome,
                    "was_bet": was_bet,
                }
            )

        latest.append(
            {
                "id": publication.get("id"),
                "created_at": publication.get("created_at"),
                "sport_label": publication.get("sport_label") or "Deporte",
                "league_label": publication.get("league_label") or "Liga",
                "total_picks": len(pick_items),
                "pending": int(summary.get("pendientes") or 0),
                "won": int(summary.get("ganadas") or 0),
                "lost": int(summary.get("perdidas") or 0),
                "push": int(summary.get("nulas") or 0),
                "picks_preview": picks_preview,
                "picks_preview_items": picks_preview_items,
            }
        )

    return latest


def _publications_for_window(*, target_date: datetime, lookback_hours: int, limit: int = DEFAULT_AUDIT_PUBLICATIONS_LIMIT) -> list[dict[str, Any]]:
    window_end = target_date.astimezone(timezone.utc)
    window_start = window_end - timedelta(hours=max(1, int(lookback_hours or DEFAULT_AUDIT_LOOKBACK_HOURS)))
    return [
        publication
        for publication in _latest_publications(limit=limit)
        if window_start <= (_parse_report_datetime(publication.get("created_at")) or target_date) <= window_end
    ]


def _daily_publication_rollup(publications: list[dict[str, Any]]) -> dict[str, Any]:
    unique_picks: dict[tuple[str, str], dict[str, str]] = {}

    for publication in sorted(
        publications,
        key=lambda item: _parse_report_datetime(item.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
    ):
        for item in publication.get("picks_preview_items", []) or []:
            key = (
                str(item.get("match_label") or "").strip().lower(),
                str(item.get("team_label") or "").strip().lower(),
            )
            if not all(key):
                continue
            unique_picks[key] = {
                "match_label": str(item.get("match_label") or "").strip(),
                "team_label": str(item.get("team_label") or "").strip(),
                "outcome": str(item.get("outcome") or "pendiente").strip().lower(),
                "was_bet": bool(item.get("was_bet")),
            }

    picks = list(unique_picks.values())
    won = sum(1 for item in picks if item.get("outcome") == "ganada")
    lost = sum(1 for item in picks if item.get("outcome") == "perdida")
    push = sum(1 for item in picks if item.get("outcome") == "nula")
    pending = sum(1 for item in picks if item.get("outcome") not in {"ganada", "perdida", "nula"})

    return {
        "total_picks": len(picks),
        "won": won,
        "lost": lost,
        "push": push,
        "pending": pending,
        "picks": picks,
    }


def _parse_report_datetime(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _metric_is_recent(metrics: Any, *, target_date: datetime, max_age_days: int = 60) -> bool:
    last_pick_date = _parse_report_datetime(getattr(metrics, "last_pick_date", None))
    if last_pick_date is None:
        return False
    age_days = (target_date.astimezone(timezone.utc) - last_pick_date).total_seconds() / 86400
    return age_days <= max_age_days


def _is_excluded_summary_segment(name: str | None) -> bool:
    return str(name or "").strip().lower() in SUMMARY_ALERTS_EXCLUDED_LEAGUES


def _calibration_alerts_for_report(calibration: Any, *, target_date: datetime) -> list[str]:
    alerts: list[str] = []
    segments_by_type = getattr(calibration, "segments_by_type", {}) or {}

    for league, metrics in (segments_by_type.get("ligas", {}) or {}).items():
        if _is_excluded_summary_segment(league):
            continue
        if not _metric_is_recent(metrics, target_date=target_date):
            continue
        if not metrics.min_sample_warning and metrics.roi < -5:
            alerts.append(f"⚠️ {league}: ROI {metrics.roi:.2f}% | hit {metrics.hit_rate:.0f}%")
        elif metrics.recommendation == "penalizar":
            alerts.append(
                f"⚠️ {league}: confianza {metrics.confidence_score:.2f} | {metrics.picks_closed} cerradas"
            )

    for combo, metrics in (segments_by_type.get("ligas_mercados", {}) or {}).items():
        league_name = str(combo or "").split("::", 1)[0].strip()
        if _is_excluded_summary_segment(league_name):
            continue
        if not _metric_is_recent(metrics, target_date=target_date):
            continue
        if not metrics.min_sample_warning and (metrics.roi < -8 or metrics.hit_rate < 42):
            alerts.append(f"⚠️ {combo}: ROI {metrics.roi:.2f}% | hit {metrics.hit_rate:.0f}%")

    return alerts[:2]


def _window_label_for_hours(lookback_hours: int) -> str:
    hours = max(1, int(lookback_hours or DEFAULT_AUDIT_LOOKBACK_HOURS))
    if hours == 24:
        return "Últimas 24h"
    if hours % 24 == 0:
        days = hours // 24
        return f"Últimos {days} días"
    return f"Últimas {hours}h"


def get_picks_for_date(
    target_date: datetime,
    db_path: str = DB_PATH,
    *,
    lookback_hours: int = DEFAULT_AUDIT_LOOKBACK_HOURS,
) -> dict[str, Any]:
    """
    Obtiene picks recomendadas y ejecutadas para una fecha específica.
    
    Args:
        target_date: Fecha a analizar (ej: hoy)
    
    Returns:
        Dict con picks recomendadas, ejecutadas, cerradas de ese día
    """
    
    all_picks = listar_picks(limit=10000, db_path=db_path)
    
    # Filtrar por ventana
    picks_today = []
    window_end = target_date.astimezone(timezone.utc)
    window_start = window_end - timedelta(hours=max(1, int(lookback_hours or DEFAULT_AUDIT_LOOKBACK_HOURS)))
    for pick in all_picks:
        try:
            created_str = (
                pick.get("created_at")
                or pick.get("fecha_creacion")
                or _pick_field(pick, "created_at", "")
            )
            if not created_str:
                continue
            
            created_date = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
            if created_date.tzinfo is None:
                created_date = created_date.replace(tzinfo=timezone.utc)
            else:
                created_date = created_date.astimezone(timezone.utc)
            if window_start <= created_date <= window_end:
                picks_today.append(pick)
        except (ValueError, AttributeError):
            continue
    
    # Clasificar
    recommended = [p for p in picks_today if _bool_pick_flag(p, "recommended_by_bot", True)]
    executed = [p for p in picks_today if _bool_pick_flag(p, "apuesta_real", False)]
    closed = [p for p in picks_today if p.get("estado") == "cerrada" and _bool_pick_flag(p, "apuesta_real", False)]
    model_published = _published_model_metrics(all_picks, target_date=target_date, lookback_hours=lookback_hours)
    
    # Métricas
    total_staked = sum(float(p.get("importe_sugerido") or 0) for p in closed)
    total_profit = sum(float(p.get("profit_loss") or 0) for p in closed)
    won = sum(1 for p in closed if p.get("resultado") == "win")
    lost = sum(1 for p in closed if p.get("resultado") == "loss")
    
    roi_pct = (total_profit / total_staked * 100) if total_staked > 0 else 0
    hitrate = (won / len(closed) * 100) if closed else 0
    
    return {
        "date": target_date.date().isoformat(),
        "window_label": _window_label_for_hours(lookback_hours),
        "recommended": len(recommended),
        "executed": len(executed),
        "closed": len(closed),
        "won": won,
        "lost": lost,
        "total_staked": round(total_staked, 2),
        "total_profit": round(total_profit, 2),
        "roi_pct": round(roi_pct, 2),
        "hitrate": round(hitrate, 2),
        "model_published": model_published,
        "picks_list": {
            "recommended": recommended,
            "executed": executed,
            "closed": closed,
        }
    }


def generate_daily_audit_report(
    target_date: datetime = None,
    db_path: str = DB_PATH,
    *,
    lookback_hours: int = DEFAULT_AUDIT_LOOKBACK_HOURS,
) -> dict[str, Any]:
    """
    Genera reporte completo de auditoría para un día.
    
    Incluye:
    - Picks recomendadas vs ejecutadas
    - Resultados
    - Comparación con histórico
    - Estado del modelo
    - Alertas críticas
    """
    
    if target_date is None:
        target_date = datetime.now(timezone.utc)
    
    # Datos del día
    try:
        day_data = get_picks_for_date(target_date, db_path, lookback_hours=lookback_hours)
    except TypeError:
        # Compatibilidad con tests antiguos que monkeypatchean la funcion sin lookback_hours.
        day_data = get_picks_for_date(target_date, db_path)
    
    # Datos generales (histórico)
    general_data = dashboard_data(db_path=db_path)
    resumen = general_data.get("resumen", {})
    
    # Calibración
    calibration = generate_calibration_snapshot()
    
    # Comparación con histórico
    historical_roi = float(resumen.get("roi") or 0)
    historical_hitrate = float(resumen.get("hit_rate") or 0)
    
    roi_vs_hist = day_data["roi_pct"] - historical_roi
    hitrate_vs_hist = day_data["hitrate"] - historical_hitrate
    
    # Alertas
    alerts = []
    
    # Alerta: pocas recomendaciones
    if day_data["recommended"] < 3:
        alerts.append("⚠️ Pocas picks recomendadas hoy. Revisar mercado.")
    
    # Alerta: muchas no ejecutadas
    if day_data["recommended"] > 0:
        execution_rate = (day_data["executed"] / day_data["recommended"]) * 100
        if execution_rate < 70:
            alerts.append(f"⚠️ Solo {execution_rate:.0f}% de picks se ejecutaron.")
    
    # Alerta: ROI negativo hoy
    if day_data["roi_pct"] < -5:
        alerts.append(f"🔴 ROI NEGATIVO: {day_data['roi_pct']}% hoy.")
    
    # Alerta: hitrate muy bajo
    if day_data["closed"] >= 3 and day_data["hitrate"] < 40:
        alerts.append(f"🔴 Hit rate bajo: {day_data['hitrate']}%.")
    
    # Incluir alertas de calibración
    calibration_alerts = _calibration_alerts_for_report(calibration, target_date=target_date)
    alerts.extend(calibration_alerts)
    
    # Status general
    if day_data["roi_pct"] > 0 and day_data["hitrate"] > 50:
        status = "✅ VERDE"
        status_detail = "Día positivo. Modelo funcionando bien."
    elif day_data["roi_pct"] >= -5 and day_data["hitrate"] >= 45:
        status = "🟡 AMARILLO"
        status_detail = "Día neutro. Revisar siguiente picks."
    else:
        status = "🔴 ROJO"
        status_detail = "Día negativo. Verificar modelo."
    
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "date": day_data["date"],
        "window_label": day_data.get("window_label") or _window_label_for_hours(lookback_hours),
        "status": status,
        "status_detail": status_detail,
        "picks": {
            "recommended": day_data["recommended"],
            "executed": day_data["executed"],
            "closed": day_data["closed"],
            "won": day_data["won"],
            "lost": day_data["lost"],
        },
        "metrics": {
            "staked": day_data["total_staked"],
            "profit": day_data["total_profit"],
            "roi": day_data["roi_pct"],
            "hitrate": day_data["hitrate"],
        },
        "vs_historical": {
            "roi_delta": round(roi_vs_hist, 2),
            "hitrate_delta": round(hitrate_vs_hist, 2),
            "historical_roi": round(historical_roi, 2),
            "historical_hitrate": round(historical_hitrate, 2),
        },
        "alerts": alerts,
        "calibration": {
            "total_picks_evaluated": calibration.total_picks_evaluated,
            "model_confidence": round(calibration.model_adjustments.get("confidence_multipliers", {}).get("model_general", 1.0), 3),
        },
        "model_portfolio": day_data["model_published"],
        "latest_publications": _latest_publications(),
        "daily_publications": _publications_for_window(target_date=target_date, lookback_hours=lookback_hours),
        "ai_insights": None,
        "picks_detail": day_data["picks_list"],
    }

    if openai_available():
        report["ai_insights"] = generate_audit_ai_brief(report)

    return report


def format_audit_report_telegram(report: dict[str, Any]) -> str:
    """
    Formatea el reporte de auditoría para Telegram (legible y Premium).
    """
    
    lines = []
    
    # Header
    lines.append("=" * 50)
    lines.append(f"📊 AUDITORÍA DIARIA - {report['date']}")
    lines.append(f"Status: {report['status']}")
    lines.append("=" * 50)
    
    portfolio = report.get("model_portfolio") or {}
    today_portfolio = portfolio.get("today") or {}
    all_time_portfolio = portfolio.get("all_time") or {}

    # Status detail
    lines.append(f"\n{report['status_detail']}")

    lines.append("\n🤖 PORTFOLIO PUBLICADO EN TELEGRAM:")
    lines.append(
        "  Hoy: "
        f"{today_portfolio.get('published', 0)} publicadas | "
        f"{today_portfolio.get('closed', 0)} cerradas | "
        f"{today_portfolio.get('pending', 0)} pendientes | "
        f"{today_portfolio.get('won', 0)}W-{today_portfolio.get('lost', 0)}L-{today_portfolio.get('push', 0)}N"
    )
    lines.append(
        "  Hoy ROI/Hit: "
        f"{today_portfolio.get('roi', 0):+.2f}% | "
        f"{today_portfolio.get('hit_rate', 0):.2f}%"
    )
    lines.append(
        "  Global: "
        f"{all_time_portfolio.get('published', 0)} publicadas | "
        f"{all_time_portfolio.get('closed', 0)} cerradas | "
        f"{all_time_portfolio.get('pending', 0)} pendientes | "
        f"{all_time_portfolio.get('won', 0)}W-{all_time_portfolio.get('lost', 0)}L-{all_time_portfolio.get('push', 0)}N"
    )
    lines.append(
        "  Global ROI/Hit: "
        f"{all_time_portfolio.get('roi', 0):+.2f}% | "
        f"{all_time_portfolio.get('hit_rate', 0):.2f}%"
    )

    # Picks del día
    lines.append("\n📈 APUESTAS REALES DEL DIA:")
    lines.append(f"  Recomendadas por el modelo: {report['picks']['recommended']}")
    lines.append(f"  Marcadas como ejecutadas: {report['picks']['executed']}")
    lines.append(f"  Cerradas hoy: {report['picks']['closed']}")

    if report['picks']['closed'] > 0:
        lines.append(f"  ✅ Ganadas: {report['picks']['won']}")
        lines.append(f"  ❌ Perdidas: {report['picks']['lost']}")
        lines.append(f"  💰 Apostado: €{report['metrics']['staked']:.2f}")
        lines.append(f"  Beneficio: €{report['metrics']['profit']:+.2f}")
        lines.append(f"  ROI: {report['metrics']['roi']:+.2f}%")
        lines.append(f"  Hit Rate: {report['metrics']['hitrate']:.2f}%")
    else:
        lines.append("  Sin apuestas reales cerradas hoy, por eso ROI/beneficio del dia salen a 0.")
    
    # Comparación histórica
    lines.append("\n📊 vs HISTÓRICO:")
    lines.append(f"  ROI: {report['vs_historical']['roi_delta']:+.2f}% (Hist: {report['vs_historical']['historical_roi']:.2f}%)")
    lines.append(f"  Hit Rate: {report['vs_historical']['hitrate_delta']:+.2f}pp (Hist: {report['vs_historical']['historical_hitrate']:.2f}%)")
    
    # Calibración
    lines.append("\n⚙️ MODELO:")
    lines.append(f"  Picks evaluadas: {report['calibration']['total_picks_evaluated']}")
    lines.append(f"  Confianza: {report['calibration']['model_confidence']:.1%}")

    latest_publications = report.get("latest_publications") or []
    if latest_publications:
        lines.append("\n🧾 ULTIMAS PUBLICACIONES:")
        for publication in latest_publications:
            lines.append(
                "  "
                f"#{publication.get('id')} {publication.get('sport_label')} / {publication.get('league_label')}: "
                f"{publication.get('total_picks', 0)} picks | "
                f"{publication.get('pending', 0)} pendientes | "
                f"{publication.get('won', 0)}W-{publication.get('lost', 0)}L-{publication.get('push', 0)}N"
            )
            for preview in publication.get("picks_preview", []):
                lines.append(f"    - {preview}")
    
    # Alertas
    if report['alerts']:
        lines.append("\n🚨 ALERTAS:")
        for alert in report['alerts'][:5]:  # Top 5
            lines.append(f"  {alert}")
    else:
        lines.append("\n✅ Sin alertas críticas.")

    if report.get("ai_insights"):
        lines.append("\n🧬 LECTURA IA:")
        lines.append(f"  {report['ai_insights']}")
    
    lines.append("\n" + "=" * 50)
    
    return "\n".join(lines)


def format_audit_report_telegram(report: dict[str, Any]) -> str:
    """Formatea el reporte de auditoria para Telegram en version compacta."""

    def result_triplet(won: int, lost: int, push: int) -> str:
        return f"✅{won} ❌{lost} ➖{push}"

    lines: list[str] = []
    portfolio = report.get("model_portfolio") or {}
    today_portfolio = portfolio.get("today") or {}
    all_time_portfolio = portfolio.get("all_time") or {}
    latest_publications = report.get("latest_publications") or []
    daily_publications = report.get("daily_publications") or []
    daily_rollup = _daily_publication_rollup(daily_publications)
    top_publication = latest_publications[0] if latest_publications else None
    window_label = str(report.get("window_label") or "Últimas 24h")

    lines.append(f"📊 AUDITORÍA {window_label} | {report['status']}")
    lines.append(report["status_detail"])
    lines.append("")
    lines.append("🤖 Portfolio modelo")
    lines.append(
        f"{window_label}: {today_portfolio.get('published', 0)} pub | "
        f"{today_portfolio.get('closed', 0)} cerr | "
        f"{today_portfolio.get('pending', 0)} pend | "
        f"{today_portfolio.get('won', 0)}W-{today_portfolio.get('lost', 0)}L-{today_portfolio.get('push', 0)}N"
    )
    lines.append(
        f"ROI/Hit {window_label.lower()}: {today_portfolio.get('roi', 0):+.2f}% | "
        f"{today_portfolio.get('hit_rate', 0):.0f}%"
    )
    lines.append(
        f"Global: {all_time_portfolio.get('published', 0)} pub | "
        f"{all_time_portfolio.get('closed', 0)} cerr | "
        f"{all_time_portfolio.get('pending', 0)} pend | "
        f"{all_time_portfolio.get('won', 0)}W-{all_time_portfolio.get('lost', 0)}L-{all_time_portfolio.get('push', 0)}N"
    )

    lines.append("")
    lines.append(f"📈 Real {window_label.lower()}")
    lines.append(
        f"Rec {report['picks']['recommended']} | "
        f"Ejec {report['picks']['executed']} | "
        f"Cerr {report['picks']['closed']} | "
        f"{report['picks']['won']}W-{report['picks']['lost']}L"
    )
    if report["picks"]["closed"] > 0:
        lines.append(
            f"€{report['metrics']['profit']:+.2f} | ROI {report['metrics']['roi']:+.2f}% | "
            f"Hit {report['metrics']['hitrate']:.0f}%"
        )
    else:
        lines.append(f"Sin cerradas reales en {window_label.lower()}.")

    lines.append("")
    lines.append(
        f"📊 vs hist | ROI {report['vs_historical']['roi_delta']:+.2f}pp | "
        f"Hit {report['vs_historical']['hitrate_delta']:+.2f}pp"
    )
    lines.append(
        f"⚙️ Modelo | {report['calibration']['total_picks_evaluated']} eval | "
        f"conf {report['calibration']['model_confidence']:.0%}"
    )

    if daily_rollup.get("total_picks", 0) > 0:
        lines.append("")
        lines.append(
            f"🗓️ Publicado {window_label.lower()} | "
            f"{daily_rollup.get('total_picks', 0)} picks | "
            f"{daily_rollup.get('won', 0)}W-{daily_rollup.get('lost', 0)}L-{daily_rollup.get('push', 0)}N | "
            f"{daily_rollup.get('pending', 0)} pend"
        )
        for item in daily_rollup.get("picks", [])[:6]:
            outcome = str(item.get("outcome") or "").strip().lower()
            was_bet = bool(item.get("was_bet"))
            icon = "✅💵" if outcome == "ganada" and was_bet else "✅" if outcome == "ganada" else "❌" if outcome == "perdida" else "➖" if outcome == "nula" else "⏳"
            lines.append(f"{icon} {item.get('match_label')} | {item.get('team_label')}")

    if top_publication:
        lines.append("")
        lines.append(
            f"🧾 Última pub #{top_publication.get('id')} | "
            f"{top_publication.get('total_picks', 0)} picks | "
            f"{top_publication.get('won', 0)}W-{top_publication.get('lost', 0)}L-{top_publication.get('push', 0)}N | "
            f"{top_publication.get('pending', 0)} pend"
        )
        for preview in top_publication.get("picks_preview", [])[:3]:
            outcome = preview.rsplit("|", 1)[-1].strip().lower()
            icon = "✅" if outcome == "ganada" else "❌" if outcome == "perdida" else "➖" if outcome == "nula" else "⏳"
            compact_preview = (
                preview.replace(" | ganada", "")
                .replace(" | perdida", "")
                .replace(" | nula", "")
                .replace(" | pendiente", "")
            )
            lines.append(f"{icon} {compact_preview}")

    if report["alerts"]:
        lines.append("")
        lines.append("🚨 Alertas")
        for alert in report["alerts"][:2]:
            lines.append(alert)

    if report.get("ai_insights"):
        lines.append("")
        lines.append(f"🧠 {report['ai_insights']}")

    return "\n".join(lines)


def format_audit_report_telegram(report: dict[str, Any]) -> str:
    """Formatea el reporte de auditoria para Telegram en version compacta."""

    lines: list[str] = []
    portfolio = report.get("model_portfolio") or {}
    window_portfolio = portfolio.get("today") or {}
    all_time_portfolio = portfolio.get("all_time") or {}
    latest_publications = report.get("latest_publications") or []
    window_publications = report.get("daily_publications") or []
    window_rollup = _daily_publication_rollup(window_publications)
    top_publication = latest_publications[0] if latest_publications else None
    window_label = str(report.get("window_label") or "Últimas 24h")
    is_daily_24h = window_label == "Últimas 24h"
    portfolio_label = "Hoy" if is_daily_24h else window_label
    published_label = "Publicado hoy" if is_daily_24h else f"Publicado {window_label.lower()}"
    real_label = "Día real" if is_daily_24h else f"Real {window_label.lower()}"
    roi_hit_label = "ROI/Hit hoy" if is_daily_24h else f"ROI/Hit {window_label.lower()}"

    lines.append(f"📊 AUDITORÍA {window_label} | {report['status']}")
    lines.append(report["status_detail"])
    lines.append("")
    lines.append("🤖 Portfolio modelo")
    lines.append(
        f"{portfolio_label}: {window_portfolio.get('published', 0)} pub | "
        f"{window_portfolio.get('closed', 0)} cerr | "
        f"{window_portfolio.get('pending', 0)} pend | "
        f"{result_triplet(window_portfolio.get('won', 0), window_portfolio.get('lost', 0), window_portfolio.get('push', 0))}"
    )
    lines.append(
        f"{roi_hit_label}: {window_portfolio.get('roi', 0):+.2f}% | "
        f"{window_portfolio.get('hit_rate', 0):.0f}%"
    )
    lines.append(
        f"Global: {all_time_portfolio.get('published', 0)} pub | "
        f"{all_time_portfolio.get('closed', 0)} cerr | "
        f"{all_time_portfolio.get('pending', 0)} pend | "
        f"{result_triplet(all_time_portfolio.get('won', 0), all_time_portfolio.get('lost', 0), all_time_portfolio.get('push', 0))}"
    )

    lines.append("")
    lines.append(f"📈 {real_label}")
    lines.append(
        f"Rec {report['picks']['recommended']} | "
        f"Ejec {report['picks']['executed']} | "
        f"Cerr {report['picks']['closed']} | "
        f"✅{report['picks']['won']} ❌{report['picks']['lost']}"
    )
    if report["picks"]["closed"] > 0:
        lines.append(
            f"€{report['metrics']['profit']:+.2f} | ROI {report['metrics']['roi']:+.2f}% | "
            f"Hit {report['metrics']['hitrate']:.0f}%"
        )
    else:
        lines.append(f"Sin cerradas reales en {window_label.lower()}.")

    lines.append("")
    lines.append(
        f"📊 vs hist | ROI {report['vs_historical']['roi_delta']:+.2f}pp | "
        f"Hit {report['vs_historical']['hitrate_delta']:+.2f}pp"
    )
    lines.append(
        f"⚙️ Modelo | {report['calibration']['total_picks_evaluated']} eval | "
        f"conf {report['calibration']['model_confidence']:.0%}"
    )

    if window_rollup.get("total_picks", 0) > 0:
        lines.append("")
        lines.append(
            f"🗓️ {published_label} | "
            f"{window_rollup.get('total_picks', 0)} picks | "
            f"{result_triplet(window_rollup.get('won', 0), window_rollup.get('lost', 0), window_rollup.get('push', 0))} | "
            f"{window_rollup.get('pending', 0)} pend"
        )
        for item in window_rollup.get("picks", [])[:6]:
            outcome = str(item.get("outcome") or "").strip().lower()
            was_bet = bool(item.get("was_bet"))
            icon = "✅💵" if outcome == "ganada" and was_bet else "✅" if outcome == "ganada" else "❌" if outcome == "perdida" else "➖" if outcome == "nula" else "⏳"
            lines.append(f"{icon} {item.get('match_label')} | {item.get('team_label')}")

    if top_publication:
        lines.append("")
        lines.append(
            f"🧾 Última pub #{top_publication.get('id')} | "
            f"{top_publication.get('total_picks', 0)} picks | "
            f"{result_triplet(top_publication.get('won', 0), top_publication.get('lost', 0), top_publication.get('push', 0))} | "
            f"{top_publication.get('pending', 0)} pend"
        )
        for preview in top_publication.get("picks_preview", [])[:3]:
            outcome = preview.rsplit("|", 1)[-1].strip().lower()
            icon = "✅" if outcome == "ganada" else "❌" if outcome == "perdida" else "➖" if outcome == "nula" else "⏳"
            compact_preview = (
                preview.replace(" | ganada", "")
                .replace(" | perdida", "")
                .replace(" | nula", "")
                .replace(" | pendiente", "")
            )
            lines.append(f"{icon} {compact_preview}")

    if report["alerts"]:
        lines.append("")
        lines.append("🚨 Alertas")
        for alert in report["alerts"][:2]:
            lines.append(alert)

    if report.get("ai_insights"):
        lines.append("")
        lines.append(f"🧠 {report['ai_insights']}")

    return "\n".join(lines)


def format_audit_report_html(report: dict[str, Any]) -> str:
    """
    Formatea el reporte como HTML premium para dashboard.
    """
    
    status_color = {
        "✅ VERDE": "#51cf66",
        "🟡 AMARILLO": "#ffd43b",
        "🔴 ROJO": "#ff6b6b",
    }.get(report["status"], "#666")
    
    status_emoji = {
        "✅ VERDE": "✅",
        "🟡 AMARILLO": "⚠️",
        "🔴 ROJO": "🔴",
    }.get(report["status"], "❓")
    
    # Construir sección de alertas
    if report['alerts']:
        alerts_html = '<div style="background: #1a1f3a; padding: 10px; border-radius: 4px; margin: 15px 0; border-left: 3px solid #ff6b6b;">'
        alerts_html += '<p style="margin: 0; color: #ff6b6b; font-size: 12px;"><strong>🚨 ALERTAS:</strong></p>'
        for alert in report['alerts'][:5]:
            alerts_html += f'<p style="margin: 5px 0; color: #e0e0e0; font-size: 11px;">{alert}</p>'
        alerts_html += '</div>'
    else:
        alerts_html = '<p style="color: #51cf66; font-size: 12px;">✅ Sin alertas críticas.</p>'
    
    html = f"""
    <div style="background: #1a1f3a; border-left: 5px solid {status_color}; padding: 20px; border-radius: 8px; margin: 20px 0;">
        <h2 style="color: {status_color}; margin-top: 0;">
            {status_emoji} Auditoría - {report['date']}
        </h2>
        
        <p style="color: #e0e0e0; font-size: 14px; line-height: 1.6;">
            {report['status_detail']}
        </p>
        
        <table style="width: 100%; color: #e0e0e0; border-collapse: collapse; margin: 15px 0;">
            <tr style="background: #0a0e27;">
                <td style="padding: 8px; border-bottom: 1px solid #333;"><strong>Picks Recomendadas</strong></td>
                <td style="padding: 8px; border-bottom: 1px solid #333; text-align: right;">{report['picks']['recommended']}</td>
            </tr>
            <tr>
                <td style="padding: 8px; border-bottom: 1px solid #333;"><strong>Picks Ejecutadas</strong></td>
                <td style="padding: 8px; border-bottom: 1px solid #333; text-align: right;">{report['picks']['executed']}</td>
            </tr>
            <tr style="background: #0a0e27;">
                <td style="padding: 8px; border-bottom: 1px solid #333;"><strong>Picks Cerradas</strong></td>
                <td style="padding: 8px; border-bottom: 1px solid #333; text-align: right;">{report['picks']['closed']}</td>
            </tr>
            <tr>
                <td style="padding: 8px; border-bottom: 1px solid #333;"><strong>Ganadas / Perdidas</strong></td>
                <td style="padding: 8px; border-bottom: 1px solid #333; text-align: right;">
                    <span style="color: #51cf66;">✅ {report['picks']['won']}</span> / 
                    <span style="color: #ff6b6b;">❌ {report['picks']['lost']}</span>
                </td>
            </tr>
            <tr style="background: #0a0e27;">
                <td style="padding: 8px; border-bottom: 1px solid #333;"><strong>Apostado</strong></td>
                <td style="padding: 8px; border-bottom: 1px solid #333; text-align: right;">€{report['metrics']['staked']:.2f}</td>
            </tr>
            <tr>
                <td style="padding: 8px; border-bottom: 1px solid #333;"><strong>Beneficio</strong></td>
                <td style="padding: 8px; border-bottom: 1px solid #333; text-align: right; color: {'#51cf66' if report['metrics']['profit'] >= 0 else '#ff6b6b'};">
                    €{report['metrics']['profit']:+.2f}
                </td>
            </tr>
            <tr style="background: #0a0e27;">
                <td style="padding: 8px; border-bottom: 1px solid #333;"><strong>ROI</strong></td>
                <td style="padding: 8px; border-bottom: 1px solid #333; text-align: right; color: {'#51cf66' if report['metrics']['roi'] >= 0 else '#ff6b6b'};">
                    {report['metrics']['roi']:+.2f}%
                </td>
            </tr>
            <tr>
                <td style="padding: 8px; border-bottom: 1px solid #333;"><strong>Hit Rate</strong></td>
                <td style="padding: 8px; border-bottom: 1px solid #333; text-align: right;">{report['metrics']['hitrate']:.2f}%</td>
            </tr>
        </table>
        
        <div style="background: #0a0e27; padding: 10px; border-radius: 4px; margin: 15px 0; color: #e0e0e0;">
            <p style="margin: 0; font-size: 12px;">
                <strong>vs Histórico:</strong><br>
                ROI: {report['vs_historical']['roi_delta']:+.2f}% (Histórico: {report['vs_historical']['historical_roi']:.2f}%)<br>
                Hit Rate: {report['vs_historical']['hitrate_delta']:+.2f}pp (Histórico: {report['vs_historical']['historical_hitrate']:.2f}%)
            </p>
        </div>
        
        <div style="background: #0a0e27; padding: 10px; border-radius: 4px; margin: 15px 0; color: #e0e0e0;">
            <p style="margin: 0; font-size: 12px;">
                <strong>Modelo:</strong><br>
                Picks Evaluadas: {report['calibration']['total_picks_evaluated']}<br>
                Confianza: {report['calibration']['model_confidence']:.1%}
            </p>
        </div>
        
        {alerts_html}
    </div>
    """
    
    return html
