import unittest

from gmsh_doc_cn.schema_validation import validate_schema


class SchemaValidationTests(unittest.TestCase):
    def test_validates_min_items_and_typed_additional_properties(self) -> None:
        with self.assertRaisesRegex(ValueError, "too few items"):
            validate_schema([], {"type": "array", "minItems": 1})
        with self.assertRaisesRegex(ValueError, "expected type"):
            validate_schema(
                {"formal": "one"},
                {"type": "object", "additionalProperties": {"type": "integer"}},
            )

    def test_boolean_does_not_satisfy_numeric_const_or_enum(self) -> None:
        with self.assertRaisesRegex(ValueError, "constant"):
            validate_schema(True, {"const": 1})
        with self.assertRaisesRegex(ValueError, "enum"):
            validate_schema(True, {"enum": [1, 2]})


if __name__ == "__main__":
    unittest.main()
