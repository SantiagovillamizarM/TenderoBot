"""Bitácora de la carga: clasificación de fallos, huecos, resumen por hora y totales."""

from datetime import UTC, datetime, timedelta

import pytest

from app.ai.usage import TICKS_PER_USD
from app.load.logbook import classify, gaps, hourly, sales_detail, summarize

T0 = datetime(2026, 11, 7, 1, 0, tzinfo=UTC)


def row(status="exito", minute=0, **extra):
    base = {
        "unit_id": f"U{minute:05d}", "kind": "venta", "origin": "generado", "text": "vendí 2 pony",
        "status": status, "attempts": 1, "started_at": T0 + timedelta(minutes=minute),
        "finished_at": T0 + timedelta(minutes=minute, seconds=3), "latency_ms": 3000, "tools": ["registrar_ventas"],
        "observed": {"ventas": [["MAL", 2]]}, "expected": {"ventas": [{"sku": "MAL", "cantidad": 2}]},
        "reply": "Revisa el resumen", "failures": None, "confirmed": status == "exito", "error_type": None,
        "llm_calls": 2, "prompt_tokens": 3000, "cached_tokens": 2400, "completion_tokens": 50,
        "cost_ticks": 12_000_000,
    }
    return {**base, **extra}


@pytest.mark.parametrize(
    "extra, expected",
    [
        ({"status": "exito"}, None),
        ({"status": "pendiente"}, None),
        ({"status": "error", "error_type": "pasarela_503"}, "error_pasarela"),
        ({"status": "error", "error_type": "red"}, "error_red"),
        ({"status": "error", "error_type": "tiempo_unidad"}, "error_tiempo"),
        ({"status": "error", "error_type": "codigo_KeyError"}, "error_codigo"),
        ({"status": "fallo", "failures": ["ventas: esperaba {...}"]}, "ventas_incorrectas"),
        ({"status": "fallo", "failures": ["cambios: esperaba [...]"]}, "cambios_incorrectos"),
        ({"status": "fallo", "failures": ["no llamó ver_alertas (llamó: ninguna)"]}, "herramienta_no_usada"),
        ({"status": "fallo", "failures": ["llamó registrar_ventas y no debía"]}, "herramienta_prohibida"),
        ({"status": "fallo", "failures": ["la respuesta no dice '287'"]}, "respuesta"),
        ({"status": "fallo", "failures": ["se acabaron las vueltas sin respuesta final"]}, "sin_terminar"),
    ],
)
def test_classify(extra, expected):
    assert classify(row(**extra)) == expected


def test_hallucinated_summary_is_detected():
    # Dijo "pulsa ✅ Confirmar" sin haber llamado registrar_ventas ni dejar borrador.
    hallucination = row("fallo", tools=["buscar_producto"], observed={"ventas": []},
                        reply="Revisa el resumen y pulsa ✅ Confirmar", failures=["no llamó registrar_ventas", "ventas: x"])
    assert classify(hallucination) == "alucinacion_resumen"
    asked = row("fallo", tools=[], observed={"ventas": []}, reply="¿Cuántas vendiste?", failures=["ventas: x"])
    assert classify(asked) == "ventas_incorrectas"


def test_sales_detail():
    expected = {"ventas": [{"sku": "A", "cantidad": 2}, {"sku": "B", "cantidad": 1}]}
    assert sales_detail(row(expected=expected, observed={"ventas": [["A", 3], ["C", 1]]})) == "A 2→3, faltó B, sobró C"
    assert sales_detail(row(expected=expected, observed={"ventas": [["A", 1], ["A", 1], ["B", 1]]})) is None


def test_gaps_report_long_pauses_only():
    rows = [row(minute=m) for m in (0, 1, 2, 30, 31)] + [row("pendiente", minute=90)]
    found = gaps(rows, interval=30)
    assert len(found) == 1 and found[0]["minutos"] == 28.0
    assert gaps(rows, interval=600) == []  # con 10 min de intervalo, 28 min no supera 4 intervalos


def test_hourly_buckets_from_run_start_including_empty_hours():
    rows = [row(minute=5), row("error", minute=10, error_type="red"), row(minute=125)]
    hours = hourly(rows, T0)
    assert [h["unidades"] for h in hours] == [2, 0, 1]
    assert hours[0]["error"] == 1 and hours[0]["tasa_exito"] == 0.5
    assert hours[0]["desde_colombia"] == "2026-11-06 20:00"  # 01:00 UTC = 8 p. m. en Colombia


def test_summarize_totals_cost_and_continuity():
    rows = [row(minute=m) for m in range(0, 60, 2)]
    rows += [row("fallo", minute=61, failures=["ventas: x"], observed={"ventas": [["MAL", 3]]}),
             row("error", minute=62, error_type="pasarela_429", attempts=3, cost_ticks=0, llm_calls=0),
             row("pendiente", minute=0, finished_at=None, started_at=None)]
    beats = [{"at": T0 + timedelta(minutes=m), "note": None} for m in (0, 5, 10, 40)] + \
            [{"at": T0 + timedelta(minutes=45), "note": "freno: 5 errores"}]
    summary = summarize(rows, {"name": "x", "status": "terminada", "config": {"intervalo_s": 30}}, beats)
    total = summary["total"]
    assert (total["unidades"], total["exito"], total["fallo"], total["error"]) == (32, 30, 1, 1)
    assert summary["pendientes"] == 1
    assert total["reintentos"] == 2
    assert total["costo_usd"] == pytest.approx(31 * 12_000_000 / TICKS_PER_USD)
    assert total["costo_por_exito_usd"] == pytest.approx(31 * 12_000_000 / TICKS_PER_USD / 30)
    assert total["porcentaje_cache"] == pytest.approx(0.8)
    assert summary["categorias"] == {"ventas_incorrectas": 1, "error_pasarela": 1}
    assert summary["ejemplos"]["ventas_incorrectas"][0]["detalle"] == "MAL 2→3"
    assert summary["errores_por_tipo"] == {"pasarela_429": 1}
    assert summary["mayor_hueco_senales_min"] == 30.0
    assert summary["freno_activado"] == 1
    assert summary["ventas_confirmadas"] == 30
    assert summary["horas"] == pytest.approx(round((62 * 60 + 3) / 3600, 2))
    assert summary["por_tipo"]["venta"]["unidades"] == 32
