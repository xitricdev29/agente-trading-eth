"""Exportación de registros: CSV por tabla + resúmenes de replay + configuración (sin secretos)."""
import csv
import io
import json
import zipfile

from .db import BaseDatos

TABLAS = ("velas", "decisiones", "ordenes", "patrimonio", "errores", "ciclos")


def a_csv(filas: list[dict]) -> bytes:
    buf = io.StringIO()
    if filas:
        w = csv.DictWriter(buf, fieldnames=list(filas[0]))
        w.writeheader()
        w.writerows(filas)
    return buf.getvalue().encode("utf-8-sig")         # BOM: Excel abre bien los acentos


def zip_registros(db: BaseDatos, config_riesgo: dict, contrato_modelo: dict) -> bytes:
    salida = io.BytesIO()
    with zipfile.ZipFile(salida, "w", zipfile.ZIP_DEFLATED) as z:
        for t in TABLAS:
            z.writestr(f"{t}.csv", a_csv(db.filas(f"SELECT * FROM {t} ORDER BY rowid")))
        corridas = [r["corrida"] for r in db.filas("SELECT DISTINCT corrida FROM decisiones WHERE modo='replay' ORDER BY corrida")]
        z.writestr("replays.json", json.dumps([db.leer_estado(f"resumen_{c}") for c in corridas], ensure_ascii=False, indent=2))
        z.writestr("evidencia_llm.json", json.dumps(db.leer_estado("evidencia_llm"), ensure_ascii=False, indent=2))
        z.writestr("configuracion_riesgo.json", json.dumps(config_riesgo, ensure_ascii=False, indent=2))
        z.writestr("contrato_modelo.json", json.dumps(contrato_modelo, ensure_ascii=False, indent=2))
    return salida.getvalue()
