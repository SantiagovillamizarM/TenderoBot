import json
from unittest.mock import AsyncMock, patch

import pytest

from app.ai.agent import Tool, ToolError, run_agent
from app.ai.matching import search_products
from app.ai.tools import StoreActions, build_tools

CATALOG = [
    {
        "sku": "GAS-COCA-1L",
        "name": "Coca-Cola 1 litro",
        "aliases": "coca litro,coca cola litro,coca 1l",
        "presentation": "Botella 1 L",
        "unit_price": 4500,
        "units_per_case": 12,
        "sold_out": False,
    },
    {
        "sku": "PAN-TAJA-UND",
        "name": "Pan tajado",
        "aliases": "pan,pan tajado,tajado",
        "presentation": "Bolsa 500 g",
        "unit_price": 6200,
        "units_per_case": 10,
        "sold_out": True,
    },
]


def tool_call(name: str, arguments: dict | str, call_id: str = "c1") -> dict:
    args = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": args}}


def assistant(content: str = "", calls: list[dict] | None = None) -> tuple[dict, dict]:
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = calls
    return message, {"prompt_tokens": 100, "completion_tokens": 20}


def patch_chat(*responses):
    return patch("app.ai.agent.chat_with_tools", new=AsyncMock(side_effect=list(responses)))


def make_actions(**overrides) -> StoreActions:
    defaults = dict(
        catalog=CATALOG,
        stock=AsyncMock(return_value=[{"sku": "GAS-COCA-1L", "producto": "Coca-Cola 1 litro", "unidades": 7}]),
        alerts=AsyncMock(return_value={"en_riesgo": [], "estancados": []}),
        draft_sales=AsyncMock(return_value={"borrador": "creado"}),
        suggest_order=AsyncMock(return_value={"borrador": "creado", "total": 54000}),
        change_order=AsyncMock(return_value={"borrador": "actualizado"}),
    )
    defaults.update(overrides)
    return StoreActions(**defaults)


def tools_by_name(actions: StoreActions) -> dict[str, Tool]:
    return {tool.name: tool for tool in build_tools(actions)}


# --- Ciclo del agente -----------------------------------------------------------


async def echo_tool(args: dict) -> dict:
    return {"eco": args.get("x")}


ECHO = Tool("eco", "Devuelve x", {"type": "object", "properties": {"x": {"type": "string"}}}, echo_tool)


@pytest.mark.asyncio
async def test_agent_returns_text_without_tools():
    with patch_chat(assistant("¡Hola!")) as chat:
        result = await run_agent("hola", [ECHO], "sistema")
    assert result.reply == "¡Hola!"
    assert result.tool_calls == []
    assert result.finished
    assert result.usage.llm_calls == 1
    assert result.usage.total_tokens == 120
    sent_tools = chat.call_args.args[1]
    assert sent_tools[0]["function"]["name"] == "eco"


@pytest.mark.asyncio
async def test_agent_runs_tool_and_sends_result_back():
    with patch_chat(assistant(calls=[tool_call("eco", {"x": "hola"})]), assistant("Listo")) as chat:
        result = await run_agent("di hola", [ECHO], "sistema")
    assert result.reply == "Listo"
    assert [(c.name, c.ok, c.result) for c in result.tool_calls] == [("eco", True, {"eco": "hola"})]
    assert result.usage.llm_calls == 2
    second_messages = chat.call_args_list[1].args[0]
    assert second_messages[-2]["tool_calls"][0]["id"] == "c1"
    assert second_messages[-1] == {"role": "tool", "tool_call_id": "c1", "content": '{"eco": "hola"}'}


@pytest.mark.asyncio
async def test_agent_reports_unknown_tool_and_bad_arguments_to_model():
    calls = [tool_call("no_existe", {}, "a"), tool_call("eco", "{no es json", "b")]
    with patch_chat(assistant(calls=calls), assistant("Perdón")):
        result = await run_agent("x", [ECHO], "sistema")
    assert [c.ok for c in result.tool_calls] == [False, False]
    assert "No existe" in result.tool_calls[0].result["error"]
    assert "JSON" in result.tool_calls[1].result["error"]


@pytest.mark.asyncio
async def test_agent_hides_unexpected_tool_errors():
    async def broken(args: dict) -> dict:
        raise RuntimeError("detalle interno")

    tool = Tool("roto", "x", {"type": "object", "properties": {}}, broken)
    with patch_chat(assistant(calls=[tool_call("roto", {})]), assistant("Falló")):
        result = await run_agent("x", [tool], "sistema")
    assert "detalle interno" not in json.dumps(result.tool_calls[0].result)


@pytest.mark.asyncio
async def test_agent_stops_after_max_steps():
    loop = [assistant(calls=[tool_call("eco", {"x": "1"})]) for _ in range(3)]
    with patch_chat(*loop):
        result = await run_agent("x", [ECHO], "sistema", max_steps=3)
    assert not result.finished
    assert result.reply == ""
    assert len(result.tool_calls) == 3


# --- Herramientas del tendero ---------------------------------------------------


def test_search_products_orders_by_similarity():
    assert [p["sku"] for p in search_products("coca litro", CATALOG)][0] == "GAS-COCA-1L"
    assert search_products("", CATALOG) == []
    assert search_products("tornillos", CATALOG) == []


@pytest.mark.asyncio
async def test_buscar_producto_shows_price_and_sold_out():
    tools = tools_by_name(make_actions())
    result = await tools["buscar_producto"].run({"texto": "pan"})
    first = result["resultados"][0]
    assert first["sku"] == "PAN-TAJA-UND"
    assert first["agotado"] is True
    assert first["precio_unidad"] == 6200


@pytest.mark.asyncio
async def test_consultar_stock_filters_by_product():
    actions = make_actions()
    tools = tools_by_name(actions)
    assert (await tools["consultar_stock"].run({}))["stock"][0]["unidades"] == 7
    assert (await tools["consultar_stock"].run({"producto": "coca litro"}))["stock"][0]["sku"] == "GAS-COCA-1L"
    assert (await tools["consultar_stock"].run({"producto": "pan"}))["stock"] == []
    missing = await tools["consultar_stock"].run({"producto": "tornillos"})
    assert missing["no_encontrado"]["producto"] == "tornillos"


@pytest.mark.asyncio
async def test_registrar_ventas_matches_and_only_drafts_recognized_lines():
    actions = make_actions()
    tools = tools_by_name(actions)
    result = await tools["registrar_ventas"].run(
        {
            "ventas": [
                {"producto": "coca litro", "cantidad": 2, "texto_cantidad": "2"},
                {"producto": "tornillos", "cantidad": 1, "texto_cantidad": "1"},
            ]
        }
    )
    drafted = actions.draft_sales.call_args.args[0]
    assert [(line.sku, line.quantity) for line in drafted] == [("GAS-COCA-1L", 2)]
    assert result["reconocidas"] == [{"producto": "Coca-Cola 1 litro", "cantidad": 2}]
    assert result["no_reconocidas"][0]["producto"] == "tornillos"
    assert "Confirmar" in result["nota"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "args",
    [{}, {"ventas": []}, {"ventas": [{"producto": "coca", "cantidad": 0, "texto_cantidad": "0"}]}, {"ventas": ["coca"]}],
)
async def test_registrar_ventas_rejects_invalid_arguments(args):
    actions = make_actions()
    with pytest.raises(ToolError):
        await tools_by_name(actions)["registrar_ventas"].run(args)
    actions.draft_sales.assert_not_called()


@pytest.mark.asyncio
async def test_ajustar_pedido_builds_changes():
    actions = make_actions()
    tools = tools_by_name(actions)
    result = await tools["ajustar_pedido"].run(
        {
            "cambios": [
                {"producto": "coca", "accion": "add", "cantidad": 2, "unidad": "case"},
                {"producto": "pan tajado", "accion": "remove", "cantidad": 0, "unidad": "case"},
                {"producto": "tornillos", "accion": "add", "cantidad": 1, "unidad": "case"},
            ]
        }
    )
    changes = actions.change_order.call_args.args[0]
    assert [(c.sku, c.action, c.quantity) for c in changes] == [
        ("GAS-COCA-1L", "add", 2),
        ("PAN-TAJA-UND", "remove", 0),
    ]
    assert result["no_reconocidos"][0]["producto"] == "tornillos"


@pytest.mark.asyncio
async def test_ajustar_pedido_rejects_unknown_action():
    with pytest.raises(ToolError):
        await tools_by_name(make_actions())["ajustar_pedido"].run(
            {"cambios": [{"producto": "coca", "accion": "regalar", "cantidad": 1, "unidad": "case"}]}
        )


@pytest.mark.asyncio
async def test_full_turn_sale_message_creates_draft():
    actions = make_actions()
    call = tool_call("registrar_ventas", {"ventas": [{"producto": "coca litro", "cantidad": 2, "texto_cantidad": "2"}]})
    with patch_chat(assistant(calls=[call]), assistant("Revisa y confirma.")):
        result = await run_agent("vendí 2 coca litro", build_tools(actions), "sistema")
    assert result.called("registrar_ventas")
    assert result.reply == "Revisa y confirma."
    actions.draft_sales.assert_awaited_once()


# --- Reglas que hace cumplir el código (no el modelo) ---------------------------

EGGS = {
    "sku": "HUE-AAA-30",
    "name": "Huevos AAA x 30",
    "aliases": "huevos,huevo,cubeta",
    "presentation": "x 30",
    "unit_price": 18000,
    "units_per_case": 1,
    "sold_out": False,
}


@pytest.mark.asyncio
async def test_sale_without_said_quantity_is_not_drafted():
    actions = make_actions()
    result = await tools_by_name(actions)["registrar_ventas"].run(
        {
            "ventas": [
                {"producto": "coca litro", "cantidad": 1, "texto_cantidad": ""},
                {"producto": "pan", "cantidad": 2, "texto_cantidad": "dos"},
            ]
        }
    )
    drafted = actions.draft_sales.call_args.args[0]
    assert [(line.sku, line.quantity) for line in drafted] == [("PAN-TAJA-UND", 2)]
    assert result["preguntar"] == [{"producto": "Coca-Cola 1 litro", "motivo": "No dijo cuántas vendió"}]


@pytest.mark.asyncio
async def test_missing_said_quantity_field_counts_as_not_said():
    actions = make_actions()
    result = await tools_by_name(actions)["registrar_ventas"].run({"ventas": [{"producto": "coca", "cantidad": 1}]})
    assert actions.draft_sales.call_args.args[0] == []
    assert result["preguntar"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "item",
    [
        {"producto": "huevos sueltos", "cantidad": 6, "texto_cantidad": "6"},
        {"producto": "huevos", "cantidad": 6, "texto_cantidad": "media docena"},
    ],
)
async def test_loose_units_of_a_pack_are_not_drafted(item):
    actions = make_actions(catalog=CATALOG + [EGGS])
    result = await tools_by_name(actions)["registrar_ventas"].run({"ventas": [item]})
    assert actions.draft_sales.call_args.args[0] == []
    assert "paquete (x 30)" in result["preguntar"][0]["motivo"]


@pytest.mark.asyncio
async def test_whole_packs_and_single_products_are_drafted():
    actions = make_actions(catalog=CATALOG + [EGGS])
    await tools_by_name(actions)["registrar_ventas"].run(
        {
            "ventas": [
                {"producto": "cubetas de huevos", "cantidad": 2, "texto_cantidad": "2"},
                # "Docena" con un producto que no es de paquete no se bloquea.
                {"producto": "pan", "cantidad": 12, "texto_cantidad": "una docena"},
            ]
        }
    )
    drafted = actions.draft_sales.call_args.args[0]
    assert [(line.sku, line.quantity) for line in drafted] == [("HUE-AAA-30", 2), ("PAN-TAJA-UND", 12)]


@pytest.mark.asyncio
async def test_quoted_quantity_must_appear_in_the_message():
    actions = make_actions()
    tools = {tool.name: tool for tool in build_tools(actions, user_message="Vendí pan y DOS cocas litro")}
    result = await tools["registrar_ventas"].run(
        {
            "ventas": [
                # El modelo inventó "una": el tendero no la dijo.
                {"producto": "pan", "cantidad": 1, "texto_cantidad": "una"},
                {"producto": "coca litro", "cantidad": 2, "texto_cantidad": "dos"},
            ]
        }
    )
    drafted = actions.draft_sales.call_args.args[0]
    assert [(line.sku, line.quantity) for line in drafted] == [("GAS-COCA-1L", 2)]
    assert result["preguntar"][0]["producto"] == "Pan tajado"
