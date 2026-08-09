import base64
import json
import os
from typing import Any

import requests


def _openai_api_key() -> str:
    return os.getenv("OPENAI_API_KEY", "").strip()


def _openai_model() -> str:
    return os.getenv("OPENAI_MODEL", "gpt-5").strip() or "gpt-5"


def _openai_enabled() -> bool:
    return os.getenv("OPENAI_ENABLED", "true").strip().lower() in {"1", "true", "yes", "si", "on"}


def _openai_base_url() -> str:
    return os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip() or "https://api.openai.com/v1"


def _openai_timeout_seconds() -> int:
    return max(5, int(os.getenv("OPENAI_TIMEOUT_SECONDS", "20")))


def _openai_telegram_picks_max() -> int:
    return max(0, int(os.getenv("OPENAI_TELEGRAM_PICKS_MAX", "3")))


def openai_available() -> bool:
    return _openai_enabled() and bool(_openai_api_key())


def _post_responses_api_content(system_prompt: str, user_content: list[dict[str, Any]]) -> str | None:
    if not openai_available():
        return None

    payload = {
        "model": _openai_model(),
        "input": [
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": system_prompt,
                    }
                ],
            },
            {
                "role": "user",
                "content": user_content,
            },
        ],
        "store": False,
        "text": {
            "verbosity": "low",
        },
    }

    response = requests.post(
        f"{_openai_base_url()}/responses",
        headers={
            "Authorization": f"Bearer {_openai_api_key()}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=_openai_timeout_seconds(),
    )
    response.raise_for_status()
    data = response.json()

    output_text = str(data.get("output_text") or "").strip()
    if output_text:
        return output_text

    output_items = data.get("output") or []
    fragments: list[str] = []
    for item in output_items:
        for content in item.get("content", []):
            text_value = content.get("text")
            if text_value:
                fragments.append(str(text_value))
    text = "\n".join(fragment.strip() for fragment in fragments if str(fragment).strip()).strip()
    return text or None


def _post_responses_api(system_prompt: str, user_prompt: str) -> str | None:
    return _post_responses_api_content(
        system_prompt,
        [
            {
                "type": "input_text",
                "text": user_prompt,
            }
        ],
    )


def generate_bet_slip_opinion_from_image(
    image_bytes: bytes,
    *,
    mime_type: str = "image/jpeg",
    user_notes: str | None = None,
) -> str | None:
    if not openai_available():
        return None

    encoded_image = base64.b64encode(image_bytes).decode("ascii")
    system_prompt = (
        "Eres un analista experto de apuestas deportivas. "
        "Debes analizar una captura de una apuesta o combinada creada por el usuario en una casa de apuestas. "
        "Extrae lo que puedas leer de la imagen y da una opinion profesional, responsable y accionable. "
        "No inventes datos que no se vean con claridad. "
        "Responde en espanol con exactamente estos apartados en texto plano, uno por linea: "
        "Apuesta detectada:, Valor:, Fiabilidad:, Riesgo principal:, Veredicto:, Stake sugerido:, Lectura:. "
        "En Veredicto di de forma directa si tu la jugarias o no. "
        "Si la captura no se entiende bien, dilo claramente."
    )
    notes = str(user_notes or "").strip()
    user_text = (
        "Analiza esta captura de apuesta subida a Telegram."
        + (f" Contexto del usuario: {notes}" if notes else "")
    )
    return _post_responses_api_content(
        system_prompt,
        [
            {
                "type": "input_text",
                "text": user_text,
            },
            {
                "type": "input_image",
                "image_url": f"data:{mime_type};base64,{encoded_image}",
            },
        ],
    )


def build_pick_action_advice(pick: dict[str, Any]) -> str:
    stake = float(pick.get("stake") or 0)
    value_pct = float(pick.get("valor_esperado") or 0) * 100
    quality = float(pick.get("quality_score") or 0)
    reliability = float(pick.get("reliability_score") or 0)
    historical_penalty_level = str(pick.get("historical_penalty_level") or "none").lower()
    market_guard_level = str(pick.get("market_guard_level") or "none").lower()
    market_guard_blocked = bool(pick.get("market_guard_blocked"))
    elite_tier = str(pick.get("elite_tier") or "").lower()
    confidence = str(pick.get("confianza") or "").lower()

    if stake <= 0 or market_guard_blocked:
        return "Yo no le entraria: el sistema la bloquea por riesgo operativo o falta de confirmacion."
    if historical_penalty_level == "alta" or market_guard_level == "block":
        return "Yo no le entraria: llega demasiado castigada por historico o por fragilidad del mercado."
    if market_guard_level == "high":
        return "Yo la dejaria pasar salvo que aceptes mucho riesgo: el mercado no la confirma lo suficiente."
    if elite_tier == "stakazo" and quality >= 88 and reliability >= 84 and value_pct >= 5:
        return "Yo si le meteria: esta si me gusta de verdad y la veo para stake disciplinado."
    if elite_tier in {"elite", "premium"} and quality >= 74 and reliability >= 70 and value_pct >= 2.6:
        return "Yo si le meteria, pero con stake contenido: tiene argumentos serios y no me parece humo."
    if confidence == "alta" and quality >= 70 and reliability >= 68 and value_pct >= 2.2:
        return "Yo si le entraria con gestion prudente: me convence, aunque sin volverme loco con el stake."
    if quality >= 66 and reliability >= 64 and value_pct >= 1.8 and historical_penalty_level not in {"media", "alta"}:
        return "Yo la podria tocar con cautela: no es de mis favoritas, pero tampoco la descartaria."
    return "Yo iria con cautela o la dejaria pasar: la veo demasiado justa para forzar entrada."


def _is_positive_advice(advice: str) -> bool:
    normalized = str(advice or "").lower()
    return "yo si le meteria" in normalized or "yo si le entraria" in normalized


def _promotion_candidate_score(pick: dict[str, Any]) -> tuple[float, float, float, float]:
    return (
        float(pick.get("stake") or 0),
        float(pick.get("quality_score") or 0),
        float(pick.get("reliability_score") or 0),
        float(pick.get("valor_esperado") or 0),
    )


def _can_promote_pick(pick: dict[str, Any]) -> bool:
    if float(pick.get("stake") or 0) <= 0:
        return False
    if bool(pick.get("market_guard_blocked")):
        return False
    if str(pick.get("market_guard_level") or "").lower() in {"block", "high"}:
        return False
    if str(pick.get("historical_penalty_level") or "").lower() == "alta":
        return False
    return (
        float(pick.get("quality_score") or 0) >= 68
        and float(pick.get("reliability_score") or 0) >= 66
        and (float(pick.get("valor_esperado") or 0) * 100) >= 2.0
    )


def _promote_batch_advice(picks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not picks:
        return picks

    target_positive = 2 if len(picks) >= 3 else 1
    positive_count = sum(1 for pick in picks if _is_positive_advice(pick.get("ai_advice_es")))
    if positive_count >= target_positive:
        return picks

    eligible_indexes = [
        index for index, pick in enumerate(picks)
        if _can_promote_pick(pick) and not _is_positive_advice(pick.get("ai_advice_es"))
    ]
    eligible_indexes.sort(key=lambda index: _promotion_candidate_score(picks[index]), reverse=True)

    promoted = list(picks)
    for index in eligible_indexes:
        if positive_count >= target_positive:
            break
        pick = promoted[index].copy()
        if float(pick.get("stake") or 0) >= 2 and str(pick.get("elite_tier") or "").lower() in {"stakazo", "elite"}:
            pick["ai_advice_es"] = "Yo si le meteria: de este envio es de las que mas me convence y si la tomaria."
        else:
            pick["ai_advice_es"] = "Yo si le entraria con stake prudente: la veo suficientemente buena para darle opcion."
        promoted[index] = pick
        positive_count += 1

    return promoted


def generate_pick_ai_narrative(pick: dict[str, Any]) -> str | None:
    deterministic_advice = build_pick_action_advice(pick)
    system_prompt = (
        "Eres un analista premium de apuestas deportivas. "
        "Debes explicar un pick con tono profesional, claro y sobrio. "
        "Incluye una recomendacion operativa directa en primera persona del tipo "
        "'yo si le entraria', 'yo solo le entraria con stake prudente' o 'yo no le entraria'. "
        "No prometas beneficios ni uses lenguaje irresponsable. "
        "Responde en espanol, en 2 frases cortas, maximo 260 caracteres."
    )
    user_prompt = json.dumps(
        {
            "partido": pick.get("partido_es") or pick.get("partido"),
            "liga": pick.get("league_label"),
            "seleccion": pick.get("equipo_es") or pick.get("equipo"),
            "mercado": pick.get("mercado"),
            "cuota": pick.get("cuota_apuesta") or pick.get("cuota_pinnacle"),
            "stake": pick.get("stake"),
            "value_pct": round(float(pick.get("valor_esperado") or 0) * 100, 2),
            "quality_score": pick.get("quality_score"),
            "reliability_score": pick.get("reliability_score"),
            "market_signal": pick.get("market_signal"),
            "market_guard_level": pick.get("market_guard_level"),
            "historical_penalty_level": pick.get("historical_penalty_level"),
            "motivo": pick.get("motivo_es") or pick.get("motivo"),
            "advice_base": deterministic_advice,
        },
        ensure_ascii=False,
    )
    try:
        return _post_responses_api(system_prompt, user_prompt)
    except Exception:
        return deterministic_advice


def generate_publication_ai_summary(
    picks: list[dict[str, Any]],
    *,
    sport_label: str | None,
    league_label: str | None,
    solo_stakazos: bool,
) -> str | None:
    if not picks:
        return None

    system_prompt = (
        "Eres un editor premium de alertas de apuestas deportivas. "
        "Redacta un resumen breve, responsable y util para Telegram. "
        "No prometas ganancias. "
        "Responde en espanol en un unico parrafo de maximo 280 caracteres."
    )
    compact_picks = [
        {
            "partido": pick.get("partido_es") or pick.get("partido"),
            "seleccion": pick.get("equipo_es") or pick.get("equipo"),
            "mercado": pick.get("mercado"),
            "cuota": pick.get("cuota_apuesta") or pick.get("cuota_pinnacle"),
            "value_pct": round(float(pick.get("valor_esperado") or 0) * 100, 2),
            "tier": pick.get("elite_tier"),
        }
        for pick in picks[:_openai_telegram_picks_max()]
    ]
    user_prompt = json.dumps(
        {
            "sport_label": sport_label,
            "league_label": league_label,
            "solo_stakazos": solo_stakazos,
            "picks": compact_picks,
        },
        ensure_ascii=False,
    )
    try:
        return _post_responses_api(system_prompt, user_prompt)
    except Exception:
        return None


def generate_audit_ai_brief(report: dict[str, Any]) -> str | None:
    system_prompt = (
        "Eres un risk manager premium de apuestas deportivas. "
        "Resume una auditoria diaria con tono ejecutivo, claro y sobrio. "
        "No prometas beneficios ni uses lenguaje irresponsable. "
        "Responde en espanol en 2 frases cortas, maximo 320 caracteres."
    )
    compact_report = {
        "date": report.get("date"),
        "status": report.get("status"),
        "status_detail": report.get("status_detail"),
        "recommended": ((report.get("picks") or {}).get("recommended")),
        "executed": ((report.get("picks") or {}).get("executed")),
        "closed": ((report.get("picks") or {}).get("closed")),
        "won": ((report.get("picks") or {}).get("won")),
        "lost": ((report.get("picks") or {}).get("lost")),
        "roi": ((report.get("metrics") or {}).get("roi")),
        "hitrate": ((report.get("metrics") or {}).get("hitrate")),
        "roi_delta": ((report.get("vs_historical") or {}).get("roi_delta")),
        "hitrate_delta": ((report.get("vs_historical") or {}).get("hitrate_delta")),
        "alerts": list(report.get("alerts") or [])[:3],
        "model_confidence": ((report.get("calibration") or {}).get("model_confidence")),
    }
    try:
        return _post_responses_api(system_prompt, json.dumps(compact_report, ensure_ascii=False))
    except Exception:
        return None


def enrich_picks_with_ai_narratives(picks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not picks:
        return picks

    enriched: list[dict[str, Any]] = []
    for index, pick in enumerate(picks):
        pick_copy = pick.copy()
        pick_copy["ai_advice_es"] = build_pick_action_advice(pick_copy)
        if openai_available() and index < _openai_telegram_picks_max():
            pick_copy["ai_narrative_es"] = generate_pick_ai_narrative(pick_copy)
        enriched.append(pick_copy)
    return _promote_batch_advice(enriched)
