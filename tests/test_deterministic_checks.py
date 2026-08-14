import tempfile
import unittest
from pathlib import Path

from gmsh_doc_cn.catalog import extract_tree, write_po
from gmsh_doc_cn.deterministic_checks import run_deterministic_checks


class DeterministicCheckTests(unittest.TestCase):
    def _source(self, root: Path) -> Path:
        source = root / "source"
        texinfo = source / "doc" / "texinfo"
        texinfo.mkdir(parents=True)
        (texinfo / "gmsh.texi").write_text(
            "@node Top\n@chapter Manual\n\nOpen @file{a.geo}.\n",
            encoding="utf-8",
        )
        return source

    def test_checks_current_po_identity_protected_content_and_structure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._source(root)
            units = extract_tree(source).units
            unit = units[-1]
            po = root / "zh_CN.po"
            write_po(
                units,
                po,
                language="zh_CN",
                translations={unit.msgctxt: "打开 @file{a.geo}。"},
            )

            report = run_deterministic_checks(source, po, [unit.unit_id])

            self.assertTrue(report["all_passed"])
            self.assertEqual(report["checked_count"], 1)
            self.assertTrue(report["structure"]["passed"])
            self.assertEqual(report["structure"]["differences"], [])
            self.assertEqual(report["units"][0]["result"], "pass")
            self.assertTrue(all(report["units"][0]["checks"].values()))

    def test_rejects_po_msgid_drift_and_does_not_claim_a_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._source(root)
            units = extract_tree(source).units
            unit = units[-1]
            po = root / "zh_CN.po"
            write_po(
                units,
                po,
                language="zh_CN",
                translations={unit.msgctxt: "打开 @file{a.geo}。"},
            )
            po.write_text(
                po.read_text(encoding="utf-8").replace(
                    'msgid "Open @file{a.geo}."', 'msgid "Changed source text."'
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "PO msgid does not match"):
                run_deterministic_checks(source, po, [unit.unit_id])

    def test_rejects_an_empty_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._source(root)
            po = root / "zh_CN.po"
            write_po(extract_tree(source).units, po, language="zh_CN")

            with self.assertRaisesRegex(ValueError, "at least one unit_id"):
                run_deterministic_checks(source, po, [])

    def test_changed_protected_content_fails_without_modifying_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._source(root)
            units = extract_tree(source).units
            unit = units[-1]
            po = root / "zh_CN.po"
            write_po(
                units,
                po,
                language="zh_CN",
                translations={unit.msgctxt: "打开 @file{b.geo}。"},
            )

            report = run_deterministic_checks(source, po, [unit.unit_id])

            self.assertFalse(report["all_passed"])
            self.assertFalse(report["units"][0]["checks"]["protected_content"])
            self.assertEqual(report["units"][0]["result"], "fail")

    def test_translated_index_terms_do_not_count_as_structure_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            texinfo = source / "doc" / "texinfo"
            texinfo.mkdir(parents=True)
            (texinfo / "gmsh.texi").write_text(
                "@node Top\n@chapter Manual\n@cindex Introduction\n",
                encoding="utf-8",
            )
            units = extract_tree(source).units
            index_unit = next(unit for unit in units if unit.role == "index")
            po = root / "zh_CN.po"
            write_po(
                units,
                po,
                language="zh_CN",
                translations={index_unit.msgctxt: "简介"},
            )

            report = run_deterministic_checks(source, po, [index_unit.unit_id])

            self.assertTrue(report["all_passed"])
            self.assertTrue(report["structure"]["passed"])


if __name__ == "__main__":
    unittest.main()
