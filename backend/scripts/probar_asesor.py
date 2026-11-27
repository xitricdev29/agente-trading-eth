"""Detecta qué API expone el servidor del asesor externo (p. ej. https://192.168.4.59:8443).

Uso (en la red de la escuela, con ASESOR_EXTERNO_URL, _API_KEY y _CA en backend/.env):
    python scripts/probar_asesor.py

Prueba rutas comunes con la API key y el certificado, e imprime SOLO códigos de estado y
un fragmento de la respuesta. Nunca imprime la clave.
"""
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
from agente.config import Ajustes  # noqa: E402

RUTAS = [("GET", "/"), ("GET", "/docs"), ("GET", "/openapi.json"), ("GET", "/health"), ("GET", "/v1/models"),
         ("GET", "/api/tags"), ("GET", "/api/version")]


def main():
    aj = Ajustes()
    if not aj.asesor_url or not aj.asesor_api_key:
        raise SystemExit("Define ASESOR_EXTERNO_URL y ASESOR_EXTERNO_API_KEY en backend/.env")
    u = urlparse(aj.asesor_url)
    base = f"{u.scheme}://{u.netloc}"
    pref = aj.asesor_prefijo + (" " if aj.asesor_prefijo and not aj.asesor_prefijo.endswith(" ") else "")
    verificar = aj.asesor_ca or True
    print(f"Servidor: {base} | certificado: {aj.asesor_ca or 'almacén del sistema'}")
    with httpx.Client(timeout=10, verify=verificar, headers={aj.asesor_encabezado: pref + aj.asesor_api_key}) as c:
        for metodo, ruta in RUTAS:
            try:
                r = c.request(metodo, base + ruta)
                cuerpo = " ".join(r.text.split())[:160]
                print(f"  {metodo:4s} {ruta:16s} → {r.status_code}  {cuerpo}")
            except httpx.ConnectError as e:
                print(f"  {metodo:4s} {ruta:16s} → sin conexión ({e.__class__.__name__}). ¿Estás en la red de la escuela?")
                break
            except Exception as e:
                print(f"  {metodo:4s} {ruta:16s} → {type(e).__name__}: {str(e)[:120]}")
    print("\nMándale a tu asistente estas líneas (no contienen la clave) para configurar el formato correcto.")


if __name__ == "__main__":
    main()
