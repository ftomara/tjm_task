"""Tests for JSON Schema flattening and response cleanup.

Pydantic emits nested models as ``$ref``/``$defs``. Structured-output
implementations differ in how much of that dialect they accept, and a rejected
schema surfaces as an opaque 400, so the references are resolved before the
schema is sent.
"""

from __future__ import annotations

import json

from fakturama_auto.extract.schema import inline_refs, strip_json_fences
from fakturama_auto.models import RawOrderExtraction


def test_the_real_extraction_schema_flattens_completely():
    flattened = inline_refs(RawOrderExtraction.model_json_schema())
    rendered = json.dumps(flattened)
    assert "$ref" not in rendered
    assert "$defs" not in flattened


def test_flattening_preserves_the_nested_shape():
    flattened = inline_refs(RawOrderExtraction.model_json_schema())

    items = flattened["properties"]["items"]
    line = items["items"]
    assert line["properties"]["sku"]["type"] == "string"

    billing = flattened["properties"]["customer"]["properties"]["billing"]
    assert "zip_code" in billing["properties"]


def test_sibling_keys_alongside_a_ref_survive():
    schema = {
        "$defs": {"Inner": {"type": "object", "properties": {"a": {"type": "string"}}}},
        "type": "object",
        "properties": {
            "thing": {"$ref": "#/$defs/Inner", "description": "the thing"},
        },
    }
    thing = inline_refs(schema)["properties"]["thing"]
    assert thing["type"] == "object"
    assert thing["description"] == "the thing"


def test_a_recursive_model_does_not_loop_forever():
    schema = {
        "$defs": {
            "Node": {
                "type": "object",
                "properties": {"child": {"$ref": "#/$defs/Node"}},
            }
        },
        "$ref": "#/$defs/Node",
    }
    resolved = inline_refs(schema)
    assert resolved["properties"]["child"] == {"type": "object"}


def test_a_dangling_ref_degrades_instead_of_raising():
    schema = {"type": "object", "properties": {"x": {"$ref": "#/$defs/Missing"}}}
    assert inline_refs(schema)["properties"]["x"] == {"type": "object"}


def test_plain_json_passes_through_unfenced():
    assert strip_json_fences('{"a": 1}') == '{"a": 1}'


def test_markdown_fences_are_stripped():
    assert strip_json_fences('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert strip_json_fences('```\n{"a": 1}\n```') == '{"a": 1}'


def test_fence_stripping_leaves_inner_backticks_alone():
    assert strip_json_fences('```json\n{"note": "use ``x``"}\n```') == '{"note": "use ``x``"}'
