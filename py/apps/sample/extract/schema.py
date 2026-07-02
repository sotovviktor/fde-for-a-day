"""Parse the per-request ``json_schema`` string into a dict.

Kept in its own module so schema handling is separated from image processing
and LLM interaction (Tier 2 code-structure guidance).
"""

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_JSON_SCHEMA_TYPES = frozenset({"array", "boolean", "integer", "null", "number", "object", "string"})


def _declared_types(schema: dict[str, Any]) -> set[str]:
    raw = schema.get("type")
    if isinstance(raw, str):
        return {raw}
    if isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        return set(raw)
    return set()


def _is_supported_schema(schema: dict[str, Any]) -> bool:
    declared = _declared_types(schema)
    if declared and not declared <= _JSON_SCHEMA_TYPES:
        return False
    properties = schema.get("properties")
    if properties is not None:
        return isinstance(properties, dict) and bool(properties)
    return "array" in declared and isinstance(schema.get("items"), dict)


def parse_schema(raw: str | None) -> dict[str, Any] | None:
    """Parse the request's ``json_schema`` string into a dict.

    Returns ``None`` when the schema is missing or unparseable; callers then
    fall back to schema-free extraction/normalization rather than failing.
    """
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("could not parse json_schema; proceeding without it")
        return None
    if not isinstance(parsed, dict):
        logger.warning("json_schema is not an object; proceeding without it")
        return None
    if not _is_supported_schema(parsed):
        logger.warning("json_schema has unsupported structure; proceeding without it")
        return None
    return parsed
