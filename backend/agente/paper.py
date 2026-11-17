"""Motor Paper: ejecución simulada con dinero virtual. Nunca envía órdenes a MEXC.

Supuestos documentados:
- Ejecución en la APERTURA de la vela siguiente a la vela cerrada que generó la decisión (equivalente a t+1 del notebook).
- Deslizamiento: el precio de ejecución se mueve `deslizamiento` en contra (compra más cara, venta más barata).
- Cantidad redondeada HACIA ABAJO al paso del par (baseSizePrecision); monto mínimo = quoteAmountPrecision.
- Costo por compra o venta = `costo` × monto negociado (0.0% por defecto en el agente).
"""
import math
from dataclasses import dataclass

from .config import ConfigRiesgo
from .errores import ErrorSaldoInsuficiente
from .mexc import InfoPar
from .riesgo import Cartera


@dataclass
class OrdenPaper:
    lado: str
    cantidad: float
    precio_referencia: float
    precio_ejecucion: float
    monto: float
    costo: float


def _redondear_abajo(x: float, paso: float) -> float:
    pasos = math.floor(x / paso + 1e-9)
    decimales = max(0, -int(math.floor(math.log10(paso))))
    return round(pasos * paso, decimales)


def ejecutar(accion: str, precio_ref: float, cartera: Cartera, cfg: ConfigRiesgo, par: InfoPar) -> OrdenPaper | None:
    if accion in ("mantener", "ninguna"):
        return None
    if not (math.isfinite(precio_ref) and precio_ref > 0):
        raise ValueError(f"Precio de referencia no válido: {precio_ref}")

    if accion == "comprar":
        precio = round(precio_ref * (1 + cfg.deslizamiento), par.decimales_precio)
        presupuesto = min(cartera.efectivo, cfg.exposicion_maxima * cartera.patrimonio(precio_ref))
        cantidad = _redondear_abajo(presupuesto / (precio * (1 + cfg.costo)), par.paso_cantidad)
        monto = cantidad * precio
        if cantidad <= 0 or monto < par.monto_minimo:
            raise ErrorSaldoInsuficiente(f"Saldo insuficiente: presupuesto {presupuesto:.2f} USDT no alcanza el mínimo "
                                         f"({par.monto_minimo} USDT o {par.paso_cantidad} ETH).")
        costo = monto * cfg.costo
        if monto + costo > cartera.efectivo + 1e-9:
            raise ErrorSaldoInsuficiente("La compra excede el efectivo disponible.")
        cartera.efectivo = round(cartera.efectivo - monto - costo, 8)
        cartera.unidades = round(cartera.unidades + cantidad, 8)
        return OrdenPaper("compra", cantidad, precio_ref, precio, monto, costo)

    if accion == "vender":
        if cartera.unidades <= 0:
            raise ErrorSaldoInsuficiente("No hay unidades para vender.")
        precio = round(precio_ref * (1 - cfg.deslizamiento), par.decimales_precio)
        cantidad = cartera.unidades
        monto = cantidad * precio
        costo = monto * cfg.costo
        cartera.efectivo = round(cartera.efectivo + monto - costo, 8)
        cartera.unidades = 0.0
        return OrdenPaper("venta", cantidad, precio_ref, precio, monto, costo)

    raise ValueError(f"Acción no reconocida: {accion}")
