import os
import tempfile
import unittest
import tomllib
from pathlib import Path

from gmsh_doc_cn.catalog import (
    apply_catalog,
    extract_tree,
    protected_values,
    read_po,
    read_po_catalog,
    write_po,
)
from gmsh_doc_cn.pilot import select_pilot_units, validate_pilot_manifest
from gmsh_doc_cn.structure import compare_structures, scan_structure


class CatalogTests(unittest.TestCase):
    def test_protects_long_options_but_not_texinfo_em_dash_text(self) -> None:
        self.assertEqual(protected_values("Use --verbose when needed."), ("--verbose",))
        self.assertEqual(
            protected_values("material properties---this is a job for the solver"),
            (),
        )

    def test_reads_fuzzy_and_obsolete_gettext_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.po"
            path.write_text(
                '#, fuzzy\nmsgctxt "active"\nmsgid "Source"\nmsgstr "候选"\n\n'
                '#~ msgctxt "old"\n#~ msgid "Old"\n#~ msgstr "旧译文"\n',
                encoding="utf-8",
            )
            catalog = read_po_catalog(path)
            self.assertEqual(catalog.translations, {"active": "候选"})
            self.assertEqual(catalog.flags, {"active": {"fuzzy"}})
            self.assertEqual(catalog.obsolete_count, 1)
            self.assertEqual(catalog.obsolete_entries[0].msgctxt, "old")
    def test_title_metadata_authors_numbers_and_units_are_not_translatable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            texinfo = root / "doc" / "texinfo"
            texinfo.mkdir(parents=True)
            (texinfo / "gmsh.texi").write_text(
                "@settitle Gmsh 4.15.2\n"
                "@title Gmsh Reference Manual\n"
                "@author Christophe Geuzaine\n\n"
                "@node Top\n"
                "A distance of 10 mm is used in this example.\n",
                encoding="utf-8",
            )
            extraction = extract_tree(root)
            self.assertEqual(len(extraction.units), 1)
            self.assertEqual(extraction.units[0].msgid, "A distance of 10 mm is used in this example.")
            self.assertIn("10 mm", extraction.units[0].protected_values)

    def test_copying_environment_is_never_extracted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            texinfo = root / "doc" / "texinfo"
            texinfo.mkdir(parents=True)
            (texinfo / "gmsh.texi").write_text(
                "@copying\n"
                "This license paragraph must remain in English.\n"
                "@end copying\n\n"
                "@node Top\n"
                "This ordinary documentation paragraph is translatable.\n",
                encoding="utf-8",
            )

            extraction = extract_tree(root)

            self.assertEqual(len(extraction.units), 1)
            self.assertEqual(
                extraction.units[0].msgid,
                "This ordinary documentation paragraph is translatable.",
            )

    def test_release_license_nodes_and_title_metadata_are_never_extracted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            texinfo = root / "doc" / "texinfo"
            texinfo.mkdir(parents=True)
            (texinfo / "gmsh.texi").write_text(
                "@ifinfo\n"
                "This is generated manual metadata.\n"
                "@end ifinfo\n"
                "@titlepage\n"
                "Copyright Example Author\n"
                "Permission is granted for verbatim copies.\n"
                "@end titlepage\n"
                "@node Top\n"
                "@top Gmsh\n\n"
                "Christophe Geuzaine and Jean-Fran@,{c}ois Remacle\n\n"
                "This ordinary introduction is translatable.\n\n"
                "@node Copying conditions\n"
                "@unnumbered Copying conditions\n\n"
                "This license explanation must remain in English.\n\n"
                "@node License\n"
                "@appendix License\n"
                "@verbatiminclude ../../LICENSE.txt\n",
                encoding="utf-8",
            )

            msgids = {unit.msgid for unit in extract_tree(root).units}

            self.assertEqual(msgids, {"This ordinary introduction is translatable."})
    def test_empty_catalog_round_trip_is_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            texinfo = source / "doc" / "texinfo"
            texinfo.mkdir(parents=True)
            original = (
                "@node Overview of Gmsh\n"
                "@chapter Overview of Gmsh\n\n"
                "Gmsh reads @file{model.geo} and calls @code{gmsh/model}.\n"
                "This remains one paragraph.\n\n"
                "@example\nMesh 3;\n@end example\n"
            )
            (texinfo / "gmsh.texi").write_text(original, encoding="utf-8")

            extraction = extract_tree(source)
            self.assertEqual(len(extraction.units), 2)
            paragraph = extraction.units[1]
            self.assertEqual(paragraph.node, "Overview of Gmsh")
            self.assertIn("@file{model.geo}", paragraph.protected_values)
            self.assertIn("@code{gmsh/model}", paragraph.protected_values)
            self.assertEqual(paragraph.category, "ordinary_tutorial")

            po_path = root / "zh_CN.po"
            write_po(extraction.units, po_path, language="zh_CN")
            catalog = read_po(po_path)
            output = root / "translated"
            apply_catalog(source, output, extraction.units, catalog)

            self.assertEqual(
                (output / "doc" / "texinfo" / "gmsh.texi").read_bytes(),
                (texinfo / "gmsh.texi").read_bytes(),
            )

    def test_rejects_translation_that_changes_protected_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            texinfo = source / "doc" / "texinfo"
            texinfo.mkdir(parents=True)
            (texinfo / "gmsh.texi").write_text(
                "@node Top\n@chapter Manual\n\nOpen @file{a.geo}.\n",
                encoding="utf-8",
            )
            extraction = extract_tree(source)
            paragraph = extraction.units[-1]
            catalog = {paragraph.msgctxt: "打开 @file{b.geo}。"}

            with self.assertRaisesRegex(ValueError, "protected content"):
                apply_catalog(source, root / "translated", extraction.units, catalog)

    def test_preserves_texinfo_syntax_while_allowing_formatted_text_and_labels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            texinfo = source / "doc" / "texinfo"
            texinfo.mkdir(parents=True)
            (texinfo / "gmsh.texi").write_text(
                "@node Top\n@chapter Manual\n\n"
                "Read @strong{important @emph{text}} on "
                "@url{https://example.com/help,help page}.\n\n"
                "@center@image{images/t1,9cm,,Screenshot of tutorial t1}\n",
                encoding="utf-8",
            )
            units = extract_tree(source).units
            prose = next(unit for unit in units if unit.msgid.startswith("Read "))
            image = next(unit for unit in units if "@image{" in unit.msgid)
            self.assertIn("@strong{", prose.protected_values)
            self.assertIn("@url{https://example.com/help,", prose.protected_values)
            self.assertNotIn("help page", prose.protected_values)
            self.assertIn("@image{images/t1,9cm,,", image.protected_values)
            self.assertNotIn("Screenshot of tutorial t1", image.protected_values)

            output = root / "translated"
            apply_catalog(
                source,
                output,
                units,
                {
                    prose.msgctxt: (
                        "请阅读 @strong{重要的 @emph{文字}}，并访问 "
                        "@url{https://example.com/help,帮助页面}。"
                    ),
                    image.msgctxt: "@center@image{images/t1,9cm,,教程 t1 截图}",
                },
            )
            translated = (output / "doc" / "texinfo" / "gmsh.texi").read_text(encoding="utf-8")
            self.assertIn("@url{https://example.com/help,帮助页面}", translated)
            self.assertIn("@image{images/t1,9cm,,教程 t1 截图}", translated)

            with self.assertRaisesRegex(ValueError, "protected content"):
                apply_catalog(
                    source,
                    root / "broken",
                    units,
                    {prose.msgctxt: "请阅读 @strong{重要的 @emph{文字}，并访问 @url{https://example.com/help,帮助页面}。"},
                )

    def test_inline_comments_and_print_indexes_are_not_translation_units(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            texinfo = root / "doc" / "texinfo"
            texinfo.mkdir(parents=True)
            (texinfo / "gmsh.texi").write_text(
                "\\input texinfo.tex @c -*-texinfo-*-\n"
                "@node Top\n@chapter Manual\n\n"
                "Visible documentation. @c private maintainer comment\n\n"
                "@node Concept index\n@unnumbered Concept index\n@printindex cp\n",
                encoding="utf-8",
            )
            msgids = {unit.msgid for unit in extract_tree(root).units}
            self.assertIn("Visible documentation.", msgids)
            self.assertFalse(any("private maintainer" in value for value in msgids))
            self.assertFalse(any("texinfo.tex" in value for value in msgids))
            self.assertNotIn("@printindex cp", msgids)

    def test_generated_api_and_option_units_keep_semantic_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            texinfo = root / "doc" / "texinfo"
            texinfo.mkdir(parents=True)
            (texinfo / "api.texi").write_text(
                "@node Namespace gmsh\n@section Namespace @code{gmsh}\n\n"
                "@ftable @code\n@item gmsh/initialize\nInitialize the API.\n@end ftable\n",
                encoding="utf-8",
            )
            (texinfo / "opt_general.texi").write_text(
                "@ftable @code\n@item General.Terminal\nPrint messages@*\n"
                "Default value: @code{1}\n@end ftable\n",
                encoding="utf-8",
            )
            (texinfo / "fields.texi").write_text(
                "@ftable @code\n@item Distance\nCompute a distance field.\n"
                "@table @code\n@item Sampling\nDefault value: @code{100}\n"
                "@end table\n@end ftable\n",
                encoding="utf-8",
            )
            (texinfo / "cmake_options.texi").write_text(
                "@table @code\n@item ENABLE_MPI\nEnable MPI support\n@end table\n",
                encoding="utf-8",
            )
            (texinfo / "commandline.texi").write_text(
                "@table @code\n@item -tol value\nSet tolerance\n@end table\n",
                encoding="utf-8",
            )

            extraction = extract_tree(root)
            api = next(unit for unit in extraction.units if "Initialize" in unit.msgid)
            option = next(unit for unit in extraction.units if "Print messages" in unit.msgid)
            self.assertEqual(api.semantic_key, "gmsh/initialize")
            self.assertEqual(api.subcategory, "api")
            self.assertEqual(option.semantic_key, "General.Terminal")
            self.assertEqual(option.subcategory, "options")
            msgids = {unit.msgid for unit in extraction.units}
            self.assertNotIn("Distance", msgids)
            self.assertNotIn("Sampling", msgids)
            self.assertNotIn("ENABLE_MPI", msgids)
            self.assertNotIn("-tol value", msgids)
            self.assertIn("Compute a distance field.", msgids)
            self.assertIn("Set tolerance", msgids)

    def test_extracts_changelog_and_non_license_credit_paragraphs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            texinfo = root / "doc" / "texinfo"
            texinfo.mkdir(parents=True)
            (texinfo / "gmsh.texi").write_text("@node Top\n@chapter Manual\n", encoding="utf-8")
            (root / "CHANGELOG.txt").write_text(
                "4.15.2 (March 24, 2026): fixed an output issue.\n\n"
                "4.15.1 (February 16, 2026): bug fixes.\n",
                encoding="utf-8",
            )
            (root / "CREDITS.txt").write_text(
                "Gmsh is copyright (C) 1997-2025\n\n"
                "Code contributions were provided by Christophe Geuzaine and Jean-Francois Remacle.\n\n"
                "The bundled component is copyright Example Author. Permission is granted to copy it.\n",
                encoding="utf-8",
            )

            extraction = extract_tree(root)
            changelog = [unit for unit in extraction.units if unit.relative_file == "CHANGELOG.txt"]
            credits = [unit for unit in extraction.units if unit.relative_file == "CREDITS.txt"]

            self.assertEqual(len(changelog), 2)
            self.assertEqual(changelog[0].subcategory, "version_history")
            self.assertEqual(len(credits), 1)
            self.assertEqual(credits[0].subcategory, "credits")
            self.assertIn("Christophe Geuzaine", credits[0].protected_values)
            self.assertIn("Jean-Francois Remacle", credits[0].protected_values)

            output = root.parent / f"{root.name}-translated"
            apply_catalog(
                root,
                output,
                extraction.units,
                {
                    credits[0].msgctxt: (
                        "代码贡献者包括 Christophe Geuzaine 和 Jean-Francois Remacle。"
                    )
                },
            )
            self.assertIn(
                "Christophe Geuzaine",
                (output / "CREDITS.txt").read_text(encoding="utf-8"),
            )

    def test_real_release_empty_round_trip_preserves_structure_and_bytes(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        source = Path(
            os.environ.get(
                "GMSH_SOURCE_ROOT",
                repository / "upstream" / "cache" / "source" / "gmsh-4.15.2-source",
            )
        )
        if not source.is_dir():
            source = repository / "upstream" / "cache" / "gmsh-4.15.2"
        if not source.is_dir():
            self.skipTest("verified Gmsh source archive is not present in the local cache")
        with (repository / "tests" / "fixtures" / "pilot" / "manifest.toml").open("rb") as stream:
            manifest = tomllib.load(stream)["pilot"]
        extraction = extract_tree(source)
        pilot = select_pilot_units(extraction.units, manifest)
        validate_pilot_manifest(pilot, manifest)
        before = scan_structure(source)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "translated"
            apply_catalog(source, output, extraction.units, {})
            self.assertTrue(compare_structures(before, scan_structure(output)).passed)
            for relative in manifest["source_files"] + manifest["code_files"] + manifest["image_files"]:
                self.assertEqual((source / relative).read_bytes(), (output / relative).read_bytes())


if __name__ == "__main__":
    unittest.main()
