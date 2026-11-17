"""Los nueve escenarios de error del protocolo (sección 15). En todos: mensaje útil, registro y ninguna operación indebida."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from agente.config import Ajustes
from agente.errores import ErrorMEXC
from agente.mexc import DIA_MS, ClienteMEXC
from conftest import AHORA, MEXCFalso, generar_velas, llm_con_respuesta, respuesta_valida


def n_ordenes(ag):
    return ag.db.filas("SELECT COUNT(*) n FROM ordenes")[0]["n"]


def errores(ag, tipo):
    return ag.db.filas("SELECT * FROM errores WHERE tipo=?", (tipo,))


# 1a. API sin respuesta
def test_api_mexc_sin_respuesta_pausa_y_no_opera(crear_agente):
    ag = crear_agente(mexc=MEXCFalso(falla=ErrorMEXC("MEXC sin respuesta (prueba)")), llm=llm_con_respuesta(respuesta_valida("comprar")))
    r = ag.ciclo_vivo()
    assert r["tipo"] == "mexc_sin_respuesta" and ag.estado == "pausado"
    assert n_ordenes(ag) == 0 and errores(ag, "mexc_sin_respuesta")


# 1b. Límite de solicitudes: reintentos limitados con espera creciente
def test_mexc_limite_de_solicitudes_reintenta_con_espera_creciente():
    esperas, intentos = [], {"n": 0}

    def manejar(req):
        intentos["n"] += 1
        return httpx.Response(429, json={"msg": "too many requests"})

    c = ClienteMEXC(transporte=httpx.MockTransport(manejar), dormir=esperas.append, intentos=4)
    with pytest.raises(ErrorMEXC, match="tras 4 intentos"):
        c.velas()
    assert intentos["n"] == 4 and esperas == [1, 2, 4]


def test_mexc_se_recupera_si_responde_en_reintento():
    respuestas = iter([httpx.Response(503), httpx.Response(200, json=[[0, "1", "1", "1", "1", "1", DIA_MS, "1"]])])
    c = ClienteMEXC(transporte=httpx.MockTransport(lambda r: next(respuestas)), dormir=lambda s: None)
    assert len(c.velas()) == 1


# 2. Dato atrasado
def test_dato_atrasado_no_decide(crear_agente):
    velas = generar_velas(ahora=AHORA - 3 * DIA_MS)          # la última vela cerrada tiene ~3 días
    ag = crear_agente(mexc=MEXCFalso(velas), llm=llm_con_respuesta(respuesta_valida("comprar")))
    r = ag.ciclo_vivo()
    assert r["tipo"] in ("dato_atrasado", "datos_invalidos") and n_ordenes(ag) == 0 and ag.estado == "pausado"


# 3. Ollama detenido
def test_ollama_detenido_pausa_y_no_opera(crear_agente):
    def caido(req):
        raise httpx.ConnectError("conexión rechazada")
    from agente.llm import ClienteLLM
    llm = ClienteLLM("http://gateway", "k" * 32, "m", transporte=httpx.MockTransport(caido), dormir=lambda s: None)
    ag = crear_agente(llm=llm)
    r = ag.ciclo_vivo()
    assert r["tipo"] == "ollama_detenido" and ag.estado == "pausado" and n_ordenes(ag) == 0
    assert "Verifica que Ollama" in r["mensaje"]


# 4. JSON inválido
@pytest.mark.parametrize("respuesta", [
    "esto no es json",
    json.dumps({"accion": "apalancar", "simbolo": "ETHUSDT", "fecha_vela": "x", "justificacion": "y"}),
    json.dumps({"accion": "comprar", "simbolo": "BTCUSDT", "fecha_vela": "x", "justificacion": "y"}),
    json.dumps({"accion": "comprar", "simbolo": "ETHUSDT"}),
])
def test_json_invalido_se_rechaza_sin_operar(crear_agente, respuesta):
    ag = crear_agente(llm=llm_con_respuesta(respuesta))
    r = ag.ciclo_vivo()
    assert r["resultado"] == "decision" and r["aprobada"] is False and r["accion_final"] == "ninguna"
    assert n_ordenes(ag) == 0 and errores(ag, "json_invalido")


def test_fecha_de_vela_equivocada_se_rechaza(crear_agente):
    ag = crear_agente(llm=llm_con_respuesta(json.dumps({"accion": "comprar", "simbolo": "ETHUSDT",
                                                        "fecha_vela": "1999-01-01", "justificacion": "x"})))
    assert ag.ciclo_vivo()["aprobada"] is False and n_ordenes(ag) == 0


# 5. Clave incorrecta (backend)
def test_clave_incorrecta_rechazada(tmp_path, monkeypatch, cfg, modelo):
    from main import construir
    monkeypatch.setenv("DB_PATH", str(tmp_path / "api.db"))
    aj = Ajustes(backend_api_key="b" * 32, gateway_api_key="g" * 32, db_path=str(tmp_path / "api.db"), intervalo_revision_s=3600)
    app, ag = construir(aj, mexc=MEXCFalso(), llm=llm_con_respuesta(respuesta_valida("mantener")))
    with TestClient(app) as cli:
        assert cli.post("/api/control/iniciar").status_code == 401
        assert cli.post("/api/control/iniciar", headers={"X-API-Key": "incorrecta"}).status_code == 401
        assert cli.post("/api/control/replay", json={"ciclos": 20}, headers={"X-API-Key": "x"}).status_code == 401
        assert ag.estado == "detenido"
        assert cli.post("/api/control/iniciar", headers={"X-API-Key": "b" * 32}).json()["estado"] == "en_marcha"
        assert cli.get("/api/panel").status_code == 200
    assert len(errores(ag, "clave_incorrecta")) == 3


# 6. Saldo insuficiente
def test_saldo_insuficiente_rechaza_orden(crear_agente):
    ag = crear_agente(llm=llm_con_respuesta(respuesta_valida("comprar")))
    ag.cartera.efectivo = 0.5                              # menos que el monto mínimo de 1 USDT
    r = ag.ciclo_vivo()
    assert r["aprobada"] is False and "Saldo insuficiente" in r["motivo"]
    assert n_ordenes(ag) == 0 and errores(ag, "saldo_insuficiente")


# 7. Evento duplicado
def test_evento_duplicado_no_opera_dos_veces(crear_agente):
    ag = crear_agente(llm=llm_con_respuesta(respuesta_valida("comprar")))
    assert ag.ciclo_vivo()["orden"] is not None
    assert ag.ciclo_vivo()["resultado"] == "sin_vela_nueva"
    # Aunque el evento se reenvíe directo al decisor, se detecta y registra
    v = ag.mexc._velas
    r = ag._decidir("vivo", "vivo", "llm", v[-22:-1], v[-1].open, v[-1].fecha, ag.cartera, "en_marcha")
    assert r["resultado"] == "duplicado" and errores(ag, "evento_duplicado")
    assert n_ordenes(ag) == 1


# 8. Reinicio del backend
def test_reinicio_recupera_estado_sin_duplicar(crear_agente, tmp_path):
    ruta = tmp_path / "persistente.db"
    ag1 = crear_agente(llm=llm_con_respuesta(respuesta_valida("comprar")), db_path=ruta)
    ag1.ciclo_vivo()
    efectivo, unidades = ag1.cartera.efectivo, ag1.cartera.unidades
    assert unidades > 0
    ag2 = crear_agente(llm=llm_con_respuesta(respuesta_valida("comprar")), db_path=ruta, estado=None)  # "reinicio"
    assert ag2.estado == "en_marcha"
    assert (ag2.cartera.efectivo, ag2.cartera.unidades) == (efectivo, unidades)
    assert ag2.ciclo_vivo()["resultado"] == "sin_vela_nueva"
    assert n_ordenes(ag2) == 1


# 9. Botón de detención
def test_detener_bloquea_ordenes_y_no_liquida(crear_agente):
    ag = crear_agente(llm=llm_con_respuesta(respuesta_valida("comprar")))
    ag.ciclo_vivo()
    unidades = ag.cartera.unidades
    ag.cambiar_estado("detenido", "usuario pulsó detener")
    ag.mexc._velas = generar_velas(ahora=AHORA + DIA_MS)        # llega una vela nueva
    r = ag.ciclo_vivo()
    assert r["resultado"] == "inactivo" and n_ordenes(ag) == 1
    assert ag.cartera.unidades == unidades                       # detener no liquida
    assert ag.liquidar()["resultado"] == "liquidado" and ag.cartera.unidades == 0
