"""Reporte de costo por tipo de tarea (sin llamar a Grok)."""

import pytest

from scripts.cost_report import build_report, money, task_type, type_stats


def row(category, status, cost, calls=2):
    return {"categoria": category, "estado": status, "costo_usd": cost, "llamadas": calls, "tokens_entrada": 3000,
            "tokens_cache": 2500, "tokens_salida": 50, "segundos": 2.0}


def test_categories_map_to_task_types():
    assert task_type("ventas_dificil") == "ventas"
    assert task_type("pedido_voz") == "pedido"
    assert task_type("seguridad") == "conversacion"
    assert task_type("otra") is None


def test_cost_per_resolved_task_charges_failed_attempts():
    stats = type_stats([row("ventas_texto", "paso", 0.001), row("ventas_texto", "fallo", 0.003)])
    assert stats["acierto"] == 0.5
    assert stats["costo_promedio_usd"] == pytest.approx(0.002)
    assert stats["costo_por_tarea_resuelta_usd"] == pytest.approx(0.004)


def test_no_resolved_tasks_has_no_cost_per_resolved():
    assert type_stats([row("ventas_texto", "fallo", 0.001)])["costo_por_tarea_resuelta_usd"] is None


def test_projection_uses_mix_and_flags_unmeasured_types():
    by_type = {"ventas": type_stats([row("ventas_texto", "paso", 0.001)])}
    data, markdown = build_report(by_type, {"ventas": 10, "foto": 1}, trm=4000, runs=["r1"], photo_measured=False)
    assert data["costo_diario_tienda_usd"] == pytest.approx(0.01)
    assert data["costo_mensual_tienda_usd"] == pytest.approx(0.3)
    assert data["tipos_sin_medir"] == ["foto"]
    assert "Sin medir" in markdown


def test_money_uses_colombian_format():
    assert money(0.00133, 3182.99) == ("US$0,00133", "$4,23")
    assert money(738.76, 3182.99) == ("US$738,76", "$2.351.466")
    assert money(None, 4000) == ("—", "—")
