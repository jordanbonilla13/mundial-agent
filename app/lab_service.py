import base64
import json
from datetime import UTC, datetime
from html import escape
from typing import Any, Callable
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from app.engine import ForecastRequest
from app.runtime_settings import RuntimeSettings


DISPLAY_TIMEZONE = ZoneInfo("Europe/Madrid")


def _parse_event_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None

    try:
        normalized = f"{text[:-1]}+00:00" if text.endswith("Z") else text
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)

    return dt


def _event_time_label(value: Any) -> str:
    dt = _parse_event_datetime(value)
    if dt is None:
        text = str(value or "").strip()
        if not text:
            return "-"
        time_part = text.split("T")[-1].replace("Z", "")
        return time_part[:5] if len(time_part) >= 5 else text

    return dt.astimezone(DISPLAY_TIMEZONE).strftime("%H:%M")


def _score_map(evento: dict[str, Any]) -> dict[str, int]:
    scores: dict[str, int] = {}

    for item in evento.get("scores") or []:
        name = item.get("name")
        score = item.get("score")
        if name is None or score is None:
            continue
        try:
            scores[str(name)] = int(score)
        except (TypeError, ValueError):
            continue

    return scores


def _result_side(home_score: int, away_score: int) -> str:
    if home_score > away_score:
        return "home"
    if away_score > home_score:
        return "away"
    return "draw"


def _selected_side(pick: dict[str, Any], home_team: str, away_team: str) -> str | None:
    tipo = str(pick.get("tipo_resultado_raw") or pick.get("tipo_resultado") or "").strip().lower()
    if tipo in {"home", "away", "draw"}:
        return tipo

    equipo_raw = str(pick.get("equipo_raw") or pick.get("equipo") or "").strip().lower()
    if equipo_raw == str(home_team or "").strip().lower():
        return "home"
    if equipo_raw == str(away_team or "").strip().lower():
        return "away"
    if equipo_raw == "draw":
        return "draw"
    return None


def _resolve_pick_result_with_score(pick: dict[str, Any], evento: dict[str, Any]) -> str | None:
    scores = _score_map(evento)
    home_team = str(evento.get("home_team") or "")
    away_team = str(evento.get("away_team") or "")

    if not home_team or not away_team or home_team not in scores or away_team not in scores:
        return None

    home_score = scores[home_team]
    away_score = scores[away_team]
    market = str(pick.get("mercado") or "").strip().lower()
    selection = str(pick.get("equipo_raw") or pick.get("equipo") or "").strip()
    description = str(pick.get("outcome_description") or "").strip()
    point = pick.get("outcome_point")

    try:
        point_value = float(point) if point is not None else None
    except (TypeError, ValueError):
        point_value = None

    if market == "h2h":
        selected = _selected_side(pick, home_team, away_team)
        if selected is None:
            return None
        return "win" if selected == _result_side(home_score, away_score) else "loss"

    if market == "spreads":
        if point_value is None:
            return None
        selected = _selected_side(pick, home_team, away_team)
        if selected == "home":
            adjusted_selected = home_score + point_value
            adjusted_other = away_score
        elif selected == "away":
            adjusted_selected = away_score + point_value
            adjusted_other = home_score
        else:
            return None

        if adjusted_selected == adjusted_other:
            return "push"
        return "win" if adjusted_selected > adjusted_other else "loss"

    if market in {"totals", "alternate_totals"}:
        if point_value is None:
            return None
        total = home_score + away_score
        if total == point_value:
            return "push"
        if selection.lower() == "over":
            return "win" if total > point_value else "loss"
        if selection.lower() == "under":
            return "win" if total < point_value else "loss"
        return None

    if market == "team_totals":
        if point_value is None or not description:
            return None
        team_score = scores.get(description)
        if team_score is None:
            return None
        if team_score == point_value:
            return "push"
        if selection.lower() == "over":
            return "win" if team_score > point_value else "loss"
        if selection.lower() == "under":
            return "win" if team_score < point_value else "loss"
        return None

    if market == "btts":
        both_score = home_score > 0 and away_score > 0
        if selection.lower() == "yes":
            return "win" if both_score else "loss"
        if selection.lower() == "no":
            return "win" if not both_score else "loss"
        return None

    if market == "double_chance":
        options = [part.strip().lower() for part in selection.split(" or ") if part.strip()]
        result_name = {
            "home": home_team.lower(),
            "away": away_team.lower(),
            "draw": "draw",
        }[_result_side(home_score, away_score)]
        return "win" if result_name in options else "loss"

    return None


def _simulated_profit_loss(pick: dict[str, Any], result: str) -> float:
    amount = float(pick.get("importe_sugerido") or 0)
    odds_raw = pick.get("cuota") or pick.get("cuota_apuesta") or pick.get("cuota_pinnacle") or 0
    odds = float(odds_raw or 0)
    if odds <= 1:
        return 0.0
    if result == "win":
        return round(amount * (odds - 1), 2)
    if result == "loss":
        return round(-amount, 2)
    return 0.0


def _simulate_historical_lab(
    *,
    picks: list[dict[str, Any]],
    fetch_scores: Callable[[int, str | None], list[dict[str, Any]]] | None,
) -> dict[str, Any]:
    if not picks or fetch_scores is None:
        return {
            "enabled": False,
            "evaluated": 0,
            "closed": 0,
            "pending": 0,
            "won": 0,
            "lost": 0,
            "push": 0,
            "staked": 0.0,
            "profit": 0.0,
            "roi": 0.0,
            "hit_rate": 0.0,
            "coverage_note": None,
        }

    events_by_id: dict[str, dict[str, Any]] = {}
    sport_keys = {
        str(pick.get("sport_key") or "").strip()
        for pick in picks
        if str(pick.get("sport_key") or "").strip()
    }
    for sport_key in sport_keys:
        try:
            sport_scores = fetch_scores(3, sport_key)
        except Exception:
            continue
        for event in sport_scores or []:
            event_id = str(event.get("id") or "").strip()
            if event_id:
                events_by_id[event_id] = event

    now = datetime.now(UTC)
    score_window_floor = now.replace(microsecond=0)
    won = lost = push = closed = 0
    staked = profit = 0.0
    coverage_missing = 0
    oldest_missing_event: datetime | None = None

    for pick in picks:
        pick["historical_result"] = None
        pick["historical_result_icon"] = "⏳"
        pick["historical_result_label"] = "Pendiente"
        pick["historical_profit_loss"] = None
        pick["historical_status_detail"] = "Sin marcador final todavia."

        event_dt = _parse_event_datetime(pick.get("commence_time"))
        if event_dt is not None and event_dt > now:
            pick["historical_status_detail"] = "El partido aun no ha empezado en el momento actual."
            continue

        event = events_by_id.get(str(pick.get("event_id") or "").strip())
        if not event:
            coverage_missing += 1
            if event_dt is not None and (oldest_missing_event is None or event_dt < oldest_missing_event):
                oldest_missing_event = event_dt
            pick["historical_status_detail"] = "No he encontrado marcador final en la ventana reciente del proveedor."
            continue

        if event.get("completed") is not True:
            pick["historical_status_detail"] = "El evento sigue abierto o sin cierre oficial."
            continue

        result = _resolve_pick_result_with_score(pick, event)
        if result is None:
            pick["historical_status_detail"] = "Mercado no liquidable automaticamente con el marcador recibido."
            continue

        pick["historical_result"] = result
        pick["historical_result_icon"] = "✅" if result == "win" else "❌" if result == "loss" else "➖"
        pick["historical_result_label"] = "Ganada" if result == "win" else "Perdida" if result == "loss" else "Nula"
        pick["historical_profit_loss"] = _simulated_profit_loss(pick, result)
        pick["historical_status_detail"] = "Resultado simulado con marcador final del proveedor."
        closed += 1
        staked += float(pick.get("importe_sugerido") or 0)
        profit += float(pick["historical_profit_loss"] or 0)
        if result == "win":
            won += 1
        elif result == "loss":
            lost += 1
        else:
            push += 1

    evaluated = len(picks)
    pending = evaluated - closed
    coverage_note = None
    if coverage_missing:
        if oldest_missing_event is not None and (now - oldest_missing_event).total_seconds() > 3 * 86400:
            coverage_note = (
                f"{coverage_missing} pick(s) siguen sin marcador recuperable. "
                "The Odds API solo devuelve scores de partidos completados hasta 3 dias atras."
            )
        else:
            coverage_note = (
                f"{coverage_missing} pick(s) siguen sin marcador recuperable en esta simulacion. "
                "The Odds API scores solo cubre ventana reciente."
            )

    return {
        "enabled": True,
        "evaluated": evaluated,
        "closed": closed,
        "pending": pending,
        "won": won,
        "lost": lost,
        "push": push,
        "staked": round(staked, 2),
        "profit": round(profit, 2),
        "roi": round((profit / staked) * 100, 2) if staked else 0.0,
        "hit_rate": round((won / closed) * 100, 2) if closed else 0.0,
        "coverage_note": coverage_note,
    }


def _build_match_overview(
    *,
    available_matches: list[dict[str, Any]],
    recommended: list[dict[str, Any]],
    discarded: list[dict[str, Any]],
    publishable: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_event: dict[str, dict[str, Any]] = {}

    for pick in recommended + discarded:
        event_id = str(pick.get("event_id") or "").strip()
        if not event_id:
            continue
        bucket = by_event.setdefault(
            event_id,
            {
                "event_id": event_id,
                "partido": pick.get("partido") or pick.get("partido_es") or event_id,
                "league_label": pick.get("league_label") or pick.get("sport_label") or "General",
                "commence_time": pick.get("commence_time"),
                "recommended": 0,
                "blocked": 0,
                "publishable": 0,
            },
        )
        bucket["partido"] = bucket.get("partido") or pick.get("partido") or pick.get("partido_es") or event_id
        bucket["league_label"] = bucket.get("league_label") or pick.get("league_label") or pick.get("sport_label") or "General"
        bucket["commence_time"] = bucket.get("commence_time") or pick.get("commence_time")
        if pick in recommended:
            bucket["recommended"] += 1
        if pick.get("performance_guard_blocked") or pick.get("risk_guard_blocked"):
            bucket["blocked"] += 1

    for pick in publishable:
        event_id = str(pick.get("event_id") or "").strip()
        if not event_id:
            continue
        bucket = by_event.setdefault(
            event_id,
            {
                "event_id": event_id,
                "partido": pick.get("partido") or pick.get("partido_es") or event_id,
                "league_label": pick.get("league_label") or pick.get("sport_label") or "General",
                "commence_time": pick.get("commence_time"),
                "recommended": 0,
                "blocked": 0,
                "publishable": 0,
            },
        )
        bucket["publishable"] += 1
        bucket["partido"] = bucket.get("partido") or pick.get("partido") or pick.get("partido_es") or event_id
        bucket["league_label"] = bucket.get("league_label") or pick.get("league_label") or pick.get("sport_label") or "General"
        bucket["commence_time"] = bucket.get("commence_time") or pick.get("commence_time")

    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()

    for item in available_matches:
        event_id = str(item.get("id") or "").strip()
        if not event_id:
            continue
        bucket = by_event.get(event_id, {}).copy()
        bucket.setdefault("event_id", event_id)
        bucket.setdefault("partido", item.get("label") or item.get("raw") or event_id)
        bucket.setdefault("league_label", "General")
        bucket.setdefault("commence_time", item.get("commence_time"))
        bucket.setdefault("recommended", 0)
        bucket.setdefault("blocked", 0)
        bucket.setdefault("publishable", 0)
        bucket["time_label"] = _event_time_label(bucket.get("commence_time") or item.get("commence_time"))
        if bucket["publishable"] > 0:
            bucket["status"] = "Publicable"
            bucket["status_kind"] = "ok"
        elif bucket["blocked"] > 0:
            bucket["status"] = "Bloqueado"
            bucket["status_kind"] = "danger"
        elif bucket["recommended"] > 0:
            bucket["status"] = "En revision"
            bucket["status_kind"] = "warn"
        else:
            bucket["status"] = "Sin picks"
            bucket["status_kind"] = "warn"
        ordered.append(bucket)
        seen.add(event_id)

    for event_id, bucket in by_event.items():
        if event_id in seen:
            continue
        bucket = bucket.copy()
        bucket["time_label"] = _event_time_label(bucket.get("commence_time"))
        if bucket["publishable"] > 0:
            bucket["status"] = "Publicable"
            bucket["status_kind"] = "ok"
        elif bucket["blocked"] > 0:
            bucket["status"] = "Bloqueado"
            bucket["status_kind"] = "danger"
        elif bucket["recommended"] > 0:
            bucket["status"] = "En revision"
            bucket["status_kind"] = "warn"
        else:
            bucket["status"] = "Sin picks"
            bucket["status_kind"] = "warn"
        ordered.append(bucket)

    return ordered


def _pick_within_range(
    pick: dict[str, Any],
    *,
    range_from: datetime | None,
    range_to: datetime | None,
) -> bool:
    if range_from is None and range_to is None:
        return True

    commence_dt = _parse_event_datetime(pick.get("commence_time"))
    if commence_dt is None:
        return True

    if range_from is not None and commence_dt < range_from:
        return False
    if range_to is not None and commence_dt > range_to:
        return False
    return True


def _lab_pick_snapshot(pick: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": pick.get("event_id"),
        "sport_key": pick.get("sport_key"),
        "sport_label": pick.get("sport_label"),
        "league_label": pick.get("league_label"),
        "commence_time": pick.get("commence_time"),
        "partido": pick.get("partido") or pick.get("partido_es"),
        "equipo": pick.get("equipo") or pick.get("equipo_es"),
        "equipo_raw": pick.get("equipo_raw") or pick.get("equipo"),
        "mercado": pick.get("mercado"),
        "tipo_resultado": pick.get("tipo_resultado"),
        "tipo_resultado_raw": pick.get("tipo_resultado_raw") or pick.get("tipo_resultado"),
        "outcome_point": pick.get("outcome_point"),
        "outcome_description": pick.get("outcome_description"),
        "casa": pick.get("casa"),
        "cuota": pick.get("cuota"),
        "cuota_apuesta": pick.get("cuota_apuesta"),
        "cuota_pinnacle": pick.get("cuota_pinnacle"),
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


def _lab_display_key(pick: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(pick.get("event_id") or "").strip().lower(),
        str(pick.get("mercado") or "").strip().lower(),
        str(pick.get("equipo") or "").strip().lower(),
    )


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
    fetch_scores: Callable[[int, str | None], list[dict[str, Any]]] | None = None,
    load_learning_summary: Callable[[], dict[str, Any]] | None = None,
    load_calibration_snapshot: Callable[[], Any] | None = None,
    todo_toggle_panel: dict[str, Any] | None = None,
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
    simulation_mode: str = "live",
    historical_snapshot_at: str | None = None,
    historical_range_from: str | None = None,
    historical_range_to: str | None = None,
) -> dict[str, Any]:
    historical_mode = str(simulation_mode or "live").strip().lower() == "historical"
    range_from_dt = _parse_event_datetime(historical_range_from)
    range_to_dt = _parse_event_datetime(historical_range_to)
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
            historical_mode=historical_mode,
            historical_date=historical_snapshot_at,
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
    if historical_mode and (range_from_dt is not None or range_to_dt is not None):
        blocked_recommended = [
            pick for pick in blocked_recommended
            if _pick_within_range(pick, range_from=range_from_dt, range_to=range_to_dt)
        ]
        blocked_discarded = [
            pick for pick in blocked_discarded
            if _pick_within_range(pick, range_from=range_from_dt, range_to=range_to_dt)
        ]
        publishable_preview = [
            pick for pick in publishable_preview
            if _pick_within_range(pick, range_from=range_from_dt, range_to=range_to_dt)
        ]
    guard_reasons = list(guard.get("reasons") or [])
    would_publish_live = bool(guard.get("allow_live_publication", False)) and not runtime_settings.shadow_mode
    if historical_mode:
        would_publish_live = False
        guard_reasons.insert(0, "Simulacion historica: el lab solo compara y no publica picks del pasado.")

    publication_decision = {
        "would_publish_live": would_publish_live,
        "runtime_mode": runtime_settings.publication_mode,
        "guard_mode": guard.get("mode"),
        "guard_reasons": guard_reasons,
    }
    match_overview = _build_match_overview(
        available_matches=list(forecast.get("partidos_disponibles", [])),
        recommended=forecast_recommended,
        discarded=forecast_discarded,
        publishable=publishable_preview,
    )
    historical_evaluation = _simulate_historical_lab(
        picks=publishable_preview,
        fetch_scores=fetch_scores if historical_mode else None,
    )
    learning_summary = {}
    if load_learning_summary is not None:
        try:
            learning_summary = dict(load_learning_summary() or {})
        except Exception:
            learning_summary = {}

    calibration_snapshot = None
    if load_calibration_snapshot is not None:
        try:
            calibration_snapshot = load_calibration_snapshot()
        except Exception:
            calibration_snapshot = None

    learning_panel = {
        "training_samples": int(learning_summary.get("training_samples", 0) or 0),
        "snapshots_guardados": int(learning_summary.get("snapshots_guardados", 0) or 0),
        "picks_evaluadas": int(learning_summary.get("picks_evaluadas", 0) or 0),
        "clv_positivo_pct": float(learning_summary.get("porcentaje_clv_positivo", 0) or 0),
        "lectura": str(learning_summary.get("lectura") or ""),
        "sport_penalties": [],
        "market_thresholds": [],
        "bookmaker_penalties": [],
    }
    if calibration_snapshot is not None:
        adjustments = getattr(calibration_snapshot, "model_adjustments", {}) or {}
        learning_panel["sport_penalties"] = sorted(
            list((adjustments.get("sport_penalties") or {}).items()),
            key=lambda item: float(item[1] or 0),
            reverse=True,
        )[:5]
        learning_panel["market_thresholds"] = sorted(
            list((adjustments.get("market_thresholds") or {}).items()),
            key=lambda item: abs(float(item[1] or 0)),
            reverse=True,
        )[:5]
        learning_panel["bookmaker_penalties"] = sorted(
            list((adjustments.get("bookmaker_penalties") or {}).items()),
            key=lambda item: float(item[1] or 0),
            reverse=True,
        )[:5]
    return {
        "runtime_mode": runtime_settings.publication_mode,
        "publication_guard": guard,
        "publication_decision": publication_decision,
        "match_overview": match_overview,
        "simulation_context": {
            "mode": "historical" if historical_mode else "live",
            "historical_mode": historical_mode,
            "snapshot_at": forecast.get("historical_snapshot_at") or historical_snapshot_at,
            "range_from": historical_range_from,
            "range_to": historical_range_to,
            "market_notice": forecast.get("historical_market_notice"),
            "provider_name": forecast.get("proveedor_cuotas"),
            "snapshots_guardados": int(forecast.get("snapshots_guardados", 0) or 0),
        },
        "learning_panel": learning_panel,
        "todo_toggle_panel": todo_toggle_panel or {"sports": [], "leagues": [], "disabled_sports": set(), "disabled_leagues": set()},
        "historical_evaluation": historical_evaluation,
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


def build_empty_lab_run(
    *,
    runtime_settings: RuntimeSettings,
    todo_toggle_panel: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "runtime_mode": runtime_settings.publication_mode,
        "publication_guard": {
            "allow_live_publication": False,
            "mode": runtime_settings.publication_mode,
            "reasons": ["lab_idle"],
        },
        "publication_decision": {
            "would_publish_live": False,
            "runtime_mode": runtime_settings.publication_mode,
            "guard_mode": runtime_settings.publication_mode,
            "guard_reasons": ["Pulsa 'Ejecutar lab' para lanzar la simulacion."],
        },
        "match_overview": [],
        "simulation_context": {
            "mode": "live",
            "historical_mode": False,
            "snapshot_at": None,
            "range_from": None,
            "range_to": None,
            "market_notice": None,
            "provider_name": None,
            "snapshots_guardados": 0,
        },
        "forecast_summary": {
            "sport_label": "Todo",
            "league_label": "Todas las ligas",
            "total_analizadas": 0,
            "total_recomendadas": 0,
            "total_descartadas_preview": 0,
            "total_publicables_preview": 0,
            "total_bloqueadas_en_recomendadas": 0,
            "total_bloqueadas_en_descartadas": 0,
        },
        "historical_evaluation": {
            "enabled": False,
            "evaluated": 0,
            "closed": 0,
            "pending": 0,
            "won": 0,
            "lost": 0,
            "push": 0,
            "staked": 0.0,
            "profit": 0.0,
            "roi": 0.0,
            "hit_rate": 0.0,
            "coverage_note": None,
        },
        "learning_panel": {
            "training_samples": 0,
            "snapshots_guardados": 0,
            "picks_evaluadas": 0,
            "clv_positivo_pct": 0.0,
            "lectura": "",
            "sport_penalties": [],
            "market_thresholds": [],
            "bookmaker_penalties": [],
        },
        "todo_toggle_panel": todo_toggle_panel or {
            "sports": [],
            "leagues": [],
            "disabled_sports": set(),
            "disabled_leagues": set(),
        },
        "publishable_preview": [],
        "blocked_picks": {
            "recommended": [],
            "discarded": [],
        },
        "forecast": {},
        "telegram_preview": {
            "resumen_telegram": "Todavia no se ha ejecutado la simulacion. Ajusta filtros y pulsa Ejecutar lab.",
            "pronosticos": [],
            "mensajes_telegram": [],
        },
    }


def _badge_class(kind: str) -> str:
    return {
        "ok": "badge-green",
        "warn": "badge-yellow",
        "danger": "badge-red",
    }.get(kind, "badge-yellow")


def _market_label(value: Any) -> str:
    market = str(value or "").strip().lower()
    return {
        "h2h": "Ganador",
        "spreads": "Handicap",
        "totals": "Totales",
        "alternate_totals": "Totales alternativos",
        "btts": "Ambos anotan",
        "double_chance": "Doble oportunidad",
        "team_totals": "Totales por equipo",
    }.get(market, str(value or "mercado"))


def _pick_card_html(pick: dict[str, Any], *, blocked: bool) -> str:
    title = escape(str(pick.get("partido") or "Partido sin nombre"))
    team = escape(str(pick.get("equipo") or "Seleccion sin nombre"))
    market = escape(_market_label(pick.get("mercado")))
    league = escape(str(pick.get("league_label") or pick.get("sport_label") or "General"))
    house = escape(str(pick.get("casa") or "Casa"))
    reason = escape(str(pick.get("performance_guard_reason") or pick.get("motivo") or "Sin detalle"))
    recommendation = escape(str(pick.get("recomendacion") or "Sin recomendacion"))
    stake = pick.get("stake")
    stake_text = f"{float(stake):.2f}" if isinstance(stake, (int, float)) else escape(str(stake or "-"))
    amount = pick.get("importe_sugerido")
    amount_text = f"EUR {float(amount):.2f}" if isinstance(amount, (int, float)) else escape(str(amount or "-"))
    quality = pick.get("quality_score")
    confidence = pick.get("confidence_score")
    quality_text = escape(str(quality if quality is not None else "-"))
    confidence_text = f"{float(confidence):.3f}" if isinstance(confidence, (int, float)) else escape(str(confidence or "-"))
    badge_kind = "danger" if blocked else "ok"
    badge_text = "Bloqueada" if blocked else "Publicable"
    card_class = "bet-red" if blocked else "bet-green"
    historical_html = ""
    if pick.get("historical_result_label"):
        result_label = escape(str(pick.get("historical_result_label") or "Pendiente"))
        result_icon = escape(str(pick.get("historical_result_icon") or "⏳"))
        detail = escape(str(pick.get("historical_status_detail") or ""))
        profit_loss = pick.get("historical_profit_loss")
        profit_text = f" | P/L simulado: EUR {float(profit_loss):+.2f}" if isinstance(profit_loss, (int, float)) else ""
        historical_html = (
            f'<p><strong>{result_icon} {result_label}</strong><span class="muted">{profit_text}</span></p>'
            f'<p class="muted">{detail}</p>'
        )
    return f"""
        <article class="card bet-card {card_class}">
            <div class="badge {_badge_class(badge_kind)}">{badge_text}</div>
            <h3>{title}</h3>
            <p><strong>{team}</strong> | {market} | {house}</p>
            <p class="muted">{league}</p>
            <div class="summary">
                <span>Stake: {stake_text}</span>
                <span>Importe: {amount_text}</span>
                <span>Quality: {quality_text}</span>
                <span>Confidence: {confidence_text}</span>
            </div>
            <p><strong>{recommendation}</strong></p>
            <p class="muted">{reason}</p>
            {historical_html}
        </article>
    """


def _option_tags(options: list[dict[str, str]], selected_value: str) -> str:
    html = []
    for option in options:
        value = str(option.get("value") or "")
        label = str(option.get("label") or value)
        selected = " selected" if value == selected_value else ""
        html.append(f'<option value="{escape(value, quote=True)}"{selected}>{escape(label)}</option>')
    return "".join(html)


def render_lab_run_html(
    lab: dict[str, Any],
    *,
    query_params: dict[str, Any],
    premium_css: Callable[[], str],
    profile_options: list[dict[str, str]],
    mode_options: list[dict[str, str]],
    sport_options: list[dict[str, str]],
    market_options: list[dict[str, str]],
    match_options: list[dict[str, str]],
) -> str:
    forecast_summary = lab.get("forecast_summary") or {}
    publication_decision = lab.get("publication_decision") or {}
    simulation_context = lab.get("simulation_context") or {}
    publishable_preview = lab.get("publishable_preview") or []
    blocked_picks = lab.get("blocked_picks") or {}
    historical_evaluation = lab.get("historical_evaluation") or {}
    learning_panel = lab.get("learning_panel") or {}
    todo_toggle_panel = lab.get("todo_toggle_panel") or {}
    blocked_recommended = blocked_picks.get("recommended") or []
    blocked_discarded = blocked_picks.get("discarded") or []
    match_overview = lab.get("match_overview") or []
    reasons = publication_decision.get("guard_reasons") or []
    runtime_mode = escape(str(lab.get("runtime_mode") or "shadow"))
    historical_mode = bool(simulation_context.get("historical_mode"))
    snapshot_at = escape(str(simulation_context.get("snapshot_at") or ""))
    range_from_value = escape(str(simulation_context.get("range_from") or ""))
    range_to_value = escape(str(simulation_context.get("range_to") or ""))
    market_notice = escape(str(simulation_context.get("market_notice") or ""))
    provider_name = escape(str(simulation_context.get("provider_name") or "the_odds_api"))
    snapshots_guardados = int(simulation_context.get("snapshots_guardados", 0) or 0)
    sport_label = escape(str(forecast_summary.get("sport_label") or "Todo"))
    league_label = escape(str(forecast_summary.get("league_label") or "Todas las ligas"))
    would_publish_live = bool(publication_decision.get("would_publish_live"))
    status_badge = "ok" if would_publish_live else ("warn" if runtime_mode == "shadow" else "danger")
    status_label = "Publicaria en vivo" if would_publish_live else ("Modo sombra" if runtime_mode == "shadow" else "Bloqueado")
    reasons_html = "".join(f"<li>{escape(str(reason))}</li>" for reason in reasons) or "<li>Sin bloqueos activos.</li>"
    query_json = dict(query_params)
    query_json["format"] = "json"
    json_href = "/lab/run?" + urlencode(query_json)
    query_refresh = dict(query_params)
    query_refresh.pop("format", None)
    refresh_href = "/lab/run?" + urlencode(query_refresh)
    current_filters = "".join(
        f"<span>{escape(str(key))}: {escape(str(value))}</span>"
        for key, value in query_refresh.items()
        if value not in (None, "")
    )
    publishable_cards = "".join(_pick_card_html(pick, blocked=False) for pick in publishable_preview) or '<div class="card"><p class="muted">No hay picks publicables con estos filtros.</p></div>'
    publishable_keys = {_lab_display_key(pick) for pick in publishable_preview}
    visible_blocked = [
        pick
        for pick in (blocked_recommended + blocked_discarded)
        if _lab_display_key(pick) not in publishable_keys
    ]
    blocked_cards = "".join(_pick_card_html(pick, blocked=True) for pick in visible_blocked) or '<div class="card"><p class="muted">No hay picks bloqueadas en esta simulacion.</p></div>'
    telegram_preview = lab.get("telegram_preview") or {}
    summary_message = escape(str(telegram_preview.get("resumen_telegram") or "Sin resumen"))
    publish_payload_b64 = ""
    try:
        publish_payload_b64 = base64.b64encode(
            json.dumps(telegram_preview, ensure_ascii=False).encode("utf-8")
        ).decode("ascii")
    except Exception:
        publish_payload_b64 = ""
    blocked_total = len(visible_blocked)
    historical_summary_html = ""
    if historical_mode and historical_evaluation.get("enabled"):
        coverage_note = historical_evaluation.get("coverage_note")
        coverage_html = f'<p class="muted">{escape(str(coverage_note))}</p>' if coverage_note else ""
        historical_summary_html = f"""
            <section class="panel" style="padding: 18px; margin-top: 18px;">
                <div class="eyebrow" style="color: var(--brand); margin-bottom: 12px;">Backtest del snapshot</div>
                <p class="lede">Esto no toca tu cartera real ni Telegram: solo mide como habria salido la cartera publicable del lab con marcadores finales disponibles.</p>
                <section class="grid-2">
                    <div class="metric">
                        <span>Evaluadas</span>
                        <strong>{int(historical_evaluation.get("evaluated", 0) or 0)}</strong>
                        <small>Picks simuladas</small>
                    </div>
                    <div class="metric">
                        <span>Cerradas</span>
                        <strong>{int(historical_evaluation.get("closed", 0) or 0)}</strong>
                        <small>Con marcador final</small>
                    </div>
                    <div class="metric">
                        <span>W-L-N</span>
                        <strong>{int(historical_evaluation.get("won", 0) or 0)}-{int(historical_evaluation.get("lost", 0) or 0)}-{int(historical_evaluation.get("push", 0) or 0)}</strong>
                        <small>Ganadas, perdidas y nulas</small>
                    </div>
                    <div class="metric">
                        <span>Pendientes</span>
                        <strong>{int(historical_evaluation.get("pending", 0) or 0)}</strong>
                        <small>Sin cierre recuperado</small>
                    </div>
                    <div class="metric">
                        <span>Beneficio</span>
                        <strong>EUR {float(historical_evaluation.get("profit", 0) or 0):+.2f}</strong>
                        <small>Resultado simulado</small>
                    </div>
                    <div class="metric">
                        <span>ROI / Hit</span>
                        <strong>{float(historical_evaluation.get("roi", 0) or 0):+.2f}% | {float(historical_evaluation.get("hit_rate", 0) or 0):.2f}%</strong>
                        <small>Sobre picks cerradas</small>
                    </div>
                </section>
                {coverage_html}
            </section>
        """
    sport_penalty_lines = "".join(
        f"<li>{escape(str(name))}: penalizacion {float(value):.2f}</li>"
        for name, value in (learning_panel.get("sport_penalties") or [])
    ) or "<li>Sin penalizaciones de deporte destacadas.</li>"
    market_adjustment_lines = "".join(
        f"<li>{escape(str(name))}: ajuste {float(value):+.2f}</li>"
        for name, value in (learning_panel.get("market_thresholds") or [])
    ) or "<li>Sin ajustes de mercado destacados.</li>"
    bookmaker_penalty_lines = "".join(
        f"<li>{escape(str(name))}: penalizacion {float(value):.2f}</li>"
        for name, value in (learning_panel.get("bookmaker_penalties") or [])
    ) or "<li>Sin penalizaciones de casa destacadas.</li>"
    bankroll_value = "" if query_params.get("bankroll") in (None, "") else escape(str(query_params.get("bankroll")), quote=True)
    solo_stakazos_checked = "checked" if str(query_params.get("solo_stakazos") or "false").lower() == "true" else ""
    simulation_mode_value = str(query_params.get("simulation_mode") or "live")
    snapshot_input_value = escape(str(query_params.get("snapshot_at") or ""), quote=True)
    range_from_input_value = escape(str(query_params.get("snapshot_from") or ""), quote=True)
    range_to_input_value = escape(str(query_params.get("snapshot_to") or ""), quote=True)
    profile_tags = _option_tags(profile_options, str(query_params.get("perfil") or "moderado"))
    mode_tags = _option_tags(mode_options, str(query_params.get("modo") or "comparador"))
    sport_tags = _option_tags(sport_options, str(query_params.get("deporte") or "todo"))
    market_tags = _option_tags(market_options, str(query_params.get("mercados") or "todo"))
    match_tags = _option_tags(match_options, str(query_params.get("partido") or "todos"))
    notice_code = str(query_params.get("lab_notice") or "").strip().lower()
    has_run = str(query_params.get("execute") or "").strip().lower() in {"1", "true", "yes", "si", "on"}
    notice_html = ""
    if notice_code == "published":
        publication_id = escape(str(query_params.get("publication_id") or "-"))
        registered_picks = escape(str(query_params.get("registered_picks") or "0"))
        sent_messages = escape(str(query_params.get("sent_messages") or "0"))
        notice_html = (
            f'<section class="card" style="margin-top: 18px; border-color: rgba(63,132,97,0.24);">'
            f'<div class="badge badge-green">Cartera registrada</div>'
            f'<p class="muted">La simulacion se ha guardado como cartera del modelo para auditoria. '
            f'Publicacion #{publication_id} | picks registradas: {registered_picks} | mensajes enviados: {sent_messages}.</p>'
            f'</section>'
        )
    elif notice_code == "empty":
        notice_html = (
            '<section class="card" style="margin-top: 18px; border-color: rgba(164,118,36,0.24);">'
            '<div class="badge badge-yellow">Sin publicables</div>'
            '<p class="muted">No habia picks publicables con esos filtros, asi que no se ha registrado ninguna cartera.</p>'
            '</section>'
        )
    elif notice_code == "queued":
        job_id = escape(str(query_params.get("job_id") or "-"))
        registered_picks = escape(str(query_params.get("registered_picks") or "0"))
        notice_html = (
            f'<section class="card" style="margin-top: 18px; border-color: rgba(46,108,171,0.24);">'
            f'<div class="badge badge-yellow">Publicacion en curso</div>'
            f'<p class="muted">El envio a Telegram se ha lanzado en segundo plano para no bloquear la web. '
            f'Job {job_id} | picks preparadas: {registered_picks}. Refresca en unos segundos si quieres seguir revisando el lab.</p>'
            f'</section>'
        )
    elif notice_code == "snapshot_required":
        notice_html = (
            '<section class="card" style="margin-top: 18px; border-color: rgba(164,118,36,0.24);">'
            '<div class="badge badge-yellow">Fecha requerida</div>'
            '<p class="muted">Para ejecutar el modo historico del lab tienes que indicar un snapshot o una fecha desde.</p>'
            '</section>'
        )
    elif notice_code == "toggles_saved":
        notice_html = (
            '<section class="card" style="margin-top: 18px; border-color: rgba(63,132,97,0.24);">'
            '<div class="badge badge-green">Filtros guardados</div>'
            '<p class="muted">Los deportes y ligas desactivados ya no se tendran en cuenta cuando uses <strong>deporte=todo</strong> ni cuando lances <strong>/apuestas</strong> desde Telegram.</p>'
            '</section>'
        )
    elif not has_run:
        notice_html = (
            '<section class="card" style="margin-top: 18px; border-color: rgba(46,108,171,0.24);">'
            '<div class="badge badge-yellow">Lab en espera</div>'
            '<p class="muted">Esta pantalla ya no consulta cuotas al abrirse. Ajusta filtros y pulsa <strong>Ejecutar lab</strong> cuando quieras lanzar la simulacion.</p>'
            '</section>'
        )
    publish_form_inputs = "".join(
        f'<input type="hidden" name="{escape(str(key), quote=True)}" value="{escape(str(value), quote=True)}">'
        for key, value in query_refresh.items()
        if key not in {"format", "lab_notice", "publication_id", "registered_picks", "sent_messages", "job_id", "execute"} and value not in (None, "")
    )
    toggle_form_inputs = "".join(
        f'<input type="hidden" name="{escape(str(key), quote=True)}" value="{escape(str(value), quote=True)}">'
        for key, value in query_refresh.items()
        if key not in {"format", "lab_notice", "publication_id", "registered_picks", "sent_messages", "job_id", "execute"} and value not in (None, "")
    )
    todo_sports = todo_toggle_panel.get("sports") or []
    todo_leagues = todo_toggle_panel.get("leagues") or []
    sport_toggle_cards = "".join(
        (
            '<form method="post" action="/lab/run/todo-filters" class="toggle-card">'
            '<input type="hidden" name="scope" value="sport">'
            f'<input type="hidden" name="key" value="{escape(str(item.get("key") or ""), quote=True)}">'
            f'<input type="hidden" name="enabled" value="{"false" if item.get("enabled") else "true"}">'
            f"{toggle_form_inputs}"
            '<button type="submit" class="toggle-row">'
            '<span class="toggle-copy">'
            f'<strong>{escape(str(item.get("label") or item.get("key") or "Deporte"))}</strong>'
            f'<small>{"Activo en deporte=todo y /apuestas" if item.get("enabled") else "Ignorado en deporte=todo y /apuestas"}</small>'
            '</span>'
            f'<span class="toggle-switch {"on" if item.get("enabled") else "off"}" aria-hidden="true"><span class="toggle-knob"></span></span>'
            '</button>'
            '</form>'
        )
        for item in todo_sports
        if str(item.get("key") or "").strip()
    ) or '<div class="card"><p class="muted">Todavia no hay deportes configurables para el modo todo.</p></div>'
    league_toggle_cards = "".join(
        (
            '<form method="post" action="/lab/run/todo-filters" class="toggle-card">'
            '<input type="hidden" name="scope" value="league">'
            f'<input type="hidden" name="key" value="{escape(str(item.get("key") or ""), quote=True)}">'
            f'<input type="hidden" name="enabled" value="{"false" if item.get("enabled") else "true"}">'
            f"{toggle_form_inputs}"
            '<button type="submit" class="toggle-row">'
            '<span class="toggle-copy">'
            f'<strong>{escape(str(item.get("label") or item.get("key") or "Liga"))}</strong>'
            f'<small>{"Activa en deporte=todo y /apuestas" if item.get("enabled") else "Ignorada en deporte=todo y /apuestas"}</small>'
            '</span>'
            f'<span class="toggle-switch {"on" if item.get("enabled") else "off"}" aria-hidden="true"><span class="toggle-knob"></span></span>'
            '</button>'
            '</form>'
        )
        for item in todo_leagues
        if str(item.get("key") or "").strip()
    ) or '<div class="card"><p class="muted">Todavia no hay ligas detectadas para este proveedor.</p></div>'
    publish_action_html = ""
    if publishable_preview and not historical_mode:
        publish_action_html = (
            '<form method="post" action="/lab/run/publicar" class="cta-row">'
            f"{publish_form_inputs}"
            f'<input type="hidden" name="lab_payload" value="{escape(publish_payload_b64, quote=True)}">'
            '<button type="submit">Publicar en Telegram y registrar cartera</button>'
            '<span class="muted">Envia estas picks al canal y las deja guardadas para que /resumen las audite despues.</span>'
            "</form>"
        )
    elif publishable_preview and historical_mode:
        publish_action_html = (
            '<div class="card" style="border-color: rgba(164,118,36,0.24);">'
            '<div class="badge badge-yellow">Solo simulacion</div>'
            '<p class="muted">Estas picks vienen de un snapshot historico, asi que el lab no permite publicarlas ni registrarlas en cartera real.</p>'
            '</div>'
        )
    match_rows = "".join(
        f'<tr><td>{escape(str(row.get("time_label") or "-"))}</td><td>{escape(str(row.get("partido") or row.get("event_id") or "Partido"))}</td><td>{escape(str(row.get("league_label") or "General"))}</td><td><span class="badge {_badge_class(str(row.get("status_kind") or "warn"))}">{escape(str(row.get("status") or "Sin picks"))}</span></td><td>{int(row.get("publishable") or 0)} / {int(row.get("blocked") or 0)}</td></tr>'
        for row in match_overview[:12]
    ) or '<tr><td colspan="5" class="muted">No hay partidos disponibles para este filtro.</td></tr>'

    return f"""
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Lab Run</title>
        <style>
            {premium_css()}
            .lab-shell {{
                max-width: 1180px;
                margin: 0 auto;
            }}
            .lab-grid {{
                display: grid;
                grid-template-columns: minmax(0, 1.35fr) minmax(300px, 0.85fr);
                gap: 18px;
                align-items: start;
            }}
            .stack {{
                display: grid;
                gap: 16px;
            }}
            .code-box {{
                background: rgba(16, 35, 60, 0.06);
                border: 1px solid var(--line);
                padding: 14px;
                border-radius: 14px;
                color: var(--ink);
                white-space: pre-wrap;
                word-break: break-word;
                font-size: 13px;
            }}
            .reasons-list {{
                margin: 0;
                padding-left: 18px;
                color: var(--muted);
            }}
            .cta-row {{
                display: flex;
                gap: 10px;
                flex-wrap: wrap;
                margin-top: 18px;
            }}
            .section-grid {{
                display: grid;
                gap: 16px;
            }}
            .toggle-panel {{
                display: grid;
                gap: 18px;
            }}
            .toggle-grid {{
                display: grid;
                gap: 12px;
            }}
            .toggle-card {{
                margin: 0;
            }}
            .toggle-row {{
                width: 100%;
                border: 1px solid var(--line);
                border-radius: 18px;
                background: rgba(255,255,255,0.82);
                padding: 14px 16px;
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 16px;
                cursor: pointer;
                transition: transform 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
            }}
            .toggle-row:hover {{
                transform: translateY(-1px);
                border-color: rgba(29, 52, 84, 0.18);
                box-shadow: 0 12px 28px rgba(17, 31, 53, 0.08);
            }}
            .toggle-copy {{
                display: grid;
                gap: 6px;
                text-align: left;
            }}
            .toggle-copy strong {{
                color: var(--ink);
                font-size: 18px;
            }}
            .toggle-copy small {{
                color: var(--muted);
                font-size: 13px;
                line-height: 1.45;
            }}
            .toggle-switch {{
                width: 62px;
                height: 34px;
                border-radius: 999px;
                padding: 4px;
                position: relative;
                flex: 0 0 auto;
                transition: background 0.18s ease;
            }}
            .toggle-switch.on {{
                background: linear-gradient(135deg, #2d6ea8, #183357);
            }}
            .toggle-switch.off {{
                background: rgba(18, 39, 66, 0.18);
            }}
            .toggle-knob {{
                display: block;
                width: 26px;
                height: 26px;
                border-radius: 999px;
                background: #fffdf7;
                box-shadow: 0 6px 18px rgba(0, 0, 0, 0.18);
                transition: transform 0.18s ease;
            }}
            .toggle-switch.on .toggle-knob {{
                transform: translateX(28px);
            }}
            .loading-overlay {{
                position: fixed;
                inset: 0;
                background: rgba(8, 18, 34, 0.78);
                backdrop-filter: blur(8px);
                display: none;
                align-items: center;
                justify-content: center;
                padding: 24px;
                z-index: 9999;
            }}
            .loading-overlay.visible {{
                display: flex;
            }}
            .loading-card {{
                width: min(560px, 100%);
                background: linear-gradient(145deg, rgba(12, 28, 51, 0.96), rgba(28, 51, 87, 0.94));
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 28px;
                box-shadow: 0 28px 80px rgba(0, 0, 0, 0.28);
                padding: 34px 28px;
                color: #f8f4ea;
                text-align: center;
            }}
            .loading-spinner {{
                width: 62px;
                height: 62px;
                margin: 0 auto 18px;
                border-radius: 999px;
                border: 4px solid rgba(255, 255, 255, 0.16);
                border-top-color: #f2dfb4;
                animation: lab-spin 0.9s linear infinite;
            }}
            .loading-card h2 {{
                margin: 0 0 10px;
                color: #fffaf0;
                font-size: 30px;
            }}
            .loading-card p {{
                margin: 0;
                color: rgba(248, 244, 234, 0.82);
                font-size: 16px;
                line-height: 1.6;
            }}
            .loading-steps {{
                margin: 22px 0 0;
                padding: 0;
                list-style: none;
                display: grid;
                gap: 10px;
                text-align: left;
            }}
            .loading-steps li {{
                background: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 999px;
                padding: 10px 14px;
                color: rgba(248, 244, 234, 0.78);
            }}
            .loading-steps li.active {{
                background: rgba(242, 223, 180, 0.16);
                border-color: rgba(242, 223, 180, 0.35);
                color: #fff8e7;
            }}
            @keyframes lab-spin {{
                from {{ transform: rotate(0deg); }}
                to {{ transform: rotate(360deg); }}
            }}
            @media (max-width: 900px) {{
                .lab-grid {{
                    grid-template-columns: 1fr;
                }}
            }}
        </style>
    </head>
    <body>
        <div class="loading-overlay" id="labLoadingOverlay" aria-hidden="true">
            <div class="loading-card">
                <div class="loading-spinner"></div>
                <h2>Ejecutando simulacion</h2>
                <p id="labLoadingMessage">Obteniendo cuotas y horarios actualizados...</p>
                <ul class="loading-steps" id="labLoadingSteps">
                    <li class="active">Obteniendo cuotas y horarios actualizados</li>
                    <li>Comparando precios entre casas</li>
                    <li>Priorizando mejores ligas y partidos activos</li>
                    <li>Filtrando value, riesgo y picks publicables</li>
                </ul>
            </div>
        </div>
        <div class="container lab-shell">
            <div class="top-menu">
                <a href="/dashboard">Dashboard</a>
                <a href="/informe-hoy">Informe hoy</a>
                <a href="/mis-apuestas">Mis apuestas</a>
                <a class="active" href="{refresh_href}">Lab run</a>
                <a href="{json_href}">Ver JSON</a>
            </div>
            <section class="hero">
                <div class="eyebrow">Laboratorio del modelo</div>
                <h1>{sport_label} | {league_label}</h1>
                <p>Esta vista resume lo que el bot habria preparado para Telegram, por que estaria en modo sombra o bloqueado y que picks hemos frenado antes de publicarlas.</p>
                <div class="hero-metrics">
                    <div class="hero-metric">
                        <span>Estado</span>
                        <strong>{escape(status_label)}</strong>
                    </div>
                    <div class="hero-metric">
                        <span>Analizadas</span>
                        <strong>{int(forecast_summary.get("total_analizadas", 0) or 0)}</strong>
                    </div>
                    <div class="hero-metric">
                        <span>Publicables</span>
                        <strong>{int(forecast_summary.get("total_publicables_preview", 0) or 0)}</strong>
                    </div>
                    <div class="hero-metric">
                        <span>Bloqueadas</span>
                        <strong>{blocked_total}</strong>
                    </div>
                </div>
                <div class="summary">
                    <span>Runtime: {runtime_mode}</span>
                    <span>Perfil: {escape(str(query_params.get("perfil") or "moderado"))}</span>
                    <span>Modo: {escape(str(query_params.get("modo") or "comparador"))}</span>
                    <span>Simulacion: {"historica" if historical_mode else "actual"}</span>
                    <span>Mercados: {escape(str(query_params.get("mercados") or "todo"))}</span>
                    <span>Partido: {escape(str(query_params.get("partido") or "todos"))}</span>
                </div>
                <div class="cta-row">
                    <a class="button-link" href="{refresh_href}">Refrescar simulacion</a>
                    <a class="button-link secondary" href="{json_href}">Abrir JSON tecnico</a>
                    <a class="button-link secondary" href="/tracking/panel">Abrir panel reciente</a>
                </div>
            </section>
            <section class="filters">
                <div class="eyebrow" style="color: var(--brand); margin-bottom: 12px;">Configurar simulacion</div>
                <form method="get" action="/lab/run" id="labRunForm">
                    <input type="hidden" name="execute" value="true">
                    <div class="field">
                        <label>Bankroll</label>
                        <input type="number" step="0.01" name="bankroll" value="{bankroll_value}" placeholder="Opcional">
                    </div>
                    <div class="field">
                        <label>Perfil</label>
                        <select name="perfil">{profile_tags}</select>
                    </div>
                    <div class="field">
                        <label>Modo</label>
                        <select name="modo">{mode_tags}</select>
                    </div>
                    <div class="field">
                        <label>Deporte</label>
                        <select name="deporte">{sport_tags}</select>
                    </div>
                    <div class="field">
                        <label>Simulacion</label>
                        <select name="simulation_mode" id="simulation_mode">
                            <option value="live" {"selected" if simulation_mode_value == "live" else ""}>Actual</option>
                            <option value="historical" {"selected" if simulation_mode_value == "historical" else ""}>Historica</option>
                        </select>
                    </div>
                    <div class="field" id="snapshotField">
                        <label>Snapshot historico</label>
                        <input type="datetime-local" name="snapshot_at" value="{snapshot_input_value}">
                    </div>
                    <div class="field" id="rangeFromField">
                        <label>Desde</label>
                        <input type="datetime-local" name="snapshot_from" value="{range_from_input_value}">
                    </div>
                    <div class="field" id="rangeToField">
                        <label>Hasta</label>
                        <input type="datetime-local" name="snapshot_to" value="{range_to_input_value}">
                    </div>
                    <div class="field">
                        <label>Mercados</label>
                        <select name="mercados">{market_tags}</select>
                    </div>
                    <div class="field">
                        <label>Partido disponible</label>
                        <select name="partido">{match_tags}</select>
                    </div>
                    <div class="checkbox-row">
                        <input type="checkbox" id="solo_stakazos" name="solo_stakazos" value="true" {solo_stakazos_checked}>
                        <label for="solo_stakazos" style="margin: 0; text-transform: none; letter-spacing: 0;">Solo stakazos</label>
                    </div>
                    <div class="cta-row">
                        <button type="submit">Ejecutar lab</button>
                        <a class="button-link secondary" href="/lab/run">Resetear</a>
                    </div>
                </form>
                <div class="summary">{current_filters}</div>
            </section>
            <section class="panel" style="padding: 18px; margin-top: 18px;">
                <div class="eyebrow" style="color: var(--brand); margin-bottom: 12px;">Control de deportes y ligas para Todo</div>
                <p class="lede">Este panel solo afecta cuando buscas con <strong>deporte=todo</strong>. Tambien lo usa el comando <strong>/apuestas</strong> de Telegram para ignorar deportes o ligas que quieras apagar temporalmente.</p>
                <div class="toggle-panel">
                    <div>
                        <h3 style="margin-bottom: 12px;">Deportes base</h3>
                        <div class="toggle-grid">{sport_toggle_cards}</div>
                    </div>
                    <div>
                        <h3 style="margin-bottom: 12px;">Ligas detectadas</h3>
                        <div class="toggle-grid">{league_toggle_cards}</div>
                    </div>
                </div>
            </section>
            {notice_html}
            {historical_summary_html}
            <section class="panel" style="padding: 18px; margin-top: 18px;">
                <div class="eyebrow" style="color: var(--brand); margin-bottom: 12px;">Mapa rapido de partidos</div>
                <p class="lede">Usa esta tabla para ver antes de lanzar la simulacion que eventos hay, a que hora van y si ya apuntan a publicable, bloqueado o sin picks.</p>
                <table>
                    <thead>
                        <tr>
                            <th>Hora</th>
                            <th>Partido</th>
                            <th>Liga</th>
                            <th>Estado</th>
                            <th>Preview</th>
                        </tr>
                    </thead>
                    <tbody>
                        {match_rows}
                    </tbody>
                </table>
            </section>
            <div class="lab-grid">
                <div class="section-grid">
                    <h2 class="section-title">Picks que saldrian a Telegram</h2>
                    <p class="lede">Esta es la previsualizacion operativa. Si el guard lo permite y sales de shadow mode, esto es lo mas cercano a lo que se publicaria.</p>
                    {publish_action_html}
                    {publishable_cards}
                    <h2 class="section-title">Picks frenadas por guards</h2>
                    <p class="lede">Aqui se ven las picks que el sistema ha parado por riesgo o por rendimiento historico insuficiente.</p>
                    {blocked_cards}
                </div>
                <div class="stack">
                    <section class="card">
                        <div class="badge {_badge_class('warn' if historical_mode else 'ok')}">{"Modo historico" if historical_mode else "Modo actual"}</div>
                        <h3>Contexto de simulacion</h3>
                        <p class="muted">Proveedor: {provider_name}</p>
                        <p class="muted">Snapshot: {snapshot_at or 'Tiempo real'}</p>
                        <p class="muted">Rango: {range_from_value or '-'} -> {range_to_value or '-'}</p>
                        <p class="muted">{market_notice or ('El lab usa cuotas actuales y mantiene ventana operativa de proximidad.' if not historical_mode else 'Se usa snapshot historico y se limita a mercados featured compatibles.')}</p>
                    </section>
                    <section class="card">
                        <div class="badge {_badge_class(status_badge)}">{escape(status_label)}</div>
                        <h3>Decision de publicacion</h3>
                        <p class="muted">El motor combina el runtime actual con el publication guard.</p>
                        <ul class="reasons-list">{reasons_html}</ul>
                    </section>
                    <section class="grid-2">
                        <div class="metric">
                            <span>Simulacion</span>
                            <strong>{"Historica" if historical_mode else "Actual"}</strong>
                            <small>Modo operativo del lab</small>
                        </div>
                        <div class="metric">
                            <span>Snapshots</span>
                            <strong>{snapshots_guardados}</strong>
                            <small>Filas guardadas en esta corrida</small>
                        </div>
                        <div class="metric">
                            <span>Recomendadas</span>
                            <strong>{int(forecast_summary.get("total_recomendadas", 0) or 0)}</strong>
                            <small>Picks finales del forecast</small>
                        </div>
                        <div class="metric">
                            <span>Descartadas</span>
                            <strong>{int(forecast_summary.get("total_descartadas_preview", 0) or 0)}</strong>
                            <small>Vista previa del descarte</small>
                        </div>
                        <div class="metric">
                            <span>Bloqueadas recomendadas</span>
                            <strong>{int(forecast_summary.get("total_bloqueadas_en_recomendadas", 0) or 0)}</strong>
                            <small>Guard sobre picks recomendadas</small>
                        </div>
                        <div class="metric">
                            <span>Bloqueadas descartadas</span>
                            <strong>{int(forecast_summary.get("total_bloqueadas_en_descartadas", 0) or 0)}</strong>
                            <small>Guard en la cola de descarte</small>
                        </div>
                    </section>
                    <section class="card">
                        <h3>Resumen Telegram</h3>
                        <div class="code-box">{summary_message}</div>
                    </section>
                    <section class="card">
                        <div class="badge badge-yellow">Aprendizaje</div>
                        <h3>Lo que el modelo va aprendiendo</h3>
                        <p class="muted">{escape(str(learning_panel.get("lectura") or "Todavia sin lectura disponible."))}</p>
                        <div class="summary">
                            <span>Training samples: {int(learning_panel.get("training_samples", 0) or 0)}</span>
                            <span>Evaluadas: {int(learning_panel.get("picks_evaluadas", 0) or 0)}</span>
                            <span>Snapshots: {int(learning_panel.get("snapshots_guardados", 0) or 0)}</span>
                            <span>CLV positivo: {float(learning_panel.get("clv_positivo_pct", 0) or 0):.2f}%</span>
                        </div>
                        <p class="muted"><strong>Deportes penalizados:</strong></p>
                        <ul class="reasons-list">{sport_penalty_lines}</ul>
                        <p class="muted"><strong>Mercados ajustados:</strong></p>
                        <ul class="reasons-list">{market_adjustment_lines}</ul>
                        <p class="muted"><strong>Casas penalizadas:</strong></p>
                        <ul class="reasons-list">{bookmaker_penalty_lines}</ul>
                    </section>
                </div>
            </div>
        </div>
        <script>
            (() => {{
                const form = document.getElementById("labRunForm");
                const overlay = document.getElementById("labLoadingOverlay");
                const message = document.getElementById("labLoadingMessage");
                const steps = Array.from(document.querySelectorAll("#labLoadingSteps li"));
                const simulationSelect = document.getElementById("simulation_mode");
                const snapshotField = document.getElementById("snapshotField");
                const rangeFromField = document.getElementById("rangeFromField");
                const rangeToField = document.getElementById("rangeToField");
                if (!form || !overlay || !message || steps.length === 0) {{
                    return;
                }}

                const liveMessages = [
                    "Obteniendo cuotas y horarios actualizados...",
                    "Comparando precios entre casas...",
                    "Priorizando mejores ligas y partidos activos...",
                    "Filtrando value, riesgo y picks publicables..."
                ];
                const historicalMessages = [
                    "Buscando snapshot historico en The Odds API...",
                    "Cargando cuotas featured del momento elegido...",
                    "Comparando casas sobre el snapshot historico...",
                    "Filtrando value y picks simulables en pasado..."
                ];

                let intervalId = null;
                const toggleSnapshotField = () => {{
                    if (!simulationSelect || !snapshotField || !rangeFromField || !rangeToField) {{
                        return;
                    }}
                    const historical = simulationSelect.value === "historical";
                    snapshotField.style.display = historical ? "block" : "none";
                    rangeFromField.style.display = historical ? "block" : "none";
                    rangeToField.style.display = historical ? "block" : "none";
                }};
                toggleSnapshotField();
                if (simulationSelect) {{
                    simulationSelect.addEventListener("change", toggleSnapshotField);
                }}

                const startOverlay = (messages) => {{
                    overlay.classList.add("visible");
                    overlay.setAttribute("aria-hidden", "false");
                    let index = 0;
                    message.textContent = messages[index];
                    steps.forEach((item, stepIndex) => item.classList.toggle("active", stepIndex === index));
                    intervalId = window.setInterval(() => {{
                        index = (index + 1) % messages.length;
                        message.textContent = messages[index];
                        steps.forEach((item, stepIndex) => item.classList.toggle("active", stepIndex === index));
                    }}, 1400);
                }};

                form.addEventListener("submit", () => {{
                    const selectedMessages = simulationSelect && simulationSelect.value === "historical"
                        ? historicalMessages
                        : liveMessages;
                    startOverlay(selectedMessages);
                }});

                window.addEventListener("pageshow", () => {{
                    if (intervalId) {{
                        window.clearInterval(intervalId);
                    }}
                    overlay.classList.remove("visible");
                    overlay.setAttribute("aria-hidden", "true");
                }});
            }})();
        </script>
    </body>
    </html>
    """
