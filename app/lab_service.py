from typing import Any, Callable

from app.engine import ForecastRequest
from app.runtime_settings import RuntimeSettings


def _lab_pick_snapshot(pick: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": pick.get("event_id"),
        "sport_label": pick.get("sport_label"),
        "league_label": pick.get("league_label"),
        "partido": pick.get("partido") or pick.get("partido_es"),
        "equipo": pick.get("equipo") or pick.get("equipo_es"),
        "mercado": pick.get("mercado"),
        "casa": pick.get("casa"),
        "stake": pick.get("stake"),
        "importe_sugerido": pick.get("importe_sugerido"),
        "recomendacion": pick.get("recomendacion"),
        "motivo": pick.get("motivo") or pick.get("motivo_es"),
        "confidence_score": pick.get("confidence_score"),
        "quality_score": pick.get("quality_score"),
        "performance_guard_blocked": bool(pick.get("performance_guard_blocked")),
        "performance_guard_reason": pick.get("performance_guard_reason"),
        "risk_guard_blocked": bool(pick.get("risk_guard_blocked")),
    }


def build_lab_run(
    *,
    runtime_settings: RuntimeSettings,
    publication_guard: Callable[[], dict[str, Any]],
    run_forecast: Callable[[ForecastRequest], dict[str, Any]],
    build_prediction_payload: Callable[..., dict[str, Any]],
    ai_available: Callable[[], bool],
    select_picks_for_telegram: Callable[..., list[dict[str, Any]]],
    enrich_with_ai: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    build_ai_summary: Callable[..., str | None],
    format_pick_message: Callable[[dict[str, Any]], str],
    format_summary_message: Callable[..., str],
    perfil: str,
    modo: str,
    mercados: str,
    partido: str,
    deporte: str,
    bankroll: float | None,
    solo_stakazos: bool,
    perfiles_stake: set[str],
    modos_informe: set[str],
    perfil_label: Callable[[str | None], str],
    modo_label: Callable[[str | None], str],
) -> dict[str, Any]:
    forecast = run_forecast(
        ForecastRequest(
            bankroll=bankroll,
            perfil=perfil,
            modo=modo,
            mercados=mercados,
            partido=partido,
            guardar=False,
            deporte=deporte,
            solo_elite=False,
            solo_stakazos=solo_stakazos,
        )
    )
    prediction_payload = build_prediction_payload(
        data=forecast,
        solo_stakazos=solo_stakazos,
        ai_available=ai_available,
        select_picks_for_telegram=select_picks_for_telegram,
        enrich_with_ai=enrich_with_ai,
        build_ai_summary=build_ai_summary,
        format_pick_message=format_pick_message,
        format_summary_message=format_summary_message,
        perfil=perfil,
        modo=modo,
        perfiles_stake=perfiles_stake,
        modos_informe=modos_informe,
        perfil_label=perfil_label,
        modo_label=modo_label,
    )
    guard = publication_guard()
    forecast_recommended = list(forecast.get("mejores_apuestas", []))
    forecast_discarded = list(forecast.get("descartadas", []))
    blocked_recommended = [
        _lab_pick_snapshot(pick)
        for pick in forecast_recommended
        if pick.get("performance_guard_blocked") or pick.get("risk_guard_blocked")
    ]
    blocked_discarded = [
        _lab_pick_snapshot(pick)
        for pick in forecast_discarded
        if pick.get("performance_guard_blocked") or pick.get("risk_guard_blocked")
    ]
    publishable_preview = [
        _lab_pick_snapshot(pick)
        for pick in prediction_payload.get("pronosticos", [])
    ]
    publication_decision = {
        "would_publish_live": bool(guard.get("allow_live_publication", False)) and not runtime_settings.shadow_mode,
        "runtime_mode": runtime_settings.publication_mode,
        "guard_mode": guard.get("mode"),
        "guard_reasons": list(guard.get("reasons") or []),
    }
    return {
        "runtime_mode": runtime_settings.publication_mode,
        "publication_guard": guard,
        "publication_decision": publication_decision,
        "forecast_summary": {
            "sport_label": forecast.get("sport_label"),
            "league_label": forecast.get("league_label"),
            "total_analizadas": int(forecast.get("total_analizadas", 0) or 0),
            "total_recomendadas": int(forecast.get("total_recomendadas", 0) or 0),
            "total_descartadas_preview": len(forecast_discarded),
            "total_publicables_preview": len(prediction_payload.get("pronosticos", [])),
            "total_bloqueadas_en_recomendadas": len(blocked_recommended),
            "total_bloqueadas_en_descartadas": len(blocked_discarded),
        },
        "publishable_preview": publishable_preview,
        "blocked_picks": {
            "recommended": blocked_recommended,
            "discarded": blocked_discarded,
        },
        "forecast": forecast,
        "telegram_preview": prediction_payload,
    }
