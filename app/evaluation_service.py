from typing import Any, Callable


def pending_bot_score_contexts(
    *,
    list_picks: Callable[..., list[dict[str, Any]]],
    read_raw_pick: Callable[[dict[str, Any]], dict[str, Any]],
    bool_pick_flag: Callable[[dict[str, Any], str, bool], bool],
    resolve_context: Callable[[str | None], dict[str, Any]],
    default_sport: str,
) -> list[dict[str, Any]]:
    picks = list_picks(limit=500, estado="pendiente")
    contexts: list[dict[str, Any]] = []
    seen: set[str] = set()

    for pick in picks:
        if not bool_pick_flag(pick, "recommended_by_bot", True):
            continue
        if not bool_pick_flag(pick, "auto_eval_eligible", True):
            continue

        raw = read_raw_pick(pick)
        sport_key = str(raw.get("sport_key") or pick.get("sport_key") or "").strip().lower()
        if not sport_key or sport_key in seen:
            continue

        seen.add(sport_key)
        contexts.append(resolve_context(sport_key))

    if contexts:
        return contexts

    return [resolve_context(default_sport)]


def scores_for_pending_bot_picks(
    *,
    days_from: int,
    list_picks: Callable[..., list[dict[str, Any]]],
    read_raw_pick: Callable[[dict[str, Any]], dict[str, Any]],
    bool_pick_flag: Callable[[dict[str, Any], str, bool], bool],
    resolve_context: Callable[[str | None], dict[str, Any]],
    fetch_scores: Callable[..., list[dict[str, Any]]],
    default_sport: str,
) -> list[dict[str, Any]]:
    events_by_id: dict[str, dict[str, Any]] = {}

    for contexto in pending_bot_score_contexts(
        list_picks=list_picks,
        read_raw_pick=read_raw_pick,
        bool_pick_flag=bool_pick_flag,
        resolve_context=resolve_context,
        default_sport=default_sport,
    ):
        deporte = str(contexto.get("catalog_key") or contexto.get("sport_key") or default_sport)
        try:
            eventos = fetch_scores(days_from=days_from, deporte=deporte)
        except Exception:
            continue

        for evento in eventos:
            event_id = evento.get("id")
            if event_id:
                events_by_id[str(event_id)] = evento

    return list(events_by_id.values())


def build_telegram_audit_summary(
    *,
    force_refresh_scores: bool,
    generate_report: Callable[[], dict[str, Any]],
    format_report: Callable[[dict[str, Any]], str],
    refresh_scores: Callable[[int], list[dict[str, Any]]],
    liquidate_picks: Callable[[list[dict[str, Any]]], dict[str, Any]],
    score_days: int = 3,
) -> tuple[str, dict[str, Any]]:
    liquidation_result: dict[str, Any] | None = None

    if force_refresh_scores:
        try:
            recent_scores = refresh_scores(max(1, int(score_days or 3)))
            liquidation_result = liquidate_picks(recent_scores)
        except Exception as exc:
            liquidation_result = {
                "status": "error",
                "error": str(exc),
                "liquidados": 0,
                "pendientes": 0,
            }

    report = generate_report()
    report_text = format_report(report)

    if liquidation_result is not None:
        if liquidation_result.get("status") == "error":
            suffix = "\n\n♻️ Autoevaluacion: no pude refrescar marcadores ahora mismo."
        else:
            suffix = (
                "\n\n♻️ Autoevaluacion:"
                f" {int(liquidation_result.get('liquidados', 0) or 0)} pick(s) liquidadas,"
                f" {int(liquidation_result.get('pendientes', 0) or 0)} pendientes."
            )
        report_text = f"{report_text}{suffix}"

    return report_text, report
