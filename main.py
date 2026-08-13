import base64
import json
import os
import queue
import re
import subprocess
import threading
import uuid
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any
from urllib.parse import parse_qs, urlencode
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from app import providers as provider_layer
from app import sports as sports_layer
from app.ai_service import (
    enrich_picks_with_ai_narratives,
    generate_bet_slip_opinion_from_image,
    generate_publication_ai_summary,
    openai_available,
)
from app.audit import (
    generate_daily_audit_report,
    format_audit_report_telegram,
    format_audit_report_html,
)
from app.calibration import (
    generate_calibration_snapshot,
    format_calibration_report,
    get_penalty_factor_for_league,
    get_market_threshold_adjustment,
    get_tier_boost,
    get_model_confidence_multiplier,
)
from app.engine import ForecastEngine, ForecastRequest
from app.forecast_service import ForecastDependencies, run_forecast_request
from app.forecasting import (
    apply_market_regime_guard,
    attach_context_to_pick,
    enrich_pick_ranking,
    execution_score_for_pick,
    market_signal_label,
    ranking_score_for_pick,
    source_strength_for_context,
    stake_limit_text,
    standard_risk_disclaimer,
)
from app.operating_mode import (
    diversify_limits_for_todo,
    multi_sport_pick_limit,
    single_sport_pick_limit,
    telegram_pick_limit,
)
from app.evaluation_service import build_telegram_audit_summary, scores_for_pending_bot_picks as scores_for_pending_bot_picks_service
from app.exposure import apply_exposure_limits
from app.lab_service import build_empty_lab_run, build_lab_run, render_lab_run_html
from app.prediction_service import build_prediction_payload
from app.performance_guard_service import build_performance_guard, apply_performance_guard_to_pick
from app.publication_service import (
    fingerprint_pick as fingerprint_pick_service,
    publish_telegram_predictions,
    select_picks_for_telegram,
)
from app.recent_panel_service import (
    build_recent_form_panel,
    format_recent_form_panel_telegram,
    render_recent_form_panel_html,
)
from app.risk_controls import apply_risk_policy_to_pick, build_risk_policy
from app.safety_service import publication_guard_state
from app.runtime_settings import RuntimeSettings, load_runtime_settings
from app.telegram_service import (
    TelegramBotConfig,
    TelegramClient,
    format_pick_message,
    format_summary_message,
    telegram_keyboard_for_pick as telegram_keyboard_for_pick_service,
    telegram_kickoff_label,
    telegram_text as telegram_text_service,
    telegram_tier_label as telegram_tier_label_service,
)
from app.ui import premium_ui_css
from betting_model import (
    analizar_comparador_casas,
    analizar_partidos,
    probabilidad_implicita,
    valor_esperado,
)
from elo import obtener_elos
from tracking import _bool_pick_flag, _raw_pick, actualizar_bankroll, actualizar_cuota_pick, actualizar_importe_pick, actualizar_resultado, estadisticas, guardar_recomendaciones, listar_picks
from tracking import archivar_picks_pendientes, eliminar_picks_archivadas, eliminar_reset_historial_deporte, guardar_apuesta_real, guardar_reset_historial_deporte, guardar_setting, marcar_apuesta_real_pick, obtener_setting
from tracking import dashboard_data, guardar_snapshot_cuotas, aprendizaje, liquidar_picks_con_scores, listar_evaluaciones_picks, obtener_bankroll, penalizaciones_historicas
from tracking import _insertar_pick, conectar, guardar_recomendaciones_unicas, inicializar_db, listar_publicaciones_telegram, registrar_publicacion_telegram
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

LAB_INPUT_TIMEZONE = ZoneInfo("Europe/Madrid")


load_dotenv()
RUNTIME_SETTINGS = load_runtime_settings()

app = FastAPI()
telegram_scheduler_stop = threading.Event()
telegram_scheduler_thread: threading.Thread | None = None
telegram_updates_thread: threading.Thread | None = None
audit_scheduler_stop = threading.Event()
audit_scheduler_thread: threading.Thread | None = None
lab_publication_jobs: dict[str, dict[str, Any]] = {}
telegram_command_jobs: dict[str, dict[str, Any]] = {}
TODO_FILTERS_SETTING = "lab_todo_filters"
_opinion_catalog_cache: dict[str, Any] = {"expires_at": None, "sports": []}

ODDS_API_KEY = os.getenv("ODDS_API_KEY")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
SPORTSGAMEODDS_API_KEY = os.getenv("SPORTSGAMEODDS_API_KEY")
ODDS_PROVIDER = provider_layer.ODDS_PROVIDER
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
TELEGRAM_APUESTAS_BUILD_TIMEOUT_SECONDS = max(30, int(os.getenv("TELEGRAM_APUESTAS_BUILD_TIMEOUT_SECONDS", "180")))
TELEGRAM_APUESTAS_PUBLISH_TIMEOUT_SECONDS = max(30, int(os.getenv("TELEGRAM_APUESTAS_PUBLISH_TIMEOUT_SECONDS", "90")))
TELEGRAM_AUDIT_ENABLED = os.getenv("TELEGRAM_AUDIT_ENABLED", "true").strip().lower() in {"1", "true", "yes", "si", "on"}
TELEGRAM_AUDIT_HOUR = int(os.getenv("TELEGRAM_AUDIT_HOUR", "21"))  # 21:00 por defecto
RISK_OPERATING_MODE = os.getenv("RISK_OPERATING_MODE", "agresivo").strip().lower() or "agresivo"
SPORTSGAMEODDS_HOST = "https://api.sportsgameodds.com/v2"
SPORTSGAMEODDS_SPORT_ID = provider_layer.SPORTSGAMEODDS_SPORT_ID
SPORTSGAMEODDS_LEAGUE_ID = provider_layer.SPORTSGAMEODDS_LEAGUE_ID
SPORTSGAMEODDS_BOOKMAKERS = os.getenv("SPORTSGAMEODDS_BOOKMAKERS", "")
SPORTSGAMEODDS_MAX_EVENTS = int(os.getenv("SPORTSGAMEODDS_MAX_EVENTS", "25"))
API_FOOTBALL_HOST = "https://v3.football.api-sports.io"
API_FOOTBALL_LEAGUE = os.getenv("API_FOOTBALL_LEAGUE", "1")
API_FOOTBALL_SEASON = os.getenv("API_FOOTBALL_SEASON", "2026")
API_FOOTBALL_MAX_PAGES = int(os.getenv("API_FOOTBALL_MAX_PAGES", "1"))
DEFAULT_SPORT = sports_layer.DEFAULT_SPORT
PERFILES_STAKE = {"conservador", "moderado", "agresivo", "alto_riesgo"}
MODOS_INFORME = {"comparador", "pinnacle"}
FEATURED_MARKETS = {"h2h", "spreads", "totals"}
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
        "spreads",
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
    "handicap": ["spreads"],
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

TELEGRAM_APUESTAS_DEFAULTS = {
    "bankroll": 200.0,
    "perfil": "agresivo",
    "modo": "comparador",
    "mercados": "h2h,spreads,totals",
    "partido": "todos",
    "deporte": "todo",
    "solo_stakazos": False,
}


def resolve_build_sha() -> str:
    env_sha = str(os.getenv("APP_BUILD_SHA") or "").strip()
    if env_sha:
        return env_sha[:12]
    try:
        output = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        if output:
            return output[:12]
    except Exception:
        pass
    return "unknown"


APP_BUILD_SHA = resolve_build_sha()


def cargar_filtros_todo() -> dict[str, set[str]]:
    raw = obtener_setting(TODO_FILTERS_SETTING, "{}")
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        data = {}
    deportes = {
        str(item).strip().lower()
        for item in (data.get("disabled_sports") or [])
        if str(item).strip()
    }
    ligas = {
        str(item).strip().lower()
        for item in (data.get("disabled_leagues") or [])
        if str(item).strip()
    }
    mercados = {
        str(item).strip().lower()
        for item in (data.get("disabled_markets") or [])
        if str(item).strip()
    }
    mercados_por_deporte = {
        str(item).strip().lower()
        for item in (data.get("disabled_market_pairs") or [])
        if str(item).strip()
    }
    if mercados and not mercados_por_deporte:
        familias = list(TODO_LIMITS_BY_FAMILY.keys())
        mercados_por_deporte = {
            f"{family}::{market}"
            for family in familias
            for market in mercados
        }
    return {
        "disabled_sports": deportes,
        "disabled_leagues": ligas,
        "disabled_markets": set(),
        "disabled_market_pairs": mercados_por_deporte,
    }


def guardar_filtros_todo(
    *,
    disabled_sports: set[str],
    disabled_leagues: set[str],
    disabled_markets: set[str],
    disabled_market_pairs: set[str] | None = None,
) -> dict[str, set[str]]:
    market_pairs = {
        str(item).strip().lower()
        for item in (disabled_market_pairs or set())
        if str(item).strip()
    }
    payload = {
        "disabled_sports": sorted({str(item).strip().lower() for item in disabled_sports if str(item).strip()}),
        "disabled_leagues": sorted({str(item).strip().lower() for item in disabled_leagues if str(item).strip()}),
        "disabled_markets": [],
        "disabled_market_pairs": sorted(market_pairs),
    }
    guardar_setting(TODO_FILTERS_SETTING, json.dumps(payload, ensure_ascii=False))
    return {
        "disabled_sports": set(payload["disabled_sports"]),
        "disabled_leagues": set(payload["disabled_leagues"]),
        "disabled_markets": set(),
        "disabled_market_pairs": set(payload["disabled_market_pairs"]),
    }


def build_todo_toggle_groups(provider: str | None = None) -> dict[str, Any]:
    filtros = cargar_filtros_todo()
    disabled_sports = filtros["disabled_sports"]
    disabled_leagues = filtros["disabled_leagues"]
    disabled_market_pairs = filtros.get("disabled_market_pairs") or set()
    sports_items = [
        {"key": key, "label": info.get("sport_label", key), "enabled": key not in disabled_sports}
        for key, info in SPORT_CATALOG.items()
        if key in TODO_LIMITS_BY_FAMILY
    ]
    sports_items.sort(key=lambda item: item["label"])

    league_items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in opciones_deporte_disponibles(provider=provider):
        value = str(item.get("value") or "").strip().lower()
        if not value or value == "todo" or value in SPORT_CATALOG or value in seen:
            continue
        seen.add(value)
        league_items.append(
            {
                "key": value,
                "label": str(item.get("label") or value),
                "enabled": value not in disabled_leagues,
            }
        )
    league_items.sort(key=lambda item: item["label"])
    market_items: list[dict[str, Any]] = []
    for family in TODO_LIMITS_BY_FAMILY:
        family_label = SPORT_PREFIX_LABELS.get(family, family.title())
        sport_bucket = str(SPORT_ALIASES.get(family, family)).strip().lower()
        market_items.extend(
            {
                "key": f"{family}::{market}",
                "market_key": market,
                "family": family,
                "family_label": family_label,
                "sport_bucket": sport_bucket,
                "label": etiqueta_mercado_toggle(market),
                "enabled": f"{family}::{market}" not in disabled_market_pairs,
            }
            for market in sorted(MERCADOS_DISPONIBLES)
        )
    return {
        "disabled_sports": disabled_sports,
        "disabled_leagues": disabled_leagues,
        "disabled_markets": set(),
        "disabled_market_pairs": disabled_market_pairs,
        "sports": sports_items,
        "leagues": league_items,
        "markets": market_items,
    }


def _redirect_query_for_lab_filters(form: dict[str, Any]) -> dict[str, str]:
    query: dict[str, str] = {}
    fields = (
        "bankroll",
        "perfil",
        "modo",
        "mercados",
        "partido",
        "deporte",
        "simulation_mode",
        "snapshot_at",
        "snapshot_from",
        "snapshot_to",
    )
    for field in fields:
        value = str(form.get(field) or "").strip()
        if value:
            query[field] = value

    solo_stakazos = str(form.get("solo_stakazos") or "").strip().lower()
    query["solo_stakazos"] = "true" if solo_stakazos == "true" else "false"
    return query


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
        "default_markets": "h2h,spreads,totals",
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
        "default_filter": "todo",
        "allowed_filters": [
            "todo",
            "resultado",
            "h2h",
            "handicap",
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
    "handicap": "Handicap",
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
    return sports_layer.family_from_sport_key(sport_key)


def build_dynamic_context_from_sport_key(sport_key: str) -> dict:
    return sports_layer.build_dynamic_context_from_sport_key(sport_key)


def _is_generic_sport_alias(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"futbol", "tenis", "baloncesto"}


def _prefer_active_context_for_generic_alias(
    deporte: str | None,
    *,
    provider: str | None = None,
) -> dict | None:
    alias = str(deporte or "").strip().lower()
    if not _is_generic_sport_alias(alias):
        return None

    fallback = sports_layer.resolver_contexto_deporte(alias)
    target_family = family_from_sport_key(fallback.get("sport_key", ""))

    try:
        catalogo = discover_available_catalog(provider=provider)
    except Exception:
        return None

    candidates: list[dict[str, Any]] = []
    for item in catalogo.get("sports", []):
        sport_key = str(item.get("sport_key") or "").strip().lower()
        if family_from_sport_key(sport_key) != target_family:
            continue
        if item.get("active", True) is False:
            continue
        if "winner" in sport_key:
            continue

        context = build_dynamic_context_from_sport_key(sport_key)
        context["catalog_key"] = alias
        candidates.append(context)

    if not candidates:
        return None

    candidates.sort(key=prioridad_contexto_todo)
    return candidates[0]


def resolver_contexto_deporte(deporte: str | None) -> dict:
    preferred = _prefer_active_context_for_generic_alias(deporte)
    if preferred is not None:
        return preferred
    return sports_layer.resolver_contexto_deporte(deporte)


def prioridad_contexto_todo(contexto: dict) -> tuple:
    return sports_layer.prioridad_contexto_todo(contexto)


def deportes_agregados_para_todo(
    provider: str | None = None,
    *,
    max_total: int | None = TODO_MAX_TOTAL_LEAGUES,
    strict_family_limits: bool = True,
) -> list[str]:
    filtros = cargar_filtros_todo()
    disabled_sports = filtros["disabled_sports"]
    disabled_leagues = filtros["disabled_leagues"]
    candidatos: list[dict] = []
    fallback_genericos: list[dict] = []

    for item in opciones_deporte_disponibles(provider=provider):
        valor = str(item.get("value") or "").strip().lower()
        if not valor or valor == "todo":
            continue
        contexto = resolver_contexto_deporte(valor)
        catalog_key = str(contexto.get("catalog_key") or valor).strip().lower()
        family = family_from_sport_key(contexto.get("sport_key", ""))
        if family not in TODO_LIMITS_BY_FAMILY:
            continue
        sport_bucket = str(SPORT_ALIASES.get(family, family)).strip().lower()
        if sport_bucket in disabled_sports:
            continue
        if _is_generic_sport_alias(valor):
            fallback_genericos.append(contexto)
            continue
        if catalog_key in disabled_leagues:
            continue
        candidatos.append(contexto)

    if not candidatos:
        candidatos = [
            contexto
            for contexto in fallback_genericos
            if str(contexto.get("catalog_key") or "").strip().lower() not in disabled_leagues
        ]

    candidatos.sort(key=prioridad_contexto_todo)
    seleccionados: list[str] = []
    por_familia: dict[str, int] = {}

    max_items = max_total if max_total is not None else len(candidatos)

    for contexto in candidatos:
        if len(seleccionados) >= max_items:
            break
        family = family_from_sport_key(contexto.get("sport_key", ""))
        limite = TODO_LIMITS_BY_FAMILY.get(family, 0)
        if strict_family_limits and por_familia.get(family, 0) >= limite:
            continue
        seleccionados.append(str(contexto.get("catalog_key") or "").strip().lower())
        por_familia[family] = por_familia.get(family, 0) + 1

    if not strict_family_limits and len(seleccionados) < max_items:
        seen = set(seleccionados)
        for contexto in candidatos:
            if len(seleccionados) >= max_items:
                break
            catalog_key = str(contexto.get("catalog_key") or "").strip().lower()
            if not catalog_key or catalog_key in seen:
                continue
            seleccionados.append(catalog_key)
            seen.add(catalog_key)

    return seleccionados


def deportes_agregados_para_todo_ultracompacta(
    provider: str | None = None,
    *,
    max_total: int = 4,
) -> list[str]:
    filtros = cargar_filtros_todo()
    disabled_sports = set(filtros.get("disabled_sports") or set())
    disabled_leagues = set(filtros.get("disabled_leagues") or set())
    candidatos: list[dict[str, Any]] = []
    fallback_genericos: list[dict[str, Any]] = []

    try:
        catalogo = discover_available_catalog(provider=provider)
        source_items = [
            item
            for item in list(catalogo.get("sports") or [])
            if item.get("active", True) is not False
            and not bool(item.get("has_outrights"))
        ]
    except Exception:
        source_items = []

    if not source_items:
        source_items = [
            {**sports_layer.resolver_contexto_deporte(str(item.get("value") or "").strip().lower())}
            for item in opciones_deporte_disponibles(provider=provider)
            if str(item.get("value") or "").strip().lower() not in {"", "todo"}
        ]

    for contexto in source_items:
        valor = str(contexto.get("catalog_key") or contexto.get("sport_key") or "").strip().lower()
        if not valor or valor == "todo":
            continue
        catalog_key = str(contexto.get("catalog_key") or valor).strip().lower()
        family = family_from_sport_key(contexto.get("sport_key", ""))
        if family not in TODO_LIMITS_BY_FAMILY:
            continue
        sport_bucket = str(SPORT_ALIASES.get(family, family)).strip().lower()
        if sport_bucket in disabled_sports:
            continue
        if "winner" in catalog_key or "championship" in catalog_key or "outright" in catalog_key:
            continue
        if _is_generic_sport_alias(valor):
            if catalog_key in disabled_leagues:
                continue
            fallback_genericos.append(contexto)
            continue
        if catalog_key in disabled_leagues:
            continue
        candidatos.append(contexto)

    if not candidatos:
        candidatos = list(fallback_genericos)

    candidatos.sort(key=prioridad_contexto_todo)
    seleccionados: list[str] = []
    por_familia: dict[str, int] = {}

    # Primera pasada: asegurar variedad minima entre familias para que
    # el modo compacto no se cierre demasiado pronto sobre una sola familia.
    vistos_primera_pasada: set[str] = set()
    for contexto in candidatos:
        if len(seleccionados) >= max_total:
            break
        family = family_from_sport_key(contexto.get("sport_key", ""))
        if family in vistos_primera_pasada:
            continue
        limite = TODO_LIMITS_BY_FAMILY.get(family, 0)
        if por_familia.get(family, 0) >= limite:
            continue
        catalog_key = str(contexto.get("catalog_key") or "").strip().lower()
        if not catalog_key:
            continue
        seleccionados.append(catalog_key)
        por_familia[family] = por_familia.get(family, 0) + 1
        vistos_primera_pasada.add(family)

    for contexto in candidatos:
        if len(seleccionados) >= max_total:
            break
        family = family_from_sport_key(contexto.get("sport_key", ""))
        limite = TODO_LIMITS_BY_FAMILY.get(family, 0)
        if por_familia.get(family, 0) >= limite:
            continue
        catalog_key = str(contexto.get("catalog_key") or "").strip().lower()
        if not catalog_key or catalog_key in seleccionados:
            continue
        seleccionados.append(catalog_key)
        por_familia[family] = por_familia.get(family, 0) + 1

    return seleccionados


def enriquecer_eventos_contexto(eventos: list[dict], contexto: dict) -> list[dict]:
    return sports_layer.enriquecer_eventos_contexto(eventos, contexto)


def config_mercados_deporte(deporte: str | None) -> dict:
    return sports_layer.config_mercados_deporte(deporte)


def etiqueta_filtro_mercado(filtro: str) -> str:
    return sports_layer.etiqueta_filtro_mercado(filtro)


def etiqueta_mercado_toggle(mercado: str | None) -> str:
    market = str(mercado or "").strip().lower()
    labels = {
        "h2h": "Ganador",
        "spreads": "Handicap",
        "totals": "Totales",
        "alternate_totals": "Totales alternativos",
        "team_totals": "Totales por equipo",
        "alternate_team_totals": "Totales alternativos por equipo",
        "double_chance": "Doble oportunidad",
        "btts": "Ambos anotan",
        "totals_h1": "Totales 1a mitad / 1er set",
        "totals_h2": "Totales 2a mitad / 2o set",
        "corners_1x2": "Corners 1X2",
        "alternate_totals_corners": "Totales de corners",
        "alternate_team_totals_corners": "Corners por equipo",
        "alternate_totals_cards": "Totales de tarjetas",
        "alternate_spreads_cards": "Handicap de tarjetas",
        "alternate_spreads_corners": "Handicap de corners",
    }
    return labels.get(market, market or "Mercado")


def etiqueta_mercado_visible(mercado: str | None) -> str:
    market = str(mercado or "").strip().lower()
    return etiqueta_mercado_toggle(market)


def aplicar_filtros_mercados_todo(mercados: list[str]) -> list[str]:
    filtros = cargar_filtros_todo()
    disabled_markets = {str(item).strip().lower() for item in (filtros.get("disabled_markets") or set()) if str(item).strip()}
    if not disabled_markets:
        return list(mercados)
    filtrados = [mercado for mercado in mercados if str(mercado).strip().lower() not in disabled_markets]
    return filtrados


def aplicar_filtros_mercados_todo_por_deporte(mercados: list[str], deporte: str | None = None) -> list[str]:
    filtros = cargar_filtros_todo()
    disabled_pairs = {
        str(item).strip().lower()
        for item in (filtros.get("disabled_market_pairs") or set())
        if str(item).strip()
    }
    if not disabled_pairs:
        return list(mercados)
    contexto = resolver_contexto_deporte(deporte)
    family = family_from_sport_key(str(contexto.get("sport_key") or ""))
    if not family:
        return list(mercados)
    filtrados = [
        mercado
        for mercado in mercados
        if f"{family}::{str(mercado).strip().lower()}" not in disabled_pairs
    ]
    return filtrados


def telegram_text(value: Any) -> str:
    return telegram_text_service(value)


def telegram_tier_label(tier: str | None) -> str:
    return telegram_tier_label_service(tier)


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


def fingerprint_pick(pick: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return fingerprint_pick_service(pick)


def _diversified_telegram_picks(
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

    # Primera pasada: dar una oportunidad a cada deporte con su mejor pick.
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

    # Segunda pasada: completar por ranking sin dejar que un deporte monopolice el canal.
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

    # Último relleno por si no había suficiente variedad real.
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


def seleccionar_picks_para_telegram(
    data: dict[str, Any],
    solo_stakazos: bool = False,
    max_items: int | None = None,
) -> list[dict[str, Any]]:
    if max_items is None:
        max_items = telegram_pick_limit(RISK_OPERATING_MODE, solo_stakazos=solo_stakazos)
    return select_picks_for_telegram(
        data,
        solo_stakazos=solo_stakazos,
        max_items=max_items,
    )


def formatear_mensaje_telegram_pick(pick: dict) -> str:
    return format_pick_message(
        pick,
        title_builder=titulo_card_apuesta,
        type_label_builder=etiqueta_tipo_apuesta,
        condition_builder=que_tiene_que_pasar,
        penalty_summary_builder=resumir_penalizacion_historica,
    )


def telegram_config() -> tuple[str, str]:
    token = os.getenv("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN).strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID).strip()

    if not token:
        raise HTTPException(status_code=500, detail="Falta TELEGRAM_BOT_TOKEN en el archivo .env")
    if not chat_id:
        raise HTTPException(status_code=500, detail="Falta TELEGRAM_CHAT_ID en el archivo .env")

    return token, chat_id


def telegram_client(token: str | None = None, chat_id: str | None = None) -> TelegramClient:
    if token and chat_id:
        return TelegramClient(TelegramBotConfig(token=token.strip(), chat_id=chat_id.strip()))

    resolved_token, resolved_chat_id = telegram_config()
    return TelegramClient(TelegramBotConfig(token=resolved_token, chat_id=resolved_chat_id))


def telegram_api_request(
    method: str,
    payload: dict[str, Any] | None = None,
    token: str | None = None,
    timeout: int = 15,
    http_method: str = "post",
) -> dict:
    client = telegram_client(token=token, chat_id=os.getenv("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID))
    return client.api_request(method=method, payload=payload, timeout=timeout, http_method=http_method)


def enviar_mensaje_telegram(
    texto: str,
    token: str | None = None,
    chat_id: str | None = None,
    reply_markup: dict[str, Any] | None = None,
) -> dict:
    client = telegram_client(token=token, chat_id=chat_id)
    return client.send_message(texto, reply_markup=reply_markup)


def answer_callback_query_telegram(callback_query_id: str, text: str, token: str | None = None) -> dict:
    client = telegram_client(token=token, chat_id=os.getenv("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID))
    return client.answer_callback_query(callback_query_id, text)


def telegram_keyboard_for_pick(pick_id: int) -> dict[str, Any]:
    return telegram_keyboard_for_pick_service(pick_id)


def telegram_resolution_keyboard_for_pick(pick_id: int) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "Ganada", "callback_data": f"pick:{pick_id}:win"},
                {"text": "Perdida", "callback_data": f"pick:{pick_id}:loss"},
                {"text": "Nula", "callback_data": f"pick:{pick_id}:push"},
            ],
        ]
    }


def _format_real_pick_compact(pick: dict[str, Any], *, include_result: bool) -> str:
    pick_id = int(pick.get("id") or 0)
    partido = telegram_text_service(pick.get("partido") or "Partido")
    equipo = telegram_text_service(pick.get("equipo") or "Seleccion")
    liga = telegram_text_service(pick.get("league_label") or pick.get("sport_label") or "General")
    mercado = telegram_text_service(pick.get("mercado") or "mercado")
    cuota = telegram_text_service(pick.get("cuota") or pick.get("cuota_apuesta") or "-")
    stake = telegram_text_service(pick.get("stake") or "-")
    importe = telegram_text_service(pick.get("importe_sugerido") or "-")

    lines = [
        f"<b>#{pick_id} | {partido}</b>",
        f"🎯 <b>Pick:</b> {equipo}",
        f"📌 <b>Mercado:</b> {mercado}",
        f"🏟️ <b>Liga:</b> {liga}",
        f"💸 <b>Cuota:</b> {cuota} | <b>Stake:</b> {stake} | <b>Importe:</b> {importe}",
    ]

    if include_result:
        resultado = str(pick.get("resultado") or "").strip().lower()
        estado = str(pick.get("estado") or "").strip().lower()
        profit = pick.get("profit_loss")
        icon = "✅" if resultado == "win" else "❌" if resultado == "loss" else "➖" if resultado == "push" else "⏳"
        result_label = "ganada" if resultado == "win" else "perdida" if resultado == "loss" else "nula" if resultado == "push" else estado or "pendiente"
        profit_text = ""
        if profit not in {None, ""}:
            try:
                profit_text = f" | <b>P/L:</b> {float(profit):+.2f} EUR"
            except (TypeError, ValueError):
                profit_text = f" | <b>P/L:</b> {telegram_text_service(profit)}"
        lines.append(f"{icon} <b>Resultado:</b> {result_label}{profit_text}")

    return "\n".join(lines)


def _real_bets_pending(limit: int = 10) -> list[dict[str, Any]]:
    return listar_picks(limit=limit, estado="pendiente", apuesta_real=True)


def _published_pending_bets(limit: int = 10) -> list[dict[str, Any]]:
    return listar_picks(limit=limit, estado="pendiente", publicada_telegram=True)


def _real_bets_by_result(result: str, *, limit: int = 10) -> list[dict[str, Any]]:
    return [
        pick
        for pick in listar_picks(limit=max(limit * 3, 50), estado="cerrada", apuesta_real=True)
        if str(pick.get("resultado") or "").strip().lower() == result
    ][:limit]


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


def procesar_comando_telegram(command_text: str) -> str:
    command = str(command_text or "").strip().lower()

    if command.startswith("/resumen"):
        token, chat_id = telegram_config()
        client = telegram_client(token=token, chat_id=chat_id)
        report_text, report = construir_resumen_telegram(force_refresh_scores=True, lookback_hours=24, score_days=3)
        client.send_message(report_text)
        return (
            f"Resumen 24h enviado. ROI: {report['metrics']['roi']:+.2f}% | "
            f"Portfolio publicado: {report['model_portfolio']['all_time']['published']} picks."
        )

    if command.startswith("/mes"):
        token, chat_id = telegram_config()
        client = telegram_client(token=token, chat_id=chat_id)
        report_text, report = construir_resumen_telegram(force_refresh_scores=True, lookback_hours=24 * 30, score_days=30)
        client.send_message(report_text)
        return (
            f"Resumen 30 dias enviado. ROI: {report['metrics']['roi']:+.2f}% | "
            f"Portfolio publicado: {report['model_portfolio']['all_time']['published']} picks."
        )

    if command.startswith("/help"):
        token, chat_id = telegram_config()
        client = telegram_client(token=token, chat_id=chat_id)
        help_text = (
            "<b>Comandos disponibles</b>\n"
            "/help - ver esta ayuda\n"
            "/resumen - auditoria compacta de las ultimas 24h\n"
            "/mes - auditoria compacta de los ultimos 30 dias\n"
            "/panel - forma reciente del modelo\n"
            "/pendientes - apuestas reales pendientes de cerrar\n"
            "/ganadas - historico reciente de apuestas reales ganadas\n"
            "/perdidas - historico reciente de apuestas reales perdidas\n"
            "/apuestas - lanzar el preset del lab y publicar picks publicables\n"
            "/opinion - responde a una captura de apuesta o envia la foto con ese comando para que la IA la valore"
        )
        client.send_message(help_text)
        return "Ayuda enviada por Telegram."

    if command.startswith("/opinion"):
        token, chat_id = telegram_config()
        client = telegram_client(token=token, chat_id=chat_id)
        client.send_message(
            "<b>/opinion</b>\n"
            "Enviane una captura de la apuesta junto al comando <code>/opinion</code>, "
            "o responde con <code>/opinion</code> a una foto ya enviada.\n"
            "Te devolvere una lectura IA con valor, fiabilidad, riesgo y veredicto."
        )
        return "Instrucciones de /opinion enviadas."

    if command.startswith("/panel"):
        token, chat_id = telegram_config()
        client = telegram_client(token=token, chat_id=chat_id)
        panel = build_recent_form_panel()
        client.send_message(format_recent_form_panel_telegram(panel))
        return f"Panel enviado. Evaluaciones: {int(panel.get('total_evaluations') or 0)}."

    if command.startswith("/pendientes"):
        token, chat_id = telegram_config()
        client = telegram_client(token=token, chat_id=chat_id)
        picks = _real_bets_pending(limit=10)
        if not picks:
            fallback_picks = _published_pending_bets(limit=10)
            if not fallback_picks:
                client.send_message("📭 <b>/pendientes</b>\nNo tienes apuestas reales pendientes ahora mismo.")
                return "Pendientes enviado. 0 apuestas."

            client.send_message(
                f"📋 <b>Pendientes del modelo</b>\n"
                f"No he encontrado apuestas reales pendientes, asi que te muestro las {len(fallback_picks)} picks pendientes publicadas mas recientes."
            )
            for pick in fallback_picks:
                client.send_message(
                    _format_real_pick_compact(pick, include_result=False),
                    reply_markup=telegram_resolution_keyboard_for_pick(int(pick["id"])),
                )
            return f"Pendientes enviado. {len(fallback_picks)} apuestas del modelo."

        client.send_message(
            f"📋 <b>Apuestas pendientes</b>\n"
            f"Te muestro las {len(picks)} apuestas reales pendientes mas recientes para cerrar rapido desde Telegram."
        )
        for pick in picks:
            client.send_message(
                _format_real_pick_compact(pick, include_result=False),
                reply_markup=telegram_resolution_keyboard_for_pick(int(pick["id"])),
            )
        return f"Pendientes enviado. {len(picks)} apuestas."

    if command.startswith("/ganadas"):
        token, chat_id = telegram_config()
        client = telegram_client(token=token, chat_id=chat_id)
        picks = _real_bets_by_result("win", limit=10)
        if not picks:
            client.send_message("✅ <b>/ganadas</b>\nNo hay apuestas reales ganadas en el historico reciente.")
            return "Ganadas enviado. 0 apuestas."

        lines = ["✅ <b>Ultimas ganadas</b>"]
        for pick in picks:
            lines.append(_format_real_pick_compact(pick, include_result=True))
            lines.append("")
        client.send_message("\n".join(lines).strip())
        return f"Ganadas enviado. {len(picks)} apuestas."

    if command.startswith("/perdidas"):
        token, chat_id = telegram_config()
        client = telegram_client(token=token, chat_id=chat_id)
        picks = _real_bets_by_result("loss", limit=10)
        if not picks:
            client.send_message("❌ <b>/perdidas</b>\nNo hay apuestas reales perdidas en el historico reciente.")
            return "Perdidas enviado. 0 apuestas."

        lines = ["❌ <b>Ultimas perdidas</b>"]
        for pick in picks:
            lines.append(_format_real_pick_compact(pick, include_result=True))
            lines.append("")
        client.send_message("\n".join(lines).strip())
        return f"Perdidas enviado. {len(picks)} apuestas."

    if command.startswith("/apuestas"):
        token, chat_id = telegram_config()
        client = telegram_client(token=token, chat_id=chat_id)
        job_id = lanzar_apuestas_telegram_async()
        client.send_message(
            "⏳ <b>/apuestas en marcha</b>\n"
            "Estoy buscando picks con el preset del lab y, si salen publicables, las envio al canal automaticamente.\n"
            f"Job: <code>{telegram_text_service(job_id)}</code>"
        )
        return f"/apuestas lanzado. Job {job_id}."

    return "Comando no soportado. Usa /help, /resumen, /mes, /panel, /pendientes, /ganadas, /perdidas, /apuestas o /opinion."


def construir_resumen_telegram(
    force_refresh_scores: bool = True,
    *,
    lookback_hours: int = 24,
    score_days: int = 3,
) -> tuple[str, dict[str, Any]]:
    return build_telegram_audit_summary(
        force_refresh_scores=force_refresh_scores,
        generate_report=lambda: generate_daily_audit_report(lookback_hours=lookback_hours),
        format_report=format_audit_report_telegram,
        refresh_scores=lambda days: scores_for_pending_bot_picks(days_from=days),
        liquidate_picks=liquidar_picks_con_scores,
        score_days=score_days,
    )


def scores_for_pending_bot_picks(days_from: int = 3) -> list[dict[str, Any]]:
    return scores_for_pending_bot_picks_service(
        days_from=days_from,
        list_picks=listar_picks,
        read_raw_pick=_raw_pick,
        bool_pick_flag=_bool_pick_flag,
        resolve_context=resolver_contexto_deporte,
        fetch_scores=scores,
        default_sport=DEFAULT_SPORT,
    )


def _telegram_message_command_text(message: dict[str, Any]) -> str:
    text = str(message.get("text") or "").strip()
    if text:
        return text
    return str(message.get("caption") or "").strip()


def _telegram_message_image_file(message: dict[str, Any]) -> tuple[str | None, str]:
    photos = list(message.get("photo") or [])
    if photos:
        photo = photos[-1] or {}
        file_id = str(photo.get("file_id") or "").strip()
        if file_id:
            return file_id, "image/jpeg"

    document = message.get("document") or {}
    mime_type = str(document.get("mime_type") or "").strip().lower()
    if mime_type.startswith("image/"):
        file_id = str(document.get("file_id") or "").strip()
        if file_id:
            return file_id, mime_type

    return None, "image/jpeg"


def _extract_opinion_user_notes(command_text: str) -> str:
    text = str(command_text or "").strip()
    if not text:
        return ""
    cleaned = re.sub(r"(?i)^/opinion(?:@\w+)?", "", text, count=1).strip()
    tokens = cleaned.split()
    if not tokens:
        return ""
    sport_hint = _extract_opinion_sport_hint(command_text)
    if sport_hint and tokens and sports_layer.SPORT_ALIASES.get(tokens[0].strip().lower()) == sport_hint:
        tokens = tokens[1:]
    return " ".join(tokens).strip()


def _extract_opinion_sport_hint(command_text: str) -> str | None:
    text = str(command_text or "").strip()
    if not text:
        return None
    cleaned = re.sub(r"(?i)^/opinion(?:@\w+)?", "", text, count=1).strip().lower()
    if not cleaned:
        return None
    first_token = cleaned.split()[0].strip()
    if not first_token:
        return None
    mapped = sports_layer.SPORT_ALIASES.get(first_token)
    return str(mapped or "").strip().lower() or None


def _normalize_name_for_match(value: str) -> str:
    text = str(value or "").strip().lower()
    replacements = {
        "á": "a",
        "à": "a",
        "ä": "a",
        "â": "a",
        "ã": "a",
        "é": "e",
        "è": "e",
        "ë": "e",
        "ê": "e",
        "í": "i",
        "ì": "i",
        "ï": "i",
        "î": "i",
        "ó": "o",
        "ò": "o",
        "ö": "o",
        "ô": "o",
        "õ": "o",
        "ú": "u",
        "ù": "u",
        "ü": "u",
        "û": "u",
        "ñ": "n",
        "ç": "c",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    text = re.sub(r"\b(fc|cf|sc|ac|cd|club|clube|esporte clube)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_matchup_from_opinion_text(text: str) -> tuple[str, str] | None:
    content = str(text or "").strip()
    if not content:
        return None

    match = re.search(r"(?im)^Partido:\s*(.+)$", content)
    if not match:
        match = re.search(r"(?im)^Apuesta detectada:\s*(.+?)\s*(?:,| cuota|$)", content)
        if not match:
            return None

    value = match.group(1).strip()
    for pattern in (r"\s+vs\s+", r"\s+v\s+", r"\s+-\s+"):
        parts = re.split(pattern, value, flags=re.IGNORECASE, maxsplit=1)
        if len(parts) == 2:
            left = parts[0].strip(" :,-")
            right = parts[1].strip(" :,-")
            if left and right:
                return left, right
    return None


def _opinion_candidate_contexts(sport_hint: str | None = None) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    cache_key = str(sport_hint or "auto").strip().lower() or "auto"
    cache_store = _opinion_catalog_cache.setdefault("by_hint", {})
    cache_entry = cache_store.get(cache_key) or {}
    expires_at = cache_entry.get("expires_at")
    cached = list(cache_entry.get("sports") or [])
    if isinstance(expires_at, datetime) and expires_at > now and cached:
        return cached

    try:
        catalog = provider_layer.discover_available_catalog(provider="the_odds_api")
    except Exception:
        return cached

    sports = []
    for item in list(catalog.get("sports") or []):
        sport_key = str(item.get("sport_key") or "")
        family = sports_layer.family_from_sport_key(sport_key)
        if sport_hint:
            mapped_hint = sports_layer.SPORT_ALIASES.get(sport_hint, sport_hint)
            target_family = {
                "futbol": "soccer",
                "tenis": "tennis",
                "baloncesto": "basketball",
                "basket": "basketball",
                "basketball": "basketball",
                "esports": "esports",
            }.get(mapped_hint, mapped_hint)
            if family != target_family:
                continue
        elif family not in {"soccer", "tennis", "basketball", "esports"}:
            continue
        if bool(item.get("has_outrights")):
            continue
        sports.append(item)

    sports.sort(key=sports_layer.prioridad_contexto_todo)
    limit_default = 6 if not sport_hint else 8
    limited = sports[: max(1, int(os.getenv("OPINION_ODDS_CONTEXTS_MAX", str(limit_default))))]
    cache_store[cache_key] = {
        "sports": limited,
        "expires_at": now + timedelta(hours=3),
    }
    return limited


def _find_real_event_for_opinion(analysis_text: str, sport_hint: str | None = None) -> dict[str, Any] | None:
    matchup = _extract_matchup_from_opinion_text(analysis_text)
    if not matchup:
        return None

    left_target = _normalize_name_for_match(matchup[0])
    right_target = _normalize_name_for_match(matchup[1])
    if not left_target or not right_target:
        return None

    contexts = _opinion_candidate_contexts(sport_hint=sport_hint)
    for context in contexts:
        try:
            events = provider_layer.fetch_the_odds_odds(["h2h"], context)
        except Exception:
            continue

        for event in events:
            home = str(event.get("home_team") or "").strip()
            away = str(event.get("away_team") or "").strip()
            home_norm = _normalize_name_for_match(home)
            away_norm = _normalize_name_for_match(away)
            if not home_norm or not away_norm:
                continue

            direct_match = left_target == home_norm and right_target == away_norm
            reverse_match = left_target == away_norm and right_target == home_norm
            soft_match = (
                (left_target in home_norm or home_norm in left_target)
                and (right_target in away_norm or away_norm in right_target)
            ) or (
                (left_target in away_norm or away_norm in left_target)
                and (right_target in home_norm or home_norm in right_target)
            )
            if not (direct_match or reverse_match or soft_match):
                continue

            return {
                "partido": f"{home} vs {away}",
                "liga": event.get("league_label") or context.get("league_label") or context.get("title") or "General",
                "deporte": event.get("sport_label") or context.get("sport_label") or "General",
                "commence_time": event.get("commence_time"),
            }
    return None


def _format_opinion_provider_context(event: dict[str, Any] | None) -> str:
    if not event:
        return ""
    kickoff = telegram_kickoff_label(event.get("commence_time"))
    lines = [
        "",
        "🧭 Contexto real detectado:",
        f"🏟️ Partido: {event.get('partido')}",
        f"🌍 Liga: {event.get('liga')}",
    ]
    if kickoff:
        lines.append(f"🕒 Hora: {kickoff}")
    return "\n".join(lines)


def _format_opinion_visual(text: str) -> str:
    icon_map = {
        "Partido:": "🏟️ ",
        "Apuesta detectada:": "🎯 ",
        "Valor:": "📈 ",
        "Fiabilidad:": "🛡️ ",
        "Riesgo principal:": "⚠️ ",
        "Veredicto:": "✅ ",
        "Cantidad sugerida:": "💶 ",
        "Lectura:": "🧠 ",
        "🧭 Contexto real detectado:": "",
        "🌍 Liga:": "",
        "🕒 Hora:": "",
    }
    formatted_lines: list[str] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        applied = False
        for prefix, icon in icon_map.items():
            if line.startswith(prefix):
                formatted_lines.append(f"{icon}{line}".strip())
                applied = True
                break
        if not applied:
            formatted_lines.append(line)
    return "\n".join(formatted_lines).strip()


def _handle_telegram_opinion_message(message: dict[str, Any], token: str) -> bool:
    command_text = _telegram_message_command_text(message)
    if not command_text.lower().startswith("/opinion"):
        return False

    token_config, chat_id = telegram_config()
    client = telegram_client(token=token or token_config, chat_id=chat_id)

    if not openai_available():
        client.send_message(
            "🤖 <b>/opinion</b>\n"
            "Ahora mismo no puedo analizar capturas porque OpenAI no esta configurado en este entorno."
        )
        return True

    file_id, mime_type = _telegram_message_image_file(message)
    if not file_id:
        reply_message = message.get("reply_to_message") or {}
        file_id, mime_type = _telegram_message_image_file(reply_message)

    if not file_id:
        client.send_message(
            "<b>/opinion</b>\n"
            "Necesito una captura de la apuesta. Puedes mandarla con el caption <code>/opinion</code> o responder <code>/opinion</code> a una foto."
        )
        return True

    notes = _extract_opinion_user_notes(command_text)
    sport_hint = _extract_opinion_sport_hint(command_text)
    client.send_message(
        "🧠 <b>/opinion en marcha</b>\n"
        "Estoy leyendo la captura y te doy una valoracion profesional en unos segundos."
    )
    try:
        image_bytes = client.download_file_bytes(file_id)
        analysis = generate_bet_slip_opinion_from_image(
            image_bytes,
            mime_type=mime_type or "image/jpeg",
            user_notes=notes,
        )
        opinion_event = _find_real_event_for_opinion(analysis or "", sport_hint=sport_hint)
    except HTTPException as exc:
        client.send_message(
            "🤖 <b>/opinion</b>\n"
            f"No pude analizar la captura.\nDetalle: <code>{telegram_text_service(str(exc.detail))}</code>"
        )
        return True
    except Exception as exc:
        client.send_message(
            "🤖 <b>/opinion</b>\n"
            f"No pude analizar la captura.\nDetalle: <code>{telegram_text_service(str(exc))}</code>"
        )
        return True

    if not analysis:
        client.send_message(
            "🤖 <b>/opinion</b>\n"
            "No he podido sacar una lectura util de la captura. Prueba con una imagen mas clara o recortada."
        )
        return True

    client.send_message(
        "🤖 <b>Opinion IA de la apuesta</b>\n"
        f"{telegram_text_service(_format_opinion_visual((analysis or '') + _format_opinion_provider_context(opinion_event)))}"
    )
    return True


def procesar_update_telegram(update: dict[str, Any], token: str) -> None:
    message = update.get("message") or {}
    if message and _handle_telegram_opinion_message(message, token):
        return
    client = telegram_client(token=token, chat_id=os.getenv("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID))
    client.process_update(update, procesar_callback_pick, procesar_comando_telegram)


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
                    "allowed_updates": ["callback_query", "message"],
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
    return publish_telegram_predictions(
        runtime_settings=RUNTIME_SETTINGS,
        publication_guard=lambda: publication_guard_state(
            runtime_settings=RUNTIME_SETTINGS,
            load_stats=estadisticas,
            load_learning=aprendizaje,
        ),
        pronosticos_fn=pronosticos,
        save_unique_recommendations=guardar_recomendaciones_unicas,
        read_raw_pick=_raw_pick,
        enrich_with_ai=enrich_picks_with_ai_narratives,
        build_ai_summary=generate_publication_ai_summary,
        ai_available=openai_available,
        format_summary=format_summary_message,
        format_pick_message=formatear_mensaje_telegram_pick,
        telegram_keyboard_for_pick=telegram_keyboard_for_pick,
        send_message=enviar_mensaje_telegram,
        register_publication=registrar_publicacion_telegram,
        perfil_label=perfil_es,
        modo_label=modo_es,
        perfiles_stake=PERFILES_STAKE,
        modos_informe=MODOS_INFORME,
        bankroll=bankroll,
        perfil=perfil,
        modo=modo,
        mercados=mercados,
        partido=partido,
        deporte=deporte,
        solo_stakazos=solo_stakazos,
        token=token,
        chat_id=chat_id,
        publication_type=publication_type,
    )


def publicar_pronosticos_lab(
    bankroll: float | None = None,
    perfil: str = "moderado",
    modo: str = "comparador",
    mercados: str = "todo",
    partido: str = "todos",
    deporte: str = DEFAULT_SPORT,
    solo_stakazos: bool = False,
) -> dict[str, Any]:
    token, chat_id = telegram_config()
    forced_live = RuntimeSettings(
        environment=RUNTIME_SETTINGS.environment,
        shadow_mode=False,
    )
    return publish_telegram_predictions(
        runtime_settings=forced_live,
        publication_guard=lambda: {
            "allow_live_publication": True,
            "mode": "manual_lab",
            "reasons": ["manual_lab_publish"],
            "stats": {},
        },
        pronosticos_fn=pronosticos,
        save_unique_recommendations=guardar_recomendaciones_unicas,
        read_raw_pick=_raw_pick,
        enrich_with_ai=enrich_picks_with_ai_narratives,
        build_ai_summary=generate_publication_ai_summary,
        ai_available=openai_available,
        format_summary=format_summary_message,
        format_pick_message=formatear_mensaje_telegram_pick,
        telegram_keyboard_for_pick=telegram_keyboard_for_pick,
        send_message=enviar_mensaje_telegram,
        register_publication=registrar_publicacion_telegram,
        perfil_label=perfil_es,
        modo_label=modo_es,
        perfiles_stake=PERFILES_STAKE,
        modos_informe=MODOS_INFORME,
        bankroll=bankroll,
        perfil=perfil,
        modo=modo,
        mercados=mercados,
        partido=partido,
        deporte=deporte,
        solo_stakazos=solo_stakazos,
        token=token,
        chat_id=chat_id,
        publication_type="lab",
    )


def pronosticos_compactos_para_apuestas(
    bankroll: float | None = None,
    perfil: str = "moderado",
    modo: str = "comparador",
    mercados: str = "todo",
    partido: str = "todos",
    deporte: str = DEFAULT_SPORT,
    solo_stakazos: bool = False,
) -> dict[str, Any]:
    built = construir_publicacion_apuestas_lab(
        bankroll=bankroll,
        perfil=perfil,
        modo=modo,
        mercados=mercados,
        partido=partido,
        deporte=deporte,
        solo_stakazos=solo_stakazos,
    )
    payload = dict(built.get("payload") or {})
    payload["zero_picks_diagnostics"] = dict(built.get("zero_picks_diagnostics") or {})
    return payload


def publicar_pronosticos_lab_compacto(
    bankroll: float | None = None,
    perfil: str = "moderado",
    modo: str = "comparador",
    mercados: str = "todo",
    partido: str = "todos",
    deporte: str = DEFAULT_SPORT,
    solo_stakazos: bool = False,
) -> dict[str, Any]:
    built = construir_publicacion_apuestas_lab(
        bankroll=bankroll,
        perfil=perfil,
        modo=modo,
        mercados=mercados,
        partido=partido,
        deporte=deporte,
        solo_stakazos=solo_stakazos,
    )
    payload = dict(built.get("payload") or {})
    picks_publicables = list(payload.get("pronosticos") or [])
    diagnostics = dict(built.get("zero_picks_diagnostics") or {})
    if not picks_publicables:
        return {
            "ok": True,
            "picks_guardados": 0,
            "mensajes_enviados": 0,
            "publication_id": None,
            "zero_picks_diagnostics": diagnostics,
        }
    result = publicar_payload_preparado(payload, publication_type="apuestas")
    result["zero_picks_diagnostics"] = diagnostics
    return result


def guardar_recomendaciones_directas_para_apuestas(
    recomendaciones: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not recomendaciones:
        return []

    inicializar_db()
    ahora = datetime.now(timezone.utc).isoformat()
    salida: list[dict[str, Any]] = []

    with conectar() as conn:
        for rec in recomendaciones:
            creado = _insertar_pick(conn, rec, created_at=ahora)
            if creado is not None:
                salida.append(creado)
        conn.commit()

    return salida


def registrar_publicacion_telegram_compacta(
    publication_type: str,
    payload: dict[str, Any],
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    slim_payload = {
        "deporte": payload.get("deporte"),
        "liga": payload.get("liga"),
        "criterio": payload.get("criterio"),
        "aviso_cobertura": payload.get("aviso_cobertura"),
        "ia_activa": bool(payload.get("ia_activa")),
        "total_elite": int(payload.get("total_elite") or 0),
        "total_stakazos": int(payload.get("total_stakazos") or 0),
        "total_analizadas": int(payload.get("total_analizadas") or 0),
        "total_recomendadas": int(payload.get("total_recomendadas") or 0),
        "snapshots_guardados": int(payload.get("snapshots_guardados") or 0),
        "solo_stakazos": bool(payload.get("solo_stakazos")),
        "resumen_telegram": payload.get("resumen_telegram"),
        "pronosticos": [
            {
                "event_id": pick.get("event_id"),
                "partido": pick.get("partido"),
                "equipo": pick.get("equipo"),
                "casa": pick.get("casa"),
                "mercado": pick.get("mercado"),
                "stake": pick.get("stake"),
                "importe_sugerido": pick.get("importe_sugerido"),
                "cuota_apuesta": pick.get("cuota_apuesta"),
                "quality_score": pick.get("quality_score"),
            }
            for pick in list(payload.get("pronosticos") or [])
        ],
    }
    return registrar_publicacion_telegram(
        publication_type=publication_type,
        payload=slim_payload,
        items=items,
    )


def publicar_payload_preparado(payload: dict[str, Any], publication_type: str = "lab") -> dict[str, Any]:
    token, chat_id = telegram_config()
    picks_publicables = list(payload.get("pronosticos", []))
    if not picks_publicables:
        return {
            "ok": True,
            "mensajes_enviados": 0,
            "picks_guardados": 0,
            "publication_id": None,
        }

    picks_guardados = guardar_recomendaciones_unicas(picks_publicables)
    picks_por_fingerprint = {
        fingerprint_pick_service(item): item
        for item in picks_guardados
    }

    picks_publicables = [
        {**pick, **(_raw_pick(picks_por_fingerprint.get(fingerprint_pick_service(pick), {})) if picks_por_fingerprint.get(fingerprint_pick_service(pick)) else {}), **(picks_por_fingerprint.get(fingerprint_pick_service(pick)) or {})}
        for pick in picks_publicables
    ]

    summary_text = str(payload.get("resumen_telegram") or "").strip()
    pick_messages = list(payload.get("mensajes_telegram") or [])
    if len(pick_messages) != len(picks_publicables):
        pick_messages = [formatear_mensaje_telegram_pick(pick) for pick in picks_publicables]
    messages = ([summary_text] if summary_text else []) + pick_messages

    sent_messages = []
    publication_items = []

    for index, text in enumerate(messages):
        reply_markup = None
        pick_id = None
        if summary_text and index > 0:
            pick = picks_publicables[index - 1]
        elif not summary_text:
            pick = picks_publicables[index]
        else:
            pick = None
        if pick is not None and pick.get("id"):
            pick_id = int(pick["id"])
            reply_markup = telegram_keyboard_for_pick(pick_id)

        result = enviar_mensaje_telegram(
            text,
            token=token,
            chat_id=chat_id,
            reply_markup=reply_markup,
        )
        sent_messages.append(result)

        publication_items.append(
            {
                "telegram_message_id": ((result.get("result") or {}).get("message_id")),
                "message_kind": "summary" if summary_text and index == 0 else "pick",
                "text": text,
                "pick_id": pick_id,
            }
        )

    publicacion = registrar_publicacion_telegram(
        publication_type=publication_type,
        payload=payload,
        items=publication_items,
    )
    return {
        "ok": True,
        "chat_id": chat_id,
        "mensajes_enviados": len(sent_messages),
        "picks_guardados": len(picks_guardados),
        "publication_id": publicacion.get("id"),
        "runtime_mode": "manual_lab",
    }


def publicar_payload_preparado_lab(payload: dict[str, Any]) -> dict[str, Any]:
    return publicar_payload_preparado(payload, publication_type="lab")


def _parse_apuestas_compact_commence(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        normalized = f"{text[:-1]}+00:00" if text.endswith("Z") else text
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def seleccionar_picks_para_apuestas_lab(
    data: dict[str, Any],
    solo_stakazos: bool = False,
) -> list[dict[str, Any]]:
    base = seleccionar_picks_para_telegram(
        data,
        solo_stakazos=solo_stakazos,
        max_items=6 if not solo_stakazos else 4,
    )
    if solo_stakazos:
        return base[:4]

    now = datetime.now(timezone.utc)
    max_hours_ahead = 48.0
    selector_reasons: dict[str, int] = {}

    def _bump_reason(reason: str) -> None:
        label = str(reason).strip()
        if not label:
            return
        selector_reasons[label] = selector_reasons.get(label, 0) + 1

    def _is_reasonable_pick(pick: dict[str, Any]) -> bool:
        cuota = float(pick.get("cuota_apuesta") or pick.get("cuota_pinnacle") or pick.get("cuota") or 0)
        quality = float(pick.get("quality_score") or 0)
        reliability = float(pick.get("reliability_score") or 0)
        commence = _parse_apuestas_compact_commence(pick.get("commence_time"))
        if commence is None:
            _bump_reason("Sin hora valida para publicacion")
            return False
        delta_hours = (commence - now).total_seconds() / 3600
        if delta_hours < 0 or delta_hours > max_hours_ahead:
            _bump_reason("Fuera de la ventana de 48h")
            return False
        if cuota <= 0 or cuota > 3.4:
            _bump_reason("Cuota fuera del rango operativo")
            return False
        if quality < 50:
            _bump_reason(f"Quality por debajo del minimo ({int(quality)}/50)")
            return False
        if not (quality >= 55 or reliability >= 68):
            _bump_reason(
                f"Filtro final Telegram insuficiente (quality {int(quality)} / reliability {int(reliability)})"
            )
            return False
        return True

    def _pick_sort_key(pick: dict[str, Any]) -> tuple[int, float, float, float]:
        commence = _parse_apuestas_compact_commence(pick.get("commence_time"))
        if commence is None:
            return (3, 9999.0, -float(pick.get("quality_score") or 0), -float(pick.get("reliability_score") or 0))
        delta_hours = (commence - now).total_seconds() / 3600
        if delta_hours <= 6:
            bucket = 0
        elif delta_hours <= 24:
            bucket = 1
        elif delta_hours <= 36:
            bucket = 2
        elif delta_hours <= max_hours_ahead:
            bucket = 3
        else:
            bucket = 4
        return (bucket, max(delta_hours, 0.0), -float(pick.get("quality_score") or 0), -float(pick.get("reliability_score") or 0))

    reasonable = [pick for pick in base if _is_reasonable_pick(pick)]
    data["_apuestas_selector_candidates"] = len(base)
    data["_apuestas_selector_rejections"] = [
        {"reason": reason, "count": count}
        for reason, count in sorted(selector_reasons.items(), key=lambda item: item[1], reverse=True)
    ]
    if not reasonable:
        return []
    reasonable.sort(key=_pick_sort_key)
    return reasonable[:3]


def _build_apuestas_zero_diagnostics_from_lab(lab_data: dict[str, Any]) -> dict[str, Any]:
    forecast_summary = dict(lab_data.get("forecast_summary") or {})
    forecast = dict(lab_data.get("forecast") or {})
    descartadas = list(forecast.get("descartadas") or [])
    reason_counter: dict[str, int] = {}
    for pick in descartadas:
        reason = str(pick.get("motivo_es") or pick.get("motivo") or "").strip()
        if not reason:
            continue
        reason_counter[reason] = reason_counter.get(reason, 0) + 1
    top_reasons = sorted(reason_counter.items(), key=lambda item: item[1], reverse=True)[:3]
    return {
        "analizadas": int(forecast_summary.get("total_analizadas") or 0),
        "recomendadas": int(forecast_summary.get("total_recomendadas") or 0),
        "descartadas_preview": int(forecast_summary.get("total_descartadas_preview") or 0),
        "partidos_disponibles": len(list(forecast.get("partidos_disponibles") or [])),
        "snapshots_guardados": int(forecast.get("snapshots_guardados") or 0),
        "coverage_notice": str(forecast.get("aviso_cobertura") or "").strip(),
        "base_criteria": str(forecast.get("criterio") or "").strip(),
        "blocked_summary": dict(forecast.get("blocked_summary") or {}),
        "top_discard_reasons": [
            {"reason": reason, "count": count}
            for reason, count in top_reasons
        ],
    }


def construir_publicacion_apuestas_lab(**kwargs) -> dict[str, Any]:
    bankroll = float(kwargs.get("bankroll") or TELEGRAM_APUESTAS_DEFAULTS.get("bankroll") or 200.0)
    perfil = str(kwargs.get("perfil") or "agresivo").strip() or "agresivo"
    modo = str(kwargs.get("modo") or "comparador").strip() or "comparador"
    mercados = str(kwargs.get("mercados") or "h2h,spreads,totals").strip() or "h2h,spreads,totals"
    partido = str(kwargs.get("partido") or "todos").strip() or "todos"
    deporte = str(kwargs.get("deporte") or "todo").strip() or "todo"
    solo_stakazos = bool(kwargs.get("solo_stakazos"))

    penalty_map = penalizaciones_historicas_seguras()
    risk_policy = politica_riesgo_actual()
    performance_guard = performance_guard_actual()
    max_pick_hours = 48.0

    if deporte == "todo":
        deportes_objetivo = deportes_agregados_para_todo(
            max_total=18,
            strict_family_limits=False,
        )
        sport_label = "Todo"
        league_label = "Todas las ligas base"
        criterio = "Agregado ampliado multi-deporte para Telegram"
    else:
        contexto = resolver_contexto_deporte(deporte)
        deportes_objetivo = [str(contexto.get("catalog_key") or deporte).strip().lower()]
        sport_label = str(contexto.get("sport_label") or "General")
        league_label = str(contexto.get("league_label") or sport_label)
        criterio = f"Analisis compacto de {league_label}"

    cobertura: list[dict[str, Any]] = []
    errores_cobertura: list[dict[str, str]] = []
    partidos_total: list[dict[str, Any]] = []
    recomendadas_total: list[dict[str, Any]] = []
    descartadas_total: list[dict[str, Any]] = []
    total_analizadas = 0
    elo_cache: dict[str, int] | None = None

    for deporte_item in deportes_objetivo:
        contexto = resolver_contexto_deporte(deporte_item)
        if deporte == "todo":
            mercados_lista, aviso_mercados = resolver_mercados_para_todo_filtrado(mercados, deporte=deporte_item)
        else:
            mercados_lista, aviso_mercados = resolver_mercados(mercados, deporte=deporte_item)
        if not mercados_lista:
            cobertura.append(
                {
                    "deporte": deporte_item,
                    "sport_label": contexto.get("sport_label"),
                    "league_label": contexto.get("league_label"),
                    "partidos": 0,
                    "recomendadas": 0,
                    "aviso_mercados": aviso_mercados,
                }
            )
            continue
        mercados_operativos = list(mercados_lista)

        try:
            data_partidos = cuotas(
                mercados=",".join(mercados_operativos),
                deporte=deporte_item,
            )
            partidos = filtrar_partidos(data_partidos, partido)
            partidos_dispo = partidos_disponibles(partidos)
            partidos_total.extend(
                [
                    {
                        **item,
                        "label": f"{contexto.get('league_label')} | {item.get('label')}",
                    }
                    for item in partidos_dispo
                ]
            )

            if source_strength_for_context(
                str(contexto.get("catalog_key") or ""),
                bool(contexto.get("supports_elo")),
            ) == "market+model":
                if elo_cache is None:
                    try:
                        elo_cache = obtener_elos()
                    except Exception:
                        elo_cache = {}
            elos = elo_cache or {}

            recomendaciones_crudas = analizar_comparador_casas(
                partidos,
                elos,
                bankroll=bankroll,
                perfil=perfil,
                casa_referencia=REFERENCE_BOOKMAKER,
                incluir_referencia=False,
                mercados=mercados_operativos,
                source_strength=source_strength_for_context(
                    str(contexto.get("catalog_key") or ""),
                    bool(contexto.get("supports_elo")),
                ),
            )
        except Exception as exc:
            errores_cobertura.append({"deporte": deporte_item, "detail": str(exc)})
            continue

        procesadas: list[dict[str, Any]] = []
        for rec in recomendaciones_crudas:
            pick = traducir_apuesta(rec)
            pick = attach_context_to_pick(
                pick,
                perfil=perfil,
                perfil_label=perfil_es(perfil),
                modo=modo,
                modo_label=modo_es(modo),
                filtro_mercados=mercados,
                contexto_deporte=contexto,
            )
            pick = aplicar_penalizacion_historica_segura_pick(pick, penalty_map)
            pick = apply_risk_policy_to_pick(
                pick,
                policy=risk_policy,
                league_penalties=None,
            )
            pick = apply_performance_guard_to_pick(pick, performance_guard)
            pick = enriquecer_pick_ranking_seguro(pick)
            procesadas.append(pick)

        total_analizadas += len(procesadas)
        recomendadas = [
            pick
            for pick in procesadas
            if float(pick.get("stake") or 0) > 0
            and str(pick.get("recomendacion") or "").strip().lower() != "no apostar"
        ]
        descartadas = [pick for pick in procesadas if pick not in recomendadas]
        recomendadas_total.extend(recomendadas)
        descartadas_total.extend(descartadas)
        cobertura.append(
            {
                "deporte": deporte_item,
                "sport_label": contexto.get("sport_label"),
                "league_label": contexto.get("league_label"),
                "partidos": len(partidos_dispo),
                "recomendadas": len(recomendadas),
                "aviso_mercados": aviso_mercados,
            }
        )

    recomendadas_ordenadas = []
    for pick in sorted(recomendadas_total, key=prioridad_pick_todo, reverse=True):
        delta_hours = hours_until_pick(pick)
        if delta_hours is None or delta_hours < 0 or delta_hours > max_pick_hours:
            continue
        recomendadas_ordenadas.append(pick)
    max_publicables = min(telegram_pick_limit(RISK_OPERATING_MODE, solo_stakazos=solo_stakazos), 4)
    recomendadas_ordenadas = apply_exposure_limits(
        recomendadas_ordenadas,
        operating_mode=RISK_OPERATING_MODE,
        max_total=max_publicables,
    )
    recomendadas_ordenadas = limitar_picks_todo(
        recomendadas_ordenadas,
        max_total=max_publicables,
        operating_mode=RISK_OPERATING_MODE,
    )

    elite = [pick for pick in recomendadas_ordenadas if bool(pick.get("elite_pick"))]
    stakazos = [pick for pick in elite if str(pick.get("elite_tier") or "").lower() == "stakazo"]
    premium = [pick for pick in recomendadas_ordenadas if str(pick.get("elite_tier") or "").lower() == "premium"]
    seguimiento = [pick for pick in recomendadas_ordenadas if str(pick.get("elite_tier") or "").lower() == "seguimiento"]

    blocked_summary = {
        "risk_count": len([pick for pick in descartadas_total if bool(pick.get("risk_guard_blocked"))]),
        "performance_count": len([pick for pick in descartadas_total if bool(pick.get("performance_guard_blocked"))]),
        "risk_reasons": [],
        "performance_reasons": [],
    }

    forecast = {
        "criterio": criterio,
        "aviso": standard_risk_disclaimer(),
        "proveedor_cuotas": ODDS_PROVIDER,
        "casa_referencia": REFERENCE_BOOKMAKER,
        "casa_referencia_fallback": False,
        "bankroll": bankroll,
        "perfil": perfil,
        "perfil_es": perfil_es(perfil),
        "modo": modo,
        "sport_key": "multi_sport" if deporte == "todo" else resolver_contexto_deporte(deporte).get("sport_key"),
        "sport_label": sport_label,
        "league_key": "multi_league" if deporte == "todo" else resolver_contexto_deporte(deporte).get("league_key"),
        "league_label": league_label,
        "deporte": "todo" if deporte == "todo" else deporte,
        "solo_elite": False,
        "solo_stakazos": solo_stakazos,
        "simulation_mode": "live",
        "historical_mode": False,
        "historical_snapshot_at": None,
        "historical_range_from": None,
        "historical_range_to": None,
        "source_strength": "mixed" if deporte == "todo" else source_strength_for_context(deporte, bool(resolver_contexto_deporte(deporte).get("supports_elo"))),
        "mercados": mercados,
        "filtro_mercados": mercados,
        "partido": partido,
        "partidos_disponibles": list({item["id"]: item for item in partidos_total if item.get("id")}.values()),
        "aviso_mercados": None,
        "aviso_cobertura": (
            f"Modo ampliado /apuestas: {len(deportes_objetivo)} ligas revisadas con mercados completos y filtro final de mejores picks en las proximas 48h."
        ),
        "cobertura_deportes": cobertura,
        "errores_cobertura": errores_cobertura,
        "snapshots_guardados": 0,
        "modo_es": modo_es(modo),
        "stake_maximo_por_pick": stake_limit_text(perfil),
        "total_analizadas": total_analizadas,
        "total_recomendadas": len(recomendadas_ordenadas),
        "total_elite": len(elite),
        "total_stakazos": len(stakazos),
        "total_premium": len(premium),
        "total_seguimiento": len(seguimiento),
        "total_guardadas": 0,
        "mejores_apuestas": recomendadas_ordenadas,
        "picks_elite": stakazos[:10] if solo_stakazos else elite[:10],
        "descartadas": sorted(descartadas_total, key=prioridad_pick, reverse=True)[:5],
        "descartadas_operativas": sorted(descartadas_total, key=prioridad_pick, reverse=True)[:25],
        "blocked_summary": blocked_summary,
    }

    payload = build_prediction_payload(
        data=forecast,
        solo_stakazos=solo_stakazos,
        ai_available=lambda: False,
        select_picks_for_telegram=seleccionar_picks_para_apuestas_lab,
        enrich_with_ai=lambda picks: picks,
        build_ai_summary=lambda *args, **inner_kwargs: None,
        format_pick_message=formatear_mensaje_telegram_pick,
        format_summary_message=format_summary_message,
        perfil=perfil,
        modo=modo,
        perfiles_stake=PERFILES_STAKE,
        modos_informe=MODOS_INFORME,
        perfil_label=perfil_es,
        modo_label=modo_es,
    )
    payload["pronosticos"] = list(payload.get("pronosticos") or [])
    payload["mensajes_telegram"] = list(payload.get("mensajes_telegram") or [])
    diagnostics = {
        "analizadas": int(forecast.get("total_analizadas") or 0),
        "recomendadas": int(forecast.get("total_recomendadas") or 0),
        "descartadas_preview": len(list(forecast.get("descartadas") or [])),
        "partidos_disponibles": len(list(forecast.get("partidos_disponibles") or [])),
        "snapshots_guardados": 0,
        "coverage_notice": str(forecast.get("aviso_cobertura") or "").strip(),
        "base_criteria": str(forecast.get("criterio") or "").strip(),
        "blocked_summary": dict(forecast.get("blocked_summary") or {}),
        "top_discard_reasons": _build_apuestas_zero_diagnostics_from_lab(
            {
                "forecast_summary": {
                    "total_analizadas": int(forecast.get("total_analizadas") or 0),
                    "total_recomendadas": int(forecast.get("total_recomendadas") or 0),
                    "total_descartadas_preview": len(list(forecast.get("descartadas") or [])),
                },
                "forecast": forecast,
            }
        ).get("top_discard_reasons", []),
        "publishable_preview": len(list(payload.get("pronosticos") or [])),
    }
    return {
        "lab_data": {
            "forecast": forecast,
            "forecast_summary": {
                "total_analizadas": int(forecast.get("total_analizadas") or 0),
                "total_recomendadas": int(forecast.get("total_recomendadas") or 0),
                "total_descartadas_preview": len(list(forecast.get("descartadas") or [])),
            },
            "telegram_preview": payload,
        },
        "payload": payload,
        "zero_picks_diagnostics": diagnostics,
    }


def _run_apuestas_phase_with_timeout(
    *,
    phase_name: str,
    timeout_seconds: int,
    fn,
):
    result_queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)

    def _target() -> None:
        try:
            result_queue.put(("ok", fn()))
        except Exception as exc:
            result_queue.put(("error", exc))

    worker = threading.Thread(target=_target, daemon=True)
    worker.start()
    join = getattr(worker, "join", None)
    if callable(join):
        join(timeout_seconds)
    is_alive = getattr(worker, "is_alive", None)
    if callable(is_alive) and is_alive():
        raise TimeoutError(
            f"La fase '{phase_name}' supero {timeout_seconds}s y se cancelo para evitar un bloqueo silencioso."
        )
    try:
        status, payload = result_queue.get_nowait()
    except queue.Empty as exc:
        raise RuntimeError(f"La fase '{phase_name}' termino sin devolver resultado.") from exc
    if status == "error":
        raise payload
    return payload


def iniciar_publicacion_lab_async(
    *,
    payload: dict[str, Any] | None,
    bankroll: float | None,
    perfil: str,
    modo: str,
    mercados: str,
    partido: str,
    deporte: str,
    solo_stakazos: bool,
) -> str:
    job_id = uuid.uuid4().hex[:12]
    lab_publication_jobs[job_id] = {
        "state": "queued",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    def _worker() -> None:
        try:
            lab_publication_jobs[job_id]["state"] = "running"
            if payload is not None:
                result = publicar_payload_preparado_lab(payload)
            else:
                result = publicar_pronosticos_lab(
                    bankroll=bankroll,
                    perfil=perfil,
                    modo=modo,
                    mercados=mercados,
                    partido=partido,
                    deporte=deporte,
                    solo_stakazos=solo_stakazos,
                )
            lab_publication_jobs[job_id] = {
                "state": "completed",
                "result": result,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as exc:
            lab_publication_jobs[job_id] = {
                "state": "error",
                "error": str(exc),
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }

    threading.Thread(target=_worker, daemon=True).start()
    return job_id


def lanzar_apuestas_telegram_async() -> str:
    token, chat_id = telegram_config()
    job_id = uuid.uuid4().hex[:12]
    telegram_command_jobs[job_id] = {
        "state": "queued",
        "command": "/apuestas",
        "created_at": datetime.now(timezone.utc).isoformat(),
        **TELEGRAM_APUESTAS_DEFAULTS,
    }

    def _worker() -> None:
        try:
            telegram_command_jobs[job_id]["state"] = "running"
            provider_layer.reset_odds_api_usage_tracking()
            telegram_command_jobs[job_id]["phase"] = "publishing_apuestas"
            result = publicar_pronosticos_lab_compacto(**TELEGRAM_APUESTAS_DEFAULTS)
            usage = provider_layer.get_odds_api_usage_tracking()
            telegram_command_jobs[job_id] = {
                **telegram_command_jobs[job_id],
                "state": "completed",
                "phase": "completed",
                "result": result,
                "odds_api_usage": usage,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }

            client = telegram_client(token=token, chat_id=chat_id)
            total_publicadas = int(result.get("picks_guardados") or 0)
            total_mensajes = int(result.get("mensajes_enviados") or 0)
            publication_id = result.get("publication_id")
            credits_used = int(usage.get("credits_used") or 0)
            calls = int(usage.get("calls") or 0)
            remaining = usage.get("last_remaining")
            usage_line = (
                f"Consumo API: <b>{credits_used}</b> creditos en <b>{calls}</b> llamadas"
                + (f"\nRestantes: <b>{remaining}</b>" if remaining is not None else "")
            )
            build_line = f"Build: <code>{telegram_text_service(APP_BUILD_SHA)}</code>"
            diagnostics = result.get("zero_picks_diagnostics") or {}

            if total_publicadas > 0:
                client.send_message(
                    "✅ <b>/apuestas completado</b>\n"
                    f"Picks publicadas: <b>{total_publicadas}</b>\n"
                    f"Mensajes enviados: <b>{total_mensajes}</b>\n"
                    f"Publication ID: <code>{telegram_text_service(publication_id or '-')}</code>\n"
                    f"{usage_line}\n"
                    f"{build_line}"
                )
            else:
                analyzed = int(diagnostics.get("analizadas") or 0)
                recommended = int(diagnostics.get("recomendadas") or 0)
                discarded = int(diagnostics.get("descartadas_preview") or 0)
                available_matches = int(diagnostics.get("partidos_disponibles") or 0)
                snapshots = int(diagnostics.get("snapshots_guardados") or 0)
                guard_reasons = list(diagnostics.get("guard_reasons") or [])
                discard_reasons = list(diagnostics.get("top_discard_reasons") or [])
                selector_candidates = int(diagnostics.get("selector_candidates") or 0)
                selector_rejections = list(diagnostics.get("selector_rejections") or [])
                coverage_notice = str(diagnostics.get("coverage_notice") or "").strip()
                base_criteria = str(diagnostics.get("base_criteria") or "").strip()
                blocked_summary = dict(diagnostics.get("blocked_summary") or {})
                detail_lines = [
                    f"Analizadas: <b>{analyzed}</b>",
                    f"Recomendadas antes del corte: <b>{recommended}</b>",
                    f"Descartadas visibles: <b>{discarded}</b>",
                    f"Partidos disponibles: <b>{available_matches}</b>",
                    f"Snapshots: <b>{snapshots}</b>",
                ]
                if selector_candidates:
                    detail_lines.append(f"Candidatas al filtro final: <b>{selector_candidates}</b>")
                if selector_rejections:
                    detail_lines.append(
                        "Filtro final Telegram: "
                        + " | ".join(
                            f"{telegram_text_service(str(item.get('reason') or 'Sin detalle'))} x{int(item.get('count') or 0)}"
                            for item in selector_rejections[:3]
                        )
                    )
                if guard_reasons:
                    detail_lines.append(
                        "Guard activo: " + " | ".join(telegram_text_service(str(reason)) for reason in guard_reasons)
                    )
                if discard_reasons:
                    detail_lines.append(
                        "Motivos top: "
                        + " | ".join(
                            f"{telegram_text_service(str(item.get('reason') or 'Sin detalle'))} x{int(item.get('count') or 0)}"
                            for item in discard_reasons
                        )
                    )
                risk_count = int(blocked_summary.get("risk_count") or 0)
                performance_count = int(blocked_summary.get("performance_count") or 0)
                if risk_count or performance_count:
                    detail_lines.append(
                        f"Bloqueos: risk <b>{risk_count}</b> | performance <b>{performance_count}</b>"
                    )
                risk_reasons = list(blocked_summary.get("risk_reasons") or [])
                if risk_reasons:
                    detail_lines.append(
                        "Risk top: "
                        + " | ".join(
                            f"{telegram_text_service(str(item.get('reason') or 'Sin detalle'))} x{int(item.get('count') or 0)}"
                            for item in risk_reasons
                        )
                    )
                performance_reasons = list(blocked_summary.get("performance_reasons") or [])
                if performance_reasons:
                    detail_lines.append(
                        "Performance top: "
                        + " | ".join(
                            f"{telegram_text_service(str(item.get('reason') or 'Sin detalle'))} x{int(item.get('count') or 0)}"
                            for item in performance_reasons
                        )
                    )
                elif coverage_notice:
                    detail_lines.append("Cobertura: " + telegram_text_service(coverage_notice))
                elif analyzed == 0 and base_criteria:
                    detail_lines.append("Contexto: " + telegram_text_service(base_criteria))
                client.send_message(
                    "ℹ️ <b>/apuestas sin picks publicables</b>\n"
                    "He ejecutado el preset del lab, pero en esta pasada no salio ninguna pick valida para publicar.\n"
                    + "\n".join(detail_lines)
                    + "\n"
                    f"{usage_line}\n"
                    f"{build_line}"
                )
        except Exception as exc:
            telegram_command_jobs[job_id] = {
                **telegram_command_jobs.get(job_id, {}),
                "state": "error",
                "phase": telegram_command_jobs.get(job_id, {}).get("phase") or "unknown",
                "error": str(exc),
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }
            try:
                client = telegram_client(token=token, chat_id=chat_id)
                client.send_message(
                    "❌ <b>/apuestas falló</b>\n"
                    f"No pude completar la publicacion automatica.\nDetalle: <code>{telegram_text_service(str(exc))}</code>"
                )
            except Exception:
                pass

    threading.Thread(target=_worker, daemon=True).start()
    return job_id


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


@app.get("/system/publication-guard")
def system_publication_guard():
    return publication_guard_state(
        runtime_settings=RUNTIME_SETTINGS,
        load_stats=estadisticas,
        load_learning=aprendizaje,
    )


@app.get("/system/performance-guard")
def system_performance_guard():
    return build_performance_guard(
        load_dashboard=dashboard_data,
        operating_mode=RISK_OPERATING_MODE,
    )


def telegram_scheduler_loop() -> None:
    while not telegram_scheduler_stop.is_set():
        try:
            auto_publicar_telegram_once()
        except Exception:
            pass

        if telegram_scheduler_stop.wait(TELEGRAM_AUTOPUBLISH_INTERVAL_HOURS * 3600):
            break


def send_audit_report_telegram() -> dict[str, Any]:
    """
    Genera y envía el reporte de auditoría diaria por Telegram.
    
    Se ejecuta una vez al día a la hora configurada (TELEGRAM_AUDIT_HOUR).
    """
    
    token, chat_id = telegram_config()
    
    if not token or not chat_id:
        return {"error": "Telegram no configurado"}
    
    try:
        report_text, report = construir_resumen_telegram(force_refresh_scores=True)
        config = TelegramBotConfig(token=token, chat_id=chat_id)
        client = TelegramClient(config)
        result = client.send_message(report_text)
        
        return {
            "status": "success",
            "telegram_result": result,
            "date": report["date"],
            "picks_closed": report["picks"]["closed"],
            "roi": report["metrics"]["roi"],
        }
    
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
        }


def audit_scheduler_loop() -> None:
    """
    Loop que envía el reporte de auditoría una vez al día a la hora configurada.
    """
    from datetime import time
    
    while not audit_scheduler_stop.is_set():
        try:
            now = datetime.now()
            target_hour = TELEGRAM_AUDIT_HOUR
            
            # Si la hora actual es igual a la target, enviar reporte
            if now.hour == target_hour:
                send_audit_report_telegram()
                
                # Esperar hasta que pase esta hora para no duplicar
                wait_seconds = 3600
            else:
                # Calcular tiempo hasta la próxima ejecución
                if now.hour < target_hour:
                    seconds_until = (target_hour - now.hour) * 3600 - now.minute * 60 - now.second
                else:
                    seconds_until = (24 - now.hour + target_hour) * 3600 - now.minute * 60 - now.second
                
                wait_seconds = min(seconds_until, 3600)  # Máximo 1 hora de espera
        
        except Exception:
            wait_seconds = 3600
        
        if audit_scheduler_stop.wait(wait_seconds):
            break


@app.on_event("startup")
def startup_event() -> None:
    global telegram_scheduler_thread, telegram_updates_thread, audit_scheduler_thread

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

    if TELEGRAM_AUTOPUBLISH_ENABLED:
        if not (telegram_scheduler_thread and telegram_scheduler_thread.is_alive()):
            telegram_scheduler_stop.clear()
            telegram_scheduler_thread = threading.Thread(
                target=telegram_scheduler_loop,
                name="telegram-autopublish",
                daemon=True,
            )
            telegram_scheduler_thread.start()
    
    # Iniciar scheduler de auditoría diaria
    if TELEGRAM_AUDIT_ENABLED and token and chat_id:
        if not (audit_scheduler_thread and audit_scheduler_thread.is_alive()):
            audit_scheduler_stop.clear()
            audit_scheduler_thread = threading.Thread(
                target=audit_scheduler_loop,
                name="audit-daily",
                daemon=True,
            )
            audit_scheduler_thread.start()


@app.on_event("shutdown")
def shutdown_event() -> None:
    telegram_scheduler_stop.set()
    audit_scheduler_stop.set()


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
        int(apuesta.get("ranking_score") or ranking_score_for_pick(apuesta)),
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


def hours_until_pick(apuesta: dict) -> float | None:
    commence = parse_commence_time(apuesta.get("commence_time"))
    if commence is None:
        return None
    return (commence - datetime.now(timezone.utc)).total_seconds() / 3600


def proximity_score_for_pick(apuesta: dict) -> int:
    delta_hours = hours_until_pick(apuesta)
    if delta_hours is None:
        return -9999

    if delta_hours < -3:
        return -500
    if delta_hours <= 1:
        return 56
    if delta_hours <= 3:
        return 48
    if delta_hours <= 6:
        return 38
    if delta_hours <= 12:
        return 28
    if delta_hours <= 18:
        return 20
    if delta_hours <= 24:
        return 12
    if delta_hours <= 36:
        return 5
    if delta_hours <= 48:
        return 1
    return 0


def prioridad_pick_todo(apuesta: dict) -> tuple:
    return (
        prioridad_tier_elite(apuesta.get("elite_tier")),
        int(apuesta.get("ranking_score") or ranking_score_for_pick(apuesta)),
        int(apuesta.get("reliability_score") or 0),
        int(apuesta.get("quality_score") or 0),
        proximity_score_for_pick(apuesta),
        int(apuesta.get("puntuacion_confianza") or 0),
        float(apuesta.get("valor_esperado") or 0),
        float(apuesta.get("margen_cuota") or 0),
    )


def limitar_picks_todo(recomendadas: list[dict], max_total: int = 6, operating_mode: str | None = None) -> list[dict]:
    limits = diversify_limits_for_todo(operating_mode or "equilibrado")
    max_total = min(max_total, limits["max_total"]) if max_total else limits["max_total"]
    seleccionadas: list[dict] = []
    por_liga: dict[str, int] = {}
    por_deporte: dict[str, int] = {}
    ordered = sorted(recomendadas, key=prioridad_pick_todo, reverse=True)

    def try_append(apuesta: dict) -> bool:
        liga = str(apuesta.get("league_label") or "General")
        deporte = str(apuesta.get("sport_label") or "General")
        if por_liga.get(liga, 0) >= limits["max_per_league"]:
            return False
        if por_deporte.get(deporte, 0) >= limits["max_per_sport"]:
            return False
        seleccionadas.append(apuesta)
        por_liga[liga] = por_liga.get(liga, 0) + 1
        por_deporte[deporte] = por_deporte.get(deporte, 0) + 1
        return True

    # Primera pasada: prioriza picks cercanas si mantienen nivel minimo de calidad.
    for apuesta in ordered:
        if len(seleccionadas) >= max_total:
            break
        delta_hours = hours_until_pick(apuesta)
        if delta_hours is None or delta_hours < -3 or delta_hours > 18:
            continue
        if int(apuesta.get("reliability_score") or 0) < 55:
            continue
        if int(apuesta.get("quality_score") or 0) < 50:
            continue
        try_append(apuesta)

    # Segunda pasada: completa con el resto del ranking sin perder picks validas.
    for apuesta in ordered:
        if len(seleccionadas) >= max_total:
            break
        if apuesta in seleccionadas:
            continue
        try_append(apuesta)

    return seleccionadas


def aplicar_penalizacion_historica(apuesta: dict, penalizaciones: dict[str, dict[str, Any]]) -> dict:
    apuesta = apuesta.copy()
    league_label = str(apuesta.get("league_label") or "")
    market_label = str(apuesta.get("mercado") or "")
    elite_tier = str(apuesta.get("elite_tier") or "seguimiento")
    penalty_items = []

    liga_penalty = penalizaciones.get("ligas", {}).get(league_label)
    if liga_penalty:
        penalty_items.append(("liga", league_label, liga_penalty))

    league_market_key = f"{league_label}::{market_label}"
    league_market_penalty = penalizaciones.get("ligas_mercados", {}).get(league_market_key)
    if league_market_penalty:
        penalty_items.append(("liga_mercado", league_market_key, league_market_penalty))

    tier_penalty = penalizaciones.get("tiers", {}).get(elite_tier)
    if tier_penalty:
        penalty_items.append(("tier", elite_tier, tier_penalty))

    if not penalty_items:
        apuesta["historical_penalty_score"] = 0
        apuesta["historical_penalty_level"] = "none"
        apuesta["historical_penalty_reasons"] = []
        apuesta["execution_score"] = execution_score_for_pick(apuesta)
        return enrich_pick_ranking(apuesta)

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

    apuesta["execution_score"] = execution_score_for_pick(apuesta)
    return enrich_pick_ranking(apuesta)


def politica_riesgo_actual() -> dict[str, Any]:
    try:
        resumen = estadisticas()
    except Exception:
        resumen = {}
    try:
        aprendizaje_info = aprendizaje()
    except Exception:
        aprendizaje_info = {}
    evaluated_sample = int(aprendizaje_info.get("picks_evaluadas") or 0)
    clv_positive_pct = aprendizaje_info.get("porcentaje_clv_positivo") if evaluated_sample > 0 else None
    roi = float(resumen.get("roi") or 0) if evaluated_sample >= 12 else 0.0
    clv_medio = resumen.get("clv_medio") if evaluated_sample >= 12 else None

    return build_risk_policy(
        total_closed=evaluated_sample,
        roi=roi,
        clv_medio=clv_medio,
        clv_positive_pct=clv_positive_pct,
        operating_mode=RISK_OPERATING_MODE,
    )


def performance_guard_actual() -> dict[str, Any]:
    try:
        return build_performance_guard(
            load_dashboard=dashboard_data,
            operating_mode=RISK_OPERATING_MODE,
        )
    except Exception:
        return {
            "operating_mode": RISK_OPERATING_MODE,
            "blocked_sports": {},
            "blocked_leagues": {},
            "overrides": {
                "allowed_sports": [],
                "allowed_leagues": [],
                "unblocked_sports": [],
                "unblocked_leagues": [],
                "performance_guard_disabled": True,
            },
            "thresholds": {},
        }


def obtener_bankroll_seguro(default: float = 200.0) -> float:
    try:
        return float(obtener_bankroll(default=default))
    except Exception:
        return float(default)


def actualizar_bankroll_seguro(bankroll: float) -> float:
    try:
        return float(actualizar_bankroll(bankroll))
    except Exception:
        return float(bankroll)


def penalizaciones_historicas_seguras() -> dict[str, Any]:
    try:
        return penalizaciones_historicas()
    except Exception:
        return {}


def enriquecer_pick_ranking_seguro(apuesta: dict[str, Any]) -> dict[str, Any]:
    ajustada = apply_market_regime_guard(apuesta.copy())
    ajustada["market_signal"] = market_signal_label(
        ajustada.get("market_support_count"),
        ajustada.get("market_width_pct"),
        ajustada.get("market_edge_vs_consensus"),
    )
    ajustada["execution_score"] = execution_score_for_pick(ajustada)
    ajustada["ranking_score"] = ranking_score_for_pick(ajustada)
    return ajustada


def aplicar_penalizacion_historica_segura_pick(
    apuesta: dict[str, Any],
    penalizaciones: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    apuesta = apuesta.copy()
    league_label = str(apuesta.get("league_label") or "")
    market_label = str(apuesta.get("mercado") or "")
    elite_tier = str(apuesta.get("elite_tier") or "seguimiento")
    penalty_items = []

    liga_penalty = penalizaciones.get("ligas", {}).get(league_label)
    if liga_penalty:
        penalty_items.append(("liga", league_label, liga_penalty))

    league_market_key = f"{league_label}::{market_label}"
    league_market_penalty = penalizaciones.get("ligas_mercados", {}).get(league_market_key)
    if league_market_penalty:
        penalty_items.append(("liga_mercado", league_market_key, league_market_penalty))

    tier_penalty = penalizaciones.get("tiers", {}).get(elite_tier)
    if tier_penalty:
        penalty_items.append(("tier", elite_tier, tier_penalty))

    if not penalty_items:
        apuesta["historical_penalty_score"] = 0
        apuesta["historical_penalty_level"] = "none"
        apuesta["historical_penalty_reasons"] = []
        return enriquecer_pick_ranking_seguro(apuesta)

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

    return enriquecer_pick_ranking_seguro(apuesta)


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
    return provider_layer.discover_available_catalog(provider=provider)


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
                "label": provider_layer.label_deporte_option(contexto),
            }
        )

    try:
        catalogo = discover_available_catalog(provider=provider)
        deportes = [
            item
            for item in catalogo.get("sports", [])
            if family_from_sport_key(item.get("sport_key", "")) in TODO_LIMITS_BY_FAMILY
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
    return provider_layer.adaptar_sportsgameodds_events(events, mercados_lista)


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
    return provider_layer.adaptar_api_football_odds(odds_items, fixtures, mercados_lista)


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
        apuesta.get("sport_key"),
        apuesta.get("sport_label"),
    )
    apuesta["tipo_resultado_es"] = tipo_resultado_es(
        apuesta.get("tipo_resultado"),
        apuesta.get("sport_key"),
        apuesta.get("sport_label"),
    )
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
            {menu_css()}
            .grid {{
                display: grid;
                grid-template-columns: repeat(2, minmax(0, 1fr));
                gap: 16px;
            }}
            .card a {{
                color: var(--brand);
                font-weight: 700;
            }}
            .card h2 {{
                margin: 0 0 10px;
                color: var(--brand);
            }}
            .card p {{
                color: var(--muted);
            }}
            .card-footer {{
                margin-top: 16px;
            }}
            @media (max-width: 900px) {{
                .grid {{
                    grid-template-columns: 1fr;
                }}
            }}
        </style>
    </head>
    <body>
    <div class="container">
        {menu_html("inicio")}
        <section class="hero">
            <div class="eyebrow">Quant betting workflow</div>
            <h1>Una mesa de analisis, no solo un script de picks.</h1>
            <p>Motor multi-deporte para detectar valor, contrastarlo con mercado y ELO, registrar ejecucion real y medir si tus mejores picks lo siguen siendo cuando pasa el tiempo.</p>
            <div class="hero-metrics">
                <div class="hero-metric"><span>Focus</span><strong>Value</strong></div>
                <div class="hero-metric"><span>Method</span><strong>Market + model</strong></div>
                <div class="hero-metric"><span>Tracking</span><strong>ROI + CLV</strong></div>
                <div class="hero-metric"><span>Output</span><strong>Elite tiers</strong></div>
            </div>
        </section>
        <div class="grid">
            <div class="card">
                <h2>Buscar apuestas</h2>
                <p>Informe operativo con filtros, razonamiento, score de fiabilidad, penalizacion historica y registro inmediato de apuesta real.</p>
                <div class="card-footer"><a class="button-link" href="/informe-hoy?perfil=alto_riesgo&modo=pinnacle&mercados=todo&partido=todos&deporte=worldcup">Abrir informe</a></div>
            </div>
            <div class="card">
                <h2>Mis apuestas</h2>
                <p>Gestiona ejecucion real, resultado, importe, tier y rendimiento de la cartera activa sin perder trazabilidad.</p>
                <div class="card-footer"><a class="button-link secondary" href="/mis-apuestas">Abrir mis apuestas</a></div>
            </div>
            <div class="card">
                <h2>Dashboard</h2>
                <p>Panel de rendimiento con ROI, hit rate, CLV, calidad media, fiabilidad media y comparativa por tier, mercado y liga.</p>
                <div class="card-footer"><a class="button-link secondary" href="/dashboard">Abrir dashboard</a></div>
            </div>
            <div class="card">
                <h2>API</h2>
                <p>Endpoints JSON para integrar discovery, cuotas, tracking, dashboard y automatizaciones con otros sistemas.</p>
                <div class="card-footer"><a class="button-link secondary" href="/docs">Abrir API Docs</a></div>
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
        "arquitectura": {
            "sports_module": "app/sports.py",
            "providers_module": "app/providers.py",
            "schemas_module": "app/schemas.py",
        },
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


def normalizar_snapshot_lab(value: str | None) -> tuple[str | None, str | None]:
    text = str(value or "").strip()
    if not text:
        return None, None

    try:
        normalized = f"{text[:-1]}+00:00" if text.endswith("Z") else text
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Fecha historica no valida para el lab.") from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=LAB_INPUT_TIMEZONE)

    parsed_utc = parsed.astimezone(timezone.utc).replace(microsecond=0)
    display_local = parsed.astimezone(LAB_INPUT_TIMEZONE).strftime("%Y-%m-%dT%H:%M")
    return parsed_utc.isoformat().replace("+00:00", "Z"), display_local


@app.get("/cuotas")
def cuotas(
    mercados: str = "h2h,totals",
    deporte: str | None = None,
    historical_date: str | None = None,
    historical_from: str | None = None,
    historical_to: str | None = None,
):
    mercados_lista = [m.strip() for m in mercados.split(",") if m.strip()]
    contexto = resolver_contexto_deporte(deporte)

    if historical_date:
        if ODDS_PROVIDER in {"sportsgameodds", "sports_game_odds", "sgo", "api_football", "api-football", "apifootball"}:
            raise HTTPException(
                status_code=400,
                detail="El modo historico del lab solo esta disponible con The Odds API como proveedor principal.",
            )
        mercados_historicos = [m for m in mercados_lista if m in FEATURED_MARKETS] or ["h2h"]
        try:
            return provider_layer.fetch_the_odds_historical_odds(
                mercados_historicos,
                contexto,
                historical_date,
                commence_time_from=historical_from,
                commence_time_to=historical_to,
            )
        except TypeError:
            return provider_layer.fetch_the_odds_historical_odds(mercados_historicos, contexto, historical_date)

    if ODDS_PROVIDER in {"sportsgameodds", "sports_game_odds", "sgo"}:
        return sports_layer.enriquecer_eventos_contexto(
            provider_layer.cuotas_sportsgameodds(mercados_lista),
            contexto,
        )

    if ODDS_PROVIDER in {"api_football", "api-football", "apifootball"}:
        return sports_layer.enriquecer_eventos_contexto(
            provider_layer.cuotas_api_football(mercados_lista),
            contexto,
        )

    return provider_layer.fetch_the_odds_odds(mercados_lista, contexto)


@app.get("/scores")
def scores(days_from: int = 3, deporte: str | None = None):
    contexto = resolver_contexto_deporte(deporte)

    if ODDS_PROVIDER in {"sportsgameodds", "sports_game_odds", "sgo"}:
        return sports_layer.enriquecer_eventos_contexto(
            provider_layer.scores_sportsgameodds(days_from=days_from),
            contexto,
        )

    if ODDS_PROVIDER in {"api_football", "api-football", "apifootball"}:
        return sports_layer.enriquecer_eventos_contexto(
            provider_layer.scores_api_football(days_from=days_from),
            contexto,
        )

    return provider_layer.fetch_the_odds_scores(days_from, contexto)


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
    catalogo = provider_layer.discover_available_catalog(provider=provider)
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
    filtro_raw = str(filtro or "").strip().lower()
    contexto = resolver_contexto_deporte(deporte)
    sport_key = str(contexto.get("sport_key") or "").lower()

    def _featured_markets_for_context() -> list[str]:
        if sport_key.startswith("basketball_"):
            return ["h2h", "spreads", "totals"]
        if sport_key.startswith("tennis_"):
            return ["h2h", "totals"]
        if sport_key.startswith("soccer_"):
            return ["h2h", "totals"]
        return ["h2h"]

    mercados_explicitos = [
        mercado.strip()
        for mercado in filtro_raw.split(",")
        if mercado.strip() in MERCADOS_DISPONIBLES
    ]

    if mercados_explicitos:
        permitidos = set(_featured_markets_for_context()) | ADDITIONAL_MARKETS
        mercados_filtrados = [mercado for mercado in mercados_explicitos if mercado in permitidos]
        if mercados_filtrados:
            return mercados_filtrados, None
        return _featured_markets_for_context(), (
            "Los mercados pedidos no aplican a este deporte; se usan los featured compatibles."
        )

    filtro_normalizado = filtro_raw if filtro_raw in config["allowed_filters"] else config["default_filter"]

    if filtro_normalizado == "todo":
        if sport_key.startswith("basketball_"):
            return ["h2h", "spreads", "totals", "alternate_totals"], None
        if sport_key.startswith("tennis_"):
            return ["h2h", "totals"], None
        if sport_key.startswith("soccer_"):
            return ["h2h", "totals", "alternate_totals"], None

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


def resolver_mercados_para_todo_filtrado(filtro: str, deporte: str | None = None) -> tuple[list[str], str | None]:
    mercados, aviso = resolver_mercados(filtro, deporte=deporte)
    filtrados = aplicar_filtros_mercados_todo_por_deporte(mercados, deporte=deporte)
    if filtrados:
        return filtrados, aviso
    return [], (
        "Todos los mercados compatibles para este contexto estan desactivados en el panel de deporte=todo."
    )


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
            "commence_time": partido.get("commence_time"),
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
    if sport_key.startswith("tennis_") and mercado in {"totals", "alternate_totals"}:
        return f"Apostar al total de juegos: {equipo.replace('goles', 'juegos')}"

    if sport_key.startswith("basketball_"):
        if mercado == "h2h":
            return f"Apostar a que {equipo} gana el partido"
        if mercado == "spreads":
            return f"Apostar al handicap: {equipo}"
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

    if mercado == "spreads":
        return f"Apostar al handicap: {equipo}"

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
    if sport_key.startswith("tennis_") and mercado in {"totals", "alternate_totals"}:
        if equipo_raw == "Over":
            return f"El partido debe superar la linea total de juegos de {point:g}." if point is not None else "El partido debe superar la linea total de juegos."
        if equipo_raw == "Under":
            return f"El partido debe quedar por debajo de la linea total de juegos de {point:g}." if point is not None else "El partido debe quedar por debajo de la linea total de juegos."

    if sport_key.startswith("basketball_"):
        if mercado == "h2h":
            return f"{equipo} debe ganar el partido segun el mercado moneyline de la casa."
        if mercado == "spreads":
            point_text = f"{abs(float(point)):.1f}" if point is not None else None
            if point is None:
                return f"{equipo} debe cubrir el handicap publicado por la casa."
            if float(point) < 0:
                return f"{equipo} debe ganar por mas de {point_text} puntos para cubrir el handicap."
            if float(point) > 0:
                return f"{equipo} puede ganar o perder por menos de {point_text} puntos para cubrir el handicap."
            return f"{equipo} debe cubrir el handicap 0, equivalente a ganar el partido."
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

    if mercado == "spreads":
        point_text = f"{abs(float(point)):.1f}" if point is not None else None
        if point is None:
            return f"{equipo} debe cubrir el handicap fijado por la casa."
        if float(point) < 0:
            return f"{equipo} debe ganar por mas de {point_text} unidades para cubrir el handicap."
        if float(point) > 0:
            return f"{equipo} puede ganar o perder por menos de {point_text} unidades para cubrir el handicap."
        return f"{equipo} debe cubrir el handicap 0."

    if mercado == "corners_1x2":
        return f"{equipo} debe sacar mas corners que el rival."

    return f"Debe cumplirse exactamente la seleccion indicada por la casa: {equipo}."


def etiqueta_tipo_apuesta(apuesta: dict) -> tuple[str, str]:
    mercado = apuesta.get("mercado")
    sport_key = str(apuesta.get("sport_key") or "")

    if mercado == "h2h":
        return "Resultado elegido", apuesta.get("tipo_resultado_es", "")

    if sport_key.startswith("tennis_") and mercado in {"totals", "alternate_totals", "team_totals"}:
        tipo = str(apuesta.get("tipo_resultado_es") or "")
        return "Mercado", tipo.replace("Goles", "Juegos").replace("goles", "juegos")

    if sport_key.startswith("basketball_") and mercado in {"totals", "alternate_totals", "team_totals", "totals_h1", "totals_h2"}:
        return "Mercado", apuesta.get("tipo_resultado_es", "")

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
    return premium_ui_css()


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
    historical_mode: bool = False,
    historical_date: str | None = None,
    historical_from: str | None = None,
    historical_to: str | None = None,
):
    resolve_markets_fn = resolver_mercados_para_todo_filtrado if str(deporte).strip().lower() == "todo" else resolver_mercados
    deps = ForecastDependencies(
        provider_name=ODDS_PROVIDER,
        reference_bookmaker=REFERENCE_BOOKMAKER,
        perfiles_stake=PERFILES_STAKE,
        modos_informe=MODOS_INFORME,
        get_bankroll=obtener_bankroll_seguro,
        update_bankroll=actualizar_bankroll_seguro,
        resolve_context=resolver_contexto_deporte,
        resolve_markets=resolve_markets_fn,
        list_sport_options=lambda: opciones_deporte_disponibles(),
        aggregate_sports=lambda: deportes_agregados_para_todo(
            max_total=24,
            strict_family_limits=False,
        ),
        fetch_odds=cuotas,
        list_matches=partidos_disponibles,
        save_snapshots=guardar_snapshot_cuotas,
        filter_matches=filtrar_partidos,
        fetch_elos=obtener_elos,
        select_reference_house=seleccionar_casa_referencia,
        analyze_comparison=analizar_comparador_casas,
        translate_pick=traducir_apuesta,
        historical_penalties=penalizaciones_historicas_seguras,
        apply_historical_penalty=aplicar_penalizacion_historica,
        sort_key_pick=prioridad_pick,
        sort_key_todo=prioridad_pick_todo,
        limit_todo_picks=lambda recomendadas, max_total: limitar_picks_todo(
            recomendadas,
            max_total=max_total,
            operating_mode=RISK_OPERATING_MODE,
        ),
        save_recommendations=guardar_recomendaciones,
        perfil_label=perfil_es,
        modo_label=modo_es,
        source_strength_for_context=source_strength_for_context,
        stake_limit_text=stake_limit_text,
        risk_disclaimer=standard_risk_disclaimer,
        attach_context_to_pick=attach_context_to_pick,
        build_risk_policy=politica_riesgo_actual,
        apply_risk_policy_to_pick=lambda pick, policy, league_penalties=None: apply_risk_policy_to_pick(
            pick,
            policy=policy,
            league_penalties=league_penalties,
        ),
        build_performance_guard=performance_guard_actual,
        apply_performance_guard_to_pick=apply_performance_guard_to_pick,
        apply_exposure_limits=lambda picks, max_total=None: apply_exposure_limits(
            picks,
            operating_mode=RISK_OPERATING_MODE,
            max_total=max_total,
        ),
        single_sport_pick_limit=lambda partido: single_sport_pick_limit(RISK_OPERATING_MODE, partido),
        multi_sport_pick_limit=lambda: multi_sport_pick_limit(RISK_OPERATING_MODE),
        run_single_request=lambda nested_request: apuestas_hoy(
            bankroll=nested_request.bankroll,
            perfil=nested_request.perfil,
            modo=nested_request.modo,
            mercados=nested_request.mercados,
            partido=nested_request.partido,
            guardar=nested_request.guardar,
            deporte=nested_request.deporte,
            solo_elite=nested_request.solo_elite,
            solo_stakazos=nested_request.solo_stakazos,
            historical_mode=nested_request.historical_mode,
            historical_date=nested_request.historical_date,
            historical_from=nested_request.historical_from,
            historical_to=nested_request.historical_to,
        ),
    )
    return run_forecast_request(
        ForecastRequest(
            bankroll=bankroll,
            perfil=perfil,
            modo=modo,
            mercados=mercados,
            partido=partido,
            guardar=guardar,
            deporte=deporte,
            solo_elite=solo_elite,
            solo_stakazos=solo_stakazos,
            historical_mode=historical_mode,
            historical_date=historical_date,
            historical_from=historical_from,
            historical_to=historical_to,
        ),
        deps,
    )


def apuestas_hoy_para_telegram_lab(
    bankroll: float | None = None,
    perfil: str = "moderado",
    modo: str = "comparador",
    mercados: str = "todo",
    partido: str = "todos",
    guardar: bool = False,
    deporte: str = DEFAULT_SPORT,
    solo_elite: bool = False,
    solo_stakazos: bool = False,
    historical_mode: bool = False,
    historical_date: str | None = None,
    historical_from: str | None = None,
    historical_to: str | None = None,
):
    resolve_markets_fn = resolver_mercados_para_todo_filtrado if str(deporte).strip().lower() == "todo" else resolver_mercados
    deps = ForecastDependencies(
        provider_name=ODDS_PROVIDER,
        reference_bookmaker=REFERENCE_BOOKMAKER,
        perfiles_stake=PERFILES_STAKE,
        modos_informe=MODOS_INFORME,
        get_bankroll=obtener_bankroll_seguro,
        update_bankroll=actualizar_bankroll_seguro,
        resolve_context=resolver_contexto_deporte,
        resolve_markets=resolve_markets_fn,
        list_sport_options=lambda: opciones_deporte_disponibles(),
        aggregate_sports=lambda: deportes_agregados_para_todo(
            max_total=10,
            strict_family_limits=True,
        ),
        fetch_odds=cuotas,
        list_matches=partidos_disponibles,
        save_snapshots=guardar_snapshot_cuotas,
        filter_matches=filtrar_partidos,
        fetch_elos=obtener_elos,
        select_reference_house=seleccionar_casa_referencia,
        analyze_comparison=analizar_comparador_casas,
        translate_pick=traducir_apuesta,
        historical_penalties=penalizaciones_historicas_seguras,
        apply_historical_penalty=aplicar_penalizacion_historica,
        sort_key_pick=prioridad_pick,
        sort_key_todo=prioridad_pick_todo,
        limit_todo_picks=lambda recomendadas, max_total: limitar_picks_todo(
            recomendadas,
            max_total=max_total,
            operating_mode=RISK_OPERATING_MODE,
        ),
        save_recommendations=guardar_recomendaciones,
        perfil_label=perfil_es,
        modo_label=modo_es,
        source_strength_for_context=source_strength_for_context,
        stake_limit_text=stake_limit_text,
        risk_disclaimer=standard_risk_disclaimer,
        attach_context_to_pick=attach_context_to_pick,
        build_risk_policy=politica_riesgo_actual,
        apply_risk_policy_to_pick=lambda pick, policy, league_penalties=None: apply_risk_policy_to_pick(
            pick,
            policy=policy,
            league_penalties=league_penalties,
        ),
        build_performance_guard=performance_guard_actual,
        apply_performance_guard_to_pick=apply_performance_guard_to_pick,
        apply_exposure_limits=lambda picks, max_total=None: apply_exposure_limits(
            picks,
            operating_mode=RISK_OPERATING_MODE,
            max_total=max_total,
        ),
        single_sport_pick_limit=lambda partido: single_sport_pick_limit(RISK_OPERATING_MODE, partido),
        multi_sport_pick_limit=lambda: multi_sport_pick_limit(RISK_OPERATING_MODE),
        run_single_request=lambda nested_request: apuestas_hoy(
            bankroll=nested_request.bankroll,
            perfil=nested_request.perfil,
            modo=nested_request.modo,
            mercados=nested_request.mercados,
            partido=nested_request.partido,
            guardar=nested_request.guardar,
            deporte=nested_request.deporte,
            solo_elite=nested_request.solo_elite,
            solo_stakazos=nested_request.solo_stakazos,
            historical_mode=nested_request.historical_mode,
            historical_date=nested_request.historical_date,
            historical_from=nested_request.historical_from,
            historical_to=nested_request.historical_to,
        ),
    )
    return run_forecast_request(
        ForecastRequest(
            bankroll=bankroll,
            perfil=perfil,
            modo=modo,
            mercados=mercados,
            partido=partido,
            guardar=guardar,
            deporte=deporte,
            solo_elite=solo_elite,
            solo_stakazos=solo_stakazos,
            historical_mode=historical_mode,
            historical_date=historical_date,
            historical_from=historical_from,
            historical_to=historical_to,
        ),
        deps,
    )


def apuestas_hoy_para_telegram_ultracompacta(
    bankroll: float | None = None,
    perfil: str = "moderado",
    modo: str = "comparador",
    mercados: str = "todo",
    partido: str = "todos",
    guardar: bool = False,
    deporte: str = DEFAULT_SPORT,
    solo_elite: bool = False,
    solo_stakazos: bool = False,
    historical_mode: bool = False,
    historical_date: str | None = None,
    historical_from: str | None = None,
    historical_to: str | None = None,
):
    bankroll_resuelto = float(bankroll if bankroll is not None else TELEGRAM_APUESTAS_DEFAULTS.get("bankroll", 200.0))

    def _resolve_featured_markets(
        filtro: str,
        deporte_actual: str | None = None,
        *,
        deporte: str | None = None,
    ) -> tuple[list[str], str | None]:
        deporte_final = deporte if deporte is not None else deporte_actual
        mercados_lista, aviso = resolver_mercados(filtro, deporte=deporte_final)
        featured = [market for market in mercados_lista if market in FEATURED_MARKETS] or ["h2h"]
        if featured != mercados_lista:
            extra_notice = "Modo compacto Telegram: se usan solo mercados featured para reducir consumo."
            aviso = f"{aviso} {extra_notice}".strip() if aviso else extra_notice
        return featured, aviso

    deps = ForecastDependencies(
        provider_name=ODDS_PROVIDER,
        reference_bookmaker=REFERENCE_BOOKMAKER,
        perfiles_stake=PERFILES_STAKE,
        modos_informe=MODOS_INFORME,
        get_bankroll=lambda: bankroll_resuelto,
        update_bankroll=lambda _: bankroll_resuelto,
        resolve_context=resolver_contexto_deporte,
        resolve_markets=_resolve_featured_markets,
        list_sport_options=lambda: opciones_deporte_disponibles(),
        aggregate_sports=lambda: deportes_agregados_para_todo_ultracompacta(
            max_total=4,
        ),
        fetch_odds=cuotas,
        list_matches=partidos_disponibles,
        save_snapshots=lambda _: 0,
        filter_matches=filtrar_partidos,
        fetch_elos=obtener_elos,
        select_reference_house=seleccionar_casa_referencia,
        analyze_comparison=analizar_comparador_casas,
        translate_pick=traducir_apuesta,
        historical_penalties=penalizaciones_historicas_seguras,
        apply_historical_penalty=aplicar_penalizacion_historica,
        sort_key_pick=prioridad_pick,
        sort_key_todo=prioridad_pick_todo,
        limit_todo_picks=lambda recomendadas, max_total: limitar_picks_todo(
            recomendadas,
            max_total=max_total,
            operating_mode=RISK_OPERATING_MODE,
        ),
        save_recommendations=guardar_recomendaciones,
        perfil_label=perfil_es,
        modo_label=modo_es,
        source_strength_for_context=source_strength_for_context,
        stake_limit_text=stake_limit_text,
        risk_disclaimer=standard_risk_disclaimer,
        attach_context_to_pick=attach_context_to_pick,
        build_risk_policy=politica_riesgo_actual,
        apply_risk_policy_to_pick=lambda pick, policy, league_penalties=None: apply_risk_policy_to_pick(
            pick,
            policy=policy,
            league_penalties=league_penalties,
        ),
        build_performance_guard=performance_guard_actual,
        apply_performance_guard_to_pick=apply_performance_guard_to_pick,
        apply_exposure_limits=lambda picks, max_total=None: apply_exposure_limits(
            picks,
            operating_mode=RISK_OPERATING_MODE,
            max_total=max_total,
        ),
        single_sport_pick_limit=lambda partido_actual: single_sport_pick_limit(RISK_OPERATING_MODE, partido_actual),
        multi_sport_pick_limit=lambda: min(telegram_pick_limit(RISK_OPERATING_MODE, solo_stakazos=solo_stakazos), 4),
        run_single_request=None,
    )
    return run_forecast_request(
        ForecastRequest(
            bankroll=bankroll,
            perfil=perfil,
            modo=modo,
            mercados=mercados,
            partido=partido,
            guardar=guardar,
            deporte=deporte,
            solo_elite=solo_elite,
            solo_stakazos=solo_stakazos,
            historical_mode=historical_mode,
            historical_date=historical_date,
            historical_from=historical_from,
            historical_to=historical_to,
        ),
        deps,
    )


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
            __MENU_CSS__
            .report-shell {
                max-width: 1180px;
                margin: 0 auto;
            }
            .report-grid {
                display: grid;
                grid-template-columns: minmax(0, 1.35fr) minmax(280px, 0.75fr);
                gap: 18px;
                align-items: start;
            }
            .report-sidebar {
                display: grid;
                gap: 16px;
            }
            .report-main > .card {
                margin-bottom: 18px;
            }
            .bet-card h3 {
                margin-top: 0;
                color: var(--brand);
            }
            .bet-card p {
                margin: 8px 0;
            }
            .kpi-stack {
                display: grid;
                gap: 12px;
            }
            .kpi-stack .metric {
                padding: 16px;
            }
            .kpi-stack .metric strong {
                font-size: 24px;
            }
            .section-card-title {
                margin: 0 0 8px;
                color: var(--brand);
            }
            .tiny-list {
                margin: 0;
                padding-left: 18px;
                color: var(--muted);
            }
            .bet-actions {
                display: flex;
                gap: 10px;
                align-items: end;
                flex-wrap: wrap;
                margin-top: 14px;
                padding-top: 14px;
                border-top: 1px solid var(--line);
            }
            .bet-actions .field {
                min-width: 170px;
            }
            .value {
                font-weight: 700;
                color: var(--success);
            }
            .descartada {
                color: var(--muted);
            }
            .stake {
                font-weight: 700;
            }
            @media (max-width: 980px) {
                .report-grid {
                    grid-template-columns: 1fr;
                }
            }
        </style>
    </head>
    <body>
    <div class="report-shell">
        __MENU_HTML__
        <section class="hero">
            <div class="eyebrow">Decision desk</div>
            <h1>Informe de apuestas</h1>
            <p>Lectura operacional del mercado para separar picks ejecutables de ruido, manteniendo disciplina de stake, sesgo conservador y trazabilidad real de resultados.</p>
            <div class="hero-metrics">
                <div class="hero-metric"><span>Perfil</span><strong>__PERFIL__</strong></div>
                <div class="hero-metric"><span>Modo</span><strong>__MODO__</strong></div>
                <div class="hero-metric"><span>Deporte</span><strong>__DEPORTE__</strong></div>
                <div class="hero-metric"><span>Recomendadas</span><strong>__TOTAL_RECOMENDADAS__</strong></div>
            </div>
        </section>
        <div class="report-grid">
        <div class="report-main">
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
            __AVISO_MERCADOS__
        </div>
        <h2 class="section-title">Resumen ejecutivo</h2>
        <div class="summary">
            <span>Bankroll: __BANKROLL__ EUR</span>
            <span>Mercados: __MERCADOS__</span>
            <span>Partido: __PARTIDO__</span>
            <span>Elite: __TOTAL_ELITE__</span>
            <span>Stakazos: __TOTAL_STAKAZOS__</span>
            <span>Premium: __TOTAL_PREMIUM__</span>
            <span>Snapshots: __SNAPSHOTS__</span>
        </div>
        <aside class="report-sidebar">
            <div class="card">
                <h3 class="section-card-title">Contexto activo</h3>
                <div class="kpi-stack">
                    <div class="metric">Bankroll<strong>__BANKROLL__ EUR</strong><small>Capital operativo actual</small></div>
                    <div class="metric">Elite<strong>__TOTAL_ELITE__</strong><small>Picks con filtro premium</small></div>
                    <div class="metric">Stakazos<strong>__TOTAL_STAKAZOS__</strong><small>Maxima exigencia del sistema</small></div>
                    <div class="metric">Snapshots<strong>__SNAPSHOTS__</strong><small>Huella de cuotas guardada</small></div>
                </div>
            </div>
            <div class="card">
                <h3 class="section-card-title">Como leer este informe</h3>
                <ul class="tiny-list">
                    <li>Prioriza fiabilidad, calidad y ajuste historico antes que el EV aislado.</li>
                    <li>Un stakazo debe mejorar en precio y sostener contexto, no solo parecer bonito.</li>
                    <li>Si no hay picks, el sistema te esta ahorrando decisiones malas.</li>
                </ul>
            </div>
        </aside>
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
        </div>
    """

    html += """
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
        solo_elite=False,
        solo_stakazos=solo_stakazos,
    )
    return build_prediction_payload(
        data=data,
        solo_stakazos=solo_stakazos,
        ai_available=openai_available,
        select_picks_for_telegram=seleccionar_picks_para_telegram,
        enrich_with_ai=enrich_picks_with_ai_narratives,
        build_ai_summary=generate_publication_ai_summary,
        format_pick_message=formatear_mensaje_telegram_pick,
        format_summary_message=format_summary_message,
        perfil=perfil,
        modo=modo,
        perfiles_stake=PERFILES_STAKE,
        modos_informe=MODOS_INFORME,
        perfil_label=perfil_es,
        modo_label=modo_es,
    )


@app.get("/lab/run", response_class=HTMLResponse)
def lab_run(
    bankroll: str | None = None,
    perfil: str = "moderado",
    modo: str = "comparador",
    mercados: str = "todo",
    partido: str = "todos",
    deporte: str = "todo",
    solo_stakazos: bool = False,
    simulation_mode: str = "live",
    snapshot_at: str | None = None,
    snapshot_from: str | None = None,
    snapshot_to: str | None = None,
    format: str = "html",
    execute: bool = False,
):
    bankroll_value = None
    bankroll_raw = str(bankroll or "").strip()
    if bankroll_raw:
        try:
            bankroll_value = float(bankroll_raw)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Bankroll no valido") from exc

    simulation_mode = str(simulation_mode or "live").strip().lower()
    historical_mode = simulation_mode == "historical"
    snapshot_at_utc = None
    snapshot_at_display = ""
    snapshot_from_utc = None
    snapshot_from_display = ""
    snapshot_to_utc = None
    snapshot_to_display = ""
    notice_code = ""

    if snapshot_at:
        snapshot_at_utc, snapshot_at_display = normalizar_snapshot_lab(snapshot_at)
    if snapshot_from:
        snapshot_from_utc, snapshot_from_display = normalizar_snapshot_lab(snapshot_from)
    if snapshot_to:
        snapshot_to_utc, snapshot_to_display = normalizar_snapshot_lab(snapshot_to)

    if not snapshot_at_utc and snapshot_from_utc:
        snapshot_at_utc = snapshot_from_utc
        snapshot_at_display = snapshot_from_display

    if execute and historical_mode and not snapshot_at_utc:
        execute = False
        notice_code = "snapshot_required"

    if execute:
        lab_data = build_lab_run(
            runtime_settings=RUNTIME_SETTINGS,
            publication_guard=lambda: publication_guard_state(
                runtime_settings=RUNTIME_SETTINGS,
                load_stats=estadisticas,
                load_learning=aprendizaje,
            ),
            run_forecast=lambda request: apuestas_hoy(
                bankroll=request.bankroll,
                perfil=request.perfil,
                modo=request.modo,
                mercados=request.mercados,
                partido=request.partido,
                guardar=request.guardar,
                deporte=request.deporte,
                solo_elite=request.solo_elite,
                solo_stakazos=request.solo_stakazos,
                historical_mode=request.historical_mode,
                historical_date=request.historical_date,
                historical_from=request.historical_from,
                historical_to=request.historical_to,
            ),
            build_prediction_payload=build_prediction_payload,
            ai_available=openai_available,
            select_picks_for_telegram=seleccionar_picks_para_telegram,
            enrich_with_ai=enrich_picks_with_ai_narratives,
            build_ai_summary=generate_publication_ai_summary,
            format_pick_message=formatear_mensaje_telegram_pick,
            format_summary_message=format_summary_message,
            fetch_scores=scores,
            load_learning_summary=aprendizaje,
            load_calibration_snapshot=generate_calibration_snapshot,
            todo_toggle_panel=build_todo_toggle_groups(),
            perfil=perfil,
            modo=modo,
            mercados=mercados,
            partido=partido,
            deporte=deporte,
            bankroll=bankroll_value,
            solo_stakazos=solo_stakazos,
            simulation_mode=simulation_mode,
            historical_snapshot_at=snapshot_at_utc,
            historical_range_from=snapshot_from_utc,
            historical_range_to=snapshot_to_utc,
            perfiles_stake=PERFILES_STAKE,
            modos_informe=MODOS_INFORME,
            perfil_label=perfil_es,
            modo_label=modo_es,
        )
    else:
        lab_data = build_empty_lab_run(
            runtime_settings=RUNTIME_SETTINGS,
            todo_toggle_panel=build_todo_toggle_groups(),
        )
    if str(format or "html").strip().lower() == "json":
        return lab_data

    market_config = config_mercados_deporte("futbol" if deporte == "todo" else deporte)
    html = render_lab_run_html(
        lab_data,
        query_params={
            "bankroll": bankroll_raw,
            "perfil": perfil,
            "modo": modo,
            "mercados": mercados,
            "partido": partido,
            "deporte": deporte,
            "solo_stakazos": "true" if solo_stakazos else "false",
            "simulation_mode": simulation_mode,
            "snapshot_at": snapshot_at_display,
            "snapshot_from": snapshot_from_display,
            "snapshot_to": snapshot_to_display,
            "execute": "true" if execute else "",
            "lab_notice": notice_code,
        },
        premium_css=premium_ui_css,
        profile_options=[
            {"value": "conservador", "label": perfil_es("conservador")},
            {"value": "moderado", "label": perfil_es("moderado")},
            {"value": "agresivo", "label": perfil_es("agresivo")},
            {"value": "alto_riesgo", "label": perfil_es("alto_riesgo")},
        ],
        mode_options=[
            {"value": "comparador", "label": modo_es("comparador")},
            {"value": "pinnacle", "label": modo_es("pinnacle")},
        ],
        sport_options=[
            {"value": str(item.get("value") or ""), "label": str(item.get("label") or item.get("value") or "")}
            for item in opciones_deporte_disponibles(selected=deporte)
        ],
        market_options=[
            {"value": value, "label": SPORT_FILTER_LABELS.get(value, value)}
            for value in market_config["allowed_filters"]
        ],
        match_options=[
            {"value": "todos", "label": "Todos los partidos"},
            *[
                {
                    "value": str(item.get("id") or ""),
                    "label": str(item.get("label") or item.get("id") or "Partido"),
                }
                for item in (lab_data.get("forecast") or {}).get("partidos_disponibles", [])
                if str(item.get("id") or "").strip()
            ],
        ],
    )
    return HTMLResponse(content=html, media_type="text/html; charset=utf-8")


@app.post("/lab/run/todo-filters")
async def lab_run_todo_filters(request: Request):
    form = await form_urlencoded(request)
    scope = str(form.get("scope") or "").strip().lower()
    key = str(form.get("key") or "").strip().lower()
    enabled = str(form.get("enabled") or "").strip().lower() == "true"

    if scope not in {"sport", "league", "market", "market_pair"} or not key:
        raise HTTPException(status_code=400, detail="Filtro no valido")

    filtros = cargar_filtros_todo()
    disabled_sports = set(filtros["disabled_sports"])
    disabled_leagues = set(filtros["disabled_leagues"])
    disabled_markets = set(filtros.get("disabled_markets") or set())
    disabled_market_pairs = set(filtros.get("disabled_market_pairs") or set())

    if scope == "sport":
        if enabled:
            disabled_sports.discard(key)
        else:
            disabled_sports.add(key)
    elif scope == "league":
        if enabled:
            disabled_leagues.discard(key)
        else:
            disabled_leagues.add(key)
    elif scope == "market":
        if enabled:
            disabled_markets.discard(key)
        else:
            disabled_markets.add(key)
    else:
        if enabled:
            disabled_market_pairs.discard(key)
        else:
            disabled_market_pairs.add(key)

    guardar_filtros_todo(
        disabled_sports=disabled_sports,
        disabled_leagues=disabled_leagues,
        disabled_markets=disabled_markets,
        disabled_market_pairs=disabled_market_pairs,
    )

    query = _redirect_query_for_lab_filters(form)
    query["lab_notice"] = "toggles_saved"
    return RedirectResponse(url="/lab/run?" + urlencode(query), status_code=303)


@app.post("/lab/run/publicar")
async def lab_run_publicar(request: Request):
    form = await form_urlencoded(request)
    bankroll_raw = str(form.get("bankroll") or "").strip()
    bankroll = None
    if bankroll_raw:
        try:
            bankroll = float(bankroll_raw)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Bankroll no valido") from exc

    perfil = str(form.get("perfil") or "moderado").strip() or "moderado"
    modo = str(form.get("modo") or "comparador").strip() or "comparador"
    mercados = str(form.get("mercados") or "todo").strip() or "todo"
    partido = str(form.get("partido") or "todos").strip() or "todos"
    deporte = str(form.get("deporte") or "todo").strip() or "todo"
    solo_stakazos = str(form.get("solo_stakazos") or "false").strip().lower() == "true"

    payload_raw = str(form.get("lab_payload") or "").strip()
    payload = None
    if payload_raw:
        try:
            payload = json.loads(base64.b64decode(payload_raw.encode("ascii")).decode("utf-8"))
        except Exception:
            payload = None

    if payload is not None and not list(payload.get("pronosticos") or []):
        resultado = {"picks_guardados": 0, "publication_id": None, "mensajes_enviados": 0}
    else:
        job_id = iniciar_publicacion_lab_async(
            payload=payload,
            bankroll=bankroll,
            perfil=perfil,
            modo=modo,
            mercados=mercados,
            partido=partido,
            deporte=deporte,
            solo_stakazos=solo_stakazos,
        )
        resultado = {
            "picks_guardados": len(list((payload or {}).get("pronosticos") or [])) if payload is not None else 0,
            "publication_id": "",
            "mensajes_enviados": 0,
            "job_id": job_id,
        }

    query = {
        "perfil": perfil,
        "modo": modo,
        "mercados": mercados,
        "partido": partido,
        "deporte": deporte,
        "solo_stakazos": "true" if solo_stakazos else "false",
        "lab_notice": "queued" if resultado.get("job_id") else ("published" if int(resultado.get("picks_guardados") or 0) > 0 else "empty"),
        "publication_id": str(resultado.get("publication_id") or ""),
        "registered_picks": str(resultado.get("picks_guardados") or 0),
        "sent_messages": str(resultado.get("mensajes_enviados") or 0),
        "job_id": str(resultado.get("job_id") or ""),
    }
    if bankroll is not None:
        query["bankroll"] = str(bankroll)

    return RedirectResponse(url="/lab/run?" + urlencode(query), status_code=303)


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


@app.get("/tracking/riesgo")
def tracking_riesgo():
    return politica_riesgo_actual()


@app.get("/tracking/dashboard-data")
def tracking_dashboard_data():
    return dashboard_data()


@app.get("/tracking/panel", response_class=HTMLResponse)
def tracking_panel(format: str = "html"):
    panel = build_recent_form_panel()
    if str(format or "html").strip().lower() == "json":
        return panel
    html = render_recent_form_panel_html(panel, premium_css=premium_ui_css)
    return HTMLResponse(content=html, media_type="text/html; charset=utf-8")


@app.post("/tracking/panel/reset-sport-form")
async def tracking_panel_reset_sport_form(request: Request):
    form = await form_urlencoded(request)
    sport_label = str(form.get("sport_label") or "").strip()
    cutoff_at = str(form.get("cutoff_at") or "").strip()

    if not sport_label:
        raise HTTPException(status_code=400, detail="Debes indicar un deporte.")

    if cutoff_at:
        cutoff_at = cutoff_at.replace(" ", "T")
        if len(cutoff_at) == 16:
            cutoff_at = f"{cutoff_at}:00+00:00"
        elif len(cutoff_at) == 19 and "+" not in cutoff_at and "Z" not in cutoff_at:
            cutoff_at = f"{cutoff_at}+00:00"
    else:
        cutoff_at = datetime.now(timezone.utc).isoformat()

    guardar_reset_historial_deporte(sport_label, cutoff_at)
    return RedirectResponse(url="/tracking/panel", status_code=303)


@app.post("/tracking/panel/clear-sport-reset-form")
async def tracking_panel_clear_sport_reset_form(request: Request):
    form = await form_urlencoded(request)
    sport_label = str(form.get("sport_label") or "").strip()

    if not sport_label:
        raise HTTPException(status_code=400, detail="Debes indicar un deporte.")

    eliminar_reset_historial_deporte(sport_label)
    return RedirectResponse(url="/tracking/panel", status_code=303)


@app.get("/tracking/evaluaciones")
def tracking_evaluaciones(limit: int = 100):
    return {
        "evaluaciones": listar_evaluaciones_picks(limit=limit),
    }


@app.get("/api/calibration")
def api_calibration():
    """
    Endpoint premium que retorna el snapshot completo de calibración.
    
    Incluye:
    - Métricas por liga, mercado, tier, casa de apuestas
    - Análisis de confianza (confidence_score)
    - Alertas de underperformance
    - Ajustes recomendados para el modelo
    
    Este es el corazón de la inteligencia adaptiva del bot.
    """
    calibration = generate_calibration_snapshot()
    
    # Serializar para JSON
    segments_serialized = {}
    for segment_type, metrics_dict in calibration.segments_by_type.items():
        segments_serialized[segment_type] = {}
        for segment_name, metric in metrics_dict.items():
            segments_serialized[segment_type][segment_name] = {
                "segment_name": metric.segment_name,
                "segment_type": metric.segment_type,
                "total_picks": metric.total_picks,
                "total_recommended": metric.total_recommended,
                "picks_closed": metric.picks_closed,
                "picks_won": metric.picks_won,
                "picks_lost": metric.picks_lost,
                "picks_push": metric.picks_push,
                "total_staked": round(metric.total_staked, 2),
                "total_profit": round(metric.total_profit, 2),
                "roi": metric.roi,
                "hit_rate": metric.hit_rate,
                "clv": metric.clv,
                "clv_positive_count": metric.clv_positive_count,
                "confidence_score": metric.confidence_score,
                "last_pick_date": metric.last_pick_date,
                "min_sample_warning": metric.min_sample_warning,
                "trend": metric.trend,
                "recommendation": metric.recommendation,
            }
    
    return {
        "timestamp": calibration.timestamp,
        "total_picks_evaluated": calibration.total_picks_evaluated,
        "segments_by_type": segments_serialized,
        "model_adjustments": calibration.model_adjustments,
        "alerts": calibration.alerts,
    }


@app.get("/api/calibration/report", response_class=HTMLResponse)
def api_calibration_report():
    """
    Retorna un reporte premium formateado de calibración.
    """
    calibration = generate_calibration_snapshot()
    report_text = format_calibration_report(calibration)
    
    # Formatear como HTML para lectura premium
    html_report = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>Reporte de Calibración Premium</title>
        <style>
            {premium_ui_css()}
            body {{ font-family: 'Courier New', monospace; background: #0a0e27; color: #e0e0e0; padding: 20px; }}
            .report {{ white-space: pre-wrap; background: #1a1f3a; padding: 20px; border-radius: 8px; }}
            .alert {{ color: #ff6b6b; margin: 10px 0; }}
            .success {{ color: #51cf66; margin: 10px 0; }}
            .warning {{ color: #ffd43b; margin: 10px 0; }}
        </style>
    </head>
    <body>
        <div class="report">
{escape(report_text)}
        </div>
    </body>
    </html>
    """
    
    return html_report


@app.get("/api/audit")
def api_audit(date: str = None):
    """
    Retorna el reporte de auditoría para una fecha específica.
    
    Si no se proporciona fecha, usa hoy.
    
    Incluye:
    - Picks recomendadas vs ejecutadas
    - Resultados del día
    - ROI intraday
    - Comparación con histórico
    - Alertas críticas
    - Estado del modelo
    """
    try:
        if date:
            target_date = datetime.fromisoformat(date)
        else:
            target_date = datetime.now(timezone.utc)
        
        report = generate_daily_audit_report(target_date)
        return report
    
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/audit/report", response_class=HTMLResponse)
def api_audit_report(date: str = None):
    """
    Retorna un reporte premium formateado de auditoría.
    
    Incluye gráficos visuales, colores por status (VERDE/AMARILLO/ROJO).
    """
    
    try:
        if date:
            target_date = datetime.fromisoformat(date)
        else:
            target_date = datetime.now(timezone.utc)
        
        report = generate_daily_audit_report(target_date)
        report_html = format_audit_report_html(report)
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>Auditoría Diaria - {report['date']}</title>
            <style>
                {premium_ui_css()}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>📊 Auditoría Diaria - {report['date']}</h1>
                {report_html}
                <div style="margin-top: 30px; padding: 10px; border-radius: 8px; font-size: 12px; border-top: 1px solid var(--line);">
                    <p style="margin: 4px 0; color: var(--muted);">Generado: {report['timestamp']}</p>
                    <p style="margin: 4px 0; color: var(--muted);">Status del modelo: {report['status']} - {report['status_detail']}</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        return html
    
    except Exception as e:
        return f"<html><body><h1>Error</h1><p>{escape(str(e))}</p></body></html>"


@app.post("/api/audit/send-telegram")
def api_audit_send_telegram():
    """
    Endpoint para enviar manualmente el reporte de auditoría por Telegram.
    
    Útil para testing o envío forzado.
    """
    return send_audit_report_telegram()


@app.get("/api/audit/telegram")
def api_audit_send_telegram_get():
    """
    Endpoint GET para enviar auditoría por Telegram.
    
    Uso: http://servidor:8000/api/audit/telegram
    
    Sirve para:
    - Llamar desde navegador: http://servidor:8000/api/audit/telegram
    - Desde curl: curl http://servidor:8000/api/audit/telegram
    - Desde cron: 0 21 * * * curl http://servidor:8000/api/audit/telegram
    """
    return send_audit_report_telegram()


@app.post("/tracking/liquidar-auto")
def tracking_liquidar_auto(days_from: int = 3):
    marcadores = scores_for_pending_bot_picks(days_from=days_from)
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


@app.post("/tracking/picks/archivar-form")
async def tracking_archivar_picks_form(request: Request):
    form = await form_urlencoded(request)
    id_desde_raw = str(form.get("id_desde") or "").strip()
    id_hasta_raw = str(form.get("id_hasta") or "").strip()

    id_desde = int(id_desde_raw) if id_desde_raw else None
    id_hasta = int(id_hasta_raw) if id_hasta_raw else None

    if id_desde is None and id_hasta is None:
        raise HTTPException(status_code=400, detail="Debes indicar al menos un limite para archivar.")

    archivar_picks_pendientes(id_desde=id_desde, id_hasta=id_hasta)
    return RedirectResponse(url="/mis-apuestas", status_code=303)


@app.post("/tracking/picks/eliminar-archivadas-form")
async def tracking_eliminar_picks_archivadas_form(request: Request):
    form = await form_urlencoded(request)
    id_desde_raw = str(form.get("id_desde") or "").strip()
    id_hasta_raw = str(form.get("id_hasta") or "").strip()

    id_desde = int(id_desde_raw) if id_desde_raw else None
    id_hasta = int(id_hasta_raw) if id_hasta_raw else None

    if id_desde is None and id_hasta is None:
        raise HTTPException(status_code=400, detail="Debes indicar al menos un limite para eliminar.")

    eliminar_picks_archivadas(id_desde=id_desde, id_hasta=id_hasta)
    return RedirectResponse(url="/mis-apuestas", status_code=303)


@app.get("/mis-apuestas", response_class=HTMLResponse)
def mis_apuestas(
    estado: str | None = None,
    elite_tier: str | None = None,
    solo_elite: bool = False,
    solo_stakazos: bool = False,
    apuesta_scope: str = "reales",
    sport_label: str | None = None,
    league_label: str | None = None,
    min_quality_score: int = 0,
    min_reliability_score: int = 0,
    order_by: str = "recientes",
):
    apuesta_real_filter = True
    if apuesta_scope == "todas":
        apuesta_real_filter = None
    elif apuesta_scope == "modelo":
        apuesta_real_filter = False

    picks = listar_picks(
        limit=300,
        estado=estado,
        elite_tier=elite_tier,
        solo_elite=solo_elite,
        solo_stakazos=solo_stakazos,
        apuesta_real=apuesta_real_filter,
        sport_label=sport_label,
        league_label=league_label,
        min_quality_score=min_quality_score or None,
        min_reliability_score=min_reliability_score or None,
        order_by=order_by,
    )
    pendientes = [p for p in picks if p["estado"] == "pendiente"]
    cerradas = [p for p in picks if p["estado"] == "cerrada"]
    premium = dashboard_data()
    bankroll = obtener_bankroll()
    checked_elite = "checked" if solo_elite else ""
    checked_stakazos = "checked" if solo_stakazos else ""
    sport_options = sorted({str((_raw_pick(p).get("sport_label") or p.get("sport_label") or "")).strip() for p in picks if (_raw_pick(p).get("sport_label") or p.get("sport_label"))})
    league_options = sorted({str((_raw_pick(p).get("league_label") or p.get("league_label") or "")).strip() for p in picks if (_raw_pick(p).get("league_label") or p.get("league_label"))})
    scope_label = {
        "reales": "Reales",
        "modelo": "No reales",
        "todas": "Todas",
    }.get(apuesta_scope, "Reales")

    visible_apostado = round(sum(float(p.get("importe_sugerido") or 0) for p in cerradas), 2)
    visible_beneficio = round(sum(float(p.get("profit_loss") or 0) for p in cerradas), 2)
    visible_roi = round((visible_beneficio / visible_apostado) * 100, 2) if visible_apostado else 0.0

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
            <td>{escape(etiqueta_mercado_visible(pick.get('mercado')))}</td>
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
            <td>{escape(etiqueta_mercado_visible(pick.get('mercado')))}</td>
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
            .danger-box {{
                background: white;
                border-radius: 8px;
                padding: 14px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.08);
                margin-bottom: 18px;
                border-left: 4px solid #b91c1c;
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
                    <label>Alcance</label>
                    <select name="apuesta_scope">
                        <option value="reales" {"selected" if apuesta_scope == "reales" else ""}>Solo reales</option>
                        <option value="modelo" {"selected" if apuesta_scope == "modelo" else ""}>Solo no reales</option>
                        <option value="todas" {"selected" if apuesta_scope == "todas" else ""}>Todas</option>
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
        <div class="danger-box">
            <h3>Limpiar pendientes antiguas</h3>
            <p>Si arrastras picks viejas que ya no quieres que cuenten como pendientes, puedes archivarlas sin borrarlas fisicamente. Dejaran de salir en pendientes, resumenes y comandos operativos.</p>
            <form method="post" action="/tracking/picks/archivar-form" class="bankroll-form">
                <div class="field">
                    <label>ID desde</label>
                    <input name="id_desde" type="number" min="1" step="1" placeholder="37">
                </div>
                <div class="field">
                    <label>ID hasta</label>
                    <input name="id_hasta" type="number" min="1" step="1" placeholder="103">
                </div>
                <button type="submit">Archivar pendientes de ese rango</button>
            </form>
            <p style="margin-top: 12px;">Si despues quieres borrarlas de verdad, este segundo paso solo elimina picks ya archivadas para que no sigan ocupando base de datos ni entren en ningun calculo futuro.</p>
            <form method="post" action="/tracking/picks/eliminar-archivadas-form" class="bankroll-form">
                <div class="field">
                    <label>ID desde</label>
                    <input name="id_desde" type="number" min="1" step="1" placeholder="37">
                </div>
                <div class="field">
                    <label>ID hasta</label>
                    <input name="id_hasta" type="number" min="1" step="1" placeholder="103">
                </div>
                <button type="submit" class="loss">Borrar archivadas de ese rango</button>
            </form>
        </div>
        <div class="summary">
            <div class="metric">Bankroll actual<strong>{bankroll:.2f} EUR</strong></div>
            <div class="metric">Pendientes {escape(scope_label)}<strong>{len(pendientes)}</strong></div>
            <div class="metric">Cerradas {escape(scope_label)}<strong>{len(cerradas)}</strong></div>
            <div class="metric">Beneficio {escape(scope_label)}<strong>{visible_beneficio:.2f} EUR</strong></div>
            <div class="metric">ROI {escape(scope_label)}<strong>{visible_roi:.2f}%</strong></div>
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
    riesgo_info = politica_riesgo_actual()

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
            <td>{escape(etiqueta_mercado_visible(pick.get('mercado')))}</td>
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
            {menu_css()}
            .dashboard-copy {{
                margin: 12px 0 20px;
                color: var(--muted);
                font-size: 15px;
            }}
            .dashboard-section {{
                margin-top: 28px;
            }}
            @media (max-width: 900px) {{
                .dashboard-copy {{
                    margin-bottom: 16px;
                }}
            }}
        </style>
    </head>
    <body>
    <div class="container">
        {menu_html("dashboard")}
        <section class="hero">
            <div class="eyebrow">Performance analytics</div>
            <h1>Dashboard de rendimiento</h1>
            <p>Panel para distinguir si el sistema realmente encuentra ventaja o si solo esta sobreviviendo a una racha. La lectura buena no es el ROI aislado: es el conjunto de ROI, CLV, calidad y fiabilidad.</p>
            <div class="hero-metrics">
                <div class="hero-metric"><span>Picks cerrados</span><strong>{resumen['picks_cerrados']}</strong></div>
                <div class="hero-metric"><span>ROI</span><strong>{resumen['roi']:.2f}%</strong></div>
                <div class="hero-metric"><span>Hit rate</span><strong>{resumen['hit_rate']:.2f}%</strong></div>
                <div class="hero-metric"><span>CLV medio</span><strong>{resumen['clv_medio'] if resumen['clv_medio'] is not None else 'N/D'}</strong></div>
            </div>
        </section>
        <div class="aviso">
            Usa este panel para decidir donde subir o bajar stake. Con poca muestra, ROI y acierto pueden moverse mucho.
            Para cerrar automaticamente picks liquidables por marcador: <code>POST /tracking/liquidar-auto</code>.
        </div>
        <div class="grid-4">
            <div class="metric">Picks cerrados<strong>{resumen['picks_cerrados']}</strong></div>
            <div class="metric">Pendientes<strong>{resumen['picks_pendientes']}</strong></div>
            <div class="metric">Beneficio<strong>{resumen['beneficio']:.2f} EUR</strong></div>
            <div class="metric">ROI<strong>{resumen['roi']:.2f}%</strong></div>
            <div class="metric">Acierto<strong>{resumen['hit_rate']:.2f}%</strong></div>
            <div class="metric">Apostado<strong>{resumen['total_apostado']:.2f} EUR</strong></div>
            <div class="metric">Snapshots<strong>{resumen['snapshots_cuotas']}</strong></div>
            <div class="metric">CLV medio<strong>{resumen['clv_medio'] if resumen['clv_medio'] is not None else 'N/D'}</strong></div>
        </div>
        <div class="dashboard-section">
        <h2 class="section-title">Filtro premium</h2>
        <div class="grid-4">
            <div class="metric">Stakazos detectados<strong>{data['solo_stakazos']['picks']}</strong></div>
            <div class="metric">Stakazos cerrados<strong>{data['solo_stakazos']['cerradas']}</strong></div>
            <div class="metric">ROI stakazos<strong>{data['solo_stakazos']['roi']:.2f}%</strong></div>
            <div class="metric">Fiabilidad media<strong>{data['solo_stakazos']['reliability_media'] if data['solo_stakazos']['reliability_media'] is not None else 'N/D'}</strong></div>
        </div>
        </div>
        <div class="dashboard-section">
        <h2 class="section-title">Comparativa por tier</h2>
        <div class="grid-4">
            <div class="metric">CLV stakazos<strong>{data['solo_stakazos']['clv_medio'] if data['solo_stakazos']['clv_medio'] is not None else 'N/D'}</strong></div>
            <div class="metric">Acierto stakazos<strong>{data['solo_stakazos']['hit_rate']:.2f}%</strong></div>
            <div class="metric">ROI elite<strong>{data['solo_elite']['roi']:.2f}%</strong></div>
            <div class="metric">ROI seguimiento<strong>{data['solo_seguimiento']['roi']:.2f}%</strong></div>
            <div class="metric">CLV elite<strong>{data['solo_elite']['clv_medio'] if data['solo_elite']['clv_medio'] is not None else 'N/D'}</strong></div>
            <div class="metric">CLV seguimiento<strong>{data['solo_seguimiento']['clv_medio'] if data['solo_seguimiento']['clv_medio'] is not None else 'N/D'}</strong></div>
            <div class="metric">CLV+ stakazos<strong>{data['solo_stakazos']['clv_positivo_pct'] if data['solo_stakazos']['clv_positivo_pct'] is not None else 'N/D'}</strong></div>
            <div class="metric">CLV+ elite<strong>{data['solo_elite']['clv_positivo_pct'] if data['solo_elite']['clv_positivo_pct'] is not None else 'N/D'}</strong></div>
        </div>
        </div>
        <div class="dashboard-section">
        <h2 class="section-title">Control de riesgo</h2>
        <div class="grid-4">
            <div class="metric">Modo operativo<strong>{escape(str(riesgo_info['operating_mode']))}</strong></div>
            <div class="metric">Fase de muestra<strong>{escape(str(riesgo_info['sample_stage']))}</strong></div>
            <div class="metric">Multiplicador stake<strong>{float(riesgo_info['stake_multiplier']):.2f}x</strong></div>
            <div class="metric">Stake maximo<strong>{float(riesgo_info['max_stake_units']):.2f}/5</strong></div>
            <div class="metric">Kill switch<strong>{'Activo' if riesgo_info['block_new_picks'] else 'No'}</strong></div>
            <div class="metric">Mercados fragiles<strong>{'Bloqueados' if riesgo_info['block_fragile_markets'] else 'Permitidos'}</strong></div>
            <div class="metric">Solo elite<strong>{'Si' if riesgo_info['only_elite_when_cautious'] else 'No'}</strong></div>
            <div class="metric">Motivo actual<strong>{escape(str(riesgo_info['reason']))}</strong></div>
            <div class="metric">Evaluaciones<strong>{aprendizaje_info.get('picks_evaluadas', 0)}</strong></div>
        </div>
        </div>
        <p class="dashboard-copy">{escape(aprendizaje_info['lectura'])}</p>
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
