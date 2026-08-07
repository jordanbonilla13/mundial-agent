TEAM_NAMES_ES = {
    "Argentina": "Argentina",
    "Spain": "España",
    "France": "Francia",
    "England": "Inglaterra",
    "Brazil": "Brasil",
    "Portugal": "Portugal",
    "Netherlands": "Países Bajos",
    "Germany": "Alemania",
    "Colombia": "Colombia",
    "Italy": "Italia",
    "Uruguay": "Uruguay",
    "Belgium": "Bélgica",
    "Croatia": "Croacia",
    "Morocco": "Marruecos",
    "Mexico": "México",
    "United States": "Estados Unidos",
    "USA": "Estados Unidos",
    "Switzerland": "Suiza",
    "Japan": "Japón",
    "Denmark": "Dinamarca",
    "Norway": "Noruega",
    "Senegal": "Senegal",
    "Austria": "Austria",
    "Canada": "Canadá",
    "Paraguay": "Paraguay",
    "Egypt": "Egipto",
    "Australia": "Australia",
    "South Africa": "Sudáfrica",
    "South Korea": "Corea del Sur",
    "Korea Republic": "Corea del Sur",
    "Ivory Coast": "Costa de Marfil",
    "Côte d'Ivoire": "Costa de Marfil",
    "Algeria": "Argelia",
    "Ghana": "Ghana",
    "Turkey": "Turquía",
    "Sweden": "Suecia",
    "Ecuador": "Ecuador",
    "Cape Verde": "Cabo Verde",
    "Cape Verde Islands": "Cabo Verde",
    "DR Congo": "RD Congo",
    "Congo DR": "RD Congo",
    "Bosnia and Herzegovina": "Bosnia y Herzegovina",
    "Czechia": "Chequia",
    "Czech Republic": "República Checa",
    "Qatar": "Catar",
    "Tunisia": "Túnez",
    "Saudi Arabia": "Arabia Saudí",
    "Iran": "Irán",
    "Iraq": "Irak",
    "New Zealand": "Nueva Zelanda",
    "Uzbekistan": "Uzbekistán",
    "Panama": "Panamá",
    "Haiti": "Haití",
    "Scotland": "Escocia",
    "Draw": "Empate",
    "Tie": "Empate",
}

RESULT_TYPES_ES = {
    "home": "Local",
    "away": "Visitante",
    "draw": "Empate",
    "totals": "Goles totales",
    "spreads": "Handicap",
    "team_totals": "Goles por equipo",
    "alternate_team_totals_corners": "Corners por equipo",
    "alternate_totals": "Total de goles alternativo",
    "alternate_totals_corners": "Total de córners",
    "alternate_totals_cards": "Total de tarjetas",
    "alternate_spreads_cards": "Hándicap de tarjetas",
    "alternate_spreads_corners": "Hándicap de córners",
    "btts": "Ambos equipos anotarán",
    "corners_1x2": "Córners 1X2",
    "double_chance": "Doble oportunidad",
    "totals_h1": "Goles - 1ª parte",
    "totals_h2": "Goles - 2ª parte",
    "other": "Otro",
}

PROFILE_LABELS_ES = {
    "conservador": "Conservador",
    "moderado": "Moderado",
    "agresivo": "Agresivo",
    "alto_riesgo": "Alto riesgo",
}

MODE_LABELS_ES = {
    "comparador": "Comparar casas",
    "pinnacle": "Solo Pinnacle",
}

RECOMMENDATIONS_ES = {
    "Value interesante": "Valor interesante",
    "Value moderado": "Valor moderado",
    "Value ligero": "Valor ligero",
    "Value ELO especulativo": "Valor ELO especulativo",
    "No apostar": "No apostar",
    "Posible apuesta": "Posible apuesta",
}

MOTIVOS_ES = {
    "Bankroll no valido": "Bankroll no válido",
    "Cuota demasiado alta para esta version": "Cuota demasiado alta para esta versión",
    "Sin ELO fiable para contrastar el mercado": "Sin ELO fiable para contrastar el mercado",
    "Margen insuficiente frente a la cuota minima": "Margen insuficiente frente a la cuota mínima",
    "Valor esperado por debajo del filtro minimo": "Valor esperado por debajo del filtro mínimo",
    "Kelly fraccional no recomienda exposicion": "Kelly fraccional no recomienda exposición",
    "Filtro de value y margen superado": "Filtro de valor y margen superado",
    "Value positivo con exposicion controlada": "Valor positivo con exposición controlada",
    "Value pequeno aceptado con stake minimo": "Valor pequeño aceptado con riesgo mínimo",
    "El ELO supera claramente al mercado, pero la cuota aun queda justa": "El ELO supera claramente al mercado, pero la cuota aún queda justa",
    "El ELO supera claramente al mercado, pero el value combinado es pequeno": "El ELO supera claramente al mercado, pero el valor combinado es pequeño",
    "Cuota mejor que Pinnacle, pero sin margen suficiente para stake": "Cuota mejor que Pinnacle, pero sin margen suficiente para apostar",
}


def equipo_es(nombre: str | None) -> str:
    if not nombre:
        return ""

    return TEAM_NAMES_ES.get(nombre, nombre)


def _is_basketball_context(
    sport_key: str | None = None,
    sport_label: str | None = None,
) -> bool:
    sport_key_text = str(sport_key or "").strip().lower()
    sport_label_text = str(sport_label or "").strip().lower()
    return sport_key_text.startswith("basketball_") or sport_label_text == "baloncesto"


def _is_tennis_context(
    sport_key: str | None = None,
    sport_label: str | None = None,
) -> bool:
    sport_key_text = str(sport_key or "").strip().lower()
    sport_label_text = str(sport_label or "").strip().lower()
    return sport_key_text.startswith("tennis_") or sport_label_text == "tenis"


def apuesta_es(
    nombre: str | None,
    mercado: str | None = None,
    point: float | None = None,
    description: str | None = None,
    sport_key: str | None = None,
    sport_label: str | None = None,
) -> str:
    descripcion = equipo_es(description)
    basketball = _is_basketball_context(sport_key, sport_label)
    tennis = _is_tennis_context(sport_key, sport_label)

    if mercado in {"totals", "alternate_totals"}:
        if nombre == "Over":
            if tennis:
                return f"Mas de {point:g} juegos" if point is not None else "Mas juegos"
            return f"Más de {point:g} puntos" if basketball and point is not None else f"Más de {point:g} goles" if point is not None else "Más puntos" if basketball else "Más goles"
        if nombre == "Under":
            if tennis:
                return f"Menos de {point:g} juegos" if point is not None else "Menos juegos"
            return f"Menos de {point:g} puntos" if basketball and point is not None else f"Menos de {point:g} goles" if point is not None else "Menos puntos" if basketball else "Menos goles"

    if mercado == "totals_h1":
        if nombre == "Over":
            return f"1ª parte: más de {point:g} puntos" if basketball and point is not None else f"1ª parte: más de {point:g} goles" if point is not None else "1ª parte: más puntos" if basketball else "1ª parte: más goles"
        if nombre == "Under":
            return f"1ª parte: menos de {point:g} puntos" if basketball and point is not None else f"1ª parte: menos de {point:g} goles" if point is not None else "1ª parte: menos puntos" if basketball else "1ª parte: menos goles"

    if mercado == "totals_h2":
        if nombre == "Over":
            return f"2ª parte: más de {point:g} puntos" if basketball and point is not None else f"2ª parte: más de {point:g} goles" if point is not None else "2ª parte: más puntos" if basketball else "2ª parte: más goles"
        if nombre == "Under":
            return f"2ª parte: menos de {point:g} puntos" if basketball and point is not None else f"2ª parte: menos de {point:g} goles" if point is not None else "2ª parte: menos puntos" if basketball else "2ª parte: menos goles"

    if mercado == "team_totals":
        if nombre == "Over":
            if tennis:
                return f"{descripcion}: mas de {point:g} juegos" if point is not None else f"{descripcion}: mas juegos"
            return f"{descripcion}: más de {point:g} puntos" if basketball and point is not None else f"{descripcion}: más de {point:g} goles" if point is not None else f"{descripcion}: más puntos" if basketball else f"{descripcion}: más goles"
        if nombre == "Under":
            if tennis:
                return f"{descripcion}: menos de {point:g} juegos" if point is not None else f"{descripcion}: menos juegos"
            return f"{descripcion}: menos de {point:g} puntos" if basketball and point is not None else f"{descripcion}: menos de {point:g} goles" if point is not None else f"{descripcion}: menos puntos" if basketball else f"{descripcion}: menos goles"

    if mercado == "alternate_team_totals_corners":
        if nombre == "Over":
            return f"{descripcion}: más de {point:g} corners" if point is not None else f"{descripcion}: más corners"
        if nombre == "Under":
            return f"{descripcion}: menos de {point:g} corners" if point is not None else f"{descripcion}: menos corners"

    if mercado == "alternate_totals_corners":
        if nombre == "Over":
            return f"Más de {point:g} corners" if point is not None else "Más corners"
        if nombre == "Under":
            return f"Menos de {point:g} corners" if point is not None else "Menos corners"

    if mercado == "alternate_totals_cards":
        if nombre == "Over":
            return f"Más de {point:g} tarjetas" if point is not None else "Más tarjetas"
        if nombre == "Under":
            return f"Menos de {point:g} tarjetas" if point is not None else "Menos tarjetas"

    if mercado == "btts":
        if nombre == "Yes":
            return "Ambos equipos anotarán: Sí"
        if nombre == "No":
            return "Ambos equipos anotarán: No"

    if mercado == "double_chance":
        return str(nombre).replace(" or ", " o ").replace("Draw", "Empate")

    if mercado == "spreads":
        if point is None:
            return f"Handicap {equipo_es(nombre)}"
        point_value = float(point)
        point_text = f"{point_value:+g}".replace(".", ",")
        return f"{equipo_es(nombre)} {point_text}"

    if mercado == "corners_1x2":
        return f"Más corners: {equipo_es(nombre)}"

    return equipo_es(nombre)


def tipo_resultado_es(
    tipo: str | None,
    sport_key: str | None = None,
    sport_label: str | None = None,
) -> str:
    if not tipo:
        return ""

    if _is_basketball_context(sport_key, sport_label):
        basketball_labels = {
            "spreads": "Handicap",
            "totals": "Puntos totales",
            "team_totals": "Puntos por equipo",
            "alternate_totals": "Total de puntos alternativo",
            "totals_h1": "Puntos - 1ª parte",
            "totals_h2": "Puntos - 2ª parte",
        }
        if tipo in basketball_labels:
            return basketball_labels[tipo]

    return RESULT_TYPES_ES.get(tipo, tipo)


def perfil_es(perfil: str | None) -> str:
    if not perfil:
        return ""

    return PROFILE_LABELS_ES.get(perfil, perfil)


def modo_es(modo: str | None) -> str:
    if not modo:
        return ""

    return MODE_LABELS_ES.get(modo, modo)


def recomendacion_es(recomendacion: str | None) -> str:
    if not recomendacion:
        return ""

    return RECOMMENDATIONS_ES.get(recomendacion, recomendacion)


def motivo_es(motivo: str | None) -> str:
    if not motivo:
        return ""

    return MOTIVOS_ES.get(motivo, motivo)


def partido_es(partido: str | None) -> str:
    if not partido:
        return ""

    if " vs " not in partido:
        return partido

    home, away = partido.split(" vs ", 1)
    return f"{equipo_es(home)} vs {equipo_es(away)}"
