"""Unit tests for schema-guided extraction normalization (no network)."""

from extract.normalize import normalize_output


def test_number_fields_are_coerced_and_stripped() -> None:
    schema = {
        "type": "object",
        "properties": {
            "amount": {"type": "number"},
            "count": {"type": "integer"},
            "rate": {"type": "number"},
        },
    }
    out = normalize_output({"amount": "$1,234.56", "count": "1,234", "rate": "10%"}, schema)
    assert out["amount"] == 1234.56
    assert out["count"] == 1234
    assert isinstance(out["count"], int)
    assert out["rate"] == 10


def test_string_fields_preserve_exact_text() -> None:
    schema = {"type": "object", "properties": {"total": {"type": "string"}}}
    # Fidelity depends on keeping the on-document formatting verbatim.
    assert normalize_output({"total": "$1,234.00"}, schema)["total"] == "$1,234.00"


def test_uncoercible_number_becomes_null() -> None:
    schema = {"type": "object", "properties": {"amount": {"type": "number"}}}
    assert normalize_output({"amount": "N/A"}, schema)["amount"] is None


def test_boolean_coercion() -> None:
    schema = {
        "type": "object",
        "properties": {"isSigned": {"type": "boolean"}, "isNew": {"type": "boolean"}},
    }
    out = normalize_output({"isSigned": "true", "isNew": False}, schema)
    assert out["isSigned"] is True
    assert out["isNew"] is False


def test_nested_objects_and_arrays_recurse() -> None:
    schema = {
        "type": "object",
        "properties": {
            "rent": {"type": "object", "properties": {"monthlyAmount": {"type": "number"}}},
            "rows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"value": {"type": "number"}, "label": {"type": "string"}},
                },
            },
        },
    }
    raw = {
        "rent": {"monthlyAmount": "9,904"},
        "rows": [{"value": "1,000", "label": "$1,000"}, {"value": "2", "label": "two"}],
    }
    out = normalize_output(raw, schema)
    assert out["rent"]["monthlyAmount"] == 9904
    assert out["rows"][0]["value"] == 1000
    assert out["rows"][0]["label"] == "$1,000"  # string preserved
    assert out["rows"][1]["value"] == 2


def test_non_schema_keys_are_pruned() -> None:
    schema = {"type": "object", "properties": {"keep": {"type": "string"}}}
    assert normalize_output({"keep": "yes", "drop": "no"}, schema) == {"keep": "yes"}


def test_nullable_union_type_is_handled() -> None:
    schema = {"type": "object", "properties": {"amount": {"type": ["number", "null"]}}}
    assert normalize_output({"amount": "1,000"}, schema)["amount"] == 1000


def test_without_schema_returns_shallow_copy() -> None:
    raw = {"a": 1, "b": "x"}
    out = normalize_output(raw, None)
    assert out == raw
    assert out is not raw


def test_non_dict_returns_empty() -> None:
    assert normalize_output([1, 2, 3], {"type": "object"}) == {}
