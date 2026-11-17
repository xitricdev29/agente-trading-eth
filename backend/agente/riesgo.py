"""Controles de riesgo deterministas. La propuesta del LLM nunca sustituye estas reglas."""
from dataclasses import dataclass

from .config import ConfigRiesgo


@dataclass
class Cartera:
    efectivo: float
    unidades: float = 0.0
    patrimonio_referencia: float | None = None    # patrimonio al cierre de la vela anterior procesada

    def patrimonio(self, precio: float) -> float:
        return self.efectivo + self.unidades * precio

    @property
    def en_posicion(self) -> bool:
        return self.unidades > 0


@dataclass
class Veredicto:
    aprobada: bool
    accion_final: str           # comprar | vender | mantener | ninguna
    motivo: str
    bloqueo: bool = False       # True si se alcanzó un límite y el agente debe bloquear nuevas órdenes


def evaluar(propuesta: dict, cartera: Cartera, precio: float, cfg: ConfigRiesgo, estado_agente: str) -> Veredicto:
    accion = propuesta["accion"]

    if estado_agente not in ("en_marcha", "replay"):
        return Veredicto(False, "ninguna", f"Agente en estado '{estado_agente}': órdenes bloqueadas.")

    # Pérdida diaria máxima: compara el patrimonio actual contra el de la vela anterior
    if cartera.patrimonio_referencia:
        caida = 1 - cartera.patrimonio(precio) / cartera.patrimonio_referencia
        if caida >= cfg.perdida_diaria_maxima:
            return Veredicto(False, "ninguna",
                             f"Pérdida diaria de {caida:.2%} ≥ límite {cfg.perdida_diaria_maxima:.0%}: se bloquean nuevas órdenes.",
                             bloqueo=True)

    if accion == "mantener":
        return Veredicto(True, "mantener", "Sin operación.")
    if accion == "comprar":
        if cartera.en_posicion:
            return Veredicto(False, "mantener", "Ya hay posición abierta; no se recompra (sin apalancamiento).")
        if cartera.efectivo <= 0:
            return Veredicto(False, "mantener", "Sin efectivo disponible.")
        return Veredicto(True, "comprar", f"Compra aprobada hasta {cfg.exposicion_maxima:.0%} del patrimonio.")
    if accion == "vender":
        if not cartera.en_posicion:
            return Veredicto(False, "mantener", "No hay posición que vender (sin ventas en corto).")
        return Veredicto(True, "vender", "Venta total de la posición aprobada.")
    return Veredicto(False, "ninguna", f"Acción desconocida {accion!r}.")
