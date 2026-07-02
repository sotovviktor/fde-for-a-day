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


def test_deeply_nested_objects_recurse_to_arbitrary_depth() -> None:
    schema = {
        "type": "object",
        "properties": {
            "a": {
                "type": "object",
                "properties": {
                    "b": {
                        "type": "object",
                        "properties": {
                            "c": {
                                "type": "object",
                                "properties": {"amount": {"type": "number"}, "note": {"type": "string"}},
                            }
                        },
                    }
                },
            }
        },
    }
    raw = {"a": {"b": {"c": {"amount": "$3,210.99", "note": "$3,210.99"}}}}
    out = normalize_output(raw, schema)
    assert out["a"]["b"]["c"]["amount"] == 3210.99
    assert out["a"]["b"]["c"]["note"] == "$3,210.99"  # string preserved verbatim


def test_arrays_of_objects_with_nested_arrays_recurse() -> None:
    schema = {
        "type": "object",
        "properties": {
            "invoices": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "total": {"type": "number"},
                        "lines": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {"qty": {"type": "integer"}, "price": {"type": "number"}},
                            },
                        },
                    },
                },
            }
        },
    }
    raw = {
        "invoices": [
            {"total": "$1,000.50", "lines": [{"qty": "2", "price": "500.25"}]},
            {"total": "2,001", "lines": [{"qty": "1", "price": "$2,001.00"}]},
        ]
    }
    out = normalize_output(raw, schema)
    assert out["invoices"][0]["total"] == 1000.50
    assert out["invoices"][0]["lines"][0]["qty"] == 2
    assert out["invoices"][1]["lines"][0]["price"] == 2001.0


def test_missing_schema_property_is_filled_with_null() -> None:
    schema = {
        "type": "object",
        "properties": {
            "present": {"type": "string"},
            "absent": {"type": "number"},
            "nested": {"type": "object", "properties": {"inner": {"type": "string"}}},
        },
    }
    out = normalize_output({"present": "here"}, schema)
    assert out["present"] == "here"
    assert out["absent"] is None
    assert out["nested"]["inner"] is None  # nested props filled too


def test_explicit_null_stays_null() -> None:
    schema = {"type": "object", "properties": {"amount": {"type": "number"}}}
    assert normalize_output({"amount": None}, schema)["amount"] is None


def test_checkbox_boolean_defaults_to_false_when_missing() -> None:
    schema = {
        "type": "object",
        "properties": {
            "agreed": {"type": "boolean", "description": "Checkbox; defaults to false if unchecked."},
            "signed": {"type": "boolean", "description": "True if signed, default false."},
            "flag": {"type": "boolean", "description": "Some other boolean field."},
        },
    }
    out = normalize_output({}, schema)
    assert out["agreed"] is False
    assert out["signed"] is False
    assert out["flag"] is None  # plain boolean without checkbox/default hint stays null


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
