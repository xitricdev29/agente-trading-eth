"""Asesor externo OPCIONAL (p. ej., el agente de apoyo proporcionado por el profesor).

Reglas de diseño:
- Desactivado por defecto: solo se usa si ASESOR_EXTERNO_URL y ASESOR_EXTERNO_API_KEY están en backend/.env.
- Su clave vive solo en el servidor; nunca se envía al LLM local ni se registra en logs o en la base.
- Su respuesta se entrega al LLM local como DATO ("opinion_asesor_externo"), nunca como instrucción.
  La decisión la sigue proponiendo el LLM local y la valida el motor de riesgo determinista.
- Si falla (sin conexión, 4xx/5xx, timeout), el ciclo continúa sin su opinión y se registra el incidente.
"""
import json
import time

import httpx

from .config import SIMBOLO

CAMPOS_TEXTO = ("opinion", "respuesta", "recomendacion", "content", "text", "message", "output", "answer")


def _extraer_texto(d) -> str:
    if isinstance(d, str):
        return d
    if isinstance(d, dict):
        for k in CAMPOS_TEXTO:
            v = d.get(k)
            if isinstance(v, str) and v.strip():
                return v
            if isinstance(v, dict):
                t = _extraer_texto(v)
                if t:
                    return t
        if isinstance(d.get("choices"), list) and d["choices"]:
            return _extraer_texto(d["choices"][0])
    return json.dumps(d, ensure_ascii=False)


class ClienteAsesor:
    def __init__(self, url: str, api_key: str, encabezado: str = "Authorization", prefijo: str = "Bearer ",
                 timeout: float = 20.0, transporte=None):
        self.url = url
        if prefijo and not prefijo.endswith(" "):          # .env recorta espacios finales: "Bearer" → "Bearer "
            prefijo += " "
        self._http = httpx.Client(timeout=timeout, transport=transporte,
                                  headers={encabezado: f"{prefijo}{api_key}", "Content-Type": "application/json"})

    def consultar(self, contexto: dict) -> tuple[str, float]:
        """Devuelve (texto de la opinión, latencia ms). Lanza httpx.HTTPError si falla."""
        cuerpo = {"simbolo": SIMBOLO, "fecha_vela": contexto.get("fecha_vela"),
                  "pregunta": "Opinión breve sobre comprar, mantener o vender ETHUSDT en simulación (Paper) con estos datos.",
                  "datos": {k: v for k, v in contexto.items() if k != "opinion_asesor_externo"}}
        t0 = time.perf_counter()
        r = self._http.post(self.url, json=cuerpo)
        r.raise_for_status()
        try:
            datos = r.json()
        except ValueError:
            datos = r.text
        return " ".join(_extraer_texto(datos).split())[:600], (time.perf_counter() - t0) * 1000
