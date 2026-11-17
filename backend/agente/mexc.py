"""Cliente de datos PÚBLICOS de MEXC (solo lectura).

No contiene ninguna función para enviar órdenes. No usa claves de trading.
Endpoints usados: GET /api/v3/klines, GET /api/v3/exchangeInfo, GET /api/v3/time.
"""
import math
import time
from dataclasses import dataclass

import httpx

from .config import INTERVALO, SIMBOLO
from .errores import ErrorDatoAtrasado, ErrorDatos, ErrorMEXC

DIA_MS = 86_400_000
BASE = "https://api.mexc.com"


@dataclass
class Vela:
    open_time: int          # ms UTC
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: int         # ms UTC

    @property
    def fecha(self) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime(self.open_time / 1000))


@dataclass
class InfoPar:
    paso_cantidad: float = 0.0001     # baseSizePrecision
    decimales_precio: int = 2         # quotePrecision
    monto_minimo: float = 1.0         # quoteAmountPrecision (monto mínimo en USDT)


class ClienteMEXC:
    def __init__(self, base: str = BASE, intentos: int = 4, timeout: float = 15.0, transporte=None, dormir=time.sleep):
        self.intentos = intentos
        self.dormir = dormir
        self._http = httpx.Client(base_url=base, timeout=timeout, transport=transporte)

    def _get(self, ruta: str, params: dict | None = None):
        ultimo = None
        for i in range(self.intentos):
            try:
                r = self._http.get(ruta, params=params)
                if r.status_code == 429 or r.status_code >= 500:
                    ultimo = f"HTTP {r.status_code}"
                    raise httpx.HTTPStatusError(ultimo, request=r.request, response=r)
                r.raise_for_status()
                return r.json()
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                ultimo = str(e) or type(e).__name__
                codigo = getattr(getattr(e, "response", None), "status_code", None)
                if codigo is not None and codigo < 500 and codigo != 429:
                    raise ErrorMEXC(f"MEXC respondió HTTP {codigo} en {ruta}: revisa símbolo e intervalo.") from e
                if i < self.intentos - 1:
                    self.dormir(2 ** i)          # espera creciente: 1, 2, 4 s
        raise ErrorMEXC(f"MEXC sin respuesta en {ruta} tras {self.intentos} intentos ({ultimo}). "
                        "Se pausan nuevas decisiones hasta que el servicio responda.")

    def hora_servidor_ms(self) -> int:
        return int(self._get("/api/v3/time")["serverTime"])

    def info_par(self, simbolo: str = SIMBOLO) -> InfoPar:
        d = self._get("/api/v3/exchangeInfo", {"symbol": simbolo})
        s = next((x for x in d.get("symbols", []) if x.get("symbol") == simbolo), None)
        if s is None:
            raise ErrorMEXC(f"MEXC no reporta el par {simbolo}.")
        return InfoPar(paso_cantidad=float(s.get("baseSizePrecision", "0.0001")),
                       decimales_precio=int(s.get("quotePrecision", 2)),
                       monto_minimo=float(s.get("quoteAmountPrecision", "1")))

    def precio_actual(self, simbolo: str = SIMBOLO) -> float:
        p = float(self._get("/api/v3/ticker/price", {"symbol": simbolo})["price"])
        if not (math.isfinite(p) and p > 0):
            raise ErrorMEXC(f"Precio actual no válido: {p}")
        return p

    def velas(self, simbolo: str = SIMBOLO, limite: int = 60, inicio_ms: int | None = None, fin_ms: int | None = None) -> list[Vela]:
        params = {"symbol": simbolo, "interval": INTERVALO, "limit": min(limite, 500)}
        if inicio_ms is not None:
            params["startTime"] = inicio_ms
            params["endTime"] = fin_ms if fin_ms is not None else inicio_ms + limite * DIA_MS - 1
        crudas = self._get("/api/v3/klines", params)
        if not isinstance(crudas, list) or not crudas:
            raise ErrorMEXC(f"MEXC devolvió velas vacías para {simbolo}.")
        return [Vela(int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]), int(k[6])) for k in crudas]


def separar_cerradas(velas: list[Vela], ahora_ms: int) -> tuple[list[Vela], Vela | None]:
    """Devuelve (velas cerradas, vela en curso). Una vela abierta nunca se trata como definitiva."""
    cerradas = [v for v in velas if v.close_time <= ahora_ms]
    abiertas = [v for v in velas if v.close_time > ahora_ms]
    return cerradas, (abiertas[-1] if abiertas else None)


def validar_velas(velas: list[Vela], minimo: int) -> None:
    if len(velas) < minimo:
        raise ErrorDatos(f"Se necesitan al menos {minimo} velas cerradas; se recibieron {len(velas)}.")
    tiempos = [v.open_time for v in velas]
    if len(set(tiempos)) != len(tiempos):
        raise ErrorDatos("Hay velas con fecha duplicada.")
    if tiempos != sorted(tiempos):
        raise ErrorDatos("Las velas no están en orden cronológico.")
    huecos = [velas[i].fecha for i in range(1, len(velas)) if tiempos[i] - tiempos[i - 1] != DIA_MS]
    if huecos:
        raise ErrorDatos(f"Faltan días antes de {huecos[:3]}: los rezagos requieren días consecutivos (no se rellena).")
    for v in velas:
        if v.open_time % DIA_MS != 0:
            raise ErrorDatos(f"La vela {v.fecha} no abre a las 00:00 UTC (convención distinta a la del entrenamiento).")
        for p in (v.open, v.high, v.low, v.close):
            if not math.isfinite(p) or p <= 0:
                raise ErrorDatos(f"Precio no válido en la vela {v.fecha}: {p}.")


def validar_antiguedad(ultima_cerrada: Vela, ahora_ms: int, max_horas: float) -> float:
    horas = (ahora_ms - ultima_cerrada.close_time) / 3_600_000
    if horas > max_horas:
        raise ErrorDatoAtrasado(f"La última vela cerrada ({ultima_cerrada.fecha}) tiene {horas:.1f} h de antigüedad; "
                                f"el máximo permitido es {max_horas} h. No se decide con datos atrasados.")
    return horas
