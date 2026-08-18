import argparse
import unittest

from gmsh_doc_cn.cli import _selected_check_units


class CheckUnitSelectionTests(unittest.TestCase):
    def test_check_all_expands_and_sorts_every_unit(self) -> None:
        args = argparse.Namespace(check_unit=[], check_all=True, source="unused")
        self.assertEqual(
            _selected_check_units(args, all_unit_ids=["unit-b", "unit-a"]),
            ["unit-a", "unit-b"],
        )

    def test_explicit_and_all_unit_selections_are_deduplicated(self) -> None:
        args = argparse.Namespace(
            check_unit=["unit-b", "unit-c"], check_all=True, source="unused"
        )
        self.assertEqual(
            _selected_check_units(args, all_unit_ids=["unit-a", "unit-b"]),
            ["unit-a", "unit-b", "unit-c"],
        )

    def test_empty_selection_is_rejected(self) -> None:
        args = argparse.Namespace(check_unit=[], check_all=False, source="unused")
        with self.assertRaisesRegex(
            ValueError, "--check-all or at least one --check-unit"
        ):
            _selected_check_units(args)


if __name__ == "__main__":
    unittest.main()
