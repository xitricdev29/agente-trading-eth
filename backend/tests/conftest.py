import json
import math
import sys
from pathlib import Path

import httpx
import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from agente.config import ConfigRiesgo  # noqa: E402
from agente.db import BaseDatos  # noqa: E402
from agente.llm import ClienteLLM  # noqa: E402
from agente.mexc import DIA_MS, InfoPar, Vela  # noqa: E402
from agente.modelo import ModeloPredictivo  # noqa: E402
from agente.nucleo import Agente  # noqa: E402

AHORA = 1_794_000_000_000 // DIA_MS * DIA_MS + 3_600_000      # 01:00 UTC de un día


def generar_velas(n_cerradas=40, ahora=AHORA, base=2000.0):
    """n velas diarias cerradas + 1 en curso. La última cerrada cerró hace 1 h."""
    hoy = ahora // DIA_MS * DIA_MS
    velas, precio = [], base
    for i in range(n_cerradas + 1):
        t = hoy - (n_cerradas - i) * DIA_MS
        cierre = precio * (1 + 0.02 * math.sin(i * 1.3))
        velas.append(Vela(t, precio, max(precio, cierre) * 1.01, min(precio, cierre) * 0.99, cierre, 1000.0, t + DIA_MS))
        precio = cierre
    return velas


class MEXCFalso:
    def __init__(self, velas=None, falla=None):
        self._velas = velas if velas is not None else generar_velas()
        self.falla = falla
        self.llamadas = 0

    def velas(self, limite=60, **_):
        self.llamadas += 1
        if self.falla:
            raise self.falla
        return self._velas[-limite:]

    def info_par(self):
        return InfoPar()

    def precio_actual(self):
        return self._velas[-1].open


def llm_con_respuesta(respuesta, estado=200):
    """ClienteLLM cuyo gateway devuelve `respuesta` (str o función contexto→str)."""
    def manejar(request: httpx.Request):
        cuerpo = json.loads(request.content)
        ctx = json.loads(cuerpo["messages"][1]["content"])
        contenido = respuesta(ctx) if callable(respuesta) else respuesta
        return httpx.Response(estado, json={"message": {"role": "assistant", "content": contenido}})
    return ClienteLLM("http://gateway", "k" * 32, "modelo-prueba", transporte=httpx.MockTransport(manejar), dormir=lambda s: None)


def respuesta_valida(accion):
    return lambda ctx: json.dumps({"accion": accion, "simbolo": "ETHUSDT", "fecha_vela": ctx["fecha_vela"],
                                   "justificacion": "prueba"})


@pytest.fixture
def cfg():
    return ConfigRiesgo.desde_archivo(RAIZ / "config" / "riesgo.json")


@pytest.fixture
def modelo():
    return ModeloPredictivo(RAIZ / "modelo")


@pytest.fixture
def crear_agente(cfg, modelo, tmp_path):
    def _crear(mexc=None, llm=None, politica="llm", db_path=None, estado="en_marcha", cfg_=None):
        db = BaseDatos(str(db_path or tmp_path / "agente.db"))
        ag = Agente(db, mexc or MEXCFalso(), modelo, llm, cfg_ or cfg, politica=politica, llm_modelo="modelo-prueba",
                    reloj=lambda: AHORA)
        if estado and ag.estado != estado:
            ag.cambiar_estado(estado, "prueba")
        return ag
    return _crear
