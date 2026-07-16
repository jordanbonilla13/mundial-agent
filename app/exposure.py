from typing import Any


def _event_key(pick: dict[str, Any]) -> str:
    return str(pick.get("event_id") or pick.get("partido") or "").strip().lower()


def _league_key(pick: dict[str, Any]) -> str:
    return str(pick.get("league_label") or "general").strip().lower()


def _market_key(pick: dict[str, Any]) -> str:
    return str(pick.get("mercado") or "general").strip().lower()


def exposure_limits_for_mode(operating_mode: str | None) -> dict[str, int]:
    mode = str(operating_mode or "equilibrado").strip().lower()
    if mode == "agresivo":
        return {
            "max_per_event": 2,
            "max_per_event_same_market": 1,
            "max_per_league": 3,
            "max_per_market": 3,
        }
    if mode == "estricto":
        return {
            "max_per_event": 1,
            "max_per_event_same_market": 1,
            "max_per_league": 2,
            "max_per_market": 2,
        }
    return {
        "max_per_event": 1,
        "max_per_event_same_market": 1,
        "max_per_league": 2,
        "max_per_market": 3,
    }


def apply_exposure_limits(
    picks: list[dict[str, Any]],
    *,
    operating_mode: str | None,
    max_total: int | None = None,
) -> list[dict[str, Any]]:
    limits = exposure_limits_for_mode(operating_mode)
    selected: list[dict[str, Any]] = []
    per_event: dict[str, int] = {}
    per_event_market: dict[tuple[str, str], int] = {}
    per_league: dict[str, int] = {}
    per_market: dict[str, int] = {}

    for pick in picks:
        if max_total is not None and len(selected) >= max_total:
            break

        event = _event_key(pick)
        league = _league_key(pick)
        market = _market_key(pick)
        event_market = (event, market)

        if event and per_event.get(event, 0) >= limits["max_per_event"]:
            continue
        if event and per_event_market.get(event_market, 0) >= limits["max_per_event_same_market"]:
            continue
        if per_league.get(league, 0) >= limits["max_per_league"]:
            continue
        if per_market.get(market, 0) >= limits["max_per_market"]:
            continue

        selected.append(pick)
        if event:
            per_event[event] = per_event.get(event, 0) + 1
            per_event_market[event_market] = per_event_market.get(event_market, 0) + 1
        per_league[league] = per_league.get(league, 0) + 1
        per_market[market] = per_market.get(market, 0) + 1

    return selected
