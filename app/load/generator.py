"""Generador de unidades de trabajo para la semana de carga (S4).

Arma mensajes como los de un tendero (ventas, consultas, pedido, conversación) a partir del
catálogo, cada uno con su resultado esperado. Es determinista: la misma semilla da las mismas
unidades, para poder repetir una corrida.

Reglas para que lo esperado sea correcto:
- Solo se usan nombres que la búsqueda del catálogo resuelve al producto correcto.
- La cantidad siempre aparece escrita en el mensaje (si no, el agente debe preguntar).
- Los cambios al pedido van justo después de armar el pedido de esa tienda (borrador abierto).
"""

import random

from app.ai.matching import resolve_sku
from app.ai.tools import pack_size
from app.load.units import WorkUnit

# Mezcla por defecto (fracción de las unidades). Parecida a la proyección de costo de S3.
# "pedido" trae en promedio ~2 unidades (armar + cambios): con 0.055 queda cerca del 10% del total.
DEFAULT_MIX = {"venta": 0.62, "venta_dificil": 0.06, "consulta": 0.165, "pedido": 0.055, "conversacion": 0.10}
ACTION_TOOLS = ["registrar_ventas", "armar_pedido", "ajustar_pedido"]

# Alias que un tendero real no usaría para ese producto o que se prestan a confusión.
SKIPPED_ALIASES = {"tinto", "familia", "chicle", "rey", "huevo", "huevos", "arroza", "cubeta", "bombon"}
LARGE_PACK = 12
NUMBER_WORDS = {1: "un", 2: "dos", 3: "tres", 4: "cuatro", 5: "cinco", 6: "seis", 7: "siete", 8: "ocho",
                9: "nueve", 10: "diez", 12: "doce", 15: "quince", 20: "veinte"}

SALE_OPENERS = ["vendí", "hoy vendí", "se fueron", "salieron", "me compraron", "se vendieron", "despaché", ""]
# Solo preguntas por el stock de la tienda ("¿hay X?" podría ser lo que tiene el proveedor).
STOCK_QUESTIONS = ["cuántas {p} me quedan?", "tengo {p}?", "cuánto me queda de {p}", "cuántos {p} tengo?"]
PRICE_QUESTIONS = ["a cómo está {p}?", "cuánto vale {p}?", "qué precio tiene {p}?"]
ALERT_QUESTIONS = ["qué se me va a acabar?", "qué me está faltando?", "qué productos están en riesgo?",
                   "qué es lo que no se está vendiendo?", "qué tengo quieto en el estante?"]
ORDER_REQUESTS = ["qué me toca pedir?", "hazme el pedido", "arma el pedido de esta semana", "qué le pido al proveedor?"]
CHAT_MESSAGES = ["hola buenas", "buenos días", "gracias", "listo, gracias mijo", "ok perfecto", "buenas tardes",
                 "quién ganó el partido ayer?", "cómo está el clima hoy?", "me cuentas un chiste?"]
UNKNOWN_PRODUCTS = ["cervezas póker", "cigarrillos", "pilas", "velas", "tornillos", "minutos de celular",
                    "empanadas", "arepas"]


UNACCENT = str.maketrans("áéíóú", "aeiou")


def _pluralize(word: str) -> str:
    if word.endswith(("a", "e", "o")):
        return word + "s"
    if word.endswith(("n", "l", "r")):
        # "postobón" -> "postobones": la tilde de la última sílaba se pierde al agregar -es.
        return word[:-2] + word[-2:].translate(UNACCENT) + "es"
    return word


class Generator:
    def __init__(self, catalog: list[dict], stores: int, seed: int = 2026) -> None:
        if stores < 1:
            raise ValueError("se necesita al menos una tienda")
        self.catalog = catalog
        self.stores = stores
        self.rng = random.Random(seed)
        self.names = {str(p["sku"]): self._usable_names(p) for p in catalog}
        self.products = [p for p in catalog if self.names[str(p["sku"])]]
        self.count = 0

    def _usable_names(self, product: dict) -> list[str]:
        sku = str(product["sku"])
        terms = [t.strip() for t in str(product.get("aliases") or "").split(",") if t.strip()]
        terms.append(str(product["name"]))
        return [t for t in terms if t.casefold() not in SKIPPED_ALIASES and resolve_sku(None, t, self.catalog) == sku]

    # --- piezas -------------------------------------------------------------------

    def _id(self) -> str:
        self.count += 1
        return f"U{self.count:05d}"

    def _quantity_text(self, quantity: int) -> str:
        if quantity in NUMBER_WORDS and self.rng.random() < 0.4:
            return NUMBER_WORDS[quantity]
        return str(quantity)

    def _mention(self, product: dict, quantity: int) -> tuple[str, str]:
        """'3 cocas litro' y el texto de la cantidad.

        Los productos de paquete grande (los huevos, "x 30") se nombran por cubeta: "2 huevos" sería
        ambiguo y el agente debe preguntar (eso lo prueban las unidades difíciles).
        """
        sku = str(product["sku"])
        quantity_text = self._quantity_text(quantity)
        if pack_size(product) >= LARGE_PACK:
            unit = "cubeta" if quantity == 1 else "cubetas"
            return f"{quantity_text} {unit} de huevos", quantity_text
        name = self.rng.choice(self.names[sku])
        if quantity > 1 and self.rng.random() < 0.5:
            words = name.split()
            plural = " ".join([_pluralize(words[0])] + words[1:])
            if resolve_sku(None, plural, self.catalog) == sku:
                name = plural
        return f"{quantity_text} {name}", quantity_text

    def _join(self, parts: list[str]) -> str:
        if len(parts) == 1:
            return parts[0]
        separator = self.rng.choice([", ", " , ", ","]) if self.rng.random() < 0.3 else ", "
        return separator.join(parts[:-1]) + " y " + parts[-1]

    def _store(self) -> int:
        return self.rng.randrange(self.stores)

    # --- tipos de unidad ------------------------------------------------------------

    def sale(self) -> WorkUnit:
        chosen = self.rng.sample(self.products, k=self.rng.choice([1, 1, 2, 2, 3, 4]))
        parts, expected = [], []
        for product in chosen:
            quantity = self.rng.choice([1, 1, 2, 2, 3, 4, 5, 6, 10, 12])
            mention, _said = self._mention(product, quantity)
            parts.append(mention)
            expected.append({"sku": str(product["sku"]), "cantidad": quantity})
        opener = self.rng.choice(SALE_OPENERS)
        text = f"{opener} {self._join(parts)}".strip()
        return WorkUnit(self._id(), self._store(), "venta", text,
                        {"herramientas": ["registrar_ventas"], "ventas": expected}, confirmar=True)

    def hard_sale(self) -> WorkUnit:
        kind = self.rng.choice(["sin_cantidad", "desconocido", "huevos_sueltos"])
        if kind == "sin_cantidad":
            product = self.rng.choice(self.products)
            text = f"{self.rng.choice(['vendí', 'se fue', 'me compraron'])} {self.rng.choice(self.names[str(product['sku'])])}"
            expected = {"ventas": []}
        elif kind == "desconocido":
            text = f"vendí {self.rng.choice([2, 3, 5])} {self.rng.choice(UNKNOWN_PRODUCTS)}"
            expected = {"ventas": []}
        else:
            text = f"vendí {self.rng.choice(['media docena de huevos', '6 huevos sueltos', 'una docena de huevos sueltos'])}"
            expected = {"ventas": []}
        return WorkUnit(self._id(), self._store(), "venta_dificil", text, expected)

    def query(self) -> WorkUnit:
        kind = self.rng.choice(["stock", "stock", "precio", "alertas"])
        no_actions = {"no_debe_llamar": ACTION_TOOLS}
        if kind == "stock":
            product = self.rng.choice(self.products)
            name = self.rng.choice(self.names[str(product["sku"])])
            text = self.rng.choice(STOCK_QUESTIONS).format(p=name)
            expected = {"herramientas": ["consultar_stock"], "stock_de": str(product["sku"]), **no_actions}
        elif kind == "precio":
            product = self.rng.choice(self.products)
            name = self.rng.choice(self.names[str(product["sku"])])
            price = int(product["unit_price"])
            variants = "|".join({f"{price:,}".replace(",", "."), str(price), f"{price:,}"})
            text = self.rng.choice(PRICE_QUESTIONS).format(p=name)
            expected = {"herramientas": ["buscar_producto"], "respuesta_contiene": [variants], **no_actions}
        else:
            text = self.rng.choice(ALERT_QUESTIONS)
            expected = {"herramientas": ["ver_alertas"], **no_actions}
        return WorkUnit(self._id(), self._store(), "consulta", text, expected)

    def order(self) -> list[WorkUnit]:
        """Armar el pedido y, a veces, uno o dos cambios seguidos (la misma tienda, borrador abierto)."""
        store = self._store()
        units = [WorkUnit(self._id(), store, "pedido", self.rng.choice(ORDER_REQUESTS),
                          {"herramientas": ["armar_pedido"], "no_debe_llamar": ["registrar_ventas"]})]
        for _ in range(self.rng.choice([0, 1, 1, 2])):
            product = self.rng.choice([p for p in self.products if pack_size(p) < LARGE_PACK])
            sku = str(product["sku"])
            name = self.rng.choice(self.names[sku])
            action = self.rng.choice(["add", "add", "remove", "set"])
            quantity = self.rng.choice([1, 2, 3])
            said = self._quantity_text(quantity)
            if action == "add":
                text = f"{self.rng.choice(['ponle', 'agrégale', 'súmale'])} {said} {'caja' if quantity == 1 else 'cajas'} más de {name}"
                change = {"sku": sku, "accion": "add", "cantidad": quantity, "unidad": "case"}
            elif action == "set":
                text = f"que sean {said} {'caja' if quantity == 1 else 'cajas'} de {name}"
                change = {"sku": sku, "accion": "set", "cantidad": quantity, "unidad": "case"}
            else:
                text = f"{self.rng.choice(['quita', 'quítale', 'no me traigas'])} {name}"
                change = {"sku": sku, "accion": "remove", "cantidad": 0}
            units.append(WorkUnit(self._id(), store, "pedido", text,
                                  {"herramientas": ["ajustar_pedido"], "cambios": [change],
                                   "no_debe_llamar": ["registrar_ventas"]}))
        return units

    def chat(self) -> WorkUnit:
        return WorkUnit(self._id(), self._store(), "conversacion", self.rng.choice(CHAT_MESSAGES),
                        {"no_debe_llamar": ACTION_TOOLS})

    # --- lote -----------------------------------------------------------------------

    def generate(self, total: int, mix: dict[str, float] | None = None) -> list[WorkUnit]:
        mix = mix or DEFAULT_MIX
        kinds, weights = zip(*mix.items())
        makers = {"venta": self.sale, "venta_dificil": self.hard_sale, "consulta": self.query,
                  "pedido": self.order, "conversacion": self.chat}
        units: list[WorkUnit] = []
        while len(units) < total:
            made = makers[self.rng.choices(kinds, weights=weights)[0]]()
            units.extend(made if isinstance(made, list) else [made])
        # Cortar al final puede dejar un pedido sin sus cambios: no rompe nada (el armado va primero).
        return units[:total]


def mix_real_units(generated: list[WorkUnit], real: list[WorkUnit], seed: int = 2026) -> list[WorkUnit]:
    """Mete los mensajes reales repartidos entre los generados (sin romper las secuencias de pedido)."""
    if not real:
        return generated
    rng = random.Random(seed)
    result = list(generated)
    stores = {unit.tienda for unit in generated}
    for unit in real:
        if stores and unit.tienda not in stores:
            raise ValueError(f"{unit.id}: la tienda {unit.tienda} no existe en la carga")
        positions = [i for i in range(len(result) + 1) if i == len(result) or result[i].tipo != "pedido"
                     or "ajustar_pedido" not in result[i].esperado.get("herramientas", [])]
        result.insert(rng.choice(positions), unit)
    return result
