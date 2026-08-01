from __future__ import annotations

from html import escape
from typing import Any, Callable

from tracking import DB_PATH, listar_evaluaciones_picks, obtener_resets_historial_deportes


def _safe_float(value: Any) -> float | None:
    try:
        if value in {None, ""}:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _result_icon(result: str | None) -> str:
    normalized = str(result or "").strip().lower()
    if normalized == "win":
        return "✅"
    if normalized == "loss":
        return "❌"
    if normalized == "push":
        return "➖"
    return "⏳"


def _result_triplet(wins: int, losses: int, pushes: int) -> str:
    return f"✅{wins} ❌{losses} ➖{pushes}"


def _window_metrics(rows: list[dict[str, Any]], size: int) -> dict[str, Any]:
    sample = rows[:size]
    wins = sum(1 for row in sample if str(row.get("resultado") or "").lower() == "win")
    losses = sum(1 for row in sample if str(row.get("resultado") or "").lower() == "loss")
    pushes = sum(1 for row in sample if str(row.get("resultado") or "").lower() == "push")
    clv_values = [_safe_float(row.get("clv_pct")) for row in sample]
    clv_values = [value for value in clv_values if value is not None]
    value_captured_values = [_safe_float(row.get("value_captured")) for row in sample]
    value_captured_values = [value for value in value_captured_values if value is not None]
    sequence = "".join(_result_icon(row.get("resultado")) for row in sample[:10])
    total = len(sample)
    hit_rate = round((wins / total) * 100, 2) if total else 0.0

    return {
        "size": size,
        "sample": total,
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "hit_rate": hit_rate,
        "clv_avg": round(sum(clv_values) / len(clv_values), 2) if clv_values else None,
        "value_avg": round(sum(value_captured_values) / len(value_captured_values), 4) if value_captured_values else None,
        "sequence": sequence or "-",
    }


def _current_streak(rows: list[dict[str, Any]]) -> dict[str, Any]:
    normalized = [str(row.get("resultado") or "").strip().lower() for row in rows if row.get("resultado")]
    if not normalized:
        return {"type": "none", "count": 0}

    first = normalized[0]
    count = 0
    for result in normalized:
        if result != first:
            break
        count += 1

    return {"type": first, "count": count}


def _group_recent_metrics(rows: list[dict[str, Any]], field: str, *, top: int = 5) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        name = str(row.get(field) or "Sin dato").strip() or "Sin dato"
        groups.setdefault(name, []).append(row)

    output: list[dict[str, Any]] = []
    for name, group_rows in groups.items():
        metrics = _window_metrics(group_rows, len(group_rows))
        output.append(
            {
                "name": name,
                "sample": metrics["sample"],
                "wins": metrics["wins"],
                "losses": metrics["losses"],
                "pushes": metrics["pushes"],
                "hit_rate": metrics["hit_rate"],
                "clv_avg": metrics["clv_avg"],
                "value_avg": metrics["value_avg"],
                "sequence": metrics["sequence"],
            }
        )

    return sorted(output, key=lambda item: (item["sample"], item["hit_rate"]), reverse=True)[:top]


def _build_alerts(sport_rows: list[dict[str, Any]], market_rows: list[dict[str, Any]]) -> list[str]:
    alerts: list[str] = []
    for row in sport_rows:
        if row["sample"] >= 5 and (row["hit_rate"] < 40 or (row["clv_avg"] is not None and row["clv_avg"] < 0)):
            alerts.append(
                f"{row['name']}: {_result_triplet(row['wins'], row['losses'], row['pushes'])} | hit {row['hit_rate']:.0f}%"
            )
    for row in market_rows:
        if row["sample"] >= 5 and (row["hit_rate"] < 42 or (row["clv_avg"] is not None and row["clv_avg"] < 0)):
            alerts.append(
                f"{row['name']}: {_result_triplet(row['wins'], row['losses'], row['pushes'])} | hit {row['hit_rate']:.0f}%"
            )
    return alerts[:4]


def build_recent_form_panel(db_path: str = DB_PATH) -> dict[str, Any]:
    evaluations = listar_evaluaciones_picks(limit=5000, db_path=db_path)
    recent_rows = list(evaluations)
    recent_50 = recent_rows[:50]
    by_sport = _group_recent_metrics(recent_50, "sport_label", top=6)
    by_market = _group_recent_metrics(recent_50, "mercado", top=6)
    history_resets = obtener_resets_historial_deportes(db_path=db_path)
    available_sports = sorted(
        {
            "Futbol",
            "Baloncesto",
            "Tenis",
            *[str(row.get("sport_label") or "").strip() for row in evaluations if str(row.get("sport_label") or "").strip()],
            *history_resets.keys(),
        }
    )

    return {
        "total_evaluations": len(recent_rows),
        "windows": [
            _window_metrics(recent_rows, 10),
            _window_metrics(recent_rows, 20),
            _window_metrics(recent_rows, 50),
        ],
        "current_streak": _current_streak(recent_rows),
        "by_sport": by_sport,
        "by_market": by_market,
        "alerts": _build_alerts(by_sport, by_market),
        "history_resets": history_resets,
        "available_sports": available_sports,
    }


def format_recent_form_panel_telegram(panel: dict[str, Any]) -> str:
    streak = panel.get("current_streak") or {}
    streak_type = str(streak.get("type") or "none").lower()
    streak_count = int(streak.get("count") or 0)
    streak_label = (
        f"{streak_count} ganadas seguidas" if streak_type == "win"
        else f"{streak_count} perdidas seguidas" if streak_type == "loss"
        else f"{streak_count} nulas seguidas" if streak_type == "push"
        else "sin racha clara"
    )

    lines = [
        "PANEL RECIENTE DEL MODELO",
        f"Evaluaciones totales: {int(panel.get('total_evaluations') or 0)}",
        f"Racha actual: {streak_label}",
    ]

    history_resets = panel.get("history_resets") or {}
    if history_resets:
        lines.append("Resets activos:")
        for sport, cutoff in history_resets.items():
            lines.append(f"- {sport}: desde {cutoff}")

    lines.extend(["", "Ventanas"])

    for window in panel.get("windows", []):
        lines.append(
            f"Ult {window['size']}: {_result_triplet(window['wins'], window['losses'], window['pushes'])} | "
            f"hit {window['hit_rate']:.0f}% | CLV {window['clv_avg'] if window['clv_avg'] is not None else '-'} | "
            f"seq {window['sequence']}"
        )

    sport_rows = panel.get("by_sport", [])
    if sport_rows:
        lines.append("")
        lines.append("Por deporte")
        for row in sport_rows[:4]:
            lines.append(
                f"{row['name']}: {_result_triplet(row['wins'], row['losses'], row['pushes'])} | "
                f"hit {row['hit_rate']:.0f}% | CLV {row['clv_avg'] if row['clv_avg'] is not None else '-'}"
            )

    market_rows = panel.get("by_market", [])
    if market_rows:
        lines.append("")
        lines.append("Por mercado")
        for row in market_rows[:4]:
            lines.append(
                f"{row['name']}: {_result_triplet(row['wins'], row['losses'], row['pushes'])} | "
                f"hit {row['hit_rate']:.0f}% | CLV {row['clv_avg'] if row['clv_avg'] is not None else '-'}"
            )

    alerts = panel.get("alerts", [])
    if alerts:
        lines.append("")
        lines.append("Alertas")
        lines.extend(alerts[:3])

    return "\n".join(lines)


def render_recent_form_panel_html(panel: dict[str, Any], *, premium_css: Callable[[], str]) -> str:
    streak = panel.get("current_streak") or {}
    streak_type = str(streak.get("type") or "none").lower()
    streak_count = int(streak.get("count") or 0)
    streak_label = (
        f"{streak_count} ganadas seguidas" if streak_type == "win"
        else f"{streak_count} perdidas seguidas" if streak_type == "loss"
        else f"{streak_count} nulas seguidas" if streak_type == "push"
        else "Sin racha clara"
    )
    window_cards = "".join(
        f"""
        <article class="metric">
            <span>Ult {int(window.get('size') or 0)}</span>
            <strong>{int(window.get('wins') or 0)}W-{int(window.get('losses') or 0)}L-{int(window.get('pushes') or 0)}N</strong>
            <small>Hit {float(window.get('hit_rate') or 0):.0f}% | CLV {escape(str(window.get('clv_avg') if window.get('clv_avg') is not None else '-'))}</small>
            <small>Secuencia {escape(str(window.get('sequence') or '-'))}</small>
        </article>
        """
        for window in panel.get("windows", [])
    )

    def _rows(items: list[dict[str, Any]]) -> str:
        return "".join(
            f"<tr><td>{escape(str(item.get('name') or 'Sin dato'))}</td><td>{int(item.get('sample') or 0)}</td><td>{int(item.get('wins') or 0)}-{int(item.get('losses') or 0)}-{int(item.get('pushes') or 0)}</td><td>{float(item.get('hit_rate') or 0):.0f}%</td><td>{escape(str(item.get('clv_avg') if item.get('clv_avg') is not None else '-'))}</td></tr>"
            for item in items
        ) or '<tr><td colspan="5" class="muted">Sin datos suficientes.</td></tr>'

    alert_html = "".join(f"<li>{escape(str(alert))}</li>" for alert in panel.get("alerts", [])) or "<li>Sin alertas activas.</li>"
    history_resets = panel.get("history_resets") or {}
    available_sports = [sport for sport in panel.get("available_sports", []) if str(sport or "").strip()]
    reset_options = "".join(
        f'<option value="{escape(str(sport))}">{escape(str(sport))}</option>'
        for sport in available_sports
    )
    clear_options = "".join(
        f'<option value="{escape(str(sport))}">{escape(str(sport))}</option>'
        for sport in history_resets.keys()
    )
    history_reset_html = "".join(
        f"<li><strong>{escape(str(sport))}</strong>: cuenta solo desde {escape(str(cutoff))}</li>"
        for sport, cutoff in history_resets.items()
    ) or "<li>Sin resets activos.</li>"

    return f"""
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Panel Reciente</title>
        <style>
            {premium_css()}
            .panel-shell {{
                max-width: 1180px;
                margin: 0 auto;
            }}
            .grid-3 {{
                display: grid;
                grid-template-columns: repeat(3, minmax(0, 1fr));
                gap: 16px;
            }}
            .stack {{
                display: grid;
                gap: 18px;
            }}
            .alert-list {{
                margin: 0;
                padding-left: 18px;
                color: var(--muted);
            }}
            @media (max-width: 900px) {{
                .grid-3 {{
                    grid-template-columns: 1fr;
                }}
            }}
        </style>
    </head>
    <body>
        <div class="container panel-shell">
            <div class="top-menu">
                <a href="/dashboard">Dashboard</a>
                <a href="/lab/run">Lab run</a>
                <a class="active" href="/tracking/panel">Panel reciente</a>
                <a href="/tracking/evaluaciones">Evaluaciones JSON</a>
            </div>
            <section class="hero">
                <div class="eyebrow">Panel de forma reciente</div>
                <h1>Rendimiento reciente del modelo</h1>
                <p>Esta vista resume la forma reciente del sistema para no reaccionar a una sola apuesta aislada y ver rapido si un deporte o mercado se esta torciendo.</p>
                <div class="hero-metrics">
                    <div class="hero-metric">
                        <span>Evaluaciones</span>
                        <strong>{int(panel.get('total_evaluations') or 0)}</strong>
                    </div>
                    <div class="hero-metric">
                        <span>Racha actual</span>
                        <strong>{escape(streak_label)}</strong>
                    </div>
                </div>
                <div class="cta-row">
                    <a class="button-link" href="/lab/run">Volver al lab</a>
                    <a class="button-link secondary" href="/tracking/panel?format=json">Abrir JSON tecnico</a>
                </div>
            </section>
            <div class="stack">
                <section class="card">
                    <h3>Reiniciar historial por deporte</h3>
                    <p>Esto no borra datos. Hace que el modelo ignore evaluaciones y picks antiguos de ese deporte para panel, calibracion y guards.</p>
                    <form method="post" action="/tracking/panel/reset-sport-form" class="inline-form" style="margin-bottom: 12px;">
                        <select name="sport_label">{reset_options}</select>
                        <input name="cutoff_at" type="datetime-local">
                        <button type="submit">Reiniciar desde esa fecha</button>
                    </form>
                    <form method="post" action="/tracking/panel/clear-sport-reset-form" class="inline-form" style="margin-bottom: 12px;">
                        <select name="sport_label">{clear_options or '<option value="">Sin resets</option>'}</select>
                        <button type="submit">Quitar reset</button>
                    </form>
                    <ul class="alert-list">{history_reset_html}</ul>
                </section>
                <section class="grid-3">
                    {window_cards}
                </section>
                <section class="panel" style="padding: 18px;">
                    <div class="eyebrow" style="color: var(--brand); margin-bottom: 12px;">Por deporte</div>
                    <table>
                        <thead><tr><th>Deporte</th><th>Muestra</th><th>W-L-N</th><th>Hit</th><th>CLV</th></tr></thead>
                        <tbody>{_rows(panel.get('by_sport', []))}</tbody>
                    </table>
                </section>
                <section class="panel" style="padding: 18px;">
                    <div class="eyebrow" style="color: var(--brand); margin-bottom: 12px;">Por mercado</div>
                    <table>
                        <thead><tr><th>Mercado</th><th>Muestra</th><th>W-L-N</th><th>Hit</th><th>CLV</th></tr></thead>
                        <tbody>{_rows(panel.get('by_market', []))}</tbody>
                    </table>
                </section>
                <section class="card">
                    <h3>Alertas de forma</h3>
                    <ul class="alert-list">{alert_html}</ul>
                </section>
            </div>
        </div>
    </body>
    </html>
    """
