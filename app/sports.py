import os


DEFAULT_SPORT = os.getenv("DEFAULT_SPORT", "worldcup").strip().lower()
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
    "se_clasificara": "The Odds API no ofrece 'se clasificara' para este endpoint de partido.",
    "ambos_tarjetas": "The Odds API no ofrece 'ambos equipos recibiran tarjetas' como mercado directo.",
    "team_fouls": "The Odds API no ofrece faltas por equipo para futbol; no se pediran cuotas para ese filtro.",
    "jugador_faltas_concedidas": "The Odds API no ofrece faltas concedidas por jugador para futbol.",
    "jugador_recibira_falta": "The Odds API no ofrece recibira falta por jugador para futbol.",
    "jugador_entradas": "The Odds API no ofrece entradas por jugador para futbol.",
    "jugador_remates_cabeza": "The Odds API no ofrece remates de cabeza a puerta por jugador.",
    "jugador_remates_fuera_area": "The Odds API no ofrece remates a puerta fuera del area por jugador.",
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
        "default_markets": "todo" if family == "soccer" else "h2h,spreads,totals" if family == "basketball" else "h2h",
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
