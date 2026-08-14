import json
import tempfile
import unittest
from pathlib import Path

from gmsh_doc_cn.catalog import TranslationUnit
from gmsh_doc_cn.review import make_review_record, read_review_batches, validate_review_record


class ReviewRecordTests(unittest.TestCase):
    def test_builds_valid_hash_bound_record(self) -> None:
        unit = TranslationUnit(
            unit_id="unit",
            msgctxt="context",
            msgid="Overview of Gmsh",
            relative_file="doc/texinfo/gmsh.texi",
            node="Overview of Gmsh",
            structure_path="path",
            role="heading",
            semantic_key="Overview of Gmsh",
            category="ordinary_tutorial",
            subcategory="prose",
            start=0,
            end=16,
            source_hash="d" * 64,
            context_hash="e" * 64,
            protected_hash="f" * 64,
            protected_values=(),
        )
        record = make_review_record(
            unit,
            candidate="Gmsh 概述",
            event_id="event",
            run_id="run",
            batch="formal-smoke-v1",
            role="language_review",
            result="accept",
            model="gpt-5.6-luna",
            reasoning_effort="xhigh",
            prompt_file="prompts/review-language.md",
            prompt_hash="a" * 64,
            glossary_hash="b" * 64,
            rules_hash="c" * 64,
            started_at="2026-08-13T04:48:31Z",
            completed_at="2026-08-13T04:48:31Z",
        )
        validate_review_record(record)
        self.assertEqual(record["result"], "accept")
        self.assertEqual(record["issues"], [])

        invalid = dict(record)
        invalid["candidate_hash"] = "not-a-hash"
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            validate_review_record(invalid)
        invalid = dict(record)
        invalid["result"] = "revise"
        invalid["issues"] = [{"severity": "minor"}]
        with self.assertRaisesRegex(ValueError, "code"):
            validate_review_record(invalid)
        invalid = dict(record)
        invalid["issues"] = [
            {"code": "language.wording", "severity": "minor", "message": "needs revision"}
        ]
        with self.assertRaisesRegex(ValueError, "must not contain issues"):
            validate_review_record(invalid)
        invalid = dict(record)
        invalid["schema_version"] = True
        with self.assertRaisesRegex(ValueError, "schema_version"):
            validate_review_record(invalid)

    def test_batch_manifest_is_the_only_authority_for_review_runs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = {
                "schema_version": 1,
                "event_id": "event",
                "run_id": "run-language",
                "batch": "batch-v1",
                "unit_id": "unit",
                "role": "language_review",
                "round": 0,
                "source_hash": "a" * 64,
                "candidate_hash": "b" * 64,
                "protected_hash": "c" * 64,
                "context_hash": "d" * 64,
                "rules_hash": "e" * 64,
                "model": "gpt-5.6-luna",
                "reasoning_effort": "xhigh",
                "prompt_file": "prompts/review-language.md",
                "prompt_hash": "f" * 64,
                "glossary_hash": "0" * 64,
                "result": "accept",
                "issues": [],
                "origin": "project",
                "started_at": "2026-08-13T04:48:31Z",
                "completed_at": "2026-08-13T04:48:31Z",
                "retry_count": 0,
                "cost": {"currency": "USD", "amount": 0, "source": "Codex session"},
            }
            (root / "batch-v1.jsonl").write_text(
                json.dumps(record, sort_keys=True) + "\n", encoding="utf-8"
            )
            manifest = root / "batches.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "batches": [
                            {
                                "batch": "batch-v1",
                                "file": "batch-v1.jsonl",
                                "run_ids": ["run-language"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(read_review_batches(root, manifest), [record])

            broken = json.loads(manifest.read_text(encoding="utf-8"))
            broken["batches"][0]["run_ids"] = ["undeclared-output"]
            manifest.write_text(json.dumps(broken), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "run_id"):
                read_review_batches(root, manifest)

            manifest.write_text(
                json.dumps({"schema_version": True, "batches": []}), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "schema_version"):
                read_review_batches(root, manifest)

            manifest.write_text(
                json.dumps({"schema_version": 1, "batches": []}), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "at least one batch"):
                read_review_batches(root, manifest)


if __name__ == "__main__":
    unittest.main()
