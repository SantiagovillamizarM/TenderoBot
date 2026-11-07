"""Catálogo de una tienda con varios proveedores: junta los productos iguales y compara precios.

Funciones puras (sin base de datos). app/store_catalog.py carga los datos y las usa.

- Dos productos de proveedores distintos son el mismo artículo si el nombre se parece mucho y la
  presentación coincide (1 L = 1 litro = 1000 ml; 500 g; x 30...). Así "Coca-Cola 1 L" nunca se
  junta con "Coca-Cola 400 ml". Si el parecido no es claro, el artículo queda marcado como dudoso.
- Cada artículo tiene un proveedor preferido: el que escogió el tendero, o si no, el más barato que
  tenga existencias. A ese proveedor se le registran las ventas y se le pide.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal

from rapidfuzz import fuzz

SAME_NAME = 88  # parecido mínimo del nombre para juntar dos productos
SURE_NAME = 96  # desde aquí el emparejamiento no se marca como dudoso

_SIZE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(ml|cc|litros?|lts?|l|kilos?|kgs?|kg|gramos?|grs?|g)\b")
_PACK = re.compile(r"\bx\s*(\d+)\b")
_UNIT_FACTOR = {"ml": ("ml", 1), "cc": ("ml", 1), "l": ("ml", 1000), "lt": ("ml", 1000), "lts": ("ml", 1000),
                "litro": ("ml", 1000), "litros": ("ml", 1000), "g": ("g", 1), "gr": ("g", 1), "grs": ("g", 1),
                "gramo": ("g", 1), "gramos": ("g", 1), "kg": ("g", 1000), "kgs": ("g", 1000), "kilo": ("g", 1000),
                "kilos": ("g", 1000)}
# Palabras de tamaño que no ayudan a comparar nombres ("Coca-Cola 1 litro" vs "Coca-Cola 1L").
_SIZE_WORDS = re.compile(r"\b\d+(?:[.,]\d+)?\s*(ml|cc|litros?|lts?|l|kilos?|kgs?|kg|gramos?|grs?|g)\b|\bx\s*\d+\b")


def fold(text: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", (text or "").casefold())
    return " ".join("".join(ch for ch in normalized if not unicodedata.combining(ch)).split())


def size_key(presentation: str | None, name: str | None = None) -> str | None:
    """'1 L' -> '1000ml'; '400 ml' -> '400ml'; '1 kg' -> '1000g'; 'x 30' -> 'x30'. None si no dice tamaño."""
    for text in (fold(presentation), fold(name)):
        match = _SIZE.search(text)
        if match:
            unit, factor = _UNIT_FACTOR[match.group(2)]
            amount = Decimal(match.group(1).replace(",", ".")) * factor
            return f"{amount.normalize():f}{unit}"
        pack = _PACK.search(text)
        if pack:
            return f"x{int(pack.group(1))}"
    return None


# Palabras de empaque o descripción que no cambian el producto ("Gaseosa Coca Cola" = "Coca-Cola").
GENERIC_WORDS = {"gaseosa", "bebida", "cubeta", "bolsa", "botella", "lata", "paquete", "caja", "unidad", "und",
                 "entera", "original", "clasica", "tradicional", "personal", "de", "la", "el", "con",
                 # unidades sueltas que quedan al quitar el tamaño ("x 500 gr")
                 "x", "g", "gr", "grs", "gramo", "ml", "cc", "l", "lt", "lts", "litro", "kg", "kilo"}
# Palabras que SÍ cambian el producto: si solo un nombre la tiene, no son el mismo.
VARIANT_WORDS = {"deslactosada", "deslactosado", "descremada", "semidescremada", "light", "zero", "diet", "integral",
                 "sin", "azucar", "sabor", "fresa", "chocolate", "vainilla", "limon", "naranja", "manzana", "uva",
                 "kids", "junior", "max", "plus"}


def _singular(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def name_words(name: str | None) -> list[str]:
    """Palabras del nombre sin tamaño, signos, plurales ni palabras de empaque."""
    cleaned = re.sub(r"[^a-z0-9ñ ]", " ", _SIZE_WORDS.sub(" ", fold(name)))
    return [w for w in (_singular(word) for word in cleaned.split()) if w not in GENERIC_WORDS]


def name_key(name: str | None) -> str:
    """Nombre normalizado, para compararlo con el de otro proveedor."""
    return " ".join(name_words(name))


@dataclass
class Offer:
    """Un producto de un proveedor, tal como lo ve la tienda."""

    product: object  # app.models.Product (o algo con id, sku, name, presentation, unit_price...)
    distributor_id: int
    distributor_name: str

    @property
    def price(self) -> Decimal:
        return Decimal(self.product.unit_price)

    @property
    def sold_out(self) -> bool:
        available = getattr(self.product, "available_units", None)
        return available is not None and available < max(1, int(self.product.units_per_case or 1))


@dataclass
class Item:
    """Un artículo de la tienda: el mismo producto en uno o varios proveedores."""

    offers: list[Offer] = field(default_factory=list)
    doubtful: bool = False
    chosen: Offer | None = None  # el que escogió el tendero

    @property
    def key(self) -> str:
        return f"i{min(offer.product.id for offer in self.offers)}"

    @property
    def cheapest(self) -> Offer:
        in_stock = [offer for offer in self.offers if not offer.sold_out] or self.offers
        return min(in_stock, key=lambda offer: (offer.price, offer.distributor_name))

    @property
    def preferred(self) -> Offer:
        return self.chosen or self.cheapest

    @property
    def name(self) -> str:
        return self.preferred.product.name


def _match(offer: Offer, item: Item) -> tuple[int, bool] | None:
    """(parecido, dudoso) si el producto es el mismo artículo; None si no."""
    if any(other.distributor_id == offer.distributor_id for other in item.offers):
        return None  # un artículo tiene a lo sumo un producto de cada proveedor
    base = item.offers[0].product
    size_a = size_key(offer.product.presentation, offer.product.name)
    size_b = size_key(base.presentation, base.name)
    if size_a and size_b and size_a != size_b:
        return None
    words_a, words_b = set(name_words(offer.product.name)), set(name_words(base.name))
    if (words_a ^ words_b) & VARIANT_WORDS:
        return None  # "Leche Alquería" no es "Leche Alquería deslactosada"
    score = fuzz.token_set_ratio(" ".join(sorted(words_a)), " ".join(sorted(words_b)))
    if score < SAME_NAME:
        return None
    return int(score), score < SURE_NAME or words_a != words_b or not (size_a and size_b)


def group_offers(offers: list[Offer], chosen_product_ids: set[int] | None = None) -> list[Item]:
    """Junta los productos iguales de distintos proveedores en artículos."""
    chosen_product_ids = chosen_product_ids or set()
    items: list[Item] = []
    for offer in sorted(offers, key=lambda o: (o.distributor_id, o.product.id)):
        candidates = [(m, item) for item in items if (m := _match(offer, item)) is not None]
        if candidates:
            (score, doubtful), item = max(candidates, key=lambda pair: pair[0][0])
            item.offers.append(offer)
            item.doubtful = item.doubtful or doubtful
        else:
            items.append(Item(offers=[offer]))
    for item in items:
        item.chosen = next((offer for offer in item.offers if offer.product.id in chosen_product_ids), None)
    return sorted(items, key=lambda item: fold(item.name))


def match_item(items: list[Item], product) -> Item | None:
    """El artículo de hoy que es el mismo producto (de un proveedor que la tienda ya dejó, por ejemplo)."""
    offer = Offer(product, product.distributor_id, "")
    scored = [(m[0], item) for item in items if (m := _match(offer, item)) is not None]
    return max(scored, key=lambda pair: pair[0])[1] if scored else None


def find_item(items: list[Item], product_id: int) -> Item | None:
    return next((item for item in items if any(o.product.id == product_id for o in item.offers)), None)


# --- Comparar y escoger (lo usan el agente y /catalogo) -----------------------------------

PROVIDER_NAME_MATCH = 70


def find_by_text(items: list[Item], text: str) -> Item | None:
    """El artículo que nombra el tendero ("coca litro"), buscando en los productos de todos sus proveedores.

    Se compara por artículo: si la Coca-Cola de dos proveedores coincide igual, no es un empate (es el
    mismo artículo). Solo hay empate si dos artículos distintos se parecen igual: entonces no se adivina.
    """
    from app.ai.matching import MATCH_THRESHOLD, product_score, product_terms, query_variants

    query = (text or "").strip()
    if not query:
        return None
    variants = query_variants(query)
    best: Item | None = None
    best_score = -1.0
    tied = False
    for item in items:
        scores = [
            product_score(variants, terms, MATCH_THRESHOLD)
            for offer in item.offers
            if (terms := product_terms({"sku": offer.product.sku, "name": offer.product.name,
                                        "aliases": getattr(offer.product, "aliases", "") or ""}))
        ]
        score = max((value for value in scores if value is not None), default=None)
        if score is None:
            continue
        if score > best_score:
            best, best_score, tied = item, score, False
        elif score == best_score:
            tied = True
    return None if tied else best


def find_provider(item: Item, text: str) -> Offer | None:
    """El proveedor de ese artículo que nombra el tendero ("postobon", "el barrio")."""
    wanted = fold(text)
    scored = [(fuzz.WRatio(wanted, fold(offer.distributor_name)), offer) for offer in item.offers]
    best = max(scored, key=lambda pair: pair[0], default=(0, None))
    return best[1] if best[0] >= PROVIDER_NAME_MATCH else None


def comparison(item: Item) -> dict:
    cheapest = item.cheapest
    return {
        "producto": item.name,
        "le_pides_a": item.preferred.distributor_name,
        "lo_escogio_el_tendero": item.chosen is not None,
        "emparejamiento_dudoso": item.doubtful,
        "opciones": [
            {
                "proveedor": offer.distributor_name,
                "producto": offer.product.name,
                "precio_unidad": int(offer.price),
                "unidades_por_caja": int(offer.product.units_per_case),
                "agotado": offer.sold_out,
                "mas_barato": offer is cheapest,
            }
            for offer in sorted(item.offers, key=lambda o: o.price)
        ],
    }


def savings(items: list[Item], limit: int = 10) -> list[dict]:
    """Artículos que venden varios proveedores, con lo que se ahorra pidiéndole al más barato."""
    rows = []
    for item in items:
        if len(item.offers) < 2:
            continue
        preferred, cheapest = item.preferred, item.cheapest
        rows.append({
            "producto": item.name,
            "le_pides_a": preferred.distributor_name,
            "precio": int(preferred.price),
            "mas_barato": cheapest.distributor_name,
            "precio_mas_barato": int(cheapest.price),
            "ahorro_por_unidad": int(preferred.price - cheapest.price),
        })
    rows.sort(key=lambda row: row["ahorro_por_unidad"], reverse=True)
    return rows[:limit]
