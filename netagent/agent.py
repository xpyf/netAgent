"""Five-step tool-calling loop (research finding 4, ticket 05).

`run_agent(client, ...)` drives a chat-completions client until the model stops
calling tools; `MAX_LOOPS` guards against infinite tool loops. The registry maps
tool names to Python callables taking **kwargs.
"""
from __future__ import annotations

import json
from typing import Any, Callable

MAX_LOOPS = 8


def run_agent(
    client: Any,
    model: str,
    system: str,
    user: str,
    tools: list[dict[str, Any]],
    registry: dict[str, Callable[..., Any]],
    max_loops: int = MAX_LOOPS,
) -> str:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    for _ in range(max_loops):
        resp = client.chat.completions.create(model=model, messages=messages, tools=tools)
        msg = resp.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None)
        if not tool_calls:
            return msg.content or ""
        messages.append(
            {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {"name": c.function.name, "arguments": c.function.arguments},
                    }
                    for c in tool_calls
                ],
            }
        )
        for tc in tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError as exc:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps({"error": {"code": "bad_args", "message": str(exc)}}),
                    }
                )
                continue
            fn = registry.get(tc.function.name)
            if fn is None:
                result = {"error": {"code": "bad_args", "message": f"unknown tool '{tc.function.name}'"}}
            else:
                try:
                    result = fn(**args)
                except TypeError as exc:
                    result = {"error": {"code": "bad_args", "message": f"bad arguments for '{tc.function.name}': {exc}"}}
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result, ensure_ascii=False)})
    raise RuntimeError("max_loops exceeded")
