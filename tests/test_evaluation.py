"""Calificador de la evaluación propia (sin llamar a Grok)."""

import json
from pathlib import Path

import pytest

from app.evaluation import Observed, evaluate, fixed_store_actions, run_case
from app.schemas import OrderChange, SaleLine
from scripts.run_eval import load_cases, result_row, summarize

ROOT = Path(__file__).resolve().parent.parent
STORE = json.loads((ROOT / "eval" / "tienda.json").read_text(encoding="utf-8"))


def test_sales_must_match_exactly_summing_repeated_skus():
    expected = {"herramientas": ["registrar_ventas"], "ventas": [{"sku": "A", "cantidad": 3}]}
    assert evaluate(expected, Observed(tools=["registrar_ventas"], sales=[("A", 1), ("A", 2)])) == []
    failures = evaluate(expected, Observed(tools=["registrar_ventas"], sales=[("A", 2)]))
    assert failures and failures[0].startswith("ventas")


def test_empty_sales_pass_when_nothing_was_drafted():
    assert evaluate({"ventas": []}, Observed(tools=["registrar_ventas"])) == []
    assert evaluate({"ventas": []}, Observed(sales=[("HUE", 6)]))


def test_tool_checks():
    observed = Observed(tools=["consultar_stock"])
    assert evaluate({"herramientas": ["consultar_stock"]}, observed) == []
    assert evaluate({"herramientas": ["ver_alertas"]}, observed)
    assert evaluate({"herramientas_alguna": ["ver_alertas", "consultar_stock"]}, observed) == []
    assert evaluate({"herramientas_alguna": ["ver_alertas"]}, observed)
    assert evaluate({"no_debe_llamar": ["consultar_stock"]}, observed)


def test_changes_compare_unit_only_when_given():
    observed = Observed(changes=[("HUE", "add", 1, "unit")])
    assert evaluate({"cambios": [{"sku": "HUE", "accion": "add", "cantidad": 1}]}, observed) == []
    assert evaluate({"cambios": [{"sku": "HUE", "accion": "add", "cantidad": 1, "unidad": "case"}]}, observed)


def test_count_uses_tolerance():
    observed = Observed(count=[("A", 9)])
    assert evaluate({"conteo": [{"sku": "A", "cantidad": 10, "tolerancia": 1}]}, observed) == []
    assert evaluate({"conteo": [{"sku": "A", "cantidad": 12, "tolerancia": 1}]}, observed)
    assert evaluate({"conteo": [{"sku": "B", "cantidad": 0}]}, observed)


def test_reply_checks_ignore_case_and_accents():
    observed = Observed(reply="El Atún no se está vendiendo. ¿Lo pido?")
    assert evaluate({"respuesta_contiene": ["atun", "?"]}, observed) == []
    assert evaluate({"respuesta_contiene": ["12.000|12000"]}, observed)
    assert evaluate({"respuesta_no_contiene": ["ATÚN"]}, observed)


def test_unfinished_agent_fails():
    assert evaluate({}, Observed(finished=False))


@pytest.mark.asyncio
async def test_fixed_store_keeps_last_draft_and_respects_open_order():
    observed = Observed()
    actions = fixed_store_actions(STORE, observed, order_open=False)
    await actions.draft_sales([SaleLine(sku="A", product_text="a", quantity=1)])
    await actions.draft_sales([SaleLine(sku="B", product_text="b", quantity=2)])
    assert observed.sales == [("B", 2)]
    assert "error" in await actions.change_order([OrderChange(sku="A", action="add", quantity=1)])
    assert observed.changes == []

    opened = fixed_store_actions(STORE, observed, order_open=True)
    await opened.change_order([OrderChange(sku="A", action="add", quantity=1)])
    assert observed.changes == [("A", "add", 1, "case")]


@pytest.mark.asyncio
async def test_case_without_media_file_is_skipped(tmp_path):
    case = {"id": "F99", "categoria": "foto", "entrada": {"foto": "media/no_existe.jpg"}, "esperado": {}}
    result = await run_case(case, STORE, tmp_path)
    assert result.status == "omitido"
    assert result.usage.calls == []


def test_case_file_is_valid():
    cases = load_cases(ROOT / "eval" / "casos.jsonl", set(), None)
    assert len(cases) >= 30
    skus = {product["sku"] for product in STORE["catalogo"]}
    for case in cases:
        assert set(case) >= {"id", "categoria", "origen", "entrada", "esperado"}
        assert case["origen"] in {"redactado", "real"}
        for row in case["esperado"].get("ventas", []) + case["esperado"].get("cambios", []):
            assert row["sku"] in skus, (case["id"], row["sku"])


def test_summary_counts_cost_per_resolved_task():
    def row(case_id, status, cost):
        return {"id": case_id, "categoria": "ventas", "origen": "redactado", "estado": status, "costo_usd": cost,
                "costo_completo": True, "segundos": 2.0, "tokens_entrada": 100, "tokens_cache": 50,
                "tokens_salida": 10, "llamadas": 2}

    rows = [row("V1", "paso", 0.002), row("V2", "fallo", 0.002), row("A1", "omitido", 0.0)]
    rows[2]["costo_completo"] = False
    summary = summarize(rows, "grok", trm=4000, messages_per_day=20)
    assert (summary["ejecutados"], summary["omitidos"], summary["pasaron"]) == (2, 1, 1)
    assert summary["costo_por_tarea_usd"] == pytest.approx(0.002)
    # Lo que se gastó en el caso que falló también se carga a la tarea resuelta.
    assert summary["costo_por_tarea_resuelta_usd"] == pytest.approx(0.004)
    assert summary["costo_mensual_tienda_usd"] == pytest.approx(0.002 * 20 * 30)
    assert summary["costo_incompleto"] == []


def test_result_row_is_json_serializable():
    from app.ai.usage import TaskUsage
    from app.evaluation import CaseResult

    case = {"id": "V1", "categoria": "ventas", "entrada": {"texto": "x"}, "esperado": {}}
    result = CaseResult(case, "paso", [], Observed(sales=[("A", 1)]), TaskUsage(task="V1"), 1.234)
    row = result_row(result)
    assert json.loads(json.dumps(row))["segundos"] == 1.23
