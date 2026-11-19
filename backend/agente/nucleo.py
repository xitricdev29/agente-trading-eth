"""Ciclo del agente (protocolo, sección 15):

1. Consultar velas cerradas y validar activo, frecuencia, orden y antigüedad.
2. Calcular variables con la definición del entrenamiento y obtener el pronóstico.
3. Consultar al LLM (Ollama local vía gateway) para proponer comprar / mantener / vender.
4. Validar la respuesta JSON (acción, símbolo, fecha de vela, justificación).
5. Aplicar controles deterministas de riesgo.
6. Ejecutar en Paper, guardar la decisión y actualizar efectivo, posición y patrimonio (sin repetir velas).
7. Exponer el resultado a la UI y esperar la siguiente vela.

Modos:
- vivo:   una decisión por vela diaria nueva de MEXC.
- replay: repetición histórica ETIQUETADA como replay, con su propia cartera; no se mezcla con vivo.
"""
import datetime as dt
import json
import threading
import time

import numpy as np

from . import paper, riesgo
from .config import SIMBOLO, ConfigRiesgo
from .db import BaseDatos, ahora
from .errores import (ErrorAgente, ErrorDatoAtrasado, ErrorDatos, ErrorMEXC, ErrorOllama, ErrorRespuestaLLM,
                      ErrorSaldoInsuficiente)
from .llm import ClienteLLM, propuesta_regla
from .mexc import DIA_MS, ClienteMEXC, InfoPar, separar_cerradas, validar_antiguedad, validar_velas
from .modelo import N_REZAGOS, ModeloPredictivo
from .riesgo import Cartera, Veredicto

ESTADOS = ("detenido", "en_marcha", "pausado", "bloqueado")


class Agente:
    def __init__(self, db: BaseDatos, mexc: ClienteMEXC, modelo: ModeloPredictivo, llm: ClienteLLM | None,
                 cfg: ConfigRiesgo, politica: str = "llm", llm_modelo: str = "", reloj=lambda: int(time.time() * 1000)):
        self.db, self.mexc, self.modelo, self.llm, self.cfg = db, mexc, modelo, llm, cfg
        self.politica, self.llm_modelo, self.reloj = politica, llm_modelo, reloj
        self.candado = threading.RLock()
        self._par: InfoPar | None = None
        # Recuperación tras reinicio: estado y cartera persistidos
        self.estado = db.leer_estado("estado_agente", "detenido")
        c = db.leer_estado("cartera_vivo")
        self.cartera = Cartera(**c) if c else Cartera(efectivo=cfg.capital_inicial)
        self.replay_en_curso = False

    # ---------------- utilidades ----------------
    @property
    def par(self) -> InfoPar:
        if self._par is None:
            try:
                self._par = self.mexc.info_par()
            except ErrorMEXC as e:
                self.db.registrar_error("info_par", e.tipo, str(e), "se usan valores por defecto del par (0.0001 ETH, 2 decimales, 1 USDT)")
                self._par = InfoPar()
        return self._par

    def _guardar_cartera(self):
        self.db.guardar_estado("cartera_vivo", self.cartera.__dict__)

    def cambiar_estado(self, nuevo: str, motivo: str) -> str:
        if nuevo not in ESTADOS:
            raise ValueError(nuevo)
        with self.candado:
            anterior, self.estado = self.estado, nuevo
            self.db.guardar_estado("estado_agente", nuevo)
            self.db.registrar_error("control", "cambio_estado", f"{anterior} → {nuevo}: {motivo}", "registrado")
        return nuevo

    def _fallo(self, etapa: str, e: ErrorAgente, pausar: bool = True) -> dict:
        accion = "se pausan nuevas decisiones" if pausar else "sin operación"
        self.db.registrar_error(etapa, e.tipo, str(e), accion)
        if pausar and self.estado == "en_marcha":
            self.cambiar_estado("pausado", f"{e.tipo}: {e}")
        return {"resultado": "error", "tipo": e.tipo, "mensaje": str(e), "accion": accion}

    # ---------------- decisión común a vivo y replay ----------------
    def _decidir(self, modo: str, corrida: str, politica: str, ventana: list, precio_ej: float, fecha_ej: str,
                 cartera: Cartera, estado_eval: str) -> dict:
        ultima = ventana[-1]
        fecha, cierre = ultima.fecha, ultima.close
        if self.db.ya_procesada(modo, corrida, politica, fecha):
            self.db.registrar_error("ciclo", "evento_duplicado", f"La vela {fecha} ya fue procesada ({modo}/{politica}).",
                                    "se ignora; no se opera dos veces")
            return {"resultado": "duplicado", "fecha_vela": fecha}

        v = self.modelo.variables([x.close for x in ventana])
        pron = self.modelo.pronosticar(v["lags"])
        senal = self.modelo.senal_regla(pron)
        patrimonio_cierre = cartera.patrimonio(cierre)
        contexto = {
            "simbolo": SIMBOLO, "fecha_vela": fecha, "precio_cierre": round(cierre, 2),
            "pronostico_rendimiento_dia_siguiente": round(pron, 6), "umbral_delta": self.modelo.delta,
            "senal_regla": senal, "indicadores": {k: round(x, 6) for k, x in v["indicadores"].items()},
            "posicion_abierta": cartera.en_posicion, "unidades_eth": round(cartera.unidades, 6),
            "efectivo_usdt": round(cartera.efectivo, 2), "patrimonio_usdt": round(patrimonio_cierre, 2),
            # Acciones con efecto real en el estado actual: el LLM solo puede elegir entre estas
            "acciones_validas": ["mantener", "vender"] if cartera.en_posicion else ["mantener", "comprar"],
        }

        latencia, propuesta, error_prop = None, None, None
        if politica == "llm":
            try:
                propuesta, latencia = self.llm.proponer(contexto)
            except ErrorRespuestaLLM as e:
                error_prop = e
                self.db.registrar_error("llm", e.tipo, str(e), "propuesta rechazada; sin operación en esta vela")
        else:
            propuesta = propuesta_regla(senal, cartera.en_posicion, fecha, pron, self.modelo.delta)

        if propuesta is None:
            ver = Veredicto(False, "ninguna", f"Propuesta rechazada: {error_prop}")
        else:
            ver = riesgo.evaluar(propuesta, cartera, cierre, self.cfg, estado_eval)

        orden = None
        if ver.aprobada and ver.accion_final in ("comprar", "vender"):
            try:
                orden = paper.ejecutar(ver.accion_final, precio_ej, cartera, self.cfg, self.par)
            except ErrorSaldoInsuficiente as e:
                self.db.registrar_error("paper", e.tipo, str(e), "orden rechazada; sin operación")
                ver = Veredicto(False, "ninguna", str(e))
        cartera.patrimonio_referencia = patrimonio_cierre

        with self.db.transaccion() as con:
            cur = con.execute(
                """INSERT INTO decisiones(modo, corrida, politica, fecha_vela, precio_cierre, pronostico, senal_regla,
                   indicadores, propuesta, accion_propuesta, accion_final, aprobada, motivo_riesgo, latencia_llm_ms,
                   modelo_version, llm_modelo, creado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (modo, corrida, politica, fecha, cierre, pron, senal, json.dumps(v["indicadores"]),
                 json.dumps(propuesta, ensure_ascii=False) if propuesta else None,
                 propuesta["accion"] if propuesta else None, ver.accion_final, int(ver.aprobada), ver.motivo,
                 latencia, self.modelo.version, self.llm_modelo if politica == "llm" else None, ahora()))
            if orden:
                con.execute(
                    """INSERT INTO ordenes(decision_id, modo, corrida, politica, fecha_vela, fecha_ejecucion, lado, cantidad,
                       precio_referencia, precio_ejecucion, monto, costo, efectivo_despues, unidades_despues, creado_en)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (cur.lastrowid, modo, corrida, politica, fecha, fecha_ej, orden.lado, orden.cantidad, orden.precio_referencia,
                     orden.precio_ejecucion, orden.monto, orden.costo, cartera.efectivo, cartera.unidades, ahora()))
            con.execute("INSERT OR REPLACE INTO patrimonio VALUES (?,?,?,?,?,?,?,?)",
                        (modo, corrida, politica, fecha, cartera.efectivo, cartera.unidades, cierre, cartera.patrimonio(cierre)))
            if modo == "vivo":
                con.execute("INSERT OR IGNORE INTO velas VALUES (?,?,?,?,?,?,?)",
                            (fecha, ultima.open, ultima.high, ultima.low, ultima.close, ultima.volume, ahora()))
                con.execute("INSERT INTO estado(clave, valor) VALUES('cartera_vivo', ?) "
                            "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor", (json.dumps(cartera.__dict__),))

        if ver.bloqueo and modo == "vivo":
            self.cambiar_estado("bloqueado", ver.motivo)
        return {"resultado": "decision", "fecha_vela": fecha, "pronostico": pron, "senal_regla": senal,
                "propuesta": propuesta, "accion_final": ver.accion_final, "aprobada": ver.aprobada,
                "motivo": ver.motivo, "orden": orden.__dict__ if orden else None, "latencia_llm_ms": latencia}

    # ---------------- modo vivo ----------------
    def ciclo_vivo(self) -> dict:
        with self.candado:
            if self.estado != "en_marcha":
                return {"resultado": "inactivo", "estado": self.estado, "mensaje": "Inicia el agente para operar."}
            try:
                ahora_ms = self.reloj()
                cerradas, en_curso = separar_cerradas(self.mexc.velas(limite=N_REZAGOS + 15), ahora_ms)
                ventana = cerradas[-(N_REZAGOS + 1):]
                validar_velas(ventana, N_REZAGOS + 1)
                validar_antiguedad(ventana[-1], ahora_ms, self.cfg.antiguedad_maxima_horas)
                if self.db.ya_procesada("vivo", "vivo", self.politica, ventana[-1].fecha):
                    return {"resultado": "sin_vela_nueva", "ultima_vela": ventana[-1].fecha}
                if en_curso is None or en_curso.open_time != ventana[-1].open_time + DIA_MS:
                    raise ErrorDatos("No está disponible la apertura de la vela siguiente (precio de ejecución t+1).")
                r = self._decidir("vivo", "vivo", self.politica, ventana, en_curso.open, en_curso.fecha,
                                  self.cartera, self.estado)
                self.db.guardar_estado("ultimo_ciclo", {"fecha": ahora(), "resultado": r["resultado"]})
                return r
            except (ErrorMEXC, ErrorOllama, ErrorDatoAtrasado, ErrorDatos) as e:
                return self._fallo("ciclo_vivo", e)

    def liquidar(self) -> dict:
        """Acción EXPLÍCITA: vende toda la posición Paper al precio actual. Detener no liquida."""
        with self.candado:
            if not self.cartera.en_posicion:
                return {"resultado": "sin_posicion"}
            try:
                precio = self.mexc.precio_actual()
            except ErrorMEXC as e:
                return self._fallo("liquidar", e, pausar=False)
            orden = paper.ejecutar("vender", precio, self.cartera, self.cfg, self.par)
            with self.db.transaccion() as con:
                con.execute(
                    """INSERT INTO ordenes(decision_id, modo, corrida, politica, fecha_vela, fecha_ejecucion, lado, cantidad,
                       precio_referencia, precio_ejecucion, monto, costo, efectivo_despues, unidades_despues, creado_en)
                       VALUES (NULL,'vivo','vivo','manual',NULL,?,?,?,?,?,?,?,?,?,?)""",
                    (ahora()[:10], orden.lado, orden.cantidad, orden.precio_referencia, orden.precio_ejecucion,
                     orden.monto, orden.costo, self.cartera.efectivo, self.cartera.unidades, ahora()))
                con.execute("INSERT INTO estado(clave, valor) VALUES('cartera_vivo', ?) "
                            "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor", (json.dumps(self.cartera.__dict__),))
            self.db.registrar_error("control", "liquidacion_manual", f"Venta Paper de {orden.cantidad} ETH a {orden.precio_ejecucion}", "ejecutada")
            return {"resultado": "liquidado", "orden": orden.__dict__}

    # ---------------- modo replay ----------------
    def replay(self, n_ciclos: int = 30, politicas: tuple = ("regla", "llm")) -> dict:
        """Repite las últimas `n_ciclos` velas cerradas. Etiquetado como replay; no toca la cartera en vivo."""
        if n_ciclos < 1:
            raise ValueError("n_ciclos debe ser positivo.")
        corrida = "replay-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S")
        self.replay_en_curso = True
        try:
            cerradas, _ = separar_cerradas(self.mexc.velas(limite=n_ciclos + N_REZAGOS + 2), self.reloj())
            validar_velas(cerradas, N_REZAGOS + 2)
            inicio = len(cerradas) - n_ciclos - 1
            if inicio < N_REZAGOS:
                raise ErrorDatos("No hay suficientes velas para el replay solicitado.")
            resumen = {"corrida": corrida, "modo": "replay", "inicio_ejecucion": cerradas[inicio + 1].fecha,
                       "fin": cerradas[-1].fecha, "ciclos": n_ciclos, "costo": self.cfg.costo,
                       "deslizamiento": self.cfg.deslizamiento, "politicas": {}}
            for pol in politicas:
                if pol == "llm" and self.llm is None:
                    continue
                cartera = Cartera(efectivo=self.cfg.capital_inicial)
                abortado = None
                for i in range(inicio, len(cerradas) - 1):
                    ventana = cerradas[i - N_REZAGOS:i + 1]
                    try:
                        self._decidir("replay", corrida, pol, ventana, cerradas[i + 1].open, cerradas[i + 1].fecha,
                                      cartera, "replay")
                    except ErrorOllama as e:
                        self.db.registrar_error("replay", e.tipo, str(e), f"se detiene la política {pol} del replay")
                        abortado = str(e)
                        break
                resumen["politicas"][pol] = self._metricas(corrida, pol, cartera, cerradas[-1], abortado)
            resumen["politicas"]["comprar_y_mantener"] = self._comprar_y_mantener(cerradas[inicio + 1:])
            self.db.guardar_estado(f"resumen_{corrida}", resumen)
            self.db.guardar_estado("ultimo_replay", corrida)
            return resumen
        except (ErrorMEXC, ErrorDatos) as e:
            self.db.registrar_error("replay", e.tipo, str(e), "replay cancelado")
            return {"resultado": "error", "tipo": e.tipo, "mensaje": str(e)}
        finally:
            self.replay_en_curso = False

    def _metricas(self, corrida: str, politica: str, cartera: Cartera, ultima, abortado) -> dict:
        serie = self.db.filas("SELECT fecha, patrimonio FROM patrimonio WHERE modo='replay' AND corrida=? AND politica=? ORDER BY fecha",
                              (corrida, politica))
        ords = self.db.filas("SELECT lado, monto, costo FROM ordenes WHERE modo='replay' AND corrida=? AND politica=? ORDER BY id",
                             (corrida, politica))
        decs = self.db.filas("SELECT aprobada, latencia_llm_ms, accion_propuesta FROM decisiones WHERE modo='replay' AND corrida=? AND politica=?",
                             (corrida, politica))
        # Liquidación final al último cierre (solo para medir; no se guarda como orden)
        final = cartera.efectivo + cartera.unidades * ultima.close * (1 - self.cfg.deslizamiento) * (1 - self.cfg.costo)
        valores = [s["patrimonio"] for s in serie] + [final]
        pico = np.maximum.accumulate(np.r_[self.cfg.capital_inicial, valores])
        dd = float(np.min(np.r_[self.cfg.capital_inicial, valores] / pico - 1))
        # Operaciones cerradas: pares compra→venta (la posición abierta al final se cierra en la liquidación)
        ganancias, base = [], None
        for o in ords:
            if o["lado"] == "compra":
                base = o["monto"] + o["costo"]
            elif base is not None:
                ganancias.append(o["monto"] - o["costo"] - base); base = None
        if base is not None:
            ganancias.append(cartera.unidades * ultima.close * (1 - self.cfg.deslizamiento) * (1 - self.cfg.costo) - base)
        lat = [d["latencia_llm_ms"] for d in decs if d["latencia_llm_ms"] is not None]
        return {"decisiones": len(decs), "patrimonio_final": round(final, 2),
                "rend_neto_pct": round(100 * (final / self.cfg.capital_inicial - 1), 3),
                "max_drawdown_pct": round(100 * dd, 3), "operaciones": len(ganancias),
                "win_rate": round(float(np.mean(np.array(ganancias) > 0)), 3) if ganancias else None,
                "costos": round(sum(o["costo"] for o in ords), 2),
                "acciones_rechazadas": sum(1 for d in decs if not d["aprobada"]),
                "latencia_media_ms": round(float(np.mean(lat)), 1) if lat else None, "abortado": abortado}

    def _comprar_y_mantener(self, velas_ejec: list) -> dict:
        c = self.cfg
        p0 = velas_ejec[0].open * (1 + c.deslizamiento)
        unidades = c.capital_inicial / (p0 * (1 + c.costo))
        valores = [unidades * v.close for v in velas_ejec]
        final = unidades * velas_ejec[-1].close * (1 - c.deslizamiento) * (1 - c.costo)
        serie = np.r_[c.capital_inicial, valores[:-1], final]
        dd = float(np.min(serie / np.maximum.accumulate(serie) - 1))
        return {"decisiones": None, "patrimonio_final": round(final, 2),
                "rend_neto_pct": round(100 * (final / c.capital_inicial - 1), 3), "max_drawdown_pct": round(100 * dd, 3),
                "operaciones": 1, "win_rate": float(final > c.capital_inicial), "costos": round(c.capital_inicial * c.costo + final * c.costo, 2),
                "acciones_rechazadas": None, "latencia_media_ms": None, "abortado": None}
