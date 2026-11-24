"""Evidencia de que el LLM corre localmente con Ollama (rúbrica: modelo, versión, hardware y tiempo de respuesta).

Consulta, a través del gateway autenticado:
  /api/version  → versión de Ollama
  /api/tags     → modelo instalado (digest, tamaño, cuantización)
  /api/show     → detalles del modelo
  /api/chat     → una inferencia de prueba con tiempos medidos por Ollama
  /api/ps       → si el modelo quedó cargado en GPU (size_vram) o en CPU
y agrega datos del hardware de este equipo. Nunca incluye claves.
"""
import datetime as dt
import os
import platform
import shutil
import subprocess
from urllib.parse import urlparse

import httpx

from .llm import ClienteLLM

HOSTS_LOCALES = {"127.0.0.1", "localhost", "::1"}


def _ram_gb() -> float | None:
    try:
        if os.name == "nt":
            import ctypes

            class MEM(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MEM(); m.dwLength = ctypes.sizeof(MEM)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return round(m.ullTotalPhys / 1e9, 1)
        if os.path.exists("/proc/meminfo"):
            kb = int(next(l for l in open("/proc/meminfo") if l.startswith("MemTotal")).split()[1])
            return round(kb / 1e6, 1)
        if platform.system() == "Darwin":
            return round(int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], timeout=5)) / 1e9, 1)
    except Exception:
        return None
    return None


def _cpu() -> str:
    try:
        if os.name == "nt":
            import winreg
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            return winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
        if os.path.exists("/proc/cpuinfo"):
            for l in open("/proc/cpuinfo"):
                if l.startswith("model name"):
                    return l.split(":", 1)[1].strip()
        if platform.system() == "Darwin":
            return subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string"], timeout=5).decode().strip()
    except Exception:
        pass
    return platform.processor() or "desconocido"


def _gpus() -> list[str]:
    try:
        if shutil.which("nvidia-smi"):
            out = subprocess.check_output(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                                          timeout=10).decode()
            return [l.strip() for l in out.splitlines() if l.strip()]
        if os.name == "nt":
            out = subprocess.check_output(["powershell", "-NoProfile", "-Command",
                                           "(Get-CimInstance Win32_VideoController).Name"], timeout=15).decode(errors="ignore")
            return [l.strip() for l in out.splitlines() if l.strip()]
    except Exception:
        pass
    return []


def hardware() -> dict:
    return {"sistema": f"{platform.system()} {platform.release()}", "arquitectura": platform.machine(),
            "cpu": _cpu(), "nucleos_logicos": os.cpu_count(), "ram_gb": _ram_gb(), "gpus": _gpus()}


def hay_internet(timeout: float = 4.0) -> bool:
    for url in ("https://api.mexc.com/api/v3/ping", "https://1.1.1.1"):
        try:
            httpx.get(url, timeout=timeout)
            return True
        except httpx.HTTPError:
            continue
    return False


def recolectar(llm: ClienteLLM, verificar_sin_red: bool = False) -> dict:
    version = llm.get("/api/version").get("version")
    tags = llm.get("/api/tags").get("models", [])
    entrada = next((m for m in tags if m.get("name") == llm.modelo or m.get("model") == llm.modelo), None)
    if entrada is None:
        raise ValueError(f"El modelo '{llm.modelo}' no está instalado en Ollama. Ejecuta: ollama pull {llm.modelo}")
    detalles_show, _ = llm.post("/api/show", {"model": llm.modelo})
    det = {**(entrada.get("details") or {}), **(detalles_show.get("details") or {})}

    sin_red = (not hay_internet()) if verificar_sin_red else None
    respuesta, lat_ms = llm.post("/api/chat", {"model": llm.modelo, "stream": False, "options": {"temperature": 0},
                                               "messages": [{"role": "user", "content": "Responde solo: ok"}]})
    ns = lambda k: (respuesta.get(k) or 0) / 1e9
    tps = round(respuesta["eval_count"] / ns("eval_duration"), 1) if respuesta.get("eval_count") and respuesta.get("eval_duration") else None

    cargados = llm.get("/api/ps").get("models", [])
    cargado = next((m for m in cargados if m.get("name") == llm.modelo or m.get("model") == llm.modelo), None)
    if cargado and cargado.get("size"):
        frac = (cargado.get("size_vram") or 0) / cargado["size"]
        procesador = "GPU" if frac >= 0.99 else ("CPU" if frac <= 0.01 else f"mixto ({frac:.0%} en GPU)")
    else:
        procesador = "desconocido"

    host = urlparse(llm.gateway_url).hostname
    ollama_host = urlparse(os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")).hostname
    return {
        "fecha_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "ollama_version": version,
        "modelo": {"nombre": llm.modelo, "digest": (entrada.get("digest") or "")[:16], "tamano_gb": round((entrada.get("size") or 0) / 1e9, 2),
                   "familia": det.get("family"), "parametros": det.get("parameter_size"), "cuantizacion": det.get("quantization_level")},
        "inferencia": {"respuesta": (respuesta.get("message") or {}).get("content", "").strip()[:80],
                       "latencia_total_ms": round(lat_ms, 1), "tiempo_carga_s": round(ns("load_duration"), 3),
                       "tiempo_total_ollama_s": round(ns("total_duration"), 3), "tokens_generados": respuesta.get("eval_count"),
                       "tokens_por_segundo": tps},
        "procesador": procesador,
        "gateway": f"{host}:{urlparse(llm.gateway_url).port}", "ollama": f"{ollama_host}",
        "inferencia_local": host in HOSTS_LOCALES and ollama_host in HOSTS_LOCALES,
        "prueba_sin_red": None if sin_red is None else {"internet_disponible": not sin_red, "inferencia_exitosa": True,
                                                         "valida": bool(sin_red)},
        "hardware": hardware(),
    }
