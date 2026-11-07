"""Catálogo con varios proveedores: emparejar productos, proveedor preferido, comparar y escoger."""

from contextlib import asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app import catalog_merge
from app.ai.tools import StoreActions, build_tools
from app.bot import handlers, texts
from app.catalog_merge import Offer, group_offers, name_words, size_key


def product(id_, name, presentation, price, units=12, available=None, aliases=""):
    return SimpleNamespace(id=id_, sku=f"S{id_}", name=name, presentation=presentation, unit_price=Decimal(price),
                           units_per_case=units, available_units=available, aliases=aliases, distributor_id=None)


BARRIO = [product(1, "Coca-Cola 1 litro", "1 L", 4500, aliases="coca litro"),
          product(2, "Coca-Cola 400 ml", "400 ml", 2500), product(3, "Leche Alquería 1 litro", "1 L", 4200),
          product(4, "Arroz Diana 500 g", "500 g", 2800), product(5, "Huevos AAA x 30", "x 30", 18000, units=1),
          product(6, "Pony Malta 330 ml", "330 ml", 2500), product(7, "Coca-Cola Zero 1 litro", "1 L", 4600),
          product(8, "Atún Van Camps 170 g", "170 g", 6900)]
POSTOBON = [product(101, "Gaseosa Coca Cola 1L", "1000 ml", 4300), product(102, "COCA COLA PERSONAL 400ML", "400 ml", 2600),
            product(103, "Leche Alqueria deslactosada 1 L", "1 L", 4600),
            product(104, "Arroz Diana x 500 gr", "500 gr", 2700, available=5),
            product(105, "Huevo AAA cubeta x30", "x30", 17500, units=1), product(106, "Pony Malta 1.5 L", "1.5 L", 5500),
            product(107, "Atun Van Camps en lomitos 170g", "170g", 6500)]


def items(chosen=None):
    offers = [Offer(p, 1, "Distribuidora El Barrio") for p in BARRIO] + [Offer(p, 2, "Postobón") for p in POSTOBON]
    return group_offers(offers, chosen or set())


def groups():
    return {frozenset(o.product.id for o in item.offers) for item in items()}


@pytest.mark.parametrize(
    "text, expected",
    [("1 L", "1000ml"), ("1 litro", "1000ml"), ("1000 ml", "1000ml"), ("1.5 L", "1500ml"), ("1,5 lt", "1500ml"),
     ("500 gr", "500g"), ("1 kg", "1000g"), ("x 30", "x30"), ("x30", "x30"), ("unidad", None)],
)
def test_size_key(text, expected):
    assert size_key(text) == expected


def test_name_words_ignore_size_packaging_and_plurals():
    assert name_words("Gaseosa Coca Cola 1L") == ["coca", "cola"]
    assert name_words("Arroz Diana x 500 gr") == ["arroz", "diana"]
    assert name_words("Huevos AAA cubeta x30") == ["huevo", "aaa"]


def test_same_products_are_joined():
    found = groups()
    assert frozenset({1, 101}) in found  # Coca-Cola 1 litro = Gaseosa Coca Cola 1L
    assert frozenset({2, 102}) in found  # 400 ml con "personal"
    assert frozenset({4, 104}) in found  # Arroz Diana 500 g = x 500 gr
    assert frozenset({5, 105}) in found  # Huevos x 30 = Huevo cubeta x30


def test_different_products_stay_apart():
    found = groups()
    assert frozenset({6}) in found and frozenset({106}) in found  # Pony 330 ml no es Pony 1.5 L
    assert frozenset({3}) in found and frozenset({103}) in found  # deslactosada es otro producto
    assert frozenset({7}) in found  # Coca-Cola Zero no es la normal


def test_doubtful_when_names_differ():
    by_ids = {frozenset(o.product.id for o in item.offers): item for item in items()}
    assert by_ids[frozenset({4, 104})].doubtful is False
    assert by_ids[frozenset({2, 102})].doubtful is False  # "personal" es solo la presentación
    assert by_ids[frozenset({8, 107})].doubtful is True  # "en lomitos" de más: hay que revisarlo


def test_preferred_is_cheapest_in_stock_unless_chosen():
    by_ids = {frozenset(o.product.id for o in item.offers): item for item in items()}
    assert by_ids[frozenset({1, 101})].preferred.distributor_name == "Postobón"  # $4.300 < $4.500
    # El arroz de Postobón es más barato pero está agotado (5 unidades no alcanzan una caja de 12).
    assert by_ids[frozenset({4, 104})].preferred.distributor_name == "Distribuidora El Barrio"
    chosen = {frozenset(o.product.id for o in item.offers): item for item in items(chosen={1})}
    assert chosen[frozenset({1, 101})].preferred.product.id == 1


def test_find_by_text_and_provider():
    all_items = items()
    item = catalog_merge.find_by_text(all_items, "coca litro")
    assert {o.product.id for o in item.offers} == {1, 101}
    assert catalog_merge.find_provider(item, "postobon").distributor_name == "Postobón"
    assert catalog_merge.find_provider(item, "el barrio").distributor_name == "Distribuidora El Barrio"
    assert catalog_merge.find_provider(item, "alpina") is None
    assert catalog_merge.find_by_text(all_items, "tornillos") is None


def test_comparison_and_savings():
    item = catalog_merge.find_by_text(items(), "coca litro")
    result = catalog_merge.comparison(item)
    assert result["le_pides_a"] == "Postobón"
    assert [(o["proveedor"], o["precio_unidad"], o["mas_barato"]) for o in result["opciones"]] == [
        ("Postobón", 4300, True), ("Distribuidora El Barrio", 4500, False)]
    rows = catalog_merge.savings(items(chosen={1}))
    assert rows[0] == {"producto": "Coca-Cola 1 litro", "le_pides_a": "Distribuidora El Barrio", "precio": 4500,
                       "mas_barato": "Postobón", "precio_mas_barato": 4300, "ahorro_por_unidad": 200}
    assert all(row["producto"] != "Pony Malta 330 ml" for row in rows)  # solo lo vende uno


def test_catalog_text_marks_preferred_and_splits_long_lists():
    [text] = texts.store_catalog(items())
    assert "⭐ Postobón $4.300 (más barato)" in text
    assert "Distribuidora El Barrio $2.700" not in text  # el arroz agotado no se marca como más barato
    assert "Postobón $2.700 (agotado)" in text
    assert "revisa: ¿es el mismo producto?" in text
    many = [catalog_merge.Item(offers=[Offer(product(i, f"Producto {i} " + "x" * 80, "1 L", 1000), 1, "A")])
            for i in range(200)]
    parts = texts.store_catalog(many)
    assert len(parts) > 1 and all(len(part) <= texts.TELEGRAM_SAFE_LENGTH for part in parts)
    assert texts.store_catalog([]) == ["Tus proveedores aún no tienen productos."]


# --- Herramientas del agente --------------------------------------------------------------


def base_actions(**extra):
    async def nothing(*args):
        return {}

    return StoreActions(catalog=[], stock=nothing, alerts=nothing, draft_sales=nothing, suggest_order=nothing,
                        change_order=nothing, **extra)


def test_supplier_tools_only_when_given():
    assert "comparar_proveedores" not in {t.name for t in build_tools(base_actions())}

    async def compare(text):
        return {"pidió": text}

    async def prefer(product_text, provider_text):
        return {"producto": product_text, "proveedor": provider_text}

    names = {t.name for t in build_tools(base_actions(compare=compare, prefer=prefer))}
    assert {"comparar_proveedores", "preferir_proveedor"} <= names


@pytest.fixture
def store_with_two_providers(monkeypatch):
    current = items()
    chosen: list[int] = []

    @asynccontextmanager
    async def fake_scope():
        class Session:
            async def get(self, model, pk):
                return SimpleNamespace(id=pk)

        yield Session()

    async def load(session, store):
        return current

    async def choose(session, store, all_items, product_id):
        chosen.append(product_id)
        item = catalog_merge.find_item(all_items, product_id)
        item.chosen = next(o for o in item.offers if o.product.id == product_id)

    monkeypatch.setattr(handlers, "session_scope", fake_scope)
    monkeypatch.setattr(handlers, "load_items", load)
    monkeypatch.setattr(handlers, "choose", choose)
    return chosen


@pytest.mark.asyncio
async def test_agent_compares_one_product_or_all(store_with_two_providers):
    actions = handlers.store_actions(41, [], "text", {})
    one = await actions.compare("coca litro")
    assert one["le_pides_a"] == "Postobón" and len(one["opciones"]) == 2
    everything = await actions.compare("")
    assert everything["comparacion"][0]["producto"]
    assert (await actions.compare("tornillos")) == {"no_encontrado": "tornillos"}


@pytest.mark.asyncio
async def test_agent_changes_provider_only_when_possible(store_with_two_providers):
    actions = handlers.store_actions(41, [], "text", {})
    done = await actions.prefer("coca litro", "el barrio")
    assert done == {"producto": "Coca-Cola 1 litro", "ahora_le_pides_a": "Distribuidora El Barrio",
                    "precio_unidad": 4500}
    assert store_with_two_providers == [1]
    only_one = await actions.prefer("Pony Malta 330 ml", "postobon")
    assert "solo lo vende" in only_one["error"]
    unknown = await actions.prefer("coca litro", "alpina")
    assert unknown["proveedores"] and store_with_two_providers == [1]


def test_same_alias_in_two_providers_is_not_a_tie():
    # Las dos Coca-Colas tienen el alias "coca litro": son el mismo artículo, no un empate.
    barrio = product(1, "Coca-Cola 1 litro", "1 L", 4500, aliases="coca litro")
    postobon = product(101, "Gaseosa Coca Cola 1L", "1000 ml", 4300, aliases="coca litro")
    found = group_offers([Offer(barrio, 1, "El Barrio"), Offer(postobon, 2, "Postobón")])
    item = catalog_merge.find_by_text(found, "coca litro")
    assert item is not None and len(item.offers) == 2


def test_two_different_items_alike_are_a_tie():
    a = product(1, "Pony Malta 330 ml", "330 ml", 2500)
    b = product(2, "Pony Malta 1.5 L", "1.5 L", 5500)
    found = group_offers([Offer(a, 1, "El Barrio"), Offer(b, 1, "El Barrio")])
    assert catalog_merge.find_by_text(found, "pony malta") is None
