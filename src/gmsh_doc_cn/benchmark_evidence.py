"""Frozen reference translations and hash-bound evidence for the 100-unit benchmark."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path

from .benchmark import qualification_report, validate_smoke_candidates
from .catalog import TranslationUnit, protected_values
from .status import candidate_hash


ROLE_PROMPT_FILES = {
    "translation": "prompts/translate.md",
    "revision": "prompts/revise.md",
    "language_review": "prompts/review-language.md",
    "technical_review": "prompts/review-technical.md",
}


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line
    ]


def bind_reference_translations(
    benchmark: Iterable[Mapping[str, object]],
    candidates: Iterable[Mapping[str, object]],
    units_by_id: Mapping[str, TranslationUnit],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Validate and freeze accepted references into the benchmark fixture."""

    benchmark_rows = [dict(row) for row in benchmark]
    candidate_rows = list(candidates)
    validation = validate_smoke_candidates(benchmark_rows, candidate_rows, units_by_id)
    by_id = {str(row["benchmark_id"]): row for row in candidate_rows}
    frozen: list[dict[str, object]] = []
    for row in benchmark_rows:
        candidate = by_id[str(row["benchmark_id"])]
        row["reference_translation"] = str(candidate["reference_translation"])
        row["reference_translation_status"] = "accepted"
        frozen.append(row)
    return frozen, validation


def reviewed_qualification_report(
    benchmark: Iterable[Mapping[str, object]],
    initial_candidates: Iterable[Mapping[str, object]],
    reviews: Iterable[Mapping[str, object]],
    approval: Mapping[str, object] | None,
    *,
    units_by_id: Mapping[str, TranslationUnit],
    current_rules_hashes: Mapping[str, str],
    current_glossary_hash: str,
    expected_prompt_hashes: Mapping[str, str],
    pot_sha256: str,
    manifest_sha256: str,
    initial_reference_sha256: str,
    reference_sha256: str,
    review_manifest_sha256: str,
    review_evidence_sha256: str,
    initial_reference_validation: Mapping[str, object],
    reference_validation: Mapping[str, object],
) -> dict[str, object]:
    """Derive qualification from immutable, current and uniquely bound review evidence."""

    rows = list(benchmark)
    report = qualification_report(
        rows, pot_sha256=pot_sha256, manifest_sha256=manifest_sha256
    )
    if report["qualification_state"] == "selection_failed":
        return report
    if len(rows) != 100:
        raise ValueError("the reviewed benchmark must contain exactly 100 units")

    by_benchmark_id = {str(row["benchmark_id"]): row for row in rows}
    by_unit_id = {str(row["unit_id"]): row for row in rows}
    if len(by_benchmark_id) != len(rows) or len(by_unit_id) != len(rows):
        raise ValueError("benchmark identities must be unique")
    initial_by_id = {str(row.get("benchmark_id", "")): row for row in initial_candidates}
    if set(initial_by_id) != set(by_benchmark_id):
        raise ValueError("initial references do not match the selected benchmark")

    initial_hashes: dict[str, str] = {}
    final_hashes: dict[str, str] = {}
    initial_protected_results: dict[str, str] = {}
    final_protected_results: dict[str, str] = {}
    for benchmark_id, row in by_benchmark_id.items():
        unit_id = str(row["unit_id"])
        unit = units_by_id.get(unit_id)
        if unit is None:
            raise ValueError(f"benchmark references an unknown current unit: {unit_id}")
        for field in ("source_hash", "context_hash", "protected_hash"):
            if row.get(field) != getattr(unit, field):
                raise ValueError(f"benchmark {field} does not match the current unit: {unit_id}")
        initial = initial_by_id[benchmark_id]
        if initial.get("unit_id") != unit_id:
            raise ValueError(f"initial reference unit_id mismatch: {benchmark_id}")
        initial_text = str(initial.get("reference_translation", ""))
        final_text = str(row.get("reference_translation", ""))
        if not initial_text or not final_text:
            raise ValueError(f"benchmark reference translation is empty: {benchmark_id}")
        initial_hashes[unit_id] = candidate_hash(initial_text)
        final_hashes[unit_id] = candidate_hash(final_text)
        protect_names = unit.relative_file == "CREDITS.txt"
        initial_protected_results[unit_id] = (
            "pass"
            if protected_values(initial_text, protect_names=protect_names)
            == unit.protected_values
            else "fail"
        )
        final_protected_results[unit_id] = (
            "pass"
            if protected_values(final_text, protect_names=protect_names)
            == unit.protected_values
            else "fail"
        )

    records = list(reviews)
    if not records:
        raise ValueError("benchmark review evidence is empty")
    expected_roles = set(ROLE_PROMPT_FILES)
    if set(expected_prompt_hashes) != expected_roles:
        raise ValueError("expected benchmark prompt hashes are incomplete")

    event_ids: set[str] = set()
    conclusion_keys: set[tuple[object, ...]] = set()
    run_roles: dict[str, set[str]] = {}
    models: set[str] = set()
    reasoning_efforts: set[str] = set()
    records_by_unit: dict[str, list[Mapping[str, object]]] = {}
    for item in records:
        unit_id = str(item.get("unit_id", ""))
        if unit_id not in by_unit_id:
            raise ValueError(f"benchmark review references an unknown unit: {unit_id}")
        role = str(item.get("role", ""))
        if role not in expected_roles:
            raise ValueError(f"benchmark review has an unsupported role: {role}")
        unit = units_by_id[unit_id]
        expected_hashes = {
            "source_hash": unit.source_hash,
            "context_hash": unit.context_hash,
            "protected_hash": unit.protected_hash,
            "rules_hash": current_rules_hashes[unit_id],
            "glossary_hash": current_glossary_hash,
        }
        for field, expected in expected_hashes.items():
            if item.get(field) != expected:
                raise ValueError(f"benchmark review {field} mismatch: {unit_id}")
        if item.get("prompt_file") != ROLE_PROMPT_FILES[role]:
            raise ValueError(f"benchmark review prompt file mismatch: {unit_id}/{role}")
        if item.get("prompt_hash") != expected_prompt_hashes[role]:
            raise ValueError(f"benchmark review prompt hash mismatch: {unit_id}/{role}")
        candidate = str(item.get("candidate_hash", ""))
        if candidate not in {initial_hashes[unit_id], final_hashes[unit_id]}:
            raise ValueError(f"benchmark review candidate is not a frozen reference: {unit_id}")
        issues = item.get("issues")
        result = str(item.get("result", ""))
        if not isinstance(issues, list):
            raise ValueError("benchmark review issues must be a list")
        if result == "accept" and issues:
            raise ValueError(f"accepted benchmark review must not contain issues: {unit_id}/{role}")
        if result in {"revise", "unresolved"} and not issues:
            raise ValueError(f"non-accepted benchmark review must explain its issues: {unit_id}/{role}")

        event_id = str(item.get("event_id", ""))
        if event_id in event_ids:
            raise ValueError(f"duplicate benchmark review event_id: {event_id}")
        event_ids.add(event_id)
        conclusion_key = (
            unit_id,
            item.get("source_hash"),
            candidate,
            role,
            item.get("round"),
        )
        if conclusion_key in conclusion_keys:
            raise ValueError(f"duplicate benchmark review conclusion: {unit_id}/{role}")
        conclusion_keys.add(conclusion_key)
        run_id = str(item.get("run_id", ""))
        run_roles.setdefault(run_id, set()).add(role)
        models.add(str(item.get("model", "")))
        reasoning_efforts.add(str(item.get("reasoning_effort", "")))
        records_by_unit.setdefault(unit_id, []).append(item)

    mixed_role_run = next(
        (run_id for run_id, roles in run_roles.items() if len(roles) != 1), None
    )
    if mixed_role_run is not None:
        raise ValueError(f"benchmark run_id is shared by multiple roles: {mixed_role_run}")
    if len(models) != 1 or "" in models:
        raise ValueError("benchmark records must use one explicit model")
    if len(reasoning_efforts) != 1 or "" in reasoning_efforts:
        raise ValueError("benchmark records must use one explicit reasoning effort")
    model = next(iter(models))
    reasoning_effort = next(iter(reasoning_efforts))

    language_runs: set[str] = set()
    technical_runs: set[str] = set()
    quality_counts = {"critical": 0, "major": 0, "minor": 0, "unresolved": 0}
    revision_records: list[Mapping[str, object]] = []

    def validation_checks_by_unit(
        validation: Mapping[str, object],
        expected_candidates: Mapping[str, str],
        expected_protected_results: Mapping[str, str],
        *,
        label: str,
        require_all_passed: bool,
    ) -> dict[str, Mapping[str, object]]:
        checks = validation.get("checks")
        if not isinstance(checks, list) or not all(
            isinstance(item, Mapping) for item in checks
        ):
            raise ValueError(f"{label} benchmark validation checks are missing")
        by_unit = {str(item.get("unit_id", "")): item for item in checks}
        if (
            int(validation.get("checked_count", 0)) != len(rows)
            or len(checks) != len(rows)
            or len(by_unit) != len(rows)
            or set(by_unit) != set(by_unit_id)
        ):
            raise ValueError(f"{label} benchmark validation does not cover every selected unit")
        all_passed = True
        for unit_id, item in by_unit.items():
            benchmark_id = str(by_unit_id[unit_id]["benchmark_id"])
            if item.get("benchmark_id") != benchmark_id:
                raise ValueError(f"{label} benchmark validation identity mismatch: {unit_id}")
            if item.get("candidate_sha256") != expected_candidates[unit_id]:
                raise ValueError(f"{label} benchmark validation candidate hash mismatch: {unit_id}")
            protected_content = item.get("protected_content")
            if protected_content not in {"pass", "fail"}:
                raise ValueError(f"{label} benchmark validation has an invalid result")
            if protected_content != expected_protected_results[unit_id]:
                raise ValueError(
                    f"{label} benchmark validation protected result mismatch: {unit_id}"
                )
            all_passed = all_passed and protected_content == "pass"
        if bool(validation.get("all_passed")) != all_passed:
            raise ValueError(f"{label} benchmark validation summary is inconsistent")
        if require_all_passed and not all_passed:
            raise ValueError(f"{label} benchmark validation did not pass")
        return by_unit

    initial_checks_by_unit = validation_checks_by_unit(
        initial_reference_validation,
        initial_hashes,
        initial_protected_results,
        label="initial",
        require_all_passed=False,
    )
    final_checks_by_unit = validation_checks_by_unit(
        reference_validation,
        final_hashes,
        final_protected_results,
        label="final",
        require_all_passed=True,
    )
    initial_reference_checked = True
    initial_protected_failures = {
        unit_id
        for unit_id, item in initial_checks_by_unit.items()
        if item.get("protected_content") == "fail"
    }
    captured_initial_failures: set[str] = set()
    for unit_id in by_unit_id:
        unit_records = records_by_unit.get(unit_id, [])
        initial_hash = initial_hashes[unit_id]
        final_hash = final_hashes[unit_id]
        changed = initial_hash != final_hash
        expected_record_count = 6 if changed else 3
        if len(unit_records) != expected_record_count:
            raise ValueError(f"benchmark review workflow is incomplete: {unit_id}")

        def matching(role: str, round_number: int, candidate: str) -> list[Mapping[str, object]]:
            return [
                item
                for item in unit_records
                if item["role"] == role
                and item["round"] == round_number
                and item["candidate_hash"] == candidate
            ]

        translations = matching("translation", 0, initial_hash)
        initial_language = matching("language_review", 0, initial_hash)
        initial_technical = matching("technical_review", 0, initial_hash)
        if (
            len(translations) != 1
            or translations[0]["result"] != "accept"
            or len(initial_language) != 1
            or len(initial_technical) != 1
        ):
            raise ValueError(f"benchmark initial translation/review workflow is incomplete: {unit_id}")

        if changed:
            if not any(
                item["result"] in {"revise", "unresolved"}
                for item in (*initial_language, *initial_technical)
            ):
                raise ValueError(f"benchmark revision has no review issue trigger: {unit_id}")
            if unit_id in initial_protected_failures:
                protected_issues = [
                    issue
                    for item in (*initial_language, *initial_technical)
                    for issue in item["issues"]
                    if str(issue.get("code", "")).startswith("protected.")
                    and issue.get("severity") == "critical"
                ]
                if not protected_issues:
                    raise ValueError(
                        f"initial protected-content failure was not captured by review: {unit_id}"
                    )
                captured_initial_failures.add(unit_id)
            revisions = matching("revision", 1, final_hash)
            final_language = matching("language_review", 1, final_hash)
            final_technical = matching("technical_review", 1, final_hash)
            if (
                len(revisions) != 1
                or revisions[0]["result"] != "accept"
                or len(final_language) != 1
                or final_language[0]["result"] != "accept"
                or len(final_technical) != 1
                or final_technical[0]["result"] != "accept"
            ):
                raise ValueError(f"benchmark revised candidate lacks final acceptance: {unit_id}")
            revision_records.extend(revisions)
            current = [*revisions, *final_language, *final_technical]
        else:
            if unit_id in initial_protected_failures:
                raise ValueError(
                    f"unchanged benchmark candidate failed protected-content validation: {unit_id}"
                )
            if initial_language[0]["result"] != "accept" or initial_technical[0]["result"] != "accept":
                raise ValueError(f"unchanged benchmark candidate has an unresolved review: {unit_id}")
            final_language = initial_language
            final_technical = initial_technical
            current = [*translations, *initial_language, *initial_technical]

        language_runs.add(str(final_language[0]["run_id"]))
        technical_runs.add(str(final_technical[0]["run_id"]))
        for item in current:
            for issue in item["issues"]:
                quality_counts[str(issue["severity"])] += 1
            if item["result"] == "unresolved":
                quality_counts["unresolved"] += 1

    final_content_valid = len(final_checks_by_unit) == len(rows)
    language_independent = language_runs.isdisjoint(technical_runs)
    evaluation_qualified = (
        initial_reference_checked
        and captured_initial_failures == initial_protected_failures
        and final_content_valid
        and language_independent
        and all(value == 0 for value in quality_counts.values())
    )

    approval_prompts = (approval or {}).get("prompt_hashes", {})
    approved_prompt_hashes = {
        "translation": approval_prompts.get("translate") if isinstance(approval_prompts, Mapping) else None,
        "revision": approval_prompts.get("revise") if isinstance(approval_prompts, Mapping) else None,
        "language_review": (
            approval_prompts.get("review_language") if isinstance(approval_prompts, Mapping) else None
        ),
        "technical_review": (
            approval_prompts.get("review_technical") if isinstance(approval_prompts, Mapping) else None
        ),
    }
    required_approval_fields = {
        "schema_version",
        "approval_id",
        "benchmark_version",
        "decision",
        "approved_at",
        "approved_by",
        "approval_source",
        "initial_reference_sha256",
        "reference_sha256",
        "review_manifest_sha256",
        "review_evidence_sha256",
        "model",
        "reasoning_effort",
        "prompt_hashes",
    }
    approval_valid = bool(
        approval
        and set(approval) == required_approval_fields
        and type(approval.get("schema_version")) is int
        and approval.get("schema_version") == 1
        and isinstance(approval.get("approval_id"), str)
        and bool(approval.get("approval_id"))
        and approval.get("benchmark_version") == "v1"
        and approval.get("decision") == "approved"
        and isinstance(approval.get("approved_at"), str)
        and re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
            str(approval.get("approved_at")),
        )
        is not None
        and approval.get("approved_by") == "maintainer"
        and isinstance(approval.get("approval_source"), str)
        and bool(approval.get("approval_source"))
        and approval.get("initial_reference_sha256") == initial_reference_sha256
        and approval.get("reference_sha256") == reference_sha256
        and approval.get("review_manifest_sha256") == review_manifest_sha256
        and approval.get("review_evidence_sha256") == review_evidence_sha256
        and approval.get("model") == model
        and approval.get("reasoning_effort") == reasoning_effort
        and isinstance(approval_prompts, Mapping)
        and set(approval_prompts)
        == {"translate", "revise", "review_language", "review_technical"}
        and approved_prompt_hashes == dict(expected_prompt_hashes)
    )
    generation_runs = sorted(
        {
            str(item["run_id"])
            for item in records
            if item["role"] in {"translation", "revision"}
        }
    )
    report.update(
        {
            "qualification_state": (
                "qualified"
                if evaluation_qualified and approval_valid
                else "ai_reviewed_pending_human_approval"
                if evaluation_qualified
                else "evaluation_failed"
            ),
            "ai_generation": {
                "status": "complete",
                "ai_calls": len(generation_runs),
                "run_ids": generation_runs,
                "model": model,
                "reasoning_effort": reasoning_effort,
                "prompt_hash": expected_prompt_hashes["translation"],
            },
            "language_review": "accept" if evaluation_qualified else "failed",
            "technical_review": "accept" if evaluation_qualified else "failed",
            "quality_counts": quality_counts,
            "initial_reference_sha256": initial_reference_sha256,
            "reference_sha256": reference_sha256,
            "review_manifest_sha256": review_manifest_sha256,
            "review_evidence_sha256": review_evidence_sha256,
            "review_record_count": len(records),
            "revision_count": len(revision_records),
            "max_revision_round": max(
                (int(item["round"]) for item in revision_records), default=0
            ),
            "evaluation_checks": {
                "initial_reference_checked": initial_reference_checked,
                "initial_protected_failure_count": len(initial_protected_failures),
                "initial_protected_failures_captured": (
                    captured_initial_failures == initial_protected_failures
                ),
                "all_references_accepted": evaluation_qualified,
                "language_review_independent": language_independent,
                "protected_content_valid": final_content_valid,
                "review_records_current": True,
                "review_records_unique": True,
            },
            "human_approval": "approved" if approval_valid else "pending",
            "approval_id": approval.get("approval_id") if approval_valid and approval else None,
            "publish_eligible": False,
        }
    )
    return report
