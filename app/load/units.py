"""Unidad de trabajo de la semana de carga (S4).

Una unidad es UN mensaje de un tendero que el agente atiende de punta a punta, igual que en el bot:
Grok escoge herramientas, las herramientas leen y escriben en la base (borradores de ventas y de
pedido, stock, alertas) y al final el código revisa si el resultado fue el correcto.

Cada unidad trae:
- tienda: índice de la tienda de la base de carga (0, 1, 2...) que manda el mensaje.
- texto: lo que escribe el tendero.
- esperado: lo que debe pasar, con las mismas claves de la evaluación de S3 (app/evaluation.py):
  herramientas, no_debe_llamar, ventas, cambios, respuesta_contiene... Además:
  - stock_de: sku cuyo stock (leído de la base justo antes de correr) debe decir la respuesta.
- confirmar: si el tendero pulsaría ✅ Confirmar cuando el resultado es correcto (ventas). Así las
  ventas de la carga mueven el inventario de verdad, como en una tienda.
- origen: "generado" (lo armó el generador) o "real" (mensaje real de un tendero).
- tipo: venta, venta_dificil, consulta, pedido, conversacion.
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

ORIGINS = ("generado", "real")
KINDS = ("venta", "venta_dificil", "consulta", "pedido", "conversacion")


@dataclass
class WorkUnit:
    id: str
    tienda: int
    tipo: str
    texto: str
    esperado: dict = field(default_factory=dict)
    confirmar: bool = False
    origen: str = "generado"

    def __post_init__(self) -> None:
        if self.origen not in ORIGINS:
            raise ValueError(f"origen inválido: {self.origen}")
        if self.tipo not in KINDS:
            raise ValueError(f"tipo inválido: {self.tipo}")
        if not self.texto.strip():
            raise ValueError("la unidad no tiene texto")


def save_units(units: list[WorkUnit], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(asdict(unit), ensure_ascii=False) + "\n" for unit in units), encoding="utf-8")


def load_units(path: Path) -> list[WorkUnit]:
    units = [WorkUnit(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    ids = [unit.id for unit in units]
    if len(ids) != len(set(ids)):
        raise ValueError("hay unidades con id repetido")
    return units
