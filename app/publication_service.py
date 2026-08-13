from collections import Counter
from datetime import datetime, timezone
from typing import Any, Callable

from app.runtime_settings import RuntimeSettings


def fingerprint_pick(pick: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return tuple(
        str(pick.get(field) or "").strip().lower()
        for field in ("event_id", "mercado", "tipo_resultado", "equipo", "casa")
    )


def diversified_telegram_picks(
    picks: list[dict[str, Any]],
    *,
    max_items: int,
) -> list[dict[str, Any]]:
    by_sport: dict[str, list[dict[str, Any]]] = {}
    sport_order: list[str] = []

    for pick in picks:
        sport = str(pick.get("sport_label") or "General").strip() or "General"
        if sport not in by_sport:
            by_sport[sport] = []
            sport_order.append(sport)
        by_sport[sport].append(pick)

    per_sport_limit = 2 if len(by_sport) >= 3 else 3
    selected: list[dict[str, Any]] = []
    selected_keys: set[tuple[str, str, str, str, str]] = set()
    used_per_sport: dict[str, int] = {sport: 0 for sport in by_sport}

    for sport in sport_order:
        if len(selected) >= max_items:
            break
        for pick in by_sport.get(sport, []):
            key = fingerprint_pick(pick)
            if key in selected_keys:
                continue
            selected.append(pick)
            selected_keys.add(key)
            used_per_sport[sport] += 1
            break

    for sport in sport_order:
        if len(selected) >= max_items:
            break
        for pick in by_sport.get(sport, [])[1:]:
            if len(selected) >= max_items or used_per_sport[sport] >= per_sport_limit:
                break
            key = fingerprint_pick(pick)
            if key in selected_keys:
                continue
            selected.append(pick)
            selected_keys.add(key)
            used_per_sport[sport] += 1

    if len(selected) < max_items:
        for pick in picks:
            if len(selected) >= max_items:
                break
            key = fingerprint_pick(pick)
            if key in selected_keys:
                continue
            selected.append(pick)
            selected_keys.add(key)

    return selected[:max_items]


def _promotable_discard_reason(reason: str) -> bool:
    normalized = str(reason or "").strip().lower()
    return any(
        token in normalized
        for token in (
            "filtro de valor y margen superado",
            "value positivo con exposicion controlada",
            "value pequeño aceptado con riesgo mínimo",
            "value pequeno aceptado con stake minimo",
            "value controlado",
            "value interesante",
            "value moderado",
            "value ligero",
            "cuota mejor que pinnacle, pero sin margen suficiente para apostar",
        )
    )


def _build_operational_fallback_picks(
    data: dict[str, Any],
    *,
    bankroll: float | None,
    max_items: int,
) -> list[dict[str, Any]]:
    fallback: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    bankroll_value = float(bankroll or 200.0)
    now = datetime.now(timezone.utc)
    fallback_source = list(data.get("descartadas_operativas") or data.get("descartadas") or [])

    for pick in fallback_source:
        if len(fallback) >= max_items:
            break
        if bool(pick.get("risk_guard_blocked")) or bool(pick.get("performance_guard_blocked")) or bool(pick.get("market_guard_blocked")):
            continue
        reason_text = str(pick.get("motivo_es") or pick.get("motivo") or "").strip()
        promotable_reason = _promotable_discard_reason(reason_text) or _promotable_discard_reason_loose(reason_text)
        if str(pick.get("recomendacion") or "").strip().lower() == "no apostar" and not promotable_reason:
            continue
        reliability = float(pick.get("reliability_score") or 0)
        value_pct = float(pick.get("valor_esperado") or 0) * 100
        margin = float(pick.get("margen_cuota") or 0)
        quality = float(pick.get("quality_score") or 0)
        cuota = float(pick.get("cuota_apuesta") or pick.get("cuota_pinnacle") or 0)
        commence = _parse_commence_time(pick.get("commence_time"))
        delta_hours = None
        if commence is not None:
            delta_hours = (commence - now).total_seconds() / 3600
            if delta_hours < -2 or delta_hours > 48:
                continue

        if reliability < 57:
            continue
        thin_margin_reason = "sin margen suficiente" in reason_text.lower()
        if thin_margin_reason:
            if cuota <= 0 or cuota < 1.5 or cuota > 2.35:
                continue
            if delta_hours is not None and delta_hours > 36:
                continue
            if reliability < 60:
                continue
            if quality < 45 and reliability < 66:
                continue
            if value_pct < 0.35 and margin < 1.003:
                continue
        if promotable_reason:
            if thin_margin_reason:
                pass
            elif value_pct < 1.2 and margin < 1.008:
                continue
        elif quality < 52 and reliability < 64:
            continue
        elif value_pct < 1.6 and margin < 1.012:
            continue

        if cuota and (cuota > 3.35 or cuota < 1.5):
            continue

        promoted = dict(pick)
        reason = reason_text
        promoted["stake"] = max(0.75, min(1.25, float(promoted.get("stake") or 0) or 0.75))
        promoted["stake_pct_bankroll"] = round(max(0.0125, min(0.02, promoted["stake"] / 100.0)), 4)
        promoted["importe_sugerido"] = round(
            max(2.5, min(4.0, bankroll_value * promoted["stake_pct_bankroll"])),
            2,
        )
        promoted["kelly_fraccional"] = max(0.001, float(promoted.get("kelly_fraccional") or 0) or 0.001)
        promoted["recomendacion"] = "Value controlado"
        promoted["motivo"] = (
            f"{reason} | Rescatada por fallback operativo del canal."
            if reason
            else "Rescatada por fallback operativo del canal."
        )
        key = fingerprint_pick(promoted)
        if key in seen:
            continue
        fallback.append(promoted)
        seen.add(key)

    return fallback


def _parse_commence_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _build_emergency_fallback_picks(
    data: dict[str, Any],
    *,
    bankroll: float | None,
    max_items: int,
) -> list[dict[str, Any]]:
    fallback: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    bankroll_value = float(bankroll or 200.0)
    now = datetime.now(timezone.utc)
    fallback_source = list(data.get("descartadas_operativas") or data.get("descartadas") or [])

    for pick in fallback_source:
        if len(fallback) >= max_items:
            break
        if bool(pick.get("risk_guard_blocked")) or bool(pick.get("performance_guard_blocked")) or bool(pick.get("market_guard_blocked")):
            continue

        reason_text = str(pick.get("motivo_es") or pick.get("motivo") or "").strip()
        if not (_promotable_discard_reason(reason_text) or _promotable_discard_reason_loose(reason_text)):
            continue

        cuota = float(pick.get("cuota_apuesta") or pick.get("cuota_pinnacle") or 0)
        if cuota <= 0 or cuota > 2.35:
            continue

        commence = _parse_commence_time(pick.get("commence_time"))
        if commence is not None:
            delta_hours = (commence - now).total_seconds() / 3600
            if delta_hours < -2 or delta_hours > 48:
                continue

        reliability = float(pick.get("reliability_score") or 0)
        quality = float(pick.get("quality_score") or 0)
        value_pct = float(pick.get("valor_esperado") or 0) * 100
        margin = float(pick.get("margen_cuota") or 0)
        if reliability < 45 and quality < 45 and value_pct < 0.8 and margin < 1.002:
            continue

        promoted = dict(pick)
        promoted["stake"] = max(0.5, min(0.75, float(promoted.get("stake") or 0) or 0.5))
        promoted["stake_pct_bankroll"] = round(max(0.01, min(0.015, promoted["stake"] / 100.0)), 4)
        promoted["importe_sugerido"] = round(
            max(2.0, min(3.0, bankroll_value * promoted["stake_pct_bankroll"])),
            2,
        )
        promoted["kelly_fraccional"] = max(0.0005, float(promoted.get("kelly_fraccional") or 0) or 0.0005)
        promoted["recomendacion"] = "Value frontera"
        promoted["motivo"] = (
            f"{reason_text} | Rescatada por fallback de emergencia para no dejar el canal vacio."
            if reason_text
            else "Rescatada por fallback de emergencia para no dejar el canal vacio."
        )
        key = fingerprint_pick(promoted)
        if key in seen:
            continue
        fallback.append(promoted)
        seen.add(key)

    return fallback


def _promotable_discard_reason_loose(reason: str) -> bool:
    normalized = str(reason or "").strip().lower()
    return any(
        token in normalized
        for token in (
            "filtro de valor y margen superado",
            "valor positivo",
            "valor pequeno",
            "valor pequeño",
            "value positivo",
            "value pequeno",
            "value peque",
            "valor controlado",
            "valor interesante",
            "valor moderado",
            "valor ligero",
            "cuota mejor que pinnacle",
        )
    )


def _build_last_resort_picks(
    data: dict[str, Any],
    *,
    bankroll: float | None,
    max_items: int,
) -> list[dict[str, Any]]:
    fallback: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    bankroll_value = float(bankroll or 200.0)
    now = datetime.now(timezone.utc)
    fallback_source = list(data.get("descartadas_operativas") or data.get("descartadas") or [])

    for pick in fallback_source:
        if len(fallback) >= max_items:
            break
        if bool(pick.get("risk_guard_blocked")) or bool(pick.get("performance_guard_blocked")) or bool(pick.get("market_guard_blocked")):
            continue

        reason_text = str(pick.get("motivo_es") or pick.get("motivo") or "").strip()
        if not (_promotable_discard_reason(reason_text) or _promotable_discard_reason_loose(reason_text)):
            continue
        if "sin margen suficiente" in reason_text.lower():
            continue

        cuota = float(pick.get("cuota_apuesta") or pick.get("cuota_pinnacle") or 0)
        if cuota <= 0 or cuota > 2.4:
            continue

        commence = _parse_commence_time(pick.get("commence_time"))
        if commence is not None:
            delta_hours = (commence - now).total_seconds() / 3600
            if delta_hours < -2 or delta_hours > 24:
                continue

        reliability = float(pick.get("reliability_score") or 0)
        quality = float(pick.get("quality_score") or 0)
        if quality < 55 and reliability < 68:
            continue

        promoted = dict(pick)
        promoted["stake"] = 0.5
        promoted["stake_pct_bankroll"] = 0.01
        promoted["importe_sugerido"] = round(max(2.0, bankroll_value * 0.01), 2)
        promoted["kelly_fraccional"] = max(0.0001, float(promoted.get("kelly_fraccional") or 0) or 0.0001)
        promoted["recomendacion"] = "Value frontera"
        promoted["motivo"] = (
            f"{reason_text} | Seleccion de ultima reserva para evitar informe vacio."
            if reason_text
            else "Seleccion de ultima reserva para evitar informe vacio."
        )
        key = fingerprint_pick(promoted)
        if key in seen:
            continue
        fallback.append(promoted)
        seen.add(key)

    return fallback


def select_picks_for_telegram(
    data: dict[str, Any],
    *,
    solo_stakazos: bool,
    max_items: int,
) -> list[dict[str, Any]]:
    if solo_stakazos:
        return list(data.get("picks_elite", []))[:max_items]

    base_picks = list(data.get("mejores_apuestas", []))
    sport_labels = {
        str(pick.get("sport_label") or "").strip().lower()
        for pick in base_picks
        if pick.get("sport_label")
    }

    if str(data.get("sport_label") or "").strip().lower() == "todo" or len(sport_labels) > 1:
        return diversified_telegram_picks(base_picks, max_items=max_items)

    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()

    for pick in base_picks:
        key = fingerprint_pick(pick)
        if key in seen:
            continue
        selected.append(pick)
        seen.add(key)
        if len(selected) >= max_items:
            break

    return selected


def _build_zero_picks_diagnostics(
    data: dict[str, Any],
    *,
    guard: dict[str, Any],
    shadow_mode: bool,
) -> dict[str, Any]:
    descartadas = list(data.get("descartadas", []))
    blocked_reasons = [
        str(reason).strip()
        for reason in (guard.get("reasons") or [])
        if str(reason).strip()
    ]
    discard_reasons = Counter()

    for pick in descartadas:
        reason = str(pick.get("motivo_es") or pick.get("motivo") or "").strip()
        if reason:
            discard_reasons[reason] += 1

    return {
        "analizadas": int(data.get("total_analizadas") or 0),
        "recomendadas": int(data.get("total_recomendadas") or 0),
        "descartadas_preview": len(descartadas),
        "shadow_mode": bool(shadow_mode),
        "guard_blocking": not guard.get("allow_live_publication", False),
        "guard_reasons": blocked_reasons[:3] if not guard.get("allow_live_publication", False) else [],
        "top_discard_reasons": [
            {"reason": reason, "count": count}
            for reason, count in discard_reasons.most_common(3)
        ],
        "snapshots_guardados": int(data.get("snapshots_guardados") or 0),
        "partidos_disponibles": len(list(data.get("partidos_disponibles") or [])),
        "coverage_notice": str(data.get("aviso_cobertura") or "").strip(),
        "base_criteria": str(data.get("criterio") or "").strip(),
        "blocked_summary": dict(data.get("blocked_summary") or {}),
    }


def publish_telegram_predictions(
    *,
    runtime_settings: RuntimeSettings,
    publication_guard: Callable[[], dict[str, Any]],
    pronosticos_fn: Callable[..., dict[str, Any]],
    save_unique_recommendations: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    read_raw_pick: Callable[[dict[str, Any]], dict[str, Any]],
    enrich_with_ai: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    build_ai_summary: Callable[..., str | None],
    ai_available: Callable[[], bool],
    format_summary: Callable[..., str],
    format_pick_message: Callable[[dict[str, Any]], str],
    telegram_keyboard_for_pick: Callable[[int], dict[str, Any]],
    send_message: Callable[..., dict[str, Any]],
    register_publication: Callable[..., dict[str, Any]],
    perfil_label: Callable[[str | None], str],
    modo_label: Callable[[str | None], str],
    perfiles_stake: set[str],
    modos_informe: set[str],
    bankroll: float | None,
    perfil: str,
    modo: str,
    mercados: str,
    partido: str,
    deporte: str,
    solo_stakazos: bool,
    token: str,
    chat_id: str,
    publication_type: str,
) -> dict[str, Any]:
    guard = publication_guard()
    fallback_max_items = 5 if not solo_stakazos else 3
    data = pronosticos_fn(
        bankroll=bankroll,
        perfil=perfil,
        modo=modo,
        mercados=mercados,
        partido=partido,
        deporte=deporte,
        solo_stakazos=solo_stakazos,
    )

    fallback_a_elite = False
    if solo_stakazos and not data.get("pronosticos") and int(data.get("total_elite") or 0) > 0:
        data = pronosticos_fn(
            bankroll=bankroll,
            perfil=perfil,
            modo=modo,
            mercados=mercados,
            partido=partido,
            deporte=deporte,
            solo_stakazos=False,
        )
        fallback_a_elite = True

    picks_publicables = list(data.get("pronosticos", []))
    operational_fallback_used = False
    emergency_fallback_used = False
    if not picks_publicables and not solo_stakazos:
        fallback_candidates = _build_operational_fallback_picks(
            data,
            bankroll=bankroll,
            max_items=fallback_max_items,
        )
        if fallback_candidates:
            picks_publicables = fallback_candidates
            operational_fallback_used = True
            data = {
                **data,
                "pronosticos": fallback_candidates,
            }
        else:
            emergency_candidates = _build_emergency_fallback_picks(
                data,
                bankroll=bankroll,
                max_items=min(3, fallback_max_items),
            )
            if emergency_candidates:
                picks_publicables = emergency_candidates
                emergency_fallback_used = True
                data = {
                    **data,
                    "pronosticos": emergency_candidates,
                }
            else:
                last_resort_candidates = _build_last_resort_picks(
                    data,
                    bankroll=bankroll,
                    max_items=min(2, fallback_max_items),
                )
                if last_resort_candidates:
                    picks_publicables = last_resort_candidates
                    emergency_fallback_used = True
                    data = {
                        **data,
                        "pronosticos": last_resort_candidates,
                    }

    zero_picks_diagnostics = _build_zero_picks_diagnostics(
        data,
        guard=guard,
        shadow_mode=runtime_settings.shadow_mode,
    )
    picks_guardados = save_unique_recommendations(picks_publicables)
    picks_por_fingerprint = {
        fingerprint_pick(item): item
        for item in picks_guardados
    }

    picks_publicables = []
    for pick in data.get("pronosticos", []):
        key = fingerprint_pick(pick)
        pick_guardado = picks_por_fingerprint.get(key)
        if pick_guardado is not None:
            pick_publicable = {**pick, **read_raw_pick(pick_guardado), **pick_guardado}
        else:
            pick_publicable = pick
        picks_publicables.append(pick_publicable)

    picks_publicables = enrich_with_ai(picks_publicables)
    ai_summary = build_ai_summary(
        picks_publicables,
        sport_label=data.get("deporte"),
        league_label=data.get("liga"),
        solo_stakazos=solo_stakazos,
    ) if ai_available() else None

    summary_text = format_summary(
        sport_label=data.get("deporte"),
        league_label=data.get("liga"),
        perfil_label=perfil_label(perfil if perfil in perfiles_stake else "moderado"),
        modo_label=modo_label(modo if modo in modos_informe else "comparador"),
        total_elite=int(data.get("total_elite", 0) or 0),
        total_stakazos=int(data.get("total_stakazos", 0) or 0),
        total_messages=len(picks_publicables),
        solo_stakazos=solo_stakazos,
        fallback_a_elite=fallback_a_elite,
        ai_summary=ai_summary,
    )

    messages = [summary_text] + [format_pick_message(pick) for pick in picks_publicables]
    sent_messages = []
    publication_items = []

    if runtime_settings.shadow_mode or not guard.get("allow_live_publication", False):
        for index, text in enumerate(messages):
            item = {
                "telegram_message_id": None,
                "message_kind": "summary" if index == 0 else "pick",
                "text": text,
                "pick_id": None,
            }
            if index > 0:
                pick = picks_publicables[index - 1]
                key = fingerprint_pick(pick)
                pick_guardado = picks_por_fingerprint.get(key)
                if pick_guardado is not None:
                    item["pick_id"] = pick_guardado.get("id")
            publication_items.append(item)
        publicacion = register_publication(
            publication_type=f"{publication_type}_shadow",
            payload={
                **data,
                "runtime_mode": runtime_settings.publication_mode,
                "publication_guard": guard,
            },
            items=publication_items,
        )
        return {
            "ok": True,
            "chat_id": chat_id,
            "mensajes_enviados": 0,
            "total_stakazos": data.get("total_stakazos", 0),
            "total_elite": data.get("total_elite", 0),
            "solo_stakazos": solo_stakazos,
            "fallback_a_elite": fallback_a_elite,
            "picks_guardados": len(picks_guardados),
            "publication_id": publicacion.get("id"),
            "runtime_mode": runtime_settings.publication_mode if runtime_settings.shadow_mode else "blocked",
            "publication_guard": guard,
            "shadow_messages": messages,
            "zero_picks_diagnostics": zero_picks_diagnostics,
            "operational_fallback_used": operational_fallback_used,
            "emergency_fallback_used": emergency_fallback_used,
        }

    for index, text in enumerate(messages):
        reply_markup = None
        if index > 0:
            pick = picks_publicables[index - 1]
            if pick.get("id"):
                reply_markup = telegram_keyboard_for_pick(int(pick["id"]))

        result = send_message(
            text,
            token=token,
            chat_id=chat_id,
            reply_markup=reply_markup,
        )
        sent_messages.append(result)

        message_id = ((result.get("result") or {}).get("message_id"))
        item = {
            "telegram_message_id": message_id,
            "message_kind": "summary" if index == 0 else "pick",
            "text": text,
            "pick_id": None,
        }

        if index > 0:
            pick = picks_publicables[index - 1]
            key = fingerprint_pick(pick)
            pick_guardado = picks_por_fingerprint.get(key)
            if pick_guardado is not None:
                item["pick_id"] = pick_guardado.get("id")

        publication_items.append(item)

    publicacion = register_publication(
        publication_type=publication_type,
        payload={**data, "runtime_mode": runtime_settings.publication_mode, "publication_guard": guard},
        items=publication_items,
    )
    return {
        "ok": True,
        "chat_id": chat_id,
        "mensajes_enviados": len(sent_messages),
        "total_stakazos": data.get("total_stakazos", 0),
        "total_elite": data.get("total_elite", 0),
        "solo_stakazos": solo_stakazos,
        "fallback_a_elite": fallback_a_elite,
        "picks_guardados": len(picks_guardados),
        "publication_id": publicacion.get("id"),
        "runtime_mode": runtime_settings.publication_mode,
        "publication_guard": guard,
        "zero_picks_diagnostics": zero_picks_diagnostics,
        "operational_fallback_used": operational_fallback_used,
        "emergency_fallback_used": emergency_fallback_used,
    }
