from pydantic import BaseModel


class ResultadoPick(BaseModel):
    resultado: str
    closing_odds: float | None = None


class RegistroApuesta(BaseModel):
    recomendacion: dict
    importe_real: float


class ImportePick(BaseModel):
    importe_real: float


class CuotaPick(BaseModel):
    cuota_real: float


class BankrollPayload(BaseModel):
    bankroll: float
