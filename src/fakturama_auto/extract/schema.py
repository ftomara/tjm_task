"""JSON Schema shaping for providers with a restricted schema dialect.

Pydantic emits nested models as ``$ref`` pointers into a ``$defs`` block.
That is valid JSON Schema, but structured-output implementations vary in how
much of the dialect they accept, and a rejected schema is an opaque 400.

:func:`inline_refs` resolves the pointers so the schema is self-contained.
The result is still ordinary JSON Schema - just flattened - so it remains
valid for providers that would have handled the references anyway.
"""

from __future__ import annotations

import copy
from typing import Any

REF_PREFIX = "#/$defs/"


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Return ``schema`` with every ``$ref`` replaced by its definition."""
    definitions = schema.get("$defs", {})
    resolved = _resolve(copy.deepcopy(schema), definitions, tuple())
    resolved.pop("$defs", None)
    return resolved


def _resolve(node: Any, definitions: dict[str, Any], path: tuple[str, ...]) -> Any:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith(REF_PREFIX):
            name = ref[len(REF_PREFIX) :]
            if name in path:
                # Recursive model: leave a permissive object rather than loop.
                return {"type": "object"}
            target = definitions.get(name)
            if target is None:
                return {"type": "object"}
            merged = _resolve(copy.deepcopy(target), definitions, path + (name,))
            # Sibling keys alongside a $ref (title, description) still apply.
            for key, value in node.items():
                if key != "$ref":
                    merged[key] = _resolve(value, definitions, path)
            return merged

        return {key: _resolve(value, definitions, path) for key, value in node.items()}

    if isinstance(node, list):
        return [_resolve(item, definitions, path) for item in node]

    return node


def strip_json_fences(text: str) -> str:
    """Remove a ```json ... ``` wrapper if the model added one.

    Only needed on the prompt-only fallback path; a server-enforced schema
    never fences its output.
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped

    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()
