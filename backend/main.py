"""API del agente (FastAPI). Documentación interactiva en http://127.0.0.1:8000/docs

Lectura (sin clave): /api/salud, /api/panel, /api/decisiones, /api/patrimonio, /api/errores, /api/replays
Control (requiere header X-API-Key = BACKEND_API_KEY): iniciar, pausar, detener, liquidar, ciclo, replay
"""
import asyncio
import hmac
import json
import logging
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from agente import __version__
from agente.config import ORDENES_REALES_HABILITADAS, SIMBOLO, Ajustes, ConfigRiesgo
from agente.db import BaseDatos
from agente.llm import ClienteLLM
from agente.mexc import ClienteMEXC
from agente.modelo import ModeloPredictivo
from agente.nucleo import Agente

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("agente")


def construir(ajustes: Ajustes, mexc=None, llm=None, db=None) -> tuple[FastAPI, Agente]:
    ajustes.validar()
    assert ORDENES_REALES_HABILITADAS is False, "Este proyecto solo opera en modo Paper."
    cfg = ConfigRiesgo.desde_archivo(ajustes.ruta_riesgo)
    db = db or BaseDatos(ajustes.db_path)
    llm = llm if llm is not None else ClienteLLM(ajustes.gateway_url, ajustes.gateway_api_key, ajustes.ollama_modelo)
    agente = Agente(db, mexc or ClienteMEXC(), ModeloPredictivo(ajustes.dir_modelo), llm, cfg,
                    politica=ajustes.modo_decision, llm_modelo=ajustes.ollama_modelo)

    @asynccontextmanager
    async def ciclo_de_vida(app):
        async def programador():
            while True:
                if agente.estado == "en_marcha":
                    try:
                        r = await run_in_threadpool(agente.ciclo_vivo)
                        log.info("Ciclo: %s", r.get("resultado"))
                    except Exception as e:          # nunca debe tumbar el programador; queda registrado
                        log.exception("Error inesperado en el ciclo")
                        db.registrar_error("programador", type(e).__name__, str(e), "se pausa el agente")
                        agente.cambiar_estado("pausado", "error inesperado")
                await asyncio.sleep(ajustes.intervalo_revision_s)
        tarea = asyncio.create_task(programador())
        log.info("Agente v%s listo. Estado recuperado: %s | política: %s", __version__, agente.estado, agente.politica)
        yield
        tarea.cancel()

    app = FastAPI(title="Agente de trading ETH/USDT (Paper)", version=__version__, lifespan=ciclo_de_vida,
                  description="Agente que observa velas diarias de MEXC, consulta un modelo DL y un LLM local (Ollama), "
                              "aplica controles de riesgo y ejecuta SOLO en simulación (Paper).")
    app.add_middleware(CORSMiddleware, allow_origins=[ajustes.cors_origen], allow_methods=["GET", "POST"],
                       allow_headers=["X-API-Key", "Content-Type"])

    def requiere_clave(x_api_key: str = Header(default="")):
        if not x_api_key or not hmac.compare_digest(x_api_key.encode(), ajustes.backend_api_key.encode()):
            db.registrar_error("api", "clave_incorrecta", "Intento de control con API key ausente o incorrecta", "rechazado (401)")
            raise HTTPException(401, "API key ausente o incorrecta.")

    # ------------------------- lectura -------------------------
    @app.get("/api/salud")
    def salud():
        return {"version": __version__, "simbolo": SIMBOLO, "estado": agente.estado, "politica": agente.politica,
                "modelo_version": agente.modelo.version, "modelo_tipo": agente.modelo.tipo,
                "llm_modelo": ajustes.ollama_modelo, "ordenes_reales": ORDENES_REALES_HABILITADAS}

    @app.get("/api/panel")
    def panel():
        ult = db.filas("SELECT * FROM decisiones WHERE modo='vivo' ORDER BY fecha_vela DESC LIMIT 1")
        ult = ult[0] if ult else None
        if ult:
            ult["indicadores"] = json.loads(ult["indicadores"] or "{}")
            ult["propuesta"] = json.loads(ult["propuesta"]) if ult["propuesta"] else None
        precio = ult["precio_cierre"] if ult else None
        c = agente.cartera
        err = db.filas("SELECT * FROM errores WHERE tipo NOT IN ('cambio_estado') ORDER BY id DESC LIMIT 1")
        return {
            "version": __version__, "simbolo": SIMBOLO, "estado": agente.estado, "politica": agente.politica,
            "replay_en_curso": agente.replay_en_curso,
            "modelo": {"version": agente.modelo.version, "tipo": agente.modelo.tipo, "delta": agente.modelo.delta,
                       "procedencia": agente.modelo.contrato.get("procedencia", {})},
            "llm_modelo": ajustes.ollama_modelo, "ultima_decision": ult,
            "cartera": {"efectivo": c.efectivo, "unidades": c.unidades, "en_posicion": c.en_posicion,
                        "precio_valoracion": precio, "patrimonio": c.patrimonio(precio) if precio else c.efectivo,
                        "capital_inicial": agente.cfg.capital_inicial},
            "riesgo": agente.cfg.__dict__, "ultimo_ciclo": db.leer_estado("ultimo_ciclo"),
            "ultimo_error": err[0] if err else None,
        }

    @app.get("/api/decisiones")
    def decisiones(modo: str = "vivo", corrida: str = "vivo", limite: int = Query(50, le=500)):
        filas = db.filas("SELECT * FROM decisiones WHERE modo=? AND corrida=? ORDER BY fecha_vela DESC, politica LIMIT ?",
                         (modo, corrida, limite))
        for f in filas:
            f["indicadores"] = json.loads(f["indicadores"] or "{}")
            f["propuesta"] = json.loads(f["propuesta"]) if f["propuesta"] else None
        return filas

    @app.get("/api/ordenes")
    def ordenes(modo: str = "vivo", corrida: str = "vivo", limite: int = Query(100, le=1000)):
        return db.filas("SELECT * FROM ordenes WHERE modo=? AND corrida=? ORDER BY id DESC LIMIT ?", (modo, corrida, limite))

    @app.get("/api/patrimonio")
    def patrimonio(modo: str = "vivo", corrida: str = "vivo"):
        return db.filas("SELECT * FROM patrimonio WHERE modo=? AND corrida=? ORDER BY fecha, politica", (modo, corrida))

    @app.get("/api/errores")
    def errores(limite: int = Query(50, le=500)):
        return db.filas("SELECT * FROM errores ORDER BY id DESC LIMIT ?", (limite,))

    @app.get("/api/replays")
    def replays():
        ids = [r["corrida"] for r in db.filas("SELECT DISTINCT corrida FROM decisiones WHERE modo='replay' ORDER BY corrida DESC")]
        return [db.leer_estado(f"resumen_{i}") or {"corrida": i, "estado": "en curso o incompleto"} for i in ids]

    # ------------------------- control (protegido) -------------------------
    @app.post("/api/control/iniciar", dependencies=[Depends(requiere_clave)])
    def iniciar():
        return {"estado": agente.cambiar_estado("en_marcha", "usuario pulsó iniciar")}

    @app.post("/api/control/pausar", dependencies=[Depends(requiere_clave)])
    def pausar():
        return {"estado": agente.cambiar_estado("pausado", "usuario pulsó pausar")}

    @app.post("/api/control/detener", dependencies=[Depends(requiere_clave)])
    def detener():
        estado = agente.cambiar_estado("detenido", "usuario pulsó detener (no liquida la posición)")
        r = {"estado": estado, "posicion_abierta": agente.cartera.en_posicion, "unidades": agente.cartera.unidades}
        if agente.cfg.liquidar_al_detener:
            r["liquidacion"] = agente.liquidar()
        return r

    @app.post("/api/control/liquidar", dependencies=[Depends(requiere_clave)])
    def liquidar():
        return agente.liquidar()

    @app.post("/api/control/ciclo", dependencies=[Depends(requiere_clave)])
    def ciclo():
        """Ejecuta un ciclo ahora (solo si el agente está en marcha). Si no hay vela nueva, no opera."""
        return agente.ciclo_vivo()

    class PeticionReplay(BaseModel):
        ciclos: int = Field(30, ge=20, le=200, description="Número de velas a repetir (mínimo 20 ciclos)")
        incluir_llm: bool = True

    @app.post("/api/control/replay", dependencies=[Depends(requiere_clave)], status_code=202)
    def replay(p: PeticionReplay, tareas: BackgroundTasks):
        if agente.replay_en_curso:
            raise HTTPException(409, "Ya hay un replay en curso.")
        politicas = ("regla", "llm") if p.incluir_llm else ("regla",)
        tareas.add_task(agente.replay, p.ciclos, politicas)
        return {"mensaje": f"Replay de {p.ciclos} velas iniciado (políticas: {', '.join(politicas)} + comprar y mantener)."}

    return app, agente


def crear() -> FastAPI:
    """Fábrica para uvicorn:  uvicorn main:crear --factory --host 127.0.0.1 --port 8000"""
    return construir(Ajustes())[0]
