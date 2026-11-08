"""Errores encontrados probando el bot como tendero (recepción, pedidos con varios proveedores, huevos...)."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.ai.tools import StoreActions, build_tools, provider_in_message, provider_matches
from app.bot import handlers, texts

COCA = {"sku": "GAS-COCA-1L", "name": "Coca-Cola 1 litro", "aliases": "coca litro", "presentation": "Botella 1 L",
        "unit_price": 4500, "units_per_case": 12, "sold_out": False, "supplier": "Distribuidora El Barrio"}
COCA_400 = {"sku": "GAS-COCA-400", "name": "Coca-Cola 400 ml", "aliases": "coca personal",
            "presentation": "Botella 400 ml", "unit_price": 2500, "units_per_case": 12, "sold_out": False,
            "supplier": "Distribuidora El Barrio"}
PONY = {"sku": "GAS-PONY-330", "name": "Pony Malta 330 ml", "aliases": "pony", "presentation": "Botella 330 ml",
        "unit_price": 2500, "units_per_case": 24, "sold_out": False, "supplier": "Distribuidora El Barrio"}
ARROZ_DIANA = {"sku": "ARR-DIANA", "name": "Arroz Diana x 500 gr", "aliases": "arroz diana", "presentation": "500 g",
               "unit_price": 2800, "units_per_case": 20, "sold_out": False, "supplier": "Mayorista La Economía"}
ARROZ_ROA = {"sku": "ARR-ROA-1K", "name": "Arroz Roa 1 kg", "aliases": "arroz roa", "presentation": "1 kg",
             "unit_price": 4800, "units_per_case": 12, "sold_out": False, "supplier": "Distribuidora El Barrio"}
AGUA = {"sku": "POS-AGUA", "name": "Agua Cristal x 600 ml", "aliases": "agua cristal", "presentation": "600 ml",
        "unit_price": 1700, "units_per_case": 24, "sold_out": False, "supplier": "Postobón Santander"}
HUEVOS = {"sku": "HUE-AAA-30", "name": "Huevos AAA x 30", "aliases": "huevos,huevo,cubeta", "presentation": "x 30",
          "unit_price": 18000, "units_per_case": 1, "sold_out": False, "supplier": "Distribuidora El Barrio"}
PAPEL = {"sku": "ASE-PAPEL-4", "name": "Papel higiénico x 4", "aliases": "papel", "presentation": "x 4",
         "unit_price": 7500, "units_per_case": 10, "sold_out": False, "supplier": "Distribuidora El Barrio"}

CATALOG = [COCA, COCA_400, PONY, ARROZ_DIANA, ARROZ_ROA, AGUA, HUEVOS, PAPEL]


def make_tools(user_message: str | None = None, catalog=CATALOG, **overrides):
    actions = dict(
        catalog=catalog,
        stock=AsyncMock(return_value=[
            {"sku": "ARR-DIANA", "producto": "Arroz Diana x 500 gr", "unidades": 20},
            {"sku": "ARR-ROA-1K", "producto": "Arroz Roa 1 kg", "unidades": 18},
            {"sku": "GAS-COCA-1L", "producto": "Coca-Cola 1 litro", "unidades": 12},
        ]),
        alerts=AsyncMock(return_value={}),
        draft_sales=AsyncMock(return_value={"borrador": "creado"}),
        suggest_order=AsyncMock(return_value={"borrador": "creado"}),
        change_order=AsyncMock(return_value={"borrador": "actualizado"}),
    )
    actions.update(overrides)
    store_actions = StoreActions(**actions)
    return {tool.name: tool for tool in build_tools(store_actions, user_message=user_message)}, store_actions


# --- Correcciones de conteo y de lo recibido -----------------------------------------


@pytest.mark.parametrize("text, expected", [
    ("coca litro 8", ("coca litro", 8)),
    ("pony malta 330 ml 5", ("pony malta 330 ml", 5)),
    ("huevos x 30 2", ("huevos x 30", 2)),
    ("pony 0", ("pony", 0)),
])
def test_single_product_corrections_are_read(text, expected):
    assert handlers.parse_stock_correction(text) == expected


@pytest.mark.parametrize("text", [
    "vendí 2 cocas de litro y una de 400",  # una venta, no "de 400 llegaron 400"
    "vendi coca litro 2",
    "2 cocas de litro 400",  # otra cantidad dentro del nombre
    "coca litro y pan 3",  # varios productos
    "ponle coca litro 2",
    "cuántas coca litro 3",
    "quita el arroz 1",
])
def test_sales_and_order_messages_are_not_corrections(text):
    assert handlers.parse_stock_correction(text) is None


def test_receipt_warns_before_delivery_day():
    lines = [{"name": "Coca-Cola 1 litro", "units": 12, "ordered": 12}]
    early = texts.receipt_summary(lines, date(2026, 11, 9), "El Barrio", today=date(2026, 11, 7))
    on_time = texts.receipt_summary(lines, date(2026, 11, 9), "El Barrio", today=date(2026, 11, 9))
    assert "Confírmalo solo cuando ya te haya llegado" in early
    assert "Confírmalo solo cuando" not in on_time


def test_missing_line_says_how_many_arrived():
    assert texts.receipt_missing_line("Coca-Cola 1 litro", 100, 108) == "Coca-Cola 1 litro (llegaron 100 de 108)"


@pytest.mark.asyncio
async def test_receipt_rejects_more_than_ordered(monkeypatch):
    replies = []

    async def record(update, text, reply_markup=None):
        replies.append(text)

    replaced = AsyncMock()
    monkeypatch.setattr(handlers, "_reply", record)
    monkeypatch.setattr(handlers, "replace_pending", replaced)
    product = SimpleNamespace(id=10, sku="GAS-COCA-1L", name="Coca-Cola 1 litro", aliases="coca litro",
                              presentation="1 L", units_per_case=12)

    class Scalars:
        def all(self):
            return [product]

    session = SimpleNamespace(scalars=AsyncMock(return_value=Scalars()))
    draft = SimpleNamespace(payload={"delivery_date": "2026-11-09", "supplier": "El Barrio",
                                     "lines": [{"product_id": 10, "name": "Coca-Cola 1 litro", "units": 108,
                                                "ordered": 108}]})
    await handlers.apply_receipt_correction(None, session, SimpleNamespace(id=7), draft, ("coca litro", 400))
    assert replies == [texts.receipt_too_many("Coca-Cola 1 litro", 108)]
    replaced.assert_not_called()


# --- Pedidos con varios proveedores ----------------------------------------------------


def test_provider_names_are_recognized():
    assert provider_matches("Postobón", "Postobón Santander")
    assert provider_matches("la economia", "Mayorista La Economía")
    assert provider_matches("El Barrio", "Distribuidora El Barrio")
    assert not provider_matches("Postobón", "Distribuidora El Barrio")
    suppliers = {"Postobón Santander", "Distribuidora El Barrio", "Mayorista La Economía"}
    assert provider_in_message("ponle 2 cajas de coca litro al pedido de Postobón", suppliers) == "Postobón Santander"
    # La marca de un producto no es un proveedor nombrado.
    assert provider_in_message("ponle 2 cajas de manzana postobon", suppliers) is None


@pytest.mark.asyncio
async def test_change_for_a_provider_that_does_not_sell_it_is_not_applied():
    tools, actions = make_tools("ponle 2 cajas de coca litro al pedido de Postobón")
    result = await tools["ajustar_pedido"].run(
        {"cambios": [{"producto": "coca litro", "accion": "add", "cantidad": 2, "unidad": "case"}]}
    )
    actions.change_order.assert_not_called()
    assert result["no_aplicados"] == [{"producto": "Coca-Cola 1 litro", "proveedor_que_dijo": "Postobón Santander",
                                       "se_lo_pides_a": "Distribuidora El Barrio"}]


@pytest.mark.asyncio
async def test_change_with_named_provider_from_the_model_is_checked():
    tools, actions = make_tools("pídele a Postobón 2 cajas de Pony Malta", catalog=[COCA, PONY, HUEVOS])
    result = await tools["ajustar_pedido"].run(
        {"cambios": [{"producto": "pony malta", "accion": "add", "cantidad": 2, "unidad": "case",
                      "proveedor": "Postobón"}]}
    )
    actions.change_order.assert_not_called()
    assert result["no_aplicados"][0]["producto"] == "Pony Malta 330 ml"


@pytest.mark.asyncio
async def test_named_provider_resolves_an_ambiguous_product():
    tools, actions = make_tools("quita el arroz del pedido de La Economía")
    await tools["ajustar_pedido"].run(
        {"cambios": [{"producto": "arroz", "accion": "remove", "cantidad": 0, "unidad": "case"}]}
    )
    (changes,), _ = actions.change_order.call_args
    assert [change.sku for change in changes] == ["ARR-DIANA"]


@pytest.mark.asyncio
async def test_change_without_provider_still_works():
    tools, actions = make_tools("ponle 2 cajas más de coca litro")
    await tools["ajustar_pedido"].run(
        {"cambios": [{"producto": "coca litro", "accion": "add", "cantidad": 2, "unidad": "case"}]}
    )
    (changes,), _ = actions.change_order.call_args
    assert [change.sku for change in changes] == ["GAS-COCA-1L"]


# --- Consultas y ventas ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stock_of_a_word_shared_by_several_products_returns_all():
    tools, _ = make_tools("cuánto me queda de arroz?")
    result = await tools["consultar_stock"].run({"producto": "arroz"})
    assert {row["sku"] for row in result["stock"]} == {"ARR-DIANA", "ARR-ROA-1K"}


def test_plural_with_several_sizes_is_not_unknown():
    from app.ai.tools import _not_found

    # "cocas" con dos tamaños: se pregunta cuál, no "no lo reconocí".
    assert _not_found("cocas", CATALOG)["varias_opciones"] == ["Coca-Cola 1 litro", "Coca-Cola 400 ml"]
    assert "parecidos" in _not_found("helados", CATALOG)


@pytest.mark.asyncio
async def test_eggs_without_saying_cubeta_are_asked():
    tools, actions = make_tools("vendí 10 huevos")
    result = await tools["registrar_ventas"].run(
        {"ventas": [{"producto": "huevos", "cantidad": 10, "texto_cantidad": "10"}]}
    )
    assert result["reconocidas"] == []
    assert result["preguntar"][0]["producto"] == "Huevos AAA x 30"


@pytest.mark.asyncio
async def test_eggs_by_cubeta_and_small_packs_are_registered():
    tools, _ = make_tools("vendí 2 cubetas de huevos y 3 papel higiénico")
    result = await tools["registrar_ventas"].run({"ventas": [
        {"producto": "huevos", "cantidad": 2, "texto_cantidad": "2"},
        {"producto": "papel higiénico", "cantidad": 3, "texto_cantidad": "3"},
    ]})
    assert [line["producto"] for line in result["reconocidas"]] == ["Huevos AAA x 30", "Papel higiénico x 4"]


# --- El tendero arma su propio pedido ------------------------------------------------------


def make_tools_building(user_message: str, **overrides):
    actions = StoreActions(
        catalog=CATALOG,
        stock=AsyncMock(return_value=[]),
        alerts=AsyncMock(return_value={}),
        draft_sales=AsyncMock(return_value={"borrador": "creado"}),
        suggest_order=AsyncMock(return_value={"borrador": "creado"}),
        change_order=AsyncMock(return_value={"borrador": "actualizado"}),
        **overrides,
    )
    tools = build_tools(actions, user_message=user_message, building_order="Distribuidora El Barrio")
    return {tool.name: tool for tool in tools}, actions


@pytest.mark.asyncio
async def test_while_building_an_order_products_are_not_sales():
    tools, actions = make_tools_building("3 cajas de coca litro y 1 cubeta de huevos")
    result = await tools["registrar_ventas"].run(
        {"ventas": [{"producto": "coca litro", "cantidad": 3, "texto_cantidad": "3"}]}
    )
    assert result["borrador"] == "no_creado" and "ajustar_pedido" in result["error"]
    actions.draft_sales.assert_not_called()


@pytest.mark.asyncio
async def test_while_building_an_order_a_real_sale_is_still_registered():
    tools, actions = make_tools_building("vendí 2 coca litro")
    await tools["registrar_ventas"].run({"ventas": [{"producto": "coca litro", "cantidad": 2, "texto_cantidad": "2"}]})
    actions.draft_sales.assert_called_once()


@pytest.mark.asyncio
async def test_order_tool_passes_the_named_provider():
    ask, suggest_for = AsyncMock(return_value={"pregunta": "enviada"}), AsyncMock(return_value={"borrador": "creado"})
    tools, actions = make_tools_building("x", ask_order=ask, suggest_order_for=suggest_for)
    await tools["armar_pedido"].run({"proveedor": "Postobón"})
    ask.assert_called_once_with("Postobón")
    await tools["armar_pedido"].run({"sugerido": True, "proveedor": "La Economía"})
    suggest_for.assert_called_once_with("La Economía")
    actions.suggest_order.assert_not_called()


# --- Fallos del piloto de carga ------------------------------------------------------------


def test_exact_alias_wins_over_the_model_sku():
    from app.ai.matching import resolve_sku

    gourmet = {"sku": "ACE-GOUR-1L", "name": "Aceite Gourmet 1 litro", "aliases": "aceite,gourmet,aceite litro"}
    premier = {"sku": "ACE-PREM-3L", "name": "Aceite Premier 3 litros", "aliases": "aceite grande,premier"}
    assert resolve_sku("ACE-PREM-3L", "aceite litro", [gourmet, premier]) == "ACE-GOUR-1L"
    # Si el texto no es un nombre exacto, se sigue usando el sku del modelo.
    assert resolve_sku("ACE-PREM-3L", "el aceitico ese", [gourmet, premier]) == "ACE-PREM-3L"


@pytest.mark.asyncio
async def test_quantity_quote_must_be_a_number():
    tools, actions = make_tools("se fue leche litro", catalog=[COCA, PAPEL])
    result = await tools["registrar_ventas"].run(
        {"ventas": [{"producto": "coca litro", "cantidad": 1, "texto_cantidad": "litro"}]}
    )
    assert result["reconocidas"] == [] and result["preguntar"][0]["motivo"] == "No dijo cuántas vendió"
    from app.ai.tools import is_quantity

    for said in ("2", "una", "media docena", "tres", "veintidós", "dieciocho", "treinta y dos", "ciento veinte"):
        assert is_quantity(said)
    for said in ("litro", "leche", "grande"):
        assert not is_quantity(said)


def test_size_numbers_are_not_quantities():
    from app.ai.tools import quote_is_only_a_size

    assert quote_is_only_a_size("400", "me compraron Axion 400 g")
    assert quote_is_only_a_size("3", "hoy vendí 1 Jabón Rey x 3 y 12 Jet")
    assert not quote_is_only_a_size("1", "hoy vendí 1 Jabón Rey x 3 y 12 Jet")
    assert not quote_is_only_a_size("6", "6 Agua Brisa 600 ml")
    assert not quote_is_only_a_size("2", "2 pony malta 330 ml")
    assert not quote_is_only_a_size("dos", "dos aceites")


def test_other_suppliers_names_find_the_preferred_product():
    from types import SimpleNamespace as NS

    from app.ai.matching import resolve_sku
    from app.store_catalog import item_products

    barrio = NS(id=1, sku="GAS-POST-1.5", name="Postobón 1.5 litros", aliases="postobon", presentation="1.5 L")
    postobon = NS(id=2, sku="POS-POST-15", name="Gaseosa Postobón 1.5 L", aliases="", presentation="1.5 L")
    item = NS(preferred=NS(product=postobon), offers=[NS(product=barrio), NS(product=postobon)])
    (product,) = item_products([item])
    assert product is postobon
    catalog = handlers.products_as_catalog([product])
    assert resolve_sku(None, "Postobón 1.5 litros", catalog) == catalog[0]["sku"]


def test_logbook_counts_restarts_and_abrupt_cuts():
    from datetime import UTC, datetime, timedelta

    from app.load.logbook import restarts

    t = datetime(2026, 11, 8, tzinfo=UTC)
    beats = [
        {"at": t, "note": "inicio"},
        {"at": t + timedelta(minutes=5), "note": None},
        # se mató el proceso: no hubo "fin"
        {"at": t + timedelta(minutes=9), "note": "inicio"},
        {"at": t + timedelta(minutes=20), "note": "fin: interrumpida"},
        {"at": t + timedelta(minutes=30), "note": "inicio"},
        {"at": t + timedelta(minutes=40), "note": "fin: terminada"},
    ]
    assert restarts(beats) == (3, 1)
    assert restarts(beats[:2]) == (1, 0)


# --- Textos ------------------------------------------------------------------------------


def test_pending_requests_are_reported_as_in_review():
    one = texts.suppliers_in_review(["Distribuidora El Barrio"])
    assert "tu solicitud está en revisión con Distribuidora El Barrio" in one
    two = texts.suppliers_in_review(["Distribuidora El Barrio", "Postobón Santander"])
    assert "tus solicitudes están en revisión con Distribuidora El Barrio y Postobón Santander" in two


@pytest.mark.asyncio
async def test_no_suppliers_text_depends_on_pending_requests():
    class Rows:
        def __init__(self, names):
            self.names = names

        def all(self):
            return self.names

    store = SimpleNamespace(id=19)
    pending = SimpleNamespace(scalars=AsyncMock(return_value=Rows(["Distribuidora El Barrio"])))
    assert await handlers.no_suppliers_text(pending, store) == texts.suppliers_in_review(["Distribuidora El Barrio"])
    none = SimpleNamespace(scalars=AsyncMock(return_value=Rows([])))
    assert await handlers.no_suppliers_text(none, store) == texts.NO_SUPPLIERS


def test_start_on_a_linked_chat_says_how_to_leave():
    text = texts.start_linked("Tienda Doña Marta")
    assert "Tienda Doña Marta" in text and "/salir" in text


def test_days_question_names_the_provider():
    assert texts.ask_days([1, 3], "Postobón Santander").startswith("📅 Postobón Santander pasa")
    assert texts.ask_days([1, 3]).startswith("📅 Tu proveedor pasa")


def test_products_bought_one_by_one_are_not_called_cases():
    from datetime import UTC, datetime
    from decimal import Decimal

    from app.inventory.engine import OrderLine

    eggs = OrderLine(product_id=1, sku="HUE", name="Huevos AAA x 30", cases=6, units=6, subtotal=Decimal(0), reason="r")
    coca = OrderLine(product_id=2, sku="COC", name="Coca-Cola 1 litro", cases=2, units=24, subtotal=Decimal(0),
                     reason="r")
    text = texts.order_draft([eggs, coca], Decimal(0), date(2026, 11, 9), datetime(2026, 11, 8, 23, tzinfo=UTC))
    assert "• 6 Huevos AAA x 30 — r" in text
    assert "• 2 cajas Coca-Cola 1 litro — r" in text
