import copy
import hashlib
import io
import json
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from gmsh_doc_cn.artifacts import write_artifact_manifest
from gmsh_doc_cn.catalog import extract_tree, read_po_catalog, write_po
from gmsh_doc_cn.cli import main
from gmsh_doc_cn.preview import (
    _localize_texinfo_chrome,
    build_preview_site,
    derive_preview_status,
    preview_build_report,
    select_formal_catalog,
)
from gmsh_doc_cn.review import make_review_record
from gmsh_doc_cn.schema_validation import validate_schema
from gmsh_doc_cn.semantic_rules import semantic_rule_hashes


ROOT = Path(__file__).resolve().parents[1]


class PreviewBuildTests(unittest.TestCase):
    def _fixture(self, root: Path):
        source = root / "source"
        texinfo = source / "doc" / "texinfo"
        (texinfo / "images").mkdir(parents=True)
        (texinfo / "images" / "pixel.png").write_bytes(b"fixture-image")
        (texinfo / "gmsh.texi").write_text(
            textwrap.dedent(
                r"""
                \input texinfo
                @setfilename gmsh.info
                @node Top
                @top Gmsh test manual

                @node Overview
                @chapter Overview

                First source paragraph for the preview.

                Second source paragraph remains in English.

                @bye
                """
            ).lstrip(),
            encoding="utf-8",
        )
        extraction = extract_tree(source)
        formal_unit = next(
            unit
            for unit in extraction.units
            if unit.msgid == "First source paragraph for the preview."
        )
        translation = "预览中的第一段正式译文。"
        po_path = root / "zh_CN.po"
        write_po(
            extraction.units,
            po_path,
            language="zh_CN",
            translations={formal_unit.msgctxt: translation},
        )
        rules = root / "translation-rules.toml"
        glossary = root / "terms.csv"
        rules.write_text("schema_version = 1\n", encoding="utf-8")
        glossary.write_text(
            "term_id,term_en,zh_cn,keep_english_context,description,status,"
            "effective_version,source_unit_ids\n",
            encoding="utf-8",
        )
        per_unit_rules = semantic_rule_hashes(extraction.units, rules, glossary)
        glossary_hash = hashlib.sha256(glossary.read_bytes()).hexdigest()
        reviews = root / "reviews"
        reviews.mkdir()
        batch = "preview-fixture-v1"
        records = []
        for index, role in enumerate(("translation", "language_review", "technical_review"), 1):
            records.append(
                make_review_record(
                    formal_unit,
                    candidate=translation,
                    event_id=f"fixture-event-{index}",
                    run_id=f"fixture-run-{role}",
                    batch=batch,
                    role=role,
                    result="accept",
                    model="fixture-model",
                    reasoning_effort="high",
                    prompt_file=f"prompts/{role}.md",
                    prompt_hash=str(index) * 64,
                    glossary_hash=glossary_hash,
                    rules_hash=per_unit_rules[formal_unit.unit_id],
                    started_at=f"2026-08-13T00:00:0{index}Z",
                    completed_at=f"2026-08-13T00:00:0{index}Z",
                )
            )
        review_file = reviews / "preview-fixture-v1.jsonl"
        review_file.write_text(
            "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
            encoding="utf-8",
        )
        review_manifest = reviews / "batches.json"
        review_manifest.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "batches": [
                        {
                            "batch": batch,
                            "file": review_file.name,
                            "run_ids": [str(record["run_id"]) for record in records],
                        }
                    ],
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        status = derive_preview_status(
            source,
            po_path,
            review_directory=reviews,
            review_manifest_path=review_manifest,
            check_units=[formal_unit.unit_id],
            rules_path=rules,
            glossary_path=glossary,
        )
        fake = root / "fake_makeinfo.py"
        fake.write_text(
            textwrap.dedent(
                """
                import os
                import sys
                from pathlib import Path

                args = sys.argv[1:]
                output = Path(args[args.index('--output') + 1])
                source = (Path.cwd() / 'gmsh.texi').read_text(encoding='utf-8')
                stamp = os.environ['SOURCE_DATE_EPOCH']
                anchor = 'TopCN' if '@documentlanguage zh' in source else 'Top'
                page = (
                    f'<html data-epoch="{stamp}"><head><title>Gmsh</title>'
                    f'<link rel="start" href="dir.html#Top"></head>'
                    f'<body lang="en"><div class="nav-panel"><p>Next: page</p></div>'
                    f'<p>Command line (see <a class="pxref" href="manual.html">manual</a>)</p>'
                    f'<p>中文说明（see <a class="pxref" href="manual.html">manual</a>）</p>'
                    f'<pre>id="{anchor}" href="#{anchor}"</pre>'
                    f'<img src="images/pixel.png" alt="fixture">'
                    f'<a href="#{anchor}">top</a><div id="{anchor}"><pre>{source}</pre></div>'
                    f'</body></html>\\n'
                )
                if '--no-split' in args:
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_text(page, encoding='utf-8')
                else:
                    output.mkdir(parents=True, exist_ok=True)
                    (output / 'index.html').write_text(page, encoding='utf-8')
                """
            ).lstrip(),
            encoding="utf-8",
        )
        return (
            source,
            extraction,
            formal_unit,
            po_path,
            rules,
            glossary,
            status,
            reviews,
            review_manifest,
            fake,
        )

    def test_builds_noindex_preview_with_only_formal_translations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (
                source,
                extraction,
                formal_unit,
                po_path,
                rules,
                glossary,
                status,
                reviews,
                review_manifest,
                fake,
            ) = self._fixture(root)
            result = build_preview_site(
                source,
                root / "dist",
                po_path=po_path,
                status=status,
                review_directory=reviews,
                review_manifest_path=review_manifest,
                check_units=[formal_unit.unit_id],
                rules_path=rules,
                glossary_path=glossary,
                version="4.15.2",
                source_date_epoch=1774310400,
                site_base="/gmsh-doc-cn/",
                upstream_tag="gmsh_4_15_2",
                upstream_commit="657c8e915f60405e6cad0c8ec7faf812bfff1a60",
                translation_revision="r1",
                translation_date="2026-08-13",
                makeinfo_command=[sys.executable, str(fake)],
            )

            english = result.english_root / "gmsh.html"
            preview = result.preview_root / "gmsh.html"
            english_text = english.read_text(encoding="utf-8")
            preview_text = preview.read_text(encoding="utf-8")
            self.assertIn("First source paragraph for the preview.", english_text)
            self.assertNotIn("预览中的第一段正式译文。", english_text)
            self.assertIn("预览中的第一段正式译文。", preview_text)
            self.assertIn("Second source paragraph remains in English.", preview_text)
            self.assertIn('<html data-epoch="1774310400" lang="zh-CN"', preview_text)
            self.assertIn('data-translation-state="mixed"', preview_text)
            self.assertIn('<meta name="robots" content="noindex,nofollow">', preview_text)
            self.assertIn("非官方简体中文预览", preview_text)
            self.assertIn("英文回退", preview_text)
            self.assertIn("下一页：page", preview_text)
            self.assertIn('see <a class="pxref"', preview_text)
            self.assertIn('中文说明（see <a class="pxref"', preview_text)
            self.assertIn('href="#Top"', preview_text)
            self.assertIn('id="Top"', preview_text)
            self.assertIn('<pre>id="TopCN" href="#TopCN"</pre>', preview_text)
            self.assertIn(
                'href="/gmsh-doc-cn/v4.15.2/en/gmsh.html"', preview_text
            )
            self.assertIn(
                'rel="canonical" href="/gmsh-doc-cn/preview/v4.15.2/zh-cn/gmsh.html"',
                preview_text,
            )
            self.assertIn(
                'rel="alternate" hreflang="zh-CN" '
                'href="/gmsh-doc-cn/preview/v4.15.2/zh-cn/gmsh.html"',
                preview_text,
            )
            self.assertNotIn("noindex", english_text)
            self.assertEqual(result.formal_unit_count, 1)
            self.assertEqual(result.fallback_unit_count, len(extraction.units) - 1)
            self.assertEqual(result.english_html_file_count, 2)
            self.assertEqual(result.preview_html_file_count, 2)
            self.assertEqual(result.noindex_nofollow_page_count, 2)
            self.assertGreater(result.site_absolute_link_count, 0)
            self.assertGreater(result.resource_link_count, 0)

    def test_localizes_references_only_inside_marked_formal_translations(self) -> None:
        unit_id = "a" * 64
        marked = (
            f'<p>\ue000{unit_id}\ue001中文说明（see '
            '<a class="pxref" href="manual.html">manual</a>）'
            f'\ue002{unit_id}\ue003</p>'
        )
        fallback = (
            '<p>English fallback 中文 see '
            '<a class="pxref" href="manual.html">manual</a></p>'
        )

        rendered = _localize_texinfo_chrome(marked + fallback)

        self.assertIn('中文说明（参见 <a class="pxref"', rendered)
        self.assertIn('English fallback 中文 see <a class="pxref"', rendered)
        self.assertNotIn("\ue000", rendered)
        self.assertNotIn("\ue003", rendered)

    def test_rejects_a_status_report_for_different_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (
                _,
                extraction,
                _,
                po_path,
                rules,
                glossary,
                status,
                _,
                _,
                _,
            ) = self._fixture(root)
            status["input_hashes"]["translation_rules"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "translation-rules hash"):
                select_formal_catalog(
                    extraction.units,
                    read_po_catalog(po_path),
                    status,
                    rules_hash=hashlib.sha256(rules.read_bytes()).hexdigest(),
                    glossary_hash=hashlib.sha256(glossary.read_bytes()).hexdigest(),
                    semantic_rules_hashes=semantic_rule_hashes(
                        extraction.units, rules, glossary
                    ),
                )

            forged = copy.deepcopy(status)
            forged["input_hashes"]["translation_rules"] = hashlib.sha256(
                rules.read_bytes()
            ).hexdigest()
            formal_row = next(row for row in forged["units"] if row["state"] == "formal")
            formal_row["semantic_rules_hash"] = "f" * 64
            with self.assertRaisesRegex(ValueError, "semantic rules hash mismatch"):
                select_formal_catalog(
                    extraction.units,
                    read_po_catalog(po_path),
                    forged,
                    rules_hash=hashlib.sha256(rules.read_bytes()).hexdigest(),
                    glossary_hash=hashlib.sha256(glossary.read_bytes()).hexdigest(),
                    semantic_rules_hashes=semantic_rule_hashes(
                        extraction.units, rules, glossary
                    ),
                )

    def test_rejects_a_forged_formal_state_before_running_makeinfo(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (
                source,
                _,
                formal_unit,
                po_path,
                rules,
                glossary,
                status,
                reviews,
                review_manifest,
                fake,
            ) = self._fixture(root)
            forged = copy.deepcopy(status)
            row = next(item for item in forged["units"] if item["state"] == "untranslated")
            row["state"] = "formal"
            forged["counts"] = {
                "formal": int(forged["counts"]["formal"]) + 1,
                "untranslated": int(forged["counts"]["untranslated"]) - 1,
            }
            with self.assertRaisesRegex(ValueError, "freshly derived"):
                build_preview_site(
                    source,
                    root / "dist",
                    po_path=po_path,
                    status=forged,
                    review_directory=reviews,
                    review_manifest_path=review_manifest,
                    check_units=[formal_unit.unit_id],
                    rules_path=rules,
                    glossary_path=glossary,
                    version="4.15.2",
                    source_date_epoch=1774310400,
                    site_base="/gmsh-doc-cn/",
                    upstream_tag="gmsh_4_15_2",
                    upstream_commit="657c8e915f60405e6cad0c8ec7faf812bfff1a60",
                    translation_revision="r1",
                    translation_date="2026-08-13",
                    makeinfo_command=[sys.executable, str(fake)],
                )

    def test_preview_report_matches_its_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (
                source,
                _,
                formal_unit,
                po_path,
                rules,
                glossary,
                status,
                reviews,
                review_manifest,
                fake,
            ) = self._fixture(root)
            result = build_preview_site(
                source,
                root / "dist",
                po_path=po_path,
                status=status,
                review_directory=reviews,
                review_manifest_path=review_manifest,
                check_units=[formal_unit.unit_id],
                rules_path=rules,
                glossary_path=glossary,
                version="4.15.2",
                source_date_epoch=1774310400,
                site_base="/gmsh-doc-cn/",
                upstream_tag="gmsh_4_15_2",
                upstream_commit="657c8e915f60405e6cad0c8ec7faf812bfff1a60",
                translation_revision="r1",
                translation_date="2026-08-13",
                makeinfo_command=[sys.executable, str(fake)],
            )
            artifact_manifest = write_artifact_manifest(
                result.dist_root, root / "artifact-manifest.json"
            )
            report = preview_build_report(
                result,
                site_base="/gmsh-doc-cn/",
                version="4.15.2",
                upstream_tag="gmsh_4_15_2",
                upstream_commit="657c8e915f60405e6cad0c8ec7faf812bfff1a60",
                translation_revision="r1",
                translation_date="2026-08-13",
                source_date_epoch=1774310400,
                input_hashes={
                    "upstream_manifest": "1" * 64,
                    "site_config": "2" * 64,
                    "release": "3" * 64,
                    "po": "4" * 64,
                    "status": "5" * 64,
                    "status_schema": "6" * 64,
                    "preview_schema": "7" * 64,
                    "translation_rules": "8" * 64,
                    "glossary": "9" * 64,
                    "review_manifest": "a" * 64,
                    "reviews": "b" * 64,
                    "check_units": "c" * 64,
                    "tool_versions": "d" * 64,
                    "containerfile": "e" * 64,
                },
                artifact_manifest=artifact_manifest,
            )
            schema = json.loads(
                (ROOT / "schemas/preview-build.schema.json").read_text(encoding="utf-8")
            )
            validate_schema(report, schema)
            self.assertEqual(
                report["formal_unit_count"] + report["fallback_unit_count"],
                report["unit_count"],
            )
            self.assertEqual(
                report["preview_html_file_count"],
                report["noindex_nofollow_page_count"],
            )

    def test_reproducible_preview_cli_preserves_the_site_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (
                source,
                _,
                formal_unit,
                po_path,
                rules,
                glossary,
                status,
                reviews,
                review_manifest,
                fake,
            ) = self._fixture(root)
            manifest = root / "manifest.toml"
            site = root / "site.toml"
            release = root / "release.toml"
            status_path = root / "status.json"
            output = root / "preview-report.json"
            dist = root / "public"
            manifest.write_text(
                textwrap.dedent(
                    """
                    [upstream]
                    version = "4.15.2"
                    tag = "gmsh_4_15_2"
                    commit = "657c8e915f60405e6cad0c8ec7faf812bfff1a60"
                    source_date_epoch = 1774310400
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            site.write_text(
                textwrap.dedent(
                    """
                    [site]
                    base = "/gmsh-doc-cn/"
                    version = "4.15.2"
                    html_language_zh = "zh-CN"
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            release.write_text(
                textwrap.dedent(
                    """
                    [release]
                    upstream_version = "4.15.2"
                    translation_revision = "r1"
                    translation_date = "2026-08-13"
                    language = "zh-CN"
                    site_base = "/gmsh-doc-cn/"
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            status_path.write_text(
                json.dumps(status, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            arguments = [
                "reproducible-preview",
                "--source",
                str(source),
                "--dist",
                str(dist),
                "--manifest",
                str(manifest),
                "--site-config",
                str(site),
                "--release",
                str(release),
                "--po",
                str(po_path),
                "--status",
                str(status_path),
                "--reviews",
                str(reviews),
                "--review-manifest",
                str(review_manifest),
                "--check-unit",
                formal_unit.unit_id,
                "--rules",
                str(rules),
                "--glossary",
                str(glossary),
                "--makeinfo",
                f"{sys.executable} {fake}",
                "--schema",
                str(ROOT / "schemas/preview-build.schema.json"),
                "--output",
                str(output),
            ]
            with redirect_stdout(io.StringIO()):
                main(arguments)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(report["reproducible"])
            self.assertEqual(report["formal_unit_count"], 1)
            self.assertTrue((dist / "v4.15.2/en/gmsh.html").is_file())
            self.assertTrue((dist / "preview/v4.15.2/zh-cn/gmsh.html").is_file())


if __name__ == "__main__":
    unittest.main()
