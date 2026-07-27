import os
from typing import Any, Callable

from app.runtime_settings import RuntimeSettings


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def publication_guard_state(
    *,
    runtime_settings: RuntimeSettings,
    load_stats: Callable[[], dict[str, Any]],
    load_learning: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    if runtime_settings.shadow_mode:
        return {
            "allow_live_publication": False,
            "mode": "shadow",
            "reasons": ["shadow_mode_activo"],
            "stats": {},
        }

    stats = load_stats()
    learning = load_learning()
    reasons: list[str] = []

    min_closed = _int_env("PUBLICATION_MIN_CLOSED_PICKS", 80)
    min_clv_sample = _int_env("PUBLICATION_MIN_CLV_SAMPLE", 50)
    min_roi = _float_env("PUBLICATION_MIN_ROI", 2.0)
    min_hit_rate = _float_env("PUBLICATION_MIN_HIT_RATE", 52.0)
    min_clv_positive_pct = _float_env("PUBLICATION_MIN_CLV_POSITIVE_PCT", 52.0)
    min_model_picks_evaluated = _int_env("PUBLICATION_MIN_MODEL_EVALS", 60)

    closed = int(stats.get("picks_cerrados") or 0)
    roi = float(stats.get("roi") or 0)
    hit_rate = float(stats.get("hit_rate") or 0)
    clv_medio = float(stats.get("clv_medio") or 0) if stats.get("clv_medio") is not None else None
    clv_positive_pct = float(learning.get("porcentaje_clv_positivo") or 0)
    model_evals = int(learning.get("picks_evaluadas") or 0)
    clv_sample = int(learning.get("picks_con_clv") or 0)

    if closed < min_closed:
        reasons.append(f"muestra_corta:{closed}/{min_closed}")
    if roi < min_roi:
        reasons.append(f"roi_bajo:{roi:.2f}<{min_roi:.2f}")
    if hit_rate < min_hit_rate:
        reasons.append(f"hit_rate_bajo:{hit_rate:.2f}<{min_hit_rate:.2f}")
    if clv_sample < min_clv_sample:
        reasons.append(f"clv_muestra_corta:{clv_sample}/{min_clv_sample}")
    if clv_positive_pct < min_clv_positive_pct:
        reasons.append(f"clv_positivo_bajo:{clv_positive_pct:.2f}<{min_clv_positive_pct:.2f}")
    if model_evals < min_model_picks_evaluated:
        reasons.append(f"evaluaciones_modelo_cortas:{model_evals}/{min_model_picks_evaluated}")
    if clv_medio is not None and clv_medio < 0:
        reasons.append(f"clv_negativo:{clv_medio:.2f}")

    return {
        "allow_live_publication": len(reasons) == 0,
        "mode": "live" if not reasons else "blocked",
        "reasons": reasons,
        "stats": {
            "picks_cerrados": closed,
            "roi": roi,
            "hit_rate": hit_rate,
            "clv_medio": clv_medio,
            "clv_positive_pct": clv_positive_pct,
            "picks_evaluadas": model_evals,
            "picks_con_clv": clv_sample,
        },
    }
