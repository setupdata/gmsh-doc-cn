"""Small deterministic validator for the JSON Schema subset used by this repository."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence


_TYPES = {
    "object": lambda value: isinstance(value, Mapping),
    "array": lambda value: isinstance(value, list),
    "string": lambda value: isinstance(value, str),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    "boolean": lambda value: isinstance(value, bool),
    "null": lambda value: value is None,
}


def _json_equal(left: object, right: object) -> bool:
    """Compare JSON values without treating booleans as the numbers 0 and 1."""

    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left is right
    return left == right


def validate_schema(instance: object, schema: Mapping[str, object], path: str = "$") -> None:
    """Raise ValueError when an instance violates the project's schema subset."""

    if "const" in schema and not _json_equal(instance, schema["const"]):
        raise ValueError(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and not any(_json_equal(instance, value) for value in schema["enum"]):
        raise ValueError(f"{path}: value is not in enum")
    expected_type = schema.get("type")
    if expected_type is not None:
        names: Sequence[str] = [expected_type] if isinstance(expected_type, str) else expected_type
        if not any(_TYPES[name](instance) for name in names):
            raise ValueError(f"{path}: expected type {expected_type!r}")
    if isinstance(instance, Mapping):
        required = {str(value) for value in schema.get("required", [])}
        missing = required - instance.keys()
        if missing:
            raise ValueError(f"{path}: missing required properties {sorted(missing)}")
        properties = schema.get("properties", {})
        extras = set(instance) - set(properties)
        additional = schema.get("additionalProperties")
        if additional is False:
            if extras:
                raise ValueError(f"{path}: unsupported properties {sorted(extras)}")
        elif isinstance(additional, Mapping):
            for key in sorted(extras):
                validate_schema(instance[key], additional, f"{path}.{key}")
        for key, child_schema in properties.items():
            if key in instance:
                validate_schema(instance[key], child_schema, f"{path}.{key}")
    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < int(schema["minItems"]):
            raise ValueError(f"{path}: too few items")
        if "maxItems" in schema and len(instance) > int(schema["maxItems"]):
            raise ValueError(f"{path}: too many items")
        child_schema = schema.get("items")
        if child_schema is not None:
            for index, value in enumerate(instance):
                validate_schema(value, child_schema, f"{path}[{index}]")
    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < int(schema["minLength"]):
            raise ValueError(f"{path}: string is too short")
        if "pattern" in schema and re.search(str(schema["pattern"]), instance) is None:
            raise ValueError(f"{path}: string does not match pattern")
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            raise ValueError(f"{path}: number is below minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            raise ValueError(f"{path}: number is above maximum")
