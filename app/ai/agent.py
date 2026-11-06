"""Agente con tool calling sobre Grok.

Grok decide qué herramienta usar según lo que escribe el tendero; el código ejecuta la herramienta
y le devuelve el resultado. Los cálculos (stock, alertas, pedido) nunca los hace el modelo: los
hacen las herramientas con el motor de inventario.

El ciclo es corto (MAX_STEPS vueltas) para acotar costo y latencia.
"""

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.ai.grok_client import chat_with_tools

MAX_STEPS = 4

logger = logging.getLogger(__name__)


class ToolError(Exception):
    """Error que se le explica al modelo (argumentos inválidos, producto no encontrado...)."""


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    run: Callable[[dict], Awaitable[dict]]

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }


@dataclass
class ToolCallRecord:
    name: str
    arguments: dict
    result: dict
    ok: bool


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    llm_calls: int = 0

    def add(self, raw: dict) -> None:
        self.prompt_tokens += int(raw.get("prompt_tokens") or 0)
        self.completion_tokens += int(raw.get("completion_tokens") or 0)
        self.llm_calls += 1

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class AgentResult:
    reply: str
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    # False si se acabaron las vueltas sin respuesta final.
    finished: bool = True

    def called(self, name: str) -> bool:
        return any(call.name == name for call in self.tool_calls)


def _parse_arguments(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ToolError("Los argumentos deben ser un objeto JSON")
    return data


async def _run_tool(tools: dict[str, Tool], call: dict) -> ToolCallRecord:
    function = call.get("function") or {}
    name = str(function.get("name") or "")
    try:
        arguments = _parse_arguments(function.get("arguments"))
    except (json.JSONDecodeError, ToolError):
        return ToolCallRecord(name, {}, {"error": "Argumentos inválidos: deben ser JSON"}, ok=False)
    tool = tools.get(name)
    if tool is None:
        return ToolCallRecord(name, arguments, {"error": f"No existe la herramienta {name}"}, ok=False)
    try:
        result = await tool.run(arguments)
    except ToolError as exc:
        return ToolCallRecord(name, arguments, {"error": str(exc)}, ok=False)
    except Exception:
        # Nunca se registra el contenido: solo qué herramienta falló.
        logger.exception("Falló la herramienta %s", name)
        return ToolCallRecord(name, arguments, {"error": "La herramienta falló. No lo intentes de nuevo."}, ok=False)
    return ToolCallRecord(name, arguments, result, ok=True)


async def run_agent(
    user_message: str,
    tools: list[Tool],
    system_prompt: str,
    max_steps: int = MAX_STEPS,
) -> AgentResult:
    """Corre el agente hasta que Grok responda con texto o se acaben las vueltas."""
    by_name = {tool.name: tool for tool in tools}
    schemas = [tool.schema() for tool in tools]
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message.strip()},
    ]
    result = AgentResult(reply="")
    for _ in range(max_steps):
        message, usage = await chat_with_tools(messages, schemas)
        result.usage.add(usage)
        calls = message.get("tool_calls") or []
        if not calls:
            result.reply = str(message.get("content") or "").strip()
            return result
        messages.append(
            {"role": "assistant", "content": message.get("content") or "", "tool_calls": calls}
        )
        for call in calls:
            record = await _run_tool(by_name, call)
            result.tool_calls.append(record)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id") or "",
                    "content": json.dumps(record.result, ensure_ascii=False, default=str),
                }
            )
    result.finished = False
    return result
