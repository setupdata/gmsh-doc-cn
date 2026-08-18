import csv
import tempfile
import unittest
from pathlib import Path

from gmsh_doc_cn.catalog import TranslationUnit
from gmsh_doc_cn.semantic_rules import semantic_rule_hashes


def unit(unit_id: str, msgid: str) -> TranslationUnit:
    return TranslationUnit(
        unit_id=unit_id,
        msgctxt=f"file|Top|paragraph|{unit_id}",
        msgid=msgid,
        relative_file="doc/texinfo/gmsh.texi",
        node="Top",
        structure_path="doc/texinfo/gmsh.texi::Top",
        role="paragraph",
        semantic_key=unit_id,
        category="ordinary_tutorial",
        subcategory="prose",
        start=0,
        end=len(msgid),
        source_hash="source",
        context_hash="context",
        protected_hash="protected",
        protected_values=(),
    )


class SemanticRuleHashTests(unittest.TestCase):
    def test_unrelated_glossary_changes_do_not_change_another_units_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rules = root / "rules.toml"
            rules.write_text('[rules]\nversion = "1"\nfaithful = true\n', encoding="utf-8")
            glossary = root / "terms.csv"
            fieldnames = [
                "term_id",
                "term_en",
                "zh_cn",
                "keep_english_context",
                "description",
                "status",
                "effective_version",
                "source_unit_ids",
            ]

            def write(second_translation: str, first_term_id: str = "term-1") -> None:
                with glossary.open("w", encoding="utf-8-sig", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(
                        [
                            {
                                "term_id": first_term_id,
                                "term_en": "mesh",
                                "zh_cn": "网格",
                                "keep_english_context": "",
                                "description": "",
                                "status": "approved",
                                "effective_version": "4.15.2",
                                "source_unit_ids": "unit-1",
                            },
                            {
                                "term_id": "term-2",
                                "term_en": "solver",
                                "zh_cn": second_translation,
                                "keep_english_context": "",
                                "description": "",
                                "status": "approved",
                                "effective_version": "4.15.2",
                                "source_unit_ids": "unit-2",
                            },
                        ]
                    )

            units = [unit("unit-1", "mesh"), unit("unit-2", "solver")]
            write("求解器")
            before = semantic_rule_hashes(units, rules, glossary)
            write("数值求解器")
            after = semantic_rule_hashes(units, rules, glossary)

            self.assertEqual(before["unit-1"], after["unit-1"])
            self.assertNotEqual(before["unit-2"], after["unit-2"])

            write("数值求解器", first_term_id="term-1-renamed")
            renamed = semantic_rule_hashes(units, rules, glossary)
            self.assertNotEqual(after["unit-1"], renamed["unit-1"])

    def test_rejects_glossary_references_to_unknown_units(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rules = root / "rules.toml"
            rules.write_text('[rules]\nversion = "1"\n', encoding="utf-8")
            glossary = root / "terms.csv"
            glossary.write_text(
                "term_id,term_en,zh_cn,keep_english_context,description,status,effective_version,source_unit_ids\n"
                "term-1,mesh,网格,,,approved,4.15.2,unknown\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "unknown unit_id"):
                semantic_rule_hashes([unit("unit-1", "mesh")], rules, glossary)
