"""Hash-bound deterministic checks for translations that may become formal."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Iterable, Mapping

from .catalog import (
    PoCatalog,
    TranslationUnit,
    apply_catalog,
    extract_tree,
    protected_values,
    read_po_catalog,
)
from .status import candidate_hash
from .structure import compare_structures, mask_external_file_content, scan_structure


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _snapshot_sha256(snapshot: object) -> str:
    payload = json.dumps(
        snapshot.to_json(), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_po_identity(units: Iterable[TranslationUnit], po: PoCatalog) -> None:
    """Require the active PO to describe exactly the current extracted catalog."""

    unit_list = list(units)
    by_context = {unit.msgctxt: unit for unit in unit_list}
    if len(by_context) != len(unit_list):
        raise ValueError("duplicate msgctxt in extracted translation units")
    active_contexts = set(po.translations)
    expected_contexts = set(by_context)
    missing = expected_contexts - active_contexts
    unknown = active_contexts - expected_contexts
    if missing:
        raise ValueError(f"PO is missing current msgctxt: {sorted(missing)[0]}")
    if unknown:
        raise ValueError(f"PO contains unknown active msgctxt: {sorted(unknown)[0]}")
    for context, unit in by_context.items():
        if po.msgids.get(context) != unit.msgid:
            raise ValueError(f"PO msgid does not match current source: {context}")
    unknown_flags = set(po.flags) - expected_contexts
    if unknown_flags:
        raise ValueError(f"PO flags reference unknown msgctxt: {sorted(unknown_flags)[0]}")


def run_deterministic_checks(
    source_root: Path, po_path: Path, unit_ids: Iterable[str]
) -> dict[str, object]:
    """Run protected-token and full Texinfo structure checks for selected units."""

    source_root = Path(source_root).resolve()
    po_path = Path(po_path).resolve()
    extraction = extract_tree(source_root)
    units = list(extraction.units)
    by_id = {unit.unit_id: unit for unit in units}
    if len(by_id) != len(units):
        raise ValueError("duplicate unit_id in extracted translation units")
    po = read_po_catalog(po_path)
    validate_po_identity(units, po)

    selected_ids = sorted(set(str(value) for value in unit_ids), key=lambda value: value.encode("utf-8"))
    if not selected_ids:
        raise ValueError("deterministic checks require at least one unit_id")
    unknown = set(selected_ids) - set(by_id)
    if unknown:
        raise ValueError(f"deterministic check references unknown unit_id: {sorted(unknown)[0]}")

    selected_catalog: dict[str, str] = {}
    rows: list[dict[str, object]] = []
    for unit_id in selected_ids:
        unit = by_id[unit_id]
        translation = po.translations[unit.msgctxt]
        is_fuzzy = "fuzzy" in po.flags.get(unit.msgctxt, set())
        protected_ok = bool(translation) and protected_values(
            translation, protect_names=unit.relative_file == "CREDITS.txt"
        ) == unit.protected_values
        checks = {
            "translation_present": bool(translation),
            "po_msgid_current": po.msgids[unit.msgctxt] == unit.msgid,
            "not_fuzzy": not is_fuzzy,
            "protected_content": protected_ok,
        }
        if all(checks.values()):
            selected_catalog[unit.msgctxt] = translation
        rows.append(
            {
                "unit_id": unit.unit_id,
                "msgctxt": unit.msgctxt,
                "source_hash": unit.source_hash,
                "context_hash": unit.context_hash,
                "protected_hash": unit.protected_hash,
                "candidate_hash": candidate_hash(translation) if translation else None,
                "checks": checks,
                "result": "pending",
            }
        )

    source_structure = scan_structure(source_root)
    external_paths = {item.relative_file for item in source_structure.external_files}
    translated_external_files = {
        by_id[unit_id].relative_file
        for unit_id in selected_ids
        if by_id[unit_id].relative_file in external_paths
        and by_id[unit_id].msgctxt in selected_catalog
    }
    comparison_source = mask_external_file_content(
        source_structure, translated_external_files
    )
    with tempfile.TemporaryDirectory() as directory:
        translated_root = Path(directory) / "translated"
        apply_catalog(source_root, translated_root, units, selected_catalog)
        translated_structure = scan_structure(translated_root)
    comparison_translated = mask_external_file_content(
        translated_structure, translated_external_files
    )
    comparison = compare_structures(comparison_source, comparison_translated)
    structure = {
        "passed": comparison.passed,
        "changed_kinds": list(comparison.changed_kinds),
        "differences": list(comparison.differences),
        "source_sha256": _snapshot_sha256(comparison_source),
        "translated_sha256": _snapshot_sha256(comparison_translated),
    }
    for row in rows:
        row["result"] = (
            "pass"
            if comparison.passed and all(bool(value) for value in row["checks"].values())
            else "fail"
        )

    return {
        "schema_version": 1,
        "algorithm": "deterministic-unit-checks-v1",
        "po_sha256": _file_sha256(po_path),
        "checked_count": len(rows),
        "all_passed": all(row["result"] == "pass" for row in rows),
        "structure": structure,
        "units": rows,
    }


def check_result_map(report: Mapping[str, object]) -> dict[str, bool]:
    """Convert a freshly computed report to the status reducer's narrow input."""

    rows = report.get("units")
    if not isinstance(rows, list):
        raise ValueError("deterministic check report units must be a list")
    result: dict[str, bool] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("deterministic check report row must be an object")
        unit_id = str(row.get("unit_id", ""))
        if not unit_id or unit_id in result:
            raise ValueError(f"duplicate or missing deterministic unit_id: {unit_id}")
        result[unit_id] = row.get("result") == "pass"
    return result


def write_deterministic_checks(report: Mapping[str, object], output: Path) -> None:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
