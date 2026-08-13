import tempfile
import unittest
from pathlib import Path

from gmsh_doc_cn.structure import compare_structures, scan_structure


class StructureTests(unittest.TestCase):
    def test_detects_structural_and_external_file_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            texinfo = source / "doc" / "texinfo"
            tutorial = source / "tutorials"
            texinfo.mkdir(parents=True)
            tutorial.mkdir()
            (tutorial / "t1.geo").write_text("Point(1) = {0, 0, 0};\n", encoding="utf-8")
            (texinfo / "gmsh.texi").write_text(
                "@node Top\n@include api.texi\n@anchor{fixed}\n"
                "@ref{fixed}\n@verbatiminclude ../../tutorials/t1.geo\n",
                encoding="utf-8",
            )
            (texinfo / "api.texi").write_text("@node API\n", encoding="utf-8")

            baseline = scan_structure(source)
            self.assertTrue(compare_structures(baseline, scan_structure(source)).passed)
            self.assertEqual(len(baseline.external_files), 1)

            refs_by_node = {
                (record.kind, record.value): record.node
                for record in baseline.records
                if record.kind in {"anchor", "ref"}
            }
            self.assertEqual(refs_by_node[("anchor", "fixed")], "Top")
            self.assertEqual(refs_by_node[("ref", "fixed")], "Top")

            (tutorial / "t1.geo").write_text("changed\n", encoding="utf-8")
            comparison = compare_structures(baseline, scan_structure(source))
            self.assertFalse(comparison.passed)
            self.assertIn("external_files", comparison.changed_kinds)
            self.assertEqual(comparison.differences[0]["kind"], "external-files")

            (texinfo / "api.texi").write_text("@node Changed\n", encoding="utf-8")
            comparison = compare_structures(baseline, scan_structure(source))
            record_difference = next(
                item for item in comparison.differences if item["kind"] == "records"
            )
            self.assertEqual(record_difference["relative_file"], "doc/texinfo/api.texi")
            self.assertEqual(record_difference["node"], "API")

    def test_inline_structure_uses_the_node_at_its_source_position(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            texinfo = root / "doc" / "texinfo"
            texinfo.mkdir(parents=True)
            (texinfo / "gmsh.texi").write_text(
                "@node First\n@anchor{first-anchor}\n@ref{second-anchor}\n"
                "@node Second\n@anchor{second-anchor}\n@ref{first-anchor}\n",
                encoding="utf-8",
            )

            records = scan_structure(root).records
            inline = [record for record in records if record.kind in {"anchor", "ref"}]
            self.assertEqual(
                [(record.kind, record.value, record.node) for record in inline],
                [
                    ("anchor", "first-anchor", "First"),
                    ("ref", "second-anchor", "First"),
                    ("anchor", "second-anchor", "Second"),
                    ("ref", "first-anchor", "Second"),
                ],
            )


if __name__ == "__main__":
    unittest.main()
