import os
import re
from typing import Any

import requests
from fastapi import HTTPException

from .sports import (
    ADDITIONAL_MARKETS,
    FEATURED_MARKETS,
    SPORT_CATALOG,
    TODO_LIMITS_BY_FAMILY,
    build_dynamic_context_from_sport_key,
    enriquecer_eventos_contexto,
    family_from_sport_key,
    resolver_contexto_deporte,
)


ODDS_API_KEY = os.getenv("ODDS_API_KEY")
ODDS_PROVIDER = os.getenv("ODDS_PROVIDER", "the_odds_api").strip().lower()
ODDS_API_BOOKMAKERS = os.getenv("ODDS_API_BOOKMAKERS", "").strip()
ODDS_API_REGIONS_DEFAULT = os.getenv("ODDS_API_REGIONS_DEFAULT", "eu,uk").strip() or "eu,uk"
ODDS_API_REGIONS_SOCCER = os.getenv("ODDS_API_REGIONS_SOCCER", ODDS_API_REGIONS_DEFAULT).strip() or ODDS_API_REGIONS_DEFAULT
ODDS_API_REGIONS_TENNIS = os.getenv("ODDS_API_REGIONS_TENNIS", ODDS_API_REGIONS_DEFAULT).strip() or ODDS_API_REGIONS_DEFAULT
ODDS_API_REGIONS_BASKETBALL = os.getenv("ODDS_API_REGIONS_BASKETBALL", "us,us2,eu,uk").strip() or "us,us2,eu,uk"
ODDS_API_INCLUDE_LINKS = os.getenv("ODDS_API_INCLUDE_LINKS", "true").strip().lower() in {"1", "true", "yes", "si", "on"}
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
API_FOOTBALL_HOST = "https://v3.football.api-sports.io"
API_FOOTBALL_LEAGUE = os.getenv("API_FOOTBALL_LEAGUE", "1")
API_FOOTBALL_SEASON = os.getenv("API_FOOTBALL_SEASON", "2026")
API_FOOTBALL_MAX_PAGES = int(os.getenv("API_FOOTBALL_MAX_PAGES", "1"))
SPORTSGAMEODDS_API_KEY = os.getenv("SPORTSGAMEODDS_API_KEY")
SPORTSGAMEODDS_HOST = "https://api.sportsgameodds.com/v2"
SPORTSGAMEODDS_SPORT_ID = os.getenv("SPORTSGAMEODDS_SPORT_ID", "SOCCER")
SPORTSGAMEODDS_LEAGUE_ID = os.getenv("SPORTSGAMEODDS_LEAGUE_ID", "")
SPORTSGAMEODDS_BOOKMAKERS = os.getenv("SPORTSGAMEODDS_BOOKMAKERS", "")
SPORTSGAMEODDS_MAX_EVENTS = int(os.getenv("SPORTSGAMEODDS_MAX_EVENTS", "25"))


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


def the_odds_regions_for_context(contexto: dict) -> str:
    family = family_from_sport_key(str(contexto.get("sport_key") or ""))
    if family == "basketball":
        return ODDS_API_REGIONS_BASKETBALL
    if family == "tennis":
        return ODDS_API_REGIONS_TENNIS
    if family == "soccer":
        return ODDS_API_REGIONS_SOCCER
    return ODDS_API_REGIONS_DEFAULT


def build_the_odds_query_params(contexto: dict, markets: list[str]) -> dict[str, str]:
    params: dict[str, str] = {
        "apiKey": ODDS_API_KEY or "",
        "markets": ",".join(markets),
        "oddsFormat": "decimal",
    }
    bookmakers = ODDS_API_BOOKMAKERS
    if bookmakers:
        params["bookmakers"] = bookmakers
    else:
        params["regions"] = the_odds_regions_for_context(contexto)
    if ODDS_API_INCLUDE_LINKS:
        params["includeLinks"] = "true"
    return params


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


def fetch_the_odds_odds(mercados_lista: list[str], contexto: dict) -> list[dict]:
    if not ODDS_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="Falta ODDS_API_KEY en el archivo .env",
        )

    mercados_base = [m for m in mercados_lista if m in FEATURED_MARKETS] or ["h2h"]
    mercados_adicionales = [m for m in mercados_lista if m in ADDITIONAL_MARKETS]
    sport_key = contexto["sport_key"]
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds"

    params = build_the_odds_query_params(contexto, mercados_base)

    try:
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502,
            detail=odds_api_error_detail(exc),
        ) from exc

    data = response.json()

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
            event_params = build_the_odds_query_params(contexto, mercados_adicionales)

            try:
                extra = requests.get(event_url, params=event_params, timeout=15)
                extra.raise_for_status()
            except requests.RequestException:
                continue

            extra_data = extra.json()

            if isinstance(extra_data, dict):
                merge_event_markets(evento, extra_data)

    return enriquecer_eventos_contexto(data, contexto)


def fetch_the_odds_historical_odds(
    mercados_lista: list[str],
    contexto: dict,
    snapshot_date: str,
) -> list[dict]:
    if not ODDS_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="Falta ODDS_API_KEY en el archivo .env",
        )

    mercados_base = [m for m in mercados_lista if m in FEATURED_MARKETS] or ["h2h"]
    sport_key = contexto["sport_key"]
    url = f"https://api.the-odds-api.com/v4/historical/sports/{sport_key}/odds"
    params = build_the_odds_query_params(contexto, mercados_base)
    params["date"] = snapshot_date

    try:
        response = requests.get(url, params=params, timeout=20)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502,
            detail=odds_api_error_detail(exc),
        ) from exc

    payload = response.json()

    if isinstance(payload, dict) and payload.get("message"):
        raise HTTPException(status_code=502, detail=payload["message"])

    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise HTTPException(status_code=502, detail="Respuesta inesperada de The Odds API en historico")

    enriched = enriquecer_eventos_contexto(payload.get("data", []), contexto)
    snapshot_time = payload.get("timestamp")
    previous_time = payload.get("previous_timestamp")
    next_time = payload.get("next_timestamp")

    for event in enriched:
        event["historical_mode"] = True
        event["historical_snapshot_time"] = snapshot_time
        event["historical_previous_snapshot_time"] = previous_time
        event["historical_next_snapshot_time"] = next_time

    return enriched


def fetch_the_odds_scores(days_from: int, contexto: dict) -> list[dict]:
    if not ODDS_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="Falta ODDS_API_KEY en el archivo .env",
        )

    normalized_days = max(1, min(days_from, 3))
    sport_key = contexto["sport_key"]
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/scores"
    params = {
        "apiKey": ODDS_API_KEY,
        "daysFrom": normalized_days,
        "dateFormat": "iso",
    }

    try:
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502,
            detail=odds_api_error_detail(exc),
        ) from exc

    data = response.json()

    if isinstance(data, dict) and data.get("message"):
        raise HTTPException(status_code=502, detail=data["message"])

    if not isinstance(data, list):
        raise HTTPException(status_code=502, detail="Respuesta inesperada de The Odds API")

    return enriquecer_eventos_contexto(data, contexto)
