"""Herramientas que el agente del tendero puede usar (tool calling).

Aquí están los esquemas que ve Grok y la validación de lo que pide. El trabajo real (leer el
inventario, guardar borradores) lo hace el bot a través de `StoreActions`, así esta capa no
depende de Telegram ni de la base de datos y se puede probar sola.

Ninguna herramienta guarda ventas ni envía pedidos: solo crea borradores. El tendero siempre
confirma con un botón.
"""

import re
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import ValidationError
from rapidfuzz import fuzz

from app.ai.agent import Tool, ToolError
from app.ai.matching import _singular, match_order_changes, match_sale_lines, resolve_sku, search_products
from app.schemas import OrderChange, SaleLine

MAX_LINES = 30

# "x 30", "x 4": el producto se vende en paquete (cubeta, paca...).
PACK_PATTERN = re.compile(r"^\s*x\s*(\d+)", re.IGNORECASE)
# Palabras de unidades sueltas: si aparecen con un producto de paquete, hay que preguntar.
LOOSE_WORDS = ("suelt", "docena")
# Paquetes grandes (la cubeta de 30 huevos): "10 huevos" casi siempre son huevos sueltos, no 10 cubetas.
# Solo se registra si el tendero nombró el paquete.
BIG_PACK = 12
PACK_WORDS = ("cubeta", "panal", "bandeja", "paca", "paquete", "x 30", "x30")
# Palabras del nombre de un proveedor que no sirven para reconocerlo ("Distribuidora El Barrio" → "barrio").
GENERIC_PROVIDER_WORDS = {"distribuidora", "distribuidor", "mayorista", "comercial", "santander", "de", "del", "la",
                          "el", "los", "las", "y", "sas", "ltda"}
PROVIDER_MATCH = 85
# "al pedido de Postobón", "pídele a La Economía": el proveedor que nombra el tendero.
PROVIDER_PHRASE = re.compile(
    r"\b(?:pedido (?:de|a|para|con)|pidele a|pedirle a|pidale a|encargale a|a|con)\s+(?:la |el |los )?(\w+)"
)


def fold(text: str) -> str:
    """Minúsculas, sin tildes y con espacios simples, para comparar con lo que escribió el tendero."""
    normalized = unicodedata.normalize("NFKD", text.casefold())
    return " ".join("".join(ch for ch in normalized if not unicodedata.combining(ch)).split())


def pack_size(product: dict) -> int:
    match = PACK_PATTERN.match(str(product.get("presentation") or ""))
    return int(match.group(1)) if match else 1


def provider_words(name: str) -> list[str]:
    """Las palabras que identifican a un proveedor: 'Mayorista La Economía' → ['economia']."""
    return [word for word in fold(name).split() if word not in GENERIC_PROVIDER_WORDS and len(word) > 2]


def provider_matches(text: str, supplier: str | None) -> bool:
    """¿El proveedor que nombró el tendero ('postobon', 'la economía') es este?"""
    if not supplier:
        return False
    wanted = [word for word in fold(text).split() if word not in GENERIC_PROVIDER_WORDS]
    if not wanted:
        return False
    words = provider_words(supplier)
    return any(fuzz.ratio(want, word) >= PROVIDER_MATCH for want in wanted for word in words)


def provider_in_message(message: str | None, suppliers: set[str]) -> str | None:
    """El proveedor que el tendero nombra en el mensaje ('al pedido de Postobón'), si nombra uno solo."""
    if not message or len(suppliers) < 2:
        return None
    named = {
        supplier
        for match in PROVIDER_PHRASE.finditer(fold(message))
        for supplier in suppliers
        if provider_matches(match.group(1), supplier)
    }
    return named.pop() if len(named) == 1 else None


def query_words(text: str) -> list[str]:
    return [_singular(word) for word in fold(text).split() if word not in {"de", "del", "la", "el", "un", "una"}]


def products_containing(text: str, catalog: list[dict]) -> list[dict]:
    """Productos cuyo nombre o alias trae todas las palabras del texto ('arroz' → Arroz Diana y Arroz Roa)."""
    words = query_words(text)
    if not words:
        return []
    found = []
    for product in catalog:
        terms = [fold(str(product.get("name") or ""))] + [
            fold(alias) for alias in str(product.get("aliases") or "").split(",") if alias.strip()
        ]
        if any(all(word in term for word in words) for term in terms):
            found.append(product)
    return found


@dataclass
class StoreActions:
    """Lo que el bot le da al agente para una tienda.

    catalog: productos activos del proveedor, cada uno con sku, name, aliases, presentation,
    unit_price, units_per_case y sold_out.
    """

    catalog: list[dict]
    stock: Callable[[], Awaitable[list[dict]]]
    alerts: Callable[[], Awaitable[dict]]
    draft_sales: Callable[[list[SaleLine]], Awaitable[dict]]
    suggest_order: Callable[[], Awaitable[dict]]
    change_order: Callable[[list[OrderChange]], Awaitable[dict]]
    # Con varios proveedores (opcionales: la evaluación y la carga no los usan).
    compare: Callable[[str], Awaitable[dict]] | None = None
    prefer: Callable[[str, str], Awaitable[dict]] | None = None
    # Preguntar antes de armar ("✍️ Armarlo yo" o "📋 Ver pedido sugerido"). Sin ella se arma el sugerido.
    # Recibe el proveedor que nombró el tendero ("" = todos).
    ask_order: Callable[[str], Awaitable[dict]] | None = None
    # El pedido sugerido de un solo proveedor ("¿qué le pido a La Economía?").
    suggest_order_for: Callable[[str], Awaitable[dict]] | None = None


def _product_view(product: dict) -> dict:
    view = {
        "sku": product.get("sku"),
        "nombre": product.get("name"),
        "presentacion": product.get("presentation"),
        "precio_unidad": product.get("unit_price"),
        "unidades_por_caja": product.get("units_per_case"),
        "agotado": bool(product.get("sold_out")),
    }
    if product.get("supplier"):
        view["proveedor"] = product["supplier"]
    return view


def _text_arg(args: dict, key: str, required: bool = True) -> str:
    value = args.get(key)
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()):
        raise ToolError(f"Falta '{key}' (texto)")
    return value.strip()


def _list_arg(args: dict, key: str) -> list[dict]:
    value = args.get(key)
    if not isinstance(value, list) or not value:
        raise ToolError(f"Falta '{key}' (lista con al menos un elemento)")
    if len(value) > MAX_LINES:
        raise ToolError(f"Máximo {MAX_LINES} elementos en '{key}'")
    if not all(isinstance(item, dict) for item in value):
        raise ToolError(f"Cada elemento de '{key}' debe ser un objeto")
    return value


def _sku_arg(item: dict) -> str | None:
    sku = item.get("sku")
    if not isinstance(sku, str):
        return None
    return sku.strip() or None


def _not_found(text: str, catalog: list[dict]) -> dict:
    several = products_containing(text, catalog)
    if len(several) > 1:
        # "cocas": sí existe, pero en varias presentaciones. No es "no lo reconocí": hay que preguntar cuál.
        return {
            "producto": text,
            "varias_opciones": [product.get("name") for product in several][:5],
            "nota": "Sí lo vende, en varias presentaciones: pregúntale cuál (no digas que no lo reconociste).",
        }
    return {
        "producto": text,
        "parecidos": [product.get("name") for product in search_products(text, catalog)],
    }


SALE_WORDS = ("vend", "venta", "salio", "salieron")


def build_tools(actions: StoreActions, user_message: str | None = None, building_order: str | None = None) -> list[Tool]:
    """`user_message` es lo que escribió (o dijo) el tendero: con él se verifica que la cantidad que
    cita el modelo esté de verdad en el mensaje. Sin él (pruebas), se confía en la cita.

    `building_order`: el proveedor del pedido que el tendero está armando él mismo (✍️ Armarlo yo).
    Mientras tanto, "3 cajas de coca" son productos para el pedido, no ventas (salvo que diga "vendí").
    """
    catalog = actions.catalog
    names = {str(product["sku"]): product.get("name") for product in catalog}

    async def buscar_producto(args: dict) -> dict:
        text = _text_arg(args, "texto")
        found = search_products(text, catalog)
        return {"resultados": [_product_view(product) for product in found]}

    async def consultar_stock(args: dict) -> dict:
        text = _text_arg(args, "producto", required=False)
        rows = await actions.stock()
        if not text:
            return {"stock": rows}
        several = products_containing(text, catalog)
        if len(several) > 1:
            # "¿cuánto me queda de arroz?" con Arroz Diana y Arroz Roa: se responde de los dos.
            skus = {str(product["sku"]) for product in several}
            return {"stock": [row for row in rows if str(row.get("sku")) in skus],
                    "nota": "Son varios productos: dile el stock de cada uno."}
        sku = resolve_sku(None, text, catalog)
        if sku is None:
            return {"no_encontrado": _not_found(text, catalog)}
        return {"stock": [row for row in rows if row.get("sku") == sku]}

    async def ver_alertas(args: dict) -> dict:
        return await actions.alerts()

    by_sku = {str(product["sku"]): product for product in catalog}

    def needs_question(line: SaleLine, said_quantity: str) -> str | None:
        """Por qué hay que preguntarle al tendero antes de registrar esta línea (None = se registra).

        Lo decide el código, no el modelo: en la evaluación, con solo instrucciones, Grok a veces
        asumía 1 cuando el tendero no dijo cuántas, o registraba huevos sueltos como cubetas.
        """
        if not said_quantity:
            return "No dijo cuántas vendió"
        if user_message is not None and fold(said_quantity) not in fold(user_message):
            # El modelo citó una cantidad que el tendero no dijo ("una" cuando solo escribió "vendí pony").
            return "No dijo cuántas vendió"
        size = pack_size(by_sku.get(str(line.sku), {}))
        said = f"{said_quantity} {line.product_text}".casefold()
        if size > 1 and any(word in said for word in LOOSE_WORDS):
            presentation = by_sku[str(line.sku)].get("presentation")
            return f"Habla de unidades sueltas y el producto se vende en paquete ({presentation})"
        if size >= BIG_PACK:
            # "vendí 10 huevos": sin decir "cubeta" no se sabe si son 10 cubetas o 10 huevos sueltos.
            context = fold(user_message if user_message is not None else said)
            if not any(word in context for word in PACK_WORDS):
                presentation = by_sku[str(line.sku)].get("presentation")
                return (f"No dijo si son paquetes ({presentation}) o unidades sueltas: pregúntale, "
                        "por ejemplo '¿son cubetas o huevos sueltos?'")
        return None

    async def registrar_ventas(args: dict) -> dict:
        if building_order is not None and user_message is not None and not any(
            word in fold(user_message) for word in SALE_WORDS
        ):
            return {
                "borrador": "no_creado",
                "error": f"El tendero está armando su pedido a {building_order}: esos productos son para el "
                         "pedido, no ventas. Usa ajustar_pedido con ellos.",
            }
        lines: list[tuple[SaleLine, str]] = []
        for item in _list_arg(args, "ventas"):
            try:
                line = SaleLine(
                    sku=_sku_arg(item),
                    product_text=str(item.get("producto") or "").strip(),
                    quantity=item.get("cantidad"),
                )
            except ValidationError as exc:
                raise ToolError("Cada venta necesita 'producto' y 'cantidad' entera mayor que 0") from exc
            said = item.get("texto_cantidad")
            lines.append((line, said.strip() if isinstance(said, str) else ""))
        matched = match_sale_lines([line for line, _said in lines], catalog)
        recognized: list[SaleLine] = []
        unknown: list[SaleLine] = []
        to_ask: list[dict] = []
        for line, (_original, said) in zip(matched, lines, strict=True):
            if not line.sku:
                unknown.append(line)
            elif (reason := needs_question(line, said)) is not None:
                to_ask.append({"producto": names.get(line.sku), "motivo": reason})
            else:
                recognized.append(line)
        result = await actions.draft_sales(recognized)
        reply = {
            **result,
            "reconocidas": [{"producto": names.get(line.sku), "cantidad": line.quantity} for line in recognized],
            "no_reconocidas": [_not_found(line.product_text, catalog) for line in unknown],
            "nota": "Aún no se guardó: el tendero debe pulsar ✅ Confirmar en el resumen.",
        }
        if to_ask:
            reply["preguntar"] = to_ask
            reply["nota_preguntar"] = "Estas no se registraron: pregúntale al tendero antes de registrarlas."
        return reply

    async def armar_pedido(args: dict) -> dict:
        provider = _text_arg(args, "proveedor", required=False)
        if args.get("sugerido") is True or actions.ask_order is None:
            if provider and actions.suggest_order_for is not None:
                return await actions.suggest_order_for(provider)
            return await actions.suggest_order()
        # El tendero decide: el bot le pregunta con botones si lo arma él o quiere ver el sugerido.
        return await actions.ask_order(provider)

    suppliers = {str(product["supplier"]) for product in catalog if product.get("supplier")}
    named_in_message = provider_in_message(user_message, suppliers)

    async def ajustar_pedido(args: dict) -> dict:
        changes: list[OrderChange] = []
        wanted: list[str] = []
        for item in _list_arg(args, "cambios"):
            try:
                changes.append(
                    OrderChange(
                        sku=_sku_arg(item),
                        product_text=str(item.get("producto") or "").strip(),
                        action=item.get("accion"),
                        quantity=item.get("cantidad", 0),
                        unit=item.get("unidad") or "case",
                    )
                )
            except ValidationError as exc:
                raise ToolError(
                    "Cada cambio necesita 'producto', 'accion' (add, remove o set), 'cantidad' y 'unidad'"
                ) from exc
            said = item.get("proveedor")
            wanted.append(said.strip() if isinstance(said, str) and said.strip() else (named_in_message or ""))
        changes = match_order_changes(changes, catalog)
        accepted: list[OrderChange] = []
        unknown: list[OrderChange] = []
        not_applied: list[dict] = []
        for change, provider in zip(changes, wanted, strict=True):
            if provider and suppliers:
                own = [product for product in catalog if provider_matches(provider, product.get("supplier"))]
                if not change.sku and own:
                    # "quita el arroz del pedido de La Economía": se busca entre los productos de ese proveedor.
                    sku = resolve_sku(None, change.product_text, own)
                    change = change.model_copy(update={"sku": sku})
                product = by_sku.get(str(change.sku)) if change.sku else None
                if product is not None and not provider_matches(provider, product.get("supplier")):
                    # El tendero pidió cambiar el pedido de un proveedor que no le vende ese producto:
                    # no se pasa en silencio al pedido de otro.
                    not_applied.append({
                        "producto": product.get("name"),
                        "proveedor_que_dijo": provider,
                        "se_lo_pides_a": product.get("supplier"),
                    })
                    continue
            if change.sku:
                accepted.append(change)
            else:
                unknown.append(change)
        result = await actions.change_order(accepted) if accepted else {"borrador": "sin_cambios"}
        reply = {**result, "no_reconocidos": [_not_found(change.product_text, catalog) for change in unknown]}
        if not_applied:
            reply["no_aplicados"] = not_applied
            reply["nota_no_aplicados"] = (
                "Esos productos NO se cambiaron: el proveedor que nombró no se los vende (o hoy se los pide a "
                "otro). Díselo y pregúntale si quiere el cambio en el pedido de 'se_lo_pides_a'."
            )
        return reply

    sku_field = {"type": ["string", "null"], "description": "sku del catálogo si estás seguro; si no, null"}
    return [
        Tool(
            "buscar_producto",
            "Busca productos del proveedor por nombre o como los llama el tendero. "
            "Devuelve sku, precio por unidad, unidades por caja y si está agotado.",
            {
                "type": "object",
                "properties": {"texto": {"type": "string", "description": "Lo que dijo el tendero, ej. 'coca litro'"}},
                "required": ["texto"],
            },
            buscar_producto,
        ),
        Tool(
            "consultar_stock",
            "Cuántas unidades tiene la tienda. Sin 'producto' devuelve todo el inventario.",
            {
                "type": "object",
                "properties": {"producto": {"type": "string", "description": "Producto a consultar (opcional)"}},
            },
            consultar_stock,
        ),
        Tool(
            "ver_alertas",
            "Productos que se van a acabar antes de la próxima entrega y productos que no se están vendiendo.",
            {"type": "object", "properties": {}},
            ver_alertas,
        ),
        Tool(
            "registrar_ventas",
            "Prepara un resumen de ventas para que el tendero lo confirme. Úsala cuando el tendero cuenta "
            "lo que vendió, aunque no diga 'vendí' (ej. '2 coca litro y un pan'). Busca sola los productos "
            "en el catálogo. No guarda nada sola. Si falta la cantidad, no la uses: primero pregúntale.",
            {
                "type": "object",
                "properties": {
                    "ventas": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "producto": {"type": "string", "description": "Como lo dijo el tendero"},
                                "cantidad": {
                                    "type": "integer",
                                    "description": "Cantidad que dijo el tendero. Nunca la inventes",
                                },
                                "texto_cantidad": {
                                    "type": "string",
                                    "description": "Las palabras exactas con que el tendero dijo la cantidad "
                                    "('2', 'una', 'cinco', 'media docena'). Vacío si no la dijo",
                                },
                                "sku": sku_field,
                            },
                            "required": ["producto", "cantidad", "texto_cantidad"],
                        },
                    }
                },
                "required": ["ventas"],
            },
            registrar_ventas,
        ),
        Tool(
            "armar_pedido",
            "Pedido para la próxima entrega. Sin 'sugerido', el bot le pregunta al tendero con botones si "
            "quiere armarlo él mismo o ver el pedido sugerido (úsala así cuando dice que quiere hacer un "
            "pedido). Con sugerido=true arma el pedido sugerido (en cajas, con motivo y total) como borrador: "
            "solo si pide el sugerido o pregunta qué debería pedir.",
            {
                "type": "object",
                "properties": {
                    "sugerido": {
                        "type": "boolean",
                        "description": "true solo si pide el pedido sugerido o qué debería pedir",
                    },
                    "proveedor": {
                        "type": "string",
                        "description": "Solo si nombra un proveedor ('¿qué le pido a La Economía?'). Si no, omítelo",
                    },
                },
            },
            armar_pedido,
        ),
        Tool(
            "ajustar_pedido",
            "Cambia el pedido que el tendero está revisando: agregar, quitar o dejar una cantidad exacta. "
            "Solo si el tendero pide cambiar el pedido. Vender un producto nunca es quitarlo del pedido.",
            {
                "type": "object",
                "properties": {
                    "cambios": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "producto": {"type": "string", "description": "Como lo dijo el tendero"},
                                "accion": {
                                    "type": "string",
                                    "enum": ["add", "remove", "set"],
                                    "description": "add=agregar o poner más, remove=quitar, set=dejar cantidad exacta",
                                },
                                "cantidad": {
                                    "type": "integer",
                                    "description": "El número que dijo, sin convertir. remove=0; add sin número=1",
                                },
                                "unidad": {
                                    "type": "string",
                                    "enum": ["case", "unit"],
                                    "description": "case si dice cajas o no dice número ('agrega coca'); unit si "
                                    "cuenta el producto por unidades ('12 cocas', '6 leches')",
                                },
                                "proveedor": {
                                    "type": "string",
                                    "description": "Solo si el tendero dice a qué proveedor o a qué pedido "
                                    "('al pedido de Postobón'). Si no lo dice, no lo pongas",
                                },
                                "sku": sku_field,
                            },
                            "required": ["producto", "accion", "cantidad", "unidad"],
                        },
                    }
                },
                "required": ["cambios"],
            },
            ajustar_pedido,
        ),
    ] + _supplier_tools(actions)


def _supplier_tools(actions: StoreActions) -> list[Tool]:
    """Comparar precios entre proveedores y escoger a cuál pedirle cada producto."""
    tools: list[Tool] = []
    if actions.compare is not None:
        compare = actions.compare

        async def comparar_proveedores(args: dict) -> dict:
            return await compare(_text_arg(args, "producto", required=False))

        tools.append(Tool(
            "comparar_proveedores",
            "Compara los precios de un producto entre los proveedores del tendero y dice cuál le está "
            "pidiendo (el preferido). Sin 'producto', lista los productos que venden varios proveedores "
            "y cuánto se ahorra con el más barato.",
            {"type": "object", "properties": {"producto": {"type": "string", "description": "Ej. 'coca litro'"}}},
            comparar_proveedores,
        ))
    if actions.prefer is not None:
        prefer = actions.prefer

        async def preferir_proveedor(args: dict) -> dict:
            return await prefer(_text_arg(args, "producto"), _text_arg(args, "proveedor"))

        tools.append(Tool(
            "preferir_proveedor",
            "Cambia a qué proveedor se le pide un producto que venden varios. Úsala solo cuando el tendero "
            "lo pida (ej. 'la coca pídesela a Postobón').",
            {
                "type": "object",
                "properties": {
                    "producto": {"type": "string", "description": "Como lo dijo el tendero"},
                    "proveedor": {"type": "string", "description": "Nombre del proveedor, como lo dijo el tendero"},
                },
                "required": ["producto", "proveedor"],
            },
            preferir_proveedor,
        ))
    return tools
