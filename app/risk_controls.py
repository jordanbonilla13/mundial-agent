from typing import Any


def build_risk_policy(
    *,
    total_closed: int,
    roi: float,
    clv_medio: float | None,
    clv_positive_pct: float | None,
    operating_mode: str = "equilibrado",
) -> dict[str, Any]:
    mode = str(operating_mode or "equilibrado").strip().lower()
    sample_stage = "seed"
    if total_closed >= 60:
        sample_stage = "mature"
    elif total_closed >= 30:
        sample_stage = "growing"
    elif total_closed >= 12:
        sample_stage = "early"

    policy = {
        "operating_mode": mode,
        "sample_stage": sample_stage,
        "stake_multiplier": 1.0,
        "max_stake_units": 5.0,
        "block_new_picks": False,
        "block_fragile_markets": False,
        "only_elite_when_cautious": False,
        "reason": "normal",
    }

    if mode == "agresivo":
        policy["stake_multiplier"] = 1.15
        policy["max_stake_units"] = 5.0
    elif mode == "estricto":
        policy["stake_multiplier"] = 0.85
        policy["max_stake_units"] = 3.0
        policy["block_fragile_markets"] = True

    if total_closed < 12:
        if mode == "agresivo":
            policy["stake_multiplier"] = 0.85
            policy["max_stake_units"] = 2.5
            policy["block_fragile_markets"] = False
        elif mode == "estricto":
            policy["stake_multiplier"] = 0.45
            policy["max_stake_units"] = 1.0
            policy["block_fragile_markets"] = True
        else:
            policy["stake_multiplier"] = 0.55
            policy["max_stake_units"] = 1.5
            policy["block_fragile_markets"] = True
        policy["reason"] = "muestra_corta"
        return policy

    if total_closed < 30:
        if mode == "agresivo":
            policy["stake_multiplier"] = min(policy["stake_multiplier"], 0.95)
            policy["max_stake_units"] = min(policy["max_stake_units"], 3.5)
            policy["block_fragile_markets"] = False
        elif mode == "estricto":
            policy["stake_multiplier"] = min(policy["stake_multiplier"], 0.65)
            policy["max_stake_units"] = min(policy["max_stake_units"], 2.0)
            policy["block_fragile_markets"] = True
        else:
            policy["stake_multiplier"] = min(policy["stake_multiplier"], 0.75)
            policy["max_stake_units"] = min(policy["max_stake_units"], 2.5)
            policy["block_fragile_markets"] = True
        policy["reason"] = "muestra_en_construccion"

    if total_closed >= 20 and roi < 0:
        roi_stake_cap = 0.80 if mode == "agresivo" else 0.65 if mode == "equilibrado" else 0.55
        roi_units_cap = 3.0 if mode == "agresivo" else 2.0 if mode == "equilibrado" else 1.5
        policy["stake_multiplier"] = min(policy["stake_multiplier"], roi_stake_cap)
        policy["max_stake_units"] = min(policy["max_stake_units"], roi_units_cap)
        policy["only_elite_when_cautious"] = True
        policy["reason"] = "roi_negativo"

    if clv_medio is not None and total_closed >= 20 and float(clv_medio) < 0:
        clv_stake_cap = 0.72 if mode == "agresivo" else 0.60 if mode == "equilibrado" else 0.50
        clv_units_cap = 2.5 if mode == "agresivo" else 2.0 if mode == "equilibrado" else 1.5
        policy["stake_multiplier"] = min(policy["stake_multiplier"], clv_stake_cap)
        policy["max_stake_units"] = min(policy["max_stake_units"], clv_units_cap)
        policy["block_fragile_markets"] = mode != "agresivo"
        policy["only_elite_when_cautious"] = True
        policy["reason"] = "clv_negativo"

    if clv_positive_pct is not None and total_closed >= 20 and float(clv_positive_pct) < 45:
        clv_pos_stake_cap = 0.72 if mode == "agresivo" else 0.60 if mode == "equilibrado" else 0.50
        clv_pos_units_cap = 2.5 if mode == "agresivo" else 2.0 if mode == "equilibrado" else 1.5
        policy["stake_multiplier"] = min(policy["stake_multiplier"], clv_pos_stake_cap)
        policy["max_stake_units"] = min(policy["max_stake_units"], clv_pos_units_cap)
        policy["block_fragile_markets"] = mode != "agresivo"
        policy["reason"] = "poco_clv_positivo"

    if total_closed >= 40 and (roi <= -8 or (clv_medio is not None and float(clv_medio) <= -3)):
        policy["block_new_picks"] = True
        policy["stake_multiplier"] = 0.0
        policy["max_stake_units"] = 0.0
        policy["block_fragile_markets"] = True
        policy["only_elite_when_cautious"] = True
        policy["reason"] = "kill_switch_rendimiento"

    return policy


def apply_risk_policy_to_pick(
    pick: dict[str, Any],
    *,
    policy: dict[str, Any],
    league_penalties: dict[str, Any] | None = None,
) -> dict[str, Any]:
    adjusted = pick.copy()
    original_stake = float(adjusted.get("stake") or 0)
    adjusted["risk_policy_stage"] = policy.get("sample_stage")
    adjusted["risk_policy_reason"] = policy.get("reason")
    adjusted["risk_policy_multiplier"] = policy.get("stake_multiplier", 1.0)
    adjusted["risk_guard_blocked"] = False

    if original_stake <= 0:
        return adjusted

    if policy.get("block_new_picks"):
        adjusted["stake"] = 0
        adjusted["importe_sugerido"] = 0
        adjusted["stake_pct_bankroll"] = 0
        adjusted["kelly_fraccional"] = 0
        adjusted["recomendacion"] = "No apostar"
        adjusted["motivo"] = "Kill switch activo: el sistema entra en modo defensivo hasta recuperar CLV/ROI."
        adjusted["risk_guard_blocked"] = True
        return adjusted

    if policy.get("block_fragile_markets") and str(adjusted.get("market_signal") or "").lower() == "mercado_fragil":
        adjusted["stake"] = 0
        adjusted["importe_sugerido"] = 0
        adjusted["stake_pct_bankroll"] = 0
        adjusted["kelly_fraccional"] = 0
        adjusted["recomendacion"] = "No apostar"
        adjusted["motivo"] = "Mercado fragil bloqueado por control de riesgo."
        adjusted["risk_guard_blocked"] = True
        return adjusted

    if bool(adjusted.get("market_guard_blocked")):
        adjusted["stake"] = 0
        adjusted["importe_sugerido"] = 0
        adjusted["stake_pct_bankroll"] = 0
        adjusted["kelly_fraccional"] = 0
        adjusted["recomendacion"] = "No apostar"
        adjusted["motivo"] = "Bloqueado por guard de mercado: falta confirmacion suficiente para este total de baloncesto."
        adjusted["risk_guard_blocked"] = True
        return adjusted

    if policy.get("only_elite_when_cautious") and not bool(adjusted.get("elite_pick")):
        adjusted["stake"] = 0
        adjusted["importe_sugerido"] = 0
        adjusted["stake_pct_bankroll"] = 0
        adjusted["kelly_fraccional"] = 0
        adjusted["recomendacion"] = "No apostar"
        adjusted["motivo"] = "Modo cautela: solo se permiten picks elite hasta mejorar el rendimiento."
        adjusted["risk_guard_blocked"] = True
        return adjusted

    league_label = str(adjusted.get("league_label") or "")
    league_penalty = (league_penalties or {}).get(league_label)
    if league_penalty and int(league_penalty.get("penalty_score") or 0) >= 18:
        adjusted["stake"] = 0
        adjusted["importe_sugerido"] = 0
        adjusted["stake_pct_bankroll"] = 0
        adjusted["kelly_fraccional"] = 0
        adjusted["recomendacion"] = "No apostar"
        adjusted["motivo"] = "Liga bloqueada temporalmente por rendimiento historico deficiente."
        adjusted["risk_guard_blocked"] = True
        return adjusted

    multiplier = float(policy.get("stake_multiplier", 1.0))
    max_stake_units = float(policy.get("max_stake_units", 5.0))

    adjusted_stake = min(max_stake_units, round(original_stake * multiplier, 2))
    adjusted["stake"] = adjusted_stake

    if adjusted_stake <= 0:
        adjusted["importe_sugerido"] = 0
        adjusted["stake_pct_bankroll"] = 0
        adjusted["kelly_fraccional"] = 0
        adjusted["recomendacion"] = "No apostar"
        adjusted["motivo"] = "El control de riesgo ha dejado la exposicion por debajo del minimo operativo."
        adjusted["risk_guard_blocked"] = True
        return adjusted

    if adjusted.get("importe_sugerido") is not None:
        adjusted["importe_sugerido"] = round(float(adjusted.get("importe_sugerido") or 0) * multiplier, 2)
    if adjusted.get("stake_pct_bankroll") is not None:
        adjusted["stake_pct_bankroll"] = round(float(adjusted.get("stake_pct_bankroll") or 0) * multiplier, 3)
    if adjusted.get("kelly_fraccional") is not None:
        adjusted["kelly_fraccional"] = round(float(adjusted.get("kelly_fraccional") or 0) * multiplier, 5)

    if multiplier < 1.0:
        motivo = str(adjusted.get("motivo") or "").strip()
        sufijo = f" | Stake recortado por control de riesgo ({policy.get('reason')})."
        adjusted["motivo"] = f"{motivo}{sufijo}".strip()

    return adjusted
