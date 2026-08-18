"""Deterministic, stratified benchmark selection and qualification reports."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Iterable, Mapping

from .catalog import TranslationUnit, protected_values


DEFAULT_SEED = "gmsh-4.15.2-benchmark-v1"
DEFAULT_QUOTAS: dict[str, int] = {
    "prose": 15,
    "tutorial": 15,
    "scripting": 12,
    "api": 13,
    "options": 10,
    "fields": 8,
    "plugins": 7,
    "formats": 10,
    "compile": 4,
    "faq": 6,
}
_CATEGORY_BY_SUBCATEGORY = {
    "prose": "ordinary_tutorial",
    "tutorial": "ordinary_tutorial",
    "scripting": "script_api",
    "api": "script_api",
    "options": "option_field_plugin",
    "fields": "option_field_plugin",
    "plugins": "option_field_plugin",
    "formats": "formats_compile_faq",
    "compile": "formats_compile_faq",
    "faq": "formats_compile_faq",
}


def _normalise(value: str) -> str:
    return unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))


def _rank(seed: str, unit_id: str) -> str:
    return hashlib.sha256(f"{seed}\0{unit_id}".encode("utf-8")).hexdigest()


def _eligible(unit: TranslationUnit) -> bool:
    if unit.role == "index":
        return False
    if unit.msgid.lstrip().startswith("@center@image{"):
        return False
    if unit.subcategory == "tutorial" and unit.msgid.lstrip().startswith("See\n@url{"):
        return False
    if unit.subcategory == "api" and re.fullmatch(
        r"(?:Input|Output|Return|Language-specific definition|Examples):(?:#\d+)?",
        unit.semantic_key,
    ):
        return False
    visible = unit.msgid
    for value in unit.protected_values:
        visible = visible.replace(value, " ")
    visible = re.sub(r"@[A-Za-z][A-Za-z0-9-]*", " ", visible)
    words = re.findall(r"[A-Za-z]{2,}", visible)
    return len(words) >= 4


def select_benchmark(
    units: Iterable[TranslationUnit],
    *,
    quotas: Mapping[str, int] = DEFAULT_QUOTAS,
    seed: str = DEFAULT_SEED,
) -> list[dict[str, object]]:
    """Select an exact, reproducible sample without cross-stratum borrowing."""

    buckets: dict[str, dict[tuple[str, str, str], TranslationUnit]] = {
        name: {} for name in quotas
    }
    for unit in units:
        if unit.subcategory not in buckets or not _eligible(unit):
            continue
        fingerprint = (
            unit.category,
            _normalise(unit.msgid),
            unit.protected_hash,
        )
        previous = buckets[unit.subcategory].get(fingerprint)
        if previous is None or (
            _rank(seed, unit.unit_id), unit.unit_id
        ) < (
            _rank(seed, previous.unit_id), previous.unit_id
        ):
            buckets[unit.subcategory][fingerprint] = unit

    selected_units: list[TranslationUnit] = []
    ranks: dict[str, str] = {}
    for subcategory, quota in quotas.items():
        candidates = sorted(
            buckets[subcategory].values(),
            key=lambda unit: (
                _rank(seed, unit.unit_id),
                unit.relative_file.encode("utf-8"),
                unit.node.encode("utf-8"),
                unit.role,
                unit.unit_id,
            ),
        )
        if len(candidates) < quota:
            raise ValueError(
                f"benchmark quota shortfall for {subcategory}: need {quota}, have {len(candidates)}"
            )
        for unit in candidates[:quota]:
            selected_units.append(unit)
            ranks[unit.unit_id] = _rank(seed, unit.unit_id)

    records: list[dict[str, object]] = []
    for number, unit in enumerate(selected_units, start=1):
        records.append(
            {
                "schema_version": 1,
                "benchmark_id": f"bmk-v1-{number:03d}",
                "unit_id": unit.unit_id,
                "category": unit.category,
                "subcategory": unit.subcategory,
                "source_file": unit.relative_file,
                "node": unit.node,
                "structure_path": unit.structure_path,
                "role": unit.role,
                "semantic_key": unit.semantic_key,
                "msgctxt": unit.msgctxt,
                "msgid": unit.msgid,
                "msgid_sha256": hashlib.sha256(unit.msgid.encode("utf-8")).hexdigest(),
                "source_hash": unit.source_hash,
                "context_hash": unit.context_hash,
                "protected_hash": unit.protected_hash,
                "protected_values": list(unit.protected_values),
                "selection_rank": ranks[unit.unit_id],
                "selection_method": "sha256-stratified-v1",
                "reference_translation": "",
                "reference_translation_status": "pending",
            }
        )
    return records


def select_smoke_benchmark(
    records: Iterable[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Take one fixed unit from five distinct content types for workflow testing."""

    wanted = ("prose", "tutorial", "scripting", "api", "formats")
    rows = list(records)
    result: list[dict[str, object]] = []
    for subcategory in wanted:
        paragraph_candidates = [
            dict(row)
            for row in rows
            if row.get("subcategory") == subcategory
            and row.get("role") == "paragraph"
        ]
        candidates = [
            row for row in paragraph_candidates if len(str(row.get("msgid", ""))) >= 60
        ] or paragraph_candidates
        if subcategory == "api":
            api_candidates = [
                row for row in candidates if str(row.get("semantic_key", "")).startswith("gmsh/")
            ]
            candidates = api_candidates or candidates
        match = candidates[0] if candidates else None
        if match is None:
            raise ValueError(f"smoke benchmark missing subcategory: {subcategory}")
        result.append(match)
    return result


def validate_smoke_candidates(
    benchmark: Iterable[Mapping[str, object]],
    candidates: Iterable[Mapping[str, object]],
    units_by_id: Mapping[str, TranslationUnit],
    *,
    require_protected_content: bool = True,
) -> dict[str, object]:
    """Validate exact sample identity and every protected token in candidate translations."""

    rows = list(benchmark)
    candidate_rows = list(candidates)
    if len(rows) != len(candidate_rows):
        raise ValueError("smoke candidate count does not match benchmark")
    by_benchmark = {str(row.get("benchmark_id")): row for row in candidate_rows}
    if len(by_benchmark) != len(candidate_rows):
        raise ValueError("duplicate smoke candidate benchmark_id")
    checks: list[dict[str, object]] = []
    for row in rows:
        benchmark_id = str(row["benchmark_id"])
        unit_id = str(row["unit_id"])
        candidate = by_benchmark.get(benchmark_id)
        if candidate is None or str(candidate.get("unit_id")) != unit_id:
            raise ValueError(f"smoke candidate identity mismatch: {benchmark_id}")
        unit = units_by_id.get(unit_id)
        if unit is None:
            raise ValueError(f"smoke candidate references unknown unit: {unit_id}")
        translation = str(candidate.get("reference_translation", ""))
        if not translation:
            raise ValueError(f"smoke candidate translation is empty: {benchmark_id}")
        protected_content_valid = protected_values(
            translation, protect_names=unit.relative_file == "CREDITS.txt"
        ) == unit.protected_values
        if require_protected_content and not protected_content_valid:
            raise ValueError(f"smoke candidate changed protected content: {benchmark_id}")
        checks.append(
            {
                "benchmark_id": benchmark_id,
                "unit_id": unit_id,
                "protected_content": "pass" if protected_content_valid else "fail",
                "candidate_sha256": hashlib.sha256(
                    _normalise(translation).encode("utf-8")
                ).hexdigest(),
            }
        )
    return {
        "schema_version": 1,
        "checked_count": len(checks),
        "all_passed": all(item["protected_content"] == "pass" for item in checks),
        "checks": checks,
    }


def qualification_report(
    records: Iterable[Mapping[str, object]],
    *,
    pot_sha256: str,
    manifest_sha256: str,
) -> dict[str, object]:
    rows = list(records)
    counts = Counter(str(row["subcategory"]) for row in rows)
    expected = dict(DEFAULT_QUOTAS)
    quota_exact = dict(counts) == expected
    unit_ids = [str(row["unit_id"]) for row in rows]
    hash_pattern = re.compile(r"^[0-9a-f]{64}$")

    def structure_is_valid(number: int, row: Mapping[str, object]) -> bool:
        source_file = str(row.get("source_file", ""))
        node = str(row.get("node", ""))
        role = str(row.get("role", ""))
        semantic_key = str(row.get("semantic_key", ""))
        msgctxt = str(row.get("msgctxt", ""))
        msgid = _normalise(str(row.get("msgid", "")))
        expected_context = "|".join((source_file, node, role, semantic_key))
        expected_unit_id = hashlib.sha256(
            f"gmsh-cn-unit-v1\0{msgctxt}\0{msgid}".encode("utf-8")
        ).hexdigest()
        return (
            row.get("schema_version") == 1
            and str(row.get("benchmark_id", "")) == f"bmk-v1-{number:03d}"
            and bool(source_file and node and role and semantic_key and msgid)
            and str(row.get("structure_path", "")) == f"{source_file}::{node}"
            and msgctxt == expected_context
            and str(row.get("context_hash", ""))
            == hashlib.sha256(msgctxt.encode("utf-8")).hexdigest()
            and str(row.get("unit_id", "")) == expected_unit_id
        )

    def protected_hash_is_valid(row: Mapping[str, object]) -> bool:
        msgid = _normalise(str(row.get("msgid", "")))
        values = row.get("protected_values")
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            return False
        expected_values = list(
            protected_values(msgid, protect_names=str(row.get("source_file")) == "CREDITS.txt")
        )
        serialised = json.dumps(
            expected_values, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        expected_protected = hashlib.sha256(serialised).hexdigest()
        expected_source = hashlib.sha256(msgid.encode("utf-8") + b"\0" + serialised).hexdigest()
        return (
            values == expected_values
            and str(row.get("msgid_sha256", ""))
            == hashlib.sha256(msgid.encode("utf-8")).hexdigest()
            and str(row.get("protected_hash", "")) == expected_protected
            and str(row.get("source_hash", "")) == expected_source
        )

    def selection_is_eligible(row: Mapping[str, object]) -> bool:
        subcategory = str(row.get("subcategory", ""))
        unit_id = str(row.get("unit_id", ""))
        role = str(row.get("role", ""))
        semantic_key = str(row.get("semantic_key", ""))
        msgid = str(row.get("msgid", ""))
        values = row.get("protected_values", [])
        if (
            subcategory not in expected
            or row.get("category") != _CATEGORY_BY_SUBCATEGORY.get(subcategory)
            or role == "index"
            or not hash_pattern.fullmatch(unit_id)
            or row.get("selection_method") != "sha256-stratified-v1"
            or row.get("selection_rank") != _rank(DEFAULT_SEED, unit_id)
        ):
            return False
        if msgid.lstrip().startswith("@center@image{"):
            return False
        if subcategory == "tutorial" and msgid.lstrip().startswith("See\n@url{"):
            return False
        if subcategory == "api" and re.fullmatch(
            r"(?:Input|Output|Return|Language-specific definition|Examples):(?:#\d+)?",
            semantic_key,
        ):
            return False
        visible = msgid
        for value in values if isinstance(values, list) else []:
            visible = visible.replace(str(value), " ")
        visible = re.sub(r"@[A-Za-z][A-Za-z0-9-]*", " ", visible)
        return len(re.findall(r"[A-Za-z]{2,}", visible)) >= 4

    structure_valid = all(structure_is_valid(number, row) for number, row in enumerate(rows, 1))
    protected_hash_valid = all(protected_hash_is_valid(row) for row in rows)
    selection_eligible = all(selection_is_eligible(row) for row in rows)
    fingerprints = {
        (
            str(row.get("category", "")),
            _normalise(str(row.get("msgid", ""))),
            str(row.get("protected_hash", "")),
        )
        for row in rows
    }
    duplicate_free = len(fingerprints) == len(rows) and len(set(unit_ids)) == len(rows)
    selection_qualified = (
        len(rows) == sum(expected.values())
        and len(set(unit_ids)) == len(rows)
        and quota_exact
        and selection_eligible
        and structure_valid
        and protected_hash_valid
        and duplicate_free
    )
    return {
        "schema_version": 1,
        "report_kind": "benchmark_qualification",
        "algorithm_version": "sha256-stratified-v1",
        "seed": DEFAULT_SEED,
        "upstream_version": "4.15.2",
        "pot_sha256": pot_sha256,
        "manifest_sha256": manifest_sha256,
        "quota_plan": expected,
        "selected_count": len(rows),
        "selected_counts": dict(sorted(counts.items())),
        "selection_checks": {
            "quota_exact": quota_exact,
            "unit_id_unique": len(set(unit_ids)) == len(rows),
            "selection_eligible": selection_eligible,
            "structure_valid": structure_valid,
            "protected_hash_valid": protected_hash_valid,
            "duplicate_free": duplicate_free,
        },
        "qualification_state": (
            "selection_qualified_pending_review"
            if selection_qualified
            else "selection_failed"
        ),
        "ai_generation": {
            "status": "not_run",
            "ai_calls": 0,
            "run_ids": [],
            "model": None,
            "prompt_hash": None,
        },
        "language_review": "not_evaluated",
        "technical_review": "not_evaluated",
        "quality_counts": {
            "critical": "not_evaluated",
            "major": "not_evaluated",
            "minor": "not_evaluated",
            "unresolved": "not_evaluated",
        },
        "human_approval": "pending",
        "publish_eligible": False,
    }


def write_jsonl(records: Iterable[Mapping[str, object]], output: Path) -> None:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def write_report(report: Mapping[str, object], output: Path) -> None:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
