"""Schema-guided post-processing for extraction output.

* Number/integer fields are coerced to real JSON numbers (``"$1,234.56"`` must
  become ``1234.56``).
* String fields are left EXACTLY as extracted, because text fidelity is an exact
  match on the on-document formatting (stripping ``$``/commas here would lose
  those points).
* Nested objects and array items are normalized recursively to arbitrary depth
  (objects-in-objects and objects-in-arrays), matching the adversarial subtype.
* Every schema-defined property is emitted. Missing/unreadable values become
  ``null`` (never ``""`` or ``0``), except boolean checkbox fields whose
  description asks for a ``false`` default.
* Keys the schema does not define are pruned.
"""

import re
from typing import Any

# Formatting artifacts stripped only from values headed for a numeric field.
_STRIP_FOR_NUMBER = re.compile(r"[,$€£¥₹%\s]")


def _schema_type(subschema: dict[str, Any]) -> str | None:
    """Return the declared JSON type, tolerating ``["string", "null"]`` unions."""
    raw = subschema.get("type")
    if isinstance(raw, list):
        return next((item for item in raw if item != "null"), None)
    return raw if isinstance(raw, str) else None


def _coerce_number(value: Any, *, integer: bool) -> float | int | None:
    """Coerce a value to a number, stripping currency/percent/grouping artifacts."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if not isinstance(value, str):
        return None
    cleaned = _STRIP_FOR_NUMBER.sub("", value)
    if cleaned in ("", "-", "+", ".", "-.", "+."):
        return None
    try:
        number = float(cleaned)
    except ValueError:
        return None
    if integer and number.is_integer():
        return int(number)
    return number


def _coerce_bool(value: Any) -> bool | None:
    """Coerce common truthy/falsy string spellings to a real bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "yes", "y", "1"):
            return True
        if lowered in ("false", "no", "n", "0"):
            return False
    return None


def _wants_false_default(subschema: dict[str, Any]) -> bool:
    """True for boolean fields whose description asks for a ``false`` default.

    Checkbox-style fields (for example "checkbox; defaults to false if
    unchecked") should read as ``false`` rather than ``null`` when the model
    leaves them out.
    """
    if _schema_type(subschema) != "boolean":
        return False
    description = subschema.get("description")
    if not isinstance(description, str):
        return False
    lowered = description.lower()
    if "checkbox" in lowered:
        return True
    return "default" in lowered and "false" in lowered


def _default_value(subschema: dict[str, Any]) -> Any:
    """Value to emit for a schema property the model did not return.

    Missing scalars become ``null`` (checkbox booleans become ``false``). A
    missing object expands into a null-filled skeleton so every nested property
    the schema defines is still present.
    """
    if _wants_false_default(subschema):
        return False
    if _schema_type(subschema) == "object" or "properties" in subschema:
        properties = subschema.get("properties")
        if isinstance(properties, dict) and properties:
            return {key: _default_value(sub) if isinstance(sub, dict) else None for key, sub in properties.items()}
    return None


def _normalize_value(value: Any, subschema: dict[str, Any]) -> Any:
    if value is None or not isinstance(subschema, dict):
        return value
    declared = _schema_type(subschema)
    if declared == "object" or "properties" in subschema:
        return _normalize_object(value, subschema) if isinstance(value, dict) else value
    if declared == "array" or "items" in subschema:
        item_schema = subschema.get("items")
        if isinstance(value, list) and isinstance(item_schema, dict):
            return [_normalize_value(item, item_schema) for item in value]
        return value
    if declared in ("number", "integer"):
        return _coerce_number(value, integer=declared == "integer")
    if declared == "boolean":
        return _coerce_bool(value)
    return value


def _normalize_object(obj: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        # Object with no declared properties: nothing to validate against, so
        # keep whatever the model returned rather than dropping data.
        return obj
    result: dict[str, Any] = {}
    for key, subschema in properties.items():
        if not isinstance(subschema, dict):
            result[key] = obj.get(key)
            continue
        value = obj.get(key)
        # Prune extra keys and fill every declared property: missing or explicit
        # ``null`` values fall back to the schema-appropriate default.
        result[key] = _normalize_value(value, subschema) if value is not None else _default_value(subschema)
    return result


def normalize_output(raw: Any, schema: dict[str, Any] | None) -> dict[str, Any]:
    """Return a normalized copy of the model's JSON, guided by ``schema``.

    Non-dict input yields an empty dict. With no schema the object is returned
    as-is (only shallow-copied) so we never drop data we cannot validate.
    """
    if not isinstance(raw, dict):
        return {}
    if not schema:
        return dict(raw)
    return _normalize_object(raw, schema)
