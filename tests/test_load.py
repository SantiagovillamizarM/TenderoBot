"""Semana de carga (S4): unidades de trabajo, generador y candado de la base separada."""

import importlib
from collections import Counter

import pytest

from app.ai.matching import resolve_sku
from app.load import database
from app.load.generator import Generator, _pluralize, mix_real_units
from app.load.units import WorkUnit, load_units, save_units
from scripts.load_generate import seed_catalog

CATALOG = seed_catalog()
SKUS = {product["sku"] for product in CATALOG}


def generate(total=600, seed=11, stores=3):
    return Generator(CATALOG, stores=stores, seed=seed).generate(total)


# --- Unidades ----------------------------------------------------------------------


def test_unit_validates_origin_kind_and_text():
    WorkUnit("U1", 0, "venta", "vendí 2 cocas")
    for bad in ({"origen": "inventado"}, {"tipo": "otro"}):
        with pytest.raises(ValueError):
            WorkUnit("U1", 0, **{"tipo": "venta", "texto": "x", **bad})
    with pytest.raises(ValueError):
        WorkUnit("U1", 0, "venta", "   ")


def test_units_roundtrip_and_reject_duplicated_ids(tmp_path):
    units = generate(20)
    path = tmp_path / "u.jsonl"
    save_units(units, path)
    assert load_units(path) == units
    save_units([units[0], units[0]], path)
    with pytest.raises(ValueError):
        load_units(path)


# --- Generador ---------------------------------------------------------------------


def test_same_seed_same_units():
    assert generate(seed=5) == generate(seed=5)
    assert generate(seed=5) != generate(seed=6)


def test_exact_total_and_reasonable_mix():
    units = generate(1500)
    assert len(units) == 1500
    share = {kind: count / len(units) for kind, count in Counter(u.tipo for u in units).items()}
    assert 0.5 < share["venta"] < 0.66
    assert 0.12 < share["consulta"] < 0.22
    assert 0.06 < share["pedido"] < 0.15
    assert 0.06 < share["conversacion"] < 0.14
    assert 0.02 < share["venta_dificil"] < 0.09


def test_expected_results_point_to_real_products():
    for unit in generate(1500):
        for row in unit.esperado.get("ventas", []) + unit.esperado.get("cambios", []):
            assert row["sku"] in SKUS
        if "stock_de" in unit.esperado:
            assert unit.esperado["stock_de"] in SKUS
            assert unit.esperado["herramientas"] == ["consultar_stock"]
        assert 0 <= unit.tienda < 3
        assert unit.origen == "generado"


@pytest.mark.parametrize("quantity", [1, 2, 5, 12])
def test_every_product_mention_resolves_and_says_the_quantity(quantity):
    generator = Generator(CATALOG, stores=1, seed=quantity)
    for product in generator.products:
        for _ in range(5):
            mention, said = generator._mention(product, quantity)
            assert said in mention
            if "cubeta" in mention:
                continue
            name = mention[len(said):].strip()
            assert resolve_sku(None, name, CATALOG) == product["sku"], mention


def test_only_correct_sales_are_confirmed_and_hard_ones_expect_no_sale():
    for unit in generate(800):
        if unit.tipo == "venta":
            assert unit.confirmar and unit.esperado["ventas"]
        else:
            assert not unit.confirmar
        if unit.tipo == "venta_dificil":
            assert unit.esperado == {"ventas": []}


def test_order_changes_follow_an_order_of_the_same_store():
    units = generate(1500)
    for index, unit in enumerate(units):
        if "ajustar_pedido" in unit.esperado.get("herramientas", []):
            previous = units[index - 1]
            assert previous.tienda == unit.tienda and previous.tipo == "pedido"


def test_pluralize():
    assert [_pluralize(w) for w in ("coca", "pan", "postobón", "jabón", "pony")] == [
        "cocas", "panes", "postobones", "jabones", "pony"]


def test_real_units_are_mixed_without_breaking_order_sequences():
    units = generate(300)
    real = [WorkUnit(f"R{i}", i % 3, "venta", f"vendí {i + 1} pony", origen="real") for i in range(40)]
    mixed = mix_real_units(units, real, seed=3)
    assert len(mixed) == 340
    assert [u for u in mixed if u.origen == "generado"] == units
    for index, unit in enumerate(mixed):
        if "ajustar_pedido" in unit.esperado.get("herramientas", []):
            assert mixed[index - 1].tipo == "pedido" and mixed[index - 1].origen == "generado"
    with pytest.raises(ValueError):
        mix_real_units(units, [WorkUnit("R9", 7, "venta", "vendí 1 pony", origen="real")])


# --- Base separada -----------------------------------------------------------------

NEON = "postgresql+asyncpg://user:secreto@ep-algo.neon.tech/neondb?ssl=require"


def test_same_database_is_refused():
    with pytest.raises(database.LoadDatabaseError):
        database.check_separate("postgresql+asyncpg://otro:otra@EP-ALGO.neon.tech/NEONDB", NEON)


@pytest.mark.parametrize(
    "load_url",
    [
        database.DEFAULT_LOAD_URL,
        "postgresql+asyncpg://user:secreto@ep-algo.neon.tech/carga",  # otra base en el mismo servidor
        "postgresql+asyncpg://user:secreto@ep-otro-branch.neon.tech/neondb",  # otro branch de Neon
    ],
)
def test_different_database_is_accepted(load_url):
    database.check_separate(load_url, NEON)


def test_load_url_comes_from_environment_then_env_file_then_default(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    monkeypatch.setattr(database, "ENV_FILE", env_file)
    monkeypatch.delenv("LOAD_DATABASE_URL", raising=False)
    assert database.load_database_url() == database.DEFAULT_LOAD_URL
    env_file.write_text("LOAD_DATABASE_URL=postgresql+asyncpg://a:b@h:1/desde_env\n", encoding="utf-8")
    assert database.load_database_url().endswith("/desde_env")
    monkeypatch.setenv("LOAD_DATABASE_URL", "postgresql+asyncpg://a:b@h:1/desde_entorno")
    assert database.load_database_url().endswith("/desde_entorno")


def test_switch_is_refused_after_app_db_was_imported(monkeypatch, tmp_path):
    importlib.import_module("app.db")  # en las pruebas ya está importado

    monkeypatch.setattr(database, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setenv("LOAD_DATABASE_URL", database.DEFAULT_LOAD_URL)
    monkeypatch.setenv("DATABASE_URL", NEON)
    with pytest.raises(database.LoadDatabaseError, match="antes de importar"):
        database.use_load_database()


def test_switch_is_refused_when_env_points_to_same_database(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setenv("LOAD_DATABASE_URL", NEON)
    monkeypatch.setenv("DATABASE_URL", NEON)
    with pytest.raises(database.LoadDatabaseError, match="misma base"):
        database.use_load_database()
