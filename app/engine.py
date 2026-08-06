from dataclasses import dataclass
from datetime import datetime, timezone
from collections import Counter
from typing import Any, Callable


@dataclass(frozen=True)
class ForecastRequest:
    bankroll: float | None = None
    perfil: str = "moderado"
    modo: str = "comparador"
    mercados: str = "todo"
    partido: str = "todos"
    guardar: bool = False
    deporte: str = "worldcup"
    solo_elite: bool = False
    solo_stakazos: bool = False
    historical_mode: bool = False
    historical_date: str | None = None
    historical_from: str | None = None
    historical_to: str | None = None


HISTORICAL_FEATURED_MARKETS = {"h2h", "spreads", "totals"}


class ForecastEngine:
    def __init__(
        self,
        *,
        provider_name: str,
        reference_bookmaker: str,
        perfiles_stake: set[str],
        modos_informe: set[str],
        get_bankroll: Callable[[], float],
        update_bankroll: Callable[[float], Any],
        resolve_context: Callable[[str | None], dict[str, Any]],
        resolve_markets: Callable[[str, str | None], tuple[list[str], str | None]],
        list_sport_options: Callable[[], list[dict[str, Any]]],
        aggregate_sports: Callable[[], list[str]],
        fetch_odds: Callable[..., list[dict[str, Any]]],
        list_matches: Callable[[list[dict]], list[dict[str, str]]],
        save_snapshots: Callable[[list[dict]], int],
        filter_matches: Callable[[list[dict], str], list[dict]],
        fetch_elos: Callable[[], dict[str, int]],
        select_reference_house: Callable[[list[dict], str], tuple[str, bool]],
        analyze_comparison: Callable[..., list[dict]],
        translate_pick: Callable[[dict], dict],
        historical_penalties: Callable[[], dict[str, Any]],
        apply_historical_penalty: Callable[[dict, dict[str, Any]], dict],
        sort_key_pick: Callable[[dict], tuple],
        sort_key_todo: Callable[[dict], tuple],
        limit_todo_picks: Callable[[list[dict], int], list[dict]],
        save_recommendations: Callable[[list[dict]], int],
        perfil_label: Callable[[str | None], str],
        modo_label: Callable[[str | None], str],
        source_strength_for_context: Callable[[str, bool], str],
        stake_limit_text: Callable[[str], str],
        risk_disclaimer: Callable[[], str],
        attach_context_to_pick: Callable[..., dict[str, Any]],
        run_single_request: Callable[[ForecastRequest], dict[str, Any]] | None = None,
        build_risk_policy: Callable[[], dict[str, Any]] | None = None,
        apply_risk_policy_to_pick: Callable[[dict[str, Any], dict[str, Any], dict[str, Any] | None], dict[str, Any]] | None = None,
        build_performance_guard: Callable[[], dict[str, Any]] | None = None,
        apply_performance_guard_to_pick: Callable[[dict[str, Any], dict[str, Any] | None], dict[str, Any]] | None = None,
        single_sport_pick_limit: Callable[[str | None], int] | None = None,
        multi_sport_pick_limit: Callable[[], int] | None = None,
        apply_exposure_limits: Callable[[list[dict[str, Any]], int | None], list[dict[str, Any]]] | None = None,
    ):
        self.provider_name = provider_name
        self.reference_bookmaker = reference_bookmaker
        self.perfiles_stake = perfiles_stake
        self.modos_informe = modos_informe
        self.get_bankroll = get_bankroll
        self.update_bankroll = update_bankroll
        self.resolve_context = resolve_context
        self.resolve_markets = resolve_markets
        self.list_sport_options = list_sport_options
        self.aggregate_sports = aggregate_sports
        self.fetch_odds = fetch_odds
        self.list_matches = list_matches
        self.save_snapshots = save_snapshots
        self.filter_matches = filter_matches
        self.fetch_elos = fetch_elos
        self.select_reference_house = select_reference_house
        self.analyze_comparison = analyze_comparison
        self.translate_pick = translate_pick
        self.historical_penalties = historical_penalties
        self.apply_historical_penalty = apply_historical_penalty
        self.sort_key_pick = sort_key_pick
        self.sort_key_todo = sort_key_todo
        self.limit_todo_picks = limit_todo_picks
        self.save_recommendations = save_recommendations
        self.perfil_label = perfil_label
        self.modo_label = modo_label
        self.source_strength_for_context = source_strength_for_context
        self.stake_limit_text = stake_limit_text
        self.risk_disclaimer = risk_disclaimer
        self.attach_context_to_pick = attach_context_to_pick
        self.run_single_request = run_single_request
        self.build_risk_policy = build_risk_policy
        self.apply_risk_policy_to_pick = apply_risk_policy_to_pick
        self.build_performance_guard = build_performance_guard
        self.apply_performance_guard_to_pick = apply_performance_guard_to_pick
        self.single_sport_pick_limit = single_sport_pick_limit
        self.multi_sport_pick_limit = multi_sport_pick_limit
        self.apply_exposure_limits = apply_exposure_limits

    @staticmethod
    def _parse_commence_time(value: str | None) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None

        try:
            if text.endswith("Z"):
                text = f"{text[:-1]}+00:00"
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None

        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(timezone.utc)

    def _filter_events_by_publication_window(
        self,
        events: list[dict[str, Any]],
        *,
        max_hours: int = 72,
    ) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        filtered: list[dict[str, Any]] = []

        for event in events:
            commence = self._parse_commence_time(event.get("commence_time"))
            if commence is None:
                filtered.append(event)
                continue

            delta_hours = (commence - now).total_seconds() / 3600
            if delta_hours <= max_hours:
                filtered.append(event)

        return filtered

    def run(self, request: ForecastRequest) -> dict[str, Any]:
        if (request.deporte or "").strip().lower() == "todo":
            return self._run_multi_sport(request)
        return self._run_single_sport(request)

    def _normalize_profile_and_mode(self, perfil: str, modo: str) -> tuple[str, str]:
        perfil_norm = perfil if perfil in self.perfiles_stake else "moderado"
        modo_norm = modo if modo in self.modos_informe else "comparador"
        return perfil_norm, modo_norm

    def _run_multi_sport(self, request: ForecastRequest) -> dict[str, Any]:
        deportes_agregados = self.aggregate_sports()
        total_opciones_todo = max(
            0,
            len([item for item in self.list_sport_options() if str(item.get("value") or "").strip().lower() != "todo"]),
        )
        agregado = []
        descartadas_total = []
        partidos_total = []
        cobertura = []
        errores_cobertura = []
        total_analizadas = 0
        total_guardadas = 0
        total_snapshots = 0
        perfil, modo = self._normalize_profile_and_mode(request.perfil, request.modo)
        resolved_bankroll = self.get_bankroll() if request.bankroll is None else float(request.bankroll)

        for deporte_item in deportes_agregados:
            nested_request = ForecastRequest(
                bankroll=resolved_bankroll,
                perfil=perfil,
                modo=modo,
                mercados=request.mercados,
                partido=request.partido,
                guardar=request.guardar,
                deporte=deporte_item,
                solo_elite=request.solo_elite,
                solo_stakazos=request.solo_stakazos,
                historical_mode=request.historical_mode,
                historical_date=request.historical_date,
                historical_from=request.historical_from,
                historical_to=request.historical_to,
            )
            try:
                if self.run_single_request is not None:
                    data_item = self.run_single_request(nested_request)
                else:
                    data_item = self._run_single_sport(nested_request)
            except Exception as exc:
                detail = getattr(exc, "detail", str(exc))
                errores_cobertura.append({"deporte": deporte_item, "detail": str(detail)})
                continue

            agregado.extend(data_item.get("mejores_apuestas", []))
            descartadas_total.extend(data_item.get("descartadas", []))
            liga_label = str(data_item.get("league_label") or data_item.get("sport_label") or deporte_item)
            partidos_item = data_item.get("partidos_disponibles", [])

            for partido_item in partidos_item:
                partidos_total.append({
                    **partido_item,
                    "label": f"{liga_label} | {partido_item.get('label')}",
                })

            cobertura.append({
                "deporte": deporte_item,
                "sport_label": data_item.get("sport_label"),
                "league_label": data_item.get("league_label"),
                "partidos": len(partidos_item),
                "recomendadas": len(data_item.get("mejores_apuestas", [])),
            })
            total_analizadas += int(data_item.get("total_analizadas", 0))
            total_guardadas += int(data_item.get("total_guardadas", 0))
            total_snapshots += int(data_item.get("snapshots_guardados", 0))

        recomendadas_full = sorted(agregado, key=self.sort_key_todo, reverse=True)
        todo_limit = self.multi_sport_pick_limit() if self.multi_sport_pick_limit is not None else 6
        recomendadas = self.limit_todo_picks(recomendadas_full, max_total=todo_limit)
        if self.apply_exposure_limits is not None:
            recomendadas = self.apply_exposure_limits(recomendadas, todo_limit)
        elite = [r for r in recomendadas if r.get("elite_pick")]
        stakazos = [r for r in elite if str(r.get("elite_tier") or "").lower() == "stakazo"]
        premium = [r for r in recomendadas if str(r.get("elite_tier") or "").lower() == "premium"]
        seguimiento = [r for r in recomendadas if str(r.get("elite_tier") or "").lower() == "seguimiento"]
        partidos_unicos = list({item["id"]: item for item in partidos_total}.values())
        deportes_con_eventos = [item for item in cobertura if item["partidos"] > 0]
        cobertura_txt = ", ".join(
            f"{item['league_label']} ({item['partidos']})"
            for item in deportes_con_eventos
        ) or "ninguno"
        aviso_cobertura = None

        if len(deportes_con_eventos) <= 1:
            aviso_cobertura = f"Con 'Todo' el sistema agrega los deportes base soportados. Ahora mismo solo hay eventos en: {cobertura_txt}."
        if total_opciones_todo > len(deportes_agregados):
            aviso_limite = (
                f"Modo 'Todo' limitado para cargar mas rapido: se revisan {len(deportes_agregados)} ligas priorizadas "
                f"de {total_opciones_todo} disponibles y se muestran solo los picks mas fiables y proximos."
            )
            aviso_cobertura = f"{aviso_limite} {aviso_cobertura}".strip() if aviso_cobertura else aviso_limite
        if errores_cobertura:
            ligas_error = ", ".join(item["deporte"] for item in errores_cobertura[:5])
            aviso_error = f"Algunas ligas se omitieron en 'Todo' porque el proveedor rechazo sus parametros: {ligas_error}."
            aviso_cobertura = f"{aviso_cobertura} {aviso_error}".strip() if aviso_cobertura else aviso_error

        if not recomendadas and not cobertura and errores_cobertura:
            raise Exception(errores_cobertura[0]["detail"])

        return {
            "criterio": "Agregado multi-deporte sobre deportes base soportados",
            "aviso": self.risk_disclaimer(),
            "proveedor_cuotas": self.provider_name,
            "casa_referencia": self.reference_bookmaker,
            "casa_referencia_fallback": False,
            "bankroll": resolved_bankroll,
            "perfil": perfil,
            "perfil_es": self.perfil_label(perfil),
            "modo": modo,
            "sport_key": "multi_sport",
            "sport_label": "Todo",
            "league_key": "multi_league",
            "league_label": "Todas las ligas base",
            "deporte": "todo",
            "solo_elite": request.solo_elite,
            "solo_stakazos": request.solo_stakazos,
            "simulation_mode": "historical" if request.historical_mode else "live",
            "historical_mode": request.historical_mode,
            "historical_snapshot_at": request.historical_date,
            "historical_range_from": request.historical_from,
            "historical_range_to": request.historical_to,
            "source_strength": "mixed",
            "mercados": request.mercados,
            "filtro_mercados": request.mercados,
            "partido": request.partido,
            "partidos_disponibles": partidos_unicos,
            "aviso_mercados": None,
            "aviso_cobertura": aviso_cobertura,
            "cobertura_deportes": cobertura,
            "errores_cobertura": errores_cobertura,
            "snapshots_guardados": total_snapshots,
            "modo_es": self.modo_label(modo),
            "stake_maximo_por_pick": self.stake_limit_text(perfil),
            "total_analizadas": total_analizadas,
            "total_recomendadas": len(recomendadas),
            "total_elite": len(elite),
            "total_stakazos": len(stakazos),
            "total_premium": len(premium),
            "total_seguimiento": len(seguimiento),
            "total_guardadas": total_guardadas,
            "mejores_apuestas": recomendadas[:todo_limit],
            "picks_elite": stakazos[:10] if request.solo_stakazos else elite[:10],
            "descartadas": sorted(descartadas_total, key=self.sort_key_pick, reverse=True)[:5],
        }

    def _run_single_sport(self, request: ForecastRequest) -> dict[str, Any]:
        bankroll = self.get_bankroll() if request.bankroll is None else float(request.bankroll)
        self.update_bankroll(bankroll)
        contexto_deporte = self.resolve_context(request.deporte)
        deporte = contexto_deporte["catalog_key"]
        source_strength = self.source_strength_for_context(
            deporte,
            bool(contexto_deporte["supports_elo"]),
        )
        perfil, modo = self._normalize_profile_and_mode(request.perfil, request.modo)
        filtro_mercados = request.mercados
        mercados_lista, aviso_mercados = self.resolve_markets(request.mercados, deporte=deporte)
        historical_market_notice = None

        if request.historical_mode:
            requested_markets = list(mercados_lista)
            mercados_lista = [market for market in mercados_lista if market in HISTORICAL_FEATURED_MARKETS] or ["h2h"]
            if requested_markets != mercados_lista:
                historical_market_notice = (
                    "Modo historico: solo se simulan mercados featured de The Odds API "
                    "(ganador, handicap y totales)."
                )
                aviso_mercados = (
                    f"{aviso_mercados} {historical_market_notice}".strip()
                    if aviso_mercados
                    else historical_market_notice
                )

        base_payload = {
            "bankroll": bankroll,
            "perfil": perfil,
            "perfil_es": self.perfil_label(perfil),
            "modo": modo,
            "modo_es": self.modo_label(modo),
            "deporte": deporte,
            "sport_key": contexto_deporte["sport_key"],
            "sport_label": contexto_deporte["sport_label"],
            "league_key": contexto_deporte.get("league_key"),
            "league_label": contexto_deporte["league_label"],
            "solo_elite": request.solo_elite,
            "solo_stakazos": request.solo_stakazos,
            "simulation_mode": "historical" if request.historical_mode else "live",
            "historical_mode": request.historical_mode,
            "historical_snapshot_at": request.historical_date,
            "historical_range_from": request.historical_from,
            "historical_range_to": request.historical_to,
            "historical_market_notice": historical_market_notice,
            "stake_maximo_por_pick": self.stake_limit_text(perfil),
        }

        if not mercados_lista:
            return {
                "criterio": "Sin mercados disponibles para el filtro elegido",
                "aviso": self.risk_disclaimer(),
                **base_payload,
                "mercados": "",
                "filtro_mercados": filtro_mercados,
                "partido": request.partido,
                "partidos_disponibles": [],
                "aviso_mercados": aviso_mercados,
                "snapshots_guardados": 0,
                "total_analizadas": 0,
                "total_recomendadas": 0,
                "total_guardadas": 0,
                "mejores_apuestas": [],
                "descartadas": [],
            }

        mercados = ",".join(mercados_lista)
        try:
            data_completa = self.fetch_odds(
                mercados=mercados,
                deporte=deporte,
                historical_date=request.historical_date if request.historical_mode else None,
                historical_from=request.historical_from if request.historical_mode else None,
                historical_to=request.historical_to if request.historical_mode else None,
            )
        except TypeError:
            data_completa = self.fetch_odds(mercados=mercados, deporte=deporte)
        if not request.historical_mode:
            data_completa = self._filter_events_by_publication_window(data_completa, max_hours=72)
        partidos_select = self.list_matches(data_completa)
        snapshots_guardados = self.save_snapshots(data_completa)
        data = self.filter_matches(data_completa, request.partido)

        if not data:
            return {
                "criterio": "No hay cuotas disponibles para el partido elegido",
                "aviso": self.risk_disclaimer(),
                "proveedor_cuotas": self.provider_name,
                "casa_referencia": self.reference_bookmaker,
                "casa_referencia_fallback": False,
                **base_payload,
                "mercados": mercados,
                "filtro_mercados": filtro_mercados,
                "partido": request.partido,
                "partidos_disponibles": partidos_select,
                "aviso_mercados": aviso_mercados,
                "snapshots_guardados": snapshots_guardados,
                "total_analizadas": 0,
                "total_recomendadas": 0,
                "total_guardadas": 0,
                "mejores_apuestas": [],
                "descartadas": [],
            }

        elos = self.fetch_elos() if contexto_deporte["supports_elo"] else {}
        casa_referencia, referencia_fallback = self.select_reference_house(data, self.reference_bookmaker)

        if modo == "comparador":
            recomendaciones = self.analyze_comparison(
                data,
                elos,
                bankroll=bankroll,
                perfil=perfil,
                mercados=mercados_lista,
                casa_referencia=casa_referencia,
                source_strength=source_strength,
            )
            criterio = f"{casa_referencia} como referencia + busqueda de cuotas mejores en otras casas + modelo"
        else:
            recomendaciones = self.analyze_comparison(
                data,
                elos,
                bankroll=bankroll,
                perfil=perfil,
                mercados=mercados_lista,
                incluir_referencia=True,
                casa_referencia=casa_referencia,
                solo_casa=casa_referencia,
                source_strength=source_strength,
            )
            criterio = f"Solo {casa_referencia} + mercados seleccionados + modelo"

        if source_strength == "market+model":
            criterio += " (mercado + ELO)"
        elif source_strength == "tennis_model":
            criterio += " (moneyline tenis conservador)"
        elif source_strength == "basketball_model":
            criterio += " (moneyline/totales basket conservador)"
        else:
            criterio += " (market-only ultra conservador)"

        if referencia_fallback:
            criterio += f" (aviso: {self.reference_bookmaker} no estaba disponible en estas cuotas)"
        if request.historical_mode and request.historical_date:
            criterio += f" | snapshot historico {request.historical_date}"

        recomendaciones = [self.translate_pick(r) for r in recomendaciones]
        penalizaciones = self.historical_penalties()
        risk_policy = self.build_risk_policy() if self.build_risk_policy is not None else None

        for recomendacion in recomendaciones:
            self.attach_context_to_pick(
                recomendacion,
                perfil=perfil,
                perfil_label=self.perfil_label(perfil),
                modo=modo,
                modo_label=self.modo_label(modo),
                filtro_mercados=filtro_mercados,
                contexto_deporte=contexto_deporte,
            )

        recomendaciones = [self.apply_historical_penalty(r, penalizaciones) for r in recomendaciones]
        if risk_policy is not None and self.apply_risk_policy_to_pick is not None:
            recomendaciones = [
                self.apply_risk_policy_to_pick(
                    r,
                    risk_policy,
                    penalizaciones.get("ligas", {}),
                )
                for r in recomendaciones
            ]
        performance_guard = self.build_performance_guard() if self.build_performance_guard is not None else None
        if performance_guard is not None and self.apply_performance_guard_to_pick is not None:
            recomendaciones = [
                self.apply_performance_guard_to_pick(r, performance_guard)
                for r in recomendaciones
            ]
        blocked_by_risk = [r for r in recomendaciones if bool(r.get("risk_guard_blocked"))]
        blocked_by_performance = [r for r in recomendaciones if bool(r.get("performance_guard_blocked"))]
        risk_block_reasons = Counter(
            str(r.get("motivo") or "").strip()
            for r in blocked_by_risk
            if str(r.get("motivo") or "").strip()
        )
        performance_block_reasons = Counter(
            str(r.get("performance_guard_reason") or r.get("motivo") or "").strip()
            for r in blocked_by_performance
            if str(r.get("performance_guard_reason") or r.get("motivo") or "").strip()
        )
        recomendadas = sorted([r for r in recomendaciones if r["stake"] > 0], key=self.sort_key_pick, reverse=True)
        elite = sorted([r for r in recomendadas if r.get("elite_pick")], key=self.sort_key_pick, reverse=True)
        stakazos = sorted(
            [r for r in elite if str(r.get("elite_tier") or "").lower() == "stakazo"],
            key=self.sort_key_pick,
            reverse=True,
        )
        premium = sorted(
            [r for r in recomendadas if str(r.get("elite_tier") or "").lower() == "premium"],
            key=self.sort_key_pick,
            reverse=True,
        )
        seguimiento = sorted(
            [r for r in recomendadas if str(r.get("elite_tier") or "").lower() == "seguimiento"],
            key=self.sort_key_pick,
            reverse=True,
        )
        limite_mejores = self.single_sport_pick_limit(request.partido) if self.single_sport_pick_limit is not None else (3 if request.partido and request.partido != "todos" else 5)
        if request.solo_stakazos:
            recomendadas = stakazos
        elif request.solo_elite:
            recomendadas = elite
        elif self.apply_exposure_limits is not None:
            recomendadas = self.apply_exposure_limits(recomendadas, limite_mejores)
        descartadas = sorted([r for r in recomendaciones if r["stake"] == 0], key=self.sort_key_pick, reverse=True)
        total_guardadas = self.save_recommendations(recomendadas) if request.guardar else 0

        return {
            "criterio": criterio,
            "aviso": self.risk_disclaimer(),
            "proveedor_cuotas": self.provider_name,
            "casa_referencia": casa_referencia,
            "casa_referencia_fallback": referencia_fallback,
            **base_payload,
            "source_strength": source_strength,
            "mercados": mercados,
            "filtro_mercados": filtro_mercados,
            "partido": request.partido,
            "partidos_disponibles": partidos_select,
            "aviso_mercados": aviso_mercados,
            "snapshots_guardados": snapshots_guardados,
            "total_analizadas": len(recomendaciones),
            "total_recomendadas": len(recomendadas),
            "total_elite": len(elite),
            "total_stakazos": len(stakazos),
            "total_premium": len(premium),
            "total_seguimiento": len(seguimiento),
            "total_guardadas": total_guardadas,
            "mejores_apuestas": recomendadas[:limite_mejores],
            "picks_elite": stakazos[:10] if request.solo_stakazos else elite[:10],
            "descartadas": descartadas[:5],
            "blocked_summary": {
                "risk_count": len(blocked_by_risk),
                "performance_count": len(blocked_by_performance),
                "risk_reasons": [
                    {"reason": reason, "count": count}
                    for reason, count in risk_block_reasons.most_common(3)
                ],
                "performance_reasons": [
                    {"reason": reason, "count": count}
                    for reason, count in performance_block_reasons.most_common(3)
                ],
            },
        }
