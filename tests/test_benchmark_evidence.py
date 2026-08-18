import copy
import csv
import hashlib
import json
import unittest
from pathlib import Path

from gmsh_doc_cn.benchmark_evidence import (
    ROLE_PROMPT_FILES,
    read_jsonl,
    reviewed_qualification_report,
)
from gmsh_doc_cn.catalog import TranslationUnit
from gmsh_doc_cn.review import read_review_batches, review_evidence_sha256
from gmsh_doc_cn.semantic_rules import semantic_rule_hashes


ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


class BenchmarkEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.benchmark = read_jsonl(ROOT / "benchmarks/units.jsonl")
        cls.initial = read_jsonl(ROOT / "benchmarks/reference-initial-v1.jsonl")
        cls.records = read_review_batches(
            ROOT / "reviews/v4.15.2-benchmark",
            ROOT / "reviews/v4.15.2-benchmark/batches.json",
        )
        cls.approval = read_jsonl(ROOT / "benchmarks/approvals.jsonl")[0]
        cls.units_by_id = {
            str(row["unit_id"]): TranslationUnit(
                unit_id=str(row["unit_id"]),
                msgctxt=str(row["msgctxt"]),
                msgid=str(row["msgid"]),
                relative_file=str(row["source_file"]),
                node=str(row["node"]),
                structure_path=str(row["structure_path"]),
                role=str(row["role"]),
                semantic_key=str(row["semantic_key"]),
                category=str(row["category"]),
                subcategory=str(row["subcategory"]),
                start=0,
                end=0,
                source_hash=str(row["source_hash"]),
                context_hash=str(row["context_hash"]),
                protected_hash=str(row["protected_hash"]),
                protected_values=tuple(str(value) for value in row["protected_values"]),
            )
            for row in cls.benchmark
        }
        glossary_unit_ids = set(cls.units_by_id)
        with (ROOT / "glossary/terms.csv").open(
            encoding="utf-8-sig", newline=""
        ) as stream:
            for row in csv.DictReader(stream):
                glossary_unit_ids.update(
                    value.strip()
                    for value in row["source_unit_ids"].split("|")
                    if value.strip()
                )
        rule_units = list(cls.units_by_id.values())
        for unit_id in sorted(glossary_unit_ids - set(cls.units_by_id)):
            rule_units.append(
                TranslationUnit(
                    unit_id=unit_id,
                    msgctxt="unused",
                    msgid="unused",
                    relative_file="unused",
                    node="unused",
                    structure_path="unused",
                    role="paragraph",
                    semantic_key="unused",
                    category="ordinary_tutorial",
                    subcategory="prose",
                    start=0,
                    end=0,
                    source_hash="0" * 64,
                    context_hash="0" * 64,
                    protected_hash="0" * 64,
                    protected_values=(),
                )
            )
        all_rules_hashes = semantic_rule_hashes(
            rule_units,
            ROOT / "config/translation-rules.toml",
            ROOT / "glossary/terms.csv",
        )
        cls.rules_hashes = {
            unit_id: all_rules_hashes[unit_id] for unit_id in cls.units_by_id
        }
        cls.prompt_hashes = {
            role: sha256(ROOT / filename) for role, filename in ROLE_PROMPT_FILES.items()
        }
        cls.arguments = {
            "units_by_id": cls.units_by_id,
            "current_rules_hashes": cls.rules_hashes,
            "current_glossary_hash": sha256(ROOT / "glossary/terms.csv"),
            "expected_prompt_hashes": cls.prompt_hashes,
            "pot_sha256": sha256(ROOT / "po/gmsh.pot"),
            "manifest_sha256": sha256(ROOT / "upstream/manifest.toml"),
            "initial_reference_sha256": sha256(
                ROOT / "benchmarks/reference-initial-v1.jsonl"
            ),
            "reference_sha256": sha256(ROOT / "benchmarks/reference-final-v1.jsonl"),
            "review_manifest_sha256": sha256(
                ROOT / "reviews/v4.15.2-benchmark/batches.json"
            ),
            "review_evidence_sha256": review_evidence_sha256(
                ROOT / "reviews/v4.15.2-benchmark",
                ROOT / "reviews/v4.15.2-benchmark/batches.json",
            ),
            "initial_reference_validation": read_json(
                ROOT / "benchmarks/reference-initial-validation-v1.json"
            ),
            "reference_validation": read_json(
                ROOT / "benchmarks/reference-validation-v1.json"
            ),
        }

    def report(
        self,
        records: list[dict[str, object]],
        **argument_overrides: object,
    ) -> dict[str, object]:
        arguments = dict(self.arguments)
        arguments.update(argument_overrides)
        return reviewed_qualification_report(
            self.benchmark,
            self.initial,
            records,
            self.approval,
            **arguments,
        )

    def test_current_evidence_qualifies(self) -> None:
        report = self.report(self.records)
        self.assertEqual(report["qualification_state"], "qualified")
        self.assertEqual(report["review_record_count"], 396)

    def test_rejects_stale_unit_hash(self) -> None:
        records = copy.deepcopy(self.records)
        records[0]["source_hash"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "source_hash mismatch"):
            self.report(records)

    def test_rejects_wrong_prompt_hash(self) -> None:
        records = copy.deepcopy(self.records)
        records[0]["prompt_hash"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "prompt hash mismatch"):
            self.report(records)

    def test_rejects_initial_validation_for_another_candidate(self) -> None:
        validation = copy.deepcopy(self.arguments["initial_reference_validation"])
        validation["checks"][0]["candidate_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "initial.*candidate hash mismatch"):
            self.report(self.records, initial_reference_validation=validation)

    def test_rejects_misreported_initial_protected_result(self) -> None:
        validation = copy.deepcopy(self.arguments["initial_reference_validation"])
        failed = next(
            item for item in validation["checks"] if item["protected_content"] == "fail"
        )
        failed["protected_content"] = "pass"
        with self.assertRaisesRegex(ValueError, "initial.*protected result mismatch"):
            self.report(self.records, initial_reference_validation=validation)

    def test_rejects_final_validation_for_another_candidate(self) -> None:
        validation = copy.deepcopy(self.arguments["reference_validation"])
        validation["checks"][0]["candidate_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "final.*candidate hash mismatch"):
            self.report(self.records, reference_validation=validation)

    def test_incomplete_approval_cannot_qualify(self) -> None:
        approval = copy.deepcopy(self.approval)
        del approval["approval_id"]
        report = reviewed_qualification_report(
            self.benchmark,
            self.initial,
            self.records,
            approval,
            **self.arguments,
        )
        self.assertEqual(
            report["qualification_state"], "ai_reviewed_pending_human_approval"
        )
        self.assertEqual(report["human_approval"], "pending")

    def test_rejects_accepted_record_with_issue(self) -> None:
        records = copy.deepcopy(self.records)
        accepted = next(record for record in records if record["result"] == "accept")
        accepted["issues"] = [
            {"code": "test.issue", "severity": "critical", "message": "tampered"}
        ]
        with self.assertRaisesRegex(ValueError, "must not contain issues"):
            self.report(records)

    def test_rejects_duplicate_conclusion(self) -> None:
        records = copy.deepcopy(self.records)
        duplicate = copy.deepcopy(records[0])
        duplicate["event_id"] = "benchmark-test-duplicate-event"
        records.append(duplicate)
        with self.assertRaisesRegex(ValueError, "duplicate benchmark review conclusion"):
            self.report(records)

    def test_rejects_missing_initial_translation(self) -> None:
        unit_id = str(self.benchmark[0]["unit_id"])
        records = [
            record
            for record in copy.deepcopy(self.records)
            if not (record["unit_id"] == unit_id and record["role"] == "translation")
        ]
        with self.assertRaisesRegex(ValueError, "workflow is incomplete"):
            self.report(records)


if __name__ == "__main__":
    unittest.main()
