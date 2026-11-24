"""Controles deterministas, motor Paper, modelo, gateway y bloqueo de operaciones reales."""
import inspect
import json
from pathlib import Path

import httpx
import numpy as np
import pytest
import torch
from fastapi.testclient import TestClient

from agente import mexc as modulo_mexc
from agente import paper, riesgo
from agente.config import ORDENES_REALES_HABILITADAS, ConfigRiesgo
from agente.errores import ErrorDatos, ErrorSaldoInsuficiente
from agente.mexc import DIA_MS, InfoPar, separar_cerradas, validar_velas
from agente.modelo import MLP
from agente.riesgo import Cartera
from conftest import AHORA, RAIZ, MEXCFalso, generar_velas, llm_con_respuesta, respuesta_valida

PAR = InfoPar()


# ---------------- riesgo ----------------
def test_no_recompra_ni_ventas_en_corto(cfg):
    con_pos, sin_pos = Cartera(efectivo=0, unidades=1.0), Cartera(efectivo=50_000)
    assert riesgo.evaluar({"accion": "comprar"}, con_pos, 2000, cfg, "en_marcha").aprobada is False
    assert riesgo.evaluar({"accion": "vender"}, sin_pos, 2000, cfg, "en_marcha").aprobada is False
    assert riesgo.evaluar({"accion": "comprar"}, sin_pos, 2000, cfg, "en_marcha").aprobada is True


@pytest.mark.parametrize("estado", ["detenido", "pausado", "bloqueado"])
def test_estado_inactivo_bloquea_ordenes(cfg, estado):
    v = riesgo.evaluar({"accion": "comprar"}, Cartera(efectivo=50_000), 2000, cfg, estado)
    assert v.aprobada is False and v.accion_final == "ninguna"


def test_perdida_diaria_maxima_bloquea_al_agente(crear_agente, cfg):
    ag = crear_agente(llm=llm_con_respuesta(respuesta_valida("mantener")))
    ag.cartera = Cartera(efectivo=0, unidades=25.0, patrimonio_referencia=25.0 * 10_000)   # caída enorme vs referencia
    r = ag.ciclo_vivo()
    assert r["aprobada"] is False and "Pérdida diaria" in r["motivo"] and ag.estado == "bloqueado"


def test_exposicion_maxima_respetada(cfg):
    c = Cartera(efectivo=50_000)
    o = paper.ejecutar("comprar", 2000.0, c, cfg, PAR)
    assert o.monto <= cfg.exposicion_maxima * 50_000 + 1e-6
    assert c.efectivo >= 50_000 * (1 - cfg.exposicion_maxima) - 1e-6


def test_config_rechaza_apalancamiento(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"exposicion_maxima": 2.0}))
    with pytest.raises(ValueError, match="apalancamiento"):
        ConfigRiesgo.desde_archivo(p)


# ---------------- Paper ----------------
def test_paper_precision_deslizamiento_y_costos():
    cfg = ConfigRiesgo(costo=0.001, deslizamiento=0.001, exposicion_maxima=1.0)
    c = Cartera(efectivo=10_000)
    o = paper.ejecutar("comprar", 1999.99, c, cfg, PAR)
    assert o.precio_ejecucion == round(1999.99 * 1.001, 2)
    assert abs(o.cantidad / 0.0001 - round(o.cantidad / 0.0001)) < 1e-6        # múltiplo del paso del par
    assert abs(o.costo - o.monto * 0.001) < 1e-9 and c.efectivo >= 0
    v = paper.ejecutar("vender", 2100.0, c, cfg, PAR)
    assert v.precio_ejecucion == round(2100 * 0.999, 2) and c.unidades == 0


def test_paper_saldo_insuficiente():
    with pytest.raises(ErrorSaldoInsuficiente):
        paper.ejecutar("comprar", 2000.0, Cartera(efectivo=0.5), ConfigRiesgo(), PAR)


# ---------------- datos MEXC ----------------
def test_vela_abierta_nunca_es_definitiva():
    velas = generar_velas()
    cerradas, en_curso = separar_cerradas(velas, AHORA)
    assert en_curso is velas[-1] and all(v.close_time <= AHORA for v in cerradas)


def test_validacion_de_velas_detecta_desorden_duplicados_y_huecos():
    v = generar_velas()[:25]
    with pytest.raises(ErrorDatos, match="orden"):
        validar_velas(list(reversed(v)), 21)
    with pytest.raises(ErrorDatos, match="duplicada"):
        validar_velas(v + [v[-1]], 21)
    with pytest.raises(ErrorDatos, match="Faltan días"):
        validar_velas(v[:10] + v[11:], 21)


# ---------------- modelo ----------------
def test_variables_coinciden_con_el_entrenamiento(modelo):
    cierres = [100.0 * (1.01 ** i) for i in range(25)]
    v = modelo.variables(cierres)
    assert abs(v["lags"]["lag_1"] - (cierres[-1] / cierres[-2] - 1)) < 1e-12
    assert abs(v["lags"]["lag_20"] - (cierres[-20] / cierres[-21] - 1)) < 1e-12


def test_pronostico_reproduce_el_modelo_exportado(modelo):
    c = modelo.contrato
    lags = {f"lag_{k}": 0.001 * ((-1) ** k) * k for k in range(1, 21)}
    x = (np.array([lags[f"lag_{k}"] for k in range(1, 21)]) - c["escalador_x"]["media"]) / c["escalador_x"]["escala"]
    preds = []
    for s in c["semillas"]:
        red = MLP(20, c["neuronas"], 0.2)
        red.load_state_dict(torch.load(RAIZ / "modelo" / f"pesos_s{s}.pt", weights_only=True)); red.eval()
        with torch.no_grad():
            preds.append(float(red(torch.tensor(x[None, :], dtype=torch.float32))[0]))
    esperado = np.mean(preds) * c["escalador_y"]["escala"] + c["escalador_y"]["media"]
    assert abs(modelo.pronosticar(lags) - esperado) < 1e-7


# ---------------- gateway de Ollama ----------------
def test_gateway_exige_api_key_y_limita_rutas():
    from gateway import crear_app
    ollama = httpx.MockTransport(lambda r: httpx.Response(200, json={"message": {"content": "{}"}}))
    app = crear_app(clave="g" * 32, transporte=ollama)
    with TestClient(app) as cli:
        assert cli.post("/api/chat", json={}).status_code == 401
        assert cli.post("/api/chat", json={}, headers={"Authorization": "Bearer mala"}).status_code == 401
        ok = {"Authorization": "Bearer " + "g" * 32}
        assert cli.post("/api/chat", json={}, headers=ok).status_code == 200
        assert cli.post("/api/pull", json={}, headers=ok).status_code == 403        # no se permite descargar/borrar modelos
        assert cli.post("/api/delete", json={}, headers=ok).status_code == 403


def test_gateway_reporta_ollama_detenido():
    from gateway import crear_app
    def caido(r):
        raise httpx.ConnectError("rechazada")
    with TestClient(crear_app(clave="g" * 32, transporte=httpx.MockTransport(caido))) as cli:
        assert cli.post("/api/chat", json={}, headers={"Authorization": "Bearer " + "g" * 32}).status_code == 502


def test_el_llm_no_recibe_secretos(crear_agente):
    vistos = []
    def capturar(ctx):
        vistos.append(json.dumps(ctx))
        return respuesta_valida("mantener")(ctx)
    ag = crear_agente(llm=llm_con_respuesta(capturar))
    ag.ciclo_vivo()
    texto = vistos[0].lower()
    assert all(p not in texto for p in ("api_key", "apikey", "secret", "password", "token", ".env"))


# ---------------- bloqueo de operaciones reales ----------------
def test_no_existen_rutas_de_ordenes_reales():
    assert ORDENES_REALES_HABILITADAS is False
    fuente = inspect.getsource(modulo_mexc)
    assert "/api/v3/order" not in fuente and ".post(" not in fuente and "X-MEXC-APIKEY" not in fuente


# ---------------- replay y comparación ----------------
def test_replay_etiquetado_y_separado_de_vivo(crear_agente):
    ag = crear_agente(mexc=MEXCFalso(generar_velas(n_cerradas=60)), llm=llm_con_respuesta(respuesta_valida("mantener")))
    r = ag.replay(20)
    assert set(r["politicas"]) == {"regla", "llm", "comprar_y_mantener"}
    assert r["politicas"]["llm"]["decisiones"] == 20 and r["politicas"]["regla"]["decisiones"] == 20
    assert ag.db.filas("SELECT COUNT(*) n FROM decisiones WHERE modo='vivo'")[0]["n"] == 0
    assert ag.cartera.efectivo == ag.cfg.capital_inicial        # la cartera en vivo no se toca


# ---------------- v0.1.1: el LLM solo puede elegir acciones válidas ----------------
def test_esquema_restringe_acciones_segun_posicion(crear_agente):
    cuerpos = []
    def manejar(req):
        cuerpos.append(json.loads(req.content))
        ctx = json.loads(cuerpos[-1]["messages"][1]["content"])
        return httpx.Response(200, json={"message": {"content": respuesta_valida("comprar")(ctx)}})
    from agente.llm import ClienteLLM
    llm = ClienteLLM("http://gateway", "k" * 32, "m", transporte=httpx.MockTransport(manejar), dormir=lambda s: None)
    ag = crear_agente(llm=llm)
    ag.ciclo_vivo()                                            # sin posición → compra
    cuerpos[:] = [c for c in cuerpos if "format" in c]
    assert cuerpos[0]["format"]["properties"]["accion"]["enum"] == ["mantener", "comprar"]
    ctx = json.loads(cuerpos[0]["messages"][1]["content"])
    assert ctx["acciones_validas"] == ["mantener", "comprar"]
    ag.mexc._velas = generar_velas(ahora=AHORA + DIA_MS)        # vela nueva con posición abierta
    ag.reloj = lambda: AHORA + DIA_MS
    ag.ciclo_vivo()
    cuerpos[:] = [c for c in cuerpos if "format" in c]
    assert cuerpos[1]["format"]["properties"]["accion"]["enum"] == ["mantener", "vender"]
    assert cuerpos[1]["format"]["properties"]["fecha_vela"]["enum"] == [json.loads(cuerpos[1]["messages"][1]["content"])["fecha_vela"]]


# ---------------- v0.1.2: contexto sin doble negación y razonamiento antes de la acción ----------------
def test_contexto_claro_y_justificacion_antes_de_la_accion(crear_agente):
    cuerpos = []
    def manejar(req):
        cuerpos.append(json.loads(req.content))
        ctx = json.loads(cuerpos[-1]["messages"][1]["content"])
        return httpx.Response(200, json={"message": {"content": respuesta_valida("mantener")(ctx)}})
    from agente.llm import ClienteLLM
    ag = crear_agente(llm=ClienteLLM("http://g", "k" * 32, "m", transporte=httpx.MockTransport(manejar), dormir=lambda s: None))
    ag.ciclo_vivo()
    cuerpos[:] = [c for c in cuerpos if "format" in c]
    ctx = json.loads(cuerpos[0]["messages"][1]["content"])
    assert ctx["situacion"].startswith("EN EFECTIVO") and "posicion_abierta" not in ctx
    props = list(cuerpos[0]["format"]["properties"])
    assert props.index("justificacion") < props.index("accion")
