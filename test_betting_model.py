import os
import tempfile
import unittest

from betting_model import (
    analizar_comparador_casas,
    analizar_partidos,
    calcular_fiabilidad_pick,
    clasificar_pick_elite,
    calcular_kelly_fraccional,
    normalizar_probabilidades,
    rescatar_casi_value,
)
from tracking import actualizar_resultado, estadisticas, guardar_recomendaciones
from tracking import aprendizaje, dashboard_data, guardar_snapshot_cuotas, liquidar_picks_con_scores, listar_picks, penalizaciones_historicas
from tracking import actualizar_bankroll, actualizar_cuota_pick, actualizar_importe_pick, guardar_apuesta_real, marcar_apuesta_real_pick, obtener_bankroll
from tracking import guardar_recomendaciones_unicas, listar_publicaciones_telegram, registrar_publicacion_telegram
from main import (
    adaptar_api_football_odds,
    adaptar_sportsgameodds_events,
    apuestas_hoy,
    auto_publicar_telegram_once,
    build_dynamic_context_from_sport_key,
    enviar_mensaje_telegram,
    filtrar_partidos,
    formatear_mensaje_telegram_pick,
    opciones_deporte_disponibles,
    partidos_disponibles,
    procesar_callback_pick,
    publicar_pronosticos_telegram,
    prioridad_pick,
    resolver_contexto_deporte,
    resolver_mercados,
    telegram_config,
    telegram_keyboard_for_pick,
)


PARTIDOS_FAKE = [
    {
        "id": "evt_1",
        "commence_time": "2026-06-11T20:00:00Z",
        "home_team": "Spain",
        "away_team": "France",
        "bookmakers": [
            {
                "title": "Pinnacle",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Spain", "price": 2.8},
                            {"name": "Draw", "price": 3.2},
                            {"name": "France", "price": 2.7},
                        ],
                    }
                ],
            }
        ],
    }
]


class BettingModelTests(unittest.TestCase):
    def test_normalizar_probabilidades_suma_uno(self):
        outcomes = PARTIDOS_FAKE[0]["bookmakers"][0]["markets"][0]["outcomes"]
        normalizadas = normalizar_probabilidades(outcomes)
        total = sum(x["probabilidad_mercado"] for x in normalizadas)

        self.assertAlmostEqual(total, 1, places=6)

    def test_kelly_fraccional_tiene_techo(self):
        stake = calcular_kelly_fraccional(0.60, 2.20)

        self.assertLessEqual(stake, 0.015)

    def test_filtro_agresivo_acepta_value_no_extremo(self):
        partidos = [
            {
                "id": "evt_2",
                "commence_time": "2026-06-12T20:00:00Z",
                "home_team": "Belgium",
                "away_team": "United States",
                "bookmakers": [
                    {
                        "title": "Pinnacle",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Belgium", "price": 2.40},
                                    {"name": "Draw", "price": 3.35},
                                    {"name": "United States", "price": 3.45},
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
        elos = {"BE": 2000, "US": 1850}
        recomendaciones = analizar_partidos(partidos, elos, bankroll=100, perfil="agresivo")
        recomendadas = [r for r in recomendaciones if r["stake"] > 0]

        self.assertGreaterEqual(len(recomendadas), 1)

    def test_conservador_no_acepta_value_elo_especulativo(self):
        partidos = [
            {
                "id": "evt_3",
                "commence_time": "2026-06-13T20:00:00Z",
                "home_team": "United States",
                "away_team": "Belgium",
                "bookmakers": [
                    {
                        "title": "Pinnacle",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "United States", "price": 2.65},
                                    {"name": "Draw", "price": 3.25},
                                    {"name": "Belgium", "price": 2.66},
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
        elos = {"US": 1800, "BE": 2000}
        recomendaciones = analizar_partidos(partidos, elos, bankroll=100, perfil="conservador")
        belgium = next(r for r in recomendaciones if r["equipo"] == "Belgium")

        self.assertEqual(belgium["recomendacion"], "No apostar")
        self.assertEqual(belgium["stake"], 0)

    def test_agresivo_acepta_value_elo_especulativo(self):
        partidos = [
            {
                "id": "evt_3b",
                "commence_time": "2026-06-13T20:00:00Z",
                "home_team": "United States",
                "away_team": "Belgium",
                "bookmakers": [
                    {
                        "title": "Pinnacle",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "United States", "price": 2.65},
                                    {"name": "Draw", "price": 3.25},
                                    {"name": "Belgium", "price": 2.66},
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
        elos = {"US": 1800, "BE": 2000}
        recomendaciones = analizar_partidos(partidos, elos, bankroll=100, perfil="agresivo")
        belgium = next(r for r in recomendaciones if r["equipo"] == "Belgium")

        self.assertEqual(belgium["recomendacion"], "Value ELO especulativo")
        self.assertGreater(belgium["stake"], 0)

    def test_perfiles_cambian_numero_de_recomendadas(self):
        partidos = [
            {
                "id": "evt_profile",
                "commence_time": "2026-06-13T20:00:00Z",
                "home_team": "United States",
                "away_team": "Belgium",
                "bookmakers": [
                    {
                        "title": "Pinnacle",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "United States", "price": 2.65},
                                    {"name": "Draw", "price": 3.25},
                                    {"name": "Belgium", "price": 2.66},
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
        elos = {"US": 1800, "BE": 2000}
        conservador = analizar_partidos(partidos, elos, bankroll=100, perfil="conservador")
        alto_riesgo = analizar_partidos(partidos, elos, bankroll=100, perfil="alto_riesgo")

        total_conservador = len([r for r in conservador if r["stake"] > 0])
        total_alto_riesgo = len([r for r in alto_riesgo if r["stake"] > 0])

        self.assertLess(total_conservador, total_alto_riesgo)

    def test_perfil_agresivo_usa_importe_minimo_practico(self):
        partidos = [
            {
                "id": "evt_4",
                "commence_time": "2026-06-13T20:00:00Z",
                "home_team": "United States",
                "away_team": "Belgium",
                "bookmakers": [
                    {
                        "title": "Pinnacle",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "United States", "price": 2.65},
                                    {"name": "Draw", "price": 3.25},
                                    {"name": "Belgium", "price": 2.66},
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
        elos = {"US": 1800, "BE": 2000}
        recomendaciones = analizar_partidos(partidos, elos, bankroll=25, perfil="agresivo")
        belgium = next(r for r in recomendaciones if r["equipo"] == "Belgium")

        self.assertEqual(belgium["recomendacion"], "Value ELO especulativo")
        self.assertEqual(belgium["importe_sugerido"], 1.0)

    def test_perfil_alto_riesgo_sube_importe_especulativo(self):
        partidos = [
            {
                "id": "evt_5",
                "commence_time": "2026-06-13T20:00:00Z",
                "home_team": "United States",
                "away_team": "Belgium",
                "bookmakers": [
                    {
                        "title": "Pinnacle",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "United States", "price": 2.65},
                                    {"name": "Draw", "price": 3.25},
                                    {"name": "Belgium", "price": 2.66},
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
        elos = {"US": 1800, "BE": 2000}
        recomendaciones = analizar_partidos(partidos, elos, bankroll=25, perfil="alto_riesgo")
        belgium = next(r for r in recomendaciones if r["equipo"] == "Belgium")

        self.assertEqual(belgium["recomendacion"], "Value ELO especulativo")
        self.assertEqual(belgium["importe_sugerido"], 5.0)

    def test_perfil_alto_riesgo_llega_a_diez_en_value_fuerte(self):
        partidos = [
            {
                "id": "evt_6",
                "commence_time": "2026-06-14T20:00:00Z",
                "home_team": "Belgium",
                "away_team": "United States",
                "bookmakers": [
                    {
                        "title": "Pinnacle",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Belgium", "price": 3.10},
                                    {"name": "Draw", "price": 3.30},
                                    {"name": "United States", "price": 2.70},
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
        elos = {"BE": 2150, "US": 1750}
        recomendaciones = analizar_partidos(partidos, elos, bankroll=25, perfil="alto_riesgo")
        belgium = next(r for r in recomendaciones if r["equipo"] == "Belgium")

        self.assertEqual(belgium["recomendacion"], "Value interesante")
        self.assertEqual(belgium["importe_sugerido"], 10.0)

    def test_comparador_recomienda_casa_con_mejor_cuota_que_pinnacle(self):
        partidos = [
            {
                "id": "evt_7",
                "commence_time": "2026-06-15T20:00:00Z",
                "home_team": "Belgium",
                "away_team": "United States",
                "bookmakers": [
                    {
                        "title": "Pinnacle",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Belgium", "price": 2.20},
                                    {"name": "Draw", "price": 3.30},
                                    {"name": "United States", "price": 3.40},
                                ],
                            }
                        ],
                    },
                    {
                        "title": "Bet365",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Belgium", "price": 2.55},
                                    {"name": "Draw", "price": 3.20},
                                    {"name": "United States", "price": 3.20},
                                ],
                            }
                        ],
                    },
                ],
            }
        ]
        elos = {"BE": 2050, "US": 1800}
        recomendaciones = analizar_comparador_casas(partidos, elos, bankroll=100, perfil="moderado")
        belgium = next(r for r in recomendaciones if r["equipo"] == "Belgium")

        self.assertEqual(belgium["casa"], "Bet365")
        self.assertEqual(belgium["cuota_apuesta"], 2.55)
        self.assertEqual(belgium["cuota_referencia_pinnacle"], 2.2)
        self.assertGreater(belgium["stake"], 0)

    def test_comparador_totals_recomienda_mas_menos_goles(self):
        partidos = [
            {
                "id": "evt_totals",
                "commence_time": "2026-06-16T20:00:00Z",
                "home_team": "Brazil",
                "away_team": "Norway",
                "bookmakers": [
                    {
                        "title": "Pinnacle",
                        "markets": [
                            {
                                "key": "totals",
                                "outcomes": [
                                    {"name": "Over", "price": 1.90, "point": 2.5},
                                    {"name": "Under", "price": 1.90, "point": 2.5},
                                ],
                            }
                        ],
                    },
                    {
                        "title": "Matchbook",
                        "markets": [
                            {
                                "key": "totals",
                                "outcomes": [
                                    {"name": "Over", "price": 2.20, "point": 2.5},
                                    {"name": "Under", "price": 1.70, "point": 2.5},
                                ],
                            }
                        ],
                    },
                ],
            }
        ]
        recomendaciones = analizar_comparador_casas(
            partidos,
            {},
            bankroll=100,
            perfil="agresivo",
            mercados=["totals"],
        )
        over = next(r for r in recomendaciones if r["equipo"] == "Over")

        self.assertEqual(over["mercado"], "totals")
        self.assertEqual(over["outcome_point"], 2.5)
        self.assertEqual(over["cuota_apuesta"], 2.2)
        self.assertGreater(over["stake"], 0)

    def test_comparador_team_totals_goles_por_equipo(self):
        partidos = [
            {
                "id": "evt_team_goals",
                "commence_time": "2026-06-16T20:00:00Z",
                "home_team": "Brazil",
                "away_team": "Norway",
                "bookmakers": [
                    {
                        "title": "Pinnacle",
                        "markets": [
                            {
                                "key": "team_totals",
                                "outcomes": [
                                    {"name": "Over", "description": "Brazil", "price": 1.90, "point": 1.5},
                                    {"name": "Under", "description": "Brazil", "price": 1.90, "point": 1.5},
                                ],
                            }
                        ],
                    },
                    {
                        "title": "Matchbook",
                        "markets": [
                            {
                                "key": "team_totals",
                                "outcomes": [
                                    {"name": "Over", "description": "Brazil", "price": 2.25, "point": 1.5},
                                    {"name": "Under", "description": "Brazil", "price": 1.65, "point": 1.5},
                                ],
                            }
                        ],
                    },
                ],
            }
        ]
        recomendaciones = analizar_comparador_casas(
            partidos,
            {},
            bankroll=100,
            perfil="agresivo",
            mercados=["team_totals"],
        )
        over = next(r for r in recomendaciones if r["equipo"] == "Over")

        self.assertEqual(over["mercado"], "team_totals")
        self.assertEqual(over["outcome_description"], "Brazil")
        self.assertGreater(over["stake"], 0)

    def test_solo_pinnacle_respeta_filtro_team_totals(self):
        partidos = [
            {
                "id": "evt_team_goals_pinnacle",
                "commence_time": "2026-06-16T20:00:00Z",
                "home_team": "Brazil",
                "away_team": "Norway",
                "bookmakers": [
                    {
                        "title": "Pinnacle",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Brazil", "price": 1.70},
                                    {"name": "Draw", "price": 3.50},
                                    {"name": "Norway", "price": 4.68},
                                ],
                            },
                            {
                                "key": "team_totals",
                                "outcomes": [
                                    {"name": "Over", "description": "Brazil", "price": 1.90, "point": 1.5},
                                    {"name": "Under", "description": "Brazil", "price": 1.90, "point": 1.5},
                                ],
                            },
                        ],
                    },
                ],
            }
        ]
        recomendaciones = analizar_comparador_casas(
            partidos,
            {},
            bankroll=100,
            perfil="alto_riesgo",
            mercados=["team_totals"],
            incluir_referencia=True,
            solo_casa="Pinnacle",
        )

        self.assertTrue(recomendaciones)
        self.assertTrue(all(r["mercado"] == "team_totals" for r in recomendaciones))

    def test_comparador_corners_por_equipo(self):
        partidos = [
            {
                "id": "evt_corners",
                "commence_time": "2026-06-16T20:00:00Z",
                "home_team": "Brazil",
                "away_team": "Norway",
                "bookmakers": [
                    {
                        "title": "Pinnacle",
                        "markets": [
                            {
                                "key": "alternate_team_totals_corners",
                                "outcomes": [
                                    {"name": "Over", "description": "Brazil", "price": 1.90, "point": 5.5},
                                    {"name": "Under", "description": "Brazil", "price": 1.90, "point": 5.5},
                                ],
                            }
                        ],
                    },
                    {
                        "title": "Matchbook",
                        "markets": [
                            {
                                "key": "alternate_team_totals_corners",
                                "outcomes": [
                                    {"name": "Over", "description": "Brazil", "price": 2.30, "point": 5.5},
                                    {"name": "Under", "description": "Brazil", "price": 1.60, "point": 5.5},
                                ],
                            }
                        ],
                    },
                ],
            }
        ]
        recomendaciones = analizar_comparador_casas(
            partidos,
            {},
            bankroll=100,
            perfil="agresivo",
            mercados=["alternate_team_totals_corners"],
        )
        over = next(r for r in recomendaciones if r["equipo"] == "Over")

        self.assertEqual(over["mercado"], "alternate_team_totals_corners")
        self.assertEqual(over["outcome_description"], "Brazil")
        self.assertGreater(over["stake"], 0)

    def test_comparador_btts_ambos_anotan(self):
        partidos = [
            {
                "id": "evt_btts",
                "commence_time": "2026-06-16T20:00:00Z",
                "home_team": "Brazil",
                "away_team": "Norway",
                "bookmakers": [
                    {
                        "title": "Pinnacle",
                        "markets": [
                            {
                                "key": "btts",
                                "outcomes": [
                                    {"name": "Yes", "price": 1.90},
                                    {"name": "No", "price": 1.90},
                                ],
                            }
                        ],
                    },
                    {
                        "title": "Matchbook",
                        "markets": [
                            {
                                "key": "btts",
                                "outcomes": [
                                    {"name": "Yes", "price": 2.25},
                                    {"name": "No", "price": 1.65},
                                ],
                            }
                        ],
                    },
                ],
            }
        ]
        recomendaciones = analizar_comparador_casas(
            partidos,
            {},
            bankroll=100,
            perfil="agresivo",
            mercados=["btts"],
        )
        yes = next(r for r in recomendaciones if r["equipo"] == "Yes")

        self.assertEqual(yes["mercado"], "btts")
        self.assertGreater(yes["stake"], 0)

    def test_adaptar_api_football_odds_a_formato_interno(self):
        fixtures = {
            1001: {
                "id": "1001",
                "commence_time": "2026-07-05T20:00:00+00:00",
                "home_team": "Brazil",
                "away_team": "Norway",
            }
        }
        odds_items = [
            {
                "fixture": {"id": 1001},
                "bookmakers": [
                    {
                        "id": 4,
                        "name": "Pinnacle",
                        "bets": [
                            {
                                "name": "Match Winner",
                                "values": [
                                    {"value": "Home", "odd": "1.70"},
                                    {"value": "Draw", "odd": "3.50"},
                                    {"value": "Away", "odd": "4.80"},
                                ],
                            },
                            {
                                "name": "Both Teams Score",
                                "values": [
                                    {"value": "Yes", "odd": "1.80"},
                                    {"value": "No", "odd": "2.10"},
                                ],
                            },
                            {
                                "name": "Goals Over/Under",
                                "values": [
                                    {"value": "Over 2.5", "odd": "1.90"},
                                    {"value": "Under 2.5", "odd": "1.95"},
                                ],
                            },
                        ],
                    }
                ],
            }
        ]

        eventos = adaptar_api_football_odds(
            odds_items,
            fixtures,
            ["h2h", "btts", "totals"],
        )
        markets = eventos[0]["bookmakers"][0]["markets"]

        self.assertEqual(eventos[0]["home_team"], "Brazil")
        self.assertEqual(eventos[0]["away_team"], "Norway")
        self.assertEqual({m["key"] for m in markets}, {"h2h", "btts", "totals"})
        h2h = next(m for m in markets if m["key"] == "h2h")
        totals = next(m for m in markets if m["key"] == "totals")

        self.assertEqual(h2h["outcomes"][0]["name"], "Brazil")
        self.assertEqual(totals["outcomes"][0]["point"], 2.5)

    def test_partidos_disponibles_y_filtro_por_partido(self):
        partidos = [
            {"id": "evt_1", "home_team": "Spain", "away_team": "Portugal"},
            {"id": "evt_2", "home_team": "Brazil", "away_team": "Norway"},
        ]

        disponibles = partidos_disponibles(partidos)
        filtrados = filtrar_partidos(partidos, "evt_1")

        self.assertEqual(disponibles[0]["label"], "España vs Portugal")
        self.assertEqual(len(filtrados), 1)
        self.assertEqual(filtrados[0]["home_team"], "Spain")

    def test_resolver_mercados_limita_por_deporte(self):
        mercados_tenis, aviso_tenis = resolver_mercados("corners", deporte="tenis")
        mercados_basket, aviso_basket = resolver_mercados("todo", deporte="baloncesto")

        self.assertEqual(mercados_tenis, ["h2h"])
        self.assertIn("no aplica", aviso_tenis.lower())
        self.assertEqual(mercados_basket, ["totals", "alternate_totals"])
        self.assertIn("total_goles", aviso_basket)

    def test_contexto_deporte_normaliza_alias(self):
        contexto = resolver_contexto_deporte("nba")

        self.assertEqual(contexto["catalog_key"], "baloncesto")
        self.assertEqual(contexto["sport_label"], "Baloncesto")

    def test_contexto_dinamico_desde_sport_key(self):
        contexto = build_dynamic_context_from_sport_key("soccer_brazil_serie_a")

        self.assertEqual(contexto["sport_label"], "Futbol")
        self.assertEqual(contexto["league_key"], "brazil_serie_a")
        self.assertTrue(contexto["supports_elo"])

    def test_fiabilidad_pick_premia_liga_top_y_mercado_base(self):
        score, tier = calcular_fiabilidad_pick(
            sport_key="soccer_england_premier_league",
            league_key="england_premier_league",
            market_key="h2h",
            casa="Pinnacle",
            source_strength="market+model",
        )

        self.assertGreaterEqual(score, 75)
        self.assertEqual(tier, "alta")

    def test_fiabilidad_pick_penaliza_liga_menor_y_mercado_volatil(self):
        score, tier = calcular_fiabilidad_pick(
            sport_key="soccer_regional_division_x",
            league_key="regional_division_x",
            market_key="alternate_totals_cards",
            casa="CasaDesconocida",
            source_strength="market_only",
        )

        self.assertLess(score, 55)
        self.assertEqual(tier, "baja")

    def test_pick_elite_exige_contexto_fiable_para_subir(self):
        elite_top, tier_top, score_top = clasificar_pick_elite(
            stake=2.0,
            confianza="Alta",
            puntuacion_confianza=78,
            valor=0.06,
            margen_cuota=1.07,
            cuota=1.95,
            source_strength="market+model",
            sport_key="soccer_england_premier_league",
            league_key="england_premier_league",
            market_key="h2h",
            casa="Pinnacle",
        )
        elite_low, tier_low, score_low = clasificar_pick_elite(
            stake=2.0,
            confianza="Alta",
            puntuacion_confianza=78,
            valor=0.06,
            margen_cuota=1.07,
            cuota=1.95,
            source_strength="market_only",
            sport_key="soccer_regional_division_x",
            league_key="regional_division_x",
            market_key="alternate_totals_cards",
            casa="CasaDesconocida",
        )

        self.assertTrue(elite_top)
        self.assertIn(tier_top, {"elite", "stakazo"})
        self.assertFalse(elite_low)
        self.assertIn(tier_low, {"premium", "seguimiento"})
        self.assertGreater(score_top, score_low)

    def test_pick_premium_aparece_antes_de_elite_si_no_llega_al_umbral(self):
        elite_pick, tier, score = clasificar_pick_elite(
            stake=2.0,
            confianza="Media",
            puntuacion_confianza=48,
            valor=0.03,
            margen_cuota=1.03,
            cuota=2.35,
            source_strength="market+model",
            sport_key="soccer_regional_division_x",
            league_key="regional_division_x",
            market_key="h2h",
            casa="CasaDesconocida",
        )

        self.assertFalse(elite_pick)
        self.assertEqual(tier, "premium")
        self.assertGreaterEqual(score, 58)

    def test_rescatar_casi_value_convierte_borderline_en_micro_stake(self):
        stake_pct, importe, stake, recomendacion, motivo = rescatar_casi_value(
            bankroll=177.24,
            perfil="alto_riesgo",
            stake=0,
            valor=-0.004,
            margen_cuota=0.992,
            cuota=2.10,
            ventaja_sobre_pinnacle=0.022,
            confianza="Media",
            puntuacion_confianza=58,
            reliability_tier="alta",
            source_strength="market+model",
            market_key="h2h",
        )

        self.assertGreater(stake_pct, 0)
        self.assertGreater(importe, 0)
        self.assertGreater(stake, 0)
        self.assertIn("Premium", recomendacion)

    def test_prioridad_pick_premia_stakazo_y_fiabilidad(self):
        pick_stakazo = {
            "elite_tier": "stakazo",
            "quality_score": 90,
            "reliability_score": 88,
            "puntuacion_confianza": 82,
            "valor_esperado": 0.05,
            "margen_cuota": 1.06,
        }
        pick_elite = {
            "elite_tier": "elite",
            "quality_score": 92,
            "reliability_score": 70,
            "puntuacion_confianza": 84,
            "valor_esperado": 0.08,
            "margen_cuota": 1.08,
        }

        self.assertGreater(prioridad_pick(pick_stakazo), prioridad_pick(pick_elite))

    def test_prioridad_pick_coloca_premium_sobre_seguimiento(self):
        premium = {
            "elite_tier": "premium",
            "quality_score": 70,
            "reliability_score": 68,
            "puntuacion_confianza": 60,
            "valor_esperado": 0.03,
            "margen_cuota": 1.04,
        }
        seguimiento = {
            "elite_tier": "seguimiento",
            "quality_score": 88,
            "reliability_score": 80,
            "puntuacion_confianza": 75,
            "valor_esperado": 0.07,
            "margen_cuota": 1.08,
        }

        self.assertGreater(prioridad_pick(premium), prioridad_pick(seguimiento))

    def test_mensaje_telegram_incluye_fiabilidad(self):
        mensaje = formatear_mensaje_telegram_pick({
            "league_label": "Premier League",
            "elite_tier": "stakazo",
            "partido_es": "Arsenal vs Chelsea",
            "equipo_es": "Arsenal",
            "cuota_apuesta": 1.95,
            "stake": 2.5,
            "importe_sugerido": 8.0,
            "valor_esperado": 0.061,
            "confianza": "Alta",
            "reliability_tier": "alta",
            "reliability_score": 86,
            "quality_score": 91,
            "mercado": "h2h",
            "tipo_resultado_es": "Local",
            "tipo_resultado": "home",
            "tipo_resultado_raw": "home",
            "motivo_es": "Value claro frente a mercado.",
        })

        self.assertIn("<b>Fiabilidad:</b> alta (86/100)", mensaje)
        self.assertIn("<b>STAKAZO | Premier League</b>", mensaje)
        self.assertIn("<b>Stake:</b> 2.5/5 | <b>Importe:</b> 8.0 EUR", mensaje)

    def test_dashboard_data_calcula_metricas_quality_y_reliability(self):
        recomendacion = {
            "event_id": "metric_1",
            "commence_time": "2026-07-15T20:00:00Z",
            "sport_key": "soccer_england_premier_league",
            "sport_label": "Futbol",
            "league_key": "england_premier_league",
            "league_label": "Premier League",
            "partido": "Arsenal vs Chelsea",
            "equipo": "Arsenal",
            "tipo_resultado": "home",
            "casa": "Pinnacle",
            "mercado": "h2h",
            "cuota_apuesta": 1.95,
            "cuota_minima_aceptable": 1.80,
            "probabilidad_mercado": 0.51,
            "probabilidad_elo": 0.55,
            "probabilidad_modelo": 0.57,
            "valor_esperado": 0.11,
            "margen_cuota": 1.08,
            "kelly_fraccional": 0.02,
            "stake_pct_bankroll": 2,
            "importe_sugerido": 4,
            "stake": 2,
            "recomendacion": "Value interesante",
            "motivo": "test",
            "perfil": "moderado",
            "modelo_mercado": "Mercado + ELO",
            "quality_score": 88,
            "reliability_score": 84,
            "reliability_tier": "alta",
            "elite_pick": True,
            "elite_tier": "stakazo",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.sqlite3")
            guardar_recomendaciones([recomendacion], db_path=db_path)
            panel = dashboard_data(db_path=db_path)

        self.assertEqual(panel["por_liga"][0]["quality_media"], 88.0)
        self.assertEqual(panel["por_liga"][0]["reliability_media"], 84.0)
        self.assertEqual(panel["solo_stakazos"]["picks"], 1)
        self.assertEqual(panel["solo_stakazos"]["reliability_media"], 84.0)

    def test_dashboard_data_calcula_clv_por_tier(self):
        stakazo = {
            "event_id": "clv_1",
            "commence_time": "2026-07-15T20:00:00Z",
            "sport_key": "soccer_england_premier_league",
            "sport_label": "Futbol",
            "league_key": "england_premier_league",
            "league_label": "Premier League",
            "partido": "Arsenal vs Chelsea",
            "equipo": "Arsenal",
            "tipo_resultado": "home",
            "casa": "Pinnacle",
            "mercado": "h2h",
            "cuota_apuesta": 2.00,
            "cuota_minima_aceptable": 1.85,
            "probabilidad_mercado": 0.50,
            "probabilidad_modelo": 0.56,
            "valor_esperado": 0.12,
            "margen_cuota": 1.08,
            "kelly_fraccional": 0.02,
            "stake_pct_bankroll": 2,
            "importe_sugerido": 4,
            "stake": 2,
            "recomendacion": "Value interesante",
            "motivo": "test",
            "quality_score": 90,
            "reliability_score": 86,
            "elite_pick": True,
            "elite_tier": "stakazo",
        }
        elite = {
            **stakazo,
            "event_id": "clv_2",
            "partido": "Liverpool vs City",
            "equipo": "Liverpool",
            "cuota_apuesta": 1.90,
            "elite_tier": "elite",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.sqlite3")
            guardar_recomendaciones([stakazo, elite], db_path=db_path)
            picks = listar_picks(db_path=db_path)
            actualizar_resultado(picks[1]["id"], "win", closing_odds=1.80, db_path=db_path)
            actualizar_resultado(picks[0]["id"], "win", closing_odds=1.95, db_path=db_path)
            panel = dashboard_data(db_path=db_path)

        self.assertAlmostEqual(panel["solo_stakazos"]["clv_medio"], 11.11, places=2)
        self.assertAlmostEqual(panel["solo_elite"]["clv_medio"], -2.56, places=2)
        self.assertEqual(panel["solo_stakazos"]["clv_positivo_pct"], 100.0)
        self.assertEqual(panel["solo_stakazos"]["cerradas"], 1)
        self.assertEqual(panel["solo_elite"]["cerradas"], 1)

    def test_penalizaciones_historicas_detecta_liga_y_tier_flojos(self):
        pick = {
            "event_id": "hist_1",
            "commence_time": "2026-07-15T20:00:00Z",
            "sport_key": "soccer_test_league",
            "sport_label": "Futbol",
            "league_key": "test_league",
            "league_label": "Test League",
            "partido": "A vs B",
            "equipo": "A",
            "tipo_resultado": "home",
            "casa": "Pinnacle",
            "mercado": "h2h",
            "cuota_apuesta": 2.00,
            "cuota_minima_aceptable": 1.85,
            "probabilidad_mercado": 0.50,
            "probabilidad_modelo": 0.56,
            "valor_esperado": 0.12,
            "margen_cuota": 1.08,
            "kelly_fraccional": 0.02,
            "stake_pct_bankroll": 2,
            "importe_sugerido": 4,
            "stake": 2,
            "recomendacion": "Value interesante",
            "motivo": "test",
            "quality_score": 90,
            "reliability_score": 86,
            "elite_pick": True,
            "elite_tier": "stakazo",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.sqlite3")
            guardar_recomendaciones([dict(pick, event_id=f"hist_{i}", partido=f"A vs B {i}") for i in range(6)], db_path=db_path)
            picks = listar_picks(db_path=db_path)
            for row in picks:
                actualizar_resultado(row["id"], "loss", closing_odds=2.20, db_path=db_path)
            penal = penalizaciones_historicas(db_path=db_path)

        self.assertIn("Test League", penal["ligas"])
        self.assertIn("stakazo", penal["tiers"])
        self.assertGreaterEqual(penal["ligas"]["Test League"]["penalty_score"], 8)

    def test_listar_picks_filtra_y_ordena_en_modo_premium(self):
        pick_stakazo = {
            "event_id": "lp_1",
            "commence_time": "2026-07-15T20:00:00Z",
            "sport_key": "soccer_england_premier_league",
            "sport_label": "Futbol",
            "league_key": "england_premier_league",
            "league_label": "Premier League",
            "partido": "Arsenal vs Chelsea",
            "equipo": "Arsenal",
            "tipo_resultado": "home",
            "casa": "Pinnacle",
            "mercado": "h2h",
            "cuota_apuesta": 1.95,
            "cuota_minima_aceptable": 1.80,
            "probabilidad_mercado": 0.51,
            "probabilidad_modelo": 0.57,
            "valor_esperado": 0.11,
            "margen_cuota": 1.08,
            "kelly_fraccional": 0.02,
            "stake_pct_bankroll": 2,
            "importe_sugerido": 4,
            "stake": 2,
            "recomendacion": "Value interesante",
            "motivo": "test",
            "perfil": "moderado",
            "quality_score": 88,
            "reliability_score": 84,
            "reliability_tier": "alta",
            "elite_pick": True,
            "elite_tier": "stakazo",
        }
        pick_elite = {
            **pick_stakazo,
            "event_id": "lp_2",
            "partido": "Liverpool vs City",
            "equipo": "Liverpool",
            "quality_score": 92,
            "reliability_score": 72,
            "elite_tier": "elite",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.sqlite3")
            guardar_recomendaciones([pick_elite, pick_stakazo], db_path=db_path)
            filtrados = listar_picks(
                db_path=db_path,
                order_by="premium",
                min_reliability_score=80,
                solo_elite=True,
            )
            solo_stakazos = listar_picks(db_path=db_path, solo_stakazos=True)

        self.assertEqual(len(filtrados), 1)
        self.assertEqual(filtrados[0]["partido"], "Arsenal vs Chelsea")
        self.assertEqual(len(solo_stakazos), 1)
        self.assertEqual(solo_stakazos[0]["partido"], "Arsenal vs Chelsea")

    def test_listar_picks_filtra_por_deporte_y_liga(self):
        pick_futbol = {
            "event_id": "sport_1",
            "commence_time": "2026-07-15T20:00:00Z",
            "sport_key": "soccer_england_premier_league",
            "sport_label": "Futbol",
            "league_key": "england_premier_league",
            "league_label": "Premier League",
            "partido": "Arsenal vs Chelsea",
            "equipo": "Arsenal",
            "tipo_resultado": "home",
            "casa": "Pinnacle",
            "mercado": "h2h",
            "cuota_apuesta": 1.95,
            "cuota_minima_aceptable": 1.80,
            "probabilidad_mercado": 0.51,
            "probabilidad_modelo": 0.57,
            "valor_esperado": 0.11,
            "margen_cuota": 1.08,
            "kelly_fraccional": 0.02,
            "stake_pct_bankroll": 2,
            "importe_sugerido": 4,
            "stake": 2,
            "recomendacion": "Value interesante",
            "motivo": "test",
            "quality_score": 88,
            "reliability_score": 84,
            "elite_pick": True,
            "elite_tier": "stakazo",
        }
        pick_tenis = {
            **pick_futbol,
            "event_id": "sport_2",
            "sport_key": "tennis_atp_wimbledon",
            "sport_label": "Tenis",
            "league_key": "atp_wimbledon",
            "league_label": "ATP Wimbledon",
            "partido": "Player A vs Player B",
            "equipo": "Player A",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.sqlite3")
            guardar_recomendaciones([pick_futbol, pick_tenis], db_path=db_path)
            futbol = listar_picks(db_path=db_path, sport_label="Futbol")
            wimbledon = listar_picks(db_path=db_path, league_label="ATP Wimbledon")

        self.assertEqual(len(futbol), 1)
        self.assertEqual(futbol[0]["partido"], "Arsenal vs Chelsea")
        self.assertEqual(len(wimbledon), 1)
        self.assertEqual(wimbledon[0]["partido"], "Player A vs Player B")

    def test_apuestas_hoy_filtra_solo_stakazos(self):
        import main

        original_cuotas = main.cuotas
        original_obtener_elos = main.obtener_elos
        original_guardar_snapshot_cuotas = main.guardar_snapshot_cuotas
        original_analizar_comparador_casas = main.analizar_comparador_casas

        try:
            main.cuotas = lambda mercados="todo", deporte="worldcup": [
                {
                    "id": "evt_1",
                    "commence_time": "2026-07-15T20:00:00Z",
                    "sport_key": "soccer_fifa_world_cup",
                    "sport_label": "Futbol",
                    "league_key": "fifa_world_cup",
                    "league_label": "FIFA World Cup",
                    "home_team": "Spain",
                    "away_team": "France",
                    "bookmakers": [],
                }
            ]
            main.obtener_elos = lambda: {}
            main.guardar_snapshot_cuotas = lambda data: 0
            main.analizar_comparador_casas = lambda *args, **kwargs: [
                {
                    "stake": 2,
                    "elite_pick": True,
                    "elite_tier": "stakazo",
                    "quality_score": 91,
                    "reliability_score": 87,
                    "puntuacion_confianza": 82,
                    "valor_esperado": 0.06,
                    "margen_cuota": 1.07,
                    "partido": "Spain vs France",
                    "partido_es": "Espana vs Francia",
                    "equipo": "Spain",
                    "equipo_es": "Espana",
                    "casa": "Pinnacle",
                    "mercado": "h2h",
                    "recomendacion": "Value interesante",
                    "motivo": "ok",
                },
                {
                    "stake": 2,
                    "elite_pick": True,
                    "elite_tier": "elite",
                    "quality_score": 94,
                    "reliability_score": 78,
                    "puntuacion_confianza": 85,
                    "valor_esperado": 0.08,
                    "margen_cuota": 1.09,
                    "partido": "Spain vs France",
                    "partido_es": "Espana vs Francia",
                    "equipo": "Draw",
                    "equipo_es": "Empate",
                    "casa": "Bet365",
                    "mercado": "h2h",
                    "recomendacion": "Value interesante",
                    "motivo": "ok",
                },
            ]
            data = apuestas_hoy(solo_stakazos=True)
        finally:
            main.cuotas = original_cuotas
            main.obtener_elos = original_obtener_elos
            main.guardar_snapshot_cuotas = original_guardar_snapshot_cuotas
            main.analizar_comparador_casas = original_analizar_comparador_casas

        self.assertEqual(data["total_stakazos"], 1)
        self.assertEqual(data["total_recomendadas"], 1)
        self.assertEqual(data["mejores_apuestas"][0]["elite_tier"], "stakazo")

    def test_apuestas_hoy_con_deporte_todo_agrega_deportes_base(self):
        import main

        original = main.apuestas_hoy
        original_opciones = main.opciones_deporte_disponibles

        def fake_opciones_deporte_disponibles(provider=None, selected=None):
            return [
                {"value": "todo", "label": "Todo"},
                {"value": "worldcup", "label": "Futbol - FIFA World Cup"},
                {"value": "futbol", "label": "Futbol - La Liga"},
                {"value": "tenis", "label": "Tenis - ATP Wimbledon"},
                {"value": "baloncesto", "label": "Baloncesto - NBA"},
                {"value": "basketball_nba_summer_league", "label": "Baloncesto - NBA Summer League"},
            ]

        def fake_apuestas_hoy(*args, **kwargs):
            deporte = kwargs.get("deporte")
            if deporte == "todo":
                return original(*args, **kwargs)
            return {
                "mejores_apuestas": [{
                    "partido": f"{deporte} match",
                    "partido_es": f"{deporte} match",
                    "commence_time": "2026-07-15T21:00:00Z",
                    "equipo": "Team",
                    "equipo_es": "Team",
                    "casa": "Pinnacle",
                    "mercado": "h2h",
                    "stake": 2,
                    "elite_pick": deporte in {"worldcup", "futbol"},
                    "elite_tier": "stakazo" if deporte == "worldcup" else "elite",
                    "quality_score": 90,
                    "reliability_score": 85,
                    "puntuacion_confianza": 80,
                    "valor_esperado": 0.05,
                    "margen_cuota": 1.05,
                }],
                "descartadas": [],
                "partidos_disponibles": [{"id": f"id_{deporte}", "label": f"{deporte} match"}],
                "total_analizadas": 1,
                "total_guardadas": 0,
                "snapshots_guardados": 0,
                "sport_label": "Futbol" if deporte in {"worldcup", "futbol"} else "Tenis" if deporte == "tenis" else "Baloncesto",
                "league_label": f"{deporte} league",
            }

        try:
            main.opciones_deporte_disponibles = fake_opciones_deporte_disponibles
            main.apuestas_hoy = fake_apuestas_hoy
            data = original(deporte="todo")
        finally:
            main.opciones_deporte_disponibles = original_opciones
            main.apuestas_hoy = original

        self.assertEqual(data["sport_label"], "Todo")
        self.assertGreaterEqual(data["total_recomendadas"], 2)
        self.assertLessEqual(data["total_recomendadas"], 6)
        self.assertEqual(data["total_stakazos"], 1)
        self.assertIn("league", data["partidos_disponibles"][0]["label"])
        self.assertEqual(len(data["cobertura_deportes"]), 5)
        self.assertIn(
            "basketball_nba_summer_league",
            [item["deporte"] for item in data["cobertura_deportes"]],
        )

    def test_apuestas_hoy_con_deporte_todo_omite_liga_con_error_y_sigue(self):
        import main

        original = main.apuestas_hoy
        original_opciones = main.opciones_deporte_disponibles

        def fake_opciones_deporte_disponibles(provider=None, selected=None):
            return [
                {"value": "todo", "label": "Todo"},
                {"value": "worldcup", "label": "Futbol - FIFA World Cup"},
                {"value": "basketball_nba_summer_league", "label": "Baloncesto - NBA Summer League"},
            ]

        def fake_apuestas_hoy(*args, **kwargs):
            deporte = kwargs.get("deporte")
            if deporte == "todo":
                return original(*args, **kwargs)
            if deporte == "worldcup":
                return {
                    "mejores_apuestas": [{
                        "partido": "worldcup match",
                        "partido_es": "worldcup match",
                        "equipo": "Team",
                        "equipo_es": "Team",
                        "casa": "Pinnacle",
                        "mercado": "h2h",
                        "stake": 2,
                        "elite_pick": True,
                        "elite_tier": "elite",
                        "quality_score": 80,
                        "reliability_score": 78,
                        "puntuacion_confianza": 72,
                        "valor_esperado": 0.03,
                        "margen_cuota": 1.02,
                    }],
                    "descartadas": [],
                    "partidos_disponibles": [{"id": "id_worldcup", "label": "worldcup match"}],
                    "total_analizadas": 1,
                    "total_guardadas": 0,
                    "snapshots_guardados": 0,
                    "sport_label": "Futbol",
                    "league_label": "FIFA World Cup",
                }
            raise main.HTTPException(
                status_code=502,
                detail="The Odds API no acepta algun parametro enviado, normalmente sport, region o mercado.",
            )

        try:
            main.opciones_deporte_disponibles = fake_opciones_deporte_disponibles
            main.apuestas_hoy = fake_apuestas_hoy
            data = original(deporte="todo")
        finally:
            main.opciones_deporte_disponibles = original_opciones
            main.apuestas_hoy = original

        self.assertEqual(data["total_recomendadas"], 1)
        self.assertEqual(len(data["cobertura_deportes"]), 1)
        self.assertEqual(len(data["errores_cobertura"]), 1)
        self.assertIn("omitieron", data["aviso_cobertura"])
        self.assertIn("basketball_nba_summer_league", data["aviso_cobertura"])

    def test_apuestas_hoy_aplica_penalizacion_historica_y_degrada_tier(self):
        import main

        original_cuotas = main.cuotas
        original_obtener_elos = main.obtener_elos
        original_guardar_snapshot_cuotas = main.guardar_snapshot_cuotas
        original_analizar_comparador_casas = main.analizar_comparador_casas
        original_penalizaciones_historicas = main.penalizaciones_historicas

        try:
            main.cuotas = lambda mercados="todo", deporte="worldcup": [
                {
                    "id": "evt_hist",
                    "commence_time": "2026-07-15T20:00:00Z",
                    "sport_key": "soccer_fifa_world_cup",
                    "sport_label": "Futbol",
                    "league_key": "fifa_world_cup",
                    "league_label": "FIFA World Cup",
                    "home_team": "Spain",
                    "away_team": "France",
                    "bookmakers": [],
                }
            ]
            main.obtener_elos = lambda: {}
            main.guardar_snapshot_cuotas = lambda data: 0
            main.penalizaciones_historicas = lambda: {
                "ligas": {
                    "FIFA World Cup": {
                        "penalty_score": 12,
                        "level": "media",
                        "reasons": ["ROI negativo"],
                        "sample_closed": 8,
                    }
                },
                "tiers": {
                    "stakazo": {
                        "penalty_score": 8,
                        "level": "moderada",
                        "reasons": ["CLV negativo"],
                        "sample_closed": 8,
                    }
                },
            }
            main.analizar_comparador_casas = lambda *args, **kwargs: [
                {
                    "stake": 2,
                    "elite_pick": True,
                    "elite_tier": "stakazo",
                    "quality_score": 91,
                    "reliability_score": 87,
                    "puntuacion_confianza": 82,
                    "valor_esperado": 0.06,
                    "margen_cuota": 1.07,
                    "partido": "Spain vs France",
                    "partido_es": "Espana vs Francia",
                    "equipo": "Spain",
                    "equipo_es": "Espana",
                    "casa": "Pinnacle",
                    "mercado": "h2h",
                    "recomendacion": "Value interesante",
                    "motivo": "ok",
                },
            ]
            data = apuestas_hoy()
        finally:
            main.cuotas = original_cuotas
            main.obtener_elos = original_obtener_elos
            main.guardar_snapshot_cuotas = original_guardar_snapshot_cuotas
            main.analizar_comparador_casas = original_analizar_comparador_casas
            main.penalizaciones_historicas = original_penalizaciones_historicas

        pick = data["mejores_apuestas"][0]
        self.assertEqual(pick["elite_tier"], "seguimiento")
        self.assertFalse(pick["elite_pick"])
        self.assertEqual(pick["historical_penalty_level"], "alta")
        self.assertGreaterEqual(pick["historical_penalty_score"], 20)

    def test_opciones_deporte_disponibles_mantiene_seleccion_dinamica(self):
        import main

        original = main.discover_available_catalog

        try:
            main.discover_available_catalog = lambda provider=None: {
                "provider": "the_odds_api",
                "sports": [
                    build_dynamic_context_from_sport_key("soccer_spain_la_liga"),
                    build_dynamic_context_from_sport_key("tennis_atp_wimbledon"),
                ],
            }
            opciones = opciones_deporte_disponibles(selected="soccer_brazil_serie_a")
        finally:
            main.discover_available_catalog = original

        valores = {item["value"] for item in opciones}

        self.assertEqual(opciones[0]["value"], "todo")
        self.assertEqual(opciones[1]["value"], "soccer_brazil_serie_a")
        self.assertIn("futbol", valores)
        self.assertIn("baloncesto", valores)
        self.assertIn("tennis_atp_wimbledon", valores)

    def test_opciones_deporte_disponibles_conserva_catalogo_base_con_discovery(self):
        import main

        original = main.discover_available_catalog

        try:
            main.discover_available_catalog = lambda provider=None: {
                "provider": "the_odds_api",
                "sports": [
                    build_dynamic_context_from_sport_key("basketball_wnba"),
                ],
            }
            opciones = opciones_deporte_disponibles(selected="basketball_wnba")
        finally:
            main.discover_available_catalog = original

        valores = {item["value"] for item in opciones}

        self.assertIn("worldcup", valores)
        self.assertIn("futbol", valores)
        self.assertIn("tenis", valores)
        self.assertIn("baloncesto", valores)
        self.assertIn("basketball_wnba", valores)

    def test_deportes_agregados_para_todo_limita_y_prioriza(self):
        import main

        original_opciones = main.opciones_deporte_disponibles

        try:
            main.opciones_deporte_disponibles = lambda provider=None, selected=None: [
                {"value": "todo", "label": "Todo"},
                {"value": "soccer_spain_la_liga", "label": "Futbol - La Liga"},
                {"value": "soccer_england_premier_league", "label": "Futbol - Premier League"},
                {"value": "soccer_germany_bundesliga", "label": "Futbol - Bundesliga"},
                {"value": "soccer_italy_serie_a", "label": "Futbol - Serie A"},
                {"value": "soccer_brazil_serie_b", "label": "Futbol - Serie B"},
                {"value": "basketball_nba", "label": "Baloncesto - NBA"},
                {"value": "basketball_wnba", "label": "Baloncesto - WNBA"},
                {"value": "basketball_nba_summer_league", "label": "Baloncesto - NBA Summer League"},
                {"value": "basketball_argentina_lnb", "label": "Baloncesto - LNB"},
                {"value": "tennis_atp_wimbledon", "label": "Tenis - ATP Wimbledon"},
                {"value": "tennis_wta_berlin", "label": "Tenis - WTA Berlin"},
                {"value": "tennis_atp_bastad", "label": "Tenis - ATP Bastad"},
            ]

            seleccionados = main.deportes_agregados_para_todo()
        finally:
            main.opciones_deporte_disponibles = original_opciones

        self.assertEqual(len(seleccionados), 8)
        self.assertIn("soccer_spain_la_liga", seleccionados)
        self.assertIn("soccer_england_premier_league", seleccionados)
        self.assertIn("basketball_nba", seleccionados)
        self.assertIn("tennis_atp_wimbledon", seleccionados)
        self.assertNotIn("soccer_brazil_serie_b", seleccionados)
        self.assertNotIn("basketball_argentina_lnb", seleccionados)
        self.assertNotIn("tennis_atp_bastad", seleccionados)

    def test_limitar_picks_todo_prioriza_fiabilidad_y_proximidad(self):
        import main

        picks = [
            {
                "partido": "A vs B",
                "league_label": "League 1",
                "sport_label": "Futbol",
                "elite_tier": "elite",
                "reliability_score": 88,
                "quality_score": 86,
                "puntuacion_confianza": 74,
                "valor_esperado": 0.04,
                "margen_cuota": 1.04,
                "commence_time": "2026-07-15T21:00:00Z",
            },
            {
                "partido": "C vs D",
                "league_label": "League 2",
                "sport_label": "Futbol",
                "elite_tier": "elite",
                "reliability_score": 83,
                "quality_score": 80,
                "puntuacion_confianza": 70,
                "valor_esperado": 0.035,
                "margen_cuota": 1.03,
                "commence_time": "2026-07-17T21:00:00Z",
            },
            {
                "partido": "E vs F",
                "league_label": "League 1",
                "sport_label": "Futbol",
                "elite_tier": "premium",
                "reliability_score": 79,
                "quality_score": 78,
                "puntuacion_confianza": 68,
                "valor_esperado": 0.03,
                "margen_cuota": 1.02,
                "commence_time": "2026-07-15T22:00:00Z",
            },
            {
                "partido": "G vs H",
                "league_label": "League 1",
                "sport_label": "Futbol",
                "elite_tier": "premium",
                "reliability_score": 77,
                "quality_score": 75,
                "puntuacion_confianza": 66,
                "valor_esperado": 0.028,
                "margen_cuota": 1.02,
                "commence_time": "2026-07-15T23:00:00Z",
            },
        ]

        seleccionadas = main.limitar_picks_todo(picks, max_total=4)

        self.assertEqual(seleccionadas[0]["partido"], "A vs B")
        self.assertEqual(len([p for p in seleccionadas if p["league_label"] == "League 1"]), 2)
        self.assertNotIn("G vs H", [p["partido"] for p in seleccionadas])

    def test_telegram_config_exige_token_y_chat_id(self):
        import main

        original_token = main.TELEGRAM_BOT_TOKEN
        original_chat_id = main.TELEGRAM_CHAT_ID
        original_env_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        original_env_chat = os.environ.get("TELEGRAM_CHAT_ID")

        try:
            main.TELEGRAM_BOT_TOKEN = ""
            main.TELEGRAM_CHAT_ID = ""
            os.environ["TELEGRAM_BOT_TOKEN"] = ""
            os.environ["TELEGRAM_CHAT_ID"] = ""
            with self.assertRaises(Exception):
                telegram_config()
        finally:
            main.TELEGRAM_BOT_TOKEN = original_token
            main.TELEGRAM_CHAT_ID = original_chat_id
            if original_env_token is None:
                os.environ.pop("TELEGRAM_BOT_TOKEN", None)
            else:
                os.environ["TELEGRAM_BOT_TOKEN"] = original_env_token
            if original_env_chat is None:
                os.environ.pop("TELEGRAM_CHAT_ID", None)
            else:
                os.environ["TELEGRAM_CHAT_ID"] = original_env_chat

    def test_enviar_mensaje_telegram_usa_endpoint_oficial(self):
        import main

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"ok": True, "result": {"message_id": 123}}

        captured = {}
        original_post = main.requests.post

        def fake_post(url, json=None, timeout=None):
            captured["url"] = url
            captured["json"] = json
            captured["timeout"] = timeout
            return FakeResponse()

        try:
            main.requests.post = fake_post
            result = enviar_mensaje_telegram("Hola Telegram", token="abc123", chat_id="999")
        finally:
            main.requests.post = original_post

        self.assertTrue(result["ok"])
        self.assertEqual(captured["url"], "https://api.telegram.org/botabc123/sendMessage")
        self.assertEqual(captured["json"]["chat_id"], "999")
        self.assertEqual(captured["json"]["text"], "Hola Telegram")
        self.assertEqual(captured["json"]["parse_mode"], "HTML")

    def test_telegram_keyboard_for_pick_incluye_botones_clave(self):
        keyboard = telegram_keyboard_for_pick(77)
        callback_values = [
            button["callback_data"]
            for row in keyboard["inline_keyboard"]
            for button in row
        ]

        self.assertIn("pick:77:bet", callback_values)
        self.assertIn("pick:77:win", callback_values)
        self.assertIn("pick:77:loss", callback_values)
        self.assertIn("pick:77:push", callback_values)

    def test_formatear_mensaje_telegram_pick_separa_ajuste_historico(self):
        pick = {
            "id": 31,
            "elite_tier": "elite",
            "league_label": "La Liga",
            "partido": "Real Madrid vs Real Sociedad",
            "equipo": "Menos de 3 goles",
            "mercado": "totals",
            "cuota_apuesta": 1.97,
            "stake": 2.5,
            "importe_sugerido": 7.5,
            "valor_esperado": 0.055,
            "quality_score": 71,
            "confianza": "Baja",
            "reliability_tier": "alta",
            "reliability_score": 84,
            "motivo_es": "Valor positivo con exposicion controlada",
            "historical_penalty_reasons": [
                "tier:elite:CLV muy negativo",
                "tier:elite:Hit rate flojo",
            ],
        }

        mensaje = formatear_mensaje_telegram_pick(pick)

        self.assertIn("<b>Motivo:</b> Valor positivo con exposicion controlada", mensaje)
        self.assertIn("<b>Ajuste historico:</b> CLV muy negativo, hit rate flojo", mensaje)
        self.assertNotIn("tier:elite:CLV muy negativo", mensaje)

    def test_guardar_recomendaciones_unicas_evita_duplicados_pendientes(self):
        recomendacion = {
            "event_id": "evt_unique",
            "commence_time": "2026-07-15T20:00:00Z",
            "sport_key": "soccer_spain_la_liga",
            "sport_label": "Futbol",
            "league_key": "la_liga",
            "league_label": "La Liga",
            "partido": "A vs B",
            "equipo": "A",
            "equipo_raw": "A",
            "tipo_resultado": "home",
            "tipo_resultado_raw": "home",
            "casa": "Bet365",
            "mercado": "h2h",
            "cuota_apuesta": 2.1,
            "cuota_minima_aceptable": 2.0,
            "probabilidad_mercado": 0.48,
            "probabilidad_modelo": 0.52,
            "valor_esperado": 0.04,
            "margen_cuota": 0.02,
            "stake_pct_bankroll": 1.5,
            "importe_sugerido": 2.5,
            "stake": 1.0,
            "recomendacion": "Value",
            "motivo": "Test",
            "quality_score": 82,
            "elite_pick": True,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "tracker.sqlite3")
            primera = guardar_recomendaciones_unicas([recomendacion], db_path=db_path)
            segunda = guardar_recomendaciones_unicas([recomendacion], db_path=db_path)
            picks = listar_picks(db_path=db_path)

        self.assertEqual(len(primera), 1)
        self.assertEqual(len(segunda), 1)
        self.assertEqual(primera[0]["id"], segunda[0]["id"])
        self.assertEqual(len(picks), 1)

    def test_publicaciones_telegram_quedan_trazadas_y_reflejan_resultado(self):
        recomendacion = {
            "event_id": "evt_pub",
            "commence_time": "2026-07-15T20:00:00Z",
            "sport_key": "soccer_spain_la_liga",
            "sport_label": "Futbol",
            "league_key": "la_liga",
            "league_label": "La Liga",
            "partido": "A vs B",
            "equipo": "A",
            "equipo_raw": "A",
            "tipo_resultado": "home",
            "tipo_resultado_raw": "home",
            "casa": "Bet365",
            "mercado": "h2h",
            "cuota_apuesta": 2.2,
            "cuota_minima_aceptable": 2.0,
            "probabilidad_mercado": 0.47,
            "probabilidad_modelo": 0.53,
            "valor_esperado": 0.05,
            "margen_cuota": 0.03,
            "stake_pct_bankroll": 1.5,
            "importe_sugerido": 3.0,
            "stake": 1.0,
            "recomendacion": "Value",
            "motivo": "Test",
            "quality_score": 84,
            "elite_pick": True,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "tracker.sqlite3")
            pick = guardar_recomendaciones_unicas([recomendacion], db_path=db_path)[0]
            registrar_publicacion_telegram(
                publication_type="manual",
                payload={
                    "deporte": "Futbol",
                    "liga": "La Liga",
                    "total_elite": 1,
                    "total_stakazos": 0,
                },
                items=[
                    {"message_kind": "summary", "text": "Resumen", "telegram_message_id": 10},
                    {"message_kind": "pick", "text": "Pick A", "telegram_message_id": 11, "pick_id": pick["id"]},
                ],
                db_path=db_path,
            )
            antes = listar_publicaciones_telegram(db_path=db_path)
            actualizar_resultado(pick["id"], "win", db_path=db_path)
            despues = listar_publicaciones_telegram(db_path=db_path)

        self.assertEqual(antes[0]["resultado_resumen"]["pendientes"], 1)
        self.assertEqual(despues[0]["resultado_resumen"]["ganadas"], 1)
        self.assertEqual(despues[0]["items"][1]["pick_id"], pick["id"])

    def test_marcar_apuesta_real_pick_y_filtrar_en_tracking(self):
        recomendacion = {
            "event_id": "evt_mark",
            "commence_time": "2026-07-15T20:00:00Z",
            "sport_key": "soccer_spain_la_liga",
            "sport_label": "Futbol",
            "league_key": "la_liga",
            "league_label": "La Liga",
            "partido": "A vs B",
            "equipo": "A",
            "equipo_raw": "A",
            "tipo_resultado": "home",
            "tipo_resultado_raw": "home",
            "casa": "Pinnacle",
            "mercado": "h2h",
            "cuota_apuesta": 2.0,
            "importe_sugerido": 5.0,
            "stake": 1.0,
            "recomendacion": "Value",
            "motivo": "Test",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "tracker.sqlite3")
            pick = guardar_recomendaciones_unicas([recomendacion], db_path=db_path)[0]
            marcado = marcar_apuesta_real_pick(pick["id"], db_path=db_path)
            apostadas = listar_picks(db_path=db_path, apuesta_real=True)
            no_apostadas = listar_picks(db_path=db_path, apuesta_real=False)

        self.assertIsNotNone(marcado)
        self.assertEqual(len(apostadas), 1)
        self.assertEqual(apostadas[0]["id"], pick["id"])
        self.assertEqual(len(no_apostadas), 0)

    def test_procesar_callback_pick_marca_apostada(self):
        import main

        called = {}
        original = main.marcar_apuesta_real_pick

        try:
            main.marcar_apuesta_real_pick = lambda pick_id: called.setdefault("pick_id", pick_id) or {"id": pick_id}
            mensaje = procesar_callback_pick(55, "bet")
        finally:
            main.marcar_apuesta_real_pick = original

        self.assertEqual(called["pick_id"], 55)
        self.assertIn("apostada", mensaje.lower())

    def test_publicar_pronosticos_telegram_guarda_y_registra(self):
        import main

        original_pronosticos = main.pronosticos
        original_enviar = main.enviar_mensaje_telegram
        original_guardar = main.guardar_recomendaciones_unicas
        original_registrar = main.registrar_publicacion_telegram
        original_token = main.TELEGRAM_BOT_TOKEN
        original_chat_id = main.TELEGRAM_CHAT_ID
        sent_texts = []

        try:
            main.TELEGRAM_BOT_TOKEN = "token_test"
            main.TELEGRAM_CHAT_ID = "chat_test"
            main.pronosticos = lambda **kwargs: {
                "deporte": "Futbol",
                "liga": "La Liga",
                "resumen_telegram": "Resumen",
                "mensajes_telegram": ["Pick 1"],
                "pronosticos": [
                    {
                        "event_id": "evt_pub",
                        "mercado": "h2h",
                        "tipo_resultado": "home",
                        "equipo": "A",
                        "casa": "Bet365",
                    }
                ],
                "total_elite": 1,
                "total_stakazos": 0,
            }
            main.enviar_mensaje_telegram = lambda texto, token=None, chat_id=None, reply_markup=None: {
                "ok": True,
                "result": {"message_id": len(sent_texts) + 1},
            } if not sent_texts.append(texto) else None
            main.guardar_recomendaciones_unicas = lambda recomendaciones: [{"id": 77, **recomendaciones[0]}]
            main.registrar_publicacion_telegram = lambda publication_type, payload, items: {
                "id": 900,
                "publication_type": publication_type,
                "items": items,
            }

            resultado = publicar_pronosticos_telegram(solo_stakazos=True)
        finally:
            main.pronosticos = original_pronosticos
            main.enviar_mensaje_telegram = original_enviar
            main.guardar_recomendaciones_unicas = original_guardar
            main.registrar_publicacion_telegram = original_registrar
            main.TELEGRAM_BOT_TOKEN = original_token
            main.TELEGRAM_CHAT_ID = original_chat_id

        self.assertTrue(resultado["ok"])
        self.assertEqual(resultado["mensajes_enviados"], 2)
        self.assertEqual(resultado["picks_guardados"], 1)
        self.assertEqual(resultado["publication_id"], 900)

    def test_publicar_pronosticos_telegram_hace_fallback_a_elite_si_no_hay_stakazos(self):
        import main

        original_pronosticos = main.pronosticos
        original_enviar = main.enviar_mensaje_telegram
        original_guardar = main.guardar_recomendaciones_unicas
        original_registrar = main.registrar_publicacion_telegram
        original_token = main.TELEGRAM_BOT_TOKEN
        original_chat_id = main.TELEGRAM_CHAT_ID
        llamadas = []

        def fake_pronosticos(**kwargs):
            llamadas.append(kwargs)
            if kwargs.get("solo_stakazos"):
                return {
                    "deporte": "Todo",
                    "liga": "Todas las ligas base",
                    "resumen_telegram": "Todo | Todas las ligas base | 0 pick(s) elite | 0 stakazo(s)",
                    "mensajes_telegram": [],
                    "pronosticos": [],
                    "total_elite": 5,
                    "total_stakazos": 0,
                }
            return {
                "deporte": "Todo",
                "liga": "Todas las ligas base",
                "resumen_telegram": "Todo | Todas las ligas base | 5 pick(s) elite | 0 stakazo(s)",
                "mensajes_telegram": ["Pick elite 1"],
                "pronosticos": [
                    {
                        "event_id": "evt_pub",
                        "mercado": "h2h",
                        "tipo_resultado": "home",
                        "equipo": "A",
                        "casa": "Pinnacle",
                    }
                ],
                "total_elite": 5,
                "total_stakazos": 0,
            }

        try:
            main.TELEGRAM_BOT_TOKEN = "token_test"
            main.TELEGRAM_CHAT_ID = "chat_test"
            main.pronosticos = fake_pronosticos
            main.enviar_mensaje_telegram = lambda texto, token=None, chat_id=None, reply_markup=None: {"ok": True, "result": {"message_id": 1}}
            main.guardar_recomendaciones_unicas = lambda recomendaciones: [{"id": 88, **recomendaciones[0]}]
            main.registrar_publicacion_telegram = lambda publication_type, payload, items: {"id": 901}

            resultado = publicar_pronosticos_telegram(solo_stakazos=True)
        finally:
            main.pronosticos = original_pronosticos
            main.enviar_mensaje_telegram = original_enviar
            main.guardar_recomendaciones_unicas = original_guardar
            main.registrar_publicacion_telegram = original_registrar
            main.TELEGRAM_BOT_TOKEN = original_token
            main.TELEGRAM_CHAT_ID = original_chat_id

        self.assertTrue(resultado["fallback_a_elite"])
        self.assertEqual(len(llamadas), 2)
        self.assertTrue(llamadas[0]["solo_stakazos"])
        self.assertFalse(llamadas[1]["solo_stakazos"])
        self.assertEqual(resultado["mensajes_enviados"], 2)

    def test_auto_publicar_telegram_once_respeta_configuracion(self):
        import main

        original_enabled = main.TELEGRAM_AUTOPUBLISH_ENABLED
        original_token = main.TELEGRAM_BOT_TOKEN
        original_chat_id = main.TELEGRAM_CHAT_ID
        original_solo_stakazos = main.TELEGRAM_AUTOPUBLISH_SOLO_STAKAZOS
        original_publicar = main.publicar_pronosticos_telegram

        try:
            main.TELEGRAM_AUTOPUBLISH_ENABLED = True
            main.TELEGRAM_BOT_TOKEN = "token_test"
            main.TELEGRAM_CHAT_ID = "chat_test"
            main.TELEGRAM_AUTOPUBLISH_SOLO_STAKAZOS = True
            main.publicar_pronosticos_telegram = lambda **kwargs: kwargs
            resultado = auto_publicar_telegram_once()
        finally:
            main.TELEGRAM_AUTOPUBLISH_ENABLED = original_enabled
            main.TELEGRAM_BOT_TOKEN = original_token
            main.TELEGRAM_CHAT_ID = original_chat_id
            main.TELEGRAM_AUTOPUBLISH_SOLO_STAKAZOS = original_solo_stakazos
            main.publicar_pronosticos_telegram = original_publicar

        self.assertEqual(resultado["publication_type"], "auto")
        self.assertTrue(resultado["solo_stakazos"])

    def test_opciones_deporte_disponibles_incluye_todo(self):
        opciones = opciones_deporte_disponibles(selected="futbol")

        self.assertEqual(opciones[0]["value"], "todo")
        self.assertIn("Todo", opciones[0]["label"])

    def test_opciones_deporte_disponibles_hace_fallback_si_falla_discovery(self):
        import main

        original = main.discover_available_catalog

        try:
            main.discover_available_catalog = lambda provider=None: (_ for _ in ()).throw(RuntimeError("boom"))
            opciones = opciones_deporte_disponibles(selected="futbol")
        finally:
            main.discover_available_catalog = original

        valores = {item["value"] for item in opciones}

        self.assertIn("worldcup", valores)
        self.assertIn("futbol", valores)
        self.assertIn("tenis", valores)
        self.assertIn("baloncesto", valores)

    def test_adaptar_sportsgameodds_events_a_formato_interno(self):
        events = [
            {
                "eventID": "sgo_1",
                "startsAt": "2026-07-05T20:00:00Z",
                "teams": {
                    "home": {"names": {"long": "Brazil"}},
                    "away": {"names": {"long": "Norway"}},
                },
                "odds": {
                    "points-home-game-ml-home": {
                        "statID": "points",
                        "statEntityID": "home",
                        "periodID": "game",
                        "betTypeID": "ml",
                        "sideID": "home",
                        "byBookmaker": {
                            "pinnacle": {"odds": "-150", "available": True},
                            "bet365": {"odds": "1.72", "available": True},
                        },
                    },
                    "points-away-game-ml-away": {
                        "statID": "points",
                        "statEntityID": "away",
                        "periodID": "game",
                        "betTypeID": "ml",
                        "sideID": "away",
                        "byBookmaker": {
                            "pinnacle": {"odds": "+430", "available": True},
                        },
                    },
                    "points-all-game-ou-over": {
                        "statID": "points",
                        "statEntityID": "all",
                        "periodID": "game",
                        "betTypeID": "ou",
                        "sideID": "over",
                        "byBookmaker": {
                            "pinnacle": {"odds": "-110", "overUnder": "2.5", "available": True},
                        },
                    },
                    "points-all-game-ou-under": {
                        "statID": "points",
                        "statEntityID": "all",
                        "periodID": "game",
                        "betTypeID": "ou",
                        "sideID": "under",
                        "byBookmaker": {
                            "pinnacle": {"odds": "-105", "overUnder": "2.5", "available": True},
                        },
                    },
                },
            }
        ]

        eventos = adaptar_sportsgameodds_events(events, ["h2h", "totals"])
        pinnacle = next(b for b in eventos[0]["bookmakers"] if b["title"] == "Pinnacle")
        market_keys = {m["key"] for m in pinnacle["markets"]}
        h2h = next(m for m in pinnacle["markets"] if m["key"] == "h2h")
        totals = next(m for m in pinnacle["markets"] if m["key"] == "totals")

        self.assertEqual(eventos[0]["home_team"], "Brazil")
        self.assertEqual(eventos[0]["away_team"], "Norway")
        self.assertEqual(market_keys, {"h2h", "totals"})
        self.assertEqual(h2h["outcomes"][0]["name"], "Brazil")
        self.assertEqual(totals["outcomes"][0]["point"], 2.5)

    def test_comparador_doble_oportunidad(self):
        partidos = [
            {
                "id": "evt_double",
                "commence_time": "2026-06-16T20:00:00Z",
                "home_team": "Brazil",
                "away_team": "Norway",
                "bookmakers": [
                    {
                        "title": "Pinnacle",
                        "markets": [
                            {
                                "key": "double_chance",
                                "outcomes": [
                                    {"name": "Brazil or Draw", "price": 1.25},
                                    {"name": "Brazil or Norway", "price": 1.30},
                                    {"name": "Draw or Norway", "price": 2.00},
                                ],
                            }
                        ],
                    },
                    {
                        "title": "Matchbook",
                        "markets": [
                            {
                                "key": "double_chance",
                                "outcomes": [
                                    {"name": "Brazil or Draw", "price": 1.25},
                                    {"name": "Brazil or Norway", "price": 1.30},
                                    {"name": "Draw or Norway", "price": 2.30},
                                ],
                            }
                        ],
                    },
                ],
            }
        ]
        recomendaciones = analizar_comparador_casas(
            partidos,
            {},
            bankroll=100,
            perfil="agresivo",
            mercados=["double_chance"],
        )
        pick = next(r for r in recomendaciones if r["equipo"] == "Draw or Norway")

        self.assertEqual(pick["mercado"], "double_chance")
        self.assertGreater(pick["stake"], 0)

    def test_snapshots_y_aprendizaje_guardan_cuotas(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.sqlite3")
            guardadas = guardar_snapshot_cuotas(PARTIDOS_FAKE, db_path=db_path)
            info = aprendizaje(db_path=db_path)

            self.assertGreater(guardadas, 0)
            self.assertGreater(info["snapshots_guardados"], 0)

    def test_analizar_partidos_devuelve_1x2(self):
        elos = {"ES": 2100, "FR": 2000}
        recomendaciones = analizar_partidos(PARTIDOS_FAKE, elos, bankroll=100)
        tipos = {r["tipo_resultado"] for r in recomendaciones}

        self.assertEqual(len(recomendaciones), 3)
        self.assertEqual(tipos, {"home", "draw", "away"})

    def test_tracking_guarda_y_cierra_pick(self):
        elos = {"ES": 2400, "FR": 1800}
        recomendaciones = analizar_partidos(PARTIDOS_FAKE, elos, bankroll=100)
        recomendadas = [r for r in recomendaciones if r["stake"] > 0]

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.sqlite3")
            guardadas = guardar_recomendaciones(recomendadas, db_path=db_path)

            self.assertGreaterEqual(guardadas, 0)

            if guardadas:
                pick = actualizar_resultado(1, "win", closing_odds=2.6, db_path=db_path)
                stats = estadisticas(db_path=db_path)

                self.assertEqual(pick["estado"], "cerrada")
                self.assertEqual(stats["picks_cerrados"], 1)

    def test_tracking_registra_apuesta_real_y_aprende_con_importe_real(self):
        recomendacion = {
            "event_id": "evt_real",
            "commence_time": "2026-07-05T20:00:00Z",
            "partido": "Brazil vs Norway",
            "equipo": "Norway",
            "equipo_raw": "Norway",
            "tipo_resultado": "away",
            "tipo_resultado_raw": "away",
            "casa": "Matchbook",
            "mercado": "h2h",
            "cuota_apuesta": 5.10,
            "cuota_minima_aceptable": 4.82,
            "probabilidad_mercado": 0.20,
            "probabilidad_elo": 0.21,
            "probabilidad_modelo": 0.207,
            "valor_esperado": 0.057,
            "margen_cuota": 1.056,
            "kelly_fraccional": 0.005,
            "stake_pct_bankroll": 0.5,
            "importe_sugerido": 0.50,
            "stake": 0.5,
            "recomendacion": "Value moderado",
            "motivo": "test",
            "perfil": "alto_riesgo",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.sqlite3")
            pick = guardar_apuesta_real(recomendacion, 10, db_path=db_path)
            pick = actualizar_importe_pick(pick["id"], 12, db_path=db_path)
            cerrado = actualizar_resultado(pick["id"], "win", db_path=db_path)
            stats = estadisticas(db_path=db_path)

            self.assertEqual(pick["importe_sugerido"], 12)
            self.assertEqual(cerrado["profit_loss"], 49.2)
            self.assertEqual(stats["beneficio"], 49.2)

    def test_tracking_actualiza_cuota_real_y_recalcula_beneficio(self):
        recomendacion = {
            "event_id": "evt_cuota_real",
            "commence_time": "2026-07-05T20:00:00Z",
            "partido": "Brazil vs Norway",
            "equipo": "Norway",
            "equipo_raw": "Norway",
            "tipo_resultado": "away",
            "tipo_resultado_raw": "away",
            "casa": "Betfair",
            "mercado": "h2h",
            "cuota_apuesta": 3.00,
            "importe_sugerido": 5.00,
            "stake": 1,
            "recomendacion": "Manual",
            "motivo": "test",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.sqlite3")
            pick = guardar_apuesta_real(recomendacion, 5, db_path=db_path)
            actualizar_resultado(pick["id"], "win", db_path=db_path)
            actualizado = actualizar_cuota_pick(pick["id"], 3.40, db_path=db_path)
            stats = estadisticas(db_path=db_path)

            self.assertEqual(actualizado["cuota"], 3.4)
            self.assertEqual(actualizado["profit_loss"], 12.0)
            self.assertEqual(stats["beneficio"], 12.0)

    def test_bankroll_se_guarda_en_settings(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.sqlite3")
            actualizado = actualizar_bankroll(10, db_path=db_path)
            actual = obtener_bankroll(db_path=db_path)

            self.assertEqual(actualizado, 10)
            self.assertEqual(actual, 10)

    def test_liquidacion_automatica_h2h_con_scores(self):
        recomendacion = {
            "event_id": "score_h2h",
            "commence_time": "2026-06-16T20:00:00Z",
            "partido": "Spain vs France",
            "equipo": "Spain",
            "equipo_raw": "Spain",
            "tipo_resultado": "home",
            "tipo_resultado_raw": "home",
            "casa": "Pinnacle",
            "mercado": "h2h",
            "cuota_apuesta": 2.00,
            "cuota_minima_aceptable": 1.90,
            "probabilidad_mercado": 0.50,
            "probabilidad_elo": 0.55,
            "probabilidad_modelo": 0.55,
            "valor_esperado": 0.10,
            "margen_cuota": 1.05,
            "kelly_fraccional": 0.01,
            "stake_pct_bankroll": 1,
            "importe_sugerido": 10,
            "stake": 2,
            "recomendacion": "Value moderado",
            "motivo": "test",
            "perfil": "agresivo",
            "modelo_mercado": "Mercado + ELO",
        }
        scores = [
            {
                "id": "score_h2h",
                "completed": True,
                "home_team": "Spain",
                "away_team": "France",
                "scores": [
                    {"name": "Spain", "score": "2"},
                    {"name": "France", "score": "1"},
                ],
            }
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.sqlite3")
            guardar_recomendaciones([recomendacion], db_path=db_path)
            resultado = liquidar_picks_con_scores(scores, db_path=db_path)
            stats = estadisticas(db_path=db_path)

            self.assertEqual(resultado["liquidados"], 1)
            self.assertEqual(stats["ganadas"], 1)
            self.assertEqual(stats["beneficio"], 10)

    def test_liquidacion_automatica_total_goles_y_dashboard(self):
        recomendacion = {
            "event_id": "score_totals",
            "commence_time": "2026-06-16T20:00:00Z",
            "partido": "Brazil vs Norway",
            "equipo": "Over",
            "equipo_raw": "Over",
            "tipo_resultado": "totals",
            "tipo_resultado_raw": "totals",
            "casa": "Matchbook",
            "mercado": "totals",
            "cuota_apuesta": 2.20,
            "cuota_minima_aceptable": 1.90,
            "probabilidad_mercado": 0.50,
            "probabilidad_elo": 0.50,
            "probabilidad_modelo": 0.56,
            "valor_esperado": 0.23,
            "margen_cuota": 1.15,
            "kelly_fraccional": 0.03,
            "stake_pct_bankroll": 3,
            "importe_sugerido": 5,
            "stake": 2,
            "recomendacion": "Value interesante",
            "motivo": "test",
            "outcome_point": 2.5,
            "perfil": "alto_riesgo",
            "modelo_mercado": "Poisson goles",
        }
        scores = [
            {
                "id": "score_totals",
                "completed": True,
                "home_team": "Brazil",
                "away_team": "Norway",
                "scores": [
                    {"name": "Brazil", "score": "2"},
                    {"name": "Norway", "score": "1"},
                ],
            }
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.sqlite3")
            guardar_recomendaciones([recomendacion], db_path=db_path)
            resultado = liquidar_picks_con_scores(scores, db_path=db_path)
            panel = dashboard_data(db_path=db_path)

            self.assertEqual(resultado["liquidados"], 1)
            self.assertEqual(panel["resumen"]["ganadas"], 1)
            self.assertEqual(panel["por_mercado"][0]["nombre"], "totals")
            self.assertEqual(panel["por_perfil"][0]["nombre"], "alto_riesgo")

    def test_tenis_modelo_conservador_reconoce_el_deporte(self):
        partidos = [
            {
                "id": "tennis_1",
                "commence_time": "2026-07-20T12:00:00Z",
                "sport_key": "tennis_atp_wimbledon",
                "sport_label": "Tenis",
                "league_key": "atp_wimbledon",
                "league_label": "ATP Wimbledon",
                "home_team": "Player A",
                "away_team": "Player B",
                "bookmakers": [
                    {
                        "title": "Pinnacle",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Player A", "price": 1.62},
                                    {"name": "Player B", "price": 2.35},
                                ],
                            }
                        ],
                    },
                    {
                        "title": "Bet365",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Player A", "price": 1.74},
                                    {"name": "Player B", "price": 2.20},
                                ],
                            }
                        ],
                    },
                ],
            }
        ]
        recomendaciones = analizar_comparador_casas(
            partidos,
            {},
            bankroll=100,
            perfil="moderado",
            mercados=["h2h"],
            source_strength="tennis_model",
        )
        favorita = next(r for r in recomendaciones if r["equipo"] == "Player A")

        self.assertEqual(favorita["sport_label"], "Tenis")
        self.assertEqual(favorita["modelo_mercado"], "Tenis moneyline conservador")

    def test_basket_totals_modelo_conservador_reconoce_el_deporte(self):
        partidos = [
            {
                "id": "basket_1",
                "commence_time": "2026-07-20T20:00:00Z",
                "sport_key": "basketball_nba",
                "sport_label": "Baloncesto",
                "league_key": "nba",
                "league_label": "NBA",
                "home_team": "Lakers",
                "away_team": "Celtics",
                "bookmakers": [
                    {
                        "title": "Pinnacle",
                        "markets": [
                            {
                                "key": "totals",
                                "outcomes": [
                                    {"name": "Over", "price": 1.91, "point": 219.5},
                                    {"name": "Under", "price": 1.91, "point": 219.5},
                                ],
                            }
                        ],
                    },
                    {
                        "title": "Bet365",
                        "markets": [
                            {
                                "key": "totals",
                                "outcomes": [
                                    {"name": "Over", "price": 2.02, "point": 219.5},
                                    {"name": "Under", "price": 1.82, "point": 219.5},
                                ],
                            }
                        ],
                    },
                ],
            }
        ]
        recomendaciones = analizar_comparador_casas(
            partidos,
            {},
            bankroll=100,
            perfil="moderado",
            mercados=["totals"],
            source_strength="basketball_model",
        )
        over = next(r for r in recomendaciones if r["equipo"] == "Over")

        self.assertEqual(over["sport_label"], "Baloncesto")
        self.assertEqual(over["modelo_mercado"], "Basket total baseline")


if __name__ == "__main__":
    unittest.main()
