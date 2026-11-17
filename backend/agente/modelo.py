"""Carga el modelo aprobado en la práctica y calcula variables con la MISMA definición del entrenamiento."""
import json
import math
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn

from .errores import ErrorDatos

N_REZAGOS = 20


class MLP(nn.Module):
    def __init__(self, n_entrada, n, dropout):
        super().__init__()
        self.red = nn.Sequential(nn.Linear(n_entrada, n), nn.ReLU(), nn.Dropout(dropout),
                                 nn.Linear(n, n // 2), nn.ReLU(), nn.Dropout(dropout),
                                 nn.Linear(n // 2, 1))

    def forward(self, x):
        return self.red(x).squeeze(-1)


class RedLSTM(nn.Module):
    def __init__(self, n_variables, n, dropout):
        super().__init__()
        self.lstm = nn.LSTM(n_variables, n, batch_first=True)
        self.drop = nn.Dropout(dropout)
        self.salida = nn.Linear(n, 1)

    def forward(self, x):
        h, _ = self.lstm(x)
        return self.salida(self.drop(h[:, -1, :])).squeeze(-1)


class ModeloPredictivo:
    def __init__(self, directorio: str | Path):
        d = Path(directorio)
        self.contrato = json.loads((d / "contrato.json").read_text(encoding="utf-8"))
        c = self.contrato
        if c["variables"] != [f"lag_{k}" for k in range(1, N_REZAGOS + 1)]:
            raise ErrorDatos("El contrato de variables no coincide con los 20 rezagos esperados.")
        self.media_x = np.array(c["escalador_x"]["media"], dtype=np.float64)
        self.escala_x = np.array(c["escalador_x"]["escala"], dtype=np.float64)
        self.media_y, self.escala_y = c["escalador_y"]["media"], c["escalador_y"]["escala"]
        self.tipo, self.delta = c["tipo"], float(c["umbral_delta"])
        self.redes = []
        for s in c["semillas"]:
            red = MLP(N_REZAGOS, c["neuronas"], c["dropout"]) if self.tipo == "MLP" else RedLSTM(1, c["neuronas"], c["dropout"])
            red.load_state_dict(torch.load(d / f"pesos_s{s}.pt", map_location="cpu", weights_only=True))
            red.eval()
            self.redes.append(red)

    @property
    def version(self) -> str:
        return self.contrato["version"]

    @staticmethod
    def variables(cierres: list[float]) -> dict:
        """cierres: cierres diarios consecutivos y cerrados, del más antiguo al más reciente (mínimo 21)."""
        if len(cierres) < N_REZAGOS + 1:
            raise ErrorDatos(f"Se necesitan {N_REZAGOS + 1} cierres consecutivos; hay {len(cierres)}.")
        c = np.asarray(cierres, dtype=np.float64)
        rend = c[1:] / c[:-1] - 1
        ult = rend[-N_REZAGOS:]
        lags = {f"lag_{k}": float(ult[-k]) for k in range(1, N_REZAGOS + 1)}   # lag_1 = rendimiento más reciente
        sma20 = float(c[-20:].mean())
        indicadores = {
            "rend_ultimo_dia": float(rend[-1]),
            "sma20": sma20,
            "dist_sma20": float(c[-1] / sma20 - 1),
            "vol20": float(np.std(rend[-20:], ddof=1)),       # desviación muestral, sin anualizar
            "rend_acum_20d": float(c[-1] / c[-21] - 1),
        }
        for k, v in {**lags, **indicadores}.items():
            if not math.isfinite(v):
                raise ErrorDatos(f"Variable no finita: {k}.")
        return {"lags": lags, "indicadores": indicadores}

    def pronosticar(self, lags: dict) -> float:
        x = np.array([lags[f"lag_{k}"] for k in range(1, N_REZAGOS + 1)], dtype=np.float64)
        xs = ((x - self.media_x) / self.escala_x).astype(np.float32)
        if self.tipo == "MLP":
            entrada = torch.tensor(xs[None, :])
        else:
            entrada = torch.tensor(xs[::-1].copy()[None, :, None])     # secuencia lag_20 … lag_1
        with torch.no_grad():
            preds = [float(red(entrada)[0]) for red in self.redes]
        y = float(np.mean(preds)) * self.escala_y + self.media_y
        if not math.isfinite(y):
            raise ErrorDatos("El modelo produjo un pronóstico no finito.")
        return y

    def senal_regla(self, pronostico: float) -> str:
        return "comprar" if pronostico > self.delta else "efectivo"
