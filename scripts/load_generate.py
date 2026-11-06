"""Genera las unidades de trabajo de la semana de carga (S4).

    python -m scripts.load_generate                          # 1.500 unidades en carga/unidades.jsonl
    python -m scripts.load_generate --total 50 --salida carga/piloto.jsonl
    python -m scripts.load_generate --reales carga/reales.jsonl   # mezcla mensajes reales de tenderos

Usa el mismo catálogo que la base de carga (scripts/seed.py). Con la misma semilla salen las mismas
unidades. Los mensajes reales van en un JSONL con el formato de app/load/units.py y origen "real".
"""

import argparse
from collections import Counter
from pathlib import Path

from app.load.generator import Generator, mix_real_units
from app.load.units import load_units, save_units
from scripts.seed import PRODUCTS, STORES


def seed_catalog() -> list[dict]:
    return [
        {"sku": sku, "name": name, "aliases": aliases, "presentation": presentation,
         "units_per_case": units_per_case, "unit_price": unit_price}
        for sku, name, aliases, presentation, units_per_case, unit_price in PRODUCTS
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--total", type=int, default=1500, help="unidades generadas (sin contar las reales)")
    parser.add_argument("--semilla", type=int, default=2026)
    parser.add_argument("--reales", type=Path, default=None, help="JSONL con mensajes reales para mezclar")
    parser.add_argument("--salida", type=Path, default=Path("carga/unidades.jsonl"))
    args = parser.parse_args()

    units = Generator(seed_catalog(), stores=len(STORES), seed=args.semilla).generate(args.total)
    real = load_units(args.reales) if args.reales else []
    if any(unit.origen != "real" for unit in real):
        raise SystemExit("Todas las unidades del archivo de reales deben tener origen \"real\".")
    units = mix_real_units(units, real, seed=args.semilla)
    save_units(units, args.salida)

    kinds = Counter(unit.tipo for unit in units)
    origins = Counter(unit.origen for unit in units)
    print(f"{len(units)} unidades en {args.salida}")
    print("  tipos: " + ", ".join(f"{kind} {count} ({count / len(units):.0%})" for kind, count in kinds.most_common()))
    print("  origen: " + ", ".join(f"{origin} {count}" for origin, count in origins.items()))


if __name__ == "__main__":
    main()
