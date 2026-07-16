import json
import os
from datetime import UTC, datetime, timedelta

import requests


CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cache")
ELO_CACHE_PATH = os.path.join(CACHE_DIR, "elo_ratings_cache.json")
ELO_CACHE_TTL = timedelta(hours=12)

TEAM_CODES = {
    "Argentina": "AR",
    "Spain": "ES",
    "France": "FR",
    "England": "EN",
    "Brazil": "BR",
    "Portugal": "PT",
    "Netherlands": "NL",
    "Germany": "DE",
    "Colombia": "CO",
    "Italy": "IT",
    "Uruguay": "UY",
    "Belgium": "BE",
    "Croatia": "HR",
    "Morocco": "MA",
    "Mexico": "MX",
    "United States": "US",
    "USA": "US",
    "Switzerland": "CH",
    "Japan": "JP",
    "Denmark": "DK",
    "Norway": "NO",
    "Senegal": "SN",
    "Austria": "AT",
    "Canada": "CA",
    "Paraguay": "PY",
    "Egypt": "EG",
    "Australia": "AU",
    "South Africa": "ZA",
    "South Korea": "KR",
    "Korea Republic": "KR",
    "Ivory Coast": "CI",
    "Cote d'Ivoire": "CI",
    "Algeria": "DZ",
    "Ghana": "GH",
    "Turkey": "TR",
    "Sweden": "SE",
    "Ecuador": "EC",
    "Cape Verde": "CV",
    "Cape Verde Islands": "CV",
    "DR Congo": "CD",
    "Congo DR": "CD",
    "Bosnia and Herzegovina": "BA",
    "Czechia": "CZ",
    "Czech Republic": "CZ",
    "Qatar": "QA",
    "Tunisia": "TN",
    "Saudi Arabia": "SA",
    "Iran": "IR",
    "Iraq": "IQ",
    "New Zealand": "NZ",
    "Uzbekistan": "UZ",
    "Panama": "PA",
    "Haiti": "HT",
    "Scotland": "SC",
}


def _load_cached_elos(max_age: timedelta | None = ELO_CACHE_TTL) -> dict | None:
    if not os.path.exists(ELO_CACHE_PATH):
        return None

    try:
        with open(ELO_CACHE_PATH, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None

    fetched_at_raw = payload.get("fetched_at_utc")
    elos = payload.get("elos")

    if not fetched_at_raw or not isinstance(elos, dict):
        return None

    try:
        fetched_at = datetime.fromisoformat(str(fetched_at_raw))
    except ValueError:
        return None

    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=UTC)

    if max_age is not None and datetime.now(UTC) - fetched_at > max_age:
        return None

    return elos


def _save_cached_elos(elos: dict) -> None:
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(ELO_CACHE_PATH, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "fetched_at_utc": datetime.now(UTC).isoformat(),
                    "elos": elos,
                },
                fh,
            )
    except OSError:
        return


def obtener_elos(force_refresh: bool = False):
    """
    Descarga ratings reales desde World Football Elo Ratings.
    Usa cache local para evitar descargas repetidas y hace fallback a la ultima
    cache disponible si la fuente remota falla temporalmente.
    """
    if not force_refresh:
        cached = _load_cached_elos()
        if cached:
            return cached

    url = "https://www.eloratings.net/World.tsv"
    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
    except requests.RequestException:
        cached = _load_cached_elos(max_age=None)
        if cached:
            return cached
        raise

    elos = {}

    for line in response.text.splitlines():
        parts = line.strip().split("\t")

        if len(parts) < 4:
            continue

        try:
            code = parts[2]
            rating = int(parts[3])
            elos[code] = rating
        except ValueError:
            continue

    if not elos:
        raise Exception("No se pudieron leer ratings ELO desde World.tsv")

    _save_cached_elos(elos)
    return elos


def obtener_elo_equipo(nombre_equipo, elos):
    code = TEAM_CODES.get(nombre_equipo)

    if code and code in elos:
        return elos[code]

    return None


def probabilidad_elo(equipo, rival, elos):
    elo_equipo = obtener_elo_equipo(equipo, elos)
    elo_rival = obtener_elo_equipo(rival, elos)

    if elo_equipo is None or elo_rival is None:
        return None, elo_equipo, elo_rival

    diferencia = elo_equipo - elo_rival
    prob = 1 / (1 + 10 ** (-diferencia / 400))

    return prob, elo_equipo, elo_rival
