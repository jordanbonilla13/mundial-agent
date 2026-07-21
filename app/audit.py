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


def _published_model_metrics(
    picks: list[dict[str, Any]],
    *,
    target_date: datetime,
) -> dict[str, Any]:
    published = [p for p in picks if _bool_pick_flag(p, "telegram_publicada", False)]
    published_today = []

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
            if created_date.date() == target_date.date():
                published_today.append(pick)
        except (ValueError, AttributeError):
            continue

    closed = [p for p in published if p.get("estado") == "cerrada"]
    closed_today = [p for p in published_today if p.get("estado") == "cerrada"]

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
            "published": len(published_today),
            "pending": sum(1 for p in published_today if p.get("estado") != "cerrada"),
            **_stats(closed_today),
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
        picks_preview = []

        for item in pick_items[:3]:
            match_label = str(item.get("partido") or "Partido").strip()
            team_label = str(item.get("equipo") or "Seleccion").strip()
            result = str(item.get("resultado") or "").strip().lower()
            state = str(item.get("estado") or "").strip().lower()
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
            }
        )

    return latest


def get_picks_for_date(target_date: datetime, db_path: str = DB_PATH) -> dict[str, Any]:
    """
    Obtiene picks recomendadas y ejecutadas para una fecha específica.
    
    Args:
        target_date: Fecha a analizar (ej: hoy)
    
    Returns:
        Dict con picks recomendadas, ejecutadas, cerradas de ese día
    """
    
    all_picks = listar_picks(limit=10000, db_path=db_path)
    
    # Filtrar por fecha
    picks_today = []
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
            if created_date.date() == target_date.date():
                picks_today.append(pick)
        except (ValueError, AttributeError):
            continue
    
    # Clasificar
    recommended = [p for p in picks_today if _bool_pick_flag(p, "recommended_by_bot", True)]
    executed = [p for p in picks_today if _bool_pick_flag(p, "apuesta_real", False)]
    closed = [p for p in picks_today if p.get("estado") == "cerrada" and _bool_pick_flag(p, "apuesta_real", False)]
    model_published = _published_model_metrics(all_picks, target_date=target_date)
    
    # Métricas
    total_staked = sum(float(p.get("importe_sugerido") or 0) for p in closed)
    total_profit = sum(float(p.get("profit_loss") or 0) for p in closed)
    won = sum(1 for p in closed if p.get("resultado") == "win")
    lost = sum(1 for p in closed if p.get("resultado") == "loss")
    
    roi_pct = (total_profit / total_staked * 100) if total_staked > 0 else 0
    hitrate = (won / len(closed) * 100) if closed else 0
    
    return {
        "date": target_date.date().isoformat(),
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


def generate_daily_audit_report(target_date: datetime = None, db_path: str = DB_PATH) -> dict[str, Any]:
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
    calibration_alerts = calibration.alerts[:3]  # Top 3
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
    
    # Status detail
    lines.append(f"\n{report['status_detail']}")
    
    # Picks del día
    lines.append("\n📈 PICKS:")
    lines.append(f"  Recomendadas: {report['picks']['recommended']}")
    lines.append(f"  Ejecutadas: {report['picks']['executed']}")
    lines.append(f"  Cerradas: {report['picks']['closed']}")
    
    if report['picks']['closed'] > 0:
        lines.append(f"  ✅ Ganadas: {report['picks']['won']}")
        lines.append(f"  ❌ Perdidas: {report['picks']['lost']}")
    
    # Métricas
    lines.append("\n💰 RESULTADOS:")
    lines.append(f"  Apostado: €{report['metrics']['staked']:.2f}")
    lines.append(f"  Beneficio: €{report['metrics']['profit']:+.2f}")
    lines.append(f"  ROI: {report['metrics']['roi']:+.2f}%")
    lines.append(f"  Hit Rate: {report['metrics']['hitrate']:.2f}%")
    
    # Comparación histórica
    lines.append("\n📊 vs HISTÓRICO:")
    lines.append(f"  ROI: {report['vs_historical']['roi_delta']:+.2f}% (Hist: {report['vs_historical']['historical_roi']:.2f}%)")
    lines.append(f"  Hit Rate: {report['vs_historical']['hitrate_delta']:+.2f}pp (Hist: {report['vs_historical']['historical_hitrate']:.2f}%)")
    
    # Calibración
    lines.append("\n⚙️ MODELO:")
    lines.append(f"  Picks evaluadas: {report['calibration']['total_picks_evaluated']}")
    lines.append(f"  Confianza: {report['calibration']['model_confidence']:.1%}")

    portfolio = report.get("model_portfolio") or {}
    today_portfolio = portfolio.get("today") or {}
    all_time_portfolio = portfolio.get("all_time") or {}
    lines.append("\n🤖 PORTFOLIO PUBLICADO:")
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
