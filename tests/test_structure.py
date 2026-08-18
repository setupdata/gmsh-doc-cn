import tempfile
import unittest
from pathlib import Path

from gmsh_doc_cn.structure import (
    compare_structures,
    mask_external_file_content,
    scan_structure,
)


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

    def test_image_alt_text_is_translatable_but_image_identity_is_structural(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            texinfo = root / "doc" / "texinfo"
            texinfo.mkdir(parents=True)
            source = texinfo / "gmsh.texi"
            source.write_text(
                "@node Top\n@image{images/t1,9cm,,Screenshot of tutorial t1,png}\n",
                encoding="utf-8",
            )
            baseline = scan_structure(root)

            source.write_text(
                "@node Top\n@image{images/t1,9cm,,教程 t1 的屏幕截图,png}\n",
                encoding="utf-8",
            )
            self.assertTrue(compare_structures(baseline, scan_structure(root)).passed)

            source.write_text(
                "@node Top\n@image{images/t2,9cm,,教程 t1 的屏幕截图,png}\n",
                encoding="utf-8",
            )
            self.assertFalse(compare_structures(baseline, scan_structure(root)).passed)

    def test_can_compare_translated_verbatim_includes_by_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            texinfo = root / "doc" / "texinfo"
            texinfo.mkdir(parents=True)
            included = root / "CHANGELOG.txt"
            (texinfo / "gmsh.texi").write_text(
                "@node Top\n@verbatiminclude ../../CHANGELOG.txt\n",
                encoding="utf-8",
            )
            included.write_text("Original release note.\n", encoding="utf-8")
            source = scan_structure(root)

            included.write_text("译后的发布说明。\n", encoding="utf-8")
            translated = scan_structure(root)
            self.assertFalse(compare_structures(source, translated).passed)
            self.assertTrue(
                compare_structures(
                    mask_external_file_content(source, {"CHANGELOG.txt"}),
                    mask_external_file_content(translated, {"CHANGELOG.txt"}),
                ).passed
            )


if __name__ == "__main__":
    unittest.main()
