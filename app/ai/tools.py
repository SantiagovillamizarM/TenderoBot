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

from app.ai.agent import Tool, ToolError
from app.ai.matching import match_order_changes, match_sale_lines, resolve_sku, search_products
from app.schemas import OrderChange, SaleLine

MAX_LINES = 30

# "x 30", "x 4": el producto se vende en paquete (cubeta, paca...).
PACK_PATTERN = re.compile(r"^\s*x\s*(\d+)", re.IGNORECASE)
# Palabras de unidades sueltas: si aparecen con un producto de paquete, hay que preguntar.
LOOSE_WORDS = ("suelt", "docena")


def fold(text: str) -> str:
    """Minúsculas, sin tildes y con espacios simples, para comparar con lo que escribió el tendero."""
    normalized = unicodedata.normalize("NFKD", text.casefold())
    return " ".join("".join(ch for ch in normalized if not unicodedata.combining(ch)).split())


def pack_size(product: dict) -> int:
    match = PACK_PATTERN.match(str(product.get("presentation") or ""))
    return int(match.group(1)) if match else 1


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


def _product_view(product: dict) -> dict:
    return {
        "sku": product.get("sku"),
        "nombre": product.get("name"),
        "presentacion": product.get("presentation"),
        "precio_unidad": product.get("unit_price"),
        "unidades_por_caja": product.get("units_per_case"),
        "agotado": bool(product.get("sold_out")),
    }


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
    return {
        "producto": text,
        "parecidos": [product.get("name") for product in search_products(text, catalog)],
    }


def build_tools(actions: StoreActions, user_message: str | None = None) -> list[Tool]:
    """`user_message` es lo que escribió (o dijo) el tendero: con él se verifica que la cantidad que
    cita el modelo esté de verdad en el mensaje. Sin él (pruebas), se confía en la cita."""
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
        return None

    async def registrar_ventas(args: dict) -> dict:
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
        return await actions.suggest_order()

    async def ajustar_pedido(args: dict) -> dict:
        changes: list[OrderChange] = []
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
        changes = match_order_changes(changes, catalog)
        unknown = [change for change in changes if not change.sku]
        result = await actions.change_order([change for change in changes if change.sku])
        return {**result, "no_reconocidos": [_not_found(change.product_text, catalog) for change in unknown]}

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
            "Arma el pedido sugerido para la próxima entrega (en cajas, con motivo y total) y lo deja como "
            "borrador. Úsala cuando el tendero pide el pedido o pregunta qué debe pedir.",
            {"type": "object", "properties": {}},
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
