from dataclasses import asdict, dataclass
import math
from typing import Any

from elo import obtener_elo_equipo


DRAW_NAMES = {"draw", "tie", "empate"}
POINT_TOTAL_MARKETS = {
    "totals",
    "alternate_totals",
    "totals_h1",
    "totals_h2",
    "alternate_totals_corners",
    "alternate_totals_cards",
}
TEAM_TOTAL_MARKETS = {
    "team_totals",
    "alternate_team_totals",
    "alternate_team_totals_corners",
}
NON_EXCLUSIVE_MARKETS = {"double_chance"}
HIGH_RELIABILITY_BOOKMAKERS = {"pinnacle", "bet365", "betfair", "betfairsportsbook", "william hill"}
MEDIUM_RELIABILITY_BOOKMAKERS = {"unibet", "1xbet", "bwin", "marathonbet"}
HIGH_RELIABILITY_MARKETS = {"h2h", "spreads", "totals"}
MEDIUM_RELIABILITY_MARKETS = {"alternate_totals", "btts", "team_totals", "double_chance"}
TOP_SOCCER_LEAGUE_HINTS = (
    "fifa_world_cup",
    "uefa_champions_league",
    "uefa_european_championship",
    "england_premier_league",
    "spain_la_liga",
    "italy_serie_a",
    "germany_bundesliga",
    "france_ligue_1",
    "netherlands_eredivisie",
    "portugal_primeira_liga",
    "brazil_serie_a",
    "argentina_primera_division",
    "usa_mls",
)
MID_SOCCER_LEAGUE_HINTS = (
    "championship",
    "league_one",
    "league_two",
    "segunda",
    "serie_b",
    "bundesliga2",
    "super_lig",
    "j_league",
    "k_league",
    "allsvenskan",
    "eliteserien",
)
TOP_TENNIS_HINTS = ("atp_", "wta_", "wimbledon", "us_open", "roland_garros", "french_open", "australian_open")
TOP_BASKET_HINTS = ("nba", "wnba", "euroleague", "acb", "ncaa")

MAX_CUOTA_RECOMENDADA = 7
MIN_MARGEN_CUOTA = 1.01
MIN_VALOR_ESPERADO = 0.01
FRACCION_KELLY = 0.15
MAX_STAKE_PCT = 0.015
VALOR_INTERESANTE = 0.08
MARGEN_INTERESANTE = 1.05
MIN_EDGE_ELO_ESPECULATIVO = 0.06
MIN_MARGEN_ESPECULATIVO = 0.96
MIN_VALOR_ESPECULATIVO = -0.03
STAKE_PCT_ESPECULATIVO = 0.0025
H2H_AMBIGUITY_MAX_SCORE_GAP = 4
H2H_AMBIGUITY_MAX_VALUE_GAP = 0.012
H2H_AMBIGUITY_MAX_PROB_GAP = 0.025

STAKE_PROFILES = {
    "conservador": {
        "fraccion_kelly": 0.15,
        "max_stake_pct": 0.015,
        "stake_pct_especulativo": 0.0025,
        "min_importe": 0,
        "min_margen_cuota": 1.03,
        "min_valor_esperado": 0.03,
        "permite_elo_especulativo": False,
        "min_edge_elo_especulativo": 0.10,
        "min_margen_especulativo": 1.00,
        "min_valor_especulativo": 0.00,
    },
    "moderado": {
        "fraccion_kelly": 0.30,
        "max_stake_pct": 0.03,
        "stake_pct_especulativo": 0.01,
        "min_importe": 0.50,
        "min_margen_cuota": 1.015,
        "min_valor_esperado": 0.015,
        "permite_elo_especulativo": True,
        "min_edge_elo_especulativo": 0.08,
        "min_margen_especulativo": 0.98,
        "min_valor_especulativo": -0.01,
    },
    "agresivo": {
        "fraccion_kelly": 0.50,
        "max_stake_pct": 0.08,
        "stake_pct_especulativo": 0.04,
        "min_importe": 1.00,
        "min_margen_cuota": 1.01,
        "min_valor_esperado": 0.01,
        "permite_elo_especulativo": True,
        "min_edge_elo_especulativo": 0.06,
        "min_margen_especulativo": 0.96,
        "min_valor_especulativo": -0.03,
    },
    "alto_riesgo": {
        "fraccion_kelly": 1.00,
        "max_stake_pct": 0.50,
        "stake_pct_especulativo": 0.20,
        "min_importe": 5.00,
        "min_margen_cuota": 1.00,
        "min_valor_esperado": 0.00,
        "permite_elo_especulativo": True,
        "min_edge_elo_especulativo": 0.04,
        "min_margen_especulativo": 0.94,
        "min_valor_especulativo": -0.05,
        "importe_especulativo": 5.00,
        "importe_ligero": 5.00,
        "importe_moderado": 7.50,
        "importe_interesante": 10.00,
    },
}


@dataclass
class BetRecommendation:
    event_id: str | None
    commence_time: str | None
    sport_key: str | None
    sport_label: str | None
    league_key: str | None
    league_label: str | None
    partido: str
    casa: str
    mercado: str
    equipo: str
    tipo_resultado: str
    cuota_pinnacle: float
    cuota_minima_aceptable: float
    margen_cuota: float
    probabilidad_mercado: float
    probabilidad_elo: float | None
    probabilidad_modelo: float
    elo_equipo: int | None
    elo_rival: int | None
    valor_esperado: float
    kelly_fraccional: float
    stake_pct_bankroll: float
    importe_sugerido: float
    stake: float
    recomendacion: str
    motivo: str
    cuota_apuesta: float | None = None
    casa_referencia: str = "Pinnacle"
    cuota_referencia_pinnacle: float | None = None
    ventaja_sobre_pinnacle: float | None = None
    outcome_point: float | None = None
    outcome_description: str | None = None
    modelo_mercado: str | None = None
    confianza: str = "Baja"
    puntuacion_confianza: int = 0
    quality_score: int = 0
    reliability_score: int = 0
    reliability_tier: str = "media"
    elite_pick: bool = False
    elite_tier: str = "descartable"
    source_strength: str = "market+model"
    market_support_count: int = 0
    market_consensus_odds: float | None = None
    market_best_odds: float | None = None
    market_worst_odds: float | None = None
    market_width_pct: float | None = None
    market_edge_vs_consensus: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def probabilidad_implicita(cuota: float) -> float:
    if cuota <= 1:
        return 0
    return 1 / cuota


def valor_esperado(probabilidad_modelo: float, cuota: float) -> float:
    return (probabilidad_modelo * cuota) - 1


def cuota_minima(probabilidad_modelo: float) -> float:
    if probabilidad_modelo <= 0:
        return 0
    return 1 / probabilidad_modelo


def clasificar_resultado(nombre: str, home: str, away: str) -> str:
    nombre_normalizado = nombre.strip().lower()

    if nombre == home:
        return "home"
    if nombre == away:
        return "away"
    if nombre_normalizado in DRAW_NAMES:
        return "draw"

    return "other"


def normalizar_probabilidades(outcomes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cuotas = []

    for outcome in outcomes:
        cuota = outcome.get("price")
        nombre = outcome.get("name")

        if not nombre or not cuota:
            continue

        prob_implicita = probabilidad_implicita(float(cuota))

        if prob_implicita <= 0:
            continue

        cuotas.append({
            "equipo": nombre,
            "cuota": float(cuota),
            "prob_implicita": prob_implicita,
        })

    suma_probs = sum(x["prob_implicita"] for x in cuotas)

    if suma_probs <= 0:
        return []

    for cuota in cuotas:
        cuota["probabilidad_mercado"] = cuota["prob_implicita"] / suma_probs

    return cuotas


def obtener_mercado_h2h(bookmaker: dict[str, Any]) -> dict[str, Any] | None:
    for market in bookmaker.get("markets", []):
        if market.get("key") == "h2h":
            return market

    return None


def obtener_mercado(bookmaker: dict[str, Any], market_key: str) -> dict[str, Any] | None:
    for market in bookmaker.get("markets", []):
        if market.get("key") == market_key:
            return market

    return None


def clave_outcome(market_key: str, outcome: dict[str, Any], home: str, away: str) -> tuple:
    if market_key == "h2h":
        return (market_key, clasificar_resultado(outcome.get("name", ""), home, away), None)

    if market_key in TEAM_TOTAL_MARKETS:
        return (
            market_key,
            outcome.get("name"),
            outcome.get("point"),
            outcome.get("description"),
        )

    if market_key in POINT_TOTAL_MARKETS:
        return (market_key, outcome.get("name"), outcome.get("point"), None)

    return (
        market_key,
        outcome.get("name"),
        outcome.get("point"),
        outcome.get("description"),
    )


def probabilidad_binaria_elo(elo_equipo: int, elo_rival: int) -> float:
    diferencia = elo_equipo - elo_rival
    return 1 / (1 + 10 ** (-diferencia / 400))


def clamp(valor: float, minimo: float, maximo: float) -> float:
    return max(minimo, min(valor, maximo))


def median(values: list[float]) -> float | None:
    cleaned = sorted(float(value) for value in values if value is not None)

    if not cleaned:
        return None

    half = len(cleaned) // 2
    if len(cleaned) % 2:
        return cleaned[half]
    return (cleaned[half - 1] + cleaned[half]) / 2


def is_tennis_doubles_match(home: str | None, away: str | None, sport_key: str | None = None) -> bool:
    sport_key_norm = str(sport_key or "").lower()
    if not sport_key_norm.startswith("tennis_"):
        return False

    home_text = str(home or "").lower()
    away_text = str(away or "").lower()
    doubles_markers = ("/", " & ", " and ")

    return any(marker in home_text for marker in doubles_markers) or any(marker in away_text for marker in doubles_markers)


def tennis_context_profile(sport_key: str | None) -> dict[str, float | str]:
    key = str(sport_key or "").lower()
    profile: dict[str, float | str] = {
        "surface": "unknown",
        "speed": "medium",
        "favorite_bias": 0.0,
        "underdog_penalty": 0.0,
        "variance_penalty": 0.0,
    }

    if any(tag in key for tag in {"wimbledon", "grass", "mallorca", "halle", "eastbourne", "segovia"}):
        profile.update({
            "surface": "fast",
            "speed": "fast",
            "favorite_bias": 0.014,
            "underdog_penalty": 0.009,
        })
    elif any(tag in key for tag in {"us_open", "australian_open", "hard", "indoor"}):
        profile.update({
            "surface": "hard",
            "speed": "medium_fast",
            "favorite_bias": 0.010,
            "underdog_penalty": 0.006,
        })
    elif any(tag in key for tag in {"roland_garros", "french_open", "clay", "estoril", "kitzbuhel", "tampere"}):
        profile.update({
            "surface": "clay",
            "speed": "slow",
            "favorite_bias": 0.006,
            "underdog_penalty": 0.003,
        })

    if any(tag in key for tag in {"segovia", "kitzbuhel", "altitude"}):
        profile["favorite_bias"] = float(profile["favorite_bias"]) + 0.006
        profile["underdog_penalty"] = float(profile["underdog_penalty"]) + 0.004

    if "challenger" in key:
        profile["variance_penalty"] = 0.004
    elif any(tag in key for tag in {"itf", "m15", "m25"}):
        profile["variance_penalty"] = 0.007

    return profile


def poisson_cdf(k: int, media: float) -> float:
    if k < 0:
        return 0

    media = max(media, 0.01)
    total = 0.0

    for i in range(k + 1):
        total += math.exp(-media) * (media ** i) / math.factorial(i)

    return clamp(total, 0, 1)


def probabilidad_over(media: float, linea: float | None) -> float | None:
    if linea is None:
        return None

    return 1 - poisson_cdf(math.floor(float(linea)), media)


def ajustar_probabilidad_por_mercado(
    market_key: str,
    nombre: str,
    point: float | None,
    description: str | None,
    prob_mercado: float,
    home: str,
    away: str,
    elos: dict[str, int],
    sport_key: str | None = None,
) -> tuple[float, str | None]:
    elo_home = obtener_elo_equipo(home, elos)
    elo_away = obtener_elo_equipo(away, elos)
    elo_diff = 0 if elo_home is None or elo_away is None else elo_home - elo_away
    nombre_norm = nombre.lower()
    sport_key = (sport_key or "").lower()

    def mezclar(prob_estimada: float, peso_estimacion: float, etiqueta: str) -> tuple[float, str]:
        prob = (prob_mercado * (1 - peso_estimacion)) + (prob_estimada * peso_estimacion)
        return clamp(prob, 0.01, 0.99), etiqueta

    if sport_key.startswith("tennis_") and market_key == "h2h":
        profile = tennis_context_profile(sport_key)
        favorite_bias = float(profile["favorite_bias"] or 0)
        underdog_penalty = float(profile["underdog_penalty"] or 0)
        variance_penalty = float(profile["variance_penalty"] or 0)
        ajuste = 0.0

        if prob_mercado >= 0.74:
            ajuste = 0.022 + favorite_bias - variance_penalty
        elif prob_mercado >= 0.64:
            ajuste = 0.014 + favorite_bias - variance_penalty
        elif prob_mercado >= 0.56:
            ajuste = 0.008 + (favorite_bias * 0.6) - variance_penalty
        elif prob_mercado <= 0.34:
            ajuste = -(0.014 + underdog_penalty + variance_penalty)
        elif prob_mercado <= 0.42:
            ajuste = -(0.008 + (underdog_penalty * 0.8) + variance_penalty)

        etiqueta = f"Tenis singles {profile['surface']} conservador"
        return clamp(prob_mercado + ajuste, 0.01, 0.99), etiqueta

    if sport_key.startswith("basketball_"):
        if market_key == "h2h":
            ajuste = 0.0

            if prob_mercado >= 0.64:
                ajuste = 0.015
            elif prob_mercado >= 0.56:
                ajuste = 0.008
            elif prob_mercado <= 0.36:
                ajuste = -0.012

            return clamp(prob_mercado + ajuste, 0.01, 0.99), "Basket moneyline conservador"

        if market_key in {"totals", "alternate_totals"}:
            if point is None:
                return prob_mercado, None

            baseline = 221.5
            spread = 15.0
            etiqueta = "Basket total baseline"

            if "wnba" in sport_key:
                baseline = 164.5
                spread = 11.0
                etiqueta = "WNBA total baseline"
            elif any(tag in sport_key for tag in {"ncaab", "ncaa"}):
                baseline = 146.5
                spread = 10.5
                etiqueta = "NCAA total baseline"

            prob_over = clamp(0.5 + ((baseline - float(point)) / spread), 0.08, 0.92)

            if nombre_norm == "over":
                return mezclar(prob_over, 0.12, etiqueta)
            if nombre_norm == "under":
                return mezclar(1 - prob_over, 0.12, etiqueta)

    if market_key in {"totals", "alternate_totals", "totals_h1", "totals_h2"}:
        factor_tiempo = 0.46 if market_key == "totals_h1" else 0.54 if market_key == "totals_h2" else 1.0
        media = (2.55 + min(abs(elo_diff) / 900, 0.35)) * factor_tiempo
        prob_over = probabilidad_over(media, point)

        if prob_over is None:
            return prob_mercado, None
        if nombre_norm == "over":
            return mezclar(prob_over, 0.20, "Poisson goles")
        if nombre_norm == "under":
            return mezclar(1 - prob_over, 0.20, "Poisson goles")

    if market_key in {"team_totals", "alternate_team_totals"}:
        edge = 0

        if description == home:
            edge = elo_diff
        elif description == away:
            edge = -elo_diff

        media = clamp(1.25 + (edge / 500), 0.55, 2.60)
        prob_over = probabilidad_over(media, point)

        if prob_over is None:
            return prob_mercado, None
        if nombre_norm == "over":
            return mezclar(prob_over, 0.25, "Poisson goles por equipo")
        if nombre_norm == "under":
            return mezclar(1 - prob_over, 0.25, "Poisson goles por equipo")

    if market_key == "alternate_totals_corners":
        media = 9.50 + min(abs(elo_diff) / 700, 0.80)
        prob_over = probabilidad_over(media, point)

        if prob_over is None:
            return prob_mercado, None
        if nombre_norm == "over":
            return mezclar(prob_over, 0.15, "Poisson corners")
        if nombre_norm == "under":
            return mezclar(1 - prob_over, 0.15, "Poisson corners")

    if market_key == "alternate_team_totals_corners":
        edge = 0

        if description == home:
            edge = elo_diff
        elif description == away:
            edge = -elo_diff

        media = clamp(4.70 + (edge / 550), 2.50, 7.20)
        prob_over = probabilidad_over(media, point)

        if prob_over is None:
            return prob_mercado, None
        if nombre_norm == "over":
            return mezclar(prob_over, 0.15, "Poisson corners por equipo")
        if nombre_norm == "under":
            return mezclar(1 - prob_over, 0.15, "Poisson corners por equipo")

    if market_key == "alternate_totals_cards":
        media = 4.50 + min(abs(elo_diff) / 800, 0.50)
        prob_over = probabilidad_over(media, point)

        if prob_over is None:
            return prob_mercado, None
        if nombre_norm == "over":
            return mezclar(prob_over, 0.10, "Poisson tarjetas")
        if nombre_norm == "under":
            return mezclar(1 - prob_over, 0.10, "Poisson tarjetas")

    if market_key == "btts":
        media_home = clamp(1.25 + (elo_diff / 500), 0.55, 2.60)
        media_away = clamp(1.25 - (elo_diff / 500), 0.55, 2.60)
        prob_si = (1 - poisson_cdf(0, media_home)) * (1 - poisson_cdf(0, media_away))

        if nombre_norm == "yes":
            return mezclar(prob_si, 0.20, "Poisson ambos anotan")
        if nombre_norm == "no":
            return mezclar(1 - prob_si, 0.20, "Poisson ambos anotan")

    return prob_mercado, None


def calcular_kelly_fraccional(
    probabilidad_modelo: float,
    cuota: float,
    fraccion_kelly: float = FRACCION_KELLY,
    max_stake_pct: float = MAX_STAKE_PCT,
) -> float:
    if cuota <= 1 or probabilidad_modelo <= 0:
        return 0

    kelly_completo = ((cuota - 1) * probabilidad_modelo - (1 - probabilidad_modelo)) / (cuota - 1)
    kelly_completo = max(0, kelly_completo)
    return min(kelly_completo * fraccion_kelly, max_stake_pct)


def obtener_perfil_stake(perfil: str) -> dict[str, float]:
    return STAKE_PROFILES.get(perfil, STAKE_PROFILES["moderado"])


def _aggressive_profile(perfil: str) -> bool:
    return str(perfil or "").strip().lower() == "agresivo"


def _pick_sort_strength(pick: dict[str, Any]) -> tuple[float, ...]:
    return (
        float(pick.get("stake") or 0),
        float(pick.get("quality_score") or 0),
        float(pick.get("puntuacion_confianza") or 0),
        float(pick.get("valor_esperado") or 0),
        float(pick.get("probabilidad_modelo") or 0),
    )


def _apply_h2h_ambiguity_guard(recomendaciones: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[int]] = {}

    for index, pick in enumerate(recomendaciones):
        if str(pick.get("mercado") or "").strip().lower() != "h2h":
            continue
        if float(pick.get("stake") or 0) <= 0:
            continue

        event_id = str(pick.get("event_id") or "").strip()
        if not event_id:
            continue

        key = (event_id, "h2h")
        grouped.setdefault(key, []).append(index)

    for indexes in grouped.values():
        active = [
            recomendaciones[idx]
            for idx in indexes
            if str(recomendaciones[idx].get("tipo_resultado") or "").strip().lower() != "draw"
        ]
        if len(active) < 2:
            continue

        ordered = sorted(active, key=_pick_sort_strength, reverse=True)
        top = ordered[0]
        runner_up = ordered[1]
        top_side = str(top.get("tipo_resultado") or "").strip().lower()
        runner_up_side = str(runner_up.get("tipo_resultado") or "").strip().lower()

        if not top_side or not runner_up_side or top_side == runner_up_side:
            continue

        score_gap = abs(float(top.get("quality_score") or 0) - float(runner_up.get("quality_score") or 0))
        value_gap = abs(float(top.get("valor_esperado") or 0) - float(runner_up.get("valor_esperado") or 0))
        prob_gap = abs(float(top.get("probabilidad_modelo") or 0) - float(runner_up.get("probabilidad_modelo") or 0))

        if (
            score_gap > H2H_AMBIGUITY_MAX_SCORE_GAP
            or value_gap > H2H_AMBIGUITY_MAX_VALUE_GAP
            or prob_gap > H2H_AMBIGUITY_MAX_PROB_GAP
        ):
            continue

        for idx in indexes:
            pick = recomendaciones[idx].copy()
            if float(pick.get("stake") or 0) <= 0:
                continue
            pick["stake"] = 0
            pick["importe_sugerido"] = 0
            pick["stake_pct_bankroll"] = 0
            pick["kelly_fraccional"] = 0
            pick["recomendacion"] = "No apostar"
            pick["motivo"] = (
                "Mercado h2h ambiguo: los dos lados salen demasiado parejos "
                "y el sistema evita publicar una senal inestable."
            )
            recomendaciones[idx] = pick

    return recomendaciones


def aplicar_importe_minimo(
    stake_pct: float,
    bankroll: float,
    min_importe: float,
    max_stake_pct: float,
) -> tuple[float, float]:
    if bankroll <= 0 or stake_pct <= 0:
        return 0, 0

    importe = round(bankroll * stake_pct, 2)

    if min_importe > 0 and importe < min_importe:
        stake_pct_minimo = min_importe / bankroll

        if stake_pct_minimo <= max_stake_pct:
            stake_pct = stake_pct_minimo
            importe = min_importe

    return stake_pct, round(importe, 2)


def aplicar_importe_objetivo(
    stake_pct: float,
    bankroll: float,
    importe_objetivo: float | None,
    max_stake_pct: float,
) -> tuple[float, float]:
    if bankroll <= 0 or stake_pct <= 0:
        return 0, 0

    if not importe_objetivo or importe_objetivo <= 0:
        return stake_pct, round(bankroll * stake_pct, 2)

    stake_pct_objetivo = importe_objetivo / bankroll

    if stake_pct_objetivo <= max_stake_pct:
        return stake_pct_objetivo, round(importe_objetivo, 2)

    stake_pct = min(stake_pct, max_stake_pct)
    return stake_pct, round(bankroll * stake_pct, 2)


def stake_en_unidades(stake_pct_bankroll: float) -> float:
    if stake_pct_bankroll <= 0:
        return 0
    if stake_pct_bankroll < 0.003:
        return 0.25
    if stake_pct_bankroll < 0.006:
        return 0.5
    if stake_pct_bankroll < 0.01:
        return 1
    if stake_pct_bankroll < 0.015:
        return 1.5
    if stake_pct_bankroll < 0.03:
        return 2
    if stake_pct_bankroll < 0.08:
        return 2.5
    if stake_pct_bankroll < 0.20:
        return 3
    if stake_pct_bankroll < 0.35:
        return 4
    return 5


def market_consensus_snapshot(
    partido: dict[str, Any],
    market_key: str,
    outcome_key: tuple,
    selected_odds: float | None = None,
) -> dict[str, Any]:
    prices: list[float] = []

    for bookmaker in partido.get("bookmakers", []):
        market = obtener_mercado(bookmaker, market_key)
        if not market:
            continue

        for outcome in market.get("outcomes", []):
            price = outcome.get("price")
            if not price:
                continue

            key = clave_outcome(
                market_key,
                outcome,
                str(partido.get("home_team") or ""),
                str(partido.get("away_team") or ""),
            )
            if key == outcome_key:
                prices.append(float(price))
                break

    support_count = len(prices)
    consensus_odds = median(prices)
    best_odds = max(prices) if prices else None
    worst_odds = min(prices) if prices else None
    width_pct = ((best_odds / worst_odds) - 1) if best_odds and worst_odds and worst_odds > 0 else None
    edge_vs_consensus = (
        ((float(selected_odds) / consensus_odds) - 1)
        if selected_odds and consensus_odds and consensus_odds > 0
        else None
    )

    return {
        "support_count": support_count,
        "consensus_odds": round(consensus_odds, 3) if consensus_odds is not None else None,
        "best_odds": round(best_odds, 3) if best_odds is not None else None,
        "worst_odds": round(worst_odds, 3) if worst_odds is not None else None,
        "width_pct": round(width_pct, 4) if width_pct is not None else None,
        "edge_vs_consensus": round(edge_vs_consensus, 4) if edge_vs_consensus is not None else None,
    }


def clasificar_fiabilidad_liga(sport_key: str | None, league_key: str | None) -> tuple[str, int]:
    sport_key_norm = str(sport_key or "").lower()
    league_key_norm = str(league_key or "").lower()
    referencia = f"{sport_key_norm} {league_key_norm}"

    if sport_key_norm.startswith("soccer_"):
        if any(hint in referencia for hint in TOP_SOCCER_LEAGUE_HINTS):
            return "alta", 18
        if any(hint in referencia for hint in MID_SOCCER_LEAGUE_HINTS):
            return "media", 8
        return "baja", -10

    if sport_key_norm.startswith("tennis_"):
        if any(hint in referencia for hint in TOP_TENNIS_HINTS):
            return "alta", 16
        return "media", 6

    if sport_key_norm.startswith("basketball_"):
        if any(hint in referencia for hint in TOP_BASKET_HINTS):
            return "alta", 16
        return "media", 5

    return "baja", -8


def clasificar_fiabilidad_mercado(market_key: str) -> tuple[str, int]:
    market_key_norm = str(market_key or "").lower()

    if market_key_norm in HIGH_RELIABILITY_MARKETS:
        return "alta", 12
    if market_key_norm in MEDIUM_RELIABILITY_MARKETS:
        return "media", 5
    return "baja", -8


def clasificar_fiabilidad_bookmaker(casa: str | None) -> tuple[str, int]:
    casa_norm = str(casa or "").strip().lower()

    if casa_norm in HIGH_RELIABILITY_BOOKMAKERS:
        return "alta", 8
    if casa_norm in MEDIUM_RELIABILITY_BOOKMAKERS:
        return "media", 3
    return "baja", -4


def calcular_fiabilidad_pick(
    sport_key: str | None,
    league_key: str | None,
    market_key: str,
    casa: str | None,
    source_strength: str,
    market_support_count: int | None = None,
    market_width_pct: float | None = None,
) -> tuple[int, str]:
    score = 50
    league_tier, league_adj = clasificar_fiabilidad_liga(sport_key, league_key)
    _, market_adj = clasificar_fiabilidad_mercado(market_key)
    _, casa_adj = clasificar_fiabilidad_bookmaker(casa)
    score += league_adj + market_adj + casa_adj

    if source_strength == "market+model":
        score += 10
    elif source_strength in {"tennis_model", "basketball_model"}:
        score += 6
    else:
        score -= 6

    if market_support_count is not None:
        if market_support_count >= 6:
            score += 10
        elif market_support_count >= 4:
            score += 6
        elif market_support_count >= 2:
            score += 2
        elif market_support_count == 1:
            score -= 6

    if market_width_pct is not None:
        if market_width_pct <= 0.02:
            score += 4
        elif market_width_pct <= 0.04:
            score += 2
        elif market_width_pct >= 0.12:
            score -= 8
        elif market_width_pct >= 0.08:
            score -= 5
        elif market_width_pct >= 0.05:
            score -= 2

    score = max(0, min(score, 100))

    if score >= 75:
        return score, "alta"
    if score >= 55:
        return score, "media"
    return score, "baja"


def calcular_confianza(
    valor: float,
    margen_cuota: float,
    ventaja_sobre_pinnacle: float | None,
    probabilidad_elo: float | None,
    probabilidad_mercado: float,
    source_strength: str = "market+model",
    market_support_count: int | None = None,
    market_width_pct: float | None = None,
    edge_vs_consensus: float | None = None,
) -> tuple[str, int]:
    score = 0

    if valor >= 0.10:
        score += 35
    elif valor >= 0.05:
        score += 25
    elif valor >= 0.02:
        score += 15
    elif valor >= 0:
        score += 8

    if margen_cuota >= 1.10:
        score += 25
    elif margen_cuota >= 1.05:
        score += 18
    elif margen_cuota >= 1.02:
        score += 10
    elif margen_cuota >= 1:
        score += 5

    if ventaja_sobre_pinnacle is not None:
        if ventaja_sobre_pinnacle >= 0.08:
            score += 25
        elif ventaja_sobre_pinnacle >= 0.04:
            score += 18
        elif ventaja_sobre_pinnacle >= 0.02:
            score += 10

    if edge_vs_consensus is not None:
        if edge_vs_consensus >= 0.05:
            score += 10
        elif edge_vs_consensus >= 0.025:
            score += 6
        elif edge_vs_consensus >= 0.01:
            score += 3

    if probabilidad_elo is not None:
        edge_elo = probabilidad_elo - probabilidad_mercado

        if edge_elo >= 0.10:
            score += 15
        elif edge_elo >= 0.06:
            score += 10
        elif edge_elo >= 0.03:
            score += 5

    if source_strength == "market_only":
        score = min(score, 72)
        if valor >= 0.04 and margen_cuota >= 1.04:
            score += 4
    elif source_strength in {"tennis_model", "basketball_model"}:
        score = min(score + 2, 84)

    if market_support_count is not None:
        if market_support_count >= 6:
            score += 8
        elif market_support_count >= 4:
            score += 5
        elif market_support_count >= 2:
            score += 2
        elif market_support_count == 1:
            score -= 4

    if market_width_pct is not None:
        if market_width_pct <= 0.02:
            score += 4
        elif market_width_pct <= 0.04:
            score += 2
        elif market_width_pct >= 0.12:
            score -= 8
        elif market_width_pct >= 0.08:
            score -= 5
        elif market_width_pct >= 0.05:
            score -= 2

    score = max(0, min(score, 100))

    if score >= 75:
        return "Alta", score
    if score >= 45:
        return "Media", score
    return "Baja", score


def decidir_stake(
    bankroll: float,
    probabilidad_modelo: float,
    probabilidad_mercado: float,
    cuota: float,
    margen_cuota: float,
    probabilidad_elo: float | None,
    valor: float,
    perfil: str = "moderado",
    source_strength: str = "market+model",
) -> tuple[float, float, float, str, str]:
    perfil_stake = obtener_perfil_stake(perfil)

    if bankroll <= 0:
        return 0, 0, 0, "No apostar", "Bankroll no valido"

    if cuota > MAX_CUOTA_RECOMENDADA:
        return 0, 0, 0, "No apostar", "Cuota demasiado alta para esta version"

    if probabilidad_elo is None and source_strength == "market+model":
        return 0, 0, 0, "No apostar", "Sin ELO fiable para contrastar el mercado"

    if source_strength == "market_only":
        min_margin = max(perfil_stake["min_margen_cuota"], 1.025 if _aggressive_profile(perfil) else 1.03)
        min_value = max(perfil_stake["min_valor_esperado"], 0.02 if _aggressive_profile(perfil) else 0.03)
        if cuota > 3.50:
            return 0, 0, 0, "No apostar", "Cuota demasiado alta sin una senal estadistica propia fuerte"
        if margen_cuota < min_margin:
            return 0, 0, 0, "No apostar", "Sin ELO solo aceptamos margen claro frente a mercado"
        if valor < min_value:
            return 0, 0, 0, "No apostar", "Sin ELO solo aceptamos value claramente positivo"
    elif source_strength == "tennis_model":
        min_margin = max(perfil_stake["min_margen_cuota"], 1.012 if _aggressive_profile(perfil) else 1.018)
        min_value = max(perfil_stake["min_valor_esperado"], 0.01 if _aggressive_profile(perfil) else 0.015)
        if cuota > 2.85:
            return 0, 0, 0, "No apostar", "En tenis solo buscamos favoritos o cuotas medias muy justificadas"
        if margen_cuota < min_margin:
            return 0, 0, 0, "No apostar", "En tenis exigimos margen claro frente al precio de mercado"
        if valor < min_value:
            return 0, 0, 0, "No apostar", "En tenis exigimos value positivo y estable"
    elif source_strength == "basketball_model":
        min_margin = max(perfil_stake["min_margen_cuota"], 1.015 if _aggressive_profile(perfil) else 1.02)
        min_value = max(perfil_stake["min_valor_esperado"], 0.015 if _aggressive_profile(perfil) else 0.02)
        if cuota > 2.80:
            return 0, 0, 0, "No apostar", "En baloncesto evitamos cuotas largas en esta fase"
        if margen_cuota < min_margin:
            return 0, 0, 0, "No apostar", "En baloncesto exigimos margen suficiente frente al mercado"
        if valor < min_value:
            return 0, 0, 0, "No apostar", "En baloncesto exigimos value claramente positivo"

    edge_elo = (probabilidad_elo - probabilidad_mercado) if probabilidad_elo is not None else 0
    es_value_elo_especulativo = (
        source_strength == "market+model"
        and
        perfil_stake["permite_elo_especulativo"]
        and edge_elo >= perfil_stake["min_edge_elo_especulativo"]
        and margen_cuota >= perfil_stake["min_margen_especulativo"]
        and valor >= perfil_stake["min_valor_especulativo"]
    )

    if margen_cuota < perfil_stake["min_margen_cuota"]:
        if es_value_elo_especulativo:
            stake_pct, importe = aplicar_importe_minimo(
                perfil_stake["stake_pct_especulativo"],
                bankroll,
                perfil_stake["min_importe"],
                perfil_stake["max_stake_pct"],
            )
            stake_pct, importe = aplicar_importe_objetivo(
                stake_pct,
                bankroll,
                perfil_stake.get("importe_especulativo"),
                perfil_stake["max_stake_pct"],
            )
            return (
                stake_pct,
                importe,
                stake_en_unidades(stake_pct),
                "Value ELO especulativo",
                "El ELO supera claramente al mercado, pero la cuota aun queda justa",
            )

        return 0, 0, 0, "No apostar", "Margen insuficiente frente a la cuota minima"

    if valor < perfil_stake["min_valor_esperado"]:
        if es_value_elo_especulativo:
            stake_pct, importe = aplicar_importe_minimo(
                perfil_stake["stake_pct_especulativo"],
                bankroll,
                perfil_stake["min_importe"],
                perfil_stake["max_stake_pct"],
            )
            stake_pct, importe = aplicar_importe_objetivo(
                stake_pct,
                bankroll,
                perfil_stake.get("importe_especulativo"),
                perfil_stake["max_stake_pct"],
            )
            return (
                stake_pct,
                importe,
                stake_en_unidades(stake_pct),
                "Value ELO especulativo",
                "El ELO supera claramente al mercado, pero el value combinado es pequeno",
            )

        return 0, 0, 0, "No apostar", "Valor esperado por debajo del filtro minimo"

    stake_pct = calcular_kelly_fraccional(
        probabilidad_modelo,
        cuota,
        fraccion_kelly=perfil_stake["fraccion_kelly"],
        max_stake_pct=perfil_stake["max_stake_pct"],
    )
    stake_pct, importe = aplicar_importe_minimo(
        stake_pct,
        bankroll,
        perfil_stake["min_importe"],
        perfil_stake["max_stake_pct"],
    )
    unidades = stake_en_unidades(stake_pct)

    if stake_pct <= 0 or importe <= 0:
        return 0, 0, 0, "No apostar", "Kelly fraccional no recomienda exposicion"

    if valor >= VALOR_INTERESANTE and margen_cuota >= MARGEN_INTERESANTE:
        stake_pct, importe = aplicar_importe_objetivo(
            stake_pct,
            bankroll,
            perfil_stake.get("importe_interesante"),
            perfil_stake["max_stake_pct"],
        )
        unidades = stake_en_unidades(stake_pct)
        return stake_pct, importe, unidades, "Value interesante", "Filtro de value y margen superado"

    if valor >= 0.03:
        stake_pct, importe = aplicar_importe_objetivo(
            stake_pct,
            bankroll,
            perfil_stake.get("importe_moderado"),
            perfil_stake["max_stake_pct"],
        )
        unidades = stake_en_unidades(stake_pct)
        return stake_pct, importe, unidades, "Value moderado", "Value positivo con exposicion controlada"

    stake_pct, importe = aplicar_importe_objetivo(
        stake_pct,
        bankroll,
        perfil_stake.get("importe_ligero"),
        perfil_stake["max_stake_pct"],
    )
    unidades = stake_en_unidades(stake_pct)
    return stake_pct, importe, unidades, "Value ligero", "Value pequeno aceptado con stake minimo"


def clasificar_pick_elite(
    stake: float,
    confianza: str,
    puntuacion_confianza: int,
    valor: float,
    margen_cuota: float,
    cuota: float,
    source_strength: str,
    sport_key: str | None = None,
    league_key: str | None = None,
    market_key: str = "h2h",
    casa: str | None = None,
    market_support_count: int | None = None,
    market_width_pct: float | None = None,
    edge_vs_consensus: float | None = None,
) -> tuple[bool, str, int]:
    if stake <= 0:
        return False, "descartable", 0

    score = puntuacion_confianza
    reliability_score, reliability_tier = calcular_fiabilidad_pick(
        sport_key=sport_key,
        league_key=league_key,
        market_key=market_key,
        casa=casa,
        source_strength=source_strength,
        market_support_count=market_support_count,
        market_width_pct=market_width_pct,
    )
    score += round((reliability_score - 50) * 0.55)

    if valor >= 0.08:
        score += 12
    elif valor >= 0.05:
        score += 8
    elif valor >= 0.03:
        score += 4

    if margen_cuota >= 1.10:
        score += 10
    elif margen_cuota >= 1.06:
        score += 6
    elif margen_cuota >= 1.03:
        score += 3

    if cuota <= 2.20:
        score += 5
    elif cuota <= 3.00:
        score += 2

    if source_strength == "market_only":
        score -= 8

    if market_support_count is not None:
        if market_support_count >= 5:
            score += 6
        elif market_support_count >= 3:
            score += 3
        elif market_support_count == 1:
            score -= 5

    if edge_vs_consensus is not None:
        if edge_vs_consensus >= 0.05:
            score += 8
        elif edge_vs_consensus >= 0.03:
            score += 5
        elif edge_vs_consensus >= 0.015:
            score += 2

    if market_width_pct is not None:
        if market_width_pct <= 0.025:
            score += 4
        elif market_width_pct >= 0.12:
            score -= 7
        elif market_width_pct >= 0.08:
            score -= 4

    score = max(0, min(score, 100))

    if reliability_tier == "alta" and confianza == "Alta" and score >= 82 and (market_support_count is None or market_support_count >= 3):
        return True, "stakazo", score
    if reliability_tier != "baja" and score >= 68:
        return True, "elite", score
    if score >= 58:
        return False, "premium", score
    return False, "seguimiento", score


def rescatar_casi_value(
    bankroll: float,
    perfil: str,
    stake: float,
    valor: float,
    margen_cuota: float,
    cuota: float,
    ventaja_sobre_pinnacle: float | None,
    confianza: str,
    puntuacion_confianza: int,
    reliability_tier: str,
    source_strength: str,
    market_key: str,
) -> tuple[float, float, float, str, str]:
    if stake > 0:
        return 0, 0, stake, "", ""
    if market_key != "h2h":
        return 0, 0, stake, "", ""
    if source_strength not in {"market+model", "tennis_model", "basketball_model"}:
        return 0, 0, stake, "", ""
    if reliability_tier == "baja":
        return 0, 0, stake, "", ""
    if confianza not in {"Alta", "Media"} or puntuacion_confianza < 52:
        return 0, 0, stake, "", ""
    if ventaja_sobre_pinnacle is None or ventaja_sobre_pinnacle < 0.018:
        return 0, 0, stake, "", ""
    if margen_cuota < 0.988 or valor < -0.008:
        return 0, 0, stake, "", ""
    if cuota > 2.35:
        return 0, 0, stake, "", ""
    if bankroll <= 0:
        return 0, 0, stake, "", ""

    stake_pct = max(0.003, min(0.006, 0.75 / bankroll))
    importe = round(bankroll * stake_pct, 2)

    if importe <= 0:
        return 0, 0, stake, "", ""

    return (
        stake_pct,
        importe,
        stake_en_unidades(stake_pct),
        "Premium cerca del valor",
        "Cuota mejor que Pinnacle y muy cerca del umbral de value; se acepta micro-stake controlado",
    )


def construir_modelo_referencia(
    partido: dict[str, Any],
    elos: dict[str, int],
    casa_referencia: str = "Pinnacle",
    peso_mercado: float = 0.90,
    peso_elo: float = 0.10,
) -> dict[str, dict[str, Any]]:
    home = partido.get("home_team")
    away = partido.get("away_team")

    if not home or not away:
        return {}

    bookmaker_referencia = None

    for bookmaker in partido.get("bookmakers", []):
        if bookmaker.get("title") == casa_referencia:
            bookmaker_referencia = bookmaker
            break

    if not bookmaker_referencia:
        return {}

    market = obtener_mercado_h2h(bookmaker_referencia)

    if not market:
        return {}

    cuotas_referencia = normalizar_probabilidades(market.get("outcomes", []))

    if not cuotas_referencia:
        return {}

    mercado_por_tipo = {
        clasificar_resultado(x["equipo"], home, away): x["probabilidad_mercado"]
        for x in cuotas_referencia
    }
    cuota_ref_por_tipo = {
        clasificar_resultado(x["equipo"], home, away): x["cuota"]
        for x in cuotas_referencia
    }
    nombre_por_tipo = {
        clasificar_resultado(x["equipo"], home, away): x["equipo"]
        for x in cuotas_referencia
    }
    prob_draw_mercado = mercado_por_tipo.get("draw", 0)
    masa_sin_empate = max(0, 1 - prob_draw_mercado)

    elo_home = obtener_elo_equipo(home, elos)
    elo_away = obtener_elo_equipo(away, elos)
    prob_home_elo_binaria = None

    if elo_home is not None and elo_away is not None:
        prob_home_elo_binaria = probabilidad_binaria_elo(elo_home, elo_away)

    modelo = {}

    for tipo_resultado, prob_mercado in mercado_por_tipo.items():
        prob_elo = None
        elo_equipo = None
        elo_rival = None

        if prob_home_elo_binaria is not None:
            if tipo_resultado == "home":
                prob_elo = prob_home_elo_binaria * masa_sin_empate
                elo_equipo = elo_home
                elo_rival = elo_away
            elif tipo_resultado == "away":
                prob_elo = (1 - prob_home_elo_binaria) * masa_sin_empate
                elo_equipo = elo_away
                elo_rival = elo_home
            elif tipo_resultado == "draw":
                prob_elo = prob_draw_mercado

        prob_modelo = prob_mercado

        if prob_elo is not None:
            prob_modelo = (prob_mercado * peso_mercado) + (prob_elo * peso_elo)

        modelo[tipo_resultado] = {
            "equipo": nombre_por_tipo.get(tipo_resultado),
            "probabilidad_mercado": prob_mercado,
            "probabilidad_elo": prob_elo,
            "probabilidad_modelo": prob_modelo,
            "elo_equipo": elo_equipo,
            "elo_rival": elo_rival,
            "cuota_referencia": cuota_ref_por_tipo.get(tipo_resultado),
            "modelo_mercado": "Mercado + ELO",
        }

    return modelo


def construir_modelo_referencia_generico(
    partido: dict[str, Any],
    elos: dict[str, int],
    market_key: str,
    casa_referencia: str = "Pinnacle",
) -> dict[tuple, dict[str, Any]]:
    sport_key = str(partido.get("sport_key") or "").lower()

    if market_key == "h2h":
        if sport_key.startswith("tennis_") or sport_key.startswith("basketball_"):
            home = partido.get("home_team")
            away = partido.get("away_team")

            if not home or not away:
                return {}

            bookmaker_referencia = None

            for bookmaker in partido.get("bookmakers", []):
                if bookmaker.get("title") == casa_referencia:
                    bookmaker_referencia = bookmaker
                    break

            if not bookmaker_referencia:
                return {}

            market = obtener_mercado_h2h(bookmaker_referencia)

            if not market:
                return {}

            cuotas_referencia = normalizar_probabilidades(market.get("outcomes", []))
            modelo = {}

            for cuota_info in cuotas_referencia:
                equipo = cuota_info["equipo"]
                tipo_resultado = clasificar_resultado(equipo, home, away)
                prob_mercado = cuota_info["probabilidad_mercado"]
                prob_modelo, modelo_mercado = ajustar_probabilidad_por_mercado(
                    "h2h",
                    equipo,
                    None,
                    None,
                    prob_mercado,
                    home,
                    away,
                    elos,
                    sport_key,
                )
                modelo[("h2h", tipo_resultado, None)] = {
                    "equipo": equipo,
                    "probabilidad_mercado": prob_mercado,
                    "probabilidad_elo": None,
                    "probabilidad_modelo": prob_modelo,
                    "elo_equipo": None,
                    "elo_rival": None,
                    "cuota_referencia": cuota_info["cuota"],
                    "modelo_mercado": modelo_mercado,
                }

            return modelo

        modelo_h2h = construir_modelo_referencia(
            partido,
            elos,
            casa_referencia=casa_referencia,
        )
        return {
            ("h2h", tipo_resultado, None): info
            for tipo_resultado, info in modelo_h2h.items()
        }

    home = partido.get("home_team")
    away = partido.get("away_team")

    if not home or not away:
        return {}

    bookmaker_referencia = None

    for bookmaker in partido.get("bookmakers", []):
        if bookmaker.get("title") == casa_referencia:
            bookmaker_referencia = bookmaker
            break

    if not bookmaker_referencia:
        return {}

    market = obtener_mercado(bookmaker_referencia, market_key)

    if not market:
        return {}

    outcomes = market.get("outcomes", [])

    if market_key in NON_EXCLUSIVE_MARKETS:
        modelo = {}

        for outcome in outcomes:
            nombre = outcome.get("name")
            price = outcome.get("price")
            point = outcome.get("point")
            description = outcome.get("description")

            if not nombre or not price:
                continue

            prob_mercado = probabilidad_implicita(float(price))
            prob_modelo, modelo_mercado = ajustar_probabilidad_por_mercado(
                market_key,
                nombre,
                point,
                description,
                prob_mercado,
                home,
                away,
                elos,
                partido.get("sport_key"),
            )
            key = (market_key, nombre, point, description)
            modelo[key] = {
                "equipo": nombre,
                "probabilidad_mercado": prob_mercado,
                "probabilidad_elo": prob_mercado,
                "probabilidad_modelo": prob_modelo,
                "elo_equipo": None,
                "elo_rival": None,
                "cuota_referencia": float(price),
                "outcome_point": point,
                "outcome_description": description,
                "modelo_mercado": modelo_mercado,
            }

        return modelo

    grupos = {}

    for outcome in outcomes:
        point = outcome.get("point")
        price = outcome.get("price")
        name = outcome.get("name")

        if not name or not price:
            continue

        if market_key in TEAM_TOTAL_MARKETS:
            grupo = (outcome.get("description"), point)
        elif market_key in POINT_TOTAL_MARKETS:
            grupo = point
        else:
            grupo = None
        grupos.setdefault(grupo, []).append(outcome)

    modelo = {}

    for _, outcomes_grupo in grupos.items():
        normalizados = normalizar_probabilidades(outcomes_grupo)

        for normalizado in normalizados:
            nombre = normalizado["equipo"]
            point = next(
                (outcome.get("point") for outcome in outcomes_grupo if outcome.get("name") == nombre),
                None,
            )
            description = next(
                (outcome.get("description") for outcome in outcomes_grupo if outcome.get("name") == nombre),
                None,
            )
            key = (market_key, nombre, point, description)
            prob_mercado = normalizado["probabilidad_mercado"]
            prob_modelo, modelo_mercado = ajustar_probabilidad_por_mercado(
                market_key,
                nombre,
                point,
                description,
                prob_mercado,
                home,
                away,
                elos,
                partido.get("sport_key"),
            )

            modelo[key] = {
                "equipo": nombre,
                "probabilidad_mercado": prob_mercado,
                "probabilidad_elo": prob_mercado,
                "probabilidad_modelo": prob_modelo,
                "elo_equipo": None,
                "elo_rival": None,
                "cuota_referencia": normalizado["cuota"],
                "outcome_point": point,
                "outcome_description": description,
                "modelo_mercado": modelo_mercado,
            }

    return modelo


def analizar_comparador_casas(
    partidos: list[dict[str, Any]],
    elos: dict[str, int],
    bankroll: float = 100,
    perfil: str = "moderado",
    casa_referencia: str = "Pinnacle",
    incluir_referencia: bool = False,
    mercados: list[str] | None = None,
    solo_casa: str | None = None,
    source_strength: str = "market+model",
) -> list[dict[str, Any]]:
    recomendaciones = []
    mercados = mercados or ["h2h"]

    for partido in partidos:
        home = partido.get("home_team")
        away = partido.get("away_team")

        if not home or not away:
            continue
        if is_tennis_doubles_match(home, away, partido.get("sport_key")):
            continue

        for market_key in mercados:
            modelo_referencia = construir_modelo_referencia_generico(
                partido,
                elos,
                market_key=market_key,
                casa_referencia=casa_referencia,
            )

            if not modelo_referencia:
                continue

            for bookmaker in partido.get("bookmakers", []):
                casa = bookmaker.get("title")

                if solo_casa and casa != solo_casa:
                    continue

                if not incluir_referencia and casa == casa_referencia:
                    continue

                market = obtener_mercado(bookmaker, market_key)

                if not market:
                    continue

                for outcome in market.get("outcomes", []):
                    equipo = outcome.get("name")
                    cuota = outcome.get("price")

                    if not equipo or not cuota:
                        continue

                    key = clave_outcome(market_key, outcome, home, away)
                    info_modelo = modelo_referencia.get(key)

                    if not info_modelo:
                        continue

                    tipo_resultado = key[1] if market_key == "h2h" else market_key
                    point = outcome.get("point")
                    description = outcome.get("description")
                    cuota = float(cuota)
                    consensus = market_consensus_snapshot(
                        partido,
                        market_key,
                        key,
                        selected_odds=cuota,
                    )
                    prob_modelo = info_modelo["probabilidad_modelo"]
                    prob_mercado = info_modelo["probabilidad_mercado"]
                    prob_elo = info_modelo["probabilidad_elo"]
                    cuota_referencia = info_modelo["cuota_referencia"]
                    valor = valor_esperado(prob_modelo, cuota)
                    cuota_justa = cuota_minima(prob_modelo)
                    margen_cuota = cuota / cuota_justa if cuota_justa else 0
                    ventaja_sobre_pinnacle = (
                        (cuota / cuota_referencia) - 1
                        if cuota_referencia
                        else None
                    )
                    stake_pct, importe, stake, recomendacion, motivo = decidir_stake(
                        bankroll=bankroll,
                        probabilidad_modelo=prob_modelo,
                        probabilidad_mercado=prob_mercado,
                        cuota=cuota,
                        margen_cuota=margen_cuota,
                        probabilidad_elo=prob_elo,
                        valor=valor,
                        perfil=perfil,
                        source_strength=source_strength,
                    )
                    confianza, puntuacion_confianza = calcular_confianza(
                        valor,
                        margen_cuota,
                        ventaja_sobre_pinnacle,
                        prob_elo,
                        prob_mercado,
                        source_strength=source_strength,
                        market_support_count=consensus["support_count"],
                        market_width_pct=consensus["width_pct"],
                        edge_vs_consensus=consensus["edge_vs_consensus"],
                    )
                    reliability_score, reliability_tier = calcular_fiabilidad_pick(
                        sport_key=partido.get("sport_key"),
                        league_key=partido.get("league_key"),
                        market_key=market_key,
                        casa=casa,
                        source_strength=source_strength,
                        market_support_count=consensus["support_count"],
                        market_width_pct=consensus["width_pct"],
                    )
                    if stake == 0:
                        rescue_stake_pct, rescue_importe, rescue_stake, rescue_recomendacion, rescue_motivo = rescatar_casi_value(
                            bankroll=bankroll,
                            perfil=perfil,
                            stake=stake,
                            valor=valor,
                            margen_cuota=margen_cuota,
                            cuota=cuota,
                            ventaja_sobre_pinnacle=ventaja_sobre_pinnacle,
                            confianza=confianza,
                            puntuacion_confianza=puntuacion_confianza,
                            reliability_tier=reliability_tier,
                            source_strength=source_strength,
                            market_key=market_key,
                        )
                        if rescue_stake > 0:
                            stake_pct = rescue_stake_pct
                            importe = rescue_importe
                            stake = rescue_stake
                            recomendacion = rescue_recomendacion
                            motivo = rescue_motivo
                    elite_pick, elite_tier, quality_score = clasificar_pick_elite(
                        stake=stake,
                        confianza=confianza,
                        puntuacion_confianza=puntuacion_confianza,
                        valor=valor,
                        margen_cuota=margen_cuota,
                        cuota=cuota,
                        source_strength=source_strength,
                        sport_key=partido.get("sport_key"),
                        league_key=partido.get("league_key"),
                        market_key=market_key,
                        casa=casa,
                        market_support_count=consensus["support_count"],
                        market_width_pct=consensus["width_pct"],
                        edge_vs_consensus=consensus["edge_vs_consensus"],
                    )

                    if ventaja_sobre_pinnacle is not None and ventaja_sobre_pinnacle > 0.02 and stake == 0:
                        motivo = "Cuota mejor que Pinnacle, pero sin margen suficiente para stake"

                    recomendaciones.append(BetRecommendation(
                        event_id=partido.get("id"),
                        commence_time=partido.get("commence_time"),
                        sport_key=partido.get("sport_key"),
                        sport_label=partido.get("sport_label"),
                        league_key=partido.get("league_key"),
                        league_label=partido.get("league_label"),
                        partido=f"{home} vs {away}",
                        casa=casa,
                        mercado=market_key,
                        equipo=equipo,
                        tipo_resultado=tipo_resultado,
                        cuota_pinnacle=round(cuota_referencia, 3) if cuota_referencia else round(cuota, 3),
                        cuota_minima_aceptable=round(cuota_justa, 3),
                        margen_cuota=round(margen_cuota, 4),
                        probabilidad_mercado=round(prob_mercado, 4),
                        probabilidad_elo=round(prob_elo, 4) if prob_elo is not None else None,
                        probabilidad_modelo=round(prob_modelo, 4),
                        elo_equipo=info_modelo["elo_equipo"],
                        elo_rival=info_modelo["elo_rival"],
                        valor_esperado=round(valor, 4),
                        kelly_fraccional=round(stake_pct, 5),
                        stake_pct_bankroll=round(stake_pct * 100, 3),
                        importe_sugerido=importe,
                        stake=stake,
                        recomendacion=recomendacion,
                        motivo=motivo,
                        cuota_apuesta=round(cuota, 3),
                        casa_referencia=casa_referencia,
                        cuota_referencia_pinnacle=round(cuota_referencia, 3) if cuota_referencia else None,
                        ventaja_sobre_pinnacle=round(ventaja_sobre_pinnacle, 4) if ventaja_sobre_pinnacle is not None else None,
                        outcome_point=point,
                        outcome_description=description,
                        modelo_mercado=info_modelo.get("modelo_mercado"),
                        confianza=confianza,
                        puntuacion_confianza=puntuacion_confianza,
                        quality_score=quality_score,
                        reliability_score=reliability_score,
                        reliability_tier=reliability_tier,
                        elite_pick=elite_pick,
                        elite_tier=elite_tier,
                        source_strength=source_strength,
                        market_support_count=consensus["support_count"],
                        market_consensus_odds=consensus["consensus_odds"],
                        market_best_odds=consensus["best_odds"],
                        market_worst_odds=consensus["worst_odds"],
                        market_width_pct=consensus["width_pct"],
                        market_edge_vs_consensus=consensus["edge_vs_consensus"],
                    ).to_dict())

    recomendaciones = _apply_h2h_ambiguity_guard(recomendaciones)
    return sorted(recomendaciones, key=lambda x: x["valor_esperado"], reverse=True)


def analizar_partidos(
    partidos: list[dict[str, Any]],
    elos: dict[str, int],
    bankroll: float = 100,
    perfil: str = "moderado",
    casa_objetivo: str = "Pinnacle",
    peso_mercado: float = 0.90,
    peso_elo: float = 0.10,
    source_strength: str = "market+model",
) -> list[dict[str, Any]]:
    recomendaciones = []

    for partido in partidos:
        home = partido.get("home_team")
        away = partido.get("away_team")

        if not home or not away:
            continue
        if is_tennis_doubles_match(home, away, partido.get("sport_key")):
            continue

        for bookmaker in partido.get("bookmakers", []):
            casa = bookmaker.get("title")

            if casa != casa_objetivo:
                continue

            for market in bookmaker.get("markets", []):
                if market.get("key") != "h2h":
                    continue

                cuotas_partido = normalizar_probabilidades(market.get("outcomes", []))

                if not cuotas_partido:
                    continue

                mercado_por_tipo = {
                    clasificar_resultado(x["equipo"], home, away): x["probabilidad_mercado"]
                    for x in cuotas_partido
                }
                prob_draw_mercado = mercado_por_tipo.get("draw", 0)
                masa_sin_empate = max(0, 1 - prob_draw_mercado)

                elo_home = obtener_elo_equipo(home, elos)
                elo_away = obtener_elo_equipo(away, elos)
                prob_home_elo_binaria = None

                if elo_home is not None and elo_away is not None:
                    prob_home_elo_binaria = probabilidad_binaria_elo(elo_home, elo_away)

                for cuota_info in cuotas_partido:
                    equipo = cuota_info["equipo"]
                    cuota = cuota_info["cuota"]
                    tipo_resultado = clasificar_resultado(equipo, home, away)
                    key = ("h2h", tipo_resultado, None)
                    consensus = market_consensus_snapshot(
                        partido,
                        "h2h",
                        key,
                        selected_odds=cuota,
                    )
                    prob_mercado = cuota_info["probabilidad_mercado"]
                    prob_elo = None
                    elo_equipo = None
                    elo_rival = None

                    if prob_home_elo_binaria is not None:
                        if tipo_resultado == "home":
                            prob_elo = prob_home_elo_binaria * masa_sin_empate
                            elo_equipo = elo_home
                            elo_rival = elo_away
                        elif tipo_resultado == "away":
                            prob_elo = (1 - prob_home_elo_binaria) * masa_sin_empate
                            elo_equipo = elo_away
                            elo_rival = elo_home
                        elif tipo_resultado == "draw":
                            prob_elo = prob_draw_mercado

                    prob_modelo = prob_mercado

                    if prob_elo is not None:
                        prob_modelo = (prob_mercado * peso_mercado) + (prob_elo * peso_elo)

                    valor = valor_esperado(prob_modelo, cuota)
                    cuota_justa = cuota_minima(prob_modelo)
                    margen_cuota = cuota / cuota_justa if cuota_justa else 0
                    stake_pct, importe, stake, recomendacion, motivo = decidir_stake(
                        bankroll=bankroll,
                        probabilidad_modelo=prob_modelo,
                        probabilidad_mercado=prob_mercado,
                        cuota=cuota,
                        margen_cuota=margen_cuota,
                        probabilidad_elo=prob_elo,
                        valor=valor,
                        perfil=perfil,
                        source_strength=source_strength,
                    )
                    confianza, puntuacion_confianza = calcular_confianza(
                        valor,
                        margen_cuota,
                        0,
                        prob_elo,
                        prob_mercado,
                        source_strength=source_strength,
                        market_support_count=consensus["support_count"],
                        market_width_pct=consensus["width_pct"],
                        edge_vs_consensus=consensus["edge_vs_consensus"],
                    )
                    reliability_score, reliability_tier = calcular_fiabilidad_pick(
                        sport_key=partido.get("sport_key"),
                        league_key=partido.get("league_key"),
                        market_key="h2h",
                        casa=casa,
                        source_strength=source_strength,
                        market_support_count=consensus["support_count"],
                        market_width_pct=consensus["width_pct"],
                    )
                    elite_pick, elite_tier, quality_score = clasificar_pick_elite(
                        stake=stake,
                        confianza=confianza,
                        puntuacion_confianza=puntuacion_confianza,
                        valor=valor,
                        margen_cuota=margen_cuota,
                        cuota=cuota,
                        source_strength=source_strength,
                        sport_key=partido.get("sport_key"),
                        league_key=partido.get("league_key"),
                        market_key="h2h",
                        casa=casa,
                        market_support_count=consensus["support_count"],
                        market_width_pct=consensus["width_pct"],
                        edge_vs_consensus=consensus["edge_vs_consensus"],
                    )

                    recomendaciones.append(BetRecommendation(
                        event_id=partido.get("id"),
                        commence_time=partido.get("commence_time"),
                        sport_key=partido.get("sport_key"),
                        sport_label=partido.get("sport_label"),
                        league_key=partido.get("league_key"),
                        league_label=partido.get("league_label"),
                        partido=f"{home} vs {away}",
                        casa=casa,
                        mercado="h2h",
                        equipo=equipo,
                        tipo_resultado=tipo_resultado,
                        cuota_pinnacle=round(cuota, 3),
                        cuota_minima_aceptable=round(cuota_justa, 3),
                        margen_cuota=round(margen_cuota, 4),
                        probabilidad_mercado=round(prob_mercado, 4),
                        probabilidad_elo=round(prob_elo, 4) if prob_elo is not None else None,
                        probabilidad_modelo=round(prob_modelo, 4),
                        elo_equipo=elo_equipo,
                        elo_rival=elo_rival,
                        valor_esperado=round(valor, 4),
                        kelly_fraccional=round(stake_pct, 5),
                        stake_pct_bankroll=round(stake_pct * 100, 3),
                        importe_sugerido=importe,
                        stake=stake,
                        recomendacion=recomendacion,
                        motivo=motivo,
                        cuota_apuesta=round(cuota, 3),
                        cuota_referencia_pinnacle=round(cuota, 3),
                        ventaja_sobre_pinnacle=0,
                        confianza=confianza,
                        puntuacion_confianza=puntuacion_confianza,
                        quality_score=quality_score,
                        reliability_score=reliability_score,
                        reliability_tier=reliability_tier,
                        elite_pick=elite_pick,
                        elite_tier=elite_tier,
                        source_strength=source_strength,
                        market_support_count=consensus["support_count"],
                        market_consensus_odds=consensus["consensus_odds"],
                        market_best_odds=consensus["best_odds"],
                        market_worst_odds=consensus["worst_odds"],
                        market_width_pct=consensus["width_pct"],
                        market_edge_vs_consensus=consensus["edge_vs_consensus"],
                    ).to_dict())

    return sorted(recomendaciones, key=lambda x: x["valor_esperado"], reverse=True)
