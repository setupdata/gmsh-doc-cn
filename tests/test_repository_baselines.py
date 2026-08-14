import csv
import hashlib
import json
import os
import tomllib
import unittest
from pathlib import Path

from gmsh_doc_cn.catalog import extract_tree, read_po_catalog
from gmsh_doc_cn.deterministic_checks import check_result_map, run_deterministic_checks
from gmsh_doc_cn.review import (
    read_review_batches,
    review_evidence_sha256,
    validate_review_record,
)
from gmsh_doc_cn.schema_validation import validate_schema
from gmsh_doc_cn.semantic_rules import semantic_rule_hashes
from gmsh_doc_cn.status import candidate_hash, reduce_status


ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def read_jsonl(path: str) -> list[dict]:
    return [
        json.loads(line)
        for line in (ROOT / path).read_text(encoding="utf-8").splitlines()
        if line
    ]


def validate_file(value: object, schema_path: str) -> None:
    validate_schema(value, read_json(schema_path))


class RepositoryBaselineTests(unittest.TestCase):
    def test_fixed_upstream_identity_and_license_copy(self) -> None:
        with (ROOT / "upstream/manifest.toml").open("rb") as stream:
            upstream = tomllib.load(stream)["upstream"]
        self.assertEqual(upstream["version"], "4.15.2")
        self.assertEqual(upstream["tag"], "gmsh_4_15_2")
        self.assertEqual(upstream["source_directory"], "gmsh-4.15.2-source")
        self.assertEqual(
            upstream["commit"],
            "657c8e915f60405e6cad0c8ec7faf812bfff1a60",
        )
        self.assertEqual(
            upstream["archive_sha256"],
            "be3f66f225d27ba9fa014f07e83169285da8a051b0e8ab7103d88066b39bdd3e",
        )
        self.assertEqual(
            sha256(ROOT / "LICENSES/Gmsh-license.txt"),
            "32085d8c954e2e22dc667c089f360049e8f1af955f946fb0a18f29d70390276a",
        )

    def test_container_and_tool_lock_agree(self) -> None:
        with (ROOT / "container/tool-versions.lock").open("rb") as stream:
            locked = tomllib.load(stream)
        containerfile = (ROOT / "container/Containerfile").read_text(encoding="utf-8")
        self.assertIn(
            f"{locked['base']['image']}@{locked['base']['digest']}",
            containerfile,
        )
        self.assertIn(f"ARG DEBIAN_SNAPSHOT={locked['base']['debian_snapshot']}", containerfile)
        for package in ("texinfo", "gettext", "po4a"):
            self.assertIn(f"{package}={locked['required'][package]}", containerfile)

    def test_pilot_and_five_unit_workflow_evidence_is_complete(self) -> None:
        pilot = read_json("tests/fixtures/pilot/pilot-report.json")
        validate_file(pilot, "schemas/pilot-report.schema.json")
        self.assertEqual(pilot["round_trip"], "pass")
        self.assertTrue(pilot["round_trip_byte_identity"])
        self.assertEqual(
            {item["name"]: item["passed"] for item in pilot["round_trip_comparisons"]},
            {"empty": True, "current-po": True},
        )
        self.assertFalse(pilot["po4a_admitted"])
        po4a = read_json("tests/fixtures/pilot/po4a-evaluation.json")
        validate_file(po4a, "schemas/po4a-evaluation.schema.json")
        self.assertEqual(po4a["execution_status"], "executed")
        self.assertEqual(po4a["evaluation_result"], "not-admitted")
        self.assertEqual(
            po4a["execution"]["master_files"],
            ["doc/texinfo/gmsh.texi", "doc/texinfo/api.texi"],
        )
        self.assertFalse(po4a["execution"]["protected_sequence_equal"])
        self.assertFalse(po4a["execution"]["structure_equal"])
        self.assertEqual(po4a["execution"]["source_unit_count"], pilot["unit_count"])
        self.assertEqual(po4a["execution"]["normalized_unit_count"], pilot["unit_count"])
        self.assertGreater(po4a["execution"]["source_only_structure_records"], 0)
        self.assertGreater(po4a["execution"]["normalized_only_structure_records"], 0)
        self.assertEqual(po4a["fallback_verification"]["unit_count"], pilot["unit_count"])
        self.assertEqual(
            po4a["fallback_verification"]["pilot_unit_count"],
            pilot["pilot_unit_count"],
        )

        html_inventory = read_json("tests/fixtures/pilot/html-anchors.json")
        validate_file(html_inventory, "schemas/html-anchor-inventory.schema.json")
        self.assertGreater(html_inventory["html_file_count"], 1)
        self.assertGreater(html_inventory["anchor_count"], 1)
        self.assertEqual(html_inventory["duplicate_anchors"], [])
        self.assertEqual(
            {
                (item["source"], item["href"], item["reason"])
                for item in html_inventory["broken_internal_links"]
            },
            {
                ("gmsh.html", "dir.html#Top", "missing-page"),
                ("index.html", "../dir_html/index.html", "missing-page"),
            },
        )
        validate_file(
            read_json("tests/baselines/texinfo-warnings.json"),
            "schemas/texinfo-warnings.schema.json",
        )

        validation = read_json("benchmarks/five-unit-validation.json")
        review = read_json("benchmarks/five-unit-review.json")
        validate_file(review, "schemas/five-unit-review.schema.json")
        self.assertTrue(validation["all_passed"])
        self.assertEqual(validation["checked_count"], 5)
        self.assertEqual(review["round_0"]["language_review"]["result"], "revise")
        self.assertEqual(review["round_1"]["language_review"]["result"], "accept")
        self.assertEqual(review["round_1"]["technical_review"]["result"], "accept")
        self.assertEqual(review["paid_api_calls"], 0)

        batches = read_json("reviews/v4.15.2/batches.json")
        validate_file(batches, "schemas/review-batches.schema.json")
        production_reviews = read_review_batches(
            ROOT / "reviews/v4.15.2", ROOT / "reviews/v4.15.2/batches.json"
        )
        self.assertEqual(len(production_reviews), 48)
        overview = [
            record for record in production_reviews if record["batch"] == "overview-of-gmsh-v1"
        ]
        self.assertEqual(len(overview), 18)
        self.assertEqual(
            {record["role"] for record in overview},
            {"translation", "revision", "language_review", "technical_review"},
        )
        overview_unit_ids = {
            "bd4e60b69841c3c518ba65f7196d3fdd87a9d22abf004f4e029465e07a19f714",
            "78fc6b746ad6b5909c817f41037c0b297e56b9c1f734834b5243ed8ddf62c833",
            "66a1ab1e87e085a88723e38e6974b4f21031fe289f4a8d29ad28558252bd18c4",
            "934aecb49b901810f1437cfd527924577279b5892614e06d90a0cafa79cb36b5",
            "2f7160b7d621cf34053e7803634b947f503542b1b3766424edbb756455e83923",
            "aad666639ab4f3aa412d1e1eae256e0cac09f6132bed4f6b6577bba1578bdf35",
        }
        self.assertEqual(
            {
                record["unit_id"]
                for record in production_reviews
                if record["unit_id"] in overview_unit_ids
            },
            overview_unit_ids,
        )
        reused_title = [
            record
            for record in production_reviews
            if record["unit_id"]
            == "bd4e60b69841c3c518ba65f7196d3fdd87a9d22abf004f4e029465e07a19f714"
        ]
        self.assertEqual({record["batch"] for record in reused_title}, {"formal-smoke-v1"})

        initial = read_jsonl("benchmarks/five-unit-initial-candidates.jsonl")
        final = read_jsonl("benchmarks/five-unit-candidates.jsonl")
        records = read_jsonl("reviews/v4.15.2/five-unit-smoke-v1.jsonl")
        benchmark = {row["unit_id"]: row for row in read_jsonl("benchmarks/units.jsonl")}
        for record in records:
            validate_review_record(record)
            validate_file(record, "schemas/review-record.schema.json")
            unit = benchmark[record["unit_id"]]
            self.assertEqual(record["source_hash"], unit["source_hash"])
            self.assertEqual(record["context_hash"], unit["context_hash"])
            self.assertEqual(record["protected_hash"], unit["protected_hash"])

        initial_hashes = {
            row["unit_id"]: candidate_hash(row["reference_translation"]) for row in initial
        }
        final_hashes = {
            row["unit_id"]: candidate_hash(row["reference_translation"]) for row in final
        }
        for unit_id, initial_hash in initial_hashes.items():
            self.assertTrue(
                any(
                    record["unit_id"] == unit_id
                    and record["role"] == "translation"
                    and record["candidate_hash"] == initial_hash
                    for record in records
                )
            )
        for unit_id, final_hash in final_hashes.items():
            final_language = [
                record
                for record in records
                if record["unit_id"] == unit_id
                and record["candidate_hash"] == final_hash
                and record["round"] == 1
                and record["role"] == "language_review"
                and record["result"] == "accept"
            ]
            final_technical = [
                record
                for record in records
                if record["unit_id"] == unit_id
                and record["candidate_hash"] == final_hash
                and record["round"] == 1
                and record["role"] == "technical_review"
                and record["result"] == "accept"
            ]
            self.assertEqual(len(final_language), 1)
            self.assertEqual(len(final_technical), 1)
            self.assertNotEqual(final_language[0]["run_id"], final_technical[0]["run_id"])

    def test_reviewed_glossary_has_150_evidence_bound_terms(self) -> None:
        with (ROOT / "glossary/terms.csv").open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 150)
        self.assertTrue(all(row["status"] == "approved" for row in rows))
        self.assertTrue(all(row["source_unit_ids"] for row in rows))

        review = read_json("glossary/review-v1.json")
        validate_file(review, "schemas/term-review.schema.json")
        self.assertEqual(review["glossary_sha256"], sha256(ROOT / "glossary/terms.csv"))
        self.assertEqual(review["language_review"]["result"], "accept")
        self.assertEqual(review["technical_review"]["result"], "accept")
        self.assertEqual(review["paid_api_calls"], 0)

    def test_formal_smoke_records_bind_three_independent_passes(self) -> None:
        records = read_jsonl("reviews/v4.15.2/formal-smoke-v1.jsonl")
        for record in records:
            validate_file(record, "schemas/review-record.schema.json")
        self.assertEqual(
            {record["role"] for record in records},
            {"translation", "language_review", "technical_review"},
        )
        self.assertEqual(len({record["run_id"] for record in records}), 3)
        self.assertTrue(all(record["result"] == "accept" for record in records))
        for field in (
            "unit_id",
            "source_hash",
            "context_hash",
            "protected_hash",
            "candidate_hash",
            "rules_hash",
            "glossary_hash",
        ):
            self.assertEqual(len({record[field] for record in records}), 1)
        self.assertTrue(all(len(record["rules_hash"]) == 64 for record in records))
        self.assertEqual(records[0]["glossary_hash"], sha256(ROOT / "glossary/terms.csv"))

    def test_real_formal_smoke_reduces_against_the_fixed_release(self) -> None:
        source = Path(
            os.environ.get(
                "GMSH_SOURCE_ROOT",
                ROOT / "upstream/cache/source/gmsh-4.15.2-source",
            )
        )
        if not source.is_dir():
            source = ROOT / "upstream/cache/gmsh-4.15.2"
        if not source.is_dir():
            self.skipTest("verified Gmsh source archive is not present in the local cache")
        extraction = extract_tree(source)
        po = read_po_catalog(ROOT / "po/zh_CN.po")
        records = read_review_batches(
            ROOT / "reviews/v4.15.2", ROOT / "reviews/v4.15.2/batches.json"
        )
        per_unit_rules = semantic_rule_hashes(
            extraction.units,
            ROOT / "config/translation-rules.toml",
            ROOT / "glossary/terms.csv",
        )
        for record in records:
            self.assertEqual(record["rules_hash"], per_unit_rules[record["unit_id"]])
        formal_units = [
            "bd4e60b69841c3c518ba65f7196d3fdd87a9d22abf004f4e029465e07a19f714",
            "78fc6b746ad6b5909c817f41037c0b297e56b9c1f734834b5243ed8ddf62c833",
            "66a1ab1e87e085a88723e38e6974b4f21031fe289f4a8d29ad28558252bd18c4",
            "934aecb49b901810f1437cfd527924577279b5892614e06d90a0cafa79cb36b5",
            "2f7160b7d621cf34053e7803634b947f503542b1b3766424edbb756455e83923",
            "aad666639ab4f3aa412d1e1eae256e0cac09f6132bed4f6b6577bba1578bdf35",
        ]
        check_report = run_deterministic_checks(
            source, ROOT / "po/zh_CN.po", formal_units
        )
        validate_file(check_report, "schemas/deterministic-checks.schema.json")
        self.assertEqual(
            check_report,
            read_json("reviews/v4.15.2/deterministic-checks-v1.json"),
        )
        report = reduce_status(
            extraction.units,
            po.translations,
            records,
            check_result_map(check_report),
            flags=po.flags,
            catalog_msgids=po.msgids,
            current_rules_hash=sha256(ROOT / "config/translation-rules.toml"),
            current_rules_hashes=per_unit_rules,
            current_glossary_hash=sha256(ROOT / "glossary/terms.csv"),
            obsolete_count=po.obsolete_count,
        )
        self.assertEqual(report["counts"], {"formal": 6, "untranslated": 6910})
        self.assertEqual(report["obsolete_count"], 20)
        validate_file(report, "schemas/status.schema.json")

    def test_license_and_title_metadata_are_not_translation_units(self) -> None:
        po = read_po_catalog(ROOT / "po/gmsh.pot")
        contexts = set(po.translations)
        msgids = set(po.msgids.values())
        self.assertFalse(any("|Copying conditions|" in value for value in contexts))
        self.assertFalse(any("|License|" in value for value in contexts))
        self.assertFalse(
            any("Permission is granted to make and distribute verbatim copies" in value for value in msgids)
        )
        self.assertFalse(any(value.startswith("Christophe Geuzaine") for value in msgids))

    def test_100_unit_model_configuration_is_qualified_and_hash_bound(self) -> None:
        rows = read_jsonl("benchmarks/units.jsonl")
        report = read_json("benchmarks/qualification-v1.json")
        initial_validation = read_json("benchmarks/reference-initial-validation-v1.json")
        validation = read_json("benchmarks/reference-validation-v1.json")
        approvals = read_jsonl("benchmarks/approvals.jsonl")
        validate_file(report, "schemas/benchmark-qualification.schema.json")
        validate_file(
            initial_validation,
            "schemas/benchmark-initial-reference-validation.schema.json",
        )
        validate_file(validation, "schemas/benchmark-reference-validation.schema.json")
        self.assertEqual(len(approvals), 1)
        validate_file(approvals[0], "schemas/benchmark-approval.schema.json")
        for row in rows:
            validate_file(row, "schemas/benchmark-unit.schema.json")
        self.assertEqual(len(rows), 100)
        self.assertEqual(len({row["unit_id"] for row in rows}), 100)
        self.assertTrue(all(row["reference_translation"] for row in rows))
        self.assertTrue(all(row["reference_translation_status"] == "accepted" for row in rows))
        self.assertEqual(report["selected_count"], 100)
        self.assertEqual(report["selected_counts"], report["quota_plan"])
        self.assertEqual(report["qualification_state"], "qualified")
        self.assertEqual(report["ai_generation"]["status"], "complete")
        self.assertEqual(report["ai_generation"]["ai_calls"], 7)
        self.assertEqual(report["language_review"], "accept")
        self.assertEqual(report["technical_review"], "accept")
        self.assertEqual(
            report["quality_counts"],
            {"critical": 0, "major": 0, "minor": 0, "unresolved": 0},
        )
        self.assertEqual(report["human_approval"], "approved")
        self.assertFalse(report["publish_eligible"])
        self.assertEqual(report["pot_sha256"], sha256(ROOT / "po/gmsh.pot"))
        self.assertEqual(report["manifest_sha256"], sha256(ROOT / "upstream/manifest.toml"))
        self.assertEqual(
            report["initial_reference_sha256"],
            sha256(ROOT / "benchmarks/reference-initial-v1.jsonl"),
        )
        self.assertEqual(
            report["reference_sha256"], sha256(ROOT / "benchmarks/reference-final-v1.jsonl")
        )
        self.assertEqual(
            report["review_manifest_sha256"],
            sha256(ROOT / "reviews/v4.15.2-benchmark/batches.json"),
        )
        self.assertEqual(
            report["review_evidence_sha256"],
            review_evidence_sha256(
                ROOT / "reviews/v4.15.2-benchmark",
                ROOT / "reviews/v4.15.2-benchmark/batches.json",
            ),
        )
        self.assertEqual(initial_validation["checked_count"], 100)
        self.assertFalse(initial_validation["all_passed"])
        self.assertEqual(
            sum(
                item["protected_content"] == "fail"
                for item in initial_validation["checks"]
            ),
            5,
        )
        self.assertEqual(validation["checked_count"], 100)
        self.assertTrue(validation["all_passed"])

        review_manifest = read_json("reviews/v4.15.2-benchmark/batches.json")
        validate_file(review_manifest, "schemas/review-batches.schema.json")
        review_records = read_review_batches(
            ROOT / "reviews/v4.15.2-benchmark",
            ROOT / "reviews/v4.15.2-benchmark/batches.json",
        )
        self.assertEqual(len(review_records), report["review_record_count"])
        self.assertEqual(len(review_records), 396)
        by_unit: dict[str, list[dict]] = {}
        for record in review_records:
            validate_file(record, "schemas/review-record.schema.json")
            by_unit.setdefault(record["unit_id"], []).append(record)
        for row in rows:
            current_hash = candidate_hash(row["reference_translation"])
            current = [
                record
                for record in by_unit[row["unit_id"]]
                if record["candidate_hash"] == current_hash
            ]
            self.assertTrue(
                any(
                    record["role"] in {"translation", "revision"}
                    and record["result"] == "accept"
                    for record in current
                )
            )
            language_runs = {
                record["run_id"]
                for record in current
                if record["role"] == "language_review" and record["result"] == "accept"
            }
            technical_runs = {
                record["run_id"]
                for record in current
                if record["role"] == "technical_review" and record["result"] == "accept"
            }
            self.assertTrue(language_runs)
            self.assertTrue(technical_runs)
            self.assertTrue(language_runs.isdisjoint(technical_runs))
            self.assertFalse(
                any(record["result"] in {"revise", "unresolved"} for record in current)
            )
            self.assertLessEqual(max(record["round"] for record in current), 1)

        approval = approvals[0]
        self.assertEqual(
            approval["initial_reference_sha256"], report["initial_reference_sha256"]
        )
        self.assertEqual(approval["reference_sha256"], report["reference_sha256"])
        self.assertEqual(approval["review_manifest_sha256"], report["review_manifest_sha256"])
        self.assertEqual(approval["review_evidence_sha256"], report["review_evidence_sha256"])
        self.assertEqual(approval["model"], report["ai_generation"]["model"])
        self.assertEqual(approval["reasoning_effort"], report["ai_generation"]["reasoning_effort"])

        expected_category_sizes = {
            "ordinary-tutorial": 30,
            "script-api": 25,
            "option-field-plugin": 25,
            "format-compile-faq": 20,
        }
        for name, expected in expected_category_sizes.items():
            self.assertEqual(len(read_jsonl(f"benchmarks/categories/{name}.jsonl")), expected)

    def test_all_committed_json_and_jsonl_artifacts_parse(self) -> None:
        for path in sorted(ROOT.rglob("*.json")):
            if any(part in {"build", "upstream"} for part in path.relative_to(ROOT).parts):
                continue
            json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(ROOT.rglob("*.jsonl")):
            if "build" in path.relative_to(ROOT).parts:
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if line:
                    json.loads(line)


if __name__ == "__main__":
    unittest.main()
