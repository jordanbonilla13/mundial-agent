import os
import tempfile
import unittest
from unittest.mock import patch

from betting_model import (
    analizar_comparador_casas,
    analizar_partidos,
    ajustar_probabilidad_por_mercado,
    calcular_fiabilidad_pick,
    clasificar_pick_elite,
    calcular_kelly_fraccional,
    market_consensus_snapshot,
    normalizar_probabilidades,
    rescatar_casi_value,
)
from app.ai_service import build_pick_action_advice, enrich_picks_with_ai_narratives
from app.calibration import (
    CalibrationSnapshot,
    SegmentMetrics,
    _generate_model_adjustments,
    build_training_dataset,
)
import app.calibrated_scoring as calibrated_scoring
from app.forecasting import apply_market_regime_guard
from app.lab_service import _event_time_label, build_empty_lab_run, build_lab_run, render_lab_run_html
from app.performance_guard_service import apply_performance_guard_to_pick, build_performance_guard
from app.publication_service import publish_telegram_predictions
from app.recent_panel_service import build_recent_form_panel, format_recent_form_panel_telegram
from app.risk_controls import apply_risk_policy_to_pick, build_risk_policy
from app.runtime_settings import RuntimeSettings
from app.safety_service import publication_guard_state
from app.operating_mode import multi_sport_pick_limit, single_sport_pick_limit, telegram_pick_limit
from app.exposure import apply_exposure_limits
from app.audit import format_audit_report_telegram
from tracking import actualizar_resultado, archivar_picks_pendientes, conectar, eliminar_picks_archivadas, eliminar_reset_historial_deporte, estadisticas, guardar_recomendaciones
from tracking import aprendizaje, dashboard_data, guardar_snapshot_cuotas, liquidar_picks_con_scores, listar_evaluaciones_picks, listar_picks, obtener_closing_odds_pick, penalizaciones_historicas
from tracking import actualizar_bankroll, actualizar_cuota_pick, actualizar_importe_pick, guardar_apuesta_real, marcar_apuesta_real_pick, obtener_bankroll
from tracking import guardar_recomendaciones_unicas, guardar_reset_historial_deporte, listar_publicaciones_telegram, registrar_publicacion_telegram
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
    procesar_comando_telegram,
    publicar_pronosticos_lab,
    publicar_pronosticos_telegram,
    prioridad_pick,
    resolver_contexto_deporte,
    resolver_mercados,
    telegram_config,
    telegram_keyboard_for_pick,
)
from translations import apuesta_es, tipo_resultado_es


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
    def test_event_time_label_convierte_utc_a_hora_madrid(self):
        self.assertEqual(_event_time_label("2026-07-29T17:00:00Z"), "19:00")

    def test_normalizar_probabilidades_suma_uno(self):
        outcomes = PARTIDOS_FAKE[0]["bookmakers"][0]["markets"][0]["outcomes"]
        normalizadas = normalizar_probabilidades(outcomes)
        total = sum(x["probabilidad_mercado"] for x in normalizadas)

        self.assertAlmostEqual(total, 1, places=6)

    def test_kelly_fraccional_tiene_techo(self):
        stake = calcular_kelly_fraccional(0.60, 2.20)

        self.assertLessEqual(stake, 0.015)

    def test_market_consensus_snapshot_resume_soporte_y_dispersion(self):
        partido = {
            "id": "evt_consensus",
            "home_team": "Belgium",
            "away_team": "United States",
            "bookmakers": [
                {"title": "Pinnacle", "markets": [{"key": "h2h", "outcomes": [{"name": "Belgium", "price": 2.2}, {"name": "Draw", "price": 3.3}, {"name": "United States", "price": 3.4}]}]},
                {"title": "Bet365", "markets": [{"key": "h2h", "outcomes": [{"name": "Belgium", "price": 2.3}, {"name": "Draw", "price": 3.25}, {"name": "United States", "price": 3.35}]}]},
                {"title": "Unibet", "markets": [{"key": "h2h", "outcomes": [{"name": "Belgium", "price": 2.26}, {"name": "Draw", "price": 3.28}, {"name": "United States", "price": 3.38}]}]},
            ],
        }

        consensus = market_consensus_snapshot(partido, "h2h", ("h2h", "home", None), selected_odds=2.3)

        self.assertEqual(consensus["support_count"], 3)
        self.assertAlmostEqual(consensus["consensus_odds"], 2.26, places=2)
        self.assertGreater(consensus["edge_vs_consensus"], 0)
        self.assertGreater(consensus["width_pct"], 0)

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

    def test_fiabilidad_mejora_con_soporte_profundo_y_mercado_ordenado(self):
        score_alto, tier_alto = calcular_fiabilidad_pick(
            sport_key="soccer_spain_la_liga",
            league_key="la_liga",
            market_key="h2h",
            casa="Pinnacle",
            source_strength="market+model",
            market_support_count=6,
            market_width_pct=0.02,
        )
        score_bajo, tier_bajo = calcular_fiabilidad_pick(
            sport_key="soccer_spain_la_liga",
            league_key="la_liga",
            market_key="h2h",
            casa="Pinnacle",
            source_strength="market+model",
            market_support_count=1,
            market_width_pct=0.14,
        )

        self.assertGreater(score_alto, score_bajo)
        self.assertEqual(tier_alto, "alta")
        self.assertLess(score_bajo, 90)

    def test_comparador_incluye_metricas_de_consenso_en_recomendaciones(self):
        partidos = [
            {
                "id": "evt_market_depth",
                "commence_time": "2026-06-15T20:00:00Z",
                "sport_key": "soccer_spain_la_liga",
                "league_key": "la_liga",
                "home_team": "Belgium",
                "away_team": "United States",
                "bookmakers": [
                    {
                        "title": "Pinnacle",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Belgium", "price": 2.2},
                                    {"name": "Draw", "price": 3.3},
                                    {"name": "United States", "price": 3.4},
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
                                    {"name": "Belgium", "price": 2.3},
                                    {"name": "Draw", "price": 3.3},
                                    {"name": "United States", "price": 3.3},
                                ],
                            }
                        ],
                    },
                    {
                        "title": "Unibet",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Belgium", "price": 2.28},
                                    {"name": "Draw", "price": 3.25},
                                    {"name": "United States", "price": 3.32},
                                ],
                            }
                        ],
                    },
                ],
            }
        ]
        elos = {"BE": 2150, "US": 1750}

        recomendaciones = analizar_comparador_casas(partidos, elos, bankroll=100, perfil="agresivo")
        belgium_bet365 = next(r for r in recomendaciones if r["equipo"] == "Belgium" and r["casa"] == "Bet365")

        self.assertEqual(belgium_bet365["market_support_count"], 3)
        self.assertIsNotNone(belgium_bet365["market_consensus_odds"])
        self.assertIsNotNone(belgium_bet365["market_edge_vs_consensus"])
        self.assertGreater(belgium_bet365["market_edge_vs_consensus"], 0)

    def test_build_risk_policy_activa_kill_switch_con_historico_malo(self):
        policy = build_risk_policy(
            total_closed=45,
            roi=-9,
            clv_medio=-3.5,
            clv_positive_pct=38,
            operating_mode="equilibrado",
        )

        self.assertTrue(policy["block_new_picks"])
        self.assertEqual(policy["reason"], "kill_switch_rendimiento")

    def test_apply_risk_policy_bloquea_mercado_fragil(self):
        pick = {
            "stake": 2,
            "importe_sugerido": 8,
            "stake_pct_bankroll": 2.5,
            "kelly_fraccional": 0.025,
            "market_signal": "mercado_fragil",
            "elite_pick": False,
            "league_label": "Liga X",
        }
        policy = {
            "sample_stage": "early",
            "stake_multiplier": 0.7,
            "max_stake_units": 2.0,
            "block_new_picks": False,
            "block_fragile_markets": True,
            "only_elite_when_cautious": False,
            "reason": "muestra_corta",
        }

        adjusted = apply_risk_policy_to_pick(pick, policy=policy, league_penalties={})

        self.assertEqual(adjusted["stake"], 0)
        self.assertTrue(adjusted["risk_guard_blocked"])

    def test_market_regime_guard_bloquea_wnba_over_fragil(self):
        guarded = apply_market_regime_guard(
            {
                "sport_key": "basketball_wnba",
                "mercado": "totals",
                "equipo": "Over",
                "market_support_count": 2,
                "market_width_pct": 0.082,
                "market_edge_vs_consensus": 0.011,
                "margen_cuota": 1.018,
                "valor_esperado": 0.019,
            }
        )

        self.assertTrue(guarded["market_guard_blocked"])
        self.assertEqual(guarded["market_guard_level"], "block")
        self.assertGreaterEqual(guarded["market_guard_penalty_score"], 26)

    def test_apply_risk_policy_bloquea_market_guard_especializado(self):
        pick = {
            "stake": 2,
            "importe_sugerido": 8,
            "stake_pct_bankroll": 2.5,
            "kelly_fraccional": 0.025,
            "market_signal": "consenso_medio",
            "market_guard_blocked": True,
            "elite_pick": True,
            "league_label": "WNBA",
        }
        policy = {
            "sample_stage": "growing",
            "stake_multiplier": 1.0,
            "max_stake_units": 3.0,
            "block_new_picks": False,
            "block_fragile_markets": False,
            "only_elite_when_cautious": False,
            "reason": "normal",
        }

        adjusted = apply_risk_policy_to_pick(pick, policy=policy, league_penalties={})

        self.assertEqual(adjusted["stake"], 0)
        self.assertTrue(adjusted["risk_guard_blocked"])
        self.assertIn("guard de mercado", adjusted["motivo"])

    def test_build_risk_policy_agresivo_relaja_frenos_con_muestra_corta(self):
        policy = build_risk_policy(
            total_closed=6,
            roi=0,
            clv_medio=None,
            clv_positive_pct=None,
            operating_mode="agresivo",
        )

        self.assertEqual(policy["operating_mode"], "agresivo")
        self.assertEqual(policy["reason"], "muestra_corta")
        self.assertFalse(policy["block_fragile_markets"])
        self.assertGreaterEqual(policy["stake_multiplier"], 0.85)
        self.assertGreaterEqual(policy["max_stake_units"], 2.5)

    def test_operating_mode_agresivo_amplia_limites_de_salida(self):
        self.assertEqual(single_sport_pick_limit("agresivo", "todos"), 7)
        self.assertEqual(single_sport_pick_limit("agresivo", "evt_1"), 4)
        self.assertEqual(multi_sport_pick_limit("agresivo"), 10)
        self.assertEqual(telegram_pick_limit("agresivo", solo_stakazos=False), 9)

    def test_apply_exposure_limits_controla_concentracion_por_evento(self):
        picks = [
            {"event_id": "evt_1", "partido": "A vs B", "league_label": "League 1", "mercado": "h2h"},
            {"event_id": "evt_1", "partido": "A vs B", "league_label": "League 1", "mercado": "totals"},
            {"event_id": "evt_1", "partido": "A vs B", "league_label": "League 1", "mercado": "btts"},
            {"event_id": "evt_2", "partido": "C vs D", "league_label": "League 1", "mercado": "h2h"},
        ]

        selected = apply_exposure_limits(picks, operating_mode="agresivo", max_total=10)

        self.assertEqual(len([p for p in selected if p["event_id"] == "evt_1"]), 2)
        self.assertNotIn("btts", [p["mercado"] for p in selected if p["event_id"] == "evt_1"])

    def test_comparador_bloquea_h2h_ambiguo_si_los_dos_lados_salen_parejos(self):
        partidos = [
            {
                "id": "evt_ambiguous_h2h",
                "commence_time": "2026-07-29T20:00:00Z",
                "sport_key": "tennis_atp_washington_open",
                "home_team": "Alex de Minaur",
                "away_team": "Stefanos Tsitsipas",
                "bookmakers": [
                    {
                        "title": "Pinnacle",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Alex de Minaur", "price": 1.98},
                                    {"name": "Stefanos Tsitsipas", "price": 1.98},
                                ],
                            }
                        ],
                    },
                    {
                        "title": "Betfair",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Alex de Minaur", "price": 2.04},
                                    {"name": "Stefanos Tsitsipas", "price": 2.04},
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
            casa_referencia="Pinnacle",
            mercados=["h2h"],
            source_strength="tennis_model",
        )

        picks_activas = [r for r in recomendaciones if r["stake"] > 0]
        bloqueadas = [
            r for r in recomendaciones
            if r["partido"] == "Alex de Minaur vs Stefanos Tsitsipas"
            and r["recomendacion"] == "No apostar"
            and "ambiguo" in str(r["motivo"]).lower()
        ]

        self.assertEqual(picks_activas, [])
        self.assertGreaterEqual(len(bloqueadas), 2)

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
        mercados_handicap, aviso_handicap = resolver_mercados("handicap", deporte="baloncesto")

        self.assertEqual(mercados_tenis, ["h2h"])
        self.assertIn("no aplica", aviso_tenis.lower())
        self.assertEqual(mercados_basket, ["h2h", "spreads", "totals", "alternate_totals"])
        self.assertIsNone(aviso_basket)
        self.assertEqual(mercados_handicap, ["spreads"])
        self.assertIsNone(aviso_handicap)

    def test_traducciones_handicap_baloncesto(self):
        self.assertEqual(
            apuesta_es("Dallas Wings", mercado="spreads", point=4.5, sport_key="basketball_wnba"),
            "Dallas Wings +4,5",
        )
        self.assertEqual(
            tipo_resultado_es("spreads", sport_key="basketball_wnba"),
            "Handicap",
        )

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
            "market_support_count": 6,
            "market_width_pct": 0.02,
            "market_edge_vs_consensus": 0.035,
            "stake": 2.5,
        }
        pick_elite = {
            "elite_tier": "elite",
            "quality_score": 92,
            "reliability_score": 70,
            "puntuacion_confianza": 84,
            "valor_esperado": 0.08,
            "margen_cuota": 1.08,
            "market_support_count": 2,
            "market_width_pct": 0.11,
            "market_edge_vs_consensus": 0.005,
            "stake": 1.0,
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
            "market_support_count": 4,
            "market_width_pct": 0.04,
            "market_edge_vs_consensus": 0.025,
            "stake": 1.0,
        }
        seguimiento = {
            "elite_tier": "seguimiento",
            "quality_score": 88,
            "reliability_score": 80,
            "puntuacion_confianza": 75,
            "valor_esperado": 0.07,
            "margen_cuota": 1.08,
            "market_support_count": 1,
            "market_width_pct": 0.14,
            "market_edge_vs_consensus": -0.01,
            "stake": 0.5,
        }

        self.assertGreater(prioridad_pick(premium), prioridad_pick(seguimiento))

    def test_apuestas_hoy_enriquece_ranking_score_y_execution_score(self):
        import main

        original_cuotas = main.cuotas
        original_obtener_elos = main.obtener_elos
        original_guardar_snapshot_cuotas = main.guardar_snapshot_cuotas
        original_analizar_comparador_casas = main.analizar_comparador_casas
        original_penalizaciones_historicas = main.penalizaciones_historicas

        try:
            main.cuotas = lambda mercados="todo", deporte="worldcup": [
                {
                    "id": "evt_rank",
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
            main.penalizaciones_historicas = lambda: {}
            main.analizar_comparador_casas = lambda *args, **kwargs: [
                {
                    "stake": 2,
                    "elite_pick": True,
                    "elite_tier": "elite",
                    "quality_score": 84,
                    "reliability_score": 80,
                    "puntuacion_confianza": 77,
                    "valor_esperado": 0.05,
                    "margen_cuota": 1.05,
                    "partido": "Spain vs France",
                    "partido_es": "Espana vs Francia",
                    "equipo": "Spain",
                    "equipo_es": "Espana",
                    "casa": "Pinnacle",
                    "mercado": "h2h",
                    "recomendacion": "Value interesante",
                    "motivo": "ok",
                    "market_support_count": 5,
                    "market_width_pct": 0.03,
                    "market_edge_vs_consensus": 0.028,
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
        self.assertIn("ranking_score", pick)
        self.assertIn("execution_score", pick)
        self.assertIn("market_signal", pick)
        self.assertGreaterEqual(pick["ranking_score"], pick["execution_score"])

    def test_mensaje_telegram_incluye_fiabilidad(self):
        mensaje = formatear_mensaje_telegram_pick({
            "league_label": "Premier League",
            "elite_tier": "stakazo",
            "partido_es": "Arsenal vs Chelsea",
            "equipo_es": "Arsenal",
            "commence_time": "2026-07-18T20:00:00Z",
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
            "ai_advice_es": "Yo si le entraria con stake disciplinado.",
        })

        self.assertIn("<b>Fiabilidad:</b> alta (86/100)", mensaje)
        self.assertIn("🔥 STAKAZO | Premier League", mensaje)
        self.assertIn("<b>Stake:</b> 2.5/5 | <b>Importe:</b> 8.0 EUR", mensaje)
        self.assertIn("<b>Empieza:</b>", mensaje)
        self.assertIn("<b>Consejo IA:</b>", mensaje)
        self.assertIn("⭐ Yo si le entraria con stake disciplinado.", mensaje)

    def test_ai_advice_bloquea_pick_fragil(self):
        advice = build_pick_action_advice(
            {
                "stake": 0,
                "market_guard_blocked": True,
                "market_guard_level": "block",
                "historical_penalty_level": "alta",
            }
        )

        self.assertIn("no le entraria", advice.lower())

    def test_mensaje_telegram_no_marca_estrella_si_ia_no_respalda(self):
        mensaje = formatear_mensaje_telegram_pick({
            "league_label": "Premier League",
            "elite_tier": "premium",
            "partido_es": "Arsenal vs Chelsea",
            "equipo_es": "Arsenal",
            "commence_time": "2026-07-18T20:00:00Z",
            "cuota_apuesta": 1.95,
            "stake": 1.0,
            "importe_sugerido": 4.0,
            "valor_esperado": 0.021,
            "confianza": "Media",
            "reliability_tier": "media",
            "reliability_score": 68,
            "quality_score": 70,
            "mercado": "h2h",
            "tipo_resultado_es": "Local",
            "tipo_resultado": "home",
            "tipo_resultado_raw": "home",
            "motivo_es": "Value justo.",
            "ai_advice_es": "Yo iria con cautela o la dejaria pasar: la veo demasiado justa para forzar entrada.",
        })

        self.assertIn("<b>Consejo IA:</b>", mensaje)
        self.assertNotIn("⭐ Yo iria con cautela", mensaje)

    def test_ai_batch_promotes_uno_o_dos_picks_buenos(self):
        picks = enrich_picks_with_ai_narratives(
            [
                {
                    "event_id": "a",
                    "stake": 2.5,
                    "elite_tier": "elite",
                    "quality_score": 81,
                    "reliability_score": 78,
                    "valor_esperado": 0.034,
                    "historical_penalty_level": "none",
                    "market_guard_level": "medium",
                },
                {
                    "event_id": "b",
                    "stake": 2.0,
                    "elite_tier": "premium",
                    "quality_score": 74,
                    "reliability_score": 71,
                    "valor_esperado": 0.028,
                    "historical_penalty_level": "none",
                    "market_guard_level": "low",
                },
                {
                    "event_id": "c",
                    "stake": 1.0,
                    "elite_tier": "seguimiento",
                    "quality_score": 62,
                    "reliability_score": 60,
                    "valor_esperado": 0.012,
                    "historical_penalty_level": "media",
                    "market_guard_level": "low",
                },
            ]
        )

        positivos = [
            pick for pick in picks
            if "yo si le meteria" in pick["ai_advice_es"].lower() or "yo si le entraria" in pick["ai_advice_es"].lower()
        ]
        self.assertGreaterEqual(len(positivos), 2)

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

    def test_recent_form_panel_resume_ventanas_y_segmentos(self):
        import app.recent_panel_service as panel_module

        original_listar_evaluaciones = panel_module.listar_evaluaciones_picks

        try:
            panel_module.listar_evaluaciones_picks = lambda limit=5000, db_path=None: [
                {"resultado": "loss", "clv_pct": -1.2, "value_captured": -0.04, "sport_label": "Tenis", "mercado": "h2h"},
                {"resultado": "win", "clv_pct": 1.8, "value_captured": 0.08, "sport_label": "Tenis", "mercado": "h2h"},
                {"resultado": "win", "clv_pct": 0.6, "value_captured": 0.03, "sport_label": "Baloncesto", "mercado": "totals"},
                {"resultado": "push", "clv_pct": 0.0, "value_captured": 0.0, "sport_label": "Baloncesto", "mercado": "totals"},
                {"resultado": "win", "clv_pct": 1.1, "value_captured": 0.02, "sport_label": "Futbol", "mercado": "btts"},
            ]

            panel = build_recent_form_panel()
        finally:
            panel_module.listar_evaluaciones_picks = original_listar_evaluaciones

        self.assertEqual(panel["total_evaluations"], 5)
        self.assertEqual(panel["windows"][0]["size"], 10)
        self.assertEqual(panel["windows"][0]["sample"], 5)
        self.assertEqual(panel["current_streak"]["type"], "loss")
        self.assertEqual(panel["current_streak"]["count"], 1)
        self.assertEqual(panel["by_sport"][0]["name"], "Tenis")
        text = format_recent_form_panel_telegram(panel)
        self.assertIn("PANEL RECIENTE DEL MODELO", text)
        self.assertIn("Por deporte", text)
        self.assertIn("Por mercado", text)

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

    def test_penalizaciones_historicas_detectan_deriva_clv_en_liga_mercado(self):
        pick = {
            "commence_time": "2026-07-15T20:00:00Z",
            "sport_key": "basketball_wnba",
            "sport_label": "Baloncesto",
            "league_key": "wnba",
            "league_label": "WNBA",
            "partido": "A vs B",
            "equipo": "Over",
            "tipo_resultado": "over",
            "casa": "Pinnacle",
            "mercado": "totals",
            "cuota_apuesta": 1.95,
            "cuota_minima_aceptable": 1.84,
            "probabilidad_mercado": 0.51,
            "probabilidad_modelo": 0.56,
            "valor_esperado": 0.05,
            "margen_cuota": 1.04,
            "kelly_fraccional": 0.01,
            "stake_pct_bankroll": 1.5,
            "importe_sugerido": 3,
            "stake": 1.5,
            "recomendacion": "Value moderado",
            "motivo": "test clv drift",
            "quality_score": 76,
            "reliability_score": 74,
            "elite_pick": False,
            "elite_tier": "premium",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.sqlite3")
            guardar_recomendaciones([dict(pick, event_id=f"wnba_{i}", partido=f"A vs B {i}") for i in range(4)], db_path=db_path)
            picks = listar_picks(db_path=db_path)
            for row in picks:
                actualizar_resultado(row["id"], "loss", closing_odds=2.18, db_path=db_path)
            penal = penalizaciones_historicas(db_path=db_path)

        combo_key = "WNBA::totals"
        self.assertIn(combo_key, penal["ligas_mercados"])
        self.assertTrue(any("CLV" in reason for reason in penal["ligas_mercados"][combo_key]["reasons"]))

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

    def test_apuestas_hoy_omite_eventos_mas_alla_de_72_horas(self):
        import main

        original_cuotas = main.cuotas
        original_obtener_elos = main.obtener_elos
        original_guardar_snapshot_cuotas = main.guardar_snapshot_cuotas
        original_analizar_comparador_casas = main.analizar_comparador_casas

        try:
            main.cuotas = lambda mercados="todo", deporte="worldcup": [
                {
                    "id": "evt_near",
                    "commence_time": "2026-07-18T20:00:00Z",
                    "sport_key": "soccer_fifa_world_cup",
                    "sport_label": "Futbol",
                    "league_key": "fifa_world_cup",
                    "league_label": "FIFA World Cup",
                    "home_team": "Spain",
                    "away_team": "France",
                    "bookmakers": [],
                },
                {
                    "id": "evt_far",
                    "commence_time": "2026-08-17T20:00:00Z",
                    "sport_key": "soccer_fifa_world_cup",
                    "sport_label": "Futbol",
                    "league_key": "fifa_world_cup",
                    "league_label": "FIFA World Cup",
                    "home_team": "Barcelona",
                    "away_team": "Athletic",
                    "bookmakers": [],
                },
            ]
            main.obtener_elos = lambda: {}
            main.guardar_snapshot_cuotas = lambda data: len(data)

            def fake_analizar(partidos, *args, **kwargs):
                return [
                    {
                        "stake": 2,
                        "elite_pick": True,
                        "elite_tier": "elite",
                        "quality_score": 88,
                        "reliability_score": 84,
                        "puntuacion_confianza": 80,
                        "valor_esperado": 0.05,
                        "margen_cuota": 1.05,
                        "partido": f"{partido['home_team']} vs {partido['away_team']}",
                        "equipo": partido["home_team"],
                        "casa": "Pinnacle",
                        "mercado": "h2h",
                        "recomendacion": "Value interesante",
                        "motivo": "ok",
                        "commence_time": partido["commence_time"],
                    }
                    for partido in partidos
                ]

            main.analizar_comparador_casas = fake_analizar
            data = apuestas_hoy()
        finally:
            main.cuotas = original_cuotas
            main.obtener_elos = original_obtener_elos
            main.guardar_snapshot_cuotas = original_guardar_snapshot_cuotas
            main.analizar_comparador_casas = original_analizar_comparador_casas

        partidos = [pick["partido"] for pick in data["mejores_apuestas"]]
        self.assertIn("España vs Francia", partidos)
        self.assertNotIn("Barcelona vs Athletic", partidos)
        self.assertEqual(data["snapshots_guardados"], 1)

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
        self.assertEqual(len(data["cobertura_deportes"]), 2)
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
                "ligas_mercados": {
                    "FIFA World Cup::h2h": {
                        "penalty_score": 6,
                        "level": "moderada",
                        "reasons": ["Racha CLV negativa reciente"],
                        "sample_closed": 4,
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
        self.assertGreaterEqual(pick["historical_penalty_score"], 26)
        self.assertTrue(any("liga_mercado:FIFA World Cup::h2h" in reason for reason in pick["historical_penalty_reasons"]))

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
        original_cargar_filtros = main.cargar_filtros_todo

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
            main.cargar_filtros_todo = lambda: {"disabled_sports": set(), "disabled_leagues": set()}

            seleccionados = main.deportes_agregados_para_todo()
        finally:
            main.opciones_deporte_disponibles = original_opciones
            main.cargar_filtros_todo = original_cargar_filtros

        self.assertEqual(len(seleccionados), 8)
        self.assertIn("soccer_spain_la_liga", seleccionados)
        self.assertIn("soccer_england_premier_league", seleccionados)
        self.assertIn("basketball_nba", seleccionados)
        self.assertIn("tennis_atp_wimbledon", seleccionados)
        self.assertNotIn("soccer_brazil_serie_b", seleccionados)
        self.assertNotIn("basketball_argentina_lnb", seleccionados)
        self.assertNotIn("tennis_atp_bastad", seleccionados)

    def test_deportes_agregados_para_todo_respeta_desactivados(self):
        import main

        original_opciones = main.opciones_deporte_disponibles
        original_cargar_filtros = main.cargar_filtros_todo

        try:
            main.opciones_deporte_disponibles = lambda provider=None, selected=None: [
                {"value": "todo", "label": "Todo"},
                {"value": "soccer_spain_la_liga", "label": "Futbol - La Liga"},
                {"value": "basketball_nba", "label": "Baloncesto - NBA"},
                {"value": "basketball_wnba", "label": "Baloncesto - WNBA"},
                {"value": "tennis_atp_wimbledon", "label": "Tenis - ATP Wimbledon"},
            ]
            main.cargar_filtros_todo = lambda: {
                "disabled_sports": {"baloncesto"},
                "disabled_leagues": {"soccer_spain_la_liga"},
            }

            seleccionados = main.deportes_agregados_para_todo()
        finally:
            main.opciones_deporte_disponibles = original_opciones
            main.cargar_filtros_todo = original_cargar_filtros

        self.assertNotIn("basketball_nba", seleccionados)
        self.assertNotIn("basketball_wnba", seleccionados)
        self.assertNotIn("soccer_spain_la_liga", seleccionados)
        self.assertIn("tennis_atp_wimbledon", seleccionados)

    def test_deportes_agregados_para_todo_no_reintroduce_liga_desactivada_por_alias_generico(self):
        import main

        original_opciones = main.opciones_deporte_disponibles
        original_cargar_filtros = main.cargar_filtros_todo

        try:
            main.opciones_deporte_disponibles = lambda provider=None, selected=None: [
                {"value": "todo", "label": "Todo"},
                {"value": "baloncesto", "label": "Baloncesto"},
                {"value": "basketball_wnba", "label": "Baloncesto - WNBA"},
                {"value": "tennis_atp_canadian_open", "label": "Tenis - ATP Canadian Open"},
            ]
            main.cargar_filtros_todo = lambda: {
                "disabled_sports": set(),
                "disabled_leagues": {"basketball_wnba"},
            }

            seleccionados = main.deportes_agregados_para_todo()
        finally:
            main.opciones_deporte_disponibles = original_opciones
            main.cargar_filtros_todo = original_cargar_filtros

        self.assertNotIn("baloncesto", seleccionados)
        self.assertNotIn("basketball_wnba", seleccionados)
        self.assertIn("tennis_atp_canadian_open", seleccionados)

    def test_deportes_agregados_para_todo_amplia_busqueda_si_no_hay_limite_por_familia(self):
        import main

        original_opciones = main.opciones_deporte_disponibles
        original_cargar_filtros = main.cargar_filtros_todo

        try:
            main.opciones_deporte_disponibles = lambda provider=None, selected=None: [
                {"value": "todo", "label": "Todo"},
                {"value": "soccer_spain_la_liga", "label": "Futbol - La Liga"},
                {"value": "soccer_england_premier_league", "label": "Futbol - Premier League"},
                {"value": "soccer_germany_bundesliga", "label": "Futbol - Bundesliga"},
                {"value": "soccer_italy_serie_a", "label": "Futbol - Serie A"},
                {"value": "soccer_france_ligue_one", "label": "Futbol - Ligue 1"},
                {"value": "soccer_usa_mls", "label": "Futbol - MLS"},
            ]
            main.cargar_filtros_todo = lambda: {
                "disabled_sports": set(),
                "disabled_leagues": set(),
            }

            seleccionados = main.deportes_agregados_para_todo(
                max_total=6,
                strict_family_limits=False,
            )
        finally:
            main.opciones_deporte_disponibles = original_opciones
            main.cargar_filtros_todo = original_cargar_filtros

        self.assertEqual(len(seleccionados), 6)
        self.assertIn("soccer_france_ligue_one", seleccionados)
        self.assertIn("soccer_usa_mls", seleccionados)

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

    def test_limitar_picks_todo_prefiere_eventos_cercanos_si_mantienen_calidad(self):
        import main

        picks = [
            {
                "partido": "Lejano premium",
                "league_label": "League 1",
                "sport_label": "Tenis",
                "elite_tier": "elite",
                "reliability_score": 80,
                "quality_score": 82,
                "puntuacion_confianza": 78,
                "valor_esperado": 0.045,
                "margen_cuota": 1.04,
                "commence_time": "2026-08-08T20:00:00Z",
            },
            {
                "partido": "Cercano fiable",
                "league_label": "League 2",
                "sport_label": "Futbol",
                "elite_tier": "elite",
                "reliability_score": 79,
                "quality_score": 78,
                "puntuacion_confianza": 76,
                "valor_esperado": 0.042,
                "margen_cuota": 1.04,
                "commence_time": "2026-08-06T13:00:00Z",
            },
        ]

        seleccionadas = main.limitar_picks_todo(picks, max_total=1)

        self.assertEqual(seleccionadas[0]["partido"], "Cercano fiable")

    def test_limitar_picks_todo_hace_fallback_si_lo_cercano_no_llega_al_minimo(self):
        import main

        picks = [
            {
                "partido": "Cercano flojo",
                "league_label": "League 1",
                "sport_label": "Baloncesto",
                "elite_tier": "seguimiento",
                "reliability_score": 44,
                "quality_score": 46,
                "puntuacion_confianza": 58,
                "valor_esperado": 0.031,
                "margen_cuota": 1.02,
                "commence_time": "2026-08-06T12:30:00Z",
            },
            {
                "partido": "Lejano solido",
                "league_label": "League 2",
                "sport_label": "Tenis",
                "elite_tier": "elite",
                "reliability_score": 81,
                "quality_score": 79,
                "puntuacion_confianza": 75,
                "valor_esperado": 0.04,
                "margen_cuota": 1.04,
                "commence_time": "2026-08-07T18:00:00Z",
            },
        ]

        seleccionadas = main.limitar_picks_todo(picks, max_total=1)

        self.assertEqual(seleccionadas[0]["partido"], "Lejano solido")

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

    def test_archivar_picks_pendientes_las_saca_de_pendientes_y_listados(self):
        recomendacion = {
            "event_id": "evt_archive",
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
            picks = guardar_recomendaciones_unicas(
                [
                    recomendacion,
                    {**recomendacion, "event_id": "evt_archive_2", "partido": "C vs D"},
                ],
                db_path=db_path,
            )
            resultado = archivar_picks_pendientes(
                id_desde=min(p["id"] for p in picks),
                id_hasta=max(p["id"] for p in picks),
                db_path=db_path,
            )
            visibles = listar_picks(db_path=db_path)
            pendientes = listar_picks(db_path=db_path, estado="pendiente")
            stats = estadisticas(db_path=db_path)

        self.assertEqual(resultado["archivadas"], 2)
        self.assertEqual(visibles, [])
        self.assertEqual(pendientes, [])
        self.assertEqual(stats["picks_pendientes"], 0)

    def test_eliminar_picks_archivadas_las_borra_fisicamente(self):
        recomendacion = {
            "event_id": "evt_delete_archived",
            "commence_time": "2026-07-15T20:00:00Z",
            "sport_key": "basketball_wnba",
            "sport_label": "Baloncesto",
            "league_key": "wnba",
            "league_label": "WNBA",
            "partido": "Old A vs Old B",
            "equipo": "Old A",
            "equipo_raw": "Old A",
            "tipo_resultado": "home",
            "tipo_resultado_raw": "home",
            "casa": "Pinnacle",
            "mercado": "h2h",
            "cuota_apuesta": 1.9,
            "importe_sugerido": 4.0,
            "stake": 1.0,
            "recomendacion": "Cleanup",
            "motivo": "Test",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "tracker.sqlite3")
            picks = guardar_recomendaciones_unicas(
                [
                    recomendacion,
                    {**recomendacion, "event_id": "evt_delete_archived_2", "partido": "Old C vs Old D"},
                ],
                db_path=db_path,
            )
            archivar_picks_pendientes(
                id_desde=min(p["id"] for p in picks),
                id_hasta=max(p["id"] for p in picks),
                db_path=db_path,
            )
            resultado = eliminar_picks_archivadas(
                id_desde=min(p["id"] for p in picks),
                id_hasta=max(p["id"] for p in picks),
                db_path=db_path,
            )
            visibles = listar_picks(db_path=db_path)
            archivadas = listar_picks(db_path=db_path, estado="archivada")
            with conectar(db_path) as conn:
                total_picks = conn.execute("SELECT COUNT(*) AS total FROM picks").fetchone()["total"]

        self.assertEqual(resultado["eliminadas"], 2)
        self.assertEqual(visibles, [])
        self.assertEqual(archivadas, [])
        self.assertEqual(total_picks, 0)

    def test_reset_historial_por_deporte_filtra_evaluaciones_y_dashboard(self):
        recomendacion_futbol = {
            "event_id": "evt_reset_soccer_old",
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
        recomendacion_tenis = {
            **recomendacion_futbol,
            "event_id": "evt_reset_tennis",
            "sport_key": "tennis_atp",
            "sport_label": "Tenis",
            "league_key": "atp",
            "league_label": "ATP Test",
            "partido": "C vs D",
            "equipo": "C",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "tracker.sqlite3")
            old_pick = guardar_recomendaciones_unicas([recomendacion_futbol], db_path=db_path)[0]
            new_pick = guardar_recomendaciones_unicas(
                [{**recomendacion_futbol, "event_id": "evt_reset_soccer_new", "partido": "E vs F", "equipo": "E"}],
                db_path=db_path,
            )[0]
            tennis_pick = guardar_recomendaciones_unicas([recomendacion_tenis], db_path=db_path)[0]

            with conectar(db_path) as conn:
                conn.execute("UPDATE picks SET created_at = ? WHERE id = ?", ("2026-07-01T10:00:00+00:00", old_pick["id"]))
                conn.execute("UPDATE picks SET created_at = ? WHERE id = ?", ("2026-08-01T10:00:00+00:00", new_pick["id"]))
                conn.execute("UPDATE picks SET created_at = ? WHERE id = ?", ("2026-07-01T11:00:00+00:00", tennis_pick["id"]))
                conn.commit()

            actualizar_resultado(old_pick["id"], "loss", closing_odds=2.1, db_path=db_path)
            actualizar_resultado(new_pick["id"], "win", closing_odds=1.8, db_path=db_path)
            actualizar_resultado(tennis_pick["id"], "win", closing_odds=1.9, db_path=db_path)

            with conectar(db_path) as conn:
                conn.execute("UPDATE pick_evaluations SET created_at = ? WHERE pick_id = ?", ("2026-07-01T10:05:00+00:00", old_pick["id"]))
                conn.execute("UPDATE pick_evaluations SET created_at = ? WHERE pick_id = ?", ("2026-08-01T10:05:00+00:00", new_pick["id"]))
                conn.execute("UPDATE pick_evaluations SET created_at = ? WHERE pick_id = ?", ("2026-07-01T11:05:00+00:00", tennis_pick["id"]))
                conn.commit()

            guardar_reset_historial_deporte("Futbol", "2026-08-01T00:00:00+00:00", db_path=db_path)
            evaluaciones = listar_evaluaciones_picks(limit=20, db_path=db_path)
            dashboard = dashboard_data(db_path=db_path)
            panel = build_recent_form_panel(db_path=db_path)
            eliminar_reset_historial_deporte("Futbol", db_path=db_path)
            evaluaciones_sin_reset = listar_evaluaciones_picks(limit=20, db_path=db_path)

        event_ids = {item["event_id"] for item in evaluaciones}
        deportes_dashboard = {item["nombre"]: item["cerradas"] for item in dashboard["por_deporte"]}
        deportes_panel = {item["name"]: item["sample"] for item in panel["by_sport"]}
        event_ids_sin_reset = {item["event_id"] for item in evaluaciones_sin_reset}

        self.assertEqual(event_ids, {"evt_reset_soccer_new", "evt_reset_tennis"})
        self.assertEqual(deportes_dashboard.get("Futbol"), 1)
        self.assertEqual(deportes_dashboard.get("Tenis"), 1)
        self.assertEqual(deportes_panel.get("Futbol"), 1)
        self.assertIn("evt_reset_soccer_old", event_ids_sin_reset)

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
        original_runtime_settings = main.RUNTIME_SETTINGS
        original_publication_guard_state = main.publication_guard_state
        original_token = main.TELEGRAM_BOT_TOKEN
        original_chat_id = main.TELEGRAM_CHAT_ID
        sent_texts = []

        try:
            main.TELEGRAM_BOT_TOKEN = "token_test"
            main.TELEGRAM_CHAT_ID = "chat_test"
            main.RUNTIME_SETTINGS = RuntimeSettings(environment="production", shadow_mode=False)
            main.publication_guard_state = lambda **kwargs: {
                "allow_live_publication": True,
                "mode": "live",
                "reasons": [],
            }
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
            main.RUNTIME_SETTINGS = original_runtime_settings
            main.publication_guard_state = original_publication_guard_state
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
        original_runtime_settings = main.RUNTIME_SETTINGS
        original_publication_guard_state = main.publication_guard_state
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
            main.RUNTIME_SETTINGS = RuntimeSettings(environment="production", shadow_mode=False)
            main.publication_guard_state = lambda **kwargs: {
                "allow_live_publication": True,
                "mode": "live",
                "reasons": [],
            }
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
            main.RUNTIME_SETTINGS = original_runtime_settings
            main.publication_guard_state = original_publication_guard_state
            main.TELEGRAM_BOT_TOKEN = original_token
            main.TELEGRAM_CHAT_ID = original_chat_id

        self.assertTrue(resultado["fallback_a_elite"])
        self.assertEqual(len(llamadas), 2)
        self.assertTrue(llamadas[0]["solo_stakazos"])
        self.assertFalse(llamadas[1]["solo_stakazos"])
        self.assertEqual(resultado["mensajes_enviados"], 2)

    def test_pronosticos_completa_con_mejores_si_hay_pocas_elite(self):
        import main

        original = main.apuestas_hoy

        try:
            main.apuestas_hoy = lambda **kwargs: {
                "sport_label": "Todo",
                "league_label": "Todas las ligas base",
                "criterio": "Test",
                "total_elite": 1,
                "picks_elite": [
                    {
                        "event_id": "evt_1",
                        "mercado": "totals",
                        "tipo_resultado": "over",
                        "equipo": "Mas de 184.5 goles",
                        "casa": "Pinnacle",
                        "partido": "A vs B",
                        "partido_es": "A vs B",
                        "equipo_es": "Mas de 184.5 goles",
                        "stake": 2.5,
                        "valor_esperado": 0.05,
                        "quality_score": 60,
                        "confianza": "Media",
                        "reliability_tier": "media",
                        "reliability_score": 55,
                        "elite_tier": "elite",
                        "motivo_es": "Value",
                    }
                ],
                "mejores_apuestas": [
                    {
                        "event_id": "evt_2",
                        "mercado": "totals",
                        "tipo_resultado": "over",
                        "equipo": "Mas de 181.5 goles",
                        "casa": "Pinnacle",
                        "partido": "C vs D",
                        "partido_es": "C vs D",
                        "equipo_es": "Mas de 181.5 goles",
                        "stake": 2.5,
                        "valor_esperado": 0.051,
                        "quality_score": 47,
                        "confianza": "Media",
                        "reliability_tier": "alta",
                        "reliability_score": 56,
                        "elite_tier": "seguimiento",
                        "motivo_es": "Value 2",
                    },
                    {
                        "event_id": "evt_1",
                        "mercado": "totals",
                        "tipo_resultado": "over",
                        "equipo": "Mas de 184.5 goles",
                        "casa": "Pinnacle",
                        "partido": "A vs B",
                        "partido_es": "A vs B",
                        "equipo_es": "Mas de 184.5 goles",
                        "stake": 2.5,
                        "valor_esperado": 0.05,
                        "quality_score": 60,
                        "confianza": "Media",
                        "reliability_tier": "media",
                        "reliability_score": 55,
                        "elite_tier": "elite",
                        "motivo_es": "Value",
                    },
                ],
            }
            data = main.pronosticos(perfil="alto_riesgo", modo="pinnacle", deporte="todo")
        finally:
            main.apuestas_hoy = original

        self.assertEqual(len(data["pronosticos"]), 2)
        self.assertIn("Envios:</b> 2", data["resumen_telegram"])
        self.assertEqual(data["pronosticos"][0]["event_id"], "evt_2")
        self.assertEqual(data["pronosticos"][1]["event_id"], "evt_1")

    def test_pronosticos_expone_resumen_ia_y_narrativa_si_openai_esta_activa(self):
        import main

        original_apuestas_hoy = main.apuestas_hoy
        original_openai_available = main.openai_available
        original_generate_publication_ai_summary = main.generate_publication_ai_summary
        original_enrich_picks_with_ai_narratives = main.enrich_picks_with_ai_narratives

        try:
            main.apuestas_hoy = lambda **kwargs: {
                "sport_label": "Baloncesto",
                "league_label": "WNBA",
                "criterio": "test",
                "total_elite": 1,
                "total_stakazos": 0,
                "picks_elite": [{"event_id": "evt_1", "elite_tier": "elite"}],
                "mejores_apuestas": [
                    {
                        "event_id": "evt_1",
                        "commence_time": "2026-07-18T20:00:00Z",
                        "mercado": "totals",
                        "tipo_resultado": "over",
                        "equipo": "Over 164.5",
                        "equipo_es": "Mas de 164.5",
                        "casa": "Pinnacle",
                        "partido": "A vs B",
                        "partido_es": "A vs B",
                        "stake": 2.5,
                        "valor_esperado": 0.05,
                        "quality_score": 60,
                        "confianza": "Media",
                        "reliability_tier": "media",
                        "reliability_score": 55,
                        "elite_tier": "elite",
                        "motivo_es": "Value",
                    },
                ],
            }
            main.openai_available = lambda: True
            main.generate_publication_ai_summary = lambda picks, sport_label=None, league_label=None, solo_stakazos=False: "Resumen IA premium."
            main.enrich_picks_with_ai_narratives = lambda picks: [
                {**pick, "ai_narrative_es": "Lectura IA del pick.", "ai_advice_es": "Yo si le entraria con stake prudente."}
                for pick in picks
            ]

            data = main.pronosticos(perfil="alto_riesgo", modo="pinnacle", deporte="todo")
        finally:
            main.apuestas_hoy = original_apuestas_hoy
            main.openai_available = original_openai_available
            main.generate_publication_ai_summary = original_generate_publication_ai_summary
            main.enrich_picks_with_ai_narratives = original_enrich_picks_with_ai_narratives

        self.assertTrue(data["ia_activa"])
        self.assertEqual(data["ia_resumen"], "Resumen IA premium.")
        self.assertEqual(data["pronosticos"][0]["ai_narrative_es"], "Lectura IA del pick.")
        self.assertEqual(data["pronosticos"][0]["ai_advice_es"], "Yo si le entraria con stake prudente.")
        self.assertIn("🧬 <b>Lectura IA:</b>", data["resumen_telegram"])

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

    def test_auditoria_diaria_incluye_lectura_ia_si_openai_esta_activa(self):
        import app.audit as audit_module

        original_get_picks_for_date = audit_module.get_picks_for_date
        original_dashboard_data = audit_module.dashboard_data
        original_generate_calibration_snapshot = audit_module.generate_calibration_snapshot
        original_openai_available = audit_module.openai_available
        original_generate_audit_ai_brief = audit_module.generate_audit_ai_brief

        class DummyCalibration:
            total_picks_evaluated = 12
            alerts = ["Alerta 1"]
            model_adjustments = {"confidence_multipliers": {"model_general": 1.0}}

        try:
            audit_module.get_picks_for_date = lambda target_date, db_path=None: {
                "date": "2026-07-17",
                "recommended": 4,
                "executed": 3,
                "closed": 3,
                "won": 2,
                "lost": 1,
                "total_staked": 12.0,
                "total_profit": 3.5,
                "roi_pct": 29.17,
                "hitrate": 66.67,
                "model_published": {
                    "today": {"published": 2, "pending": 1, "won": 1, "lost": 0, "push": 0, "roi": 14.0, "profit": 1.4, "hit_rate": 100.0},
                    "all_time": {"published": 8, "pending": 2, "won": 4, "lost": 2, "push": 0, "roi": 11.5, "profit": 4.6, "hit_rate": 66.67},
                },
                "picks_list": {"recommended": [], "executed": [], "closed": []},
            }
            audit_module.dashboard_data = lambda db_path=None: {"resumen": {"roi": 8.0, "hit_rate": 54.0}}
            audit_module.generate_calibration_snapshot = lambda: DummyCalibration()
            audit_module.openai_available = lambda: True
            audit_module.generate_audit_ai_brief = lambda report: "Lectura IA de la auditoria."

            report = audit_module.generate_daily_audit_report()
            text = audit_module.format_audit_report_telegram(report)
        finally:
            audit_module.get_picks_for_date = original_get_picks_for_date
            audit_module.dashboard_data = original_dashboard_data
            audit_module.generate_calibration_snapshot = original_generate_calibration_snapshot
            audit_module.openai_available = original_openai_available
            audit_module.generate_audit_ai_brief = original_generate_audit_ai_brief

        self.assertEqual(report["ai_insights"], "Lectura IA de la auditoria.")
        self.assertIn("🧠 Lectura IA de la auditoria.", text)
        self.assertIn("🤖 Portfolio modelo", text)
        self.assertIn("Global: 8 pub", text)

    def test_auditoria_filtra_alertas_de_calibracion_antiguas(self):
        import app.audit as audit_module
        from datetime import datetime, timezone

        stale_metric = SegmentMetrics(
            segment_name="FIFA World Cup",
            segment_type="liga",
            total_picks=20,
            total_recommended=20,
            picks_closed=15,
            picks_won=3,
            picks_lost=10,
            picks_push=2,
            total_staked=100.0,
            total_profit=-18.0,
            roi=-18.0,
            hit_rate=20.0,
            clv=-3.0,
            clv_positive_count=2,
            confidence_score=0.19,
            last_pick_date="2024-07-20T12:00:00+00:00",
            min_sample_warning=False,
            trend="weak",
            recommendation="penalizar",
        )

        recent_metric = SegmentMetrics(
            segment_name="WNBA",
            segment_type="liga",
            total_picks=16,
            total_recommended=16,
            picks_closed=12,
            picks_won=4,
            picks_lost=8,
            picks_push=0,
            total_staked=80.0,
            total_profit=-9.0,
            roi=-11.25,
            hit_rate=33.33,
            clv=-1.5,
            clv_positive_count=3,
            confidence_score=0.31,
            last_pick_date="2026-07-29T12:00:00+00:00",
            min_sample_warning=False,
            trend="weak",
            recommendation="penalizar",
        )

        calibration = CalibrationSnapshot(
            timestamp="2026-07-30T12:00:00+00:00",
            total_picks_evaluated=30,
            segments_by_type={
                "ligas": {"FIFA World Cup": stale_metric, "WNBA": recent_metric},
                "mercados": {},
                "ligas_mercados": {},
                "tiers": {},
                "casas": {},
            },
            model_adjustments={"confidence_multipliers": {"model_general": 1.0}},
            alerts=[],
        )

        original_get_picks_for_date = audit_module.get_picks_for_date
        original_dashboard_data = audit_module.dashboard_data
        original_generate_calibration_snapshot = audit_module.generate_calibration_snapshot
        original_openai_available = audit_module.openai_available

        try:
            audit_module.get_picks_for_date = lambda target_date, db_path=None: {
                "date": "2026-07-30",
                "recommended": 1,
                "executed": 1,
                "closed": 1,
                "won": 1,
                "lost": 0,
                "total_staked": 5.0,
                "total_profit": 2.0,
                "roi_pct": 40.0,
                "hitrate": 100.0,
                "model_published": {
                    "today": {"published": 1, "pending": 0, "won": 1, "lost": 0, "push": 0, "roi": 40.0, "profit": 2.0, "hit_rate": 100.0},
                    "all_time": {"published": 10, "pending": 2, "won": 5, "lost": 3, "push": 0, "roi": 8.0, "profit": 6.0, "hit_rate": 62.5},
                },
                "picks_list": {"recommended": [], "executed": [], "closed": []},
            }
            audit_module.dashboard_data = lambda db_path=None: {"resumen": {"roi": 8.0, "hit_rate": 54.0}}
            audit_module.generate_calibration_snapshot = lambda: calibration
            audit_module.openai_available = lambda: False

            report = audit_module.generate_daily_audit_report(datetime(2026, 7, 30, tzinfo=timezone.utc))
        finally:
            audit_module.get_picks_for_date = original_get_picks_for_date
            audit_module.dashboard_data = original_dashboard_data
            audit_module.generate_calibration_snapshot = original_generate_calibration_snapshot
            audit_module.openai_available = original_openai_available

        joined_alerts = "\n".join(report["alerts"])
        self.assertIn("WNBA", joined_alerts)
        self.assertNotIn("FIFA World Cup", joined_alerts)

    def test_auditoria_excluye_fifa_world_cup_del_resumen_aunque_sea_reciente(self):
        import app.audit as audit_module
        from datetime import datetime, timezone

        fifa_metric = SegmentMetrics(
            segment_name="FIFA World Cup",
            segment_type="liga",
            total_picks=18,
            total_recommended=18,
            picks_closed=12,
            picks_won=3,
            picks_lost=9,
            picks_push=0,
            total_staked=90.0,
            total_profit=-20.0,
            roi=-22.22,
            hit_rate=25.0,
            clv=-2.5,
            clv_positive_count=2,
            confidence_score=0.18,
            last_pick_date="2026-07-29T12:00:00+00:00",
            min_sample_warning=False,
            trend="weak",
            recommendation="penalizar",
        )
        fifa_combo_metric = SegmentMetrics(
            segment_name="FIFA World Cup::h2h",
            segment_type="liga_mercado",
            total_picks=12,
            total_recommended=12,
            picks_closed=10,
            picks_won=2,
            picks_lost=8,
            picks_push=0,
            total_staked=60.0,
            total_profit=-18.0,
            roi=-30.0,
            hit_rate=20.0,
            clv=-3.0,
            clv_positive_count=1,
            confidence_score=0.14,
            last_pick_date="2026-07-29T12:00:00+00:00",
            min_sample_warning=False,
            trend="weak",
            recommendation="penalizar",
        )

        calibration = CalibrationSnapshot(
            timestamp="2026-07-30T12:00:00+00:00",
            total_picks_evaluated=22,
            segments_by_type={
                "ligas": {"FIFA World Cup": fifa_metric},
                "mercados": {},
                "ligas_mercados": {"FIFA World Cup::h2h": fifa_combo_metric},
                "tiers": {},
                "casas": {},
            },
            model_adjustments={"confidence_multipliers": {"model_general": 1.0}},
            alerts=[],
        )

        original_get_picks_for_date = audit_module.get_picks_for_date
        original_dashboard_data = audit_module.dashboard_data
        original_generate_calibration_snapshot = audit_module.generate_calibration_snapshot
        original_openai_available = audit_module.openai_available

        try:
            audit_module.get_picks_for_date = lambda target_date, db_path=None: {
                "date": "2026-07-30",
                "recommended": 1,
                "executed": 1,
                "closed": 1,
                "won": 1,
                "lost": 0,
                "total_staked": 5.0,
                "total_profit": 2.0,
                "roi_pct": 40.0,
                "hitrate": 100.0,
                "model_published": {
                    "today": {"published": 1, "pending": 0, "won": 1, "lost": 0, "push": 0, "roi": 40.0, "profit": 2.0, "hit_rate": 100.0},
                    "all_time": {"published": 10, "pending": 2, "won": 5, "lost": 3, "push": 0, "roi": 8.0, "profit": 6.0, "hit_rate": 62.5},
                },
                "picks_list": {"recommended": [], "executed": [], "closed": []},
            }
            audit_module.dashboard_data = lambda db_path=None: {"resumen": {"roi": 8.0, "hit_rate": 54.0}}
            audit_module.generate_calibration_snapshot = lambda: calibration
            audit_module.openai_available = lambda: False

            report = audit_module.generate_daily_audit_report(datetime(2026, 7, 30, tzinfo=timezone.utc))
        finally:
            audit_module.get_picks_for_date = original_get_picks_for_date
            audit_module.dashboard_data = original_dashboard_data
            audit_module.generate_calibration_snapshot = original_generate_calibration_snapshot
            audit_module.openai_available = original_openai_available

        joined_alerts = "\n".join(report["alerts"])
        self.assertNotIn("FIFA World Cup", joined_alerts)

    def test_auditoria_diaria_cuenta_solo_picks_apostadas_y_cerradas(self):
        import app.audit as audit_module
        from datetime import datetime, timezone

        recomendacion = {
            "event_id": "audit_exec_1",
            "commence_time": "2026-07-19T20:00:00Z",
            "sport_key": "soccer_spain_la_liga",
            "sport_label": "Futbol",
            "league_key": "la_liga",
            "league_label": "La Liga",
            "partido": "A vs B",
            "equipo": "A",
            "tipo_resultado": "home",
            "casa": "Pinnacle",
            "mercado": "h2h",
            "cuota_apuesta": 2.0,
            "importe_sugerido": 5.0,
            "stake": 1.0,
            "recomendacion": "Value",
            "motivo": "Test",
            "recommended_by_bot": True,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "tracker.sqlite3")
            pick = guardar_recomendaciones_unicas([recomendacion], db_path=db_path)[0]
            marcar_apuesta_real_pick(pick["id"], db_path=db_path)
            actualizar_resultado(pick["id"], "win", db_path=db_path)
            report = audit_module.get_picks_for_date(datetime.now(timezone.utc), db_path=db_path)

        self.assertEqual(report["recommended"], 1)
        self.assertEqual(report["executed"], 1)
        self.assertEqual(report["closed"], 1)
        self.assertEqual(report["won"], 1)
        self.assertEqual(report["model_published"]["today"]["published"], 0)

    def test_auditoria_diaria_modelo_cuenta_picks_publicadas_aunque_no_apostadas(self):
        import app.audit as audit_module
        from datetime import datetime, timezone

        recomendacion = {
            "event_id": "audit_pub_1",
            "commence_time": "2026-07-19T20:00:00Z",
            "sport_key": "basketball_wnba",
            "sport_label": "Baloncesto",
            "league_key": "wnba",
            "league_label": "WNBA",
            "partido": "Aces vs Liberty",
            "equipo": "Under",
            "tipo_resultado": "totals",
            "casa": "Pinnacle",
            "mercado": "totals",
            "cuota_apuesta": 1.95,
            "importe_sugerido": 5.0,
            "stake": 1.0,
            "recomendacion": "Value",
            "motivo": "Test",
            "recommended_by_bot": True,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "tracker.sqlite3")
            pick = guardar_recomendaciones_unicas([recomendacion], db_path=db_path)[0]
            registrar_publicacion_telegram(
                publication_type="manual",
                payload={"deporte": "Baloncesto"},
                items=[{"pick_id": pick["id"], "message_kind": "pick", "text": "Pick publicada", "telegram_message_id": 77}],
                db_path=db_path,
            )
            actualizar_resultado(pick["id"], "loss", db_path=db_path)
            report = audit_module.get_picks_for_date(datetime.now(timezone.utc), db_path=db_path)

        self.assertEqual(report["executed"], 0)
        self.assertEqual(report["model_published"]["today"]["published"], 1)
        self.assertEqual(report["model_published"]["today"]["lost"], 1)
        self.assertEqual(report["model_published"]["all_time"]["pending"], 0)

    def test_procesar_comando_resumen_envia_auditoria_por_telegram(self):
        import main

        original_telegram_config = main.telegram_config
        original_telegram_client = main.telegram_client
        original_construir_resumen = main.construir_resumen_telegram

        sent_messages: list[str] = []

        class DummyClient:
            def send_message(self, text, reply_markup=None):
                sent_messages.append(text)
                return {"ok": True}

        try:
            main.telegram_config = lambda: ("token-test", "chat-test")
            main.telegram_client = lambda token=None, chat_id=None: DummyClient()
            main.construir_resumen_telegram = lambda force_refresh_scores=True, lookback_hours=24, score_days=3: (
                "Resumen premium del modelo",
                {
                    "metrics": {"roi": 8.25},
                    "model_portfolio": {"all_time": {"published": 12}},
                },
            )

            response = procesar_comando_telegram("/resumen")
        finally:
            main.telegram_config = original_telegram_config
            main.telegram_client = original_telegram_client
            main.construir_resumen_telegram = original_construir_resumen

        self.assertEqual(sent_messages, ["Resumen premium del modelo"])
        self.assertIn("Resumen 24h enviado", response)
        self.assertIn("12 picks", response)

    def test_procesar_comando_mes_envia_auditoria_30_dias_por_telegram(self):
        import main

        original_telegram_config = main.telegram_config
        original_telegram_client = main.telegram_client
        original_construir_resumen = main.construir_resumen_telegram

        sent_messages: list[str] = []
        captured_args: list[dict[str, object]] = []

        class DummyClient:
            def send_message(self, text, reply_markup=None):
                sent_messages.append(text)
                return {"ok": True}

        try:
            main.telegram_config = lambda: ("token-test", "chat-test")
            main.telegram_client = lambda token=None, chat_id=None: DummyClient()

            def fake_construir_resumen(force_refresh_scores=True, lookback_hours=24, score_days=3):
                captured_args.append(
                    {
                        "force_refresh_scores": force_refresh_scores,
                        "lookback_hours": lookback_hours,
                        "score_days": score_days,
                    }
                )
                return (
                    "Resumen mensual premium",
                    {
                        "metrics": {"roi": 12.5},
                        "model_portfolio": {"all_time": {"published": 44}},
                    },
                )

            main.construir_resumen_telegram = fake_construir_resumen

            response = procesar_comando_telegram("/mes")
        finally:
            main.telegram_config = original_telegram_config
            main.telegram_client = original_telegram_client
            main.construir_resumen_telegram = original_construir_resumen

        self.assertEqual(sent_messages, ["Resumen mensual premium"])
        self.assertEqual(
            captured_args,
            [{"force_refresh_scores": True, "lookback_hours": 24 * 30, "score_days": 30}],
        )
        self.assertIn("Resumen 30 dias enviado", response)
        self.assertIn("44 picks", response)

    def test_procesar_comando_help_envia_lista_de_comandos(self):
        import main

        original_telegram_config = main.telegram_config
        original_telegram_client = main.telegram_client

        sent_messages: list[str] = []

        class DummyClient:
            def send_message(self, text, reply_markup=None):
                sent_messages.append(text)
                return {"ok": True}

        try:
            main.telegram_config = lambda: ("token-test", "chat-test")
            main.telegram_client = lambda token=None, chat_id=None: DummyClient()

            response = procesar_comando_telegram("/help")
        finally:
            main.telegram_config = original_telegram_config
            main.telegram_client = original_telegram_client

        self.assertEqual(len(sent_messages), 1)
        self.assertIn("/help", sent_messages[0])
        self.assertIn("/resumen", sent_messages[0])
        self.assertIn("/mes", sent_messages[0])
        self.assertIn("/panel", sent_messages[0])
        self.assertIn("/pendientes", sent_messages[0])
        self.assertIn("/ganadas", sent_messages[0])
        self.assertIn("/perdidas", sent_messages[0])
        self.assertIn("/apuestas", sent_messages[0])
        self.assertIn("Ayuda enviada", response)

    def test_procesar_comando_panel_envia_panel_reciente(self):
        import main

        original_telegram_config = main.telegram_config
        original_telegram_client = main.telegram_client
        original_build_panel = main.build_recent_form_panel
        original_format_panel = main.format_recent_form_panel_telegram

        sent_messages: list[str] = []

        class DummyClient:
            def send_message(self, text, reply_markup=None):
                sent_messages.append(text)
                return {"ok": True}

        try:
            main.telegram_config = lambda: ("token-test", "chat-test")
            main.telegram_client = lambda token=None, chat_id=None: DummyClient()
            main.build_recent_form_panel = lambda: {"total_evaluations": 27}
            main.format_recent_form_panel_telegram = lambda panel: "Panel reciente premium"

            response = procesar_comando_telegram("/panel")
        finally:
            main.telegram_config = original_telegram_config
            main.telegram_client = original_telegram_client
            main.build_recent_form_panel = original_build_panel
            main.format_recent_form_panel_telegram = original_format_panel

        self.assertEqual(sent_messages, ["Panel reciente premium"])
        self.assertIn("Panel enviado. Evaluaciones: 27.", response)

    def test_procesar_comando_pendientes_envia_apuestas_reales_con_botones(self):
        import main

        original_telegram_config = main.telegram_config
        original_telegram_client = main.telegram_client
        original_real_bets_pending = main._real_bets_pending

        sent_messages: list[dict[str, object]] = []

        class DummyClient:
            def send_message(self, text, reply_markup=None):
                sent_messages.append({"text": text, "reply_markup": reply_markup})
                return {"ok": True}

        try:
            main.telegram_config = lambda: ("token-test", "chat-test")
            main.telegram_client = lambda token=None, chat_id=None: DummyClient()
            main._real_bets_pending = lambda limit=10: [
                {
                    "id": 101,
                    "partido": "Alex de Minaur vs Ben Shelton",
                    "equipo": "Alex de Minaur",
                    "league_label": "ATP Washington Open",
                    "mercado": "h2h",
                    "cuota": 2.1,
                    "stake": 1.7,
                    "importe_sugerido": 3.7,
                    "estado": "pendiente",
                }
            ]

            response = procesar_comando_telegram("/pendientes")
        finally:
            main.telegram_config = original_telegram_config
            main.telegram_client = original_telegram_client
            main._real_bets_pending = original_real_bets_pending

        self.assertEqual(len(sent_messages), 2)
        self.assertIn("Apuestas pendientes", str(sent_messages[0]["text"]))
        self.assertIn("Alex de Minaur vs Ben Shelton", str(sent_messages[1]["text"]))
        keyboard = sent_messages[1]["reply_markup"]
        self.assertIsInstance(keyboard, dict)
        self.assertIn("inline_keyboard", keyboard)
        self.assertIn("win", str(keyboard))
        self.assertIn("loss", str(keyboard))
        self.assertIn("push", str(keyboard))
        self.assertIn("Pendientes enviado. 1 apuestas.", response)

    def test_procesar_comando_ganadas_envia_historico_reciente(self):
        import main

        original_telegram_config = main.telegram_config
        original_telegram_client = main.telegram_client
        original_real_bets_by_result = main._real_bets_by_result

        sent_messages: list[str] = []

        class DummyClient:
            def send_message(self, text, reply_markup=None):
                sent_messages.append(text)
                return {"ok": True}

        try:
            main.telegram_config = lambda: ("token-test", "chat-test")
            main.telegram_client = lambda token=None, chat_id=None: DummyClient()
            main._real_bets_by_result = lambda result, limit=10: [
                {
                    "id": 55,
                    "partido": "Brandon Nakashima vs Jakub Mensik",
                    "equipo": "Brandon Nakashima",
                    "league_label": "ATP Washington Open",
                    "mercado": "h2h",
                    "cuota": 2.45,
                    "stake": 2,
                    "importe_sugerido": 5.0,
                    "estado": "cerrada",
                    "resultado": "win",
                    "profit_loss": 7.25,
                }
            ]

            response = procesar_comando_telegram("/ganadas")
        finally:
            main.telegram_config = original_telegram_config
            main.telegram_client = original_telegram_client
            main._real_bets_by_result = original_real_bets_by_result

        self.assertEqual(len(sent_messages), 1)
        self.assertIn("Ultimas ganadas", sent_messages[0])
        self.assertIn("Brandon Nakashima vs Jakub Mensik", sent_messages[0])
        self.assertIn("Resultado", sent_messages[0])
        self.assertIn("Ganadas enviado. 1 apuestas.", response)

    def test_procesar_comando_perdidas_envia_historico_reciente(self):
        import main

        original_telegram_config = main.telegram_config
        original_telegram_client = main.telegram_client
        original_real_bets_by_result = main._real_bets_by_result

        sent_messages: list[str] = []

        class DummyClient:
            def send_message(self, text, reply_markup=None):
                sent_messages.append(text)
                return {"ok": True}

        try:
            main.telegram_config = lambda: ("token-test", "chat-test")
            main.telegram_client = lambda token=None, chat_id=None: DummyClient()
            main._real_bets_by_result = lambda result, limit=10: [
                {
                    "id": 56,
                    "partido": "Taylor Fritz vs Kamil Majchrzak",
                    "equipo": "Taylor Fritz",
                    "league_label": "ATP Washington Open",
                    "mercado": "h2h",
                    "cuota": 1.85,
                    "stake": 2,
                    "importe_sugerido": 4.0,
                    "estado": "cerrada",
                    "resultado": "loss",
                    "profit_loss": -4.0,
                }
            ]

            response = procesar_comando_telegram("/perdidas")
        finally:
            main.telegram_config = original_telegram_config
            main.telegram_client = original_telegram_client
            main._real_bets_by_result = original_real_bets_by_result

        self.assertEqual(len(sent_messages), 1)
        self.assertIn("Ultimas perdidas", sent_messages[0])
        self.assertIn("Taylor Fritz vs Kamil Majchrzak", sent_messages[0])
        self.assertIn("Resultado", sent_messages[0])
        self.assertIn("Perdidas enviado. 1 apuestas.", response)

    def test_procesar_comando_apuestas_lanza_job_y_notifica(self):
        import main

        original_telegram_config = main.telegram_config
        original_telegram_client = main.telegram_client
        original_lanzar_apuestas = main.lanzar_apuestas_telegram_async

        sent_messages: list[str] = []

        class DummyClient:
            def send_message(self, text, reply_markup=None):
                sent_messages.append(text)
                return {"ok": True}

        try:
            main.telegram_config = lambda: ("token-test", "chat-test")
            main.telegram_client = lambda token=None, chat_id=None: DummyClient()
            main.lanzar_apuestas_telegram_async = lambda: "job123abc"

            response = procesar_comando_telegram("/apuestas")
        finally:
            main.telegram_config = original_telegram_config
            main.telegram_client = original_telegram_client
            main.lanzar_apuestas_telegram_async = original_lanzar_apuestas

        self.assertEqual(len(sent_messages), 1)
        self.assertIn("/apuestas en marcha", sent_messages[0])
        self.assertIn("job123abc", sent_messages[0])
        self.assertIn("/apuestas lanzado", response)

    def test_lanzar_apuestas_async_usa_preset_lab_y_envia_resumen_final(self):
        import main

        original_telegram_config = main.telegram_config
        original_telegram_client = main.telegram_client
        original_publicar_pronosticos_lab = main.publicar_pronosticos_lab
        original_thread = main.threading.Thread

        sent_messages: list[str] = []
        published_calls: list[dict[str, object]] = []

        class DummyClient:
            def send_message(self, text, reply_markup=None):
                sent_messages.append(text)
                return {"ok": True}

        class ImmediateThread:
            def __init__(self, target=None, daemon=None):
                self._target = target

            def start(self):
                if self._target is not None:
                    self._target()

        try:
            main.telegram_config = lambda: ("token-test", "chat-test")
            main.telegram_client = lambda token=None, chat_id=None: DummyClient()

            def fake_publicar_pronosticos_lab(**kwargs):
                published_calls.append(kwargs)
                return {
                    "ok": True,
                    "picks_guardados": 3,
                    "mensajes_enviados": 4,
                    "publication_id": 77,
                }

            main.publicar_pronosticos_lab = fake_publicar_pronosticos_lab
            main.threading.Thread = ImmediateThread

            job_id = main.lanzar_apuestas_telegram_async()
        finally:
            main.telegram_config = original_telegram_config
            main.telegram_client = original_telegram_client
            main.publicar_pronosticos_lab = original_publicar_pronosticos_lab
            main.threading.Thread = original_thread

        self.assertTrue(job_id)
        self.assertEqual(len(published_calls), 1)
        self.assertEqual(
            published_calls[0],
            {
                "bankroll": 200.0,
                "perfil": "agresivo",
                "modo": "comparador",
                "mercados": "todo",
                "partido": "todos",
                "deporte": "todo",
                "solo_stakazos": False,
            },
        )
        self.assertEqual(len(sent_messages), 1)
        self.assertIn("/apuestas completado", sent_messages[0])
        self.assertIn("Picks publicadas: <b>3</b>", sent_messages[0])

    def test_resumen_telegram_muestra_publicado_hoy_con_picks_del_dia(self):
        report = {
            "date": "2026-07-30",
            "status": "✅ VERDE",
            "status_detail": "Dia positivo. Modelo funcionando bien.",
            "model_portfolio": {
                "today": {"published": 4, "closed": 2, "pending": 2, "won": 2, "lost": 0, "push": 0, "roi": 37.98, "hit_rate": 100.0},
                "all_time": {"published": 89, "closed": 32, "pending": 57, "won": 5, "lost": 8, "push": 19, "roi": 0.0, "hit_rate": 0.0},
            },
            "picks": {"recommended": 4, "executed": 2, "closed": 1, "won": 1, "lost": 0},
            "metrics": {"profit": 8.7, "roi": 63.97, "hitrate": 100.0},
            "vs_historical": {"roi_delta": 57.08, "hitrate_delta": 66.0},
            "calibration": {"total_picks_evaluated": 13, "model_confidence": 1.1},
            "latest_publications": [
                {
                    "id": 68,
                    "created_at": "2026-07-30T17:30:00+00:00",
                    "total_picks": 1,
                    "won": 0,
                    "lost": 0,
                    "push": 0,
                    "pending": 1,
                    "picks_preview": ["Ugo Humbert vs Ben Shelton | Ben Shelton | pendiente"],
                    "picks_preview_items": [
                        {"match_label": "Ugo Humbert vs Ben Shelton", "team_label": "Ben Shelton", "outcome": "pendiente"},
                    ],
                }
            ],
            "daily_publications": [
                {
                    "id": 65,
                    "created_at": "2026-07-30T10:00:00+00:00",
                    "picks_preview_items": [
                        {"match_label": "Brandon Nakashima vs Jakub Mensik", "team_label": "Brandon Nakashima", "outcome": "ganada", "was_bet": True},
                        {"match_label": "Terence Atmane vs Alejandro Tabilo", "team_label": "Alejandro Tabilo", "outcome": "pendiente"},
                        {"match_label": "Alex de Minaur vs Cruz Hewitt", "team_label": "Alex de Minaur", "outcome": "ganada", "was_bet": True},
                    ],
                },
                {
                    "id": 68,
                    "created_at": "2026-07-30T17:30:00+00:00",
                    "picks_preview_items": [
                        {"match_label": "Ugo Humbert vs Ben Shelton", "team_label": "Ben Shelton", "outcome": "pendiente"},
                    ],
                },
            ],
            "alerts": ["⚠️ Solo 50% de picks se ejecutaron."],
            "ai_insights": None,
        }

        text = format_audit_report_telegram(report)

        self.assertIn("🗓️ Publicado hoy | 4 picks | ✅2 ❌0 ➖0 | 2 pend", text)
        self.assertIn("✅💵 Brandon Nakashima vs Jakub Mensik | Brandon Nakashima", text)
        self.assertIn("⏳ Terence Atmane vs Alejandro Tabilo | Alejandro Tabilo", text)
        self.assertIn("✅💵 Alex de Minaur vs Cruz Hewitt | Alex de Minaur", text)
        self.assertIn("⏳ Ugo Humbert vs Ben Shelton | Ben Shelton", text)
        self.assertIn("🧾 Última pub #68 | 1 picks | ✅0 ❌0 ➖0 | 1 pend", text)

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

    def test_obtener_closing_odds_pick_desde_snapshots(self):
        recomendacion = {
            "event_id": "evt_closing",
            "commence_time": "2026-07-20T20:00:00Z",
            "partido": "Spain vs France",
            "equipo": "Spain",
            "equipo_raw": "Spain",
            "tipo_resultado": "home",
            "tipo_resultado_raw": "home",
            "casa": "Pinnacle",
            "mercado": "h2h",
            "cuota_apuesta": 2.00,
            "importe_sugerido": 10,
            "stake": 2,
            "recomendacion": "Value moderado",
            "motivo": "test",
        }
        partido = [
            {
                "id": "evt_closing",
                "commence_time": "2026-07-20T20:00:00Z",
                "sport_key": "soccer_fifa_world_cup",
                "sport_label": "Futbol",
                "league_key": "fifa_world_cup",
                "league_label": "FIFA World Cup",
                "home_team": "Spain",
                "away_team": "France",
                "bookmakers": [
                    {
                        "title": "Pinnacle",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Spain", "price": 1.91},
                                    {"name": "Draw", "price": 3.3},
                                    {"name": "France", "price": 4.0},
                                ],
                            }
                        ],
                    }
                ],
            }
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.sqlite3")
            pick = guardar_recomendaciones_unicas([recomendacion], db_path=db_path)[0]
            guardar_snapshot_cuotas(partido, db_path=db_path)

            closing = obtener_closing_odds_pick(pick["id"], db_path=db_path)

            self.assertEqual(closing, 1.91)

    def test_liquidacion_auto_solo_evalua_picks_recomendadas_por_el_bot(self):
        recomendada = {
            "event_id": "evt_eval_yes",
            "commence_time": "2026-07-20T20:00:00Z",
            "partido": "Spain vs France",
            "equipo": "Spain",
            "equipo_raw": "Spain",
            "tipo_resultado": "home",
            "tipo_resultado_raw": "home",
            "casa": "Pinnacle",
            "mercado": "h2h",
            "cuota_apuesta": 2.00,
            "importe_sugerido": 10,
            "stake": 2,
            "recomendacion": "Value moderado",
            "motivo": "test",
        }
        no_recomendada = {
            **recomendada,
            "event_id": "evt_eval_no",
            "partido": "Brazil vs Norway",
            "equipo": "Brazil",
            "equipo_raw": "Brazil",
            "recommended_by_bot": False,
            "auto_eval_eligible": False,
        }
        partidos = [
            {
                "id": "evt_eval_yes",
                "commence_time": "2026-07-20T20:00:00Z",
                "home_team": "Spain",
                "away_team": "France",
                "bookmakers": [
                    {
                        "title": "Pinnacle",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Spain", "price": 1.88},
                                    {"name": "Draw", "price": 3.4},
                                    {"name": "France", "price": 4.1},
                                ],
                            }
                        ],
                    }
                ],
            },
            {
                "id": "evt_eval_no",
                "commence_time": "2026-07-20T21:00:00Z",
                "home_team": "Brazil",
                "away_team": "Norway",
                "bookmakers": [
                    {
                        "title": "Pinnacle",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Brazil", "price": 1.72},
                                    {"name": "Draw", "price": 3.6},
                                    {"name": "Norway", "price": 5.0},
                                ],
                            }
                        ],
                    }
                ],
            },
        ]
        scores = [
            {
                "id": "evt_eval_yes",
                "completed": True,
                "home_team": "Spain",
                "away_team": "France",
                "scores": [
                    {"name": "Spain", "score": "1"},
                    {"name": "France", "score": "0"},
                ],
            },
            {
                "id": "evt_eval_no",
                "completed": True,
                "home_team": "Brazil",
                "away_team": "Norway",
                "scores": [
                    {"name": "Brazil", "score": "2"},
                    {"name": "Norway", "score": "0"},
                ],
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.sqlite3")
            picks = guardar_recomendaciones_unicas([recomendada, no_recomendada], db_path=db_path)
            guardar_snapshot_cuotas(partidos, db_path=db_path)
            resultado = liquidar_picks_con_scores(scores, db_path=db_path)
            evaluaciones = listar_evaluaciones_picks(db_path=db_path)
            todos = listar_picks(db_path=db_path)
            pick_yes = next(p for p in todos if p["event_id"] == "evt_eval_yes")
            pick_no = next(p for p in todos if p["event_id"] == "evt_eval_no")

            self.assertEqual(len(picks), 2)
            self.assertEqual(resultado["scope"], "solo_picks_recomendadas_por_el_bot")
            self.assertEqual(resultado["liquidados"], 1)
            self.assertEqual(pick_yes["estado"], "cerrada")
            self.assertEqual(pick_no["estado"], "pendiente")
            self.assertEqual(len(evaluaciones), 1)
            self.assertEqual(evaluaciones[0]["event_id"], "evt_eval_yes")
            self.assertEqual(evaluaciones[0]["closing_odds"], 1.88)

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
        self.assertEqual(favorita["modelo_mercado"], "Tenis singles fast conservador")

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

    def test_basket_totals_wnba_usa_un_baseline_distinto(self):
        prob_wnba, etiqueta_wnba = ajustar_probabilidad_por_mercado(
            market_key="totals",
            nombre="Over",
            point=166.5,
            description=None,
            prob_mercado=0.50,
            home="Liberty",
            away="Aces",
            elos={},
            sport_key="basketball_wnba",
        )
        prob_nba, etiqueta_nba = ajustar_probabilidad_por_mercado(
            market_key="totals",
            nombre="Over",
            point=166.5,
            description=None,
            prob_mercado=0.50,
            home="Lakers",
            away="Celtics",
            elos={},
            sport_key="basketball_nba",
        )

        self.assertEqual(etiqueta_wnba, "WNBA total baseline")
        self.assertEqual(etiqueta_nba, "Basket total baseline")
        self.assertLess(prob_wnba, prob_nba)

    def test_calibration_generates_league_market_penalty_for_bad_wnba_totals(self):
        bad_combo = SegmentMetrics(
            segment_name="WNBA::totals",
            segment_type="liga_mercado",
            total_picks=16,
            total_recommended=16,
            picks_closed=16,
            picks_won=5,
            picks_lost=11,
            picks_push=0,
            total_staked=16.0,
            total_profit=-5.2,
            roi=-32.5,
            hit_rate=31.25,
            clv=-4.0,
            clv_positive_count=4,
            confidence_score=0.19,
            last_pick_date="2026-07-16T12:00:00Z",
            min_sample_warning=False,
            trend="weak",
            recommendation="penalizar",
        )
        solid_tier = SegmentMetrics(
            segment_name="elite",
            segment_type="tier",
            total_picks=20,
            total_recommended=20,
            picks_closed=20,
            picks_won=12,
            picks_lost=8,
            picks_push=0,
            total_staked=20.0,
            total_profit=2.0,
            roi=10.0,
            hit_rate=60.0,
            clv=2.0,
            clv_positive_count=12,
            confidence_score=0.72,
            last_pick_date="2026-07-16T12:00:00Z",
            min_sample_warning=False,
            trend="strong",
            recommendation="confiable",
        )

        adjustments = _generate_model_adjustments(
            {
                "ligas": {},
                "mercados": {},
                "ligas_mercados": {"WNBA::totals": bad_combo},
                "tiers": {"elite": solid_tier},
                "casas": {},
            }
        )

        self.assertGreater(adjustments["league_market_penalties"]["WNBA::totals"], 0)
        self.assertGreater(adjustments["league_market_thresholds"]["WNBA::totals"], 0)

    def test_calibration_generates_sport_and_bookmaker_penalties(self):
        bad_sport = SegmentMetrics(
            segment_name="Baloncesto",
            segment_type="deporte",
            total_picks=18,
            total_recommended=18,
            picks_closed=18,
            picks_won=4,
            picks_lost=14,
            picks_push=0,
            total_staked=18.0,
            total_profit=-7.0,
            roi=-38.89,
            hit_rate=22.22,
            clv=-3.0,
            clv_positive_count=3,
            confidence_score=0.15,
            last_pick_date="2026-07-16T12:00:00Z",
            min_sample_warning=False,
            trend="weak",
            recommendation="penalizar",
        )
        bad_bookmaker = SegmentMetrics(
            segment_name="BookieX",
            segment_type="casa",
            total_picks=16,
            total_recommended=16,
            picks_closed=16,
            picks_won=5,
            picks_lost=11,
            picks_push=0,
            total_staked=16.0,
            total_profit=-4.2,
            roi=-26.25,
            hit_rate=31.25,
            clv=-4.5,
            clv_positive_count=2,
            confidence_score=0.18,
            last_pick_date="2026-07-16T12:00:00Z",
            min_sample_warning=False,
            trend="weak",
            recommendation="penalizar",
        )

        adjustments = _generate_model_adjustments(
            {
                "deportes": {"Baloncesto": bad_sport},
                "ligas": {},
                "mercados": {},
                "ligas_mercados": {},
                "tiers": {},
                "casas": {"BookieX": bad_bookmaker},
            }
        )

        self.assertGreater(adjustments["sport_penalties"]["Baloncesto"], 0)
        self.assertGreater(adjustments["bookmaker_penalties"]["BookieX"], 0)

    def test_build_training_dataset_enlaza_snapshots_y_resultado(self):
        recomendacion = {
            "event_id": "train_evt_1",
            "commence_time": "2026-07-30T20:00:00Z",
            "sport_key": "basketball_wnba",
            "sport_label": "Baloncesto",
            "league_key": "wnba",
            "league_label": "WNBA",
            "partido": "Aces vs Liberty",
            "equipo": "Under",
            "equipo_raw": "Under",
            "tipo_resultado": "totals",
            "tipo_resultado_raw": "totals",
            "casa": "Pinnacle",
            "mercado": "totals",
            "outcome_point": 166.5,
            "cuota_apuesta": 1.95,
            "importe_sugerido": 5.0,
            "stake": 1.0,
            "recomendacion": "Value",
            "motivo": "Test",
            "recommended_by_bot": True,
        }
        snapshot_event = {
            "id": "train_evt_1",
            "commence_time": "2026-07-30T20:00:00Z",
            "sport_key": "basketball_wnba",
            "sport_label": "Baloncesto",
            "league_key": "wnba",
            "league_label": "WNBA",
            "home_team": "Aces",
            "away_team": "Liberty",
            "bookmakers": [
                {
                    "title": "Pinnacle",
                    "markets": [
                        {
                            "key": "totals",
                            "outcomes": [
                                {"name": "Over", "price": 1.87, "point": 166.5},
                                {"name": "Under", "price": 1.91, "point": 166.5},
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
                                {"name": "Over", "price": 1.88, "point": 166.5},
                                {"name": "Under", "price": 1.90, "point": 166.5},
                            ],
                        }
                    ],
                },
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "tracker.sqlite3")
            pick = guardar_recomendaciones_unicas([recomendacion], db_path=db_path)[0]
            guardar_snapshot_cuotas([snapshot_event], db_path=db_path)
            actualizar_resultado(pick["id"], "win", db_path=db_path)

            dataset = build_training_dataset(db_path=db_path, limit=100)

        self.assertEqual(len(dataset), 1)
        self.assertEqual(dataset[0]["sport_label"], "Baloncesto")
        self.assertEqual(dataset[0]["mercado"], "totals")
        self.assertEqual(dataset[0]["casa"], "Pinnacle")
        self.assertEqual(dataset[0]["resultado"], "win")
        self.assertEqual(dataset[0]["snapshot_support_bookmakers"], 2)
        self.assertEqual(dataset[0]["snapshot_rows"], 2)
        self.assertAlmostEqual(float(dataset[0]["closing_odds"]), 1.91, places=2)

    def test_calibration_metadata_includes_league_market_adjustments(self):
        snapshot = CalibrationSnapshot(
            timestamp="2026-07-17T10:00:00Z",
            total_picks_evaluated=24,
            segments_by_type={},
            model_adjustments={
                "league_penalties": {},
                "sport_penalties": {"Baloncesto": 0.12},
                "market_thresholds": {"totals": 0.08},
                "league_market_penalties": {"WNBA::totals": 0.22},
                "league_market_thresholds": {"WNBA::totals": 0.18},
                "bookmaker_penalties": {"Pinnacle": 0.06},
                "tier_boosts": {},
                "confidence_multipliers": {"model_general": 1.0},
                "training_dataset": {"samples": 44},
            },
            alerts=[],
        )

        calibrated_scoring.clear_calibration_cache()
        with patch("app.calibrated_scoring.generate_calibration_snapshot", return_value=snapshot):
            metadata = calibrated_scoring.get_calibration_metadata(
                {
                    "sport_label": "Baloncesto",
                    "league_label": "WNBA",
                    "mercado": "totals",
                    "casa": "Pinnacle",
                    "elite_tier": "premium",
                }
            )

        self.assertEqual(metadata["sport_penalty_factor"], 0.88)
        self.assertEqual(metadata["league_market_penalty_factor"], 0.78)
        self.assertEqual(metadata["league_market_threshold_adjustment"], 0.18)
        self.assertEqual(metadata["bookmaker_penalty_factor"], 0.94)
        self.assertEqual(metadata["training_samples"], 44)

    def test_publication_guard_en_shadow_mode_bloquea_publicacion_real(self):
        guard = publication_guard_state(
            runtime_settings=RuntimeSettings(environment="development", shadow_mode=True),
            load_stats=lambda: {"picks_cerrados": 999, "roi": 12.0, "hit_rate": 60.0, "clv_medio": 1.4},
            load_learning=lambda: {"porcentaje_clv_positivo": 66.0, "picks_evaluadas": 150, "picks_con_clv": 120},
        )

        self.assertFalse(guard["allow_live_publication"])
        self.assertEqual(guard["mode"], "shadow")
        self.assertIn("shadow_mode_activo", guard["reasons"])

    def test_publish_telegram_predictions_shadow_mode_registra_preview(self):
        sent_messages: list[dict] = []
        publications: list[dict] = []

        result = publish_telegram_predictions(
            runtime_settings=RuntimeSettings(environment="development", shadow_mode=True),
            publication_guard=lambda: {"allow_live_publication": False, "mode": "shadow", "reasons": ["shadow_mode_activo"]},
            pronosticos_fn=lambda **kwargs: {
                "pronosticos": [
                    {
                        "event_id": "evt_shadow",
                        "mercado": "h2h",
                        "tipo_resultado": "home",
                        "equipo": "Spain",
                        "casa": "Pinnacle",
                        "id": 7,
                    }
                ],
                "total_elite": 1,
                "total_stakazos": 0,
                "deporte": "Futbol",
                "liga": "La Liga",
            },
            save_unique_recommendations=lambda picks: [{"id": 7, **picks[0]}],
            read_raw_pick=lambda pick: {},
            enrich_with_ai=lambda picks: picks,
            build_ai_summary=lambda *args, **kwargs: None,
            ai_available=lambda: False,
            format_summary=lambda **kwargs: "Resumen",
            format_pick_message=lambda pick: f"Pick {pick['equipo']}",
            telegram_keyboard_for_pick=lambda pick_id: {"inline_keyboard": []},
            send_message=lambda *args, **kwargs: sent_messages.append({"args": args, "kwargs": kwargs}) or {"result": {"message_id": 101}},
            register_publication=lambda **kwargs: publications.append(kwargs) or {"id": 55},
            perfil_label=lambda perfil: perfil or "moderado",
            modo_label=lambda modo: modo or "comparador",
            perfiles_stake={"moderado"},
            modos_informe={"comparador"},
            bankroll=None,
            perfil="moderado",
            modo="comparador",
            mercados="todo",
            partido="todos",
            deporte="worldcup",
            solo_stakazos=False,
            token="token",
            chat_id="chat",
            publication_type="manual",
        )

        self.assertEqual(result["runtime_mode"], "shadow")
        self.assertEqual(result["mensajes_enviados"], 0)
        self.assertEqual(result["shadow_messages"], ["Resumen", "Pick Spain"])
        self.assertEqual(sent_messages, [])
        self.assertEqual(publications[0]["publication_type"], "manual_shadow")
        self.assertEqual(publications[0]["items"][1]["pick_id"], 7)

    def test_publicar_pronosticos_lab_envia_telegram_y_registra_cartera(self):
        with (
            patch("main.telegram_config", return_value=("token_test", "chat_test")),
            patch("main.publish_telegram_predictions", return_value={"publication_id": 88, "picks_guardados": 3, "mensajes_enviados": 4}) as publish_mock,
        ):
            result = publicar_pronosticos_lab(
                bankroll=200.0,
                perfil="agresivo",
                modo="comparador",
                mercados="resultado",
                partido="todos",
                deporte="tenis",
                solo_stakazos=False,
            )

        self.assertEqual(result["publication_id"], 88)
        self.assertEqual(result["picks_guardados"], 3)
        self.assertEqual(result["mensajes_enviados"], 4)
        kwargs = publish_mock.call_args.kwargs
        self.assertEqual(kwargs["publication_type"], "lab")
        self.assertFalse(kwargs["runtime_settings"].shadow_mode)
        self.assertEqual(kwargs["token"], "token_test")
        self.assertEqual(kwargs["chat_id"], "chat_test")
        self.assertTrue(kwargs["publication_guard"]()["allow_live_publication"])

    def test_build_performance_guard_y_bloqueo_por_liga(self):
        guard = build_performance_guard(
            load_dashboard=lambda: {
                "por_deporte": [],
                "por_liga": [
                    {
                        "nombre": "WNBA",
                        "cerradas": 20,
                        "roi": -15.0,
                        "hit_rate": 35.0,
                        "clv_positivo_pct": 30.0,
                    }
                ],
            }
        )
        adjusted = apply_performance_guard_to_pick(
            {
                "league_label": "WNBA",
                "sport_label": "Baloncesto",
                "stake": 2,
                "importe_sugerido": 5,
                "stake_pct_bankroll": 1.5,
                "kelly_fraccional": 0.01,
                "recomendacion": "Value",
                "motivo": "test",
            },
            guard,
        )

        self.assertIn("WNBA", guard["blocked_leagues"])
        self.assertTrue(adjusted["performance_guard_blocked"])
        self.assertEqual(adjusted["stake"], 0)
        self.assertEqual(adjusted["recomendacion"], "No apostar")


    def test_build_performance_guard_permita_baloncesto_por_override(self):
        with patch.dict(os.environ, {"PERF_GUARD_ALLOW_SPORTS": "Baloncesto"}, clear=False):
            guard = build_performance_guard(
                load_dashboard=lambda: {
                    "por_deporte": [
                        {
                            "nombre": "Baloncesto",
                            "cerradas": 20,
                            "roi": -12.0,
                            "hit_rate": 31.0,
                            "clv_positivo_pct": 28.0,
                        }
                    ],
                    "por_liga": [],
                }
            )

        adjusted = apply_performance_guard_to_pick(
            {
                "league_label": "WNBA",
                "sport_label": "Baloncesto",
                "stake": 2,
                "importe_sugerido": 5,
                "stake_pct_bankroll": 1.5,
                "kelly_fraccional": 0.01,
                "recomendacion": "Value",
                "motivo": "test",
            },
            guard,
        )

        self.assertNotIn("Baloncesto", guard["blocked_sports"])
        self.assertIn("baloncesto", guard["overrides"]["allowed_sports"])
        self.assertIn("Baloncesto", guard["overrides"]["unblocked_sports"])
        self.assertFalse(adjusted["performance_guard_blocked"])
        self.assertEqual(adjusted["stake"], 2)

    def test_resolver_contexto_deporte_generico_prefiere_liga_activa(self):
        import main

        fake_catalog = {
            "sports": [
                {
                    "sport_key": "basketball_wnba",
                    "sport_label": "Baloncesto",
                    "league_key": "wnba",
                    "league_label": "Wnba",
                    "active": True,
                },
                {
                    "sport_key": "basketball_nba_championship_winner",
                    "sport_label": "Baloncesto",
                    "league_key": "nba_championship_winner",
                    "league_label": "Nba Championship Winner",
                    "active": True,
                },
                {
                    "sport_key": "soccer_argentina_primera_division",
                    "sport_label": "Futbol",
                    "league_key": "argentina_primera_division",
                    "league_label": "Argentina Primera Division",
                    "active": True,
                },
            ]
        }

        with patch.object(main, "discover_available_catalog", return_value=fake_catalog):
            basketball = resolver_contexto_deporte("baloncesto")
            football = resolver_contexto_deporte("futbol")

        self.assertEqual(basketball["catalog_key"], "baloncesto")
        self.assertEqual(basketball["sport_key"], "basketball_wnba")
        self.assertEqual(basketball["league_label"], "Wnba")
        self.assertEqual(football["catalog_key"], "futbol")
        self.assertEqual(football["sport_key"], "soccer_argentina_primera_division")

    def test_build_lab_run_resume_decision_y_bloqueos(self):
        result = build_lab_run(
            runtime_settings=RuntimeSettings(environment="development", shadow_mode=True),
            publication_guard=lambda: {"allow_live_publication": False, "mode": "shadow", "reasons": ["shadow_mode_activo"]},
            run_forecast=lambda request: {
                "sport_label": "Baloncesto",
                "league_label": "WNBA",
                "total_analizadas": 8,
                "total_recomendadas": 1,
                "mejores_apuestas": [
                    {
                        "event_id": "evt_ok",
                        "sport_label": "Baloncesto",
                        "league_label": "WNBA",
                        "partido": "Aces vs Liberty",
                        "equipo": "Under 166.5",
                        "mercado": "totals",
                        "casa": "Pinnacle",
                        "stake": 1,
                        "importe_sugerido": 3.0,
                        "recomendacion": "Value",
                        "motivo": "test",
                    }
                ],
                "descartadas": [
                    {
                        "event_id": "evt_blocked",
                        "sport_label": "Baloncesto",
                        "league_label": "WNBA",
                        "partido": "Fever vs Storm",
                        "equipo": "Over 177.5",
                        "mercado": "totals",
                        "casa": "Pinnacle",
                        "stake": 0,
                        "importe_sugerido": 0,
                        "recomendacion": "No apostar",
                        "motivo": "guard",
                        "performance_guard_blocked": True,
                        "performance_guard_reason": "Liga bloqueada",
                    }
                ],
            },
            build_prediction_payload=lambda **kwargs: {
                "pronosticos": [
                    {
                        "event_id": "evt_ok",
                        "sport_label": "Baloncesto",
                        "league_label": "WNBA",
                        "partido": "Aces vs Liberty",
                        "equipo": "Under 166.5",
                        "mercado": "totals",
                        "casa": "Pinnacle",
                        "stake": 1,
                        "importe_sugerido": 3.0,
                        "recomendacion": "Value",
                        "motivo": "test",
                    }
                ]
            },
            ai_available=lambda: False,
            select_picks_for_telegram=lambda *args, **kwargs: [],
            enrich_with_ai=lambda picks: picks,
            build_ai_summary=lambda *args, **kwargs: None,
            format_pick_message=lambda pick: "pick",
            format_summary_message=lambda **kwargs: "summary",
            fetch_scores=lambda days_from, deporte=None: [],
            perfil="moderado",
            modo="comparador",
            mercados="todo",
            partido="todos",
            deporte="baloncesto",
            bankroll=None,
            solo_stakazos=False,
            perfiles_stake={"moderado"},
            modos_informe={"comparador"},
            perfil_label=lambda value: value or "moderado",
            modo_label=lambda value: value or "comparador",
        )

        self.assertEqual(result["publication_decision"]["runtime_mode"], "shadow")
        self.assertFalse(result["publication_decision"]["would_publish_live"])
        self.assertEqual(result["forecast_summary"]["total_publicables_preview"], 1)
        self.assertEqual(result["forecast_summary"]["total_bloqueadas_en_descartadas"], 1)
        self.assertEqual(result["publishable_preview"][0]["event_id"], "evt_ok")
        self.assertEqual(result["blocked_picks"]["discarded"][0]["event_id"], "evt_blocked")

    def test_build_lab_run_historico_desactiva_publicacion(self):
        captured = {}

        def fake_run_forecast(request):
            captured["request"] = request
            return {
                "sport_label": "Tenis",
                "league_label": "ATP",
                "proveedor_cuotas": "the_odds_api",
                "historical_snapshot_at": "2026-08-01T10:00:00Z",
                "historical_market_notice": "Modo historico: featured only",
                "snapshots_guardados": 12,
                "total_analizadas": 2,
                "total_recomendadas": 1,
                "mejores_apuestas": [],
                "descartadas": [],
            }

        result = build_lab_run(
            runtime_settings=RuntimeSettings(environment="development", shadow_mode=False),
            publication_guard=lambda: {"allow_live_publication": True, "mode": "live", "reasons": []},
            run_forecast=fake_run_forecast,
            build_prediction_payload=lambda **kwargs: {"pronosticos": []},
            ai_available=lambda: False,
            select_picks_for_telegram=lambda *args, **kwargs: [],
            enrich_with_ai=lambda picks: picks,
            build_ai_summary=lambda *args, **kwargs: None,
            format_pick_message=lambda pick: "pick",
            format_summary_message=lambda **kwargs: "summary",
            fetch_scores=lambda days_from, deporte=None: [],
            perfil="agresivo",
            modo="comparador",
            mercados="todo",
            partido="todos",
            deporte="tenis",
            bankroll=None,
            solo_stakazos=False,
            perfiles_stake={"agresivo"},
            modos_informe={"comparador"},
            perfil_label=lambda value: value or "agresivo",
            modo_label=lambda value: value or "comparador",
            simulation_mode="historical",
            historical_snapshot_at="2026-08-01T10:00:00Z",
        )

        self.assertTrue(captured["request"].historical_mode)
        self.assertEqual(captured["request"].historical_date, "2026-08-01T10:00:00Z")
        self.assertFalse(result["publication_decision"]["would_publish_live"])
        self.assertTrue(result["simulation_context"]["historical_mode"])
        self.assertEqual(result["simulation_context"]["snapshot_at"], "2026-08-01T10:00:00Z")

    def test_build_lab_run_historico_filtra_publicables_por_rango(self):
        result = build_lab_run(
            runtime_settings=RuntimeSettings(environment="development", shadow_mode=False),
            publication_guard=lambda: {"allow_live_publication": True, "mode": "live", "reasons": []},
            run_forecast=lambda request: {
                "sport_label": "Tenis",
                "league_label": "ATP",
                "proveedor_cuotas": "the_odds_api",
                "historical_snapshot_at": "2026-08-01T10:00:00Z",
                "total_analizadas": 3,
                "total_recomendadas": 3,
                "mejores_apuestas": [],
                "descartadas": [],
            },
            build_prediction_payload=lambda **kwargs: {
                "pronosticos": [
                    {
                        "event_id": "evt_in",
                        "sport_key": "tennis_atp",
                        "sport_label": "Tenis",
                        "league_label": "ATP",
                        "commence_time": "2026-08-02T12:00:00Z",
                        "partido": "In Range",
                        "equipo": "A",
                        "equipo_raw": "A",
                        "mercado": "h2h",
                        "tipo_resultado": "home",
                        "tipo_resultado_raw": "home",
                        "cuota": 1.8,
                        "importe_sugerido": 3.0,
                        "recomendacion": "Value",
                        "motivo": "test",
                    },
                    {
                        "event_id": "evt_out",
                        "sport_key": "tennis_atp",
                        "sport_label": "Tenis",
                        "league_label": "ATP",
                        "commence_time": "2026-08-05T12:00:00Z",
                        "partido": "Out Range",
                        "equipo": "B",
                        "equipo_raw": "B",
                        "mercado": "h2h",
                        "tipo_resultado": "home",
                        "tipo_resultado_raw": "home",
                        "cuota": 1.8,
                        "importe_sugerido": 3.0,
                        "recomendacion": "Value",
                        "motivo": "test",
                    },
                ]
            },
            ai_available=lambda: False,
            select_picks_for_telegram=lambda *args, **kwargs: [],
            enrich_with_ai=lambda picks: picks,
            build_ai_summary=lambda *args, **kwargs: None,
            format_pick_message=lambda pick: "pick",
            format_summary_message=lambda **kwargs: "summary",
            fetch_scores=lambda days_from, deporte=None: [],
            perfil="agresivo",
            modo="comparador",
            mercados="todo",
            partido="todos",
            deporte="tenis",
            bankroll=None,
            solo_stakazos=False,
            perfiles_stake={"agresivo"},
            modos_informe={"comparador"},
            perfil_label=lambda value: value or "agresivo",
            modo_label=lambda value: value or "comparador",
            simulation_mode="historical",
            historical_snapshot_at="2026-08-01T10:00:00Z",
            historical_range_from="2026-08-02T00:00:00Z",
            historical_range_to="2026-08-03T23:59:59Z",
        )

        self.assertEqual(len(result["publishable_preview"]), 1)
        self.assertEqual(result["publishable_preview"][0]["event_id"], "evt_in")
        self.assertEqual(result["simulation_context"]["range_from"], "2026-08-02T00:00:00Z")
        self.assertEqual(result["simulation_context"]["range_to"], "2026-08-03T23:59:59Z")

    def test_build_lab_run_historico_autoevalua_publicables(self):
        result = build_lab_run(
            runtime_settings=RuntimeSettings(environment="development", shadow_mode=False),
            publication_guard=lambda: {"allow_live_publication": True, "mode": "live", "reasons": []},
            run_forecast=lambda request: {
                "sport_label": "Baloncesto",
                "league_label": "WNBA",
                "proveedor_cuotas": "the_odds_api",
                "historical_snapshot_at": "2026-08-04T10:00:00Z",
                "total_analizadas": 2,
                "total_recomendadas": 2,
                "mejores_apuestas": [],
                "descartadas": [],
            },
            build_prediction_payload=lambda **kwargs: {
                "pronosticos": [
                    {
                        "event_id": "evt_hist_1",
                        "sport_key": "basketball_wnba",
                        "sport_label": "Baloncesto",
                        "league_label": "WNBA",
                        "commence_time": "2026-08-04T18:00:00Z",
                        "partido": "Wings vs Sun",
                        "equipo": "Under",
                        "equipo_raw": "Under",
                        "mercado": "totals",
                        "outcome_point": 171.5,
                        "casa": "1xBet",
                        "cuota": 1.94,
                        "stake": 1.7,
                        "importe_sugerido": 3.84,
                        "recomendacion": "Value",
                        "motivo": "test",
                    },
                    {
                        "event_id": "evt_hist_2",
                        "sport_key": "basketball_wnba",
                        "sport_label": "Baloncesto",
                        "league_label": "WNBA",
                        "commence_time": "2026-08-04T20:00:00Z",
                        "partido": "Mercury vs Liberty",
                        "equipo": "Phoenix Mercury",
                        "equipo_raw": "Phoenix Mercury",
                        "mercado": "spreads",
                        "tipo_resultado": "home",
                        "tipo_resultado_raw": "home",
                        "outcome_point": 4.5,
                        "casa": "Bet365",
                        "cuota": 1.91,
                        "stake": 1.2,
                        "importe_sugerido": 2.4,
                        "recomendacion": "Value",
                        "motivo": "test",
                    },
                ]
            },
            ai_available=lambda: False,
            select_picks_for_telegram=lambda *args, **kwargs: [],
            enrich_with_ai=lambda picks: picks,
            build_ai_summary=lambda *args, **kwargs: None,
            format_pick_message=lambda pick: "pick",
            format_summary_message=lambda **kwargs: "summary",
            fetch_scores=lambda days_from, deporte=None: [
                {
                    "id": "evt_hist_1",
                    "completed": True,
                    "home_team": "Wings",
                    "away_team": "Sun",
                    "scores": [
                        {"name": "Wings", "score": "80"},
                        {"name": "Sun", "score": "85"},
                    ],
                },
                {
                    "id": "evt_hist_2",
                    "completed": True,
                    "home_team": "Phoenix Mercury",
                    "away_team": "New York Liberty",
                    "scores": [
                        {"name": "Phoenix Mercury", "score": "89"},
                        {"name": "New York Liberty", "score": "83"},
                    ],
                },
            ],
            perfil="agresivo",
            modo="comparador",
            mercados="todo",
            partido="todos",
            deporte="baloncesto",
            bankroll=None,
            solo_stakazos=False,
            perfiles_stake={"agresivo"},
            modos_informe={"comparador"},
            perfil_label=lambda value: value or "agresivo",
            modo_label=lambda value: value or "comparador",
            simulation_mode="historical",
            historical_snapshot_at="2026-08-04T10:00:00Z",
        )

        self.assertTrue(result["historical_evaluation"]["enabled"])
        self.assertEqual(result["historical_evaluation"]["closed"], 2)
        self.assertEqual(result["historical_evaluation"]["won"], 2)
        self.assertEqual(result["publishable_preview"][0]["historical_result"], "win")
        self.assertEqual(result["publishable_preview"][1]["historical_result"], "win")

    def test_build_lab_run_historico_calcula_profit_con_cuota_apuesta(self):
        result = build_lab_run(
            runtime_settings=RuntimeSettings(environment="development", shadow_mode=False),
            publication_guard=lambda: {"allow_live_publication": True, "mode": "live", "reasons": []},
            run_forecast=lambda request: {
                "sport_label": "Tenis",
                "league_label": "ATP",
                "proveedor_cuotas": "the_odds_api",
                "historical_snapshot_at": "2026-08-04T10:00:00Z",
                "total_analizadas": 1,
                "total_recomendadas": 1,
                "mejores_apuestas": [],
                "descartadas": [],
            },
            build_prediction_payload=lambda **kwargs: {
                "pronosticos": [
                    {
                        "event_id": "evt_hist_profit",
                        "sport_key": "tennis_atp_canadian_open",
                        "sport_label": "Tenis",
                        "league_label": "ATP Canadian Open",
                        "commence_time": "2026-08-04T12:00:00Z",
                        "partido": "Player A vs Player B",
                        "equipo": "Player A",
                        "equipo_raw": "Player A",
                        "mercado": "h2h",
                        "tipo_resultado": "home",
                        "tipo_resultado_raw": "home",
                        "casa": "Betfair",
                        "cuota_apuesta": 1.5,
                        "stake": 1.27,
                        "importe_sugerido": 2.29,
                        "recomendacion": "Value",
                        "motivo": "test",
                    }
                ]
            },
            ai_available=lambda: False,
            select_picks_for_telegram=lambda *args, **kwargs: [],
            enrich_with_ai=lambda picks: picks,
            build_ai_summary=lambda *args, **kwargs: None,
            format_pick_message=lambda pick: "pick",
            format_summary_message=lambda **kwargs: "summary",
            fetch_scores=lambda days_from, deporte=None: [
                {
                    "id": "evt_hist_profit",
                    "completed": True,
                    "home_team": "Player A",
                    "away_team": "Player B",
                    "scores": [
                        {"name": "Player A", "score": "2"},
                        {"name": "Player B", "score": "0"},
                    ],
                }
            ],
            perfil="agresivo",
            modo="comparador",
            mercados="todo",
            partido="todos",
            deporte="tenis",
            bankroll=None,
            solo_stakazos=False,
            perfiles_stake={"agresivo"},
            modos_informe={"comparador"},
            perfil_label=lambda value: value or "agresivo",
            modo_label=lambda value: value or "comparador",
            simulation_mode="historical",
            historical_snapshot_at="2026-08-04T10:00:00Z",
        )

        self.assertEqual(result["publishable_preview"][0]["historical_result"], "win")
        self.assertEqual(result["publishable_preview"][0]["historical_profit_loss"], 1.15)
        self.assertEqual(result["historical_evaluation"]["profit"], 1.15)
        self.assertEqual(result["historical_evaluation"]["roi"], 50.22)

    def test_build_empty_lab_run_no_lanza_simulacion(self):
        result = build_empty_lab_run(
            runtime_settings=RuntimeSettings(environment="development", shadow_mode=True),
        )

        self.assertEqual(result["runtime_mode"], "shadow")
        self.assertEqual(result["forecast_summary"]["total_analizadas"], 0)
        self.assertEqual(result["publishable_preview"], [])
        self.assertIn("Pulsa 'Ejecutar lab'", result["publication_decision"]["guard_reasons"][0])


    def test_render_lab_run_html_muestra_resumen_visual(self):
        html = render_lab_run_html(
            {
                "runtime_mode": "shadow",
                "publication_decision": {
                    "would_publish_live": False,
                    "runtime_mode": "shadow",
                    "guard_mode": "shadow",
                    "guard_reasons": ["shadow_mode_activo"],
                },
                "forecast_summary": {
                    "sport_label": "Baloncesto",
                    "league_label": "WNBA",
                    "total_analizadas": 8,
                    "total_recomendadas": 2,
                    "total_descartadas_preview": 3,
                    "total_publicables_preview": 1,
                    "total_bloqueadas_en_recomendadas": 0,
                    "total_bloqueadas_en_descartadas": 1,
                },
                "publishable_preview": [
                    {
                        "event_id": "evt_1",
                        "partido": "Aces vs Liberty",
                        "equipo": "Under 166.5",
                        "mercado": "h2h",
                        "casa": "Pinnacle",
                        "league_label": "WNBA",
                        "stake": 1,
                        "importe_sugerido": 3.0,
                        "recomendacion": "Value",
                        "motivo": "test",
                    }
                ],
                "blocked_picks": {
                    "recommended": [],
                    "discarded": [
                        {
                            "event_id": "evt_2",
                            "partido": "Fever vs Storm",
                            "equipo": "Over 177.5",
                            "mercado": "totals",
                            "casa": "Pinnacle",
                            "league_label": "WNBA",
                            "stake": 0,
                            "importe_sugerido": 0,
                            "recomendacion": "No apostar",
                            "performance_guard_reason": "Liga bloqueada",
                        }
                    ],
                },
                "match_overview": [
                    {
                        "event_id": "evt_1",
                        "partido": "Aces vs Liberty",
                        "league_label": "WNBA",
                        "time_label": "19:30",
                        "status": "Publicable",
                        "status_kind": "ok",
                        "publishable": 1,
                        "blocked": 0,
                    },
                    {
                        "event_id": "evt_2",
                        "partido": "Fever vs Storm",
                        "league_label": "WNBA",
                        "time_label": "22:00",
                        "status": "Bloqueado",
                        "status_kind": "danger",
                        "publishable": 0,
                        "blocked": 1,
                    }
                ],
                "telegram_preview": {"resumen_telegram": "Resumen de prueba"},
                "todo_toggle_panel": {
                    "sports": [
                        {"key": "baloncesto", "label": "Baloncesto", "enabled": True},
                        {"key": "tenis", "label": "Tenis", "enabled": False},
                    ],
                    "leagues": [
                        {"key": "basketball_wnba", "label": "WNBA", "enabled": True},
                    ],
                },
            },
            query_params={
                "perfil": "moderado",
                "modo": "comparador",
                "mercados": "todo",
                "partido": "todos",
                "deporte": "todo",
                "solo_stakazos": "false",
                "simulation_mode": "live",
            },
            premium_css=lambda: ":root{}",
            profile_options=[
                {"value": "moderado", "label": "Moderado"},
                {"value": "agresivo", "label": "Agresivo"},
            ],
            mode_options=[
                {"value": "comparador", "label": "Comparador"},
                {"value": "pinnacle", "label": "Pinnacle"},
            ],
            sport_options=[
                {"value": "todo", "label": "Todo - deportes base"},
                {"value": "baloncesto", "label": "Baloncesto"},
            ],
            market_options=[
                {"value": "todo", "label": "Todo"},
                {"value": "total_goles", "label": "Totales"},
            ],
            match_options=[
                {"value": "todos", "label": "Todos los partidos"},
                {"value": "evt_1", "label": "Aces vs Liberty"},
            ],
        )

        self.assertIn("Laboratorio del modelo", html)
        self.assertIn("Picks que saldrian a Telegram", html)
        self.assertIn("Aces vs Liberty", html)
        self.assertIn("Liga bloqueada", html)
        self.assertIn("Ver JSON", html)
        self.assertIn("Configurar simulacion", html)
        self.assertIn('id="labRunForm"', html)
        self.assertIn('labLoadingOverlay', html)
        self.assertIn('Comparando precios entre casas', html)
        self.assertIn('/lab/run/publicar', html)
        self.assertIn('Publicar en Telegram y registrar cartera', html)
        self.assertIn('/tracking/panel', html)
        self.assertIn('<option value="todo" selected>Todo - deportes base</option>', html)
        self.assertIn('name="deporte"', html)
        self.assertIn('name="partido"', html)
        self.assertIn('Control de deportes y ligas para Todo', html)
        self.assertIn('/lab/run/todo-filters', html)
        self.assertIn('toggle-switch on', html)
        self.assertIn('toggle-switch off', html)
        self.assertIn('/apuestas', html)

    def test_render_lab_run_html_muestra_estado_en_espera_sin_execute(self):
        html = render_lab_run_html(
            build_empty_lab_run(
                runtime_settings=RuntimeSettings(environment="development", shadow_mode=True),
            ),
            query_params={
                "bankroll": "",
                "perfil": "agresivo",
                "modo": "comparador",
                "mercados": "todo",
                "partido": "todos",
                "deporte": "todo",
                "solo_stakazos": "false",
                "execute": "",
            },
            premium_css=lambda: "",
            profile_options=[{"value": "agresivo", "label": "Agresivo"}],
            mode_options=[{"value": "comparador", "label": "Comparador"}],
            sport_options=[{"value": "todo", "label": "Todo - deportes base"}],
            market_options=[{"value": "todo", "label": "Todo"}],
            match_options=[{"value": "todos", "label": "Todos los partidos"}],
        )

        self.assertIn("Lab en espera", html)
        self.assertIn("ya no consulta cuotas al abrirse", html)
        self.assertIn('name="execute" value="true"', html)

    def test_render_lab_run_html_historico_oculta_publicacion(self):
        html = render_lab_run_html(
            {
                "runtime_mode": "live",
                "publication_decision": {
                    "would_publish_live": False,
                    "runtime_mode": "live",
                    "guard_mode": "live",
                    "guard_reasons": ["Simulacion historica: el lab solo compara y no publica picks del pasado."],
                },
                "simulation_context": {
                    "mode": "historical",
                    "historical_mode": True,
                    "snapshot_at": "2026-08-01T10:00:00Z",
                    "market_notice": "Modo historico: solo featured.",
                    "provider_name": "the_odds_api",
                    "snapshots_guardados": 14,
                },
                "historical_evaluation": {
                    "enabled": True,
                    "evaluated": 1,
                    "closed": 1,
                    "pending": 0,
                    "won": 1,
                    "lost": 0,
                    "push": 0,
                    "staked": 2.5,
                    "profit": 2.25,
                    "roi": 90.0,
                    "hit_rate": 100.0,
                    "coverage_note": None,
                },
                "forecast_summary": {
                    "sport_label": "Tenis",
                    "league_label": "ATP",
                    "total_analizadas": 5,
                    "total_recomendadas": 1,
                    "total_descartadas_preview": 1,
                    "total_publicables_preview": 1,
                    "total_bloqueadas_en_recomendadas": 0,
                    "total_bloqueadas_en_descartadas": 0,
                },
                "publishable_preview": [
                    {
                        "event_id": "evt_hist",
                        "partido": "Player A vs Player B",
                        "equipo": "Player A",
                        "mercado": "h2h",
                        "casa": "Bet365",
                        "league_label": "ATP",
                        "stake": 1,
                        "importe_sugerido": 2.5,
                        "recomendacion": "Value",
                        "motivo": "test",
                        "historical_result_label": "Ganada",
                        "historical_result_icon": "✅",
                        "historical_profit_loss": 2.25,
                        "historical_status_detail": "Resultado simulado con marcador final del proveedor.",
                    }
                ],
                "blocked_picks": {"recommended": [], "discarded": []},
                "match_overview": [],
                "telegram_preview": {"resumen_telegram": "Resumen historico", "pronosticos": [{"id": 1}]},
            },
            query_params={
                "perfil": "agresivo",
                "modo": "comparador",
                "mercados": "todo",
                "partido": "todos",
                "deporte": "tenis",
                "solo_stakazos": "false",
                "simulation_mode": "historical",
                "snapshot_at": "2026-08-01T12:00",
                "execute": "true",
            },
            premium_css=lambda: "",
            profile_options=[{"value": "agresivo", "label": "Agresivo"}],
            mode_options=[{"value": "comparador", "label": "Comparador"}],
            sport_options=[{"value": "tenis", "label": "Tenis"}],
            market_options=[{"value": "todo", "label": "Todo"}],
            match_options=[{"value": "todos", "label": "Todos los partidos"}],
        )

        self.assertIn("Modo historico", html)
        self.assertIn("Snapshot: 2026-08-01T10:00:00Z", html)
        self.assertIn("Rango: - -&gt;", html.replace("->", "-&gt;"))
        self.assertIn('name="simulation_mode"', html)
        self.assertIn('name="snapshot_at"', html)
        self.assertIn('name="snapshot_from"', html)
        self.assertIn('name="snapshot_to"', html)
        self.assertIn("Solo simulacion", html)
        self.assertIn("Backtest del snapshot", html)
        self.assertIn("Resultado simulado con marcador final del proveedor.", html)
        self.assertNotIn("/lab/run/publicar", html)

    def test_cuotas_historicas_usan_the_odds_api_historical(self):
        import main

        original_provider = main.ODDS_PROVIDER
        original_fetch_historical = main.provider_layer.fetch_the_odds_historical_odds
        original_fetch_live = main.provider_layer.fetch_the_odds_odds

        try:
            main.ODDS_PROVIDER = "the_odds_api"
            called = {}

            def fake_historical(markets, context, snapshot):
                called["markets"] = markets
                called["context"] = context
                called["snapshot"] = snapshot
                return [{"id": "hist_evt"}]

            def fail_live(*args, **kwargs):
                raise AssertionError("No deberia usar el endpoint live en modo historico")

            main.provider_layer.fetch_the_odds_historical_odds = fake_historical
            main.provider_layer.fetch_the_odds_odds = fail_live

            result = main.cuotas(
                mercados="h2h,team_totals,totals",
                deporte="baloncesto",
                historical_date="2026-08-01T10:00:00Z",
            )
        finally:
            main.ODDS_PROVIDER = original_provider
            main.provider_layer.fetch_the_odds_historical_odds = original_fetch_historical
            main.provider_layer.fetch_the_odds_odds = original_fetch_live

        self.assertEqual(result, [{"id": "hist_evt"}])
        self.assertEqual(called["markets"], ["h2h", "totals"])
        self.assertEqual(called["snapshot"], "2026-08-01T10:00:00Z")

    def test_lab_run_no_consulta_datos_si_no_se_ejecuta(self):
        import main

        original_apuestas_hoy = main.apuestas_hoy

        try:
            def fail_apuestas_hoy(*args, **kwargs):
                raise AssertionError("No deberia consultar cuotas al abrir /lab/run sin execute")

            main.apuestas_hoy = fail_apuestas_hoy
            response = main.lab_run()
        finally:
            main.apuestas_hoy = original_apuestas_hoy

        self.assertEqual(response.status_code, 200)
        self.assertIn("Lab en espera", response.body.decode("utf-8"))

    def test_lab_run_acepta_bankroll_vacio_en_query(self):
        import main

        original_build_lab_run = main.build_lab_run

        captured: dict[str, object] = {}

        try:
            def fake_build_lab_run(**kwargs):
                captured["bankroll"] = kwargs.get("bankroll")
                return build_empty_lab_run(
                    runtime_settings=RuntimeSettings(environment="development", shadow_mode=True),
                )

            main.build_lab_run = fake_build_lab_run
            response = main.lab_run(
                bankroll="",
                execute=True,
                simulation_mode="historical",
                snapshot_at="2026-08-04T00:00",
            )
        finally:
            main.build_lab_run = original_build_lab_run

        self.assertEqual(captured["bankroll"], None)
        self.assertEqual(response.status_code, 200)

    def test_lab_run_historico_usa_desde_como_snapshot_base(self):
        import main

        original_build_lab_run = main.build_lab_run
        captured: dict[str, object] = {}

        try:
            def fake_build_lab_run(**kwargs):
                captured["historical_snapshot_at"] = kwargs.get("historical_snapshot_at")
                captured["historical_range_from"] = kwargs.get("historical_range_from")
                captured["historical_range_to"] = kwargs.get("historical_range_to")
                return build_empty_lab_run(
                    runtime_settings=RuntimeSettings(environment="development", shadow_mode=True),
                )

            main.build_lab_run = fake_build_lab_run
            response = main.lab_run(
                execute=True,
                simulation_mode="historical",
                snapshot_from="2026-08-04T00:00",
                snapshot_to="2026-08-04T23:59",
            )
        finally:
            main.build_lab_run = original_build_lab_run

        self.assertEqual(captured["historical_snapshot_at"], "2026-08-03T22:00:00Z")
        self.assertEqual(captured["historical_range_from"], "2026-08-03T22:00:00Z")
        self.assertEqual(captured["historical_range_to"], "2026-08-04T21:59:00Z")
        self.assertEqual(response.status_code, 200)

    def test_get_picks_for_date_usa_ventana_de_24_horas_y_no_dia_calendario(self):
        import app.audit as audit_module
        from datetime import datetime, timezone

        original_listar_picks = audit_module.listar_picks

        try:
            audit_module.listar_picks = lambda limit=10000, db_path=None: [
                {
                    "created_at": "2026-07-30T12:30:00+00:00",
                    "estado": "cerrada",
                    "resultado": "win",
                    "importe_sugerido": 10.0,
                    "profit_loss": 9.0,
                    "raw_json": "{\"recommended_by_bot\": true, \"apuesta_real\": true, \"telegram_publicada\": true}",
                },
                {
                    "created_at": "2026-07-30T09:00:00+00:00",
                    "estado": "cerrada",
                    "resultado": "loss",
                    "importe_sugerido": 10.0,
                    "profit_loss": -10.0,
                    "raw_json": "{\"recommended_by_bot\": true, \"apuesta_real\": true, \"telegram_publicada\": true}",
                },
            ]

            report = audit_module.get_picks_for_date(
                datetime(2026, 7, 31, 10, 0, tzinfo=timezone.utc),
                lookback_hours=24,
            )
        finally:
            audit_module.listar_picks = original_listar_picks

        self.assertEqual(report["window_label"], "Últimas 24h")
        self.assertEqual(report["recommended"], 1)
        self.assertEqual(report["executed"], 1)
        self.assertEqual(report["won"], 1)
        self.assertEqual(report["lost"], 0)


if __name__ == "__main__":
    unittest.main()
