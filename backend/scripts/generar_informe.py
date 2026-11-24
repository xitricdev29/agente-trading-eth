"""Genera docs/RESULTADOS.md con las tablas del informe, calculadas desde la base SQLite.

Uso:  python scripts/generar_informe.py [--corrida replay-AAAAMMDDTHHMMSS]
Sin --corrida usa el replay más reciente que tenga la política con LLM completa.
Las cifras salen de los registros; la interpretación se escribe en docs/INFORME.md.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from agente import __version__  # noqa: E402
from agente.config import Ajustes  # noqa: E402
from agente.db import BaseDatos  # noqa: E402

NOMBRES = {"regla": "Modelo sin LLM (regla δ)", "llm": "Agente con LLM", "comprar_y_mantener": "Comprar y mantener"}


def tabla(filas, columnas):
    out = ["| " + " | ".join(c for c, _ in columnas) + " |", "|" + "---|" * len(columnas)]
    for f in filas:
        out.append("| " + " | ".join(fmt(f) for _, fmt in columnas) + " |")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corrida")
    a = ap.parse_args()
    aj = Ajustes()
    db = BaseDatos(aj.db_path)
    L = [f"# Resultados del agente (generado automáticamente, v{__version__})", "",
         "> Cifras calculadas desde `backend/datos/agente.db`. Regenerar con `python scripts/generar_informe.py`.", ""]

    # 1. Evidencia del LLM local
    e = db.leer_estado("evidencia_llm")
    L += ["## 1. LLM local (Ollama)", ""]
    if e:
        m, i, h = e["modelo"], e["inferencia"], e["hardware"]
        L += [tabla([None], [
            ("Ollama", lambda _: str(e["ollama_version"])), ("Modelo", lambda _: f"{m['nombre']} ({m['parametros']}, {m['cuantizacion']})"),
            ("Procesador", lambda _: e["procesador"]), ("CPU", lambda _: h["cpu"]), ("RAM", lambda _: f"{h['ram_gb']} GB"),
            ("GPU", lambda _: ", ".join(h["gpus"]) or "—"), ("Latencia prueba", lambda _: f"{i['latencia_total_ms']:.0f} ms"),
            ("Tokens/s", lambda _: str(i["tokens_por_segundo"])), ("Local", lambda _: "sí" if e["inferencia_local"] else "NO")]),
            "", f"Registrada el {e['fecha_utc']}. Ver también `evidencia/ollama_*.json`.", ""]
    else:
        L += ["*Pendiente:* pulsa **Registrar evidencia del LLM** en la UI o ejecuta `python scripts/evidencia_ollama.py`.", ""]

    # 2. Ciclo programado en vivo
    c = db.filas("SELECT COUNT(*) n, SUM(origen='programado') prog, MIN(inicio) ini, MAX(inicio) fin, "
                 "COUNT(DISTINCT fecha_vela) velas FROM ciclos")[0]
    res = Counter(r["resultado"] for r in db.filas("SELECT resultado FROM ciclos"))
    dec_vivo = db.filas("SELECT fecha_vela, pronostico, accion_propuesta, accion_final, aprobada, latencia_llm_ms "
                        "FROM decisiones WHERE modo='vivo' ORDER BY fecha_vela")
    L += ["## 2. Ciclo programado (operación en vivo con MEXC)", "",
          f"- Revisiones registradas: **{c['n']}** ({c['prog'] or 0} automáticas), del {c['ini']} al {c['fin']} (UTC).",
          f"- Resultados: {dict(res)}.", f"- Decisiones en vivo (una por vela diaria cerrada): **{len(dec_vivo)}**.", ""]
    if dec_vivo:
        L += [tabla(dec_vivo, [("Vela", lambda d: d["fecha_vela"]), ("Pronóstico", lambda d: f"{d['pronostico']:+.3%}"),
                               ("Propuesta", lambda d: str(d["accion_propuesta"])), ("Final", lambda d: d["accion_final"]),
                               ("Riesgo", lambda d: "aprobada" if d["aprobada"] else "rechazada"),
                               ("Latencia", lambda d: f"{d['latencia_llm_ms']:.0f} ms" if d["latencia_llm_ms"] else "—")]), ""]

    # 3. Replay y comparación
    corridas = [r["corrida"] for r in db.filas("SELECT DISTINCT corrida FROM decisiones WHERE modo='replay' ORDER BY corrida DESC")]
    resumenes = [db.leer_estado(f"resumen_{x}") for x in corridas]
    resumenes = [r for r in resumenes if r and "llm" in r.get("politicas", {}) and not r["politicas"]["llm"].get("abortado")]
    if a.corrida:
        resumenes = [r for r in resumenes if r["corrida"] == a.corrida]
    L += ["## 3. Comparación en replay (mismo periodo y costos)", ""]
    if resumenes:
        r = resumenes[0]
        L += [f"Corrida `{r['corrida']}` (etiquetada como **replay**): ejecución del {r['inicio_ejecucion']} al {r['fin']}, "
              f"{r['ciclos']} ciclos, costo {r['costo']:.2%}, deslizamiento {r['deslizamiento']:.2%}.", "",
              tabla(list(r["politicas"].items()), [
                  ("Estrategia", lambda kv: NOMBRES.get(kv[0], kv[0])), ("Rend. neto", lambda kv: f"{kv[1]['rend_neto_pct']:.2f}%"),
                  ("Máx. drawdown", lambda kv: f"{kv[1]['max_drawdown_pct']:.2f}%"), ("Operaciones", lambda kv: str(kv[1]["operaciones"])),
                  ("Win Rate", lambda kv: "no definido" if kv[1]["win_rate"] is None else f"{kv[1]['win_rate']:.0%}"),
                  ("Rechazadas", lambda kv: "—" if kv[1]["acciones_rechazadas"] is None else str(kv[1]["acciones_rechazadas"])),
                  ("Latencia media", lambda kv: f"{kv[1]['latencia_media_ms']:.0f} ms" if kv[1]["latencia_media_ms"] else "—")]), ""]
        d = db.filas("SELECT fecha_vela, politica, accion_final, herramientas, propuesta FROM decisiones "
                     "WHERE modo='replay' AND corrida=? ORDER BY fecha_vela, politica", (r["corrida"],))
        por = {}
        for x in d:
            por.setdefault(x["fecha_vela"], {})[x["politica"]] = x
        difs = [(f, p["llm"], p["regla"]) for f, p in por.items() if "llm" in p and "regla" in p and p["llm"]["accion_final"] != p["regla"]["accion_final"]]
        usos = Counter(u.get("nombre") for x in d if x["politica"] == "llm" and x["herramientas"]
                       for u in json.loads(x["herramientas"]) if u.get("nombre"))
        L += [f"- Velas en que el LLM decidió distinto que la regla: **{len(difs)} de {len(por)}**.",
              f"- Herramientas usadas por el LLM: {dict(usos) or 'ninguna'}.", ""]
        if difs:
            L += ["Ejemplos de desacuerdo (justificación del LLM):", ""]
            for f, l, g in difs[:5]:
                j = (json.loads(l["propuesta"]) or {}).get("justificacion", "") if l["propuesta"] else ""
                L.append(f"- **{f}**: LLM `{l['accion_final']}` vs regla `{g['accion_final']}`. *\"{j}\"*")
            L.append("")
    else:
        L += ["*Pendiente:* ejecuta un replay de al menos 20 velas con el LLM encendido.", ""]

    # 4. Errores y eventos
    err = db.filas("SELECT tipo, COUNT(*) n FROM errores GROUP BY tipo ORDER BY n DESC")
    L += ["## 4. Bitácora de errores y eventos", "", tabla(err, [("Tipo", lambda x: x["tipo"]), ("Eventos", lambda x: str(x["n"]))]) if err else "Sin eventos.", "",
          "Los nueve escenarios de error se demuestran con pruebas automatizadas: `cd backend && pytest -v tests/test_escenarios_error.py`.", ""]

    destino = RAIZ.parent / "docs" / "RESULTADOS.md"
    destino.parent.mkdir(exist_ok=True)
    destino.write_text("\n".join(L), encoding="utf-8")
    print(f"Informe de resultados generado en {destino}")


if __name__ == "__main__":
    main()
