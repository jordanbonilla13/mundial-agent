from dataclasses import dataclass
from typing import Any, Callable

from app.engine import ForecastEngine, ForecastRequest


@dataclass(frozen=True)
class ForecastDependencies:
    provider_name: str
    reference_bookmaker: str
    perfiles_stake: set[str]
    modos_informe: set[str]
    get_bankroll: Callable[[], float]
    update_bankroll: Callable[[float], Any]
    resolve_context: Callable[[str | None], dict[str, Any]]
    resolve_markets: Callable[[str, str | None], tuple[list[str], str | None]]
    list_sport_options: Callable[[], list[dict[str, Any]]]
    aggregate_sports: Callable[[], list[str]]
    fetch_odds: Callable[..., list[dict[str, Any]]]
    list_matches: Callable[[list[dict]], list[dict[str, str]]]
    save_snapshots: Callable[[list[dict]], int]
    filter_matches: Callable[[list[dict], str], list[dict]]
    fetch_elos: Callable[[], dict[str, int]]
    select_reference_house: Callable[[list[dict], str], tuple[str, bool]]
    analyze_comparison: Callable[..., list[dict]]
    translate_pick: Callable[[dict], dict]
    historical_penalties: Callable[[], dict[str, Any]]
    apply_historical_penalty: Callable[[dict, dict[str, Any]], dict]
    sort_key_pick: Callable[[dict], tuple]
    sort_key_todo: Callable[[dict], tuple]
    limit_todo_picks: Callable[[list[dict], int], list[dict]]
    save_recommendations: Callable[[list[dict]], int]
    perfil_label: Callable[[str | None], str]
    modo_label: Callable[[str | None], str]
    source_strength_for_context: Callable[[str, bool], str]
    stake_limit_text: Callable[[str], str]
    risk_disclaimer: Callable[[], str]
    attach_context_to_pick: Callable[..., dict[str, Any]]
    run_single_request: Callable[[ForecastRequest], dict[str, Any]] | None = None
    build_risk_policy: Callable[[], dict[str, Any]] | None = None
    apply_risk_policy_to_pick: Callable[[dict[str, Any], dict[str, Any], dict[str, Any] | None], dict[str, Any]] | None = None
    build_performance_guard: Callable[[], dict[str, Any]] | None = None
    apply_performance_guard_to_pick: Callable[[dict[str, Any], dict[str, Any] | None], dict[str, Any]] | None = None
    single_sport_pick_limit: Callable[[str | None], int] | None = None
    multi_sport_pick_limit: Callable[[], int] | None = None
    apply_exposure_limits: Callable[[list[dict[str, Any]], int | None], list[dict[str, Any]]] | None = None


def run_forecast_request(
    request: ForecastRequest,
    deps: ForecastDependencies,
) -> dict[str, Any]:
    engine = ForecastEngine(
        provider_name=deps.provider_name,
        reference_bookmaker=deps.reference_bookmaker,
        perfiles_stake=deps.perfiles_stake,
        modos_informe=deps.modos_informe,
        get_bankroll=deps.get_bankroll,
        update_bankroll=deps.update_bankroll,
        resolve_context=deps.resolve_context,
        resolve_markets=deps.resolve_markets,
        list_sport_options=deps.list_sport_options,
        aggregate_sports=deps.aggregate_sports,
        fetch_odds=deps.fetch_odds,
        list_matches=deps.list_matches,
        save_snapshots=deps.save_snapshots,
        filter_matches=deps.filter_matches,
        fetch_elos=deps.fetch_elos,
        select_reference_house=deps.select_reference_house,
        analyze_comparison=deps.analyze_comparison,
        translate_pick=deps.translate_pick,
        historical_penalties=deps.historical_penalties,
        apply_historical_penalty=deps.apply_historical_penalty,
        sort_key_pick=deps.sort_key_pick,
        sort_key_todo=deps.sort_key_todo,
        limit_todo_picks=deps.limit_todo_picks,
        save_recommendations=deps.save_recommendations,
        perfil_label=deps.perfil_label,
        modo_label=deps.modo_label,
        source_strength_for_context=deps.source_strength_for_context,
        stake_limit_text=deps.stake_limit_text,
        risk_disclaimer=deps.risk_disclaimer,
        attach_context_to_pick=deps.attach_context_to_pick,
        run_single_request=deps.run_single_request,
        build_risk_policy=deps.build_risk_policy,
        apply_risk_policy_to_pick=deps.apply_risk_policy_to_pick,
        build_performance_guard=deps.build_performance_guard,
        apply_performance_guard_to_pick=deps.apply_performance_guard_to_pick,
        single_sport_pick_limit=deps.single_sport_pick_limit,
        multi_sport_pick_limit=deps.multi_sport_pick_limit,
        apply_exposure_limits=deps.apply_exposure_limits,
    )
    return engine.run(request)
