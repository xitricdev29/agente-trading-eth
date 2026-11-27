"""Exporta el modelo aprobado en el notebook (capítulos 7-9) a un formato portable.

Uso (después de ejecutar el notebook en Colab con T4 y descargar la carpeta de Drive):

    python scripts/exportar_modelo.py --drive /ruta/DL_Finanzas_ETHUSDT --salida modelo

Lee:
  modelos/escalador_tr_cap7.joblib, modelos/escalador_y_tr_cap8.joblib,
  modelos/cap8_<tipo>_n<N>_s<semilla>.pt (las tres semillas de la mejor configuración),
  resultados/regla_cap9.json, resultados/evidencia_t4.json
Escribe:
  <salida>/contrato.json  (variables, escalas, arquitectura, umbral, procedencia)
  <salida>/pesos_s<semilla>.pt
"""
import argparse
import datetime as dt
import glob
import json
import os
import re
import shutil

import joblib
import torch


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--drive", required=True, help="Carpeta DL_Finanzas_ETHUSDT descargada de Drive")
    ap.add_argument("--salida", default="modelo")
    ap.add_argument("--version", default="0.1.0")
    a = ap.parse_args()

    regla = json.load(open(os.path.join(a.drive, "resultados", "regla_cap9.json"), encoding="utf-8"))
    tipo = regla["pronostico"]
    if tipo not in ("MLP", "LSTM"):
        raise SystemExit(f"La regla usa '{tipo}'. Este exportador solo admite MLP o LSTM.")

    ckpts = sorted(glob.glob(os.path.join(a.drive, "modelos", f"cap8_{tipo.lower()}_n*_s*.pt")))
    # Las tres semillas de repetición (101, 202, 303) de la configuración seleccionada
    sel = [c for c in ckpts if re.search(r"_s(101|202|303)\.pt$", c)]
    if len(sel) != 3:
        raise SystemExit(f"Se esperaban 3 checkpoints de semillas para {tipo}; se encontraron {len(sel)}.")

    esc_x = joblib.load(os.path.join(a.drive, "modelos", "escalador_tr_cap7.joblib"))
    esc_y = joblib.load(os.path.join(a.drive, "modelos", "escalador_y_tr_cap8.joblib"))
    evid_path = os.path.join(a.drive, "resultados", "evidencia_t4.json")
    evid = json.load(open(evid_path, encoding="utf-8")) if os.path.exists(evid_path) else {}

    os.makedirs(a.salida, exist_ok=True)
    semillas, neuronas = [], None
    for c in sel:
        ck = torch.load(c, map_location="cpu", weights_only=False)
        neuronas = ck["config"]["neuronas"]
        semillas.append(ck["semilla"])
        torch.save(ck["estado"], os.path.join(a.salida, f"pesos_s{ck['semilla']}.pt"))

    contrato = {
        "version": a.version,
        "simbolo": "ETHUSDT",
        "intervalo": "1d",
        "objetivo": "rendimiento simple del día siguiente: close[t+1]/close[t] - 1",
        "variables": [f"lag_{k}" for k in range(1, 21)],
        "definicion_variables": "lag_1 = close[t]/close[t-1]-1 (último día cerrado), ..., lag_20; solo días consecutivos",
        "escalador_x": {"media": esc_x.mean_.tolist(), "escala": esc_x.scale_.tolist()},
        "escalador_y": {"media": float(esc_y.mean_[0]), "escala": float(esc_y.scale_[0])},
        "tipo": tipo,
        "neuronas": neuronas,
        "dropout": 0.2,
        "semillas": sorted(semillas),
        "agregacion": "promedio de las predicciones de las semillas",
        "umbral_delta": regla["delta"],
        "regla": f"comprar si pronóstico > {regla['delta']}; en otro caso efectivo",
        "procedencia": {
            "notebook": "Practica_DL_Finanzas_ETHUSDT_SebastianLopez.ipynb (capítulos 7-11)",
            "entrenamiento_dispositivo": evid.get("gpu", "desconocido"),
            "valido_t4": bool(evid.get("valida_para_entrega", False)),
            "exportado_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        },
    }
    json.dump(contrato, open(os.path.join(a.salida, "contrato.json"), "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    shutil.copy(os.path.join(a.drive, "resultados", "regla_cap9.json"), os.path.join(a.salida, "regla_cap9.json"))
    print(f"Exportado {tipo} n={neuronas} semillas={sorted(semillas)} δ={regla['delta']} → {a.salida}/")


if __name__ == "__main__":
    main()
