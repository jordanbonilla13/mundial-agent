from typing import Any, Callable


def build_prediction_payload(
    *,
    data: dict[str, Any],
    solo_stakazos: bool,
    ai_available: Callable[[], bool],
    select_picks_for_telegram: Callable[..., list[dict[str, Any]]],
    enrich_with_ai: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    build_ai_summary: Callable[..., str | None],
    format_pick_message: Callable[[dict[str, Any]], str],
    format_summary_message: Callable[..., str],
    perfil: str,
    modo: str,
    perfiles_stake: set[str],
    modos_informe: set[str],
    perfil_label: Callable[[str | None], str],
    modo_label: Callable[[str | None], str],
) -> dict[str, Any]:
    mensajes: list[str] = []
    stakazos = [
        pick for pick in data.get("picks_elite", [])
        if str(pick.get("elite_tier") or "").lower() == "stakazo"
    ]
    picks_telegram = select_picks_for_telegram(data, solo_stakazos=solo_stakazos)
    picks_telegram = enrich_with_ai(picks_telegram)
    ai_summary = build_ai_summary(
        picks_telegram,
        sport_label=data.get("sport_label"),
        league_label=data.get("league_label"),
        solo_stakazos=solo_stakazos,
    ) if ai_available() else None

    for pick in picks_telegram:
        mensajes.append(format_pick_message(pick))

    resumen = format_summary_message(
        sport_label=data.get("sport_label"),
        league_label=data.get("league_label"),
        perfil_label=perfil_label(perfil if perfil in perfiles_stake else "moderado"),
        modo_label=modo_label(modo if modo in modos_informe else "comparador"),
        total_elite=int(data.get("total_elite", 0) or 0),
        total_stakazos=len(stakazos),
        total_messages=len(picks_telegram),
        solo_stakazos=solo_stakazos,
        ai_summary=ai_summary,
    )

    return {
        "canal": "premium",
        "deporte": data.get("sport_label"),
        "liga": data.get("league_label"),
        "criterio": data.get("criterio"),
        "aviso_cobertura": data.get("aviso_cobertura"),
        "cobertura_deportes": list(data.get("cobertura_deportes") or []),
        "errores_cobertura": list(data.get("errores_cobertura") or []),
        "ia_activa": ai_available(),
        "ia_resumen": ai_summary,
        "resumen_telegram": resumen,
        "total_elite": data.get("total_elite", 0),
        "total_stakazos": len(stakazos),
        "total_analizadas": int(data.get("total_analizadas", 0) or 0),
        "total_recomendadas": int(data.get("total_recomendadas", 0) or 0),
        "snapshots_guardados": int(data.get("snapshots_guardados", 0) or 0),
        "partidos_disponibles": list(data.get("partidos_disponibles") or []),
        "descartadas": list(data.get("descartadas") or []),
        "descartadas_operativas": list(data.get("descartadas_operativas") or []),
        "blocked_summary": dict(data.get("blocked_summary") or {}),
        "selector_candidates": int(data.get("_apuestas_selector_candidates", 0) or 0),
        "selector_rejections": list(data.get("_apuestas_selector_rejections") or []),
        "solo_stakazos": solo_stakazos,
        "pronosticos": picks_telegram,
        "mensajes_telegram": mensajes,
    }
