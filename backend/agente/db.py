"""Persistencia SQLite: velas procesadas, decisiones, órdenes Paper, patrimonio, errores y estado.

Idempotencia: (modo, corrida, fecha_vela) es único en `decisiones`; una vela no se procesa dos veces
aunque el evento llegue repetido o el backend se reinicie.
"""
import datetime as dt
import json
import sqlite3
from pathlib import Path

ESQUEMA = """
CREATE TABLE IF NOT EXISTS velas (
    fecha_vela TEXT PRIMARY KEY, open REAL, high REAL, low REAL, close REAL, volume REAL, registrada_en TEXT);
CREATE TABLE IF NOT EXISTS decisiones (
    id INTEGER PRIMARY KEY AUTOINCREMENT, modo TEXT NOT NULL, corrida TEXT NOT NULL, politica TEXT NOT NULL,
    fecha_vela TEXT NOT NULL, precio_cierre REAL, pronostico REAL, senal_regla TEXT, indicadores TEXT,
    propuesta TEXT, accion_propuesta TEXT, accion_final TEXT, aprobada INTEGER, motivo_riesgo TEXT,
    latencia_llm_ms REAL, modelo_version TEXT, llm_modelo TEXT, creado_en TEXT,
    UNIQUE (modo, corrida, politica, fecha_vela));
CREATE TABLE IF NOT EXISTS ordenes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, decision_id INTEGER REFERENCES decisiones(id), modo TEXT, corrida TEXT,
    politica TEXT, fecha_vela TEXT, fecha_ejecucion TEXT, lado TEXT, cantidad REAL, precio_referencia REAL,
    precio_ejecucion REAL, monto REAL, costo REAL, efectivo_despues REAL, unidades_despues REAL, creado_en TEXT);
CREATE TABLE IF NOT EXISTS patrimonio (
    modo TEXT, corrida TEXT, politica TEXT, fecha TEXT, efectivo REAL, unidades REAL, precio REAL, patrimonio REAL,
    PRIMARY KEY (modo, corrida, politica, fecha));
CREATE TABLE IF NOT EXISTS errores (
    id INTEGER PRIMARY KEY AUTOINCREMENT, creado_en TEXT, etapa TEXT, tipo TEXT, mensaje TEXT, accion TEXT);
CREATE TABLE IF NOT EXISTS estado (clave TEXT PRIMARY KEY, valor TEXT);
CREATE TABLE IF NOT EXISTS ciclos (
    id INTEGER PRIMARY KEY AUTOINCREMENT, inicio TEXT, fin TEXT, origen TEXT, resultado TEXT,
    fecha_vela TEXT, detalle TEXT);
"""

MIGRACIONES = [("decisiones", "herramientas", "TEXT")]


def ahora() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class BaseDatos:
    def __init__(self, ruta: str):
        if ruta != ":memory:":
            Path(ruta).parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(ruta, check_same_thread=False, isolation_level=None)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.executescript(ESQUEMA)
        for tabla, columna, tipo in MIGRACIONES:          # bases creadas con versiones anteriores
            cols = {r["name"] for r in self.con.execute(f"PRAGMA table_info({tabla})")}
            if columna not in cols:
                self.con.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna} {tipo}")

    # --- estado clave/valor ---
    def leer_estado(self, clave: str, defecto=None):
        r = self.con.execute("SELECT valor FROM estado WHERE clave=?", (clave,)).fetchone()
        return json.loads(r["valor"]) if r else defecto

    def guardar_estado(self, clave: str, valor) -> None:
        self.con.execute("INSERT INTO estado(clave, valor) VALUES(?, ?) ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor",
                         (clave, json.dumps(valor)))

    # --- consultas ---
    def ya_procesada(self, modo: str, corrida: str, politica: str, fecha: str) -> bool:
        return self.con.execute("SELECT 1 FROM decisiones WHERE modo=? AND corrida=? AND politica=? AND fecha_vela=?",
                                (modo, corrida, politica, fecha)).fetchone() is not None

    def registrar_error(self, etapa: str, tipo: str, mensaje: str, accion: str) -> None:
        self.con.execute("INSERT INTO errores(creado_en, etapa, tipo, mensaje, accion) VALUES (?,?,?,?,?)",
                         (ahora(), etapa, tipo, mensaje[:1000], accion))

    def filas(self, sql: str, params=()) -> list[dict]:
        return [dict(r) for r in self.con.execute(sql, params).fetchall()]

    def transaccion(self):
        return _Transaccion(self.con)


class _Transaccion:
    def __init__(self, con):
        self.con = con

    def __enter__(self):
        self.con.execute("BEGIN IMMEDIATE")
        return self.con

    def __exit__(self, tipo, *_):
        self.con.execute("ROLLBACK" if tipo else "COMMIT")
        return False
