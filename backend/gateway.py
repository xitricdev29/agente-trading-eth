"""Gateway autenticado para Ollama local.

Ollama no exige autenticación por defecto; este servicio agrega la capa de API key:
- Escucha en 127.0.0.1:8100 y reenvía solo /api/chat, /api/tags, /api/version, /api/show y /api/ps a Ollama (127.0.0.1:11434).
- Rechaza con 401 cualquier petición sin `Authorization: Bearer <OLLAMA_GATEWAY_API_KEY>` o con una clave incorrecta.
- No registra la clave en logs. La clave vive solo en backend/.env (servidor), nunca en el frontend.

Ejecutar:  uvicorn gateway:crear_app --factory --host 127.0.0.1 --port 8100
"""
import hmac
import logging
import os
import time

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
log = logging.getLogger("gateway")

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
# Solo inferencia y consulta de metadatos. Descargar, borrar o crear modelos queda bloqueado (403).
RUTAS_PERMITIDAS = {("POST", "/api/chat"), ("GET", "/api/tags"), ("GET", "/api/version"),
                    ("POST", "/api/show"), ("GET", "/api/ps")}


def crear_app(clave: str | None = None, transporte=None) -> FastAPI:
    clave = clave if clave is not None else os.getenv("OLLAMA_GATEWAY_API_KEY", "")
    if len(clave) < 16:
        raise RuntimeError("Define OLLAMA_GATEWAY_API_KEY (≥16 caracteres) en backend/.env antes de iniciar el gateway.")
    app = FastAPI(title="Gateway Ollama (API key)", version="0.1.0", docs_url=None, redoc_url=None)
    cliente = httpx.AsyncClient(base_url=OLLAMA_URL, timeout=120.0, transport=transporte)

    @app.middleware("http")
    async def autenticar(request: Request, call_next):
        auth = request.headers.get("authorization", "")
        recibida = auth[7:] if auth.lower().startswith("bearer ") else ""
        if not recibida or not hmac.compare_digest(recibida.encode(), clave.encode()):
            log.warning("Acceso rechazado a %s %s (clave %s)", request.method, request.url.path,
                        "ausente" if not recibida else "incorrecta")
            return JSONResponse({"detail": "API key ausente o incorrecta"}, status_code=401)
        return await call_next(request)

    @app.api_route("/api/{ruta:path}", methods=["GET", "POST"])
    async def reenviar(ruta: str, request: Request):
        if (request.method, f"/api/{ruta}") not in RUTAS_PERMITIDAS:
            raise HTTPException(403, "Ruta no permitida por el gateway.")
        t0 = time.perf_counter()
        try:
            r = await cliente.request(request.method, f"/api/{ruta}", content=await request.body(),
                                      headers={"content-type": "application/json"})
        except httpx.TransportError as e:
            return JSONResponse({"detail": f"Ollama no responde en {OLLAMA_URL}: {type(e).__name__}"}, status_code=502)
        log.info("%s /api/%s → %s en %.0f ms", request.method, ruta, r.status_code, (time.perf_counter() - t0) * 1000)
        return JSONResponse(r.json() if r.content else {}, status_code=r.status_code)

    return app

