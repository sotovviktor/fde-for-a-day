"""Unit tests for extraction schema parsing."""

from extract.schema import parse_schema


def test_parse_schema_accepts_object_properties() -> None:
    schema = parse_schema('{"type":"object","properties":{"amount":{"type":"number"}}}')

    assert schema is not None
    assert schema["properties"]["amount"]["type"] == "number"


def test_parse_schema_rejects_shape_less_object() -> None:
    assert parse_schema('{"type":"object"}') is None
    assert parse_schema('{"description":"not enough structure"}') is None


def test_parse_schema_rejects_invalid_type_value() -> None:
    assert parse_schema('{"type":"spaceship","properties":{"amount":{"type":"number"}}}') is None


def test_parse_schema_rejects_non_object_json() -> None:
    assert parse_schema('["amount"]') is None
