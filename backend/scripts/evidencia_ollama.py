"""Registra la evidencia de que el LLM corre localmente con Ollama.

Uso (con el gateway encendido):
    python scripts/evidencia_ollama.py              # evidencia normal
    python scripts/evidencia_ollama.py --sin-red    # apaga el Wi-Fi ANTES: demuestra inferencia sin nube

Escribe ../evidencia/ollama_<fecha>.json (para el repositorio) y lo imprime. No incluye claves.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from agente.config import Ajustes  # noqa: E402
from agente.evidencia import recolectar  # noqa: E402
from agente.llm import ClienteLLM  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sin-red", action="store_true", help="verifica que NO hay internet y que la inferencia funciona igual")
    a = ap.parse_args()
    aj = Ajustes()
    aj.validar()
    llm = ClienteLLM(aj.gateway_url, aj.gateway_api_key, aj.ollama_modelo)
    e = recolectar(llm, verificar_sin_red=a.sin_red)
    salida = RAIZ.parent / "evidencia"
    salida.mkdir(exist_ok=True)
    sufijo = "_sin_red" if a.sin_red else ""
    ruta = salida / f"ollama_{datetime.now(timezone.utc):%Y%m%d_%H%M}{sufijo}.json"
    ruta.write_text(json.dumps(e, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(e, ensure_ascii=False, indent=2))
    print(f"\nEvidencia guardada en {ruta}")
    if a.sin_red and not e["prueba_sin_red"]["valida"]:
        print("AVISO: se detectó internet. Desconecta el Wi-Fi/cable y repite para que la prueba sea válida.")


if __name__ == "__main__":
    main()
