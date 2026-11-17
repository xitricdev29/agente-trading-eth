"""Configuración del agente.

- Secretos (API keys) solo en variables de entorno del servidor (.env), nunca en el repositorio ni en la UI.
- Límites de riesgo en config/riesgo.json: los lee el backend al iniciar. El LLM no tiene ninguna vía para cambiarlos.
"""
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent
load_dotenv(RAIZ / ".env")

SIMBOLO = "ETHUSDT"
INTERVALO = "1d"

# Bloqueo explícito de operaciones reales: este proyecto SOLO simula (modo Paper).
# No existe ninguna función que envíe órdenes a MEXC; esta constante lo documenta y las pruebas lo verifican.
ORDENES_REALES_HABILITADAS = False


@dataclass(frozen=True)
class ConfigRiesgo:
    capital_inicial: float = 50_000.0
    costo: float = 0.0                       # 0.0% por compra o venta (protocolo del agente)
    deslizamiento: float = 0.0005            # 0.05% en contra al ejecutar (supuesto documentado)
    exposicion_maxima: float = 0.90          # fracción máxima del patrimonio invertida en ETH
    perdida_diaria_maxima: float = 0.05      # 5%: si el patrimonio cae más entre dos velas, se bloquean órdenes
    antiguedad_maxima_horas: float = 26.0    # la última vela cerrada no puede tener más de 26 h
    liquidar_al_detener: bool = False        # detener NO liquida; liquidar es una acción explícita

    @classmethod
    def desde_archivo(cls, ruta: Path) -> "ConfigRiesgo":
        datos = json.loads(Path(ruta).read_text(encoding="utf-8"))
        desconocidas = set(datos) - set(cls.__dataclass_fields__)
        if desconocidas:
            raise ValueError(f"Claves desconocidas en {ruta}: {sorted(desconocidas)}")
        cfg = cls(**datos)
        if not (0 < cfg.exposicion_maxima <= 1):
            raise ValueError("exposicion_maxima debe estar en (0, 1]: sin apalancamiento.")
        if not (0 < cfg.perdida_diaria_maxima < 1):
            raise ValueError("perdida_diaria_maxima debe estar en (0, 1).")
        if cfg.costo < 0 or cfg.deslizamiento < 0 or cfg.capital_inicial <= 0:
            raise ValueError("Costo, deslizamiento y capital deben ser no negativos (capital > 0).")
        return cfg


@dataclass(frozen=True)
class Ajustes:
    backend_api_key: str = field(default_factory=lambda: os.getenv("BACKEND_API_KEY", ""))
    gateway_url: str = field(default_factory=lambda: os.getenv("OLLAMA_GATEWAY_URL", "http://127.0.0.1:8100"))
    gateway_api_key: str = field(default_factory=lambda: os.getenv("OLLAMA_GATEWAY_API_KEY", ""))
    ollama_modelo: str = field(default_factory=lambda: os.getenv("OLLAMA_MODELO", "llama3.2:3b"))
    modo_decision: str = field(default_factory=lambda: os.getenv("MODO_DECISION", "llm"))   # llm | regla
    intervalo_revision_s: int = field(default_factory=lambda: int(os.getenv("INTERVALO_REVISION_S", "300")))
    db_path: str = field(default_factory=lambda: os.getenv("DB_PATH", str(RAIZ / "datos" / "agente.db")))
    dir_modelo: str = field(default_factory=lambda: os.getenv("DIR_MODELO", str(RAIZ / "modelo")))
    ruta_riesgo: str = field(default_factory=lambda: os.getenv("RUTA_RIESGO", str(RAIZ / "config" / "riesgo.json")))
    cors_origen: str = field(default_factory=lambda: os.getenv("CORS_ORIGEN", "http://localhost:5173"))

    def validar(self):
        faltan = [n for n, v in [("BACKEND_API_KEY", self.backend_api_key), ("OLLAMA_GATEWAY_API_KEY", self.gateway_api_key)]
                  if len(v) < 16]
        if faltan:
            raise RuntimeError(f"Define en backend/.env {', '.join(faltan)} con al menos 16 caracteres "
                               "(ver .env.example). Genera una con: python -c \"import secrets;print(secrets.token_urlsafe(32))\"")
        if self.modo_decision not in ("llm", "regla"):
            raise RuntimeError("MODO_DECISION debe ser 'llm' o 'regla'.")
