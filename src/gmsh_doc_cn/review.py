"""Structured, hash-bound review records without model chain-of-thought."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Mapping

from .catalog import TranslationUnit
from .status import candidate_hash


_ROLES = {"translation", "language_review", "technical_review", "revision"}
_RESULTS = {"accept", "revise", "unresolved"}


def make_review_record(
    unit: TranslationUnit,
    *,
    candidate: str,
    event_id: str,
    run_id: str,
    batch: str,
    role: str,
    result: str,
    model: str,
    reasoning_effort: str,
    prompt_file: str,
    prompt_hash: str,
    glossary_hash: str,
    rules_hash: str,
    started_at: str,
    completed_at: str,
    round_number: int = 0,
    issues: list[dict[str, str]] | None = None,
    origin: str = "project",
) -> dict[str, object]:
    record: dict[str, object] = {
        "schema_version": 1,
        "event_id": event_id,
        "run_id": run_id,
        "batch": batch,
        "unit_id": unit.unit_id,
        "role": role,
        "round": round_number,
        "source_hash": unit.source_hash,
        "candidate_hash": candidate_hash(candidate),
        "protected_hash": unit.protected_hash,
        "context_hash": unit.context_hash,
        "rules_hash": rules_hash,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "prompt_file": prompt_file,
        "prompt_hash": prompt_hash,
        "glossary_hash": glossary_hash,
        "result": result,
        "issues": issues or [],
        "origin": origin,
        "started_at": started_at,
        "completed_at": completed_at,
        "retry_count": 0,
        "cost": {"currency": "USD", "amount": 0, "source": "Codex session"},
    }
    validate_review_record(record)
    return record


def validate_review_record(record: Mapping[str, object]) -> None:
    required = {
        "schema_version",
        "event_id",
        "run_id",
        "batch",
        "unit_id",
        "role",
        "round",
        "source_hash",
        "candidate_hash",
        "protected_hash",
        "context_hash",
        "rules_hash",
        "model",
        "reasoning_effort",
        "prompt_file",
        "prompt_hash",
        "glossary_hash",
        "result",
        "issues",
        "origin",
        "started_at",
        "completed_at",
        "retry_count",
        "cost",
    }
    missing = required - record.keys()
    if missing:
        raise ValueError(f"review record missing fields: {sorted(missing)}")
    extra = set(record) - required
    if extra:
        raise ValueError(f"review record has unsupported fields: {sorted(extra)}")
    if type(record.get("schema_version")) is not int or record.get("schema_version") != 1:
        raise ValueError("review schema_version must be 1")
    for field in (
        "event_id",
        "run_id",
        "batch",
        "unit_id",
        "model",
        "reasoning_effort",
        "prompt_file",
        "origin",
        "started_at",
        "completed_at",
    ):
        if not isinstance(record.get(field), str) or not record[field]:
            raise ValueError(f"review field must be a non-empty string: {field}")
    for field in (
        "source_hash",
        "candidate_hash",
        "protected_hash",
        "context_hash",
        "rules_hash",
        "prompt_hash",
        "glossary_hash",
    ):
        if not isinstance(record.get(field), str) or re.fullmatch(
            r"[0-9a-f]{64}", str(record[field])
        ) is None:
            raise ValueError(f"review field must be a lowercase SHA-256: {field}")
    if record["role"] not in _ROLES:
        raise ValueError(f"invalid review role: {record['role']}")
    if record["result"] not in _RESULTS:
        raise ValueError(f"invalid review result: {record['result']}")
    timestamp_pattern = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z"
    for field in ("started_at", "completed_at"):
        if re.fullmatch(timestamp_pattern, str(record[field])) is None:
            raise ValueError(f"review timestamp must be fixed UTC ISO-8601: {field}")
    if str(record["completed_at"]) < str(record["started_at"]):
        raise ValueError("review completed_at must not precede started_at")
    if (
        not isinstance(record["round"], int)
        or isinstance(record["round"], bool)
        or not 0 <= int(record["round"]) <= 2
    ):
        raise ValueError("review round must be an integer from 0 through 2")
    if not isinstance(record["issues"], list):
        raise ValueError("review issues must be a list")
    if not isinstance(record.get("retry_count"), int) or int(record["retry_count"]) < 0:
        raise ValueError("review retry_count must be a non-negative integer")
    if not isinstance(record.get("cost"), dict):
        raise ValueError("review cost must be an object")
    cost = record["cost"]
    if set(cost) != {"currency", "amount", "source"}:
        raise ValueError("review cost must contain only currency, amount, and source")
    if not isinstance(cost["currency"], str) or not cost["currency"]:
        raise ValueError("review cost currency must be a non-empty string")
    if (
        not isinstance(cost["amount"], (int, float))
        or isinstance(cost["amount"], bool)
        or cost["amount"] < 0
    ):
        raise ValueError("review cost amount must be a non-negative number")
    if not isinstance(cost["source"], str) or not cost["source"]:
        raise ValueError("review cost source must be a non-empty string")
    for issue in record["issues"]:
        if not isinstance(issue, dict) or set(issue) != {"code", "severity", "message"}:
            raise ValueError("review issue must contain only code, severity, and message")
        if issue.get("severity") not in {
            "critical",
            "major",
            "minor",
        }:
            raise ValueError("review issue must have a supported severity")
        if not isinstance(issue.get("code"), str) or not issue["code"]:
            raise ValueError("review issue code must be a non-empty string")
        if not isinstance(issue.get("message"), str) or not issue["message"]:
            raise ValueError("review issue message must be a non-empty string")


def read_review_batches(directory: Path, manifest_path: Path) -> list[dict[str, object]]:
    """Read only records and run IDs authorised by the versioned batch manifest."""

    directory = Path(directory).resolve()
    manifest_path = Path(manifest_path).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or set(manifest) != {"schema_version", "batches"}:
        raise ValueError("review batch manifest must contain schema_version and batches")
    if (
        type(manifest.get("schema_version")) is not int
        or manifest.get("schema_version") != 1
        or not isinstance(manifest.get("batches"), list)
    ):
        raise ValueError("review batch manifest schema_version must be 1")
    if not manifest["batches"]:
        raise ValueError("review batch manifest must declare at least one batch")

    declared_files: set[str] = set()
    declared_runs: set[str] = set()
    records: list[dict[str, object]] = []
    for entry in manifest["batches"]:
        if not isinstance(entry, dict) or set(entry) != {"batch", "file", "run_ids"}:
            raise ValueError("review batch entry must contain batch, file, and run_ids")
        batch = entry["batch"]
        filename = entry["file"]
        run_ids = entry["run_ids"]
        if not isinstance(batch, str) or not batch:
            raise ValueError("review batch name must be a non-empty string")
        if (
            not isinstance(filename, str)
            or not filename.endswith(".jsonl")
            or Path(filename).name != filename
        ):
            raise ValueError("review batch file must be one JSONL file name")
        if filename in declared_files:
            raise ValueError(f"review batch file is declared more than once: {filename}")
        declared_files.add(filename)
        if (
            not isinstance(run_ids, list)
            or not run_ids
            or not all(isinstance(value, str) and value for value in run_ids)
            or len(set(run_ids)) != len(run_ids)
        ):
            raise ValueError(f"review batch run_ids must be unique non-empty strings: {batch}")
        overlap = declared_runs & set(run_ids)
        if overlap:
            raise ValueError(f"review run_id is declared by multiple batches: {sorted(overlap)[0]}")
        declared_runs.update(run_ids)

        file_path = directory / filename
        if not file_path.is_file() or file_path.is_symlink():
            raise ValueError(f"review batch file is missing or unsafe: {filename}")
        seen_runs: set[str] = set()
        for line in file_path.read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError(f"review record must be an object: {filename}")
            validate_review_record(record)
            if record["batch"] != batch:
                raise ValueError(f"review record batch does not match manifest: {filename}")
            if record["run_id"] not in run_ids:
                raise ValueError(f"review run_id is not authorised by manifest: {record['run_id']}")
            seen_runs.add(str(record["run_id"]))
            records.append(record)
        missing_runs = set(run_ids) - seen_runs
        if missing_runs:
            raise ValueError(f"review run_id has no records: {sorted(missing_runs)[0]}")

    actual_files = {path.name for path in directory.glob("*.jsonl") if path.is_file()}
    undeclared_files = actual_files - declared_files
    missing_files = declared_files - actual_files
    if undeclared_files:
        raise ValueError(f"review JSONL file is not declared: {sorted(undeclared_files)[0]}")
    if missing_files:
        raise ValueError(f"declared review JSONL file is missing: {sorted(missing_files)[0]}")
    return records
