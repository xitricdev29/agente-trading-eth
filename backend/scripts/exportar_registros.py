"""Exporta la base SQLite del agente a CSV (para entregar los registros sin la base).

Uso:  python scripts/exportar_registros.py            → ../evidencia/registros/*.csv
"""
import json
import sys
import zipfile
import io
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from agente.config import Ajustes, ConfigRiesgo  # noqa: E402
from agente.db import BaseDatos  # noqa: E402
from agente.exportar import zip_registros  # noqa: E402


def main():
    aj = Ajustes()
    if not Path(aj.db_path).exists():
        raise SystemExit(f"No existe la base {aj.db_path}. Enciende el agente al menos una vez.")
    db = BaseDatos(aj.db_path)
    contrato = json.loads((Path(aj.dir_modelo) / "contrato.json").read_text(encoding="utf-8"))
    datos = zip_registros(db, ConfigRiesgo.desde_archivo(aj.ruta_riesgo).__dict__, contrato)
    destino = RAIZ.parent / "evidencia" / "registros"
    destino.mkdir(parents=True, exist_ok=True)
    zipfile.ZipFile(io.BytesIO(datos)).extractall(destino)
    for f in sorted(destino.iterdir()):
        print(f"  {f.name:28s} {f.stat().st_size / 1024:8.1f} KB")
    print(f"Registros exportados en {destino}")


if __name__ == "__main__":
    main()
