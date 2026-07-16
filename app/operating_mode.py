from typing import Any


def aggressive_mode_enabled(operating_mode: str | None) -> bool:
    return str(operating_mode or "").strip().lower() == "agresivo"


def single_sport_pick_limit(operating_mode: str | None, partido: str | None) -> int:
    if partido and str(partido).strip().lower() != "todos":
        return 4 if aggressive_mode_enabled(operating_mode) else 3
    return 7 if aggressive_mode_enabled(operating_mode) else 5


def multi_sport_pick_limit(operating_mode: str | None) -> int:
    return 8 if aggressive_mode_enabled(operating_mode) else 6


def telegram_pick_limit(operating_mode: str | None, solo_stakazos: bool) -> int:
    if solo_stakazos:
        return 8 if aggressive_mode_enabled(operating_mode) else 5
    return 7 if aggressive_mode_enabled(operating_mode) else 5


def diversify_limits_for_todo(operating_mode: str | None) -> dict[str, int]:
    if aggressive_mode_enabled(operating_mode):
        return {
            "max_total": 8,
            "max_per_league": 3,
            "max_per_sport": 4,
        }
    return {
        "max_total": 6,
        "max_per_league": 2,
        "max_per_sport": 3,
    }


def operating_mode_summary(operating_mode: str | None) -> dict[str, Any]:
    mode = str(operating_mode or "equilibrado").strip().lower() or "equilibrado"
    aggressive = aggressive_mode_enabled(mode)
    return {
        "operating_mode": mode,
        "single_sport_limit": 7 if aggressive else 5,
        "multi_sport_limit": 8 if aggressive else 6,
        "telegram_limit": 7 if aggressive else 5,
    }
