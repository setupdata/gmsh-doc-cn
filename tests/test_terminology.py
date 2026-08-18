import tempfile
import unittest
from pathlib import Path

from gmsh_doc_cn.catalog import TranslationUnit
from gmsh_doc_cn.terminology import (
    TERM_QUOTAS,
    apply_term_decisions,
    load_term_decisions,
    load_term_seeds,
    merge_term_decisions,
    select_term_candidates,
    write_terms_csv,
)


def make_unit(subcategory: str, number: int) -> TranslationUnit:
    category = {
        "api": "script_api",
        "scripting": "script_api",
        "options": "option_field_plugin",
        "formats": "formats_compile_faq",
        "faq": "formats_compile_faq",
        "prose": "ordinary_tutorial",
    }[subcategory]
    value = f"{subcategory}Term{number}"
    semantic_key = f"gmsh/{value}" if subcategory == "api" else value
    msgid = (
        f"Configure @code{{{value}}} for finite mesh workflow."
        if subcategory == "scripting"
        else f"Configure {value} for finite mesh workflow."
    )
    return TranslationUnit(
        unit_id=f"{subcategory}-{number}",
        msgctxt=f"file|node|paragraph|{value}",
        msgid=msgid,
        relative_file=f"doc/texinfo/{subcategory}.texi",
        node=subcategory,
        structure_path=subcategory,
        role="paragraph",
        semantic_key=semantic_key,
        category=category,
        subcategory=subcategory,
        start=0,
        end=1,
        source_hash=value,
        context_hash=value,
        protected_hash=value,
        protected_values=(),
    )


class TerminologyTests(unittest.TestCase):
    def test_selects_150_pending_candidates_deterministically(self) -> None:
        source_subcategory = {
            "api_symbol": "api",
            "script_command": "scripting",
            "option_field_plugin": "options",
            "file_format": "formats",
            "mesh_geometry_solver": "prose",
            "build_workflow_faq": "faq",
        }
        units = [
            make_unit(source_subcategory[group], number)
            for group, quota in TERM_QUOTAS.items()
            for number in range(quota + 3)
        ]
        forward = select_term_candidates(units)
        reverse = select_term_candidates(reversed(units))
        self.assertEqual(forward, reverse)
        self.assertEqual(len(forward), 150)
        self.assertTrue(all(row["status"] == "candidate_pending" for row in forward))
        self.assertTrue(all(row["zh_cn"] == "" for row in forward))

    def test_curated_seeds_are_evidence_bound_and_keep_declared_order(self) -> None:
        units = [make_unit("scripting", number) for number in range(3)]
        records = select_term_candidates(
            units,
            quotas={"script_command": 2},
            preferred_terms={"script_command": ["scriptingTerm2", "scriptingTerm0"]},
        )
        self.assertEqual([row["term_en"] for row in records], ["scriptingTerm2", "scriptingTerm0"])
        self.assertTrue(all(row["extraction_rule"] == "curated-evidence-v1" for row in records))
        with self.assertRaisesRegex(ValueError, "has no source evidence"):
            select_term_candidates(
                units,
                quotas={"script_command": 1},
                preferred_terms={"script_command": ["DoesNotExist"]},
            )

    def test_same_surface_form_can_have_context_specific_records(self) -> None:
        script = make_unit("scripting", 0)
        prose = make_unit("prose", 0)
        records = select_term_candidates(
            [script, prose],
            quotas={"script_command": 1, "mesh_geometry_solver": 1},
            preferred_terms={
                "script_command": ["finite"],
                "mesh_geometry_solver": ["finite"],
            },
        )
        self.assertEqual([row["term_en"] for row in records], ["finite", "finite"])
        self.assertEqual(len({row["stable_id"] for row in records}), 2)

    def test_loads_seed_file_and_preserves_reviewed_decisions_by_stable_id(self) -> None:
        unit = make_unit("api", 0)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            seed_path = root / "seeds.toml"
            seed_path.write_text('[terms]\napi_symbol = ["gmsh/apiTerm0"]\n', encoding="utf-8")
            seeds = load_term_seeds(seed_path)
            records = select_term_candidates(
                [unit], quotas={"api_symbol": 1}, preferred_terms=seeds
            )
            reviewed = dict(records[0])
            reviewed.update(
                {
                    "zh_cn": "原样保留",
                    "description": "API 标识符",
                    "status": "approved",
                    "ai_review": "two-independent-reviews-accepted",
                    "effective_version": "4.15.2",
                }
            )
            csv_path = root / "terms.csv"
            write_terms_csv([reviewed], csv_path)

            merged = merge_term_decisions(records, csv_path)
            self.assertEqual(merged[0]["zh_cn"], "原样保留")
            self.assertEqual(merged[0]["status"], "approved")

    def test_applies_complete_reviewed_decisions_without_guessing(self) -> None:
        api = select_term_candidates(
            [make_unit("api", 0)],
            quotas={"api_symbol": 1},
            preferred_terms={"api_symbol": ["gmsh/apiTerm0"]},
        )
        script = select_term_candidates(
            [make_unit("scripting", 0)],
            quotas={"script_command": 1},
            preferred_terms={"script_command": ["scriptingTerm0"]},
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "decisions.toml"
            path.write_text(
                'schema_version = 1\nreview_state = "two-independent-reviews-accepted"\n'
                'identity_groups = ["api_symbol"]\n'
                '[translations.script_command]\n"scriptingTerm0" = "脚本术语"\n',
                encoding="utf-8",
            )
            decisions = load_term_decisions(path)
            approved = apply_term_decisions(api + script, decisions)
            self.assertEqual(approved[0]["zh_cn"], "gmsh/apiTerm0")
            self.assertEqual(approved[1]["zh_cn"], "脚本术语")
            self.assertTrue(all(row["status"] == "approved" for row in approved))
            self.assertTrue(
                all(row["ai_review"] == "two-independent-reviews-accepted" for row in approved)
            )

            missing = dict(decisions)
            missing["translations"] = {}
            with self.assertRaisesRegex(ValueError, "missing terminology decision"):
                apply_term_decisions(script, missing)


if __name__ == "__main__":
    unittest.main()
