import requests


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
    "Côte d'Ivoire": "CI",
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


def obtener_elos():
    """
    Descarga ratings reales desde World Football Elo Ratings.
    Fuente TSV pública de eloratings.net.
    """
    url = "https://www.eloratings.net/World.tsv"

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    r = requests.get(url, headers=headers, timeout=15)
    r.raise_for_status()

    elos = {}

    for line in r.text.splitlines():
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