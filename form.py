def calcular_forma_manual(equipo):
    datos = {
        "France": 8.7,
        "Spain": 8.8,
        "Brazil": 8.2,
        "Argentina": 8.5,
        "England": 8.1,
        "Portugal": 8.0,
        "Morocco": 7.6,
        "Canada": 6.4,
        "Paraguay": 5.8,
    }

    return datos.get(equipo, 6.5)