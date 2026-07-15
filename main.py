import base64
import json
import os
import re
import threading
from datetime import datetime, timezone
from html import escape
from typing import Any
from urllib.parse import parse_qs

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from betting_model import (
    analizar_comparador_casas,
    analizar_partidos,
    probabilidad_implicita,
    valor_esperado,
)
from elo import obtener_elos
from tracking import _raw_pick, actualizar_bankroll, actualizar_cuota_pick, actualizar_importe_pick, actualizar_resultado, estadisticas, guardar_recomendaciones, listar_picks
from tracking import guardar_apuesta_real, marcar_apuesta_real_pick, obtener_setting
from tracking import dashboard_data, guardar_snapshot_cuotas, aprendizaje, liquidar_picks_con_scores, obtener_bankroll, penalizaciones_historicas
from tracking import guardar_recomendaciones_unicas, inicializar_db, listar_publicaciones_telegram, registrar_publicacion_telegram
from translations import (
    apuesta_es,
    equipo_es,
    modo_es,
    motivo_es,
    partido_es,
    perfil_es,
    recomendacion_es,
    tipo_resultado_es,
)


load_dotenv()

app = FastAPI()
telegram_scheduler_stop = threading.Event()
telegram_scheduler_thread: threading.Thread | None = None
telegram_updates_thread: threading.Thread | None = None

ODDS_API_KEY = os.getenv("ODDS_API_KEY")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
SPORTSGAMEODDS_API_KEY = os.getenv("SPORTSGAMEODDS_API_KEY")
ODDS_PROVIDER = os.getenv("ODDS_PROVIDER", "the_odds_api").strip().lower()
REFERENCE_BOOKMAKER = os.getenv("REFERENCE_BOOKMAKER", "Pinnacle")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_AUTOPUBLISH_ENABLED = os.getenv("TELEGRAM_AUTOPUBLISH_ENABLED", "true").strip().lower() in {"1", "true", "yes", "si", "on"}
TELEGRAM_AUTOPUBLISH_INTERVAL_HOURS = max(1, int(os.getenv("TELEGRAM_AUTOPUBLISH_INTERVAL_HOURS", "6")))
TELEGRAM_AUTOPUBLISH_PERFIL = os.getenv("TELEGRAM_AUTOPUBLISH_PERFIL", "alto_riesgo").strip() or "alto_riesgo"
TELEGRAM_AUTOPUBLISH_MODO = os.getenv("TELEGRAM_AUTOPUBLISH_MODO", "pinnacle").strip() or "pinnacle"
TELEGRAM_AUTOPUBLISH_MERCADOS = os.getenv("TELEGRAM_AUTOPUBLISH_MERCADOS", "todo").strip() or "todo"
TELEGRAM_AUTOPUBLISH_PARTIDO = os.getenv("TELEGRAM_AUTOPUBLISH_PARTIDO", "todos").strip() or "todos"
TELEGRAM_AUTOPUBLISH_DEPORTE = os.getenv("TELEGRAM_AUTOPUBLISH_DEPORTE", "todo").strip() or "todo"
TELEGRAM_AUTOPUBLISH_SOLO_STAKAZOS = os.getenv("TELEGRAM_AUTOPUBLISH_SOLO_STAKAZOS", "true").strip().lower() in {"1", "true", "yes", "si", "on"}
SPORTSGAMEODDS_HOST = "https://api.sportsgameodds.com/v2"
SPORTSGAMEODDS_SPORT_ID = os.getenv("SPORTSGAMEODDS_SPORT_ID", "SOCCER")
SPORTSGAMEODDS_LEAGUE_ID = os.getenv("SPORTSGAMEODDS_LEAGUE_ID", "")
SPORTSGAMEODDS_BOOKMAKERS = os.getenv("SPORTSGAMEODDS_BOOKMAKERS", "")
SPORTSGAMEODDS_MAX_EVENTS = int(os.getenv("SPORTSGAMEODDS_MAX_EVENTS", "25"))
API_FOOTBALL_HOST = "https://v3.football.api-sports.io"
API_FOOTBALL_LEAGUE = os.getenv("API_FOOTBALL_LEAGUE", "1")
API_FOOTBALL_SEASON = os.getenv("API_FOOTBALL_SEASON", "2026")
API_FOOTBALL_MAX_PAGES = int(os.getenv("API_FOOTBALL_MAX_PAGES", "1"))
DEFAULT_SPORT = os.getenv("DEFAULT_SPORT", "worldcup").strip().lower()
PERFILES_STAKE = {"conservador", "moderado", "agresivo", "alto_riesgo"}
MODOS_INFORME = {"comparador", "pinnacle"}
FEATURED_MARKETS = {"h2h", "totals"}
ADDITIONAL_MARKETS = {
    "alternate_totals",
    "alternate_totals_cards",
    "alternate_totals_corners",
    "alternate_team_totals",
    "alternate_team_totals_corners",
    "alternate_spreads_cards",
    "alternate_spreads_corners",
    "btts",
    "corners_1x2",
    "double_chance",
    "team_totals",
    "totals_h1",
    "totals_h2",
}
MERCADOS_DISPONIBLES = FEATURED_MARKETS | ADDITIONAL_MARKETS
FILTROS_MERCADO = {
    "todo": [
        "h2h",
        "btts",
        "double_chance",
        "totals",
        "team_totals",
        "alternate_totals_corners",
        "alternate_team_totals_corners",
        "alternate_totals_cards",
    ],
    "resultado": ["h2h"],
    "h2h": ["h2h"],
    "ambos_anotan": ["btts"],
    "se_clasificara": [],
    "doble_oportunidad": ["double_chance"],
    "total_goles": ["totals", "alternate_totals"],
    "goles_intervalo": ["totals_h1", "totals_h2"],
    "corners": ["alternate_totals_corners", "alternate_team_totals_corners", "corners_1x2"],
    "tarjetas": ["alternate_totals_cards", "alternate_spreads_cards"],
    "ambos_tarjetas": [],
    "equipo_mayor_numero": ["corners_1x2"],
    "team_goals": ["team_totals"],
    "team_corners": ["alternate_team_totals_corners"],
    "team_fouls": [],
    "jugador_faltas_concedidas": [],
    "jugador_recibira_falta": [],
    "jugador_entradas": [],
    "jugador_remates_cabeza": [],
    "jugador_remates_fuera_area": [],
}
FILTROS_NO_SOPORTADOS = {
    "se_clasificara": "The Odds API no ofrece 'se clasificará' para este endpoint de partido.",
    "ambos_tarjetas": "The Odds API no ofrece 'ambos equipos recibirán tarjetas' como mercado directo.",
    "team_fouls": "The Odds API no ofrece faltas por equipo para fútbol; no se pedirán cuotas para ese filtro.",
    "jugador_faltas_concedidas": "The Odds API no ofrece faltas concedidas por jugador para fútbol.",
    "jugador_recibira_falta": "The Odds API no ofrece recibirá falta por jugador para fútbol.",
    "jugador_entradas": "The Odds API no ofrece entradas por jugador para fútbol.",
    "jugador_remates_cabeza": "The Odds API no ofrece remates de cabeza a puerta por jugador.",
    "jugador_remates_fuera_area": "The Odds API no ofrece remates a puerta fuera del área por jugador.",
}


SPORT_CATALOG = {
    "worldcup": {
        "sport_key": "soccer_fifa_world_cup",
        "sport_label": "Futbol",
        "league_key": "fifa_world_cup",
        "league_label": "FIFA World Cup",
        "supports_elo": True,
        "default_markets": "todo",
    },
    "futbol": {
        "sport_key": "soccer_spain_la_liga",
        "sport_label": "Futbol",
        "league_key": "la_liga",
        "league_label": "La Liga",
        "supports_elo": True,
        "default_markets": "todo",
    },
    "tenis": {
        "sport_key": "tennis_atp_wimbledon",
        "sport_label": "Tenis",
        "league_key": "atp_wimbledon",
        "league_label": "ATP Wimbledon",
        "supports_elo": False,
        "default_markets": "h2h",
    },
    "baloncesto": {
        "sport_key": "basketball_nba",
        "sport_label": "Baloncesto",
        "league_key": "nba",
        "league_label": "NBA",
        "supports_elo": False,
        "default_markets": "h2h,totals",
    },
}
SPORT_MARKET_CONFIG = {
    "worldcup": {
        "default_filter": "todo",
        "allowed_filters": [
            "todo",
            "resultado",
            "ambos_anotan",
            "doble_oportunidad",
            "total_goles",
            "goles_intervalo",
            "corners",
            "tarjetas",
            "equipo_mayor_numero",
            "team_goals",
            "team_corners",
        ],
    },
    "futbol": {
        "default_filter": "todo",
        "allowed_filters": [
            "todo",
            "resultado",
            "ambos_anotan",
            "doble_oportunidad",
            "total_goles",
            "goles_intervalo",
            "corners",
            "tarjetas",
            "equipo_mayor_numero",
            "team_goals",
            "team_corners",
        ],
    },
    "tenis": {
        "default_filter": "resultado",
        "allowed_filters": [
            "resultado",
            "h2h",
        ],
    },
    "baloncesto": {
        "default_filter": "total_goles",
        "allowed_filters": [
            "resultado",
            "h2h",
            "total_goles",
        ],
    },
}
SPORT_FILTER_LABELS = {
    "todo": "Todo",
    "resultado": "Resultado",
    "h2h": "Ganador",
    "ambos_anotan": "Ambos equipos anotaran",
    "doble_oportunidad": "Doble oportunidad",
    "total_goles": "Totales",
    "goles_intervalo": "Intervalos / parciales",
    "corners": "Corners",
    "tarjetas": "Tarjetas",
    "equipo_mayor_numero": "Equipo - mayor numero",
    "team_goals": "Totales por equipo",
    "team_corners": "Corners por equipo",
}
SPORT_ALIASES = {
    "soccer": "futbol",
    "football": "futbol",
    "futbol": "futbol",
    "worldcup": "worldcup",
    "mundial": "worldcup",
    "tenis": "tenis",
    "tennis": "tenis",
    "baloncesto": "baloncesto",
    "basket": "baloncesto",
    "basketball": "baloncesto",
    "nba": "baloncesto",
}

SPORT_PREFIX_LABELS = {
    "soccer": "Futbol",
    "tennis": "Tenis",
    "basketball": "Baloncesto",
    "baseball": "Beisbol",
    "americanfootball": "Football americano",
    "icehockey": "Hockey hielo",
    "cricket": "Cricket",
    "mma": "MMA",
    "rugbyleague": "Rugby league",
    "rugbyunion": "Rugby union",
}
TODO_LIMITS_BY_FAMILY = {
    "soccer": 4,
    "basketball": 3,
    "tennis": 2,
}
TODO_MAX_TOTAL_LEAGUES = 8
TODO_PRIORITY_KEYWORDS = {
    "soccer": {
        "world cup": 40,
        "champions league": 32,
        "premier league": 28,
        "la liga": 28,
        "serie a": 26,
        "bundesliga": 26,
        "ligue 1": 24,
        "euros": 22,
        "copa america": 22,
    },
    "basketball": {
        "nba": 30,
        "euroleague": 24,
        "acb": 20,
        "wnba": 18,
        "summer": 14,
    },
    "tennis": {
        "wimbledon": 30,
        "atp": 24,
        "wta": 22,
        "us open": 20,
        "roland garros": 20,
        "australian open": 20,
    },
}


def family_from_sport_key(sport_key: str) -> str:
    return (sport_key or "").split("_", 1)[0].lower()


def build_dynamic_context_from_sport_key(sport_key: str) -> dict:
    family = family_from_sport_key(sport_key)
    league_key = sport_key.split("_", 1)[1] if "_" in sport_key else sport_key
    sport_label = SPORT_PREFIX_LABELS.get(family, family.replace("_", " ").title() or "General")
    league_label = league_key.replace("_", " ").title()

    return {
        "catalog_key": sport_key,
        "sport_key": sport_key,
        "sport_label": sport_label,
        "league_key": league_key,
        "league_label": league_label,
        "supports_elo": family == "soccer",
        "default_markets": "todo" if family == "soccer" else "h2h,totals" if family == "basketball" else "h2h",
    }


def resolver_contexto_deporte(deporte: str | None) -> dict:
    valor = (deporte or DEFAULT_SPORT).strip().lower()
    clave = SPORT_ALIASES.get(valor, valor)

    if clave in SPORT_CATALOG:
        contexto = SPORT_CATALOG[clave].copy()
        contexto["catalog_key"] = clave
        return contexto

    if "_" in clave:
        return build_dynamic_context_from_sport_key(clave)

    contexto = SPORT_CATALOG["worldcup"].copy()
    contexto["catalog_key"] = "worldcup"
    return contexto


def prioridad_contexto_todo(contexto: dict) -> tuple:
    catalog_key = str(contexto.get("catalog_key") or "").strip().lower()
    sport_key = str(contexto.get("sport_key") or "").strip().lower()
    family = family_from_sport_key(sport_key)
    league_label = str(contexto.get("league_label") or contexto.get("title") or catalog_key).strip()
    league_text = league_label.lower()
    score = 0

    if catalog_key in SPORT_CATALOG:
        score += 60
    if catalog_key == "worldcup":
        score += 30
    if family == "soccer" and contexto.get("supports_elo"):
        score += 8

    for keyword, bonus in TODO_PRIORITY_KEYWORDS.get(family, {}).items():
        if keyword in league_text:
            score += bonus

    return (-score, contexto.get("sport_label") or "", league_label, catalog_key)


def deportes_agregados_para_todo(provider: str | None = None) -> list[str]:
    candidatos: list[dict] = []

    for item in opciones_deporte_disponibles(provider=provider):
        valor = str(item.get("value") or "").strip().lower()
        if not valor or valor == "todo":
            continue
        contexto = resolver_contexto_deporte(valor)
        family = family_from_sport_key(contexto.get("sport_key", ""))
        if family not in TODO_LIMITS_BY_FAMILY:
            continue
        candidatos.append(contexto)

    candidatos.sort(key=prioridad_contexto_todo)
    seleccionados: list[str] = []
    por_familia: dict[str, int] = {}

    for contexto in candidatos:
        if len(seleccionados) >= TODO_MAX_TOTAL_LEAGUES:
            break
        family = family_from_sport_key(contexto.get("sport_key", ""))
        limite = TODO_LIMITS_BY_FAMILY.get(family, 0)
        if por_familia.get(family, 0) >= limite:
            continue
        seleccionados.append(str(contexto.get("catalog_key") or "").strip().lower())
        por_familia[family] = por_familia.get(family, 0) + 1

    return seleccionados


def enriquecer_eventos_contexto(eventos: list[dict], contexto: dict) -> list[dict]:
    enriched = []

    for evento in eventos:
        copia = evento.copy()
        copia["sport_key"] = contexto["sport_key"]
        copia["sport_label"] = contexto["sport_label"]
        copia["league_key"] = contexto["league_key"]
        copia["league_label"] = contexto["league_label"]
        enriched.append(copia)

    return enriched


def config_mercados_deporte(deporte: str | None) -> dict:
    contexto = resolver_contexto_deporte(deporte)
    clave = contexto["catalog_key"]

    if clave in SPORT_MARKET_CONFIG:
        return SPORT_MARKET_CONFIG[clave]

    family = family_from_sport_key(contexto["sport_key"])

    if family == "soccer":
        return SPORT_MARKET_CONFIG["futbol"]
    if family == "tennis":
        return SPORT_MARKET_CONFIG["tenis"]
    if family == "basketball":
        return SPORT_MARKET_CONFIG["baloncesto"]

    return {
        "default_filter": "h2h",
        "allowed_filters": ["resultado", "h2h"],
    }


def etiqueta_filtro_mercado(filtro: str) -> str:
    return SPORT_FILTER_LABELS.get(filtro, filtro)


def telegram_text(value: Any) -> str:
    return escape(str(value if value is not None else ""))


def telegram_tier_label(tier: str | None) -> str:
    tier_normalized = str(tier or "elite").strip().lower()
    return {
        "stakazo": "STAKAZO",
        "elite": "ELITE",
        "premium": "PREMIUM",
        "seguimiento": "SEGUIMIENTO",
    }.get(tier_normalized, tier_normalized.upper() or "ELITE")


def resumir_penalizacion_historica(reasons: Any) -> str:
    if not reasons or not isinstance(reasons, list):
        return ""

    etiquetas = {
        "clv muy negativo": "CLV muy negativo",
        "rate flojo": "hit rate flojo",
        "hit rate flojo": "hit rate flojo",
        "muestra corta": "muestra corta",
        "varianza alta": "varianza alta",
    }
    resumen: list[str] = []

    for item in reasons[:3]:
        parts = [str(part).strip() for part in str(item).split(":") if str(part).strip()]
        detalle = parts[-1].lower() if parts else ""
        texto = etiquetas.get(detalle, detalle or str(item).strip())
        if texto and texto not in resumen:
            resumen.append(texto)

    return ", ".join(resumen)


def formatear_mensaje_telegram_pick(pick: dict) -> str:
    cuota = pick.get("cuota_apuesta") or pick.get("cuota_pinnacle")
    stake = pick.get("stake")
    importe = pick.get("importe_sugerido")
    valor = float(pick.get("valor_esperado") or 0) * 100
    partido = pick.get("partido_es") or pick.get("partido")
    titulo = titulo_card_apuesta(pick)
    seleccion = pick.get("equipo_es") or pick.get("equipo")
    liga = pick.get("league_label") or pick.get("sport_label") or "General"
    tier = telegram_tier_label(pick.get("elite_tier"))
    confianza = pick.get("confianza") or "Media"
    fiabilidad = pick.get("reliability_tier") or "media"
    fiabilidad_score = pick.get("reliability_score") or 0
    quality_score = pick.get("quality_score") or 0
    tipo_label, tipo_valor = etiqueta_tipo_apuesta(pick)
    condicion = que_tiene_que_pasar(pick)
    motivo = pick.get("motivo_es") or pick.get("motivo") or "Sin detalle adicional."
    ajuste_historico = pick.get("historical_penalty_summary_es") or resumir_penalizacion_historica(
        pick.get("historical_penalty_reasons")
    )

    return (
        f"<b>{telegram_text(tier)} | {telegram_text(liga)}</b>\n"
        f"<b>{telegram_text(titulo)}</b>\n"
        f"<b>Pick ID:</b> {telegram_text(pick.get('id') or '-')}\n"
        f"<b>Partido:</b> {telegram_text(partido)}\n"
        f"<b>Seleccion:</b> {telegram_text(seleccion)}\n"
        f"<b>{telegram_text(tipo_label)}:</b> {telegram_text(tipo_valor)}\n"
        f"<b>Cuota:</b> {telegram_text(cuota)}\n"
        f"<b>Stake:</b> {telegram_text(stake)}/5"
        + (f" | <b>Importe:</b> {telegram_text(importe)} EUR\n" if importe is not None else "\n")
        + f"<b>Value:</b> {valor:.1f}% | <b>Calidad:</b> {telegram_text(quality_score)}/100\n"
        f"<b>Confianza:</b> {telegram_text(confianza)} | <b>Fiabilidad:</b> {telegram_text(fiabilidad)} ({telegram_text(fiabilidad_score)}/100)\n"
        f"<b>Condicion:</b> {telegram_text(condicion)}\n"
        f"<b>Motivo:</b> {telegram_text(motivo)}"
        + (
            f"\n<b>Ajuste historico:</b> {telegram_text(ajuste_historico)}"
            if ajuste_historico
            else ""
        )
    )


def telegram_config() -> tuple[str, str]:
    token = os.getenv("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN).strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID).strip()

    if not token:
        raise HTTPException(status_code=500, detail="Falta TELEGRAM_BOT_TOKEN en el archivo .env")
    if not chat_id:
        raise HTTPException(status_code=500, detail="Falta TELEGRAM_CHAT_ID en el archivo .env")

    return token, chat_id


def telegram_api_request(
    method: str,
    payload: dict[str, Any] | None = None,
    token: str | None = None,
    timeout: int = 15,
    http_method: str = "post",
) -> dict:
    bot_token = (token or os.getenv("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN)).strip()

    if not bot_token:
        telegram_config()

    url = f"https://api.telegram.org/bot{bot_token}/{method}"

    try:
        if http_method.lower() == "get":
            response = requests.get(url, params=payload or {}, timeout=timeout)
        else:
            response = requests.post(url, json=payload or {}, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"No se pudo completar la llamada a Telegram ({method}): {exc}") from exc

    data = response.json()
    if not isinstance(data, dict) or not data.get("ok"):
        raise HTTPException(status_code=502, detail=f"Telegram devolvio una respuesta inesperada en {method}")

    return data


def enviar_mensaje_telegram(
    texto: str,
    token: str | None = None,
    chat_id: str | None = None,
    reply_markup: dict[str, Any] | None = None,
) -> dict:
    bot_token = (token or os.getenv("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN)).strip()
    target_chat_id = (chat_id or os.getenv("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID)).strip()

    if not bot_token or not target_chat_id:
        telegram_config()

    payload = {
        "chat_id": target_chat_id,
        "text": texto,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    return telegram_api_request("sendMessage", payload=payload, token=bot_token)


def answer_callback_query_telegram(callback_query_id: str, text: str, token: str | None = None) -> dict:
    return telegram_api_request(
        "answerCallbackQuery",
        payload={
            "callback_query_id": callback_query_id,
            "text": text[:180],
            "show_alert": False,
        },
        token=token,
    )


def telegram_keyboard_for_pick(pick_id: int) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "Apostada", "callback_data": f"pick:{pick_id}:bet"},
                {"text": "Ganada", "callback_data": f"pick:{pick_id}:win"},
            ],
            [
                {"text": "Perdida", "callback_data": f"pick:{pick_id}:loss"},
                {"text": "Nula", "callback_data": f"pick:{pick_id}:push"},
            ],
        ]
    }


def procesar_callback_pick(pick_id: int, action: str) -> str:
    if action == "bet":
        pick = marcar_apuesta_real_pick(pick_id)
        if pick is None:
            return "No encontre esa pick."
        return f"Pick {pick_id} marcada como apostada."

    if action in {"win", "loss", "push"}:
        pick = actualizar_resultado(pick_id, action)
        if pick is None:
            return "No encontre esa pick."
        estado = {"win": "ganada", "loss": "perdida", "push": "nula"}[action]
        return f"Pick {pick_id} marcada como {estado}."

    return "Accion no soportada."


def procesar_update_telegram(update: dict[str, Any], token: str) -> None:
    callback = update.get("callback_query") or {}
    callback_id = callback.get("id")
    data = str(callback.get("data") or "").strip()

    if not callback_id or not data.startswith("pick:"):
        return

    parts = data.split(":")
    if len(parts) != 3:
        answer_callback_query_telegram(callback_id, "Accion no valida.", token=token)
        return

    try:
        pick_id = int(parts[1])
    except ValueError:
        answer_callback_query_telegram(callback_id, "Pick no valida.", token=token)
        return

    action = parts[2].strip().lower()
    try:
        mensaje = procesar_callback_pick(pick_id, action)
    except ValueError as exc:
        mensaje = str(exc)
    except HTTPException as exc:
        mensaje = str(exc.detail)

    answer_callback_query_telegram(callback_id, mensaje, token=token)


def telegram_updates_loop() -> None:
    token, _ = telegram_config()
    offset_raw = obtener_setting("telegram_last_update_id", "0")

    try:
        offset = int(offset_raw or 0)
    except ValueError:
        offset = 0

    while not telegram_scheduler_stop.is_set():
        try:
            data = telegram_api_request(
                "getUpdates",
                payload={
                    "timeout": 20,
                    "offset": offset + 1,
                    "allowed_updates": ["callback_query"],
                },
                token=token,
                timeout=25,
                http_method="get",
            )
            for update in data.get("result", []):
                update_id = int(update.get("update_id") or 0)
                procesar_update_telegram(update, token)
                if update_id > offset:
                    offset = update_id
                    guardar_setting("telegram_last_update_id", str(offset))
        except Exception:
            if telegram_scheduler_stop.wait(5):
                break


def publicar_pronosticos_telegram(
    bankroll: float | None = None,
    perfil: str = "moderado",
    modo: str = "comparador",
    mercados: str = "todo",
    partido: str = "todos",
    deporte: str = DEFAULT_SPORT,
    solo_stakazos: bool = False,
    publication_type: str = "manual",
) -> dict:
    token, chat_id = telegram_config()
    data = pronosticos(
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
        data = pronosticos(
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
    picks_guardados = guardar_recomendaciones_unicas(picks_publicables)
    picks_por_fingerprint = {
        tuple(
            str(item.get(key) or "").strip().lower()
            for key in ("event_id", "mercado", "tipo_resultado", "equipo", "casa")
        ): item
        for item in picks_guardados
    }

    picks_publicables = []
    for pick in data.get("pronosticos", []):
        key = tuple(
            str(pick.get(field) or "").strip().lower()
            for field in ("event_id", "mercado", "tipo_resultado", "equipo", "casa")
        )
        pick_guardado = picks_por_fingerprint.get(key)
        if pick_guardado is not None:
            pick_publicable = {**pick, **_raw_pick(pick_guardado), **pick_guardado}
        else:
            pick_publicable = pick
        picks_publicables.append(pick_publicable)

    resumen = data.get("resumen_telegram", "Pronosticos")
    if fallback_a_elite:
        resumen += " | Sin stakazos ahora: se publican picks elite."

    mensajes = [resumen] + [formatear_mensaje_telegram_pick(pick) for pick in picks_publicables]
    enviados = []
    publication_items = []

    for index, texto in enumerate(mensajes):
        reply_markup = None
        if index > 0:
            pick = picks_publicables[index - 1]
            if pick.get("id"):
                reply_markup = telegram_keyboard_for_pick(int(pick["id"]))

        resultado = enviar_mensaje_telegram(
            texto,
            token=token,
            chat_id=chat_id,
            reply_markup=reply_markup,
        )
        enviados.append(resultado)

        message_id = ((resultado.get("result") or {}).get("message_id"))
        item = {
            "telegram_message_id": message_id,
            "message_kind": "summary" if index == 0 else "pick",
            "text": texto,
            "pick_id": None,
        }

        if index > 0:
            pick = picks_publicables[index - 1]
            key = tuple(
                str(pick.get(field) or "").strip().lower()
                for field in ("event_id", "mercado", "tipo_resultado", "equipo", "casa")
            )
            pick_guardado = picks_por_fingerprint.get(key)
            if pick_guardado is not None:
                item["pick_id"] = pick_guardado.get("id")

        publication_items.append(item)

    publicacion = registrar_publicacion_telegram(
        publication_type=publication_type,
        payload=data,
        items=publication_items,
    )

    return {
        "ok": True,
        "chat_id": chat_id,
        "mensajes_enviados": len(enviados),
        "total_stakazos": data.get("total_stakazos", 0),
        "total_elite": data.get("total_elite", 0),
        "solo_stakazos": solo_stakazos,
        "fallback_a_elite": fallback_a_elite,
        "picks_guardados": len(picks_guardados),
        "publication_id": publicacion.get("id"),
    }


def auto_publicar_telegram_once() -> dict | None:
    if not TELEGRAM_AUTOPUBLISH_ENABLED:
        return None

    if not (os.getenv("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN).strip() and os.getenv("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID).strip()):
        return None

    return publicar_pronosticos_telegram(
        perfil=TELEGRAM_AUTOPUBLISH_PERFIL,
        modo=TELEGRAM_AUTOPUBLISH_MODO,
        mercados=TELEGRAM_AUTOPUBLISH_MERCADOS,
        partido=TELEGRAM_AUTOPUBLISH_PARTIDO,
        deporte=TELEGRAM_AUTOPUBLISH_DEPORTE,
        solo_stakazos=TELEGRAM_AUTOPUBLISH_SOLO_STAKAZOS,
        publication_type="auto",
    )


def telegram_scheduler_loop() -> None:
    while not telegram_scheduler_stop.is_set():
        try:
            auto_publicar_telegram_once()
        except Exception:
            pass

        if telegram_scheduler_stop.wait(TELEGRAM_AUTOPUBLISH_INTERVAL_HOURS * 3600):
            break


@app.on_event("startup")
def startup_event() -> None:
    global telegram_scheduler_thread, telegram_updates_thread

    inicializar_db()

    token = os.getenv("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN).strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID).strip()

    if token and chat_id and not (telegram_updates_thread and telegram_updates_thread.is_alive()):
        telegram_updates_thread = threading.Thread(
            target=telegram_updates_loop,
            name="telegram-updates",
            daemon=True,
        )
        telegram_updates_thread.start()

    if not TELEGRAM_AUTOPUBLISH_ENABLED:
        return

    if telegram_scheduler_thread and telegram_scheduler_thread.is_alive():
        return

    telegram_scheduler_stop.clear()
    telegram_scheduler_thread = threading.Thread(
        target=telegram_scheduler_loop,
        name="telegram-autopublish",
        daemon=True,
    )
    telegram_scheduler_thread.start()


@app.on_event("shutdown")
def shutdown_event() -> None:
    telegram_scheduler_stop.set()


def prioridad_tier_elite(tier: str | None) -> int:
    return {
        "stakazo": 3,
        "elite": 2,
        "premium": 1,
        "seguimiento": 0,
        "descartable": 0,
    }.get(str(tier or "").lower(), 0)


def prioridad_pick(apuesta: dict) -> tuple:
    return (
        prioridad_tier_elite(apuesta.get("elite_tier")),
        int(apuesta.get("quality_score") or 0),
        int(apuesta.get("reliability_score") or 0),
        int(apuesta.get("puntuacion_confianza") or 0),
        float(apuesta.get("valor_esperado") or 0),
        float(apuesta.get("margen_cuota") or 0),
    )


def parse_commence_time(value: str | None) -> datetime | None:
    texto = str(value or "").strip()
    if not texto:
        return None

    try:
        if texto.endswith("Z"):
            texto = f"{texto[:-1]}+00:00"
        dt = datetime.fromisoformat(texto)
    except ValueError:
        return None

    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


def proximity_score_for_pick(apuesta: dict) -> int:
    commence = parse_commence_time(apuesta.get("commence_time"))
    if commence is None:
        return -9999

    delta_hours = (commence - datetime.now(timezone.utc)).total_seconds() / 3600

    if delta_hours < -3:
        return -500
    if delta_hours <= 2:
        return 40
    if delta_hours <= 6:
        return 32
    if delta_hours <= 12:
        return 24
    if delta_hours <= 24:
        return 18
    if delta_hours <= 48:
        return 10
    return 0


def prioridad_pick_todo(apuesta: dict) -> tuple:
    return (
        prioridad_tier_elite(apuesta.get("elite_tier")),
        int(apuesta.get("reliability_score") or 0),
        int(apuesta.get("quality_score") or 0),
        proximity_score_for_pick(apuesta),
        int(apuesta.get("puntuacion_confianza") or 0),
        float(apuesta.get("valor_esperado") or 0),
        float(apuesta.get("margen_cuota") or 0),
    )


def limitar_picks_todo(recomendadas: list[dict], max_total: int = 6) -> list[dict]:
    seleccionadas: list[dict] = []
    por_liga: dict[str, int] = {}
    por_deporte: dict[str, int] = {}

    for apuesta in sorted(recomendadas, key=prioridad_pick_todo, reverse=True):
        if len(seleccionadas) >= max_total:
            break

        liga = str(apuesta.get("league_label") or "General")
        deporte = str(apuesta.get("sport_label") or "General")

        if por_liga.get(liga, 0) >= 2:
            continue
        if por_deporte.get(deporte, 0) >= 3:
            continue

        seleccionadas.append(apuesta)
        por_liga[liga] = por_liga.get(liga, 0) + 1
        por_deporte[deporte] = por_deporte.get(deporte, 0) + 1

    return seleccionadas


def aplicar_penalizacion_historica(apuesta: dict, penalizaciones: dict[str, dict[str, Any]]) -> dict:
    apuesta = apuesta.copy()
    league_label = str(apuesta.get("league_label") or "")
    elite_tier = str(apuesta.get("elite_tier") or "seguimiento")
    penalty_items = []

    liga_penalty = penalizaciones.get("ligas", {}).get(league_label)
    if liga_penalty:
        penalty_items.append(("liga", league_label, liga_penalty))

    tier_penalty = penalizaciones.get("tiers", {}).get(elite_tier)
    if tier_penalty:
        penalty_items.append(("tier", elite_tier, tier_penalty))

    if not penalty_items:
        apuesta["historical_penalty_score"] = 0
        apuesta["historical_penalty_level"] = "none"
        apuesta["historical_penalty_reasons"] = []
        return apuesta

    total_penalty = sum(item[2]["penalty_score"] for item in penalty_items)
    reasons = []

    for scope, name, item in penalty_items:
        for reason in item.get("reasons", []):
            reasons.append(f"{scope}:{name}:{reason}")

    apuesta["quality_score"] = max(0, int(apuesta.get("quality_score") or 0) - total_penalty)
    apuesta["reliability_score"] = max(0, int(apuesta.get("reliability_score") or 0) - total_penalty)
    apuesta["historical_penalty_score"] = total_penalty
    apuesta["historical_penalty_level"] = "alta" if total_penalty >= 18 else "media" if total_penalty >= 12 else "moderada"
    apuesta["historical_penalty_reasons"] = reasons

    if total_penalty >= 18:
        apuesta["elite_pick"] = False
        apuesta["elite_tier"] = "seguimiento"
    elif total_penalty >= 10 and str(apuesta.get("elite_tier") or "").lower() == "stakazo":
        apuesta["elite_tier"] = "elite"

    if reasons:
        apuesta["historical_penalty_summary_es"] = resumir_penalizacion_historica(reasons)

    return apuesta


class ResultadoPick(BaseModel):
    resultado: str
    closing_odds: float | None = None


class RegistroApuesta(BaseModel):
    recomendacion: dict
    importe_real: float


class ImportePick(BaseModel):
    importe_real: float


class CuotaPick(BaseModel):
    cuota_real: float


class BankrollPayload(BaseModel):
    bankroll: float


def odds_api_error_detail(exc: requests.RequestException) -> str:
    response = getattr(exc, "response", None)
    status_code = response.status_code if response is not None else None

    if status_code == 401:
        return (
            "The Odds API rechaza la API key. Revisa ODDS_API_KEY en .env, "
            "genera una clave nueva si esta se ha expuesto y reinicia el servidor."
        )

    if status_code == 403:
        return "The Odds API no autoriza este recurso con tu plan actual."

    if status_code == 429:
        return "The Odds API indica limite de peticiones alcanzado. Espera o revisa tu cuota del plan."

    if status_code == 422:
        return "The Odds API no acepta algun parametro enviado, normalmente sport, region o mercado."

    return "No se pudieron obtener datos desde The Odds API. Revisa conexion, parametros y estado de la cuenta."


def api_football_error_detail(exc: requests.RequestException | None = None, errors: object | None = None) -> str:
    if errors:
        return f"API-Football devolvio errores: {errors}"

    response = getattr(exc, "response", None) if exc else None
    status_code = response.status_code if response is not None else None

    if status_code in {401, 403}:
        return "API-Football rechaza la API key o el plan no permite este endpoint. Revisa API_FOOTBALL_KEY en .env."

    if status_code == 429:
        return "API-Football indica limite de peticiones alcanzado. Espera o revisa tu cuota diaria."

    return "No se pudieron obtener datos desde API-Football. Revisa conexion, parametros y estado de la cuenta."


def api_football_get(path: str, params: dict | None = None) -> dict:
    if not API_FOOTBALL_KEY:
        raise HTTPException(
            status_code=500,
            detail="Falta API_FOOTBALL_KEY en el archivo .env",
        )

    try:
        response = requests.get(
            f"{API_FOOTBALL_HOST}{path}",
            headers={"x-apisports-key": API_FOOTBALL_KEY},
            params=params or {},
            timeout=20,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=api_football_error_detail(exc)) from exc

    data = response.json()
    errors = data.get("errors")

    if errors:
        if isinstance(errors, list) and not errors:
            return data
        if isinstance(errors, dict) and not errors:
            return data
        raise HTTPException(status_code=502, detail=api_football_error_detail(errors=errors))

    return data


def sportsgameodds_error_detail(exc: requests.RequestException | None = None, data: dict | None = None) -> str:
    if data and data.get("error"):
        return f"SportsGameOdds devolvio error: {data['error']}"

    response = getattr(exc, "response", None) if exc else None
    status_code = response.status_code if response is not None else None

    if status_code in {401, 403}:
        return "SportsGameOdds rechaza la API key o el plan no permite este endpoint. Revisa SPORTSGAMEODDS_API_KEY."

    if status_code == 429:
        return "SportsGameOdds indica limite de peticiones alcanzado. Espera o revisa tu plan."

    return "No se pudieron obtener datos desde SportsGameOdds. Revisa conexion, parametros y estado de la cuenta."


def sportsgameodds_get(path: str, params: dict | None = None) -> dict:
    if not SPORTSGAMEODDS_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="Falta SPORTSGAMEODDS_API_KEY en el archivo .env",
        )

    request_params = params.copy() if params else {}
    request_params.setdefault("apiKey", SPORTSGAMEODDS_API_KEY)

    try:
        response = requests.get(
            f"{SPORTSGAMEODDS_HOST}{path}",
            params=request_params,
            timeout=20,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=sportsgameodds_error_detail(exc)) from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=sportsgameodds_error_detail()) from exc

    if response.status_code >= 400 or data.get("success") is False:
        raise HTTPException(status_code=502, detail=sportsgameodds_error_detail(data=data))

    return data


def the_odds_api_sports() -> list[dict]:
    if not ODDS_API_KEY:
        raise HTTPException(status_code=500, detail="Falta ODDS_API_KEY en el archivo .env")

    try:
        response = requests.get(
            "https://api.the-odds-api.com/v4/sports",
            params={"apiKey": ODDS_API_KEY},
            timeout=20,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=odds_api_error_detail(exc)) from exc

    data = response.json()

    if not isinstance(data, list):
        raise HTTPException(status_code=502, detail="Respuesta inesperada de The Odds API al listar deportes")

    return data


def discover_available_catalog(provider: str | None = None) -> dict:
    proveedor = (provider or ODDS_PROVIDER).strip().lower()

    if proveedor in {"the_odds_api", "the-odds-api", "odds_api"}:
        deportes = []

        for item in the_odds_api_sports():
            sport_key = str(item.get("key") or "")
            if not sport_key:
                continue

            contexto = build_dynamic_context_from_sport_key(sport_key)
            contexto["title"] = item.get("title") or contexto["league_label"]
            contexto["active"] = item.get("active")
            contexto["has_outrights"] = item.get("has_outrights")
            deportes.append(contexto)

        return {
            "provider": "the_odds_api",
            "total": len(deportes),
            "sports": deportes,
        }

    if proveedor in {"api_football", "api-football", "apifootball"}:
        data = api_football_get("/leagues", {"current": "true"})
        deportes = []

        for item in data.get("response", []):
            league = item.get("league") or {}
            country = item.get("country") or {}
            sport_key = f"soccer_{str(country.get('name') or 'world').strip().lower().replace(' ', '_')}_{str(league.get('name') or 'league').strip().lower().replace(' ', '_')}"
            contexto = build_dynamic_context_from_sport_key(sport_key)
            contexto["title"] = league.get("name") or contexto["league_label"]
            contexto["country"] = country.get("name")
            contexto["league_id"] = league.get("id")
            deportes.append(contexto)

        return {
            "provider": "api_football",
            "total": len(deportes),
            "sports": deportes,
        }

    if proveedor in {"sportsgameodds", "sports_game_odds", "sgo"}:
        data = sportsgameodds_get("/leagues", {"sportID": SPORTSGAMEODDS_SPORT_ID, "limit": "500"})
        deportes = []

        for item in data.get("data", []):
            league_id = item.get("leagueID") or item.get("id") or item.get("name")
            league_name = item.get("name") or item.get("leagueID") or "League"
            sport_key = f"{str(SPORTSGAMEODDS_SPORT_ID).strip().lower()}_{str(league_id).strip().lower()}"
            contexto = build_dynamic_context_from_sport_key(sport_key)
            contexto["title"] = league_name
            contexto["league_id"] = league_id
            deportes.append(contexto)

        return {
            "provider": "sportsgameodds",
            "total": len(deportes),
            "sports": deportes,
        }

    raise HTTPException(status_code=400, detail=f"Proveedor no soportado para discovery: {proveedor}")


def label_deporte_option(contexto: dict) -> str:
    sport_label = contexto.get("sport_label") or "General"
    league_label = contexto.get("league_label") or contexto.get("title") or contexto.get("sport_key") or "General"
    return f"{sport_label} - {league_label}"


def catalogo_deportes_fallback() -> list[dict]:
    return [
        {
            **info,
            "catalog_key": nombre,
        }
        for nombre, info in SPORT_CATALOG.items()
    ]


def opciones_deporte_disponibles(provider: str | None = None, selected: str | None = None) -> list[dict]:
    seleccion_actual = resolver_contexto_deporte(selected)
    opciones: list[dict] = []
    vistos: set[str] = set()

    def agregar(contexto: dict) -> None:
        valor = str(contexto.get("catalog_key") or contexto.get("sport_key") or "").strip().lower()
        if not valor or valor in vistos:
            return
        vistos.add(valor)
        opciones.append(
            {
                "value": valor,
                "label": label_deporte_option(contexto),
            }
        )

    try:
        catalogo = discover_available_catalog(provider=provider)
        deportes = [
            item
            for item in catalogo.get("sports", [])
            if family_from_sport_key(item.get("sport_key", "")) in {"soccer", "tennis", "basketball"}
            and item.get("active", True) is not False
        ]
        deportes.sort(
            key=lambda item: (
                item.get("sport_label") or "",
                item.get("league_label") or item.get("title") or "",
            )
        )
    except Exception:
        deportes = catalogo_deportes_fallback()

    agregar(seleccion_actual)
    for contexto in catalogo_deportes_fallback():
        agregar(contexto)

    for contexto in deportes[:500]:
        agregar(contexto)

    if not opciones:
        for contexto in catalogo_deportes_fallback():
            agregar(contexto)

    return [{"value": "todo", "label": "Todo - deportes base"}] + opciones


def cuota_sportsgameodds_decimal(value: object) -> float | None:
    if value is None:
        return None

    try:
        odd = float(str(value).replace("+", ""))
    except (TypeError, ValueError):
        return None

    if odd <= -100:
        return round(1 + (100 / abs(odd)), 3)
    if odd >= 100:
        return round(1 + (odd / 100), 3)
    if odd > 1:
        return round(odd, 3)

    return None


BOOKMAKER_LABELS = {
    "pinnacle": "Pinnacle",
    "bet365": "Bet365",
    "betfair": "Betfair",
    "betfairexchange": "Betfair Exchange",
    "betfairsportsbook": "Betfair Sportsbook",
    "unibet": "Unibet",
    "draftkings": "DraftKings",
    "fanduel": "FanDuel",
    "betmgm": "BetMGM",
    "williamhill": "William Hill",
}


def bookmaker_label(bookmaker_id: str) -> str:
    return BOOKMAKER_LABELS.get(bookmaker_id.lower(), bookmaker_id)


def event_team_name_sgo(event: dict, side: str) -> str | None:
    teams = event.get("teams") or {}
    team = teams.get(side) or {}
    names = team.get("names") or {}
    return names.get("long") or names.get("medium") or names.get("short") or team.get("name")


def event_start_sgo(event: dict) -> str | None:
    info = event.get("info") or {}
    status = event.get("status") or {}

    for key in ("startTime", "startsAt", "scheduledStart", "startDate", "date"):
        if event.get(key):
            return event.get(key)
        if info.get(key):
            return info.get(key)
        if status.get(key):
            return status.get(key)

    return None


def mercado_sportsgameodds(odd_id: str, odd: dict, home: str, away: str) -> tuple[str, str, float | None, str | None] | None:
    bet_type = str(odd.get("betTypeID") or "").lower()
    side = str(odd.get("sideID") or "").lower()
    stat = str(odd.get("statID") or "").lower()
    entity = str(odd.get("statEntityID") or "").lower()
    period = str(odd.get("periodID") or "").lower()
    odd_id_norm = odd_id.lower()

    if period and period not in {"game", "reg"}:
        return None

    if bet_type == "ml":
        if side == "home":
            return "h2h", home, None, None
        if side == "away":
            return "h2h", away, None, None
        if side == "draw":
            return "h2h", "Draw", None, None

    if bet_type == "ou" and side in {"over", "under"}:
        name = side.title()

        if stat in {"corners", "corner_kicks"} or "corner" in odd_id_norm:
            market = "alternate_team_totals_corners" if entity in {"home", "away"} else "alternate_totals_corners"
        elif stat in {"cards", "yellow_cards", "bookings"} or "card" in odd_id_norm:
            market = "alternate_totals_cards"
        elif entity in {"home", "away"}:
            market = "team_totals"
        else:
            market = "totals"

        description = home if entity == "home" else away if entity == "away" else None
        return market, name, None, description

    if side in {"yes", "no"} and ("btts" in odd_id_norm or "both" in odd_id_norm or "both_teams" in odd_id_norm):
        return "btts", side.title(), None, None

    if bet_type in {"dc", "double_chance"}:
        mapas = {
            "home_draw": f"{home} or Draw",
            "homeordraw": f"{home} or Draw",
            "1x": f"{home} or Draw",
            "home_away": f"{home} or {away}",
            "homeoraway": f"{home} or {away}",
            "12": f"{home} or {away}",
            "draw_away": f"Draw or {away}",
            "draworaway": f"Draw or {away}",
            "x2": f"Draw or {away}",
        }
        outcome = mapas.get(side.replace("-", "_"))

        if outcome:
            return "double_chance", outcome, None, None

    return None


def adaptar_sportsgameodds_events(events: list[dict], mercados_lista: list[str]) -> list[dict]:
    mercados_permitidos = set(mercados_lista)
    eventos = []

    for event in events:
        home = event_team_name_sgo(event, "home")
        away = event_team_name_sgo(event, "away")
        event_id = event.get("eventID") or event.get("id")

        if not home or not away or not event_id:
            continue

        markets_by_bookmaker: dict[str, dict[tuple, list[dict]]] = {}

        for odd_id, odd in (event.get("odds") or {}).items():
            mercado_info = mercado_sportsgameodds(odd_id, odd, home, away)

            if not mercado_info:
                continue

            market_key, outcome_name, _, description = mercado_info

            if market_key not in mercados_permitidos:
                continue

            for bookmaker_id, bookmaker_odd in (odd.get("byBookmaker") or {}).items():
                if bookmaker_odd.get("available") is False:
                    continue

                cuota = cuota_sportsgameodds_decimal(
                    bookmaker_odd.get("odds")
                    or bookmaker_odd.get("bookOdds")
                    or odd.get("bookOdds")
                    or odd.get("fairOdds")
                )

                if not cuota:
                    continue

                point_value = (
                    bookmaker_odd.get("overUnder")
                    or bookmaker_odd.get("bookOverUnder")
                    or odd.get("bookOverUnder")
                    or odd.get("fairOverUnder")
                )
                point = None

                if point_value is not None:
                    try:
                        point = float(point_value)
                    except (TypeError, ValueError):
                        point = None

                key = (
                    market_key,
                    description,
                    point if market_key != "h2h" else None,
                )
                outcome = {
                    "name": outcome_name,
                    "price": cuota,
                }

                if point is not None:
                    outcome["point"] = point
                if description:
                    outcome["description"] = description

                markets_by_bookmaker.setdefault(bookmaker_id, {}).setdefault(key, []).append(outcome)

        bookmakers = []

        for bookmaker_id, grouped_markets in markets_by_bookmaker.items():
            markets = []

            for (market_key, _, _), outcomes in grouped_markets.items():
                markets.append({"key": market_key, "outcomes": outcomes})

            if markets:
                bookmakers.append({
                    "key": bookmaker_id,
                    "title": bookmaker_label(bookmaker_id),
                    "markets": markets,
                })

        if bookmakers:
            eventos.append({
                "id": str(event_id),
                "commence_time": event_start_sgo(event),
                "home_team": home,
                "away_team": away,
                "bookmakers": bookmakers,
            })

    return eventos


def cuotas_sportsgameodds(mercados_lista: list[str]) -> list[dict]:
    params = {
        "sportID": SPORTSGAMEODDS_SPORT_ID,
        "oddsAvailable": "true",
        "includeOpposingOdds": "true",
        "includeAltLines": "true",
        "limit": str(SPORTSGAMEODDS_MAX_EVENTS),
    }

    if SPORTSGAMEODDS_LEAGUE_ID:
        params["leagueID"] = SPORTSGAMEODDS_LEAGUE_ID

    if SPORTSGAMEODDS_BOOKMAKERS:
        params["bookmakerID"] = SPORTSGAMEODDS_BOOKMAKERS

    data = sportsgameodds_get("/events", params)
    return adaptar_sportsgameodds_events(data.get("data", []), mercados_lista)


def scores_sportsgameodds(days_from: int = 3) -> list[dict]:
    params = {
        "sportID": SPORTSGAMEODDS_SPORT_ID,
        "ended": "true",
        "expandResults": "true",
        "limit": str(SPORTSGAMEODDS_MAX_EVENTS),
    }

    if SPORTSGAMEODDS_LEAGUE_ID:
        params["leagueID"] = SPORTSGAMEODDS_LEAGUE_ID

    data = sportsgameodds_get("/events", params)
    scores_data = []

    for event in data.get("data", []):
        home = event_team_name_sgo(event, "home")
        away = event_team_name_sgo(event, "away")
        event_id = event.get("eventID") or event.get("id")
        results = event.get("results") or {}
        home_score = results.get("home") or results.get("homeScore") or results.get("scoreHome")
        away_score = results.get("away") or results.get("awayScore") or results.get("scoreAway")

        if not home or not away or not event_id or home_score is None or away_score is None:
            continue

        scores_data.append({
            "id": str(event_id),
            "completed": True,
            "home_team": home,
            "away_team": away,
            "scores": [
                {"name": home, "score": str(home_score)},
                {"name": away, "score": str(away_score)},
            ],
        })

    return scores_data


def api_football_params_base() -> dict[str, str]:
    params = {
        "league": API_FOOTBALL_LEAGUE,
        "season": API_FOOTBALL_SEASON,
    }

    date_filter = os.getenv("API_FOOTBALL_DATE")

    if date_filter:
        params["date"] = date_filter

    return params


def api_football_fixture_map() -> dict[int, dict]:
    data = api_football_get("/fixtures", api_football_params_base())
    fixtures = {}

    for item in data.get("response", []):
        fixture = item.get("fixture") or {}
        fixture_id = fixture.get("id")
        teams = item.get("teams") or {}
        home = (teams.get("home") or {}).get("name")
        away = (teams.get("away") or {}).get("name")

        if not fixture_id or not home or not away:
            continue

        fixtures[int(fixture_id)] = {
            "id": str(fixture_id),
            "commence_time": fixture.get("date"),
            "home_team": home,
            "away_team": away,
            "goals": item.get("goals") or {},
            "status": (fixture.get("status") or {}).get("short"),
        }

    return fixtures


def parse_linea_over_under(value: str) -> tuple[str, float | None] | None:
    match = re.search(r"\b(Over|Under)\s+([0-9]+(?:\.[0-9]+)?)\b", value, re.IGNORECASE)

    if not match:
        return None

    return match.group(1).title(), float(match.group(2))


def normalizar_double_chance_api_football(value: str, home: str, away: str) -> str | None:
    value_norm = value.strip().lower().replace(" ", "")
    mapas = {
        "home/draw": f"{home} or Draw",
        "homeordraw": f"{home} or Draw",
        "1x": f"{home} or Draw",
        "home/away": f"{home} or {away}",
        "homeoraway": f"{home} or {away}",
        "12": f"{home} or {away}",
        "draw/away": f"Draw or {away}",
        "draworaway": f"Draw or {away}",
        "x2": f"Draw or {away}",
    }

    return mapas.get(value_norm)


def adaptar_api_football_bet(
    bet: dict,
    home: str,
    away: str,
    mercados_permitidos: set[str],
) -> dict | None:
    bet_name = str(bet.get("name") or "")
    bet_name_norm = bet_name.lower()
    market_key = None
    outcomes = []

    if "match winner" in bet_name_norm or bet_name_norm in {"winner", "home/away"}:
        market_key = "h2h"

        for value in bet.get("values", []):
            name = str(value.get("value") or "")
            odd = value.get("odd")

            if not odd:
                continue

            name_norm = name.strip().lower()

            if name_norm == "home":
                outcome_name = home
            elif name_norm == "away":
                outcome_name = away
            elif name_norm == "draw":
                outcome_name = "Draw"
            else:
                outcome_name = name

            outcomes.append({"name": outcome_name, "price": float(odd)})

    elif "both teams" in bet_name_norm and "score" in bet_name_norm:
        market_key = "btts"

        for value in bet.get("values", []):
            name = str(value.get("value") or "").title()
            odd = value.get("odd")

            if name in {"Yes", "No"} and odd:
                outcomes.append({"name": name, "price": float(odd)})

    elif "double chance" in bet_name_norm:
        market_key = "double_chance"

        for value in bet.get("values", []):
            odd = value.get("odd")
            outcome_name = normalizar_double_chance_api_football(str(value.get("value") or ""), home, away)

            if outcome_name and odd:
                outcomes.append({"name": outcome_name, "price": float(odd)})

    elif "corner" in bet_name_norm and ("over" in bet_name_norm or "under" in bet_name_norm):
        market_key = "alternate_totals_corners"

        for value in bet.get("values", []):
            odd = value.get("odd")
            parsed = parse_linea_over_under(str(value.get("value") or ""))

            if parsed and odd:
                name, point = parsed
                outcomes.append({"name": name, "point": point, "price": float(odd)})

    elif "card" in bet_name_norm and ("over" in bet_name_norm or "under" in bet_name_norm):
        market_key = "alternate_totals_cards"

        for value in bet.get("values", []):
            odd = value.get("odd")
            parsed = parse_linea_over_under(str(value.get("value") or ""))

            if parsed and odd:
                name, point = parsed
                outcomes.append({"name": name, "point": point, "price": float(odd)})

    elif "goals over/under" in bet_name_norm or "over/under" in bet_name_norm:
        market_key = "totals"

        for value in bet.get("values", []):
            odd = value.get("odd")
            parsed = parse_linea_over_under(str(value.get("value") or ""))

            if parsed and odd:
                name, point = parsed
                outcomes.append({"name": name, "point": point, "price": float(odd)})

    if not market_key or market_key not in mercados_permitidos or not outcomes:
        return None

    return {"key": market_key, "outcomes": outcomes}


def adaptar_api_football_odds(
    odds_items: list[dict],
    fixtures: dict[int, dict],
    mercados_lista: list[str],
) -> list[dict]:
    eventos: dict[str, dict] = {}
    mercados_permitidos = set(mercados_lista)

    for item in odds_items:
        fixture_id = ((item.get("fixture") or {}).get("id"))

        if fixture_id is None:
            continue

        fixture_info = fixtures.get(int(fixture_id))

        if not fixture_info:
            continue

        event_id = str(fixture_id)
        evento = eventos.setdefault(
            event_id,
            {
                "id": event_id,
                "commence_time": fixture_info.get("commence_time"),
                "home_team": fixture_info["home_team"],
                "away_team": fixture_info["away_team"],
                "bookmakers": [],
            },
        )

        for bookmaker in item.get("bookmakers", []):
            markets = []

            for bet in bookmaker.get("bets", []):
                market = adaptar_api_football_bet(
                    bet,
                    fixture_info["home_team"],
                    fixture_info["away_team"],
                    mercados_permitidos,
                )

                if market:
                    markets.append(market)

            if markets:
                evento["bookmakers"].append({
                    "key": str(bookmaker.get("id") or bookmaker.get("name")),
                    "title": bookmaker.get("name"),
                    "markets": markets,
                })

    return list(eventos.values())


def cuotas_api_football(mercados_lista: list[str]) -> list[dict]:
    fixtures = api_football_fixture_map()
    response_items = []
    total_pages = 1
    page = 1

    while page <= min(total_pages, API_FOOTBALL_MAX_PAGES):
        params = api_football_params_base()
        params["page"] = str(page)
        data = api_football_get("/odds", params)
        response_items.extend(data.get("response", []))
        paging = data.get("paging") or {}
        total_pages = int(paging.get("total") or 1)
        page += 1

    return adaptar_api_football_odds(response_items, fixtures, mercados_lista)


def scores_api_football(days_from: int = 3) -> list[dict]:
    fixtures = api_football_fixture_map()
    completed_statuses = {"FT", "AET", "PEN"}
    scores_data = []

    for fixture_id, fixture in fixtures.items():
        if fixture.get("status") not in completed_statuses:
            continue

        goals = fixture.get("goals") or {}
        home_goals = goals.get("home")
        away_goals = goals.get("away")

        if home_goals is None or away_goals is None:
            continue

        scores_data.append({
            "id": str(fixture_id),
            "completed": True,
            "home_team": fixture["home_team"],
            "away_team": fixture["away_team"],
            "scores": [
                {"name": fixture["home_team"], "score": str(home_goals)},
                {"name": fixture["away_team"], "score": str(away_goals)},
            ],
        })

    return scores_data


def traducir_apuesta(apuesta: dict) -> dict:
    apuesta = apuesta.copy()
    apuesta["partido_raw"] = apuesta.get("partido")
    apuesta["equipo_raw"] = apuesta.get("equipo")
    apuesta["tipo_resultado_raw"] = apuesta.get("tipo_resultado")
    apuesta["recomendacion_raw"] = apuesta.get("recomendacion")
    apuesta["motivo_raw"] = apuesta.get("motivo")
    apuesta["partido_es"] = partido_es(apuesta.get("partido"))
    apuesta["equipo_es"] = apuesta_es(
        apuesta.get("equipo"),
        apuesta.get("mercado"),
        apuesta.get("outcome_point"),
        apuesta.get("outcome_description"),
    )
    apuesta["tipo_resultado_es"] = tipo_resultado_es(apuesta.get("tipo_resultado"))
    apuesta["recomendacion_es"] = recomendacion_es(apuesta.get("recomendacion"))
    apuesta["motivo_es"] = motivo_es(apuesta.get("motivo"))
    apuesta["sport_label"] = apuesta.get("sport_label") or "General"
    apuesta["league_label"] = apuesta.get("league_label") or apuesta["sport_label"]
    apuesta["elite_tier"] = apuesta.get("elite_tier") or "seguimiento"
    apuesta["partido"] = apuesta["partido_es"]
    apuesta["equipo"] = apuesta["equipo_es"]
    apuesta["tipo_resultado"] = apuesta["tipo_resultado_es"]
    apuesta["recomendacion"] = apuesta["recomendacion_es"]
    apuesta["motivo"] = apuesta["motivo_es"]
    return apuesta


@app.get("/")
def home():
    html = f"""
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Betting Agent</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                background: #f4f6f8;
                color: #111;
                margin: 32px;
            }}
            .container {{
                max-width: 920px;
                margin: auto;
            }}
            {menu_css()}
            .card {{
                background: white;
                border-radius: 8px;
                padding: 18px;
                margin-bottom: 16px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.08);
            }}
            .grid {{
                display: grid;
                grid-template-columns: repeat(2, minmax(0, 1fr));
                gap: 14px;
            }}
            .card a {{
                color: #0b1f3a;
                font-weight: bold;
            }}
            @media (max-width: 720px) {{
                body {{
                    margin: 16px;
                }}
                .grid {{
                    grid-template-columns: 1fr;
                }}
            }}
        </style>
    </head>
    <body>
    <div class="container">
        {menu_html("inicio")}
        <h1>Betting Agent</h1>
        <p>Herramienta local para detectar picks, quedarte solo con señales élite y medir resultados por deporte, liga y mercado.</p>
        <div class="grid">
            <div class="card">
                <h2>Buscar apuestas</h2>
                <p>Informe con filtros, colores, razonamiento y boton para registrar una apuesta real.</p>
                <a href="/informe-hoy?perfil=alto_riesgo&modo=pinnacle&mercados=todo&partido=todos&deporte=worldcup">Abrir informe</a>
            </div>
            <div class="card">
                <h2>Mis apuestas</h2>
                <p>Gestiona importes reales y marca apuestas como ganadas, perdidas o nulas.</p>
                <a href="/mis-apuestas">Abrir mis apuestas</a>
            </div>
            <div class="card">
                <h2>Dashboard</h2>
                <p>ROI, beneficio, acierto, rendimiento por mercado, casa, perfil y modelo.</p>
                <a href="/dashboard">Abrir dashboard</a>
            </div>
            <div class="card">
                <h2>API</h2>
                <p>Endpoints JSON y pruebas manuales desde Swagger.</p>
                <a href="/docs">Abrir API Docs</a>
            </div>
        </div>
    </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html, media_type="text/html; charset=utf-8")


@app.get("/status")
def status():
    return {
        "status": "Betting agent online",
        "modelo": "Motor multi-deporte con filtros de value, confianza y picks elite",
        "proveedor_cuotas": ODDS_PROVIDER,
        "casa_referencia": REFERENCE_BOOKMAKER,
        "deportes_disponibles": sorted(SPORT_CATALOG.keys()),
        "discovery_endpoint": "/deportes-disponibles",
        "aviso": "No garantiza beneficio. Usar solo como soporte de decision.",
    }


def merge_event_markets(evento_base: dict, evento_extra: dict) -> dict:
    bookmakers_por_key = {
        bookmaker.get("key") or bookmaker.get("title"): bookmaker
        for bookmaker in evento_base.get("bookmakers", [])
    }

    for bookmaker_extra in evento_extra.get("bookmakers", []):
        key = bookmaker_extra.get("key") or bookmaker_extra.get("title")

        if key in bookmakers_por_key:
            markets = bookmakers_por_key[key].setdefault("markets", [])
            existing_keys = {market.get("key") for market in markets}

            for market in bookmaker_extra.get("markets", []):
                if market.get("key") not in existing_keys:
                    markets.append(market)
        else:
            evento_base.setdefault("bookmakers", []).append(bookmaker_extra)

    return evento_base


@app.get("/cuotas")
def cuotas(mercados: str = "h2h,totals", deporte: str | None = None):
    mercados_lista = [m.strip() for m in mercados.split(",") if m.strip()]
    contexto = resolver_contexto_deporte(deporte)

    if ODDS_PROVIDER in {"sportsgameodds", "sports_game_odds", "sgo"}:
        return enriquecer_eventos_contexto(cuotas_sportsgameodds(mercados_lista), contexto)

    if ODDS_PROVIDER in {"api_football", "api-football", "apifootball"}:
        return enriquecer_eventos_contexto(cuotas_api_football(mercados_lista), contexto)

    if not ODDS_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="Falta ODDS_API_KEY en el archivo .env",
        )

    mercados_base = [m for m in mercados_lista if m in FEATURED_MARKETS] or ["h2h"]
    mercados_adicionales = [m for m in mercados_lista if m in ADDITIONAL_MARKETS]
    sport_key = contexto["sport_key"]
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds"

    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu",
        "markets": ",".join(mercados_base),
        "oddsFormat": "decimal",
    }

    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502,
            detail=odds_api_error_detail(exc),
        ) from exc

    data = r.json()

    if isinstance(data, dict) and data.get("message"):
        raise HTTPException(status_code=502, detail=data["message"])

    if not isinstance(data, list):
        raise HTTPException(status_code=502, detail="Respuesta inesperada de The Odds API")

    if mercados_adicionales:
        for evento in data:
            event_id = evento.get("id")

            if not event_id:
                continue

            event_url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/events/{event_id}/odds"
            event_params = {
                "apiKey": ODDS_API_KEY,
                "regions": "eu",
                "markets": ",".join(mercados_adicionales),
                "oddsFormat": "decimal",
            }

            try:
                extra = requests.get(event_url, params=event_params, timeout=15)
                extra.raise_for_status()
            except requests.RequestException:
                continue

            extra_data = extra.json()

            if isinstance(extra_data, dict):
                merge_event_markets(evento, extra_data)

    return enriquecer_eventos_contexto(data, contexto)


@app.get("/scores")
def scores(days_from: int = 3, deporte: str | None = None):
    contexto = resolver_contexto_deporte(deporte)

    if ODDS_PROVIDER in {"sportsgameodds", "sports_game_odds", "sgo"}:
        return enriquecer_eventos_contexto(scores_sportsgameodds(days_from=days_from), contexto)

    if ODDS_PROVIDER in {"api_football", "api-football", "apifootball"}:
        return enriquecer_eventos_contexto(scores_api_football(days_from=days_from), contexto)

    if not ODDS_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="Falta ODDS_API_KEY en el archivo .env",
        )

    days_from = max(1, min(days_from, 3))
    sport_key = contexto["sport_key"]
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/scores"
    params = {
        "apiKey": ODDS_API_KEY,
        "daysFrom": days_from,
        "dateFormat": "iso",
    }

    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502,
            detail=odds_api_error_detail(exc),
        ) from exc

    data = r.json()

    if isinstance(data, dict) and data.get("message"):
        raise HTTPException(status_code=502, detail=data["message"])

    if not isinstance(data, list):
        raise HTTPException(status_code=502, detail="Respuesta inesperada de The Odds API")

    return enriquecer_eventos_contexto(data, contexto)


@app.get("/sportsgameodds/leagues")
def sportsgameodds_leagues(limit: int = 100):
    limit = max(1, min(limit, 500))
    return sportsgameodds_get("/leagues", {
        "sportID": SPORTSGAMEODDS_SPORT_ID,
        "limit": str(limit),
    })


@app.get("/sportsgameodds/eventos-debug")
def sportsgameodds_eventos_debug(limit: int = 3):
    limit = max(1, min(limit, 10))
    params = {
        "sportID": SPORTSGAMEODDS_SPORT_ID,
        "oddsAvailable": "true",
        "limit": str(limit),
    }

    if SPORTSGAMEODDS_LEAGUE_ID:
        params["leagueID"] = SPORTSGAMEODDS_LEAGUE_ID

    return sportsgameodds_get("/events", params)


@app.get("/deportes-disponibles")
def deportes_disponibles(provider: str | None = None):
    catalogo = discover_available_catalog(provider=provider)
    recomendados = [
        item for item in catalogo["sports"]
        if family_from_sport_key(item.get("sport_key", "")) in {"soccer", "tennis", "basketball"}
    ]

    return {
        "provider": catalogo["provider"],
        "total": catalogo["total"],
        "recomendados": recomendados[:200],
        "sports": catalogo["sports"][:500],
    }


def resolver_mercados(filtro: str, deporte: str | None = None) -> tuple[list[str], str | None]:
    config = config_mercados_deporte(deporte)
    filtro_normalizado = filtro if filtro in config["allowed_filters"] else config["default_filter"]

    if filtro_normalizado in FILTROS_MERCADO:
        mercados = FILTROS_MERCADO[filtro_normalizado]
    else:
        mercados = [
            mercado.strip()
            for mercado in filtro_normalizado.split(",")
            if mercado.strip() in MERCADOS_DISPONIBLES
        ]

    aviso = None

    if filtro_normalizado in FILTROS_NO_SOPORTADOS:
        aviso = FILTROS_NO_SOPORTADOS[filtro_normalizado]

    if filtro != filtro_normalizado:
        base = f"El filtro '{filtro}' no aplica a este deporte; se usa '{filtro_normalizado}'."
        aviso = f"{base} {aviso}" if aviso else base

    return mercados, aviso


def estimar_probabilidad_basica(cuota: float) -> float:
    prob = probabilidad_implicita(cuota)

    if cuota >= 10:
        return prob * 0.70
    if cuota >= 5:
        return prob * 0.85
    if cuota >= 2:
        return prob * 0.95
    return prob * 1.02


def seleccionar_casa_referencia(partidos: list[dict], preferida: str) -> tuple[str, bool]:
    casas = []

    for partido in partidos:
        for bookmaker in partido.get("bookmakers", []):
            title = bookmaker.get("title")

            if title and title not in casas:
                casas.append(title)

    if preferida in casas:
        return preferida, False

    return (casas[0], True) if casas else (preferida, False)


def partidos_disponibles(partidos: list[dict]) -> list[dict[str, str]]:
    disponibles = []

    for partido in partidos:
        event_id = partido.get("id")
        home = partido.get("home_team")
        away = partido.get("away_team")

        if not event_id or not home or not away:
            continue

        raw = f"{home} vs {away}"
        disponibles.append({
            "id": str(event_id),
            "label": partido_es(raw),
            "raw": raw,
        })

    return disponibles


def filtrar_partidos(partidos: list[dict], partido_filtro: str) -> list[dict]:
    if not partido_filtro or partido_filtro == "todos":
        return partidos

    filtrados = []

    for partido in partidos:
        event_id = str(partido.get("id") or "")
        raw = f"{partido.get('home_team')} vs {partido.get('away_team')}"

        if partido_filtro == event_id or partido_filtro == raw:
            filtrados.append(partido)

    return filtrados


def titulo_card_apuesta(apuesta: dict) -> str:
    mercado = apuesta.get("mercado")
    equipo = apuesta.get("equipo_es") or apuesta.get("equipo") or ""
    equipo_raw = apuesta.get("equipo_raw") or apuesta.get("equipo")
    tipo_raw = apuesta.get("tipo_resultado_raw") or apuesta.get("tipo_resultado")
    sport_key = str(apuesta.get("sport_key") or "")

    if sport_key.startswith("tennis_") and mercado == "h2h":
        return f"Apostar a que {equipo} gana el partido"

    if sport_key.startswith("basketball_"):
        if mercado == "h2h":
            return f"Apostar a que {equipo} gana el partido"
        if mercado in {"totals", "alternate_totals"}:
            return f"Apostar al total de puntos: {equipo}"

    if mercado == "btts":
        if equipo_raw == "Yes":
            return "Apostar a que ambos equipos anotan"
        if equipo_raw == "No":
            return "Apostar a que no anotan ambos equipos"

    if mercado == "h2h":
        if tipo_raw == "draw":
            return "Apostar a empate"
        return f"Apostar a que {equipo} gana el partido"

    if mercado == "double_chance":
        return f"Apostar doble oportunidad: {equipo}"

    if mercado and mercado != "h2h":
        return f"Apostar: {equipo}"

    return f"Apostar a {equipo}"


def que_tiene_que_pasar(apuesta: dict) -> str:
    mercado = apuesta.get("mercado")
    equipo = apuesta.get("equipo_es") or apuesta.get("equipo") or ""
    equipo_raw = apuesta.get("equipo_raw") or apuesta.get("equipo")
    tipo_raw = apuesta.get("tipo_resultado_raw") or apuesta.get("tipo_resultado")
    point = apuesta.get("outcome_point")
    descripcion = equipo_es(apuesta.get("outcome_description"))
    sport_key = str(apuesta.get("sport_key") or "")

    if sport_key.startswith("tennis_") and mercado == "h2h":
        return f"{equipo} debe ganar el partido completo segun las reglas de la casa."

    if sport_key.startswith("basketball_"):
        if mercado == "h2h":
            return f"{equipo} debe ganar el partido segun el mercado moneyline de la casa."
        if mercado in {"totals", "alternate_totals"}:
            if equipo_raw == "Over":
                return f"El partido debe superar la linea total de puntos de {point:g}." if point is not None else "El partido debe superar la linea total de puntos."
            if equipo_raw == "Under":
                return f"El partido debe quedar por debajo de la linea total de puntos de {point:g}." if point is not None else "El partido debe quedar por debajo de la linea total de puntos."

    if mercado == "h2h":
        if tipo_raw == "draw":
            return "El partido debe terminar empatado en el resultado final, normalmente 90 minutos mas añadido."
        return f"{equipo} debe ganar el partido en el resultado final, normalmente 90 minutos mas añadido."

    if mercado == "btts":
        if equipo_raw == "Yes":
            return "Deben marcar los dos equipos al menos un gol."
        if equipo_raw == "No":
            return "No deben marcar los dos equipos; vale 0-0 o que solo marque uno."

    if mercado in {"totals", "alternate_totals"}:
        if equipo_raw == "Over":
            return f"Entre los dos equipos debe haber mas de {point:g} goles." if point is not None else "Debe haber mas goles que la linea marcada."
        if equipo_raw == "Under":
            return f"Entre los dos equipos debe haber menos de {point:g} goles." if point is not None else "Debe haber menos goles que la linea marcada."

    if mercado == "team_totals":
        if equipo_raw == "Over":
            return f"{descripcion} debe marcar mas de {point:g} goles." if point is not None else f"{descripcion} debe superar su linea de goles."
        if equipo_raw == "Under":
            return f"{descripcion} debe marcar menos de {point:g} goles." if point is not None else f"{descripcion} debe quedar por debajo de su linea de goles."

    if mercado == "alternate_team_totals_corners":
        if equipo_raw == "Over":
            return f"{descripcion} debe sacar mas de {point:g} corners." if point is not None else f"{descripcion} debe superar su linea de corners."
        if equipo_raw == "Under":
            return f"{descripcion} debe sacar menos de {point:g} corners." if point is not None else f"{descripcion} debe quedar por debajo de su linea de corners."

    if mercado == "alternate_totals_corners":
        if equipo_raw == "Over":
            return f"El partido debe tener mas de {point:g} corners en total." if point is not None else "El partido debe superar la linea total de corners."
        if equipo_raw == "Under":
            return f"El partido debe tener menos de {point:g} corners en total." if point is not None else "El partido debe quedar por debajo de la linea total de corners."

    if mercado == "alternate_totals_cards":
        if equipo_raw == "Over":
            return f"El partido debe tener mas de {point:g} tarjetas en total." if point is not None else "El partido debe superar la linea total de tarjetas."
        if equipo_raw == "Under":
            return f"El partido debe tener menos de {point:g} tarjetas en total." if point is not None else "El partido debe quedar por debajo de la linea total de tarjetas."

    if mercado == "double_chance":
        return f"La apuesta gana si se cumple cualquiera de estas opciones: {equipo}."

    if mercado == "corners_1x2":
        return f"{equipo} debe sacar mas corners que el rival."

    return f"Debe cumplirse exactamente la seleccion indicada por la casa: {equipo}."


def etiqueta_tipo_apuesta(apuesta: dict) -> tuple[str, str]:
    mercado = apuesta.get("mercado")

    if mercado == "h2h":
        return "Resultado elegido", apuesta.get("tipo_resultado_es", "")

    return "Mercado", apuesta.get("tipo_resultado_es", "")


def detalle_modelo_html(apuesta: dict) -> str:
    modelo = apuesta.get("modelo_mercado")
    elo_equipo = apuesta.get("elo_equipo")
    elo_rival = apuesta.get("elo_rival")

    partes = []

    if modelo:
        partes.append(f"<p><strong>Modelo usado:</strong> {escape(str(modelo))}</p>")

    if elo_equipo is not None or elo_rival is not None:
        partes.append(f"<p><strong>ELO apuesta:</strong> {elo_equipo}</p>")
        partes.append(f"<p><strong>ELO rival:</strong> {elo_rival}</p>")

    return "".join(partes)


def analisis_apuesta_texto(apuesta: dict) -> str:
    prob_mercado = float(apuesta.get("probabilidad_mercado") or 0)
    prob_modelo = float(apuesta.get("probabilidad_modelo") or 0)
    edge_puntos = (prob_modelo - prob_mercado) * 100
    valor = float(apuesta.get("valor_esperado") or 0) * 100
    margen = float(apuesta.get("margen_cuota") or 0)
    confianza = apuesta.get("confianza", "Baja")
    motivo = apuesta.get("motivo_es") or apuesta.get("motivo") or ""
    seleccion = apuesta.get("equipo_es") or apuesta.get("equipo") or "esta seleccion"
    cuota = apuesta.get("cuota_apuesta") or apuesta.get("cuota_pinnacle")
    cuota_minima = apuesta.get("cuota_minima_aceptable")
    stake = float(apuesta.get("stake") or 0)

    if stake <= 0:
        return (
            f"No la recomiendo porque {motivo.lower()}. "
            f"La cuota {cuota} no compensa lo suficiente frente a la cuota minima {cuota_minima}, "
            f"o el modelo no ve ventaja clara."
        )

    partes = [
        f"La apuesta tiene sentido porque el modelo da a {seleccion} una probabilidad de {prob_modelo * 100:.1f}%, "
        f"mientras el mercado la valora en {prob_mercado * 100:.1f}%.",
    ]

    if edge_puntos > 0:
        partes.append(f"Esa diferencia es de {edge_puntos:.1f} puntos a favor del modelo.")
    else:
        partes.append("La diferencia frente al mercado es muy ajustada, asi que no es una apuesta fuerte.")

    partes.append(f"La cuota {cuota} queda por encima de la cuota minima estimada {cuota_minima}, con valor esperado de {valor:.1f}%.")

    if margen < 1.03:
        partes.append("El margen es pequeno, por eso el stake recomendado sigue siendo prudente.")

    if confianza == "Baja":
        partes.append("La confianza es baja: puede ser value, pero no conviene forzar mucho dinero.")
    elif confianza == "Media":
        partes.append("La confianza es media: es una senal razonable, aunque sigue dependiendo de varianza.")
    else:
        partes.append("La confianza es alta dentro del sistema, aun asi no garantiza acierto.")

    partes.append(f"Motivo del modelo: {motivo}.")

    return " ".join(partes)


def analisis_final_texto(mejores: list[dict], descartadas: list[dict]) -> str:
    if not mejores:
        if not descartadas:
            return "No hay suficientes datos utiles para emitir una recomendacion ahora mismo."

        principal = descartadas[0]
        motivo = principal.get("motivo_es") or principal.get("motivo") or "no hay margen suficiente"
        return (
            "Ahora mismo no apostaria. Las mejores opciones analizadas no superan el filtro minimo; "
            f"la principal descartada cae por este motivo: {motivo.lower()}."
        )

    mejor = mejores[0]
    confianza = mejor.get("confianza", "Baja")
    total = len(mejores)
    valor = float(mejor.get("valor_esperado") or 0) * 100
    importe = mejor.get("importe_sugerido")
    seleccion = mejor.get("equipo_es") or mejor.get("equipo")
    partido = mejor.get("partido_es") or mejor.get("partido")

    texto = (
        f"El informe encuentra {total} apuesta(s) con valor. La senal principal es {seleccion} en {partido}, "
        f"con valor esperado aproximado de {valor:.1f}% e importe sugerido de {importe} EUR."
    )

    if confianza == "Baja":
        texto += " Como la confianza es baja, lo trataria como apuesta ligera: buena para probar el modelo, no para cargar fuerte."
    elif confianza == "Media":
        texto += " La confianza es media, asi que se puede aceptar el stake sugerido sin salirse del bankroll."
    else:
        texto += " La confianza es alta para el sistema, pero mantendria igualmente el limite de stake."

    if descartadas:
        texto += " Las descartadas se dejan fuera porque la cuota no compensa suficiente o el modelo no ve ventaja real."

    return texto


def clase_card_apuesta(apuesta: dict) -> str:
    if float(apuesta.get("stake") or 0) <= 0:
        return "card bet-card bet-red"

    confianza = apuesta.get("confianza")
    valor = float(apuesta.get("valor_esperado") or 0)
    margen = float(apuesta.get("margen_cuota") or 0)

    if confianza == "Alta" or (valor >= 0.05 and margen >= 1.05):
        return "card bet-card bet-green"

    return "card bet-card bet-yellow"


def apuesta_form_token(apuesta: dict) -> str:
    payload = json.dumps(apuesta, ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def apuesta_from_form_token(token: str) -> dict:
    try:
        payload = base64.urlsafe_b64decode(token.encode("ascii"))
        return json.loads(payload.decode("utf-8"))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("No se pudo leer la apuesta enviada") from exc


def menu_html(active: str = "") -> str:
    items = [
        ("inicio", "/", "Inicio"),
        ("informe", "/informe-hoy?perfil=alto_riesgo&modo=pinnacle&mercados=todo&partido=todos&deporte=worldcup", "Buscar apuestas"),
        ("mis_apuestas", "/mis-apuestas", "Mis apuestas"),
        ("dashboard", "/dashboard", "Dashboard"),
        ("stats", "/tracking/stats", "Stats JSON"),
        ("aprendizaje", "/tracking/aprendizaje", "Aprendizaje JSON"),
        ("docs", "/docs", "API Docs"),
    ]
    links = []

    for key, href, label in items:
        cls = "active" if key == active else ""
        links.append(f'<a class="{cls}" href="{href}">{label}</a>')

    return f"""
    <nav class="top-menu">
        {"".join(links)}
    </nav>
    """


def menu_css() -> str:
    return """
            .top-menu {
                display: flex;
                flex-wrap: wrap;
                gap: 8px;
                margin-bottom: 20px;
                align-items: center;
            }
            .top-menu a {
                background: #e2e8f0;
                color: #0b1f3a;
                border-radius: 6px;
                padding: 9px 11px;
                text-decoration: none;
                font-weight: bold;
                font-size: 14px;
            }
            .top-menu a.active {
                background: #0b1f3a;
                color: white;
            }
    """


@app.get("/mejores-apuestas")
def mejores_apuestas():
    data = cuotas(mercados="h2h")
    apuestas = []

    for partido in data:
        home = partido.get("home_team")
        away = partido.get("away_team")

        for bookmaker in partido.get("bookmakers", []):
            casa = bookmaker.get("title")

            #if casa != "Pinnacle":
            #    continue

            for market in bookmaker.get("markets", []):
                if market.get("key") != "h2h":
                    continue

                for outcome in market.get("outcomes", []):
                    equipo = outcome.get("name")
                    cuota = outcome.get("price")

                    if not equipo or not cuota:
                        continue

                    prob_modelo = estimar_probabilidad_basica(float(cuota))
                    implied = probabilidad_implicita(float(cuota))
                    ev = valor_esperado(prob_modelo, float(cuota))

                    apuestas.append({
                        "partido": partido_es(f"{home} vs {away}"),
                        "partido_raw": f"{home} vs {away}",
                        "partido_es": partido_es(f"{home} vs {away}"),
                        "casa": casa,
                        "equipo": equipo_es(equipo),
                        "equipo_raw": equipo,
                        "equipo_es": equipo_es(equipo),
                        "cuota": cuota,
                        "probabilidad_implicita": round(implied, 3),
                        "probabilidad_estimada": round(prob_modelo, 3),
                        "valor_esperado": round(ev, 3),
                        "stake": 0 if ev <= 0 else 1,
                        "recomendacion": "No apostar" if ev <= 0 else "Posible apuesta",
                    })

    apuestas = sorted(apuestas, key=lambda x: x["valor_esperado"], reverse=True)

    return {
        "total_apuestas_analizadas": len(apuestas),
        "top_10": apuestas[:10],
    }


@app.get("/hoy")
def apuestas_hoy(
    bankroll: float | None = None,
    perfil: str = "moderado",
    modo: str = "comparador",
    mercados: str = "todo",
    partido: str = "todos",
    guardar: bool = False,
    deporte: str = DEFAULT_SPORT,
    solo_elite: bool = False,
    solo_stakazos: bool = False,
):
    if (deporte or "").strip().lower() == "todo":
        deportes_agregados = deportes_agregados_para_todo()
        total_opciones_todo = max(
            0,
            len([item for item in opciones_deporte_disponibles() if str(item.get("value") or "").strip().lower() != "todo"]),
        )
        agregado = []
        descartadas_total = []
        partidos_total = []
        cobertura = []
        errores_cobertura = []
        total_analizadas = 0
        total_guardadas = 0
        total_snapshots = 0

        for deporte_item in deportes_agregados:
            try:
                data_item = apuestas_hoy(
                    bankroll=bankroll,
                    perfil=perfil,
                    modo=modo,
                    mercados=mercados,
                    partido=partido,
                    guardar=guardar,
                    deporte=deporte_item,
                    solo_elite=solo_elite,
                    solo_stakazos=solo_stakazos,
                )
            except HTTPException as exc:
                errores_cobertura.append({
                    "deporte": deporte_item,
                    "detail": str(exc.detail),
                })
                continue
            agregado.extend(data_item.get("mejores_apuestas", []))
            descartadas_total.extend(data_item.get("descartadas", []))
            liga_label = str(data_item.get("league_label") or data_item.get("sport_label") or deporte_item)
            partidos_item = data_item.get("partidos_disponibles", [])
            for partido_item in partidos_item:
                partidos_total.append({
                    **partido_item,
                    "label": f"{liga_label} | {partido_item.get('label')}",
                })
            cobertura.append({
                "deporte": deporte_item,
                "sport_label": data_item.get("sport_label"),
                "league_label": data_item.get("league_label"),
                "partidos": len(partidos_item),
                "recomendadas": len(data_item.get("mejores_apuestas", [])),
            })
            total_analizadas += int(data_item.get("total_analizadas", 0))
            total_guardadas += int(data_item.get("total_guardadas", 0))
            total_snapshots += int(data_item.get("snapshots_guardados", 0))

        recomendadas_full = sorted(agregado, key=prioridad_pick_todo, reverse=True)
        recomendadas = limitar_picks_todo(recomendadas_full, max_total=6)
        elite = [r for r in recomendadas if r.get("elite_pick")]
        stakazos = [r for r in elite if str(r.get("elite_tier") or "").lower() == "stakazo"]
        premium = [r for r in recomendadas if str(r.get("elite_tier") or "").lower() == "premium"]
        seguimiento = [r for r in recomendadas if str(r.get("elite_tier") or "").lower() == "seguimiento"]
        partidos_unicos = list({item["id"]: item for item in partidos_total}.values())
        deportes_con_eventos = [item for item in cobertura if item["partidos"] > 0]
        cobertura_txt = ", ".join(
            f"{item['league_label']} ({item['partidos']})"
            for item in deportes_con_eventos
        ) or "ninguno"
        aviso_cobertura = None
        if len(deportes_con_eventos) <= 1:
            aviso_cobertura = f"Con 'Todo' el sistema agrega los deportes base soportados. Ahora mismo solo hay eventos en: {cobertura_txt}."
        if total_opciones_todo > len(deportes_agregados):
            aviso_limite = (
                f"Modo 'Todo' limitado para cargar mas rapido: se revisan {len(deportes_agregados)} ligas priorizadas "
                f"de {total_opciones_todo} disponibles y se muestran solo los picks mas fiables y proximos."
            )
            aviso_cobertura = f"{aviso_limite} {aviso_cobertura}".strip() if aviso_cobertura else aviso_limite
        if errores_cobertura:
            ligas_error = ", ".join(item["deporte"] for item in errores_cobertura[:5])
            aviso_error = f"Algunas ligas se omitieron en 'Todo' porque el proveedor rechazo sus parametros: {ligas_error}."
            aviso_cobertura = f"{aviso_cobertura} {aviso_error}".strip() if aviso_cobertura else aviso_error

        if not recomendadas and not cobertura and errores_cobertura:
            raise HTTPException(status_code=502, detail=errores_cobertura[0]["detail"])

        return {
            "criterio": "Agregado multi-deporte sobre deportes base soportados",
            "aviso": "No garantiza beneficio. El stake esta limitado y debe subirse solo con historico, ROI y CLV positivo.",
            "proveedor_cuotas": ODDS_PROVIDER,
            "casa_referencia": REFERENCE_BOOKMAKER,
            "casa_referencia_fallback": False,
            "bankroll": obtener_bankroll() if bankroll is None else float(bankroll),
            "perfil": perfil,
            "perfil_es": perfil_es(perfil if perfil in PERFILES_STAKE else "moderado"),
            "modo": modo,
            "sport_key": "multi_sport",
            "sport_label": "Todo",
            "league_key": "multi_league",
            "league_label": "Todas las ligas base",
            "deporte": "todo",
            "solo_elite": solo_elite,
            "solo_stakazos": solo_stakazos,
            "source_strength": "mixed",
            "mercados": mercados,
            "filtro_mercados": mercados,
            "partido": partido,
            "partidos_disponibles": partidos_unicos,
            "aviso_mercados": None,
            "aviso_cobertura": aviso_cobertura,
            "cobertura_deportes": cobertura,
            "errores_cobertura": errores_cobertura,
            "snapshots_guardados": total_snapshots,
            "modo_es": modo_es(modo if modo in MODOS_INFORME else "comparador"),
            "stake_maximo_por_pick": {
                "conservador": "1.5% del bankroll",
                "moderado": "3% del bankroll",
                "agresivo": "8% del bankroll",
                "alto_riesgo": "50% del bankroll",
            }.get(perfil, "3% del bankroll"),
            "total_analizadas": total_analizadas,
            "total_recomendadas": len(recomendadas),
            "total_elite": len(elite),
            "total_stakazos": len(stakazos),
            "total_premium": len(premium),
            "total_seguimiento": len(seguimiento),
            "total_guardadas": total_guardadas,
            "mejores_apuestas": recomendadas[:5],
            "picks_elite": stakazos[:10] if solo_stakazos else elite[:10],
            "descartadas": sorted(descartadas_total, key=prioridad_pick, reverse=True)[:5],
        }

    bankroll = obtener_bankroll() if bankroll is None else float(bankroll)
    actualizar_bankroll(bankroll)
    contexto_deporte = resolver_contexto_deporte(deporte)
    deporte = contexto_deporte["catalog_key"]
    source_strength = {
        "worldcup": "market+model",
        "futbol": "market+model",
        "tenis": "tennis_model",
        "baloncesto": "basketball_model",
    }.get(deporte, "market_only")
    if source_strength not in {"market+model", "tennis_model", "basketball_model"} and contexto_deporte["supports_elo"]:
        source_strength = "market+model"
    if source_strength not in {"market+model", "tennis_model", "basketball_model"}:
        source_strength = "market_only"

    if perfil not in PERFILES_STAKE:
        perfil = "moderado"
    if modo not in MODOS_INFORME:
        modo = "comparador"

    filtro_mercados = mercados
    mercados_lista, aviso_mercados = resolver_mercados(mercados, deporte=deporte)

    if not mercados_lista:
        return {
            "criterio": "Sin mercados disponibles para el filtro elegido",
            "aviso": "No garantiza beneficio. El stake esta limitado y debe subirse solo con historico, ROI y CLV positivo.",
            "bankroll": bankroll,
            "perfil": perfil,
            "perfil_es": perfil_es(perfil),
            "modo": modo,
            "modo_es": modo_es(modo),
            "deporte": deporte,
            "sport_key": contexto_deporte["sport_key"],
            "sport_label": contexto_deporte["sport_label"],
            "league_label": contexto_deporte["league_label"],
            "solo_elite": solo_elite,
            "solo_stakazos": solo_stakazos,
            "mercados": "",
            "filtro_mercados": filtro_mercados,
            "partido": partido,
            "partidos_disponibles": [],
            "aviso_mercados": aviso_mercados,
            "snapshots_guardados": 0,
            "stake_maximo_por_pick": {
                "conservador": "1.5% del bankroll",
                "moderado": "3% del bankroll",
                "agresivo": "8% del bankroll",
                "alto_riesgo": "50% del bankroll",
            }.get(perfil, "3% del bankroll"),
            "total_analizadas": 0,
            "total_recomendadas": 0,
            "total_guardadas": 0,
            "mejores_apuestas": [],
            "descartadas": [],
        }

    mercados = ",".join(mercados_lista)
    data_completa = cuotas(mercados=mercados, deporte=deporte)
    partidos_select = partidos_disponibles(data_completa)
    snapshots_guardados = guardar_snapshot_cuotas(data_completa)
    data = filtrar_partidos(data_completa, partido)

    if not data:
        return {
            "criterio": "No hay cuotas disponibles para el partido elegido",
            "aviso": "No garantiza beneficio. El stake esta limitado y debe subirse solo con historico, ROI y CLV positivo.",
            "proveedor_cuotas": ODDS_PROVIDER,
            "casa_referencia": REFERENCE_BOOKMAKER,
            "casa_referencia_fallback": False,
            "bankroll": bankroll,
            "perfil": perfil,
            "perfil_es": perfil_es(perfil),
            "modo": modo,
            "deporte": deporte,
            "sport_key": contexto_deporte["sport_key"],
            "sport_label": contexto_deporte["sport_label"],
            "league_key": contexto_deporte["league_key"],
            "league_label": contexto_deporte["league_label"],
            "solo_elite": solo_elite,
            "solo_stakazos": solo_stakazos,
            "mercados": mercados,
            "filtro_mercados": filtro_mercados,
            "partido": partido,
            "partidos_disponibles": partidos_select,
            "aviso_mercados": aviso_mercados,
            "snapshots_guardados": snapshots_guardados,
            "modo_es": modo_es(modo),
            "stake_maximo_por_pick": {
                "conservador": "1.5% del bankroll",
                "moderado": "3% del bankroll",
                "agresivo": "8% del bankroll",
                "alto_riesgo": "50% del bankroll",
            }.get(perfil, "3% del bankroll"),
            "total_analizadas": 0,
            "total_recomendadas": 0,
            "total_guardadas": 0,
            "mejores_apuestas": [],
            "descartadas": [],
        }

    try:
        elos = obtener_elos() if contexto_deporte["supports_elo"] else {}
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"No se pudieron descargar los ELO: {exc}",
        ) from exc

    casa_referencia, referencia_fallback = seleccionar_casa_referencia(data, REFERENCE_BOOKMAKER)

    if modo == "comparador":
        recomendaciones = analizar_comparador_casas(
            data,
            elos,
            bankroll=bankroll,
            perfil=perfil,
            mercados=mercados_lista,
            casa_referencia=casa_referencia,
            source_strength=source_strength,
        )
        criterio = f"{casa_referencia} como referencia + busqueda de cuotas mejores en otras casas + modelo"
    else:
        recomendaciones = analizar_comparador_casas(
            data,
            elos,
            bankroll=bankroll,
            perfil=perfil,
            mercados=mercados_lista,
            incluir_referencia=True,
            casa_referencia=casa_referencia,
            solo_casa=casa_referencia,
            source_strength=source_strength,
        )
        criterio = f"Solo {casa_referencia} + mercados seleccionados + modelo"

    if source_strength == "market+model":
        criterio += " (mercado + ELO)"
    elif source_strength == "tennis_model":
        criterio += " (moneyline tenis conservador)"
    elif source_strength == "basketball_model":
        criterio += " (moneyline/totales basket conservador)"
    else:
        criterio += " (market-only ultra conservador)"

    if referencia_fallback:
        criterio += f" (aviso: {REFERENCE_BOOKMAKER} no estaba disponible en estas cuotas)"

    recomendaciones = [traducir_apuesta(r) for r in recomendaciones]
    penalizaciones = penalizaciones_historicas()

    for recomendacion in recomendaciones:
        recomendacion["perfil"] = perfil
        recomendacion["perfil_es"] = perfil_es(perfil)
        recomendacion["modo"] = modo
        recomendacion["modo_es"] = modo_es(modo)
        recomendacion["filtro_mercados"] = filtro_mercados
        recomendacion["sport_key"] = contexto_deporte["sport_key"]
        recomendacion["sport_label"] = contexto_deporte["sport_label"]
        recomendacion["league_key"] = contexto_deporte["league_key"]
        recomendacion["league_label"] = contexto_deporte["league_label"]

    recomendaciones = [aplicar_penalizacion_historica(r, penalizaciones) for r in recomendaciones]

    recomendadas = sorted(
        [r for r in recomendaciones if r["stake"] > 0],
        key=prioridad_pick,
        reverse=True,
    )
    elite = sorted(
        [r for r in recomendadas if r.get("elite_pick")],
        key=prioridad_pick,
        reverse=True,
    )
    stakazos = sorted(
        [r for r in elite if str(r.get("elite_tier") or "").lower() == "stakazo"],
        key=prioridad_pick,
        reverse=True,
    )
    premium = sorted(
        [r for r in recomendadas if str(r.get("elite_tier") or "").lower() == "premium"],
        key=prioridad_pick,
        reverse=True,
    )
    seguimiento = sorted(
        [r for r in recomendadas if str(r.get("elite_tier") or "").lower() == "seguimiento"],
        key=prioridad_pick,
        reverse=True,
    )
    if solo_stakazos:
        recomendadas = stakazos
    elif solo_elite:
        recomendadas = elite
    descartadas = sorted(
        [r for r in recomendaciones if r["stake"] == 0],
        key=prioridad_pick,
        reverse=True,
    )
    total_guardadas = guardar_recomendaciones(recomendadas) if guardar else 0
    limite_mejores = 3 if partido and partido != "todos" else 5

    return {
        "criterio": criterio,
        "aviso": "No garantiza beneficio. El stake esta limitado y debe subirse solo con historico, ROI y CLV positivo.",
        "proveedor_cuotas": ODDS_PROVIDER,
        "casa_referencia": casa_referencia,
        "casa_referencia_fallback": referencia_fallback,
        "bankroll": bankroll,
        "perfil": perfil,
        "perfil_es": perfil_es(perfil),
        "modo": modo,
        "deporte": deporte,
        "sport_key": contexto_deporte["sport_key"],
        "sport_label": contexto_deporte["sport_label"],
        "league_key": contexto_deporte["league_key"],
        "league_label": contexto_deporte["league_label"],
        "solo_elite": solo_elite,
        "solo_stakazos": solo_stakazos,
        "source_strength": source_strength,
        "mercados": mercados,
        "filtro_mercados": filtro_mercados,
        "partido": partido,
        "partidos_disponibles": partidos_select,
        "aviso_mercados": aviso_mercados,
        "snapshots_guardados": snapshots_guardados,
        "modo_es": modo_es(modo),
        "stake_maximo_por_pick": {
            "conservador": "1.5% del bankroll",
            "moderado": "3% del bankroll",
            "agresivo": "8% del bankroll",
            "alto_riesgo": "50% del bankroll",
        }.get(perfil, "3% del bankroll"),
        "total_analizadas": len(recomendaciones),
        "total_recomendadas": len(recomendadas),
        "total_elite": len(elite),
        "total_stakazos": len(stakazos),
        "total_premium": len(premium),
        "total_seguimiento": len(seguimiento),
        "total_guardadas": total_guardadas,
        "mejores_apuestas": recomendadas[:limite_mejores],
        "picks_elite": stakazos[:10] if solo_stakazos else elite[:10],
        "descartadas": descartadas[:5],
    }


@app.get("/informe-hoy", response_class=HTMLResponse)
def informe_hoy(
    bankroll: float | None = None,
    perfil: str = "moderado",
    modo: str = "comparador",
    mercados: str = "todo",
    partido: str = "todos",
    deporte: str = DEFAULT_SPORT,
    solo_elite: bool = False,
    solo_stakazos: bool = False,
):
    bankroll = obtener_bankroll() if bankroll is None else float(bankroll)
    actualizar_bankroll(bankroll)
    if (deporte or "").strip().lower() == "todo":
        contexto_deporte = {
            "catalog_key": "todo",
            "sport_label": "Todo",
            "league_label": "Todas las ligas base",
        }
        deporte = "todo"
    else:
        contexto_deporte = resolver_contexto_deporte(deporte)
        deporte = contexto_deporte["catalog_key"]

    if perfil not in PERFILES_STAKE:
        perfil = "moderado"
    if modo not in MODOS_INFORME:
        modo = "comparador"

    data = apuestas_hoy(
        bankroll=bankroll,
        perfil=perfil,
        modo=modo,
        mercados=mercados,
        partido=partido,
        deporte=deporte,
        solo_elite=solo_elite,
        solo_stakazos=solo_stakazos,
    )
    filtro_mercados = data.get("filtro_mercados", mercados)
    origen_url = (
        f"/informe-hoy?perfil={perfil}&modo={modo}&mercados={filtro_mercados}&partido={partido}&deporte={deporte}&solo_elite={'true' if solo_elite else 'false'}&solo_stakazos={'true' if solo_stakazos else 'false'}"
    )

    mejores = data.get("mejores_apuestas", [])
    descartadas = data.get("descartadas", [])
    perfil_seguro = escape(perfil_es(perfil))
    modo_seguro = escape(modo_es(modo))
    deporte_seguro = escape(data.get("sport_label", deporte))
    partido_actual = data.get("partido", partido)
    partidos_select = data.get("partidos_disponibles", [])
    config_deporte = config_mercados_deporte("futbol" if deporte == "todo" else deporte)
    opciones_perfil = "".join(
        f'<option value="{nombre}" {"selected" if perfil == nombre else ""}>{label}</option>'
        for nombre, label in [
            ("conservador", perfil_es("conservador")),
            ("moderado", perfil_es("moderado")),
            ("agresivo", perfil_es("agresivo")),
            ("alto_riesgo", perfil_es("alto_riesgo")),
        ]
    )
    opciones_modo = "".join(
        f'<option value="{nombre}" {"selected" if modo == nombre else ""}>{label}</option>'
        for nombre, label in [
            ("comparador", modo_es("comparador")),
            ("pinnacle", modo_es("pinnacle")),
        ]
    )
    opciones_deporte = "".join(
        f'<option value="{item["value"]}" {"selected" if deporte == item["value"] else ""}>{escape(item["label"])}</option>'
        for item in opciones_deporte_disponibles(selected=deporte)
    )
    opciones_mercados = "".join(
        f'<option value="{value}" {"selected" if filtro_mercados == value else ""}>{label}</option>'
        for value, label in [
            ("todo", "Todo"),
            ("resultado", "Resultado"),
            ("ambos_anotan", "Ambos equipos anotarán"),
            ("se_clasificara", "Se clasificará"),
            ("doble_oportunidad", "Doble oportunidad"),
            ("total_goles", "Total de goles"),
            ("goles_intervalo", "Goles - intervalo"),
            ("corners", "Córners"),
            ("tarjetas", "Tarjetas"),
            ("ambos_tarjetas", "Ambos equipos recibirán tarjetas"),
            ("equipo_mayor_numero", "Equipo - mayor número"),
            ("team_goals", "Más/menos goles por equipo"),
            ("team_corners", "Más/menos corners por equipo"),
            ("team_fouls", "Más/menos faltas por equipo"),
            ("jugador_faltas_concedidas", "Jugador - faltas concedidas"),
            ("jugador_recibira_falta", "Jugador - recibirá falta"),
            ("jugador_entradas", "Jugador - entradas"),
            ("jugador_remates_cabeza", "Jugador - remates a puerta de cabeza"),
            ("jugador_remates_fuera_area", "Jugador - remates a puerta fuera del área"),
        ]
    )
    opciones_mercados = "".join(
        f'<option value="{value}" {"selected" if filtro_mercados == value else ""}>{SPORT_FILTER_LABELS.get(value, value)}</option>'
        for value in config_deporte["allowed_filters"]
    )
    checked_elite = "checked" if solo_elite else ""
    checked_stakazos = "checked" if solo_stakazos else ""
    opciones_partidos = '<option value="todos" {}>Todos los partidos</option>'.format(
        "selected" if partido_actual == "todos" else ""
    )
    opciones_partidos += "".join(
        f'<option value="{escape(item["id"], quote=True)}" {"selected" if partido_actual == item["id"] else ""}>{escape(item["label"])}</option>'
        for item in partidos_select
    )

    html = """
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Informe de apuestas</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                background: #f4f6f8;
                color: #111;
                margin: 40px;
            }
            .container {
                max-width: 900px;
                margin: auto;
            }
            __MENU_CSS__
            h1 {
                color: #0b1f3a;
            }
            .aviso {
                background: #fff3cd;
                padding: 15px;
                border-radius: 8px;
                margin-bottom: 25px;
            }
            .card {
                background: white;
                border-radius: 12px;
                padding: 20px;
                margin-bottom: 20px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.08);
            }
            .bet-card {
                border-left: 8px solid #94a3b8;
            }
            .bet-green {
                background: #ecfdf3;
                border-left-color: #16a34a;
            }
            .bet-yellow {
                background: #fffbeb;
                border-left-color: #f59e0b;
            }
            .bet-red {
                background: #fef2f2;
                border-left-color: #dc2626;
            }
            .badge {
                display: inline-block;
                border-radius: 999px;
                padding: 5px 9px;
                font-size: 13px;
                font-weight: bold;
                margin-bottom: 8px;
            }
            .badge-green {
                background: #dcfce7;
                color: #166534;
            }
            .badge-yellow {
                background: #fef3c7;
                color: #92400e;
            }
            .badge-red {
                background: #fee2e2;
                color: #991b1b;
            }
            .bet-actions {
                display: flex;
                gap: 10px;
                align-items: end;
                flex-wrap: wrap;
                margin-top: 14px;
                padding-top: 14px;
                border-top: 1px solid #e2e8f0;
            }
            .bet-actions .field {
                min-width: 170px;
            }
            .value {
                font-weight: bold;
                color: #0a7a32;
            }
            .descartada {
                color: #666;
            }
            .stake {
                font-weight: bold;
            }
            .filters {
                background: white;
                border-radius: 8px;
                padding: 16px;
                margin-bottom: 20px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.08);
            }
            .filters form {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
                gap: 12px;
                align-items: end;
            }
            .filters .field,
            .filters button {
                min-width: 0;
            }
            .field {
                display: flex;
                flex-direction: column;
                gap: 6px;
            }
            label {
                font-size: 13px;
                font-weight: bold;
                color: #334155;
            }
            input,
            select {
                width: 100%;
                min-width: 0;
                height: 40px;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 0 10px;
                font-size: 15px;
                background: white;
                box-sizing: border-box;
            }
            button {
                height: 40px;
                border: 0;
                border-radius: 6px;
                padding: 0 18px;
                background: #0b1f3a;
                color: white;
                font-weight: bold;
                cursor: pointer;
            }
            .summary {
                display: flex;
                gap: 12px;
                flex-wrap: wrap;
                margin-top: 14px;
                color: #334155;
                font-size: 14px;
            }
            .summary span {
                background: #e2e8f0;
                border-radius: 6px;
                padding: 6px 8px;
            }
            .checkbox-row {
                display: flex;
                align-items: center;
                gap: 10px;
                min-height: 40px;
                flex-wrap: wrap;
            }
            .checkbox-row input {
                height: auto;
                width: auto;
            }
            @media (max-width: 720px) {
                body {
                    margin: 18px;
                }
                .filters form {
                    grid-template-columns: 1fr;
                }
                button {
                    width: 100%;
                }
            }
        </style>
    </head>
    <body>
    <div class="container">
        __MENU_HTML__
        <h1>Informe de apuestas</h1>
        <div class="aviso">
            No garantiza beneficio. Modo: __MODO__. Perfil activo: __PERFIL__. El riesgo sube la varianza.
        </div>
        <div class="filters">
            <form method="get" action="/informe-hoy">
                <div class="field">
                    <label for="bankroll">Bankroll</label>
                    <input id="bankroll" name="bankroll" type="number" min="1" step="0.01" value="__BANKROLL__">
                </div>
                <div class="field">
                    <label for="perfil">Perfil</label>
                    <select id="perfil" name="perfil">
                        __OPCIONES_PERFIL__
                    </select>
                </div>
                <div class="field">
                    <label for="modo">Modo</label>
                    <select id="modo" name="modo">
                        __OPCIONES_MODO__
                    </select>
                </div>
                <div class="field">
                    <label for="deporte">Deporte</label>
                    <select id="deporte" name="deporte">
                        __OPCIONES_DEPORTE__
                    </select>
                </div>
                <div class="field">
                    <label for="mercados">Mercados</label>
                    <select id="mercados" name="mercados">
                        __OPCIONES_MERCADOS__
                    </select>
                </div>
                <div class="field">
                    <label for="partido">Partido</label>
                    <select id="partido" name="partido">
                        __OPCIONES_PARTIDOS__
                    </select>
                </div>
                <div class="field">
                    <label>Filtro elite</label>
                    <label class="checkbox-row"><input type="checkbox" name="solo_elite" value="true" __CHECKED_ELITE__> Solo picks elite</label>
                </div>
                <div class="field">
                    <label>Filtro stakazos</label>
                    <label class="checkbox-row"><input type="checkbox" name="solo_stakazos" value="true" __CHECKED_STAKAZOS__> Solo stakazos</label>
                </div>
                <button type="submit">Buscar</button>
            </form>
            <div class="summary">
                <span>Bankroll: __BANKROLL__ EUR</span>
                <span>Perfil: __PERFIL__</span>
                <span>Modo: __MODO__</span>
                <span>Deporte: __DEPORTE__</span>
                <span>Mercados: __MERCADOS__</span>
                <span>Partido: __PARTIDO__</span>
                <span>Recomendadas: __TOTAL_RECOMENDADAS__</span>
                <span>Elite: __TOTAL_ELITE__</span>
                <span>Stakazos: __TOTAL_STAKAZOS__</span>
                <span>Premium: __TOTAL_PREMIUM__</span>
                <span>Snapshots: __SNAPSHOTS__</span>
            </div>
            __AVISO_MERCADOS__
        </div>
    """
    html = (
        html
        .replace("__PERFIL__", perfil_seguro)
        .replace("__MODO__", modo_seguro)
        .replace("__MENU_CSS__", menu_css())
        .replace("__MENU_HTML__", menu_html("informe"))
        .replace("__BANKROLL__", f"{bankroll:.2f}")
        .replace("__OPCIONES_PERFIL__", opciones_perfil)
        .replace("__OPCIONES_MODO__", opciones_modo)
        .replace("__OPCIONES_DEPORTE__", opciones_deporte)
        .replace("__OPCIONES_MERCADOS__", opciones_mercados)
        .replace("__OPCIONES_PARTIDOS__", opciones_partidos)
        .replace("__CHECKED_ELITE__", checked_elite)
        .replace("__CHECKED_STAKAZOS__", checked_stakazos)
        .replace("__DEPORTE__", deporte_seguro)
        .replace("__MERCADOS__", escape(etiqueta_filtro_mercado(filtro_mercados)))
        .replace(
            "__PARTIDO__",
            escape(
                next(
                    (item["label"] for item in partidos_select if item["id"] == partido_actual),
                    "Todos los partidos",
                )
            ),
        )
        .replace("__TOTAL_RECOMENDADAS__", str(data.get("total_recomendadas", 0)))
        .replace("__TOTAL_ELITE__", str(data.get("total_elite", 0)))
        .replace("__TOTAL_STAKAZOS__", str(data.get("total_stakazos", 0)))
        .replace("__TOTAL_PREMIUM__", str(data.get("total_premium", 0)))
        .replace("__SNAPSHOTS__", str(data.get("snapshots_guardados", 0)))
        .replace(
            "__AVISO_MERCADOS__",
            (
                (f'<p class="aviso">{escape(data["aviso_mercados"])}</p>' if data.get("aviso_mercados") else "")
                + (f'<p class="aviso">{escape(data["aviso_cobertura"])}</p>' if data.get("aviso_cobertura") else "")
            ),
        )
    )

    if not mejores:
        html += """
        <div class="card">
            <h2>No hay apuestas recomendadas ahora mismo</h2>
            <p>El modelo no detecta suficiente valor con los filtros actuales para este deporte.</p>
        </div>
        """
    else:
        titulo_mejores = "3 mejores opciones para el partido" if partido_actual != "todos" else "Mejores apuestas detectadas"
        html += f"<h2>{titulo_mejores}</h2>"

        for i, apuesta in enumerate(mejores, start=1):
            cuota_apuesta = apuesta.get("cuota_apuesta") or apuesta.get("cuota_pinnacle")
            cuota_ref = apuesta.get("cuota_referencia_pinnacle") or apuesta.get("cuota_pinnacle")
            ventaja = apuesta.get("ventaja_sobre_pinnacle")
            ventaja_txt = f"{ventaja * 100:.1f}%" if ventaja is not None else "N/D"
            tipo_label, tipo_valor = etiqueta_tipo_apuesta(apuesta)
            modelo_html = detalle_modelo_html(apuesta)
            analisis = escape(analisis_apuesta_texto(apuesta))
            condicion_apuesta = escape(que_tiene_que_pasar(apuesta))
            apuesta_token = escape(apuesta_form_token(apuesta), quote=True)
            clase_card = clase_card_apuesta(apuesta)
            badge_class = "badge-green" if "bet-green" in clase_card else "badge-yellow"
            badge_text = "Apostar" if "bet-green" in clase_card else "Apuesta prudente"
            html += f"""
            <div class="{clase_card}">
                <span class="badge {badge_class}">{badge_text}</span>
                <h3>{i}. {titulo_card_apuesta(apuesta)}</h3>
                <p><strong>Partido:</strong> {apuesta['partido_es']}</p>
                <p><strong>Casa de apuestas:</strong> {apuesta['casa']}</p>
                <p><strong>Seleccion exacta:</strong> {apuesta['equipo_es']}</p>
                <p><strong>Que tiene que pasar:</strong> {condicion_apuesta}</p>
                <p><strong>{tipo_label}:</strong> {tipo_valor}</p>
                <p><strong>Cuota apuesta:</strong> {cuota_apuesta}</p>
                <p><strong>Referencia Pinnacle:</strong> {cuota_ref}</p>
                <p><strong>Mejora sobre Pinnacle:</strong> {ventaja_txt}</p>
                <p><strong>Confianza:</strong> {apuesta['confianza']} ({apuesta['puntuacion_confianza']}/100)</p>
                <p><strong>Fiabilidad:</strong> {apuesta.get('reliability_tier', 'media')} ({apuesta.get('reliability_score', 0)}/100)</p>
                <p><strong>Calidad global:</strong> {apuesta.get('quality_score', 0)}/100 | Tier: {apuesta.get('elite_tier', 'seguimiento')}</p>
                <p><strong>Ajuste historico:</strong> {apuesta.get('historical_penalty_level', 'none')} ({apuesta.get('historical_penalty_score', 0)})</p>
                <p><strong>Cuota mínima aceptable:</strong> {apuesta['cuota_minima_aceptable']}</p>
                <p><strong>Margen de cuota:</strong> {apuesta['margen_cuota']}</p>
                <p><strong>Probabilidad mercado:</strong> {apuesta['probabilidad_mercado'] * 100:.1f}%</p>
                <p><strong>Probabilidad modelo:</strong> {apuesta['probabilidad_modelo'] * 100:.1f}%</p>
                {modelo_html}
                <p class="value">Valor esperado: {apuesta['valor_esperado'] * 100:.1f}%</p>
                <p class="stake">Riesgo recomendado: {apuesta['stake']}/5</p>
                <p><strong>Exposición:</strong> {apuesta['stake_pct_bankroll']}% del bankroll</p>
                <p><strong>Importe sugerido:</strong> {apuesta['importe_sugerido']} EUR</p>
                <p><strong>Veredicto:</strong> {apuesta['recomendacion_es']}</p>
                <p><strong>Motivo:</strong> {apuesta['motivo_es']}</p>
                <p><strong>Analisis:</strong> {analisis}</p>
                <form class="bet-actions" method="post" action="/tracking/registrar-apuesta-form">
                    <input type="hidden" name="recomendacion_token" value="{apuesta_token}">
                    <input type="hidden" name="origen" value="/mis-apuestas">
                    <div class="field">
                        <label>Importe real apostado</label>
                        <input name="importe_real" type="number" min="0.01" step="0.01" value="{apuesta['importe_sugerido']}" required>
                    </div>
                    <button type="submit">Registrar que la aposte</button>
                </form>
            </div>
            """

    html += "<h2>Descartadas principales</h2>"

    for apuesta in descartadas[:3]:
        cuota_apuesta = apuesta.get("cuota_apuesta") or apuesta.get("cuota_pinnacle")
        analisis = escape(analisis_apuesta_texto(apuesta))
        html += f"""
        <div class="{clase_card_apuesta(apuesta)} descartada">
            <span class="badge badge-red">No apostar</span>
            <p>
                <strong>{apuesta['equipo_es']}</strong> en {apuesta['partido_es']} ({apuesta['casa']})
                a cuota {cuota_apuesta} -> {apuesta['recomendacion_es']}
                ({apuesta['motivo_es']})
            </p>
            <p><strong>Analisis:</strong> {analisis}</p>
        </div>
        """

    html += f"""
        <div class="card">
            <h2>Lectura final</h2>
            <p>{escape(analisis_final_texto(mejores, descartadas))}</p>
        </div>
    """

    html += """
    </div>
    </body>
    </html>
    """

    return HTMLResponse(content=html, media_type="text/html; charset=utf-8")


@app.get("/pronosticos")
def pronosticos(
    bankroll: float | None = None,
    perfil: str = "moderado",
    modo: str = "comparador",
    mercados: str = "todo",
    partido: str = "todos",
    deporte: str = DEFAULT_SPORT,
    solo_stakazos: bool = False,
):
    data = apuestas_hoy(
        bankroll=bankroll,
        perfil=perfil,
        modo=modo,
        mercados=mercados,
        partido=partido,
        deporte=deporte,
        solo_elite=not solo_stakazos,
        solo_stakazos=solo_stakazos,
    )
    mensajes = []
    stakazos = [
        pick for pick in data.get("picks_elite", [])
        if str(pick.get("elite_tier") or "").lower() == "stakazo"
    ]

    for pick in data.get("picks_elite", []):
        mensajes.append(formatear_mensaje_telegram_pick(pick))

    resumen = (
        f"<b>PREDI IA | INFORME PREMIUM</b>\n"
        f"<b>Deporte:</b> {telegram_text(data.get('sport_label'))}\n"
        f"<b>Liga:</b> {telegram_text(data.get('league_label'))}\n"
        f"<b>Perfil:</b> {telegram_text(perfil_es(perfil if perfil in PERFILES_STAKE else 'moderado'))}\n"
        f"<b>Modo:</b> {telegram_text(modo_es(modo if modo in MODOS_INFORME else 'comparador'))}\n"
        f"<b>Picks elite:</b> {telegram_text(data.get('total_elite', 0))}\n"
        f"<b>Stakazos:</b> {telegram_text(len(stakazos))}\n"
        f"<b>Filtro:</b> {'Solo stakazos' if solo_stakazos else 'Elite y stakazos'}"
    )

    return {
        "canal": "premium",
        "deporte": data.get("sport_label"),
        "liga": data.get("league_label"),
        "criterio": data.get("criterio"),
        "resumen_telegram": resumen,
        "total_elite": data.get("total_elite", 0),
        "total_stakazos": len(stakazos),
        "solo_stakazos": solo_stakazos,
        "pronosticos": data.get("picks_elite", []),
        "mensajes_telegram": mensajes,
    }


@app.get("/telegram/test")
def telegram_test():
    token, chat_id = telegram_config()
    resultado = enviar_mensaje_telegram(
        "Prueba de conexion Telegram OK desde Betting Agent.",
        token=token,
        chat_id=chat_id,
    )
    return {
        "ok": True,
        "chat_id": chat_id,
        "telegram_result": resultado,
    }


@app.get("/telegram/test-botones")
def telegram_test_botones():
    token, chat_id = telegram_config()
    resultado = enviar_mensaje_telegram(
        "<b>Prueba de botones</b>\nSi ves botones debajo, Telegram esta renderizando inline keyboards correctamente.",
        token=token,
        chat_id=chat_id,
        reply_markup={
            "inline_keyboard": [
                [
                    {"text": "Boton 1", "callback_data": "pick:999:bet"},
                    {"text": "Boton 2", "callback_data": "pick:999:win"},
                ]
            ]
        },
    )
    return {
        "ok": True,
        "chat_id": chat_id,
        "telegram_result": resultado,
    }


@app.get("/telegram/enviar-pronosticos")
def telegram_enviar_pronosticos(
    bankroll: float | None = None,
    perfil: str = "moderado",
    modo: str = "comparador",
    mercados: str = "todo",
    partido: str = "todos",
    deporte: str = DEFAULT_SPORT,
    solo_stakazos: bool = False,
):
    return publicar_pronosticos_telegram(
        bankroll=bankroll,
        perfil=perfil,
        modo=modo,
        mercados=mercados,
        partido=partido,
        deporte=deporte,
        solo_stakazos=solo_stakazos,
    )

@app.get("/telegram/publicaciones")
def telegram_publicaciones(limit: int = 20):
    return {
        "publicaciones": listar_publicaciones_telegram(limit=limit),
    }


@app.get("/tracking/picks")
def tracking_picks(
    limit: int = 100,
    estado: str | None = None,
    elite_tier: str | None = None,
    solo_elite: bool = False,
    solo_stakazos: bool = False,
    sport_label: str | None = None,
    league_label: str | None = None,
    min_quality_score: int | None = None,
    min_reliability_score: int | None = None,
    order_by: str = "recientes",
):
    return {
        "picks": listar_picks(
            limit=limit,
            estado=estado,
            elite_tier=elite_tier,
            solo_elite=solo_elite,
            solo_stakazos=solo_stakazos,
            sport_label=sport_label,
            league_label=league_label,
            min_quality_score=min_quality_score,
            min_reliability_score=min_reliability_score,
            order_by=order_by,
        ),
    }


def parse_importe_form(valor: object) -> float:
    try:
        return float(str(valor).replace(",", "."))
    except (TypeError, ValueError) as exc:
        raise ValueError("Importe no valido") from exc


async def form_urlencoded(request: Request) -> dict[str, str]:
    body = (await request.body()).decode("utf-8")
    parsed = parse_qs(body, keep_blank_values=True)
    return {
        key: values[-1] if values else ""
        for key, values in parsed.items()
    }


@app.post("/tracking/apuestas")
def tracking_registrar_apuesta(payload: RegistroApuesta):
    try:
        pick = guardar_apuesta_real(payload.recomendacion, payload.importe_real)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return pick


@app.post("/tracking/registrar-apuesta-form")
async def tracking_registrar_apuesta_form(request: Request):
    form = await form_urlencoded(request)
    origen = str(form.get("origen") or "/mis-apuestas")

    try:
        recomendacion = apuesta_from_form_token(str(form.get("recomendacion_token") or ""))
        importe_real = parse_importe_form(form.get("importe_real"))
        guardar_apuesta_real(recomendacion, importe_real)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return RedirectResponse(url=origen, status_code=303)


@app.post("/tracking/picks/{pick_id}/importe")
def tracking_importe_pick(pick_id: int, payload: ImportePick):
    try:
        pick = actualizar_importe_pick(pick_id, payload.importe_real)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if pick is None:
        raise HTTPException(status_code=404, detail="Pick no encontrado")

    return pick


@app.post("/tracking/picks/{pick_id}/cuota")
def tracking_cuota_pick(pick_id: int, payload: CuotaPick):
    try:
        pick = actualizar_cuota_pick(pick_id, payload.cuota_real)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if pick is None:
        raise HTTPException(status_code=404, detail="Pick no encontrado")

    return pick


@app.post("/tracking/picks/{pick_id}/importe-form")
async def tracking_importe_pick_form(pick_id: int, request: Request):
    form = await form_urlencoded(request)

    try:
        importe_real = parse_importe_form(form.get("importe_real"))
        pick = actualizar_importe_pick(pick_id, importe_real)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if pick is None:
        raise HTTPException(status_code=404, detail="Pick no encontrado")

    return RedirectResponse(url="/mis-apuestas", status_code=303)


@app.post("/tracking/picks/{pick_id}/cuota-form")
async def tracking_cuota_pick_form(pick_id: int, request: Request):
    form = await form_urlencoded(request)

    try:
        cuota_real = parse_importe_form(form.get("cuota_real"))
        pick = actualizar_cuota_pick(pick_id, cuota_real)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if pick is None:
        raise HTTPException(status_code=404, detail="Pick no encontrado")

    return RedirectResponse(url="/mis-apuestas", status_code=303)



@app.get("/tracking/stats")
def tracking_stats():
    return estadisticas()


@app.get("/tracking/aprendizaje")
def tracking_aprendizaje():
    return aprendizaje()


@app.get("/tracking/dashboard-data")
def tracking_dashboard_data():
    return dashboard_data()


@app.post("/tracking/liquidar-auto")
def tracking_liquidar_auto(days_from: int = 3):
    marcadores = scores(days_from=days_from)
    return liquidar_picks_con_scores(marcadores)


@app.post("/tracking/picks/{pick_id}/resultado-form")
async def tracking_resultado_form(pick_id: int, request: Request):
    form = await form_urlencoded(request)
    resultado = str(form.get("resultado") or "").strip().lower()
    closing_raw = form.get("closing_odds")
    closing_odds = None

    if closing_raw not in {None, ""}:
        try:
            closing_odds = parse_importe_form(closing_raw)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        pick = actualizar_resultado(
            pick_id=pick_id,
            resultado=resultado,
            closing_odds=closing_odds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if pick is None:
        raise HTTPException(status_code=404, detail="Pick no encontrado")

    return RedirectResponse(url="/mis-apuestas", status_code=303)


@app.get("/bankroll")
def bankroll_actual():
    return {"bankroll": obtener_bankroll()}


@app.post("/bankroll")
def bankroll_actualizar(payload: BankrollPayload):
    try:
        bankroll = actualizar_bankroll(payload.bankroll)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"bankroll": bankroll}


@app.post("/bankroll-form")
async def bankroll_actualizar_form(request: Request):
    form = await form_urlencoded(request)

    try:
        bankroll = parse_importe_form(form.get("bankroll"))
        actualizar_bankroll(bankroll)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return RedirectResponse(url="/mis-apuestas", status_code=303)


@app.get("/mis-apuestas", response_class=HTMLResponse)
def mis_apuestas(
    estado: str | None = None,
    elite_tier: str | None = None,
    solo_elite: bool = False,
    solo_stakazos: bool = False,
    sport_label: str | None = None,
    league_label: str | None = None,
    min_quality_score: int = 0,
    min_reliability_score: int = 0,
    order_by: str = "recientes",
):
    picks = listar_picks(
        limit=300,
        estado=estado,
        elite_tier=elite_tier,
        solo_elite=solo_elite,
        solo_stakazos=solo_stakazos,
        sport_label=sport_label,
        league_label=league_label,
        min_quality_score=min_quality_score or None,
        min_reliability_score=min_reliability_score or None,
        order_by=order_by,
    )
    pendientes = [p for p in picks if p["estado"] == "pendiente"]
    cerradas = [p for p in picks if p["estado"] == "cerrada"]
    stats = estadisticas()
    premium = dashboard_data()
    bankroll = obtener_bankroll()
    checked_elite = "checked" if solo_elite else ""
    checked_stakazos = "checked" if solo_stakazos else ""
    sport_options = sorted({str((_raw_pick(p).get("sport_label") or p.get("sport_label") or "")).strip() for p in picks if (_raw_pick(p).get("sport_label") or p.get("sport_label"))})
    league_options = sorted({str((_raw_pick(p).get("league_label") or p.get("league_label") or "")).strip() for p in picks if (_raw_pick(p).get("league_label") or p.get("league_label"))})

    def pick_meta(pick: dict) -> tuple[str, float, float]:
        raw = _raw_pick(pick)
        tier = str(raw.get("elite_tier") or pick.get("elite_tier") or "seguimiento")
        quality = float(raw.get("quality_score") or pick.get("quality_score") or 0)
        reliability = float(raw.get("reliability_score") or pick.get("reliability_score") or 0)
        return tier, quality, reliability

    def tier_badge(tier: str) -> str:
        tier_norm = str(tier or "seguimiento").lower()
        badge_class = {
            "stakazo": "tier-stakazo",
            "elite": "tier-elite",
            "premium": "tier-premium",
            "seguimiento": "tier-seguimiento",
        }.get(tier_norm, "tier-seguimiento")
        return f'<span class="tier-badge {badge_class}">{escape(tier_norm)}</span>'

    def fila_pendiente(pick: dict) -> str:
        cuota = float(pick["cuota"] or 0)
        importe = float(pick["importe_sugerido"] or 0)
        retorno = round(importe * cuota, 2)
        tier, quality, reliability = pick_meta(pick)

        return f"""
        <tr>
            <td>{pick['id']}</td>
            <td>{escape(str(pick['partido']))}</td>
            <td>{escape(str(pick['equipo']))}</td>
            <td>{tier_badge(tier)}</td>
            <td>{escape(str(pick['mercado']))}</td>
            <td>{escape(str(pick['casa']))}</td>
            <td>{quality:.0f}</td>
            <td>{reliability:.0f}</td>
            <td>
                <form method="post" action="/tracking/picks/{pick['id']}/cuota-form" class="inline-form">
                    <input name="cuota_real" type="number" step="0.01" min="1.01" value="{cuota:.2f}">
                    <button type="submit">Guardar</button>
                </form>
            </td>
            <td>
                <form method="post" action="/tracking/picks/{pick['id']}/importe-form" class="inline-form">
                    <input name="importe_real" type="number" step="0.01" min="0.01" value="{importe:.2f}">
                    <button type="submit">Guardar</button>
                </form>
            </td>
            <td>{retorno:.2f}</td>
            <td>
                <form method="post" action="/tracking/picks/{pick['id']}/resultado-form" class="inline-form">
                    <input name="closing_odds" type="number" step="0.01" min="1.01" placeholder="Cuota cierre">
                    <button name="resultado" value="win" type="submit" class="win">Ganada</button>
                    <button name="resultado" value="loss" type="submit" class="loss">Perdida</button>
                    <button name="resultado" value="push" type="submit">Nula</button>
                </form>
            </td>
        </tr>
        """

    def fila_cerrada(pick: dict) -> str:
        cuota = float(pick["cuota"] or 0)
        tier, quality, reliability = pick_meta(pick)
        return f"""
        <tr>
            <td>{pick['id']}</td>
            <td>{escape(str(pick['partido']))}</td>
            <td>{escape(str(pick['equipo']))}</td>
            <td>{tier_badge(tier)}</td>
            <td>{escape(str(pick['mercado']))}</td>
            <td>{quality:.0f}</td>
            <td>{reliability:.0f}</td>
            <td>{float(pick['importe_sugerido'] or 0):.2f}</td>
            <td>
                <form method="post" action="/tracking/picks/{pick['id']}/cuota-form" class="inline-form">
                    <input name="cuota_real" type="number" step="0.01" min="1.01" value="{cuota:.2f}">
                    <button type="submit">Guardar</button>
                </form>
            </td>
            <td>{escape(str(pick['resultado']))}</td>
            <td>{float(pick['profit_loss'] or 0):.2f}</td>
        </tr>
        """

    pendientes_html = "".join(fila_pendiente(p) for p in pendientes) or (
        '<tr><td colspan="12">No hay apuestas pendientes.</td></tr>'
    )
    cerradas_html = "".join(fila_cerrada(p) for p in cerradas[:50]) or (
        '<tr><td colspan="10">No hay apuestas cerradas todavia.</td></tr>'
    )

    html = f"""
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Mis apuestas</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                background: #f4f6f8;
                color: #111;
                margin: 32px;
            }}
            .container {{
                max-width: 1180px;
                margin: auto;
            }}
            {menu_css()}
            .summary {{
                display: grid;
                grid-template-columns: repeat(4, minmax(0, 1fr));
                gap: 12px;
                margin: 18px 0;
            }}
            .metric {{
                background: white;
                border-radius: 8px;
                padding: 14px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.08);
            }}
            .metric strong {{
                display: block;
                color: #0a7a32;
                font-size: 22px;
                margin-top: 6px;
            }}
            .bankroll-form {{
                background: white;
                border-radius: 8px;
                padding: 14px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.08);
                margin-bottom: 18px;
                display: flex;
                gap: 10px;
                align-items: end;
                flex-wrap: wrap;
            }}
            .bankroll-form .field {{
                display: flex;
                flex-direction: column;
                gap: 6px;
            }}
            .filters {{
                background: white;
                border-radius: 8px;
                padding: 14px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.08);
                margin-bottom: 18px;
            }}
            .filters form {{
                display: grid;
                grid-template-columns: repeat(4, minmax(0, 1fr));
                gap: 10px;
                align-items: end;
            }}
            .filters .field {{
                display: flex;
                flex-direction: column;
                gap: 6px;
            }}
            label {{
                font-size: 13px;
                font-weight: bold;
                color: #334155;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                background: white;
                margin-bottom: 26px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.08);
            }}
            th, td {{
                padding: 10px;
                border-bottom: 1px solid #e2e8f0;
                text-align: left;
                vertical-align: top;
                font-size: 14px;
            }}
            th {{
                background: #0b1f3a;
                color: white;
            }}
            input {{
                height: 34px;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 0 8px;
                max-width: 120px;
            }}
            select {{
                height: 34px;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 0 8px;
            }}
            button {{
                height: 34px;
                border: 0;
                border-radius: 6px;
                padding: 0 10px;
                background: #0b1f3a;
                color: white;
                font-weight: bold;
                cursor: pointer;
            }}
            .inline-form {{
                display: flex;
                gap: 6px;
                flex-wrap: wrap;
            }}
            .checkbox-row {{
                display: flex;
                align-items: center;
                gap: 8px;
                min-height: 34px;
            }}
            .checkbox-row input {{
                height: auto;
            }}
            .tier-badge {{
                display: inline-block;
                padding: 4px 8px;
                border-radius: 999px;
                font-size: 12px;
                font-weight: bold;
                text-transform: uppercase;
            }}
            .tier-stakazo {{
                background: #dcfce7;
                color: #166534;
            }}
            .tier-elite {{
                background: #dbeafe;
                color: #1d4ed8;
            }}
            .tier-premium {{
                background: #ede9fe;
                color: #6d28d9;
            }}
            .tier-seguimiento {{
                background: #fef3c7;
                color: #92400e;
            }}
            .win {{
                background: #16a34a;
            }}
            .loss {{
                background: #dc2626;
            }}
            @media (max-width: 800px) {{
                body {{
                    margin: 16px;
                }}
                .summary {{
                    grid-template-columns: 1fr 1fr;
                }}
                .filters form {{
                    grid-template-columns: 1fr;
                }}
                table {{
                    display: block;
                    overflow-x: auto;
                }}
            }}
        </style>
    </head>
    <body>
    <div class="container">
        {menu_html("mis_apuestas")}
        <h1>Mis apuestas</h1>
        <form method="post" action="/bankroll-form" class="bankroll-form">
            <div class="field">
                <label>Bankroll actual</label>
                <input name="bankroll" type="number" min="0" step="0.01" value="{bankroll:.2f}">
            </div>
            <button type="submit">Actualizar bankroll</button>
        </form>
        <div class="filters">
            <form method="get" action="/mis-apuestas">
                <div class="field">
                    <label>Estado</label>
                    <select name="estado">
                        <option value="" {"selected" if not estado else ""}>Todos</option>
                        <option value="pendiente" {"selected" if estado == "pendiente" else ""}>Pendiente</option>
                        <option value="cerrada" {"selected" if estado == "cerrada" else ""}>Cerrada</option>
                    </select>
                </div>
                <div class="field">
                    <label>Tier</label>
                    <select name="elite_tier">
                        <option value="" {"selected" if not elite_tier else ""}>Todos</option>
                        <option value="stakazo" {"selected" if elite_tier == "stakazo" else ""}>Stakazo</option>
                        <option value="elite" {"selected" if elite_tier == "elite" else ""}>Elite</option>
                        <option value="premium" {"selected" if elite_tier == "premium" else ""}>Premium</option>
                        <option value="seguimiento" {"selected" if elite_tier == "seguimiento" else ""}>Seguimiento</option>
                    </select>
                </div>
                <div class="field">
                    <label>Orden</label>
                    <select name="order_by">
                        <option value="recientes" {"selected" if order_by == "recientes" else ""}>Mas recientes</option>
                        <option value="premium" {"selected" if order_by == "premium" else ""}>Premium</option>
                    </select>
                </div>
                <div class="field">
                    <label>Deporte</label>
                    <select name="sport_label">
                        <option value="" {"selected" if not sport_label else ""}>Todos</option>
                        {"".join(f'<option value="{escape(option)}" {"selected" if sport_label == option else ""}>{escape(option)}</option>' for option in sport_options)}
                    </select>
                </div>
                <div class="field">
                    <label>Liga</label>
                    <select name="league_label">
                        <option value="" {"selected" if not league_label else ""}>Todas</option>
                        {"".join(f'<option value="{escape(option)}" {"selected" if league_label == option else ""}>{escape(option)}</option>' for option in league_options)}
                    </select>
                </div>
                <div class="field">
                    <label>Quality minimo</label>
                    <input name="min_quality_score" type="number" min="0" max="100" step="1" value="{min_quality_score}">
                </div>
                <div class="field">
                    <label>Reliability minimo</label>
                    <input name="min_reliability_score" type="number" min="0" max="100" step="1" value="{min_reliability_score}">
                </div>
                <div class="field">
                    <label>Filtro elite</label>
                    <label class="checkbox-row"><input type="checkbox" name="solo_elite" value="true" {checked_elite}> Solo elite</label>
                </div>
                <div class="field">
                    <label>Filtro stakazos</label>
                    <label class="checkbox-row"><input type="checkbox" name="solo_stakazos" value="true" {checked_stakazos}> Solo stakazos</label>
                </div>
                <button type="submit">Aplicar filtros</button>
            </form>
        </div>
        <div class="summary">
            <div class="metric">Bankroll actual<strong>{bankroll:.2f} EUR</strong></div>
            <div class="metric">Pendientes<strong>{stats['picks_pendientes']}</strong></div>
            <div class="metric">Cerradas<strong>{stats['picks_cerrados']}</strong></div>
            <div class="metric">Beneficio<strong>{stats['beneficio']:.2f} EUR</strong></div>
            <div class="metric">ROI<strong>{stats['roi']:.2f}%</strong></div>
        </div>
        <h2>Resumen premium</h2>
        <div class="summary">
            <div class="metric">Stakazos pendientes<strong>{premium['solo_stakazos']['pendientes']}</strong></div>
            <div class="metric">Stakazos cerrados<strong>{premium['solo_stakazos']['cerradas']}</strong></div>
            <div class="metric">ROI stakazos<strong>{premium['solo_stakazos']['roi']:.2f}%</strong></div>
            <div class="metric">CLV stakazos<strong>{premium['solo_stakazos']['clv_medio'] if premium['solo_stakazos']['clv_medio'] is not None else 'N/D'}</strong></div>
            <div class="metric">Elite cerradas<strong>{premium['solo_elite']['cerradas']}</strong></div>
            <div class="metric">ROI elite<strong>{premium['solo_elite']['roi']:.2f}%</strong></div>
            <div class="metric">Seguimiento cerradas<strong>{premium['solo_seguimiento']['cerradas']}</strong></div>
            <div class="metric">ROI seguimiento<strong>{premium['solo_seguimiento']['roi']:.2f}%</strong></div>
        </div>
        <h2>Pendientes</h2>
        <table>
            <thead>
                <tr>
                    <th>ID</th>
                    <th>Partido</th>
                    <th>Apuesta</th>
                    <th>Tier</th>
                    <th>Mercado</th>
                    <th>Casa</th>
                    <th>Quality</th>
                    <th>Reliability</th>
                    <th>Cuota real</th>
                    <th>Importe real</th>
                    <th>Retorno bruto si gana</th>
                    <th>Resultado</th>
                </tr>
            </thead>
            <tbody>{pendientes_html}</tbody>
        </table>
        <h2>Cerradas recientes</h2>
        <table>
            <thead>
                <tr>
                    <th>ID</th>
                    <th>Partido</th>
                    <th>Apuesta</th>
                    <th>Tier</th>
                    <th>Mercado</th>
                    <th>Quality</th>
                    <th>Reliability</th>
                    <th>Importe</th>
                    <th>Cuota real</th>
                    <th>Resultado</th>
                    <th>Beneficio</th>
                </tr>
            </thead>
            <tbody>{cerradas_html}</tbody>
        </table>
    </div>
    </body>
    </html>
    """

    return HTMLResponse(content=html, media_type="text/html; charset=utf-8")


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    data = dashboard_data()
    resumen = data["resumen"]
    aprendizaje_info = data["aprendizaje"]

    def tabla(titulo: str, filas: list[dict]) -> str:
        if not filas:
            return f"<h2>{escape(titulo)}</h2><p>No hay datos todavia.</p>"

        rows = ""

        for fila in filas[:12]:
            quality_media = fila.get("quality_media")
            reliability_media = fila.get("reliability_media")
            clv_medio = fila.get("clv_medio")
            clv_positivo = fila.get("clv_positivo_pct")
            rows += f"""
            <tr>
                <td>{escape(str(fila['nombre']))}</td>
                <td>{fila['picks']}</td>
                <td>{fila['cerradas']}</td>
                <td>{fila['ganadas']}</td>
                <td>{fila['perdidas']}</td>
                <td>{fila['nulas']}</td>
                <td>{fila['apostado']:.2f}</td>
                <td>{fila['beneficio']:.2f}</td>
                <td>{fila['roi']:.2f}%</td>
                <td>{fila['hit_rate']:.2f}%</td>
                <td>{quality_media if quality_media is not None else 'N/D'}</td>
                <td>{reliability_media if reliability_media is not None else 'N/D'}</td>
                <td>{clv_medio if clv_medio is not None else 'N/D'}</td>
                <td>{clv_positivo if clv_positivo is not None else 'N/D'}</td>
            </tr>
            """

        return f"""
        <h2>{escape(titulo)}</h2>
        <table>
            <thead>
                <tr>
                    <th>Grupo</th>
                    <th>Picks</th>
                    <th>Cerradas</th>
                    <th>Ganadas</th>
                    <th>Perdidas</th>
                    <th>Nulas</th>
                    <th>Apostado</th>
                    <th>Beneficio</th>
                    <th>ROI</th>
                    <th>Acierto</th>
                    <th>Quality media</th>
                    <th>Reliability media</th>
                    <th>CLV medio</th>
                    <th>CLV positivo %</th>
                </tr>
            </thead>
            <tbody>{rows}</tbody>
        </table>
        """

    pendientes = ""

    for pick in data["pendientes"][:10]:
        pendientes += f"""
        <tr>
            <td>{pick['id']}</td>
            <td>{escape(str(pick['partido']))}</td>
            <td>{escape(str(pick['equipo']))}</td>
            <td>{escape(str(pick['mercado']))}</td>
            <td>{escape(str(pick['casa']))}</td>
            <td>{float(pick['importe_sugerido'] or 0):.2f}</td>
        </tr>
        """

    pendientes_html = (
        f"""
        <table>
            <thead>
                <tr>
                    <th>ID</th>
                    <th>Partido</th>
                    <th>Apuesta</th>
                    <th>Mercado</th>
                    <th>Casa</th>
                    <th>Importe</th>
                </tr>
            </thead>
            <tbody>{pendientes}</tbody>
        </table>
        """
        if pendientes
        else "<p>No hay picks pendientes.</p>"
    )

    html = f"""
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Dashboard de rendimiento</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                background: #f4f6f8;
                color: #111;
                margin: 40px;
            }}
            .container {{
                max-width: 1120px;
                margin: auto;
            }}
            {menu_css()}
            h1, h2 {{
                color: #0b1f3a;
            }}
            .grid {{
                display: grid;
                grid-template-columns: repeat(4, minmax(0, 1fr));
                gap: 12px;
                margin: 20px 0;
            }}
            .metric {{
                background: white;
                border-radius: 8px;
                padding: 16px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.08);
            }}
            .metric strong {{
                display: block;
                font-size: 24px;
                color: #0a7a32;
                margin-top: 6px;
            }}
            .aviso {{
                background: #fff3cd;
                padding: 15px;
                border-radius: 8px;
                margin-bottom: 20px;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                background: white;
                margin-bottom: 28px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.08);
            }}
            th, td {{
                padding: 10px;
                border-bottom: 1px solid #e2e8f0;
                text-align: left;
                font-size: 14px;
            }}
            th {{
                background: #0b1f3a;
                color: white;
            }}
            @media (max-width: 800px) {{
                body {{
                    margin: 18px;
                }}
                .grid {{
                    grid-template-columns: 1fr 1fr;
                }}
                table {{
                    display: block;
                    overflow-x: auto;
                }}
            }}
        </style>
    </head>
    <body>
    <div class="container">
        {menu_html("dashboard")}
        <h1>Dashboard de rendimiento</h1>
        <div class="aviso">
            Usa este panel para decidir donde subir o bajar stake. Con poca muestra, ROI y acierto pueden moverse mucho.
            Para cerrar automaticamente picks liquidables por marcador: <code>POST /tracking/liquidar-auto</code>.
        </div>
        <div class="grid">
            <div class="metric">Picks cerrados<strong>{resumen['picks_cerrados']}</strong></div>
            <div class="metric">Pendientes<strong>{resumen['picks_pendientes']}</strong></div>
            <div class="metric">Beneficio<strong>{resumen['beneficio']:.2f} EUR</strong></div>
            <div class="metric">ROI<strong>{resumen['roi']:.2f}%</strong></div>
            <div class="metric">Acierto<strong>{resumen['hit_rate']:.2f}%</strong></div>
            <div class="metric">Apostado<strong>{resumen['total_apostado']:.2f} EUR</strong></div>
            <div class="metric">Snapshots<strong>{resumen['snapshots_cuotas']}</strong></div>
            <div class="metric">CLV medio<strong>{resumen['clv_medio'] if resumen['clv_medio'] is not None else 'N/D'}</strong></div>
        </div>
        <h2>Filtro premium</h2>
        <div class="grid">
            <div class="metric">Stakazos detectados<strong>{data['solo_stakazos']['picks']}</strong></div>
            <div class="metric">Stakazos cerrados<strong>{data['solo_stakazos']['cerradas']}</strong></div>
            <div class="metric">ROI stakazos<strong>{data['solo_stakazos']['roi']:.2f}%</strong></div>
            <div class="metric">Fiabilidad media<strong>{data['solo_stakazos']['reliability_media'] if data['solo_stakazos']['reliability_media'] is not None else 'N/D'}</strong></div>
        </div>
        <h2>Comparativa por tier</h2>
        <div class="grid">
            <div class="metric">CLV stakazos<strong>{data['solo_stakazos']['clv_medio'] if data['solo_stakazos']['clv_medio'] is not None else 'N/D'}</strong></div>
            <div class="metric">Acierto stakazos<strong>{data['solo_stakazos']['hit_rate']:.2f}%</strong></div>
            <div class="metric">ROI elite<strong>{data['solo_elite']['roi']:.2f}%</strong></div>
            <div class="metric">ROI seguimiento<strong>{data['solo_seguimiento']['roi']:.2f}%</strong></div>
            <div class="metric">CLV elite<strong>{data['solo_elite']['clv_medio'] if data['solo_elite']['clv_medio'] is not None else 'N/D'}</strong></div>
            <div class="metric">CLV seguimiento<strong>{data['solo_seguimiento']['clv_medio'] if data['solo_seguimiento']['clv_medio'] is not None else 'N/D'}</strong></div>
            <div class="metric">CLV+ stakazos<strong>{data['solo_stakazos']['clv_positivo_pct'] if data['solo_stakazos']['clv_positivo_pct'] is not None else 'N/D'}</strong></div>
            <div class="metric">CLV+ elite<strong>{data['solo_elite']['clv_positivo_pct'] if data['solo_elite']['clv_positivo_pct'] is not None else 'N/D'}</strong></div>
        </div>
        <p>{escape(aprendizaje_info['lectura'])}</p>
        {tabla("Por deporte", data["por_deporte"])}
        {tabla("Por liga", data["por_liga"])}
        {tabla("Por mercado", data["por_mercado"])}
        {tabla("Por casa", data["por_casa"])}
        {tabla("Por perfil", data["por_perfil"])}
        {tabla("Por modelo", data["por_modelo"])}
        {tabla("Por tier", data["por_elite"])}
        {tabla("Solo stakazos por tier", [{"nombre": "stakazos", **data["solo_stakazos"]}])}
        <h2>Pendientes</h2>
        {pendientes_html}
    </div>
    </body>
    </html>
    """

    return HTMLResponse(content=html, media_type="text/html; charset=utf-8")


@app.post("/tracking/picks/{pick_id}/resultado")
def tracking_resultado(pick_id: int, payload: ResultadoPick):
    try:
        pick = actualizar_resultado(
            pick_id=pick_id,
            resultado=payload.resultado,
            closing_odds=payload.closing_odds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if pick is None:
        raise HTTPException(status_code=404, detail="Pick no encontrado")

    return pick
