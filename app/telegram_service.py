from dataclasses import dataclass
from html import escape
from typing import Any, Callable

import requests
from fastapi import HTTPException


@dataclass(frozen=True)
class TelegramBotConfig:
    token: str
    chat_id: str


def telegram_text(value: Any) -> str:
    return escape(str(value if value is not None else ""))


def telegram_tier_label(tier: str | None) -> str:
    tier_normalized = str(tier or "elite").strip().lower()
    return {
        "stakazo": "STAKAZO",
        "elite": "ELITE",
        "premium": "PREMIUM",
        "seguimiento": "SEGUIMIENTO",
    }.get(tier_normalized, tier_normalized.upper() or "ELITE")


def format_pick_message(
    pick: dict[str, Any],
    title_builder: Callable[[dict[str, Any]], str],
    type_label_builder: Callable[[dict[str, Any]], tuple[str, str]],
    condition_builder: Callable[[dict[str, Any]], str],
    penalty_summary_builder: Callable[[Any], str],
) -> str:
    cuota = pick.get("cuota_apuesta") or pick.get("cuota_pinnacle")
    stake = pick.get("stake")
    importe = pick.get("importe_sugerido")
    valor = float(pick.get("valor_esperado") or 0) * 100
    partido = pick.get("partido_es") or pick.get("partido")
    titulo = title_builder(pick)
    seleccion = pick.get("equipo_es") or pick.get("equipo")
    liga = pick.get("league_label") or pick.get("sport_label") or "General"
    tier = telegram_tier_label(pick.get("elite_tier"))
    confianza = pick.get("confianza") or "Media"
    fiabilidad = pick.get("reliability_tier") or "media"
    fiabilidad_score = pick.get("reliability_score") or 0
    quality_score = pick.get("quality_score") or 0
    tipo_label, tipo_valor = type_label_builder(pick)
    condicion = condition_builder(pick)
    motivo = pick.get("motivo_es") or pick.get("motivo") or "Sin detalle adicional."
    ajuste_historico = pick.get("historical_penalty_summary_es") or penalty_summary_builder(
        pick.get("historical_penalty_reasons")
    )

    return (
        f"<b>{telegram_text(tier)} | {telegram_text(liga)}</b>\n"
        f"<b>{telegram_text(titulo)}</b>\n"
        f"<b>Pick ID:</b> {telegram_text(pick.get('id') or '-')}\n"
        f"<b>Partido:</b> {telegram_text(partido)}\n"
        f"<b>Seleccion:</b> {telegram_text(seleccion)}\n"
        f"<b>{telegram_text(tipo_label)}:</b> {telegram_text(tipo_valor)}\n"
        f"<b>Cuota:</b> {telegram_text(cuota)}\n"
        f"<b>Stake:</b> {telegram_text(stake)}/5"
        + (f" | <b>Importe:</b> {telegram_text(importe)} EUR\n" if importe is not None else "\n")
        + f"<b>Value:</b> {valor:.1f}% | <b>Calidad:</b> {telegram_text(quality_score)}/100\n"
        f"<b>Confianza:</b> {telegram_text(confianza)} | <b>Fiabilidad:</b> {telegram_text(fiabilidad)} ({telegram_text(fiabilidad_score)}/100)\n"
        f"<b>Condicion:</b> {telegram_text(condicion)}\n"
        f"<b>Motivo:</b> {telegram_text(motivo)}"
        + (f"\n<b>Ajuste historico:</b> {telegram_text(ajuste_historico)}" if ajuste_historico else "")
    )


def format_summary_message(
    *,
    sport_label: str | None,
    league_label: str | None,
    perfil_label: str,
    modo_label: str,
    total_elite: int,
    total_stakazos: int,
    total_messages: int,
    solo_stakazos: bool,
    fallback_a_elite: bool = False,
) -> str:
    filtro = "Solo stakazos" if solo_stakazos else "Top 3-5 mejores apuestas"
    footer = "Sin stakazos ahora: se publica seleccion elite." if fallback_a_elite else "Seleccion priorizada por calidad, value y fiabilidad."

    return (
        f"<b>PREDI IA | INFORME PREMIUM</b>\n"
        f"<b>Deporte:</b> {telegram_text(sport_label or 'General')}\n"
        f"<b>Liga:</b> {telegram_text(league_label or 'General')}\n"
        f"<b>Perfil:</b> {telegram_text(perfil_label)}\n"
        f"<b>Modo:</b> {telegram_text(modo_label)}\n"
        f"<b>Picks elite:</b> {telegram_text(total_elite)}\n"
        f"<b>Stakazos:</b> {telegram_text(total_stakazos)}\n"
        f"<b>Envios:</b> {telegram_text(total_messages)}\n"
        f"<b>Filtro:</b> {telegram_text(filtro)}\n"
        f"<b>Nota:</b> {telegram_text(footer)}"
    )


def telegram_keyboard_for_pick(pick_id: int) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "Apostada", "callback_data": f"pick:{pick_id}:bet"},
                {"text": "Ganada", "callback_data": f"pick:{pick_id}:win"},
            ],
            [
                {"text": "Perdida", "callback_data": f"pick:{pick_id}:loss"},
                {"text": "Nula", "callback_data": f"pick:{pick_id}:push"},
            ],
        ]
    }


class TelegramClient:
    def __init__(self, config: TelegramBotConfig):
        self.config = config

    def api_request(
        self,
        method: str,
        payload: dict[str, Any] | None = None,
        timeout: int = 15,
        http_method: str = "post",
    ) -> dict:
        url = f"https://api.telegram.org/bot{self.config.token}/{method}"

        try:
            if http_method.lower() == "get":
                response = requests.get(url, params=payload or {}, timeout=timeout)
            else:
                response = requests.post(url, json=payload or {}, timeout=timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=f"No se pudo completar la llamada a Telegram ({method}): {exc}") from exc

        data = response.json()
        if not isinstance(data, dict) or not data.get("ok"):
            raise HTTPException(status_code=502, detail=f"Telegram devolvio una respuesta inesperada en {method}")

        return data

    def send_message(self, texto: str, reply_markup: dict[str, Any] | None = None) -> dict:
        payload = {
            "chat_id": self.config.chat_id,
            "text": texto,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        return self.api_request("sendMessage", payload=payload)

    def answer_callback_query(self, callback_query_id: str, text: str) -> dict:
        return self.api_request(
            "answerCallbackQuery",
            payload={
                "callback_query_id": callback_query_id,
                "text": text[:180],
                "show_alert": False,
            },
        )

    def process_update(
        self,
        update: dict[str, Any],
        action_handler: Callable[[int, str], str],
    ) -> None:
        callback = update.get("callback_query") or {}
        callback_id = callback.get("id")
        data = str(callback.get("data") or "").strip()

        if not callback_id or not data.startswith("pick:"):
            return

        parts = data.split(":")
        if len(parts) != 3:
            self.answer_callback_query(callback_id, "Accion no valida.")
            return

        try:
            pick_id = int(parts[1])
        except ValueError:
            self.answer_callback_query(callback_id, "Pick no valida.")
            return

        action = parts[2].strip().lower()
        try:
            mensaje = action_handler(pick_id, action)
        except ValueError as exc:
            mensaje = str(exc)
        except HTTPException as exc:
            mensaje = str(exc.detail)

        self.answer_callback_query(callback_id, mensaje)
